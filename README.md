# 视频处理器 Web 版

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

## 企业微信通知

配置群机器人 Webhook 后，任务全部完成、存在失败或被取消时会发送通知。Webhook 是密钥，只应配置在服务器环境变量中，不能写入代码或提交到 Git：

```bash
export VIDEO_PROCESSOR_WECHAT_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=replace-with-your-key'
export VIDEO_PROCESSOR_PUBLIC_URL='http://服务器IP:8899'
```

`VIDEO_PROCESSOR_PUBLIC_URL` 为可选项；设置后，完成通知会附带打开处理记录页面的链接。
部署后可以在“处理记录”页面点击“测试通知”验证机器人是否可用。如果提示 webhook 缺少完整 key，请检查面板里的环境变量是否保存成了完整的 `...send?key=xxxx`。
如果提示 `CERTIFICATE_VERIFY_FAILED`，程序会自动使用企业微信专用 TLS 兼容模式重试；仍建议在项目环境中重新执行 `pip install -r requirements-web.txt`，并确认 Ubuntu 已安装 `ca-certificates`。

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
