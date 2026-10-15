# Ubuntu 内网服务器：RTX 3090 部署

目标服务器为双路 Xeon Platinum 8163（48 个物理核心、96 个逻辑线程）、约 125 GiB 内存和 RTX 3090。
RTX 3090 支持 H.264、HEVC 的 NVENC 编码；本版本使用 GPU 编码、CPU 解码和水印，保持现有固定/动态水印逻辑。
GPU 支持依据：[NVIDIA 官方支持矩阵](https://developer.nvidia.com/video-encode-decode-support-matrix)。

已经通过宝塔导入镜像并部署成功的服务器保持现有项目；宝塔操作以 [宝塔 Docker 指南](baota-ubuntu.md) 为准，其他方式见 [部署总览](README.md)。本页补充 GPU 宿主机环境。

## 1. 确认驱动

在 Ubuntu 宿主机执行：

```bash
nvidia-smi
docker version
docker compose version
```

`lspci` 识别到显卡只说明硬件存在，`nvidia-smi` 正常返回才说明驱动已工作。
如果驱动未安装或损坏，先按服务器 Ubuntu 版本安装 NVIDIA 驱动，再继续。
不要把宿主机显卡驱动装进应用镜像。

若提示 `Driver/library version mismatch`，先读取实际版本：

```bash
cat /proc/driver/nvidia/version
modinfo -F version nvidia
sudo dmesg -T | grep -Ei 'NVRM|API mismatch' | tail -n 20
```

本次现场已加载模块为 580.173.02，磁盘模块/库为 580.178.04，版本一致后恢复正常。升级后的旧模块仍加载时，通常在维护窗口重启服务器；如果磁盘模块与库也不一致，先核对驱动安装状态。重启整机、Docker 或重载驱动可能影响其他服务，按维护流程处理。[Ubuntu 官方驱动故障说明](https://ubuntu.com/server/docs/nvidia-drivers-installation/)

## 2. 安装 NVIDIA Container Toolkit

已经安装且能正常向容器提供 GPU 的服务器可以跳过此节。
以下使用 NVIDIA 官方稳定软件源，需要服务器可访问该源；内网受限时先准备对应 Ubuntu 的离线软件包。

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends ca-certificates curl gnupg2
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

最后一步会重启 Docker，请在服务器其他容器允许重启时执行。
来源：[NVIDIA Container Toolkit 安装与 Docker 配置说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

## 3. 启动应用

将本次修改后的项目放到服务器目录，在项目根目录执行：

```bash
# 第一次部署时执行，已有 .env 时保留原配置。
cp .env.example .env
```

推荐初始配置（写入 `.env`）：

```dotenv
VIDEO_PROCESSOR_ENCODER_DEVICE=auto
VIDEO_PROCESSOR_GPU_DEVICE_ID=0
VIDEO_PROCESSOR_GPU_MAX_CONCURRENT=8
VIDEO_PROCESSOR_GPU_CPU_THREADS=4
VIDEO_PROCESSOR_AUTH_USER=admin
VIDEO_PROCESSOR_AUTH_PASSWORD='填写自己的登录密码'
```

```bash
chmod 600 .env
export VIDEO_PROCESSOR_REVISION="$(git rev-parse HEAD)"
export VIDEO_PROCESSOR_IMAGE_TAG="$VIDEO_PROCESSOR_REVISION"
docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml up -d --build --wait
docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml ps
docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml logs --tail=100
```

把完整提交号保存到 .env 的镜像标签和 revision，后续管理使用相同的三个 Compose 文件。完整发布与回滚步骤见 [Docker 部署](docker.md)。

访问 `http://服务器IP:8899`。页面应显示服务器 GPU 名称；选择自动时显示 GPU 的 CQ 和策略，
并保留独立的 CPU 参数用于回退。CPU CRF 和 GPU CQ 均默认为 32，可先以默认参数处理代表性视频，确认画质、体积及总耗时。

验证容器内 H.264 硬件编码：

```bash
docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml exec -T video-processor ffmpeg -hide_banner -v error -f lavfi -i testsrc2=size=1280x720:rate=30 -t 1 -c:v h264_nvenc -preset p4 -f null -
```

正常完成并返回退出码 0 即表示 GPU 编码可用。检查页面前需确认应用容器已启用 GPU 透传。
宿主机 `nvidia-smi` 正常，或 FFmpeg 列出 `h264_nvenc`，都不能代替容器内实际试编码。

旧服务数据迁移请先完整备份，并遵照 [运维手册](operations.md) 中保持绝对路径的说明。
不要让旧服务和 Docker 服务同时读写同一个数据目录。

## 4. 内网不能拉取镜像时

按 [Docker 离线导入步骤](docker.md#3-离线导入已交付镜像) 保存和加载镜像，使用相同标签的 panel 编排，填写非空登录密码；也可直接参考 [宝塔图形部署](baota-ubuntu.md)。不需要在服务器下载基础镜像或 Python 依赖。

离线加载应用镜像仍需要宿主机已安装 NVIDIA 驱动和 Container Toolkit。

## 5. 调整吞吐量

并发从 1 路自动增加。新增任务运行 5 秒后采样，在 GPU 编码器、显存、GPU 利用率、CPU 和
系统内存均低于 80% 且磁盘余量充足时继续启动下一条；达到阈值时停止增加，稍后重新检查。
该阈值不限制已运行任务的瞬时负载；监控不可用时保持单路。
默认安全上限为 8 路，页面无需手选。旧 `.env` 若设置上限为 2，需改成 8 并重新创建容器。
显存容量不会直接决定编码速度，多任务共享硬件编码器，也会受到源视频解码、CPU 水印、
磁盘读写、其他 GPU 任务和驱动的会话数量限制影响。
来源：[NVIDIA NVENC 应用说明](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-application-note/index.html)、
[nvidia-smi 监控指标定义](https://docs.nvidia.com/deploy/nvidia-smi/index.html)。

使用 `nvidia-smi dmon -s u` 查看编码器指标（驱动支持时），并结合页面处理速度观察。
单个应用容器和 `--workers 1` 保持不变。GPU 不可用时如需继续运行，
可只使用基础 Compose 文件启动，并选择自动或 CPU 模式。
