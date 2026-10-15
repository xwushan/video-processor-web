# Ubuntu 普通部署：Python + FFmpeg + systemd

本方式直接在 Ubuntu 上运行后端，不使用 Docker。推荐 Ubuntu 24.04、Python 3.12，GPU 可选。程序不是静态 HTML 网站，需要 Uvicorn 持续运行 `web_app.main:app`。

以下是**新部署**示例，源码 `/data/video-processor-web`、数据 `/data/video-processor-data`、配置 `/etc/video-processor/video-processor.env`。已有部署沿用原数据路径和账号，按 [运维手册](operations.md) 升级。

## 1. 安装系统依赖

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv ffmpeg git ca-certificates
python3.12 --version
ffmpeg -version
ffprobe -version
```

Ubuntu 其他版本未提供 Python 3.12 时，通过受信任的系统或面板 Python 环境准备 3.12；不要替换 Ubuntu 的系统 `/usr/bin/python3`。安装 Web 依赖使用独立虚拟环境，不使用全局 pip。

GPU 方式先确认宿主机 `nvidia-smi` 正常。普通部署直接使用宿主机驱动及 FFmpeg，不需要为了本方式安装 NVIDIA Container Toolkit；Toolkit 用于 Docker GPU。

## 2. 创建账号、源码和虚拟环境

以下命令以有 sudo 权限的管理员执行。专用运行账号不用于 SSH 登录：

```bash
id video-processor >/dev/null 2>&1 || sudo useradd --system --user-group --home-dir /data/video-processor-data --shell /usr/sbin/nologin video-processor
sudo install -d -o root -g video-processor -m 0750 /data/video-processor-web /etc/video-processor
sudo install -d -o video-processor -g video-processor -m 0750 /data/video-processor-data
sudo git clone --branch codex/docker-gpu-release --single-branch https://github.com/xwushan/video-processor-web.git /data/video-processor-web
sudo python3.12 -m venv /data/video-processor-web/.venv
sudo /data/video-processor-web/.venv/bin/python -m pip install --upgrade pip
sudo /data/video-processor-web/.venv/bin/python -m pip install -r /data/video-processor-web/requirements-web.txt
```

源码目标目录应为空；已有仓库不要重复 clone。源码和依赖由管理员更新，服务账号只需读取代码、写入数据目录。不要把已有视频目录递归改成另一个应用的账号。

## 3. 配置账号、端口和 GPU

首次创建配置，已有文件保留：

```bash
sudo test -f /etc/video-processor/video-processor.env || sudo install -o root -g video-processor -m 0640 /data/video-processor-web/deploy/native.env.example /etc/video-processor/video-processor.env
sudoedit /etc/video-processor/video-processor.env
```

关键内容：

```dotenv
VIDEO_PROCESSOR_APP_DIR=/data/video-processor-web
VIDEO_PROCESSOR_ROOT=/data/video-processor-data
VIDEO_PROCESSOR_BIND_IP=0.0.0.0
VIDEO_PROCESSOR_PORT=8899
VIDEO_PROCESSOR_REQUIRE_AUTH=1
VIDEO_PROCESSOR_AUTH_USER=admin
VIDEO_PROCESSOR_AUTH_PASSWORD='填写自己的登录密码'
VIDEO_PROCESSOR_ENCODER_DEVICE=auto
```

`auto` 优先试编码成功的 GPU，否则 CPU；`cpu` 固定 CPU；`nvidia` 要求 GPU。CPU CRF 和 GPU CQ 默认均为 32。该环境文件由受信任的管理员管理，使用简单赋值，不放 shell 命令、`export` 或引用其他变量；单引号密码保留 `$`、`#` 等字符。这里选用本身不含单引号的密码，使 Bash 和 systemd 两种解析保持一致。

前台试运行：

```bash
sudo -u video-processor env VIDEO_PROCESSOR_ENV_FILE=/etc/video-processor/video-processor.env bash /data/video-processor-web/deploy/start-web.sh
```

浏览器访问 `http://服务器IP:8899`，另一终端验证：

