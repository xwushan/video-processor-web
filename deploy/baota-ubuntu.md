# Ubuntu + 宝塔：离线部署视频处理器

适用：Ubuntu x86_64、Docker Engine + Compose v2、NVIDIA RTX 3090。镜像版本 1.3.8，CPU CRF 与 GPU CQ 默认均为 32。

使用宝塔的文件管理上传部署包，再通过宝塔终端执行 Compose。这样能完整保留 GPU 配置，减少不同面板版本的表单差异。启动后可在宝塔 Docker 的容器列表查看日志、状态与资源；参数更新在同一部署目录执行 Compose。宝塔支持镜像导入和 Compose，参考[官方 Docker 手册](https://www.bt.cn/bbs/thread-95674-1-1.html)。

本文用于首次部署。已有服务先按原配置备份，保持项目名和数据挂载路径；不要用空的 `data` 目录替代原数据。

## 1. 检查宿主机环境

在宝塔左侧“Docker”安装或检查 Docker 服务；若已经存在，不重复安装。打开宝塔终端，在 Ubuntu 宿主机执行：

```bash
uname -m
docker version
docker compose version
nvidia-smi
```

架构应为 `x86_64`，Compose 应为 v2，`nvidia-smi` 应识别 RTX 3090。镜像包含 Python 和 FFmpeg，宿主机无需单独安装它们。驱动与 NVIDIA Container Toolkit 属于宿主机依赖，需要提前准备。

若 `nvidia-smi` 已正常，保留现有驱动。驱动未安装时，可按 Ubuntu 的工具选择服务器驱动：

```bash
sudo apt-get update
sudo apt-get install -y ubuntu-drivers-common
sudo ubuntu-drivers list --gpgpu
sudo ubuntu-drivers install --gpgpu
```

按安装结果在维护时间重启服务器，重新确认 `nvidia-smi`。内网无软件源时先准备与 Ubuntu 版本、内核匹配的驱动软件包；不能仅凭 `lspci` 判断驱动可用。来源：[Ubuntu NVIDIA 驱动安装说明](https://ubuntu.com/server/docs/how-to/graphics/install-nvidia-drivers/)。

## 2. 配置容器 GPU 访问

若 GPU 容器已能正常运行，可跳过安装。否则，在可访问 NVIDIA 软件源的 Ubuntu 终端执行：

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

最后一步会重启 Docker 服务，应在其他容器允许重启时执行。内网受限时提前离线安装上述系统依赖；加载应用镜像本身不需要访问 Docker Hub 或 PyPI。来源：[NVIDIA 官方安装与 Docker 配置说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

## 3. 上传、解压并导入镜像

在宝塔“文件”中新建固定目录 `/www/server/docker/video-processor-web`，将交付的 `*-baota.zip` 上传并解压到该目录。确保该目录直接包含以下文件，而不是额外套一层目录：

```text
image.tar.gz             离线 Linux/amd64 镜像
docker-compose.yml       含 GPU、登录保护及数据持久化的独立编排
.env.example             已填写镜像标签，登录密码留空
DEPLOY.md                本部署说明
release.json             版本、完整提交号、镜像标签和镜像 ID
VALIDATION.md            本次发布验证结果
SHA256SUMS               文件校验值
validate_docker.py       上线后的功能自检
```

终端执行：

```bash
cd /www/server/docker/video-processor-web
sha256sum -c SHA256SUMS
docker load -i image.tar.gz
test -e .env || cp .env.example .env
chmod 600 .env
```

校验应全部显示 `OK`，然后才能导入。Docker 支持直接读取 gzip 压缩镜像，不必手动解压镜像。[Docker 导入说明](https://docs.docker.com/reference/cli/docker/image/load/)

这一步使用 `docker load` 导入完整镜像，不能用 `docker import`。也可用宝塔“Docker → 本地镜像 → 导入”选择镜像；若面板不支持 `.tar.gz`，使用上述终端命令。

## 4. 配置账号、数据目录与端口

在宝塔文件编辑器打开 `.env`。保留交付模板内的 `VIDEO_PROCESSOR_IMAGE_TAG` 和 `VIDEO_PROCESSOR_REVISION`，修改：

```dotenv
COMPOSE_PROJECT_NAME=video-processor-web
VIDEO_PROCESSOR_AUTH_USER=admin
VIDEO_PROCESSOR_AUTH_PASSWORD='填写自己的长随机密码'
VIDEO_PROCESSOR_PORT=8899
VIDEO_PROCESSOR_BIND_IP=0.0.0.0
VIDEO_PROCESSOR_DATA_DIR=/www/server/docker/video-processor-web/data
VIDEO_PROCESSOR_ENCODER_DEVICE=auto
VIDEO_PROCESSOR_GPU_DEVICE_ID=0
VIDEO_PROCESSOR_GPU_MAX_CONCURRENT=8
VIDEO_PROCESSOR_GPU_CPU_THREADS=4
```

密码必须填写，空密码会阻止启动。密码建议用单引号包住，尤其包含 `$`、空格或 `#` 时。不要把 `.env` 提交到 Git，或放进网站公开目录。

`VIDEO_PROCESSOR_DATA_DIR` 可改为容量足够的数据盘目录；下方创建目录和权限命令也同步替换。这里保存数据库、原视频、成品、水印和续传缓存。应用成品默认保留 14 天、记录保留 90 天，时间可在 `.env` 调整。

首次部署，在宿主机预先创建空的数据目录并让容器用户 UID/GID 1000 可写：

```bash
sudo install -d -m 0750 -o 1000 -g 1000 /www/server/docker/video-processor-web/data
df -h /www/server/docker/video-processor-web/data
```

编排不会自动创建错误路径，避免挂载错误而启动出一套空数据。已有目录里的文件也需给 UID/GID 1000 所需权限；先确认该路径只用于本项目，再处理权限，不要对其他服务目录执行递归权限修改。

## 5. 启动与检查

```bash
cd /www/server/docker/video-processor-web
docker compose config --quiet
docker compose up -d --no-build --pull never --wait --wait-timeout 90
docker compose ps
docker compose logs --tail=100
curl --fail http://127.0.0.1:8899/health
docker compose exec -T video-processor nvidia-smi
docker compose exec -T video-processor python -c 'from web_app.encoding import get_gpu_capabilities; print(get_gpu_capabilities())'
```

若修改了绑定 IP 或端口，健康检查地址也同步修改；只绑定服务器内网 IP 时，不能使用 `127.0.0.1` 检查。

应看到容器为 `healthy`，健康接口的 `version` 为 `1.3.8`、`revision` 与 `release.json` 一致；GPU 检测中 H.264 与 H.265 均可用。GPU 不可用且选择自动时可能回退 CPU，应根据检测原因检查驱动与透传。

使用自生成画面做真实 NVENC 验证：

```bash
docker compose exec -T video-processor ffmpeg -hide_banner -v error -f lavfi -i testsrc2=size=1280x720:rate=30 -t 1 -c:v h264_nvenc -preset p4 -f null -
docker compose exec -T video-processor ffmpeg -hide_banner -v error -f lavfi -i testsrc2=size=1280x720:rate=30 -t 1 -c:v hevc_nvenc -preset p4 -f null -
```

两条命令均正常退出才确认编码可用。不要仅依靠编码器列表判断。

## 6. 内网访问与宝塔反向代理

直接访问时，在服务器网络和宝塔安全设置中允许所需内网客户端连接 8899，浏览器打开 `http://Ubuntu服务器IP:8899`，输入 `.env` 设置的账号和密码。服务器 IP 使用 Ubuntu 的实际地址。

可将 `VIDEO_PROCESSOR_BIND_IP` 改为服务器内网 IP，限制监听范围。Docker 发布端口可能绕过 UFW，不能仅根据宝塔或 UFW 的列表推断端口已经被阻止。[Docker 防火墙说明](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations)

若通过宝塔网站绑定域名并启用 HTTPS，先将 `.env` 的 `VIDEO_PROCESSOR_BIND_IP` 改为 `127.0.0.1`，重新运行同一个 `docker compose up` 命令。然后在宝塔的网站/反向代理界面设置目标为 `http://127.0.0.1:8899`。

本应用使用 8 MiB 分块上传。Nginx 的代理 `location` 中可采用：

```nginx
client_max_body_size 64m;
proxy_connect_timeout 30s;
proxy_send_timeout 300s;
proxy_read_timeout 300s;
proxy_request_buffering off;
proxy_buffering off;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Authorization $http_authorization;
```

保留宝塔生成的 `proxy_pass`，按现有配置补充指令，避免同一层级重复定义。不要缓存登录接口、`/api/` 或样片地址，避免复用已失效的预览。若直接调用普通上传接口发送整个视频，`client_max_body_size` 需按实际单文件大小调整；64m 适用于当前网页的分块上传。

在另外一台电脑通过实际访问地址，验证选择视频 → 预估 → 查看抽中的最多 3 个样片 → 开始制作 → 下载。刷新、调整参数后样片应清理；正式制作应包含全部视频。

## 7. 资源统计与上线验收

Ubuntu 原生 Docker 中，当前 CPU 和内存采集通常反映宿主机全局状态；GPU 编码器与显存反映所选 RTX 3090 的硬件状态，包括其他程序。部署后与宿主机的 `htop`、`free -h`、`nvidia-smi` 对照，采样和内存缓存算法可能造成数值差异。

宝塔 Docker 的容器占用属于另一种统计范围，不必与网页的全局负载相等。此编排没有 CPU/内存配额；以后增加配额时，还需单独检查容器是否达到上限。

自动并发从 1 路增长，新增任务前检查 CPU、内存、GPU 编码器、GPU 利用率及显存是否达到 80%，默认最多 8 路。80% 是停止增加任务的阈值，已运行任务仍可能超过此值。

完整自检应在任务空闲时执行，会制作短测试视频并清理它创建的任务；若开启通知，测试也可能发出通知：

```bash
cd /www/server/docker/video-processor-web
export VIDEO_PROCESSOR_AUTH_USER=admin
read -r -s -p '验证用登录密码: ' VIDEO_PROCESSOR_AUTH_PASSWORD; echo
export VIDEO_PROCESSOR_AUTH_PASSWORD
python3 validate_docker.py --gpu --container "$(docker compose ps -q video-processor)" --url http://127.0.0.1:8899
unset VIDEO_PROCESSOR_AUTH_PASSWORD
```

账号、IP 和端口如有修改，以上命令也同步修改。自检覆盖 H.264/H.265/MKV、水印、音轨、下载、ZIP、断点续传、样片 Range、清理、同名文件及暂停恢复。

## 8. 更新、备份与回滚

更新前停止上传，等待任务结束，在同一项目目录执行：

```bash
cd /www/server/docker/video-processor-web
stamp="$(date +%Y%m%d-%H%M%S)"
sudo install -d -m 0700 /www/backup/video-processor-web
docker inspect --format '{{.Config.Image}}' "$(docker compose ps -q video-processor)" > "/www/backup/video-processor-web/$stamp-image.txt"
docker compose stop
sudo tar -czf "/www/backup/video-processor-web/$stamp-data.tgz" -C /www/server/docker/video-processor-web data .env docker-compose.yml
sudo chmod 600 "/www/backup/video-processor-web/$stamp-data.tgz"
sudo tar -tzf "/www/backup/video-processor-web/$stamp-data.tgz" > /dev/null
```

任何备份步骤失败，先用 `docker compose start` 恢复原服务，再排查。自定义数据盘时，备份命令同步改为实际数据路径；不要只备份数据库，视频和水印也要保留。

上传新包到另一临时目录，校验并加载新镜像；将新 `docker-compose.yml` 复制到固定项目目录，保留原 `.env`、项目名和数据目录，只把镜像标签、提交号更新为新包中的值。仍用 `--no-build --pull never` 启动并检查健康接口。

回滚时，在 `.env` 把 `VIDEO_PROCESSOR_IMAGE_TAG` 改回备份记录的旧标签，重新执行启动命令，复用现有数据。先保留旧镜像；健康接口的 revision 来自镜像，会随回滚恢复。若必须恢复数据快照，停止服务后恢复到新目录，修改数据挂载指向新目录，再启动；保留原目录供核对。

不删除 `data`，不运行 `docker compose down -v`，不同时启动两个应用副本访问同一数据目录。应用必须保持一个容器、一个 Uvicorn worker。

镜像验证可在本机完成，但 Ubuntu RTX 3090 的最终驱动、宝塔配置和真实吞吐量，需要按本文在目标服务器验收。
