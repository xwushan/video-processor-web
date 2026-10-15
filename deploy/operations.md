# 配置、日常运维、备份、更新与排障

已部署服务器保持现有项目名、密码、数据卷/目录和应用内绝对路径。操作前确认没有正在制作或上传的任务；备份和更新针对本应用，不重启整个 Docker 或整台服务器。

## 1. 参数在哪里设置

| 部署方式 | 配置位置 | 生效操作 |
| --- | --- | --- |
| 宝塔 Docker 编排 | 原项目的 `.env 内容` 与 compose 内容 | 更新/重新部署同一项目 |
| Docker 命令行 | 与原 Compose 文件配套的 .env | 原来的文件组合执行 up -d |
| 原生 systemd | /etc/video-processor/video-processor.env | systemctl restart video-processor |
| 宝塔 Python 项目 | 运行账号可读的可信环境文件，或面板配置 | 重启该 Python 项目 |

Docker 的 .env 变量只有在 Compose 中引用后才会传入容器，不能假定任意键都自动生效。仓库新版 panel 两份模板已显式传入下表通用变量；已部署旧模板需先更新 compose 内容，再填写新增参数。原生环境文件由启动脚本加载。

| 变量 | 默认/含义 |
| --- | --- |
| VIDEO_PROCESSOR_AUTH_USER | admin，登录名 |
| VIDEO_PROCESSOR_AUTH_PASSWORD | 生产必须填自己的非空密码 |
| VIDEO_PROCESSOR_PORT | 8899，Docker 为宿主机端口，原生为监听端口 |
| VIDEO_PROCESSOR_BIND_IP | 0.0.0.0；同机代理时可设 127.0.0.1 |
| VIDEO_PROCESSOR_ENCODER_DEVICE | auto/cpu/nvidia；原生及根目录 Compose 可配，panel 模板固定 auto，网页仍可选设备 |
| VIDEO_PROCESSOR_GPU_DEVICE_ID | Docker NVIDIA 请求的物理设备编号，默认 0；仅 GPU 配置使用 |
| VIDEO_PROCESSOR_GPU_MAX_CONCURRENT | 8，GPU 自动并发安全上限，非固定并发数 |
| VIDEO_PROCESSOR_GPU_CPU_THREADS | 4，单路 GPU 任务的 CPU 工作线程预算 |
| VIDEO_PROCESSOR_FILE_RETENTION_DAYS | 14，成品及源文件清理期限 |
| VIDEO_PROCESSOR_RECORD_RETENTION_DAYS | 90，历史记录保留天数 |
| VIDEO_PROCESSOR_MAX_UPLOAD_MB | 8192，服务端单文件上传限制 MiB |
| VIDEO_PROCESSOR_MIN_FREE_GB | 20，按 GiB 计算的数据盘最小空闲空间 |
| VIDEO_PROCESSOR_ARCHIVE_RETENTION_HOURS | 24，下载压缩包保留小时 |
| VIDEO_PROCESSOR_UPLOAD_SESSION_RETENTION_HOURS | 24，断点上传会话保留小时 |
| VIDEO_PROCESSOR_H264_PRESET/H265_PRESET/MKV_PRESET | veryfast/fast/veryfast，环境兜底策略；网页传入的策略优先 |
| VIDEO_PROCESSOR_WECHAT_WEBHOOK | 可选，企业微信群机器人地址，按密钥保管 |
| VIDEO_PROCESSOR_PUBLIC_URL | 可选，通知里链接使用的实际站点地址 |

原生启动脚本还支持 `VIDEO_PROCESSOR_APP_DIR`、`VIDEO_PROCESSOR_ENV_FILE`、`VIDEO_PROCESSOR_PYTHON`、`VIDEO_PROCESSOR_ROOT` 和 `VIDEO_PROCESSOR_REQUIRE_AUTH=1`。systemd 模板已要求密码；本地开发若直接运行 Uvicorn，应自行提供数据目录与认证配置。

