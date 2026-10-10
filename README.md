# 视频处理器 Web 版

## Docker 部署（推荐）

镜像内已包含 Python、FFmpeg/FFprobe 和 Web 依赖，支持 Docker Engine + Compose 插件，或启用 Linux 容器的 Docker Desktop。

在项目根目录执行：

```bash
cp .env.example .env
docker compose up -d --build
```

Windows PowerShell 中第一条命令改为 `Copy-Item .env.example .env`。
浏览器访问 `http://localhost:8899`，其他设备访问 `http://服务器IP:8899`。
无需在宿主机安装 Python 或 FFmpeg。

### NVIDIA GPU 编码

支持自动选择、CPU、NVIDIA GPU 三种模式。自动模式通过实际试编码检测 H.264/H.265 支持，
优先使用 NVENC；设备不可用时使用 CPU。执行中遇到 GPU 专属错误时，自动模式会使用独立的 CPU 参数重新制作；
明确选择 NVIDIA 模式则报告错误，避免悄悄切换设备。损坏视频、磁盘不足和水印错误不会触发 GPU 回退。

在已安装 NVIDIA 驱动和 NVIDIA Container Toolkit 的服务器上执行：

```bash
docker compose -f compose.yaml -f compose.nvidia.yaml up -d --build --wait
```

GPU 模式的启动、停止、更新命令均使用这两个 Compose 文件。
普通 `docker compose up -d` 使用 CPU 部署配置，自动模式会选择 CPU。
详细安装步骤及内网离线部署方法见 [Ubuntu + RTX 3090 部署说明](deploy/ubuntu-nvidia.md)。

页面分别保存 CPU 的 CRF/编码策略和 GPU 的 CQ/编码策略。数值越小画质越高，文件通常越大，
但 CRF 与 CQ 不能直接等同。GPU 默认 CQ 26、均衡策略 p4，并发根据资源自动调节；
自动模式同时显示 CPU 参数，供失败后重新制作时使用。处理进度和记录显示实际编码器及回退情况。
GPU 状态显示可用的编码器负载与显存指标。调度从 1 路开始，新任务启动 5 秒后再次采样，
GPU 编码器占用、显存占用、GPU 利用率、CPU 或系统内存达到 80% 时停止新增任务，
负载回落后继续增加；同时保留磁盘空间和 I/O 检查。监控缺失时保持单路处理。
这是新增任务的判断阈值，不会中止已运行任务或强行把瞬时负载固定在 80%。

`.env` 中可配置：

```dotenv
VIDEO_PROCESSOR_ENCODER_DEVICE=auto
VIDEO_PROCESSOR_GPU_DEVICE_ID=0
VIDEO_PROCESSOR_GPU_MAX_CONCURRENT=8
VIDEO_PROCESSOR_GPU_CPU_THREADS=4
```

页面无需手选并发数。`VIDEO_PROCESSOR_GPU_MAX_CONCURRENT` 是安全上限，默认 8，与 CPU 的上限一致，
实际并发由资源采样决定；旧页面保存的 1/2 路设置不会限制新的自动调度。
已有 `.env` 若设置了 `VIDEO_PROCESSOR_GPU_MAX_CONCURRENT=2`，更新时将其改成 `8` 后重新创建容器。
GeForce 的 NVENC 会话数量也受显卡驱动和其他应用影响，见 [NVIDIA 官方 NVENC 说明](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-application-note/index.html)。
单个选定 GPU 负责编码，CPU 负责解码、水印和音频；CPU 线程数只控制 GPU 任务的 CPU 工作部分。
本版本保留单个应用进程，旧任务没有设备选项时继续使用 CPU。

### 成品体积预估

选择视频和处理参数后，在处理参数末尾点击“预估成品”。预估完成后确认下方大小与样片，再点击
预计成品大小区域的“开始制作”；视频或参数修改后需要重新预估，才能开始制作。
超过 3 个视频时随机选取 3 个，不超过 3 个时全部试编码。预估只上传抽中的视频，
调整参数时沿用同一组样本，便于对比；正式制作时补传其余视频。随后使用当前的
CPU/GPU 编码器、质量、策略、水印及 AAC 128 kbps 音频参数试编码。长视频在前、中、后位置
各抽取 8 秒；不超过 24 秒的视频完整试编码。水印预览及处理参数下方的全宽列表显示视频名、
时长、原始大小、预计大小、参考范围、实际试编码器和处理后缩略图。点击缩略图播放实际试编码
样片，可切换三个取样位置、全屏查看画质，调整参数后重新预估。预览仅转换封装为 MP4，
保留原编码画质、分辨率和音轨；浏览器不支持 H.265 时可下载样片查看。
抽中的视频保留实际样片；其他视频按抽样码率与本地读取的时长推算，明确标注“未试编码”。
浏览器无法读取某个视频时长时，改用抽样压缩比例推算并标明原因。整批预计大小包含全部视频。
实际试编码的长视频至少预留上下 25% 的波动；未抽中视频至少预留上下 40%，并随样本差异扩大。
不同分辨率、画面复杂度或素材类型会增加误差，这并非保证区间。
CPU 的 CRF 与 GPU 的 CQ 都是质量控制，不能通过参数直接换算成固定码率或精确文件大小。

