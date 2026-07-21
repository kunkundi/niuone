# 容器镜像发布流程

简体中文 | [English](CONTAINER_RELEASE_EN.md)

本文档面向 NiuOne 维护者，说明如何通过 GitHub Actions 构建并发布 Docker 镜像。发布逻辑以 [`.github/workflows/docker-publish.yml`](../.github/workflows/docker-publish.yml) 为准；如果本文档与工作流不一致，应以工作流为准并同步更新本文档。

## 1. 发布目标

镜像发布到 Docker Hub：

```text
docker.io/kunkundi/niuone
```

正式镜像同时支持以下平台：

- `linux/amd64`
- `linux/arm64`

## 2. 仓库配置

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中配置：

| 类型 | 名称 | 要求 |
|---|---|---|
| Repository variable | `DOCKERHUB_USERNAME` | 必须为 `kunkundi` |
| Repository secret | `DOCKERHUB_TOKEN` | 具有 `kunkundi/niuone` 推送权限的 Docker Hub access token |

工作流会在登录 Docker Hub 前检查这两项配置，缺失或用户名不匹配时会直接终止发布。

## 3. 发布条件

推送名称匹配 `v*.*.*` 的 Git tag 时会触发容器发布工作流。工作流随后要求 Tag 严格符合：

```text
vMAJOR.MINOR.PATCH
```

例如：

```text
v0.0.1
v1.2.3
v10.0.0
```

不接受预发布版本、构建元数据、缺少 `v` 前缀或带前导零的版本，例如 `v1.2.3-rc.1`、`1.2.3`、`v01.2.3`。

Tag 指向的提交还必须位于仓库默认分支的提交历史中。为避免发布未合并代码，应先完成合并，再从更新后的默认分支创建 Tag。

## 4. 发起发布

发布前先同步默认分支并在本地完成验证：

```bash
git switch main
git pull --ff-only
./scripts/validate.sh
```

确认版本号后创建并推送 Tag：

```bash
git tag v1.2.3
git push origin v1.2.3
```

所有容器发布共用同一个并发组。一次只推送一个版本 Tag，并等待对应工作流结束后再发下一个版本。

不要移动或覆盖已经发布的版本 Tag。需要修复已发布版本时，应提交修复并发布更高的补丁版本。

## 5. GitHub Actions 执行流程

工作流在 `ubuntu-latest` runner 上依次执行：

1. Checkout 完整 Git 历史，并验证 Tag 格式及提交归属。
2. 配置 Python 3.11 和 Node.js 24，安装依赖并运行 `./scripts/validate.sh`。
3. 检查 Docker Hub 用户名和 access token。
4. 配置 QEMU 与 Docker Buildx，并登录 Docker Hub。
5. 根据 Git tag 生成镜像名称、版本标签和 OCI 元数据。
6. 构建仅供测试的 `linux/amd64` 镜像 `niuone:smoke`，加载到 runner 本地。
7. 启动测试容器，检查 `/healthz` 和 `/api/v2/public/latest`；测试失败时输出容器日志并终止发布。
8. 构建 `linux/amd64`、`linux/arm64` 多平台镜像并推送 Docker Hub。
9. 根据版本顺序决定是否更新 `latest`。
10. 将镜像标签、digest 和 `latest` 更新结果写入 GitHub Actions Summary。

发布构建使用 GitHub Actions cache，并为正式镜像生成 provenance 和 SBOM。

## 6. 镜像标签规则

以 Git tag `v1.2.3` 为例，工作流固定推送：

```text
kunkundi/niuone:v1.2.3
kunkundi/niuone:1.2.3
```

只有当 `v1.2.3` 是默认分支历史中版本号最高的严格 SemVer Tag 时，工作流才会把同一镜像 digest 标记为：

```text
kunkundi/niuone:latest
```

因此，补发或重新运行较低版本不会让 `latest` 回退。`niuone:smoke` 只存在于 GitHub Actions runner，不会推送到 Docker Hub。

## 7. 构建内容

GitHub Actions 使用仓库根目录作为构建上下文，并使用根目录的 `Dockerfile`。Python 基础镜像使用默认版本，同时把发布 Tag 注入镜像作为页面显示的当前版本：

```text
PYTHON_VERSION=3.11
NIUONE_VERSION=v1.2.3
```

看板每次打开都会请求本机的 `/api/version`；服务端定期查询 Docker Hub 的严格 SemVer 标签，并在页面顶部显示当前版本和可用更新。Docker Hub 暂时不可用不会影响其他看板功能。

`.dockerignore` 默认排除整个仓库，只允许构建所需的以下内容进入上下文：

- `Dockerfile`
- `.dockerignore`
- `requirements.txt`
- `app/`
- `frontend/`
- `web/` 中的 Vue/Vite 源码、配置和依赖锁
- `scripts/docker-entrypoint.sh`

Docker 使用 Node.js 24 构建阶段安装锁定的 pnpm 依赖并生成 `web/dist/`，随后只把构建产物复制进 Python 运行镜像。测试文件、Git 历史、Node.js、前端依赖、本地运行数据和仓库中的其他文件不会打入最终镜像。运行配置、数据库、日志和凭据应保存在容器的 `/data` volume 中。

## 8. 发布验证

在 GitHub Actions 页面确认 `Publish container image` 工作流成功，并检查该次运行的 Summary。也可以在本地检查已发布的多平台镜像：

```bash
docker buildx imagetools inspect kunkundi/niuone:1.2.3
```

验证指定版本可以正常拉取：

```bash
docker pull kunkundi/niuone:1.2.3
```

部署时优先使用不可变的版本标签，不要仅依赖 `latest`。

## 9. 常见失败原因

- Tag 不符合严格的 `vMAJOR.MINOR.PATCH` 格式。
- Tag 指向的提交不在默认分支历史中。
- `DOCKERHUB_USERNAME` 或 `DOCKERHUB_TOKEN` 未配置。
- `DOCKERHUB_USERNAME` 不是 `kunkundi`。
- 项目验证或单元测试失败。
- amd64 冒烟容器未能启动，或健康接口检查失败。
- Docker Hub token 无推送权限或 registry 暂时不可用。

修复原因后，可以重新运行失败的工作流；如果修复涉及源码变更，应创建新的补丁版本，不要把已有 Tag 移动到新的提交。
