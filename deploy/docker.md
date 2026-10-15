# Docker 命令行完整部署

宝塔图形界面请使用 [宝塔 Docker 文档](baota-ubuntu.md)。本文适用于 Linux Docker Engine + Compose v2 或更新版本，以及 Docker Desktop Linux 容器。

## 1. 准备与配置选择

```bash
docker version
docker compose version
```

Docker 镜像包含 Python、FFmpeg/FFprobe 和运行依赖。宿主机无需重复安装它们；GPU 仍需宿主机 NVIDIA 驱动及 Container Toolkit。

本仓库有两套配置，升级始终沿用原来的一套：

| 配置族 | 场景 | 数据卷 |
| --- | --- | --- |
| 根目录 compose.yaml，加 production/nvidia 覆盖 | 从源码构建 | `<原项目名>_video-data` |
| deploy/compose.baota-panel.cpu/gpu.yaml | 已导入镜像，也供宝塔使用 | `video-processor-web-data` |

不要在已有数据时直接换配置族，它们的默认卷名不同。数据卷/路径的迁移见 [运维手册](operations.md)。

## 2. 从源码在线构建

首次部署：

```bash
git clone --branch codex/docker-gpu-release --single-branch https://github.com/xwushan/video-processor-web.git
cd video-processor-web
test -f .env || cp .env.example .env
```

编辑 `.env`，填写自己的 `VIDEO_PROCESSOR_AUTH_PASSWORD`。保留时间、端口、GPU 并发上限可同时调整。密码用单引号包住，`.env` 不进 Git。

在同一终端标记本次构建版本：

```bash
export VIDEO_PROCESSOR_REVISION="$(git rev-parse HEAD)"
export VIDEO_PROCESSOR_IMAGE_TAG="$VIDEO_PROCESSOR_REVISION"
```

第一次可先使用 CPU 生产配置：

```bash
docker compose -f compose.yaml -f compose.production.yaml config --quiet
docker compose -f compose.yaml -f compose.production.yaml up -d --build --wait --wait-timeout 90
```

GPU 已准备好时，使用相同项目和基础文件增加 NVIDIA 配置：

```bash
nvidia-smi
docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml config --quiet
docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml up -d --build --wait --wait-timeout 90
```

本次使用的镜像标签应记录到 `.env` 的 `VIDEO_PROCESSOR_IMAGE_TAG`，完整提交号记录到 `VIDEO_PROCESSOR_REVISION`，下次打开终端才能继续解析同一镜像。只有构建新版本时才改这两个值。生产覆盖文件要求非空密码和标签，配置解析失败时先修复配置。

GPU 后续日志、停止、更新命令均带相同三个 `-f` 文件；CPU 使用对应两个文件。可定义当前终端函数：

```bash
dc() { docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml "$@"; }
dc ps
dc logs --tail=100
```

访问 `http://服务器IP:8899`。端口以 `.env` 为准；更改后重新部署使映射生效。

## 3. 离线导入已交付镜像

服务器不需要下载源码依赖。准备 `image.tar`、两份 panel 编排和环境模板，可只上传这些文件，或上传整个源码目录。

```bash
docker load -i image.tar
docker image inspect video-processor-web:1.3.8-787b7c9 --format '{{.Os}}/{{.Architecture}} {{.Id}}'
```

在部署目录复制模板为 `.env`，填写密码和导入的真实镜像标签。以下以文件仍位于仓库 `deploy/` 下为例：

```bash
test -f .env || cp deploy/baota-panel.env.example .env
```

CPU 启动：

```bash
docker compose --env-file .env -p video-processor-web -f deploy/compose.baota-panel.cpu.yaml up -d --no-build --pull never --wait --wait-timeout 90
```

GPU 启动或切换：

```bash
docker compose --env-file .env -p video-processor-web -f deploy/compose.baota-panel.gpu.yaml up -d --no-build --pull never --wait --wait-timeout 90
```

如果编排文件直接放在部署目录，命令中的文件路径同步去掉 `deploy/`。两阶段项目名保持不变。已在宝塔创建的项目，应优先在宝塔更新，不另建一个项目竞争同一数据卷和端口。

导出自己的镜像，在构建机器执行：

```bash
docker save -o image.tar "video-processor-web:$VIDEO_PROCESSOR_IMAGE_TAG"
sha256sum image.tar > image.tar.sha256
```

传输后可运行 `sha256sum -c image.tar.sha256`。使用 `docker load` 加载完整镜像；`docker import` 是另一种文件系统导入方式，不用于此交付包。

## 4. 验证 GPU 与服务

Ubuntu 宿主机 `nvidia-smi` 正常后，在**宿主机终端**使用已导入镜像做真实试编码：

```bash
docker run --rm --gpus device=0 -e NVIDIA_DRIVER_CAPABILITIES=compute,video,utility video-processor-web:1.3.8-787b7c9 python -c "from web_app.encoding import get_gpu_capabilities; print(get_gpu_capabilities())"
```

自己的新镜像应替换成真实标签。H.264 和 H.265 两个 available 都为 True，才证明两种编码实际可用。这是短时检测命令，放在宿主机终端，不作为宝塔常驻容器创建命令。

```bash
curl --fail http://127.0.0.1:8899/health
```

登录后使用一个短视频完成预估、样片播放、制作与下载。完整自检需在空闲时运行，账号密码输入方式及容器名参数见 [生产发布手册](production-release.md)。

## 5. 数据、端口和更新

容器内固定数据路径为 `/data/video-processor`。命名卷在 Docker 中持久保存，重建容器仍复用；需要宿主机目录时，预建目录并赋予 UID/GID 1000 读写权限，参考 [compose.baota.yaml](compose.baota.yaml)。

生产可绑定内网地址；启用同机反向代理时将 `VIDEO_PROCESSOR_BIND_IP=127.0.0.1`，按 [代理文档](reverse-proxy.md) 设置域名和 HTTPS。Docker 端口发布的实际防火墙行为以网络测试为准。

更新前等待任务结束，先构建/导入新镜像，再停止应用备份，最后在同一项目切换镜像标签。原版本镜像及数据备份保留到验收完成；[备份、回滚与迁移](operations.md) 提供完整步骤。

参考：[Docker Compose 环境变量](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/)、[GPU 设备配置](https://docs.docker.com/compose/how-tos/gpu-support/)。