CPU CRF 与 GPU CQ 默认均为 32，在网页里调整，不是上述环境变量。浏览器已保存的自定义值会恢复，不会强行覆盖为默认值。GPU 原生路径当前以单卡/GPU 0 为准，多 GPU 部署优先使用 Docker 设备透传隔离。

## 2. 日常查看与验收

- 宝塔：查看原编排、容器健康与日志；普通 Python 查看对应项目日志。
- Docker：使用原来的 `-p`、`-f` 和 `--env-file` 参数执行 `ps`、`logs --tail=100`。
- systemd：`systemctl status video-processor` 与 `journalctl -u video-processor -n 100 --no-pager`。
- HTTP：`curl --fail http://127.0.0.1:实际端口/health`。健康接口公开是供探测使用，业务接口需要登录。
- 网页：登录 → 短视频预估 → 样片播放 → 正式制作 → 下载，GPU 时确认实际编码器 h264_nvenc/hevc_nvenc。

默认成品 14 天、记录 90 天；单个视频成功后会及时删除相应上传源文件。预估只试编码最多 3 个抽样视频，正式制作处理整批；样片在参数变化、刷新、离开或制作时清理，失联/重启还会回收。备份策略不应依赖已被清理的源视频。

## 3. Docker 命名卷完整备份

先在宝塔或原 Compose 项目停止**本应用容器**，不删除卷。找到实际容器名、数据卷和镜像：

```bash
docker inspect 实际应用容器名 --format '{{range .Mounts}}{{if eq .Destination "/data/video-processor"}}{{.Name}}{{end}}{{end}}'
docker inspect 实际应用容器名 --format '{{.Config.Image}}'
```

panel 默认卷 `video-processor-web-data`；根目录 Compose 默认带项目名前缀。如果结果为空，检查是否使用宿主机目录挂载，改用下一节，不凭空创建新卷。

下例适用于 panel 默认卷；先确认真实卷名，再按需替换：

```bash
backup_stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir=/www/backup/video-processor
sudo install -d -m 0700 "$backup_dir"
docker volume inspect video-processor-web-data >/dev/null
docker run --rm --pull never --network none --user 0:0 --entrypoint tar \
  --mount type=volume,src=video-processor-web-data,dst=/backup-source,readonly \
  --mount "type=bind,src=$backup_dir,dst=/backup-target" \
  video-processor-web:1.3.8-787b7c9 \
  -czf "/backup-target/$backup_stamp-data.tgz" -C /backup-source .
sudo chmod 600 "$backup_dir/$backup_stamp-data.tgz"
sudo tar -tzf "$backup_dir/$backup_stamp-data.tgz" >/dev/null
```

从 `docker volume inspect` 开始逐条确认成功再继续；备份失败时先恢复原应用运行并排查，不以失败或空文件作为可恢复备份。备份容器复用现有镜像，仅运行 tar，不占 GPU、不启动网页。使用其他镜像标签时将示例标签替换成现有应用镜像。

另行保密备份 compose/.env、镜像标签和完整提交号。校验完成后通过原项目启动应用，并把备份转存到独立存储。数据包括数据库、成品、水印、续传和临时目录，不只一个 SQLite 文件。

## 4. 普通目录与 systemd 备份

普通部署示例：

```bash
backup_stamp="$(date +%Y%m%d-%H%M%S)"
sudo install -d -m 0700 /var/backups/video-processor
sudo systemctl stop video-processor
sudo tar -czf "/var/backups/video-processor/$backup_stamp-data-config.tgz" \
  -C / data/video-processor-data etc/video-processor
sudo chmod 600 "/var/backups/video-processor/$backup_stamp-data-config.tgz"
sudo tar -tzf "/var/backups/video-processor/$backup_stamp-data-config.tgz" >/dev/null
sudo systemctl start video-processor
```