正式制作复用已上传的视频。修改参数后旧结果会标为待更新，需要重新试编码。
预估不生成处理记录、不发送完成通知。样片和缩略图仅为临时文件，在点击开始制作（补传前）、
修改参数/视频、清空、重新预估、取消、刷新、关闭或离开处理页面时清理。
正常页面每分钟续期；断网、浏览器崩溃或清理请求未送达时，服务端在最后续期 10 分钟后清理，
检查周期 30 秒；服务重启也清理遗留样片。清理请求绑定本次预估，旧页面不会误删新样片。
上传的原视频分块按上传会话期限保留以支持续传，和预估输出的临时样片分别管理。
样片接口沿用登录保护，旧参数的样片地址不会返回新参数的视频。可取消上传或预估，已上传内容仍可继续使用。
正式任务和试编码共享执行门限，避免试编码额外占用正在制作任务的 GPU 会话；预估时有其他
用户提交正式任务，该任务会等待试编码结束。服务器重启导致的预估中断会提示重新预估。

空闲时可运行 HTTP 全流程验证（只清理脚本自己创建的任务）：

```bash
python3 deploy/validate_docker.py --gpu
```

修改 `.env` 可调整端口、保留时间、上传限制、登录密码和企业微信通知。
例如设置 `VIDEO_PROCESSOR_PORT=9000` 后，通过 `http://服务器IP:9000` 访问。
如需登录保护，设置 `VIDEO_PROCESSOR_AUTH_PASSWORD`；密码和 Webhook 不要提交到 Git。
仅本机访问时可设置 `VIDEO_PROCESSOR_BIND_IP=127.0.0.1`。
修改配置后执行 `docker compose up -d`。

### 正式环境发布

Ubuntu + RTX 3090 的正式发布使用三个配置文件：`compose.yaml`、`compose.nvidia.yaml`、
`compose.production.yaml`。生产配置要求非空登录密码和镜像版本标签，并启用只读根文件系统；
数据库和视频仍写入原来的数据卷。
版本标识、发布前备份、离线镜像交付、验证和回滚命令见 [正式环境发布步骤](deploy/production-release.md)。
`/health` 返回应用版本和构建 Git 提交号，用于确认运行中的版本。

```bash
docker compose ps                 # 查看运行状态与健康检查
docker compose logs -f --tail=100  # 查看日志，Ctrl+C 退出
docker compose down               # 停止并删除容器，保留数据
docker compose up -d --build       # 更新源码后重建并启动
```

数据默认保存在 Compose 创建的 `video-data` 命名卷中（实际名称带项目名前缀），
容器内目录为 `/data/video-processor`，包含数据库、上传视频、成品、水印和临时文件。
重新构建镜像或执行 `docker compose down` 不会删除这些数据。
**`docker compose down -v` 会删除数据卷，不能作为普通更新命令。**
备份数据库和视频时先执行 `docker compose stop`，再备份整个数据卷；备份后执行 `docker compose start`。

如需直接保存到宿主机目录，把 `compose.yaml` 中服务的卷映射改为：

```yaml
    volumes:
      - ./data:/data/video-processor
```

Linux 上先执行 `mkdir -p data && sudo chown 1000:1000 data`。
容器以 UID/GID `1000:1000` 运行，挂载目录必须允许该用户写入。
Windows Docker Desktop 使用命名卷即可，无需执行 Linux 权限命令。

原部署的数据如需迁移，先停止旧服务并完整备份。数据库中保存了文件的绝对路径，
不能只把数据库复制到新的目录。保持原来的容器内数据路径，例如旧目录为
`/data/video-processor-data` 时，同时把服务中的 `VIDEO_PROCESSOR_ROOT` 改为该路径、
把卷映射改为 `/data/video-processor-data:/data/video-processor-data`，并确认目录权限。
旧任务如使用项目自带水印，数据库中还可能引用原源码目录的 `rt.png`、`dt.png`；
应保留这两个原路径（通过只读挂载对应文件），或在迁移前完成旧任务。

