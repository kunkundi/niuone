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
        self.assertIn("httpx2>=2.0.0", requirements)

    def test_image_builds_vue_assets_and_packages_the_fastapi_runtime(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        self.assertIn("FROM node:24-bookworm-slim AS web-builder", dockerfile)
        self.assertIn("pnpm install --frozen-lockfile", dockerfile)
        self.assertIn("RUN pnpm run build", dockerfile)
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

    def test_compose_runs_all_long_lived_processes_with_shared_storage(self):
        config = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
        services = config["services"]
        self.assertEqual(set(services), {"dashboard", "scheduler", "x-watchlist"})
        self.assertEqual(services["dashboard"]["command"], ["dashboard"])
        self.assertEqual(services["scheduler"]["command"], ["scheduler"])
        self.assertEqual(services["x-watchlist"]["command"], ["x-watchlist"])
        for service in services.values():
            self.assertIn("niuone-data:/data", service["volumes"])

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
                        "CUSTOM_FROM_ENV=loaded",
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "DASHBOARD_ENV_FILE": str(source_env),
                    "NIUONE_CONTAINER_DATA_DIR": str(data_dir),
                    "NIUONE_CONTAINER_HOST": "0.0.0.0",
                    "NIUONE_CONTAINER_PORT": "8787",
                }
            )
            code = """
import json, os, sys
from pathlib import Path
sys.path[:0] = [str(Path.cwd() / 'app' / 'compat'), str(Path.cwd() / 'app')]
import niuone_cron_scheduler
import niuone_dashboard
import x_watchlist_daemon
keys = (
    'DASHBOARD_ENV_FILE', 'DASHBOARD_HOME', 'DASHBOARD_HOST',
    'DASHBOARD_PORT', 'PYTHON_BIN', 'DASHBOARD_CONFIG',
    'DASHBOARD_NIUNIU_DB', 'CUSTOM_FROM_ENV'
)
result = {
    'process': {key: os.environ.get(key) for key in keys},
    'scheduler': {key: niuone_cron_scheduler.parse_env_file().get(key) for key in keys},
    'watchlist': {key: x_watchlist_daemon.parse_env_file().get(key) for key in keys},
}
niuone_dashboard.write_env_file_values({'DASHBOARD_RATE_LIMIT_ANON': '241'})
result['persisted'] = Path(os.environ['DASHBOARD_ENV_FILE']).read_text()
print(json.dumps(result))
"""
            output = subprocess.check_output(
                [
                    "bash",
                    str(ROOT / "scripts" / "docker-entrypoint.sh"),
                    sys.executable,
                    "-c",
                    code,
                ],
                cwd=ROOT,
                env=env,
                text=True,
            )
            values = json.loads(output)
            for name in ("process", "scheduler", "watchlist"):
                runtime_values = values[name]
                self.assertEqual(runtime_values["DASHBOARD_ENV_FILE"], str(data_dir / "dashboard.env"))
                self.assertEqual(runtime_values["DASHBOARD_HOME"], str(data_dir / "runtime"))
                self.assertEqual(runtime_values["DASHBOARD_HOST"], "0.0.0.0")
                self.assertEqual(runtime_values["DASHBOARD_PORT"], "8787")
                self.assertEqual(Path(runtime_values["PYTHON_BIN"]).resolve(), Path(sys.executable).resolve())
                self.assertEqual(runtime_values["DASHBOARD_CONFIG"], str(data_dir / "runtime" / "config.yaml"))
                self.assertEqual(runtime_values["DASHBOARD_NIUNIU_DB"], str(data_dir / "runtime" / "niuniu.db"))
                self.assertEqual(runtime_values["CUSTOM_FROM_ENV"], "loaded")
            self.assertIn("DASHBOARD_RATE_LIMIT_ANON=241", values["persisted"])
            self.assertNotIn("NIUONE_ROOT=", values["persisted"])
            self.assertNotIn("DASHBOARD_LOG_DIR=", values["persisted"])
            self.assertNotIn("DASHBOARD_B1_SCANNER=", values["persisted"])

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