自定义路径、宝塔 Python 守护或 Docker 绑定目录时同步替换实际停止方式与路径。逐条检查返回结果；任何备份步骤失败，先启动原服务再处理。备份存放位置须有足够空间，不能放进正在打包的数据目录。

## 5. 更新、回滚与恢复

更新前记录旧镜像标签/ID或 `git rev-parse HEAD`，等待任务结束，准备并验证新镜像/依赖，再停服务完整备份。

- 宝塔 Docker：导入新镜像，编辑原项目 .env 的标签，保留卷和端口后重新部署。
- Docker 源码：固定目标分支/标签，构建新提交镜像后使用相同 Compose 文件组切换；[正式发布手册](production-release.md) 有命令。
- 原生：管理员更新代码及该项目虚拟环境依赖，重启本应用。更改 unit 时先 `systemd-analyze verify` 和 `daemon-reload`。

验收原记录、已有成品下载、版本/提交号以及新短视频。代码回滚使用保留的旧镜像或旧提交；依赖随目标版本恢复。回退代码不能自动恢复已经删除或迁移的数据。

### 普通部署的源码与依赖更新

在源码目录先查看改动、取得目标提交，不强制覆盖本地修改：

```bash
cd /data/video-processor-web
sudo git status --short
sudo git rev-parse HEAD
sudo git fetch origin
sudo git log --oneline -5 origin/codex/docker-gpu-release
```

记录旧提交，确认目标完整提交号并完成上一节备份。停止本应用后切换目标代码，为新版本建立独立虚拟环境：

```bash
sudo systemctl stop video-processor
sudo git switch --detach 目标完整提交号
sudo python3.12 -m venv /data/video-processor-web/.venv-目标版本
sudo /data/video-processor-web/.venv-目标版本/bin/python -m pip install -r requirements-web.txt
sudoedit /etc/video-processor/video-processor.env
```

将环境文件 `VIDEO_PROCESSOR_PYTHON` 设为新环境解释器的绝对路径，保留旧环境用于回滚。此例的“目标完整提交号”和“目标版本”为需要替换的占位内容。离线环境使用预备的 wheelhouse；任一步失败，切回旧提交和旧解释器，再启动原服务。

启动并验收：

```bash
sudo systemctl start video-processor
sudo systemctl status video-processor --no-pager
curl --fail http://127.0.0.1:8899/health
```

如本次需要更新 systemd 模板，先检查差异与自定义路径，再安装模板、`systemd-analyze verify`、`daemon-reload`，然后启动；不要用新模板覆盖已经自定义的挂载或运行账号。宝塔 Python 方式用原面板项目的停止/启动操作，并将解释器配置同步到同一环境。

### 恢复备份

恢复数据快照时，停止应用，恢复到**新的目录/卷**，核对 SQLite 完整性、文件和运行用户权限，再调整挂载。容器内路径/原生路径仍要与记录中的绝对路径一致。保留当前数据以便核对；旧快照缺少备份之后产生的数据。

可在恢复副本中检查数据库，不对正在写入的数据库直接覆盖：

```bash
python3 -c "import sqlite3; c=sqlite3.connect('/恢复副本实际路径/video_processor.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0]); c.close()"
```

Docker 更新不用 `down -v`，也不删除仍被现有或回滚版本使用的数据卷和镜像。

### 企业微信完成通知