注意：

- 保持单个容器、`--workers 1`；当前任务队列在进程内，不能直接扩容多个副本。
- 默认预留 20 GB 磁盘空间；Docker Desktop 应检查 Docker 虚拟磁盘的剩余空间。
  测试环境空间较小时可调整 `.env` 中的 `VIDEO_PROCESSOR_MIN_FREE_GB`。
- 默认 Compose 无需 GPU；GPU 部署增加 `compose.nvidia.yaml`。正在制作时建议先暂停任务，再更新或重启容器。
- 镜像通过 `/health` 检查服务状态；`unhealthy` 可通过日志排查，重启策略仅在容器退出时触发。

如果构建提示无法连接 `auth.docker.io` 或 `registry-1.docker.io`，先检查网络及 Docker Desktop 的代理设置。
Windows 上即使已配置系统代理，Docker CLI 仍可能需要在当前 PowerShell 中指定代理后重试：

```powershell
# 替换为本机实际运行的 HTTP 代理地址；仅影响当前终端。
$env:HTTP_PROXY = 'http://127.0.0.1:10808'
$env:HTTPS_PROXY = $env:HTTP_PROXY
docker compose up -d --build
```

代理地址属于部署环境配置，无需写入 Dockerfile 或提交到 Git。

### 验证与回归测试

