# 公司 Ubuntu 服务器：在宝塔 Docker 导入镜像并部署

适用：Ubuntu x86_64、已安装宝塔和 Docker、NVIDIA RTX 3090。交付的是 **Linux/amd64 Docker 镜像**。应用版本 1.3.8，CPU CRF 与 GPU CQ 默认均为 32。

部署流程：**宝塔文件上传并解压交付包 → 宝塔 Docker 导入 image.tar → 创建容器 → 浏览器访问**。镜像已包含应用、Python、FFmpeg 和运行依赖，导入及启动应用无需从 Docker Hub 拉取镜像，也不用在服务器安装项目源码。

以下以宝塔界面操作为主。不同版本菜单可能显示“镜像”或“本地镜像”；GPU 表单选项以实际面板为准。导入镜像、创建容器及端口/目录/环境变量配置见[宝塔官方 Docker 手册](https://www.bt.cn/bbs/thread-95674-1-1.html)。

## 1. 上传部署包并导入镜像

1. 在宝塔“文件”中新建 `/www/server/docker/video-processor-web`。
2. 上传交付的 `*-baota.zip`，在宝塔文件管理中解压到这个目录。该目录下应直接出现 `image.tar`。
3. 进入 **Docker → 镜像/本地镜像 → 导入镜像**，选择服务器上的 `/www/server/docker/video-processor-web/image.tar`。
4. 等待导入完成，在本地镜像列表确认出现 `video-processor-web:1.3.8-<提交号>`。**完整标签见交付包的 `release.json`，创建容器时选择这个标签。**

交付包内容：

| 文件 | 用途 |
| --- | --- |
| `image.tar` | 可直接导入的 Docker 镜像，包含 CPU 和 NVIDIA 编码能力 |
| `DEPLOY.md` | 本说明 |
| `CONTAINER-SETTINGS.txt` | 当前发布镜像的容器配置速查表 |
| `docker-compose.yml` | 宝塔容器编排备用配置，包含 GPU 透传和运行限制 |
| `.env.example` | 编排参数模板，镜像标签已填，登录密码需自己设置 |
| `release.json` | 版本、完整 Git 提交号、镜像标签 |
| `VALIDATION.md` | 发布检查结果及适用范围 |
| `SHA256SUMS` | 包内文件校验值 |
| `validate_docker.py` | 部署后可选的功能自检脚本 |

需要核对传输完整性时，可在宝塔终端进入上述目录执行 `sha256sum -c SHA256SUMS`，应全部为 `OK`。镜像来自 `docker save`，对应的是加载镜像；不要选择把文件系统转换为新镜像的 `docker import`。

## 2. 准备数据目录

在宝塔文件管理中新建 `/www/server/docker/video-processor-web/data`，用于保存数据库、原视频、成品、水印和续传数据。也可以使用服务器容量足够的数据盘目录，后面挂载时填写实际路径。

镜像使用 UID/GID **1000:1000** 的普通用户运行。首次部署可在宝塔终端设置这个新目录的权限：

```bash
sudo install -d -m 0750 -o 1000 -g 1000 /www/server/docker/video-processor-web/data
df -h /www/server/docker/video-processor-web/data
```

**数据目录所在磁盘须至少剩余 20 GiB，另外预留原视频和成品所需空间。** 默认低于 20 GiB 会拒绝上传并返回 HTTP 507，避免处理过程中磁盘耗尽。这是数据盘剩余空间阈值，不是为容器分配 20 GB。

已有部署应继续挂载原数据目录并先备份。不要以新建空目录替换已有数据。已有文件的权限也需要允许 UID 1000 读写；只处理本项目目录。

## 3. 确认服务器能向 Docker 提供 GPU

CPU 编码不需要这一步；使用 RTX 3090 编码时需要 **Ubuntu NVIDIA 驱动 + NVIDIA Container Toolkit**。这两项属于服务器配置，不能只靠导入应用镜像替代。宝塔安装 Docker 后也需要满足这两个条件。

在宝塔终端执行宿主机命令：

```bash
nvidia-smi
docker version
```

`nvidia-smi` 应识别 RTX 3090。如果已有 GPU 容器能正常使用这张卡，可保留现有配置。

若尚未安装 Container Toolkit，在服务器能访问 NVIDIA 软件源时执行：

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

最后一步会重启 Docker 服务，应安排在其他容器允许重启时执行。公司内网不能访问软件源时，由运维提前准备匹配系统的离线安装包。来源：[NVIDIA 官方说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

驱动尚未安装时，按公司维护流程和 [Ubuntu NVIDIA 驱动说明](https://ubuntu.com/server/docs/how-to/graphics/install-nvidia-drivers/)安装服务器驱动；仅有 `lspci` 输出不能确认编码可用。

## 4. 在宝塔创建容器

在导入的镜像上点击“创建容器”，或在“容器”中添加并选择该镜像。填写：

| 宝塔字段 | 设置 |
| --- | --- |
| 容器名称 | `video-processor-web` |
| 镜像 | 选择 `release.json` 中的完整镜像标签 |
| 网络 | `bridge` |
| 端口映射 | **宿主机 8899 → 容器 8899 / TCP**；添加后确认已出现在映射列表 |
| 挂载目录 | **宿主机 `/www/server/docker/video-processor-web/data` → 容器 `/data/video-processor`**，读写；添加后确认已生效 |
| 启动命令 | **留空**，使用镜像默认命令 |
| 重启规则 | `unless-stopped`，或面板提供的自动重启规则 |
| 停止后自动删除 | 不勾选 |
| CPU/内存限制 | 首次部署可不设置额外配额，应用按实际资源调节并发 |
| 特权模式 | 不勾选 |
| GPU | 如果表单支持 GPU 设备请求，启用 NVIDIA GPU 并选择第 0 张卡；如果没有该选项，使用下一节的宝塔容器编排 |

环境变量逐项添加，账号密码由自己填写：

| 名称 | 值 |
| --- | --- |
| `TZ` | `Asia/Shanghai` |
| `VIDEO_PROCESSOR_ROOT` | `/data/video-processor` |
| `VIDEO_PROCESSOR_AUTH_USER` | `admin`，可改 |
| `VIDEO_PROCESSOR_AUTH_PASSWORD` | **自己设置的非空长密码**，不要使用公开示例密码 |
| `VIDEO_PROCESSOR_ENCODER_DEVICE` | `auto`，优先 GPU，不可用时允许 CPU |
| `NVIDIA_DRIVER_CAPABILITIES` | `compute,video,utility` |
| `VIDEO_PROCESSOR_GPU_MAX_CONCURRENT` | `8`，自动并发的安全上限 |
| `VIDEO_PROCESSOR_GPU_CPU_THREADS` | `4` |
| `VIDEO_PROCESSOR_MIN_FREE_GB` | `20` |
| `VIDEO_PROCESSOR_FILE_RETENTION_DAYS` | `14`，成品保留天数 |
| `VIDEO_PROCESSOR_RECORD_RETENTION_DAYS` | `90`，记录保留天数 |

创建容器后，密码通过环境变量生效。**仅把密码写到宿主机 `.env` 文件不会自动传给普通“创建容器”表单。** 普通容器方式不需要填写镜像构建参数或上传源码。

`NVIDIA_DRIVER_CAPABILITIES` 是驱动能力声明，不能替代实际 GPU 透传。也不要把 `--gpus` 填进“启动命令”，该字段是应用启动命令。CPU 和 GPU 使用同一个交付镜像。

保留镜像的普通用户和默认单 worker 启动方式。不要同时创建两个应用容器挂载同一个数据目录。

## 5. 若宝塔表单没有 GPU 选项：在宝塔导入编排

这种方式仍是 **宝塔管理同一个 Docker 引擎和已导入镜像**，无需另外搭建运行环境。应用容器用本节替代上一节创建，不要两种方式各建一个。

1. 在宝塔“文件”中把 `.env.example` 复制为 `.env`，保留交付包已填写的镜像标签，设置非空 `VIDEO_PROCESSOR_AUTH_PASSWORD`；其他变量可沿用模板。密码建议用单引号包住，包含单引号时须按 Compose 环境文件规则转义。
2. 确认 `VIDEO_PROCESSOR_DATA_DIR` 指向第 2 节准备的数据目录，`VIDEO_PROCESSOR_GPU_DEVICE_ID=0`。
3. 在 **Docker → 容器编排/Compose → 添加项目** 中，从文件选择交付包里的 `docker-compose.yml`，项目目录设置为 `/www/server/docker/video-processor-web`，并让配置读取该目录的 `.env`。若页面单独要求环境变量，就把 `.env` 中的参数填入该页面。
4. 检查镜像为已经导入的完整标签、端口为 8899、挂载为上述数据目录，再创建并启动。配置使用本地镜像，不包含构建或在线拉取步骤。

宝塔 [11.0.0 更新记录](https://docs.bt.cn/update-log/)已列出“容器编排支持直接选择文件创建”；旧版可使用对应的 Compose 模板入口，并按其界面设置项目目录和环境变量。如果该版本无法传入环境变量，先在宝塔编辑器中将编排中的 `${变量…}` 替换为实际值，再创建项目；密码使用 YAML 单引号字符串，密码中的单引号须写成两个单引号。保存配置到受限目录，避免公开账号密码。

此编排已经配置 NVIDIA 第 0 张卡透传、登录密码检查、数据持久化、非 root 用户、只读根文件系统、可写 `/tmp`、日志轮转和自动重启，适合复用。普通容器表单不要单独勾选只读根文件系统，除非同时配置了可写 `/tmp`。

## 6. 启动后验收

在宝塔容器列表查看状态和日志；启动后等待健康状态为 `healthy`。另一台内网电脑打开：

```text
http://Ubuntu服务器IP:8899
```

服务器 IP 使用公司的 Ubuntu 地址，输入第 4 或第 5 节设置的账号密码。`http://服务器IP:8899/health` 的版本应为 1.3.8、提交号应与 `release.json` 一致。

进入宝塔中该应用容器的“终端”，执行：

```bash
nvidia-smi
python -c 'from web_app.encoding import get_gpu_capabilities; print(get_gpu_capabilities())'
ffmpeg -hide_banner -v error -f lavfi -i testsrc2=size=1280x720:rate=30 -t 1 -c:v h264_nvenc -preset p4 -f null -
ffmpeg -hide_banner -v error -f lavfi -i testsrc2=size=1280x720:rate=30 -t 1 -c:v hevc_nvenc -preset p4 -f null -
```

显卡检测应显示服务器的 RTX 3090，H.264/H.265 可用，两条试编码命令均正常退出。不要仅以 FFmpeg 的编码器列表确认 GPU 可用。自动模式可能回退 CPU，实际设备和原因可以在网页查看。

最后通过另一台电脑验证：选择视频 → 预估成品 → 查看最多 3 个随机抽中的样片 → 开始制作 → 下载。正式制作应包含全部视频；刷新、调整参数和开始制作会清理旧样片。

### 内网端口与可选 HTTPS

直接访问时允许所需内网客户端连接服务器 8899。Docker 端口发布可能绕过 UFW，需要核对实际网络访问规则。来源：[Docker 官方防火墙说明](https://docs.docker.com/engine/install/ubuntu/#firewall-limitations)。

若使用宝塔网站/反向代理和 HTTPS，映射宿主机 `127.0.0.1:8899`，反向代理目标设为 `http://127.0.0.1:8899`。当前网页是 8 MiB 分块上传，Nginx 代理配置可补充：

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

保留宝塔生成的 `proxy_pass`，避免同一层级重复定义指令。登录、`/api/` 和样片地址不配置缓存。64m 适用于网页分块上传；普通接口上传整文件需按实际视频大小调整。

### 资源统计与并发

Ubuntu 原生 Docker 中，本项目 CPU/内存采集通常反映宿主机全局资源；GPU 编码器和显存反映所选物理显卡，包括其他程序。宝塔显示的容器占用属于不同统计范围。部署后与宿主机 `htop`、`free -h`、`nvidia-smi` 对照，采样和内存缓存算法会导致部分差异。

自动并发从 1 路增长，新增任务前检查 CPU、内存、GPU 编码器、GPU 利用率和显存是否达到 80%，GPU 默认最多 8 路。80% 是停止增加任务的阈值，已运行任务仍可能超过此值。如果另设容器 CPU/内存配额，需要同时观察容器上限。

### 可选完整自检

服务器空闲时，在宿主机的宝塔终端执行；将容器名、账号和端口改为实际值。自检会制作短测试视频并清理自己的测试任务，开启通知时可能发出测试通知：

```bash
cd /www/server/docker/video-processor-web
export VIDEO_PROCESSOR_AUTH_USER=admin
read -r -s -p '验证用登录密码: ' VIDEO_PROCESSOR_AUTH_PASSWORD; echo
export VIDEO_PROCESSOR_AUTH_PASSWORD
python3 validate_docker.py --gpu --container video-processor-web --url http://127.0.0.1:8899
unset VIDEO_PROCESSOR_AUTH_PASSWORD
```

编排创建的容器名可在宝塔容器列表复制。该脚本覆盖真实编码、水印、音轨、下载、ZIP、分块续传、样片 Range、清理、同名文件和暂停恢复。

## 7. 后续更新、备份与回滚

在宝塔中等待当前任务完成后停止应用容器，备份**完整宿主机数据目录**和容器配置/环境变量；编排部署还备份 `.env` 与 `docker-compose.yml`。原视频和水印也在数据目录内，不要只备份数据库。备份存放在其他目录，并验证能读取。

更新时先导入新镜像，保留旧镜像；记录旧标签，然后用新镜像重新创建应用容器，复用原端口、账号、环境变量和数据挂载。编排部署则更新同一项目的镜像标签后重新部署。修改后按第 6 节验收。

回滚时用记录的旧镜像标签重新创建容器，继续挂载原数据目录；如需恢复数据快照，先停止服务再恢复到新目录并调整挂载，保留原目录供核对。

**不要删除持久化数据目录，不要同时让两个应用副本访问同一目录。** 单纯删除或重建应用容器不会影响正确挂载的宿主机数据目录，但宝塔操作时不要同时选择删除数据。

本次已验证离线镜像导入、CPU/GPU 容器和网页完整流程。公司 Ubuntu RTX 3090 的驱动、宝塔实际版本和访问环境，仍需在服务器按第 6 节验收；服务器尚未由本次工作远程部署。