在对应环境文件填写完整 `VIDEO_PROCESSOR_WECHAT_WEBHOOK`，例如群机器人提供的 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...`；可选 `VIDEO_PROCESSOR_PUBLIC_URL` 填实际站点地址，用于处理记录链接。更新本应用配置后生效，预估不通知，正式任务完成/失败或取消会通知。

Webhook 按密码管理。证书错误时检查系统 CA；组织自签 CA 可用 `SSL_CERT_FILE` 指向受信任 PEM，Docker 还需显式传入该变量并只读挂载证书。默认编排不会自动传入任意自定义环境变量，不关闭 TLS 校验。

## 6. Docker 与普通部署迁移

完成或取消未完成任务后停旧服务，完整备份；旧服务与新服务不能并发读写同一数据。

数据库保存输入、输出和部分水印的绝对路径。例如 Docker 内为 `/data/video-processor`，迁往原生时应将完整数据恢复到宿主机同名路径，并设置 `VIDEO_PROCESSOR_ROOT=/data/video-processor`；原生 systemd 的 `ReadWritePaths` 同步修改。恢复文件属主应匹配新运行账号，不能直接假定是容器 UID 1000。

原生迁往 Docker，也要保持原来的应用内绝对路径，通过绑定挂载和 `VIDEO_PROCESSOR_ROOT` 对齐。若旧任务引用 `/app/rt.png` 等代码路径，应先完成旧任务，再迁移；历史任务重试还需保留对应资源路径。迁移不是只复制数据库。

## 7. GPU 与资源异常

```bash
nvidia-smi
cat /proc/driver/nvidia/version
modinfo -F version nvidia
sudo dmesg -T | grep -Ei 'NVRM|API mismatch' | tail -n 20
```

以上为读取信息。现场曾出现：已加载模块 580.173.02，磁盘模块及 NVML 为 580.178.04；处理后加载版本一致，宿主机与容器 NVENC 测试通过。

升级后旧模块仍在内存时，维护窗口重启宿主机通常能加载新模块；若磁盘模块、库也不一致，先检查安装/构建状态。重启整机中断服务器业务，重启 Docker 可能影响容器，卸载/重载驱动可能影响 GPU 任务，不把这些作为无影响的排查命令。

Docker 申请 GPU 失败发生在应用启动之前，auto CPU 回退无法覆盖；先用基础编排运行，再修复 GPU 环境。原生宿主机驱动正常但服务不可用时，检查实际运行账号、设备权限和 FFmpeg 编译支持。

Ubuntu 原生环境的 CPU/内存通常按宿主机全局统计，GPU 指标也包括其他程序。Docker Desktop 则有虚拟机边界；宝塔的容器占用与全局资源不是同一范围。80% 是新增任务阈值，不是硬限制；磁盘、CPU 水印和驱动会话上限都会限制吞吐量。

## 8. 常见故障与诊断

| 现象 | 处理方向 |
| --- | --- |
| 没有创建出容器 | 编排解析、空密码、镜像标签、端口或 GPU 分配；看部署日志 |
| 重启循环/普通服务退出 | 容器或 Python/systemd 日志，数据权限、依赖、环境值 |
| unhealthy 但网页可用 | 检查探测输出；新版编排已绕过健康检查的代理干扰 |
| 端口已占用 | 查已有应用实例，复用原项目或选择空闲端口 |
| 502/413/504 | 按 [反向代理文档](reverse-proxy.md) 检查后端、请求限制和超时 |
| 上传 HTTP 507 | 数据盘至少保留 20 GiB，另留素材/成品空间 |
| Permission denied | 对照实际运行账号和挂载目录，不以 777 或 root 掩盖原因 |
| 样片 404 | 参数变化/刷新/制作后的清理属于预期，重新预估；代理不要缓存样片 |
| H.265 不能播放 | 客户端浏览器解码能力；可下载查看或选择 H.264 |
| 原记录消失 | 先核对项目名、卷/目录和绝对路径，防止误挂空数据 |
| 通知证书错误 | 校验证书链或设置可信 CA 的 SSL_CERT_FILE，保持 TLS 校验 |

Docker 现场可上传并运行 `bash deploy/diagnose_baota.sh 原项目名`，收集只读报告；该脚本不创建/停止/删除容器，不输出完整环境或密码。没有容器时，同时保留宝塔编排的部署日志。普通部署收集 `journalctl` 或面板 Python 日志、Python/FFmpeg 版本、目录权限和 `nvidia-smi`。