Docker 实测结果见 [deploy/docker-validation.md](deploy/docker-validation.md)。
H.265 编码已使用 x265 工作线程池参数设置线程预算，帧并行数交给编码器自动选择，
避免将 64 个工作线程误传为超出限制的帧线程数。
参数定义见 [x265 文档](https://x265.readthedocs.io/en/master/cli.html#performance-options)。

启动容器后，可在 PowerShell 中运行实际 FFmpeg 编码回归测试：

```powershell
Get-Content -Raw tests/test_ffmpeg_integration.py | docker compose exec -T video-processor python -
```

已在宿主机安装 Python 和 FFmpeg 时，先执行 `python -m pip install -r requirements-dev.txt`，
再执行 `python -m unittest discover -s tests`。也可使用隔离的镜像测试阶段：

```bash
docker build --target test -t video-processor-web:test .
docker run --rm video-processor-web:test
```

GitHub Actions 自动运行回归测试、依赖漏洞审计、生产配置检查、镜像构建和 CPU HTTP 全流程验证。
测试使用临时生成的视频，覆盖 H.265 自动线程数及 1、16、64 个工作线程预算。

内网 Web 版默认部署目录：

```bash
/data/video-processor-web
```

运行端口：

```bash
8899
```

运行数据目录通过 `VIDEO_PROCESSOR_ROOT` 配置，推荐与源码分离：

```text
/data/video-processor-data/uploads
/data/video-processor-data/outputs
/data/video-processor-data/tmp
/data/video-processor-data/watermarks
/data/video-processor-data/video_processor.db
```

## Ubuntu 部署

```bash
sudo mkdir -p /data/video-processor-web /data/video-processor-data
sudo chown -R $USER:$USER /data/video-processor-web /data/video-processor-data

cd /data/video-processor-web
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-web.txt

sudo apt update
sudo apt install -y ffmpeg

VIDEO_PROCESSOR_ROOT=/data/video-processor-data uvicorn web_app.main:app --host 0.0.0.0 --port 8899 --workers 1
```

浏览器访问：

```text
http://服务器IP:8899
```

如果宝塔启动命令里使用了：

```bash
--env-file /data/video-processor-data/video-processor.env
```

需要先创建这个文件，否则 uvicorn 会直接启动失败：

```bash
sudo mkdir -p /data/video-processor-data
sudo touch /data/video-processor-data/video-processor.env
```

也可以改用 `deploy/start-web.sh` 启动脚本；它会在 env 文件存在时自动加载，不存在时正常启动。

## 保存时间

默认文件保留 14 天，处理记录保留 90 天。

可通过环境变量调整：

```bash
export VIDEO_PROCESSOR_FILE_RETENTION_DAYS=14
export VIDEO_PROCESSOR_RECORD_RETENTION_DAYS=90
export VIDEO_PROCESSOR_MAX_UPLOAD_MB=8192
export VIDEO_PROCESSOR_MIN_FREE_GB=20
export VIDEO_PROCESSOR_ARCHIVE_RETENTION_HOURS=24
export VIDEO_PROCESSOR_UPLOAD_SESSION_RETENTION_HOURS=24
```

- 成品、上传源文件会按 `VIDEO_PROCESSOR_FILE_RETENTION_DAYS` 自动清理；处理记录按 `VIDEO_PROCESSOR_RECORD_RETENTION_DAYS` 清理。
- 浏览器下载生成的压缩包默认只保留 24 小时，未完成的断点续传会话默认保留 24 小时。
- 服务会始终预留 `VIDEO_PROCESSOR_MIN_FREE_GB` 的磁盘空间；上传、制作和打包前空间不足会直接提示，避免磁盘写满导致任务或数据库异常。

文件过期后会自动清理上传文件和输出文件，处理记录仍会保留到记录过期时间；记录页会显示“文件已清理”，并隐藏下载入口。

## 编码性能

服务端支持 CPU 和 NVIDIA GPU 编码。CPU 编码策略如下：

- 极速优先：最快，文件通常更大。
- 速度优先：批量处理推荐，速度更快，文件可能略大。
- 均衡：速度和体积折中。
- 体积优先：输出通常更小，但更慢。
- 高压缩：处理最慢，适合少量视频或特别在意体积的任务。

也可以通过服务器环境变量设置默认/兜底 preset：

```bash
export VIDEO_PROCESSOR_H264_PRESET=veryfast
export VIDEO_PROCESSOR_H265_PRESET=fast
export VIDEO_PROCESSOR_MKV_PRESET=veryfast
```

可选值：`ultrafast`、`superfast`、`veryfast`、`faster`、`fast`、`medium`、`slow`、`slower`、`veryslow`、`placebo`。越靠前速度越快，文件可能更大；越靠后压缩更充分，但处理更慢。

## 企业微信通知

配置群机器人 Webhook 后，任务全部完成、存在失败或被取消时会发送通知。Webhook 是密钥，只应配置在服务器环境变量中，不能写入代码或提交到 Git：

```bash
export VIDEO_PROCESSOR_WECHAT_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=replace-with-your-key'
export VIDEO_PROCESSOR_PUBLIC_URL='http://服务器IP:8899'
```

`VIDEO_PROCESSOR_PUBLIC_URL` 为可选项；设置后，完成通知会附带打开处理记录页面的链接。
如果提示 webhook 缺少完整 key，请检查面板里的环境变量是否保存成了完整的 `...send?key=xxxx`。
如果提示 `CERTIFICATE_VERIFY_FAILED`，更新项目依赖并确认 Ubuntu 已安装 `ca-certificates`。
内网使用自签 CA 时可通过 `SSL_CERT_FILE` 指定包含所需证书的 PEM 文件，并将文件只读挂载进容器。
程序始终校验 TLS 证书，校验失败会报告通知失败。

### 可选访问保护

内网环境也可以开启浏览器基础认证。仅当设置密码时才会生效：

```bash
export VIDEO_PROCESSOR_AUTH_USER=admin
export VIDEO_PROCESSOR_AUTH_PASSWORD='请设置一个独立强密码'
```

浏览器首次访问会显示页面内登录框；`/health` 健康检查不受影响。

## 功能说明

- 处理页面用于上传视频、设置水印、查看当前处理进度。
- 处理记录页面用于查看历史任务、展开查看文件、单个下载或打包下载成品。
- 处理记录支持删除；删除记录会同步删除服务器上的上传文件、成品文件、水印文件和临时压缩包。
- 开始制作后，参数区域会显示锁定提示，避免制作中误改视频列表和水印参数。
- 当前任务支持暂停、继续和取消；暂停会终止正在运行的 FFmpeg，继续时只处理未完成的视频。
- 取消任务会终止正在运行的 FFmpeg，并删除本次任务的上传文件、输出文件、水印文件和临时压缩包。
- 单个视频制作成功后，会立即删除对应的上传源视频；成品文件仍按保存时间保留。
- 固定水印预览窗口常驻显示，可直接拖动水印设置自定义位置。

## systemd 示例

创建 `/etc/systemd/system/video-processor.service`：

```ini
[Unit]
Description=Video Processor Web
After=network.target

[Service]
WorkingDirectory=/data/video-processor-web
EnvironmentFile=-/data/video-processor-data/video-processor.env
Environment=VIDEO_PROCESSOR_ROOT=/data/video-processor-data
Environment=VIDEO_PROCESSOR_FILE_RETENTION_DAYS=14
Environment=VIDEO_PROCESSOR_RECORD_RETENTION_DAYS=90
Environment=VIDEO_PROCESSOR_MIN_FREE_GB=20
Environment=VIDEO_PROCESSOR_ARCHIVE_RETENTION_HOURS=24
Environment=VIDEO_PROCESSOR_UPLOAD_SESSION_RETENTION_HOURS=24
ExecStart=/data/video-processor-web/deploy/start-web.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now video-processor
```
