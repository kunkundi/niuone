# Container Image Release Process

[简体中文](CONTAINER_RELEASE.md) | English

This document is intended for NiuOne maintainers and explains how to build and publish Docker images through GitHub Actions. The release logic in [`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml) is authoritative. If this document differs from the workflow, follow the workflow and update this document accordingly.

## 1. Release Target

Images are published to Docker Hub:

```text
docker.io/kunkundi/niuone
```

Production images support both of the following platforms:

- `linux/amd64`
- `linux/arm64`

## 2. Repository Configuration

Configure the following under **Settings → Secrets and variables → Actions** in the GitHub repository:

| Type | Name | Requirement |
|---|---|---|
| Repository variable | `DOCKERHUB_USERNAME` | Must be `kunkundi` |
| Repository secret | `DOCKERHUB_TOKEN` | A Docker Hub access token with push permission for `kunkundi/niuone` |

Before logging in to Docker Hub, the workflow checks both settings. The release stops immediately if either setting is missing or if the username does not match.

## 3. Release Conditions

Pushing a Git tag whose name matches `v*.*.*` triggers the container release workflow. The workflow then requires the tag to match the following format exactly:

```text
vMAJOR.MINOR.PATCH
```

For example:

```text
v0.0.1
v1.2.3
v10.0.0
```

Prerelease versions, build metadata, versions without the `v` prefix, and versions with leading zeros are not accepted. Examples of invalid tags include `v1.2.3-rc.1`, `1.2.3`, and `v01.2.3`.

The commit referenced by the tag must also be in the commit history of the repository's default branch. To avoid releasing unmerged code, complete the merge first, then create the tag from the updated default branch.

## 4. Starting a Release

Before releasing, synchronize the default branch and complete local validation:

```bash
git switch main
git pull --ff-only
./scripts/validate.sh
```

After confirming the version number, create and push the tag:

```bash
git tag v1.2.3
git push origin v1.2.3
```

All container releases share a single concurrency group. Push only one version tag at a time, and wait for its workflow to finish before publishing the next version.

Do not move or overwrite a version tag that has already been published. To fix a published version, commit the fix and release a higher patch version.

## 5. GitHub Actions Workflow

The workflow performs the following steps in order on an `ubuntu-latest` runner:

1. Check out the complete Git history, then validate the tag format and commit ancestry.
2. Configure Python 3.11 and Node.js 24, install dependencies, and run `./scripts/validate.sh`.
3. Validate the Docker Hub username and access token.
4. Configure QEMU and Docker Buildx, then log in to Docker Hub.
5. Generate the image name, version tags, and OCI metadata from the Git tag.
6. Build the test-only `linux/amd64` image `niuone:smoke` and load it into the runner's local Docker daemon.
7. Start a test container and check `/healthz` and `/api/v2/public/latest`. If a check fails, print the container logs and stop the release.
8. Build the `linux/amd64` and `linux/arm64` multi-platform image and push it to Docker Hub.
9. Decide whether to update `latest` based on version ordering.
10. Write the image tags, digest, and `latest` update result to the GitHub Actions Summary.

Release builds use the GitHub Actions cache and generate provenance and an SBOM for production images.

## 6. Image Tagging Rules

For a Git tag such as `v1.2.3`, the workflow always pushes:

```text
kunkundi/niuone:v1.2.3
kunkundi/niuone:1.2.3
```

Only when `v1.2.3` is the highest strict SemVer tag in the default branch history does the workflow apply the following tag to the same image digest:

```text
kunkundi/niuone:latest
```

As a result, publishing again or rerunning a lower version cannot roll `latest` back. `niuone:smoke` exists only on the GitHub Actions runner and is never pushed to Docker Hub.

## 7. Build Contents

GitHub Actions uses the repository root as the build context and the root-level `Dockerfile`. The Python base image keeps its default version, while the release tag is injected into the image for the dashboard's current-version display:

```text
PYTHON_VERSION=3.11
NIUONE_VERSION=v1.2.3
```

Every dashboard page load requests the local `/api/version` endpoint. The server periodically checks strict SemVer tags on Docker Hub and shows the current version and any available update in the header. A temporary Docker Hub failure does not affect other dashboard features.

By default, `.dockerignore` excludes the entire repository and allows only the following files required for the build into the context:

- `Dockerfile`
- `.dockerignore`
- `requirements.txt`
- `app/`
- `frontend/`
- Vue/Vite source, configuration, and dependency lock under `web/`
- `scripts/docker-entrypoint.sh`

Docker installs the locked pnpm dependencies and creates `web/dist/` in a Node.js 24 build stage, then copies only the build output into the Python runtime image. Tests, Git history, Node.js, frontend dependencies, local runtime data, and other repository files are not included in the final image. Runtime configuration, databases, logs, and credentials should be stored in the container's `/data` volume.

## 8. Release Verification

On the GitHub Actions page, confirm that the `Publish container image` workflow succeeded and review the run Summary. You can also inspect the published multi-platform image locally:

```bash
docker buildx imagetools inspect kunkundi/niuone:1.2.3
```

Verify that the specific version can be pulled successfully:

```bash
docker pull kunkundi/niuone:1.2.3
```

Prefer immutable version tags for deployments instead of relying only on `latest`.

## 9. Common Causes of Failure

- The tag does not use the strict `vMAJOR.MINOR.PATCH` format.
- The commit referenced by the tag is not in the default branch history.
- `DOCKERHUB_USERNAME` or `DOCKERHUB_TOKEN` is not configured.
- `DOCKERHUB_USERNAME` is not `kunkundi`.
- Project validation or unit tests fail.
- The amd64 smoke-test container cannot start, or a health endpoint check fails.
- The Docker Hub token lacks push permission, or the registry is temporarily unavailable.

After correcting the cause, you can rerun the failed workflow. If the fix changes source code, create a new patch version instead of moving an existing tag to a new commit.