```bash
curl --fail http://127.0.0.1:8899/health
```

验证完用 Ctrl+C 停止前台服务，再启用下一节 systemd，避免两个进程争用端口和数据库。启动脚本自动定位源码，支持 `VIDEO_PROCESSOR_APP_DIR`、`VIDEO_PROCESSOR_PYTHON`、监听地址和端口；始终只启用一个 worker。

## 4. systemd 开机启动

```bash
sudo install -m 0644 /data/video-processor-web/deploy/video-processor.service /etc/systemd/system/video-processor.service
sudo systemd-analyze verify /etc/systemd/system/video-processor.service
sudo systemctl daemon-reload
sudo systemctl enable --now video-processor
sudo systemctl status video-processor --no-pager
sudo journalctl -u video-processor -n 100 --no-pager
```

模板以专用账号运行，配置文件必须存在且密码非空，只允许数据目录写入。源码、数据或配置路径调整时，同步修改 unit 的 `WorkingDirectory`、`EnvironmentFile`、`VIDEO_PROCESSOR_ENV_FILE`、`ExecStart` 和 `ReadWritePaths`，以及环境文件的源码/数据路径。

`daemon-reload` 只是重新读取 systemd 配置；`systemctl restart video-processor` 只重启本应用。更新驱动需要整机重启时，另行安排服务器维护窗口。

常用命令：

```bash
sudo systemctl restart video-processor
sudo systemctl stop video-processor
sudo systemctl start video-processor
sudo journalctl -u video-processor -f
```

## 5. 验证宿主机 GPU 与运行账号

先运行 `nvidia-smi`。再以实际服务账号、虚拟环境解释器验证真实试编码：

```bash
cd /data/video-processor-web
sudo -u video-processor .venv/bin/python -c 'from web_app.encoding import get_gpu_capabilities; print(get_gpu_capabilities())'
```

H.264/H.265 的 available 应为 True。FFmpeg 仅列出 NVENC 名称不代表能实际编码；默认使用单张显卡/GPU 0。

宿主机可用但运行账号不可用时，检查 `/dev/nvidia*` 的权限和用户组，仅在需要时为服务账号配置实际设备对应组；不要把整台服务器的设备改为任意可写。FFmpeg 缺少 NVENC 时更换为含 NVENC 的可信构建，或先选择 CPU。保持单 worker。

## 6. 域名和 HTTPS

IP 端口访问正常后，按 [Nginx 与 HTTPS](reverse-proxy.md) 设置代理。应用环境文件改为 `VIDEO_PROCESSOR_BIND_IP=127.0.0.1` 后重启本应用，代理目标使用 `http://127.0.0.1:8899`。

## 7. 内网离线安装

在与目标架构和 Python 版本匹配的 Linux 联网环境准备：

```bash
python3.12 -m pip download --only-binary=:all: -r requirements-web.txt -d wheelhouse
```

将源码与 wheelhouse 复制到服务器，先准备 Python 3.12/venv、FFmpeg、证书等系统依赖，再安装：

```bash
sudo /data/video-processor-web/.venv/bin/python -m pip install --no-index --find-links /离线包实际路径/wheelhouse -r /data/video-processor-web/requirements-web.txt
```

离线系统依赖由匹配 Ubuntu 版本的企业软件源或离线 deb 提供。不要跨系统复制 `.venv`；重建虚拟环境并安装对应平台的 wheel。

## 8. 上线验收与更新

检查登录、上传、最多 3 个预估样片、样片播放、正式整批制作和下载；GPU 页面实际编码器为 h264_nvenc/hevc_nvenc。确认空闲空间、自动清理时间、开机自启和日志位置。

更新前等待任务结束，备份完整数据和环境文件；源码、依赖更新后只重启本服务，检查原记录和已有成品。备份、回滚、容器与普通部署迁移详见 [运维手册](operations.md)。

参考：[Uvicorn 配置](https://www.uvicorn.org/settings/)、[systemd 环境与权限说明](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml)。
