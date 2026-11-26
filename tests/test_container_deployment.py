#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ContainerDeploymentTests(unittest.TestCase):
    def test_runtime_requirements_include_fastapi_test_client_transport(self):
        requirements = {
            line.strip()
            for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertIn("httpx2==2.9.1", requirements)
        self.assertIn("mini-racer==0.14.1", requirements)

    def test_image_builds_vue_assets_and_packages_the_fastapi_runtime(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("FROM node:24-bookworm-slim AS web-builder", dockerfile)
        self.assertIn("pnpm install --frozen-lockfile", dockerfile)
        self.assertIn("id=niuone-pnpm-store", dockerfile)
        self.assertIn("sharing=locked", dockerfile)
        self.assertLess(
            dockerfile.index("COPY web/src/ ./src/"),
            dockerfile.index("pnpm install --frozen-lockfile"),
        )
        self.assertIn("&& pnpm run build", dockerfile)
        self.assertIn("&& rm -rf node_modules", dockerfile)
        self.assertIn("&& pnpm store prune", dockerfile)
        self.assertIn("--no-cache-dir", dockerfile)
        self.assertNotIn("target=/root/.cache/pip", dockerfile)
        self.assertIn("COPY app/ ./app/", dockerfile)
        self.assertIn("COPY frontend/ ./frontend/", dockerfile)
        self.assertIn("COPY --from=web-builder /build/web/dist ./web/dist", dockerfile)
        self.assertIn("!frontend/", dockerignore)
        self.assertIn("!frontend/**", dockerignore)
        self.assertIn("!web/package.json", dockerignore)
        self.assertIn("!web/pnpm-lock.yaml", dockerignore)
        self.assertIn("!web/src/**", dockerignore)
        self.assertIn("ARG NIUONE_VERSION=dev", dockerfile)
        self.assertIn("NIUONE_VERSION=${NIUONE_VERSION}", dockerfile)
        self.assertIn("python3 -m pip install", dockerfile)
        self.assertIn('CMD ["python3", "-c"', dockerfile)

    def test_local_build_scripts_only_prune_dangling_niuone_images(self):
        shell = (ROOT / "scripts" / "docker-build.sh").read_text(encoding="utf-8")
        powershell = (
            ROOT / "scripts" / "docker-build.ps1"
        ).read_text(encoding="utf-8")
        label_filter = "label=org.opencontainers.image.title=NiuOne"

        self.assertIn("docker compose build", shell)
        self.assertEqual(shell.count(label_filter), 1)
        self.assertIn("docker compose build", powershell)
        self.assertEqual(powershell.count(label_filter), 2)
        for source in (shell, powershell):
            self.assertIn("docker image prune --force", source)
            self.assertNotIn("--all", source)
            self.assertNotIn("docker system prune", source)
            self.assertNotIn("docker builder prune", source)

    def test_compose_runs_niuone_processes_with_bundled_newsnow(self):
        config = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
        fastapi_source = (
            ROOT / "app" / "dashboard" / "fastapi_app.py"
        ).read_text(encoding="utf-8")
        services = config["services"]
        self.assertEqual(set(services), {"dashboard", "scheduler", "newsnow"})
        expected_logging = {
            "driver": "local",
            "options": {
                "max-size": "5m",
                "max-file": "3",
                "compress": "true",
            },
        }
        for service in services.values():
            self.assertEqual(service["logging"], expected_logging)
        self.assertEqual(services["dashboard"]["command"], ["dashboard"])
        self.assertEqual(services["scheduler"]["command"], ["scheduler"])
        for name in ("dashboard", "scheduler"):
            self.assertIn("niuone-data:/data", services[name]["volumes"])

        newsnow = services["newsnow"]
        self.assertEqual(
            newsnow["image"],
            "${NEWSNOW_IMAGE:-ghcr.io/ourongxing/newsnow:latest}",
        )
        self.assertNotIn("ports", newsnow)
        self.assertEqual(newsnow["volumes"], [
            "newsnow-data:/usr/app/.data",
            "./scripts/newsnow-network.cjs:/etc/niuone/newsnow-network.cjs:ro",
        ])
        self.assertEqual(
            newsnow["environment"]["NODE_OPTIONS"],
            "--require=/etc/niuone/newsnow-network.cjs",
        )
        self.assertEqual(newsnow["environment"]["HOST"], "0.0.0.0")
        self.assertEqual(newsnow["environment"]["PORT"], "4444")
        self.assertEqual(newsnow["environment"]["INIT_TABLE"], "true")
        self.assertEqual(newsnow["environment"]["ENABLE_CACHE"], "true")
        self.assertEqual(
            services["dashboard"]["environment"]["NIUONE_BUNDLED_NEWSNOW_URL"],
            "http://newsnow:4444/api/s",
        )
        self.assertEqual(
            services["dashboard"]["environment"]["DASHBOARD_ACCESS_LOG"],
            "0",
        )
        self.assertIn('os.environ.get("DASHBOARD_ACCESS_LOG", "1")', fastapi_source)
        self.assertIn("access_log=access_log", fastapi_source)
        self.assertEqual(
            services["dashboard"]["depends_on"],
            {"newsnow": {"condition": "service_started"}},
        )
        self.assertEqual(set(config["volumes"]), {"niuone-data", "newsnow-data"})

    def test_newsnow_network_preload_keeps_family_fallback_and_longer_limits(self):
        script = ROOT / "scripts" / "newsnow-network.cjs"
        program = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[1], 'utf8');
const results = [];
for (const initial of [250, 2000, 5000, null]) {
  const calls = [];
  const net = initial === null ? {} : {
    getDefaultAutoSelectFamilyAttemptTimeout: () => initial,
    setDefaultAutoSelectFamilyAttemptTimeout: value => calls.push(value),
    setDefaultAutoSelectFamily: () => { throw new Error('family fallback changed'); },
  };
  vm.runInNewContext(source, {require: name => {
    if (name !== 'node:net') throw new Error(name);
    return net;
  }});
  results.push(calls);
}
console.log(JSON.stringify(results));
"""
        output = subprocess.check_output(
            ["node", "-e", program, str(script)], text=True,
        )
        self.assertEqual(json.loads(output), [[2000], [2000], [5000], []])

    def test_entrypoint_keeps_container_paths_and_listener_invariants(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            source_env = data_dir / "dashboard.env"
            source_env.write_text(
                "\n".join(
                    (
                        "DASHBOARD_HOME=/host/runtime",
                        "DASHBOARD_HOST=127.0.0.1",
                        "DASHBOARD_PORT=9999",
                        "PYTHON_BIN=/host/python",
                        "DASHBOARD_CONFIG=/host/config.yaml",
                        "DASHBOARD_NIUNIU_DB=/host/niuniu.db",
                        "NEWSNOW_BASE_URL=https://legacy-public.example/api/s",
                        "CUSTOM_FROM_ENV=loaded",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("NEWSNOW_BASE_URL", None)
            env.update(
                {
                    "DASHBOARD_ENV_FILE": str(source_env),
                    "NIUONE_CONTAINER_DATA_DIR": str(data_dir),
                    "NIUONE_CONTAINER_HOST": "0.0.0.0",
                    "NIUONE_CONTAINER_PORT": "8787",
                    "NIUONE_BUNDLED_NEWSNOW_URL": "http://newsnow:4444/api/s",
                    "PYTHON_BIN": sys.executable,
                }
            )
            container_python = Path(sys.executable).resolve()
            code = """
import json, os, sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd() / 'app' / 'compat'), str(Path.cwd() / 'app')]
import niuone_cron_scheduler
import niuone_dashboard
keys = (
    'DASHBOARD_ENV_FILE', 'DASHBOARD_HOME', 'DASHBOARD_HOST',
    'DASHBOARD_PORT', 'PYTHON_BIN', 'DASHBOARD_CONFIG',
    'DASHBOARD_NIUNIU_DB', 'CUSTOM_FROM_ENV'
)
result = {
    'process': {key: os.environ.get(key) for key in keys},
    'scheduler': {key: niuone_cron_scheduler.parse_env_file().get(key) for key in keys},
    'newsnow_process_base_url': os.environ.get('NEWSNOW_BASE_URL'),
    'bundled_newsnow_url': os.environ.get('NIUONE_BUNDLED_NEWSNOW_URL'),
    'newsnow_endpoint': niuone_dashboard.newsnow_config().endpoint,
}
niuone_dashboard.write_env_file_values({'DASHBOARD_RATE_LIMIT_ANON': '241'})
result['persisted'] = Path(os.environ['DASHBOARD_ENV_FILE']).read_text()
print(json.dumps(result))
"""
            output = subprocess.check_output(
                [
                    "bash",
                    str(ROOT / "scripts" / "docker-entrypoint.sh"),
                    str(container_python),
                    "-c",
                    code,
                ],
                cwd=ROOT,
                env=env,
                text=True,
            )
            values = json.loads(output)
            for name in ("process", "scheduler"):
                runtime_values = values[name]
                self.assertEqual(runtime_values["DASHBOARD_ENV_FILE"], str(data_dir / "dashboard.env"))
                self.assertEqual(runtime_values["DASHBOARD_HOME"], str(data_dir / "runtime"))
                self.assertEqual(runtime_values["DASHBOARD_HOST"], "0.0.0.0")
                self.assertEqual(runtime_values["DASHBOARD_PORT"], "8787")
                self.assertEqual(
                    Path(runtime_values["PYTHON_BIN"]).resolve(),
                    Path(container_python).resolve(),
                )
                self.assertEqual(runtime_values["DASHBOARD_CONFIG"], str(data_dir / "runtime" / "config.yaml"))
                self.assertEqual(runtime_values["DASHBOARD_NIUNIU_DB"], str(data_dir / "runtime" / "niuniu.db"))
                self.assertEqual(runtime_values["CUSTOM_FROM_ENV"], "loaded")
            self.assertIsNone(values["newsnow_process_base_url"])
            self.assertEqual(values["bundled_newsnow_url"], "http://newsnow:4444/api/s")
            self.assertEqual(values["newsnow_endpoint"], "http://newsnow:4444/api/s")
            self.assertTrue((data_dir / "runtime" / "cron" / "output").is_dir())
            self.assertIn("DASHBOARD_RATE_LIMIT_ANON=241", values["persisted"])
            self.assertNotIn("NIUONE_ROOT=", values["persisted"])
            self.assertNotIn("DASHBOARD_LOG_DIR=", values["persisted"])
            self.assertNotIn("DASHBOARD_B1_SCANNER=", values["persisted"])

    @unittest.skipIf(
        os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
        "POSIX non-root permissions are required",
    )
    def test_entrypoint_rejects_unwritable_account_runtime_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            output_dir = data_dir / "runtime" / "cron" / "output"
            output_dir.mkdir(parents=True)
            output_dir.chmod(0o500)
            env = os.environ.copy()
            env.update({
                "NIUONE_CONTAINER_DATA_DIR": str(data_dir),
                "DASHBOARD_ENV_FILE": str(data_dir / "missing.env"),
                "PYTHON_BIN": sys.executable,
            })
            try:
                result = subprocess.run(
                    [
                        "bash",
                        str(ROOT / "scripts" / "docker-entrypoint.sh"),
                        "true",
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
            finally:
                output_dir.chmod(0o700)

            self.assertEqual(result.returncode, 73)
            self.assertIn("runtime directory is not writable", result.stderr)

    @unittest.skipIf(
        os.name == "nt" or getattr(os, "geteuid", lambda: 0)() == 0,
        "POSIX non-root permissions are required",
    )
    def test_entrypoint_reports_uncreatable_runtime_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            runtime_dir = data_dir / "runtime"
            runtime_dir.mkdir(parents=True)
            runtime_dir.chmod(0o500)
            env = os.environ.copy()
            env.update({
                "NIUONE_CONTAINER_DATA_DIR": str(data_dir),
                "DASHBOARD_ENV_FILE": str(data_dir / "missing.env"),
                "PYTHON_BIN": sys.executable,
            })
            try:
                result = subprocess.run(
                    [
                        "bash",
                        str(ROOT / "scripts" / "docker-entrypoint.sh"),
                        "true",
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
            finally:
                runtime_dir.chmod(0o700)

            self.assertEqual(result.returncode, 73)
            self.assertIn(
                "runtime directories cannot be created",
                result.stderr,
            )

    def test_release_workflow_uses_tag_trigger_and_repository_credentials(self):
        path = ROOT / ".github" / "workflows" / "docker-publish.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertEqual(workflow["on"]["push"]["tags"], ["v*.*.*"])
        dispatch_input = workflow["on"]["workflow_dispatch"]["inputs"]["release_tag"]
        self.assertEqual(dispatch_input["required"], "true")
        self.assertEqual(dispatch_input["type"], "string")
        self.assertEqual(workflow["permissions"]["contents"], "read")
        uses = [step["uses"] for step in workflow["jobs"]["publish"]["steps"] if "uses" in step]
        for action in uses:
            self.assertRegex(action, r"@[0-9a-f]{40}$")
        self.assertIn("vars.DOCKERHUB_USERNAME", text)
        self.assertIn("secrets.DOCKERHUB_TOKEN", text)
        self.assertIn('DOCKERHUB_USERNAME" != "kunkundi', text)
        self.assertIn("linux/amd64,linux/arm64", text)
        self.assertIn("docker.io/${{ vars.DOCKERHUB_USERNAME }}/niuone", text)
        self.assertEqual(text.count("NIUONE_VERSION=${{ steps.release.outputs.tag }}"), 2)

    def test_manual_release_checks_out_and_verifies_the_existing_tag(self):
        path = ROOT / ".github" / "workflows" / "docker-publish.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["publish"]["steps"]
        checkout = next(step for step in steps if step["name"] == "Check out release source")
        verification = next(
            step for step in steps if step["name"] == "Verify release tag and commit"
        )
        self.assertIn("format('refs/tags/{0}', inputs.release_tag)", checkout["with"]["ref"])
        self.assertIn(
            'release_commit="$(git rev-parse "refs/tags/$RELEASE_TAG^{commit}")"',
            verification["run"],
        )
        self.assertIn('checked_out_commit="$(git rev-parse HEAD)"', verification["run"])
        self.assertIn('echo "tag=$RELEASE_TAG"', verification["run"])

    def test_release_smoke_test_waits_for_public_snapshot(self):
        path = ROOT / ".github" / "workflows" / "docker-publish.yml"
        workflow = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        steps = workflow["jobs"]["publish"]["steps"]
        smoke_test = next(
            step["run"] for step in steps if step.get("name") == "Smoke test container"
        )
        retry_loop = smoke_test.split("for _ in {1..120}; do", 1)[1].split("done", 1)[0]
        self.assertIn("/healthz", retry_loop)
        self.assertIn("/api/v2/public/latest", retry_loop)


if __name__ == "__main__":
    unittest.main()
