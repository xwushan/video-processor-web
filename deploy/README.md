# 部署与运维总览

应用为 Python FastAPI + Uvicorn 服务，网页和 API 由同一进程提供，FFmpeg 负责视频处理。普通网站部署需要运行 Python 后端；仅把 HTML 放入网站目录无法处理视频。

## 选择部署方式

| 场景 | 文档 | 环境与运行方式 |
| --- | --- | --- |
| Ubuntu + 宝塔 Docker，导入现成镜像 | [宝塔 Docker 部署](baota-ubuntu.md) | 镜像导入、两个编排输入框、数据卷、GPU |
| Linux Docker 命令行，在线构建或离线导入 | [Docker 完整部署](docker.md) | CPU/GPU、生产配置、固定版本、离线导出 |
| Ubuntu 普通部署，不使用 Docker | [Python + systemd 部署](native-ubuntu.md) | Python 3.12、venv、FFmpeg、独立用户、自启 |
| 宝塔 Python 项目或进程管理 | [宝塔普通网站部署](baota-native.md) | 原生 Python 后端、启动脚本、反向代理 |
| 内网域名、HTTPS、上传代理 | [Nginx 与 HTTPS](reverse-proxy.md) | 根路径代理、分块上传、证书和端口 |
| 参数、备份、升级、回滚、迁移与故障 | [运维手册](operations.md) | 日常操作和数据保护 |

公司服务器已使用 Ubuntu + 宝塔 Docker + RTX 3090 部署，用户已确认完成。宿主机驱动与项目镜像内 H.264/H.265 NVENC 实际试编码均已通过；现场经过见 [部署复查记录](baota-recheck-20261015.md)。已部署的服务器保持现有项目、镜像标签、密码和挂载配置，后续按运维手册更新。

## 所有方式共同遵守

- 单个应用进程、一个 Uvicorn worker；目前使用内存任务队列，不能直接横向扩容或多 worker 共用 SQLite。
- CPU CRF、GPU CQ 默认均为 32。GPU 仅编码；解码、水印和音频仍使用 CPU。
- GPU 并发自动增长，资源达到 80% 后停止增加任务，默认最多 8 路；已运行任务仍可能超过阈值。
- 数据盘至少保留 20 GiB 空闲，另预留原视频和成品空间。运行账号需要数据目录写权限。
- 生产环境设置非空登录密码，密码和 Webhook 只放受限环境文件，使用 HTTPS 承载跨网访问。
- NVIDIA GPU 需要宿主机驱动；Docker GPU 还需要 Container Toolkit。驱动库和已加载内核模块版本必须一致。
- 数据路径进入数据库记录，升级保留同一挂载和应用内路径。备份完整数据，不能只备份数据库。

## 模板文件

| 文件 | 用途 |
| --- | --- |
| [compose.baota-panel.cpu.yaml](compose.baota-panel.cpu.yaml) | 已导入镜像的基础启动，自动创建命名卷 |
| [compose.baota-panel.gpu.yaml](compose.baota-panel.gpu.yaml) | 同一项目启用 NVIDIA GPU，保留相同卷 |
| [baota-panel.env.example](baota-panel.env.example) | 宝塔 `.env 内容` 模板 |
| [compose.baota.yaml](compose.baota.yaml) | 高级用法：GPU + 宿主机目录挂载 |
| [native.env.example](native.env.example) | 普通部署配置，密码必须填写 |
| [start-web.sh](start-web.sh) | 普通部署启动脚本，支持路径、解释器、监听 IP、端口 |
| [video-processor.service](video-processor.service) | systemd 独立账号、自动启动、日志和停止管理 |
| [nginx-video-processor.conf](nginx-video-processor.conf) | HTTP 反向代理示例 |
| [diagnose_baota.sh](diagnose_baota.sh) | 宿主机和目标容器只读诊断 |
| [validate_docker.py](validate_docker.py) | 空闲时执行的真实编码与 HTTP 验收 |

当前完整交付代码位于 Git 分支 `codex/docker-gpu-release`，示例克隆命令显式使用该分支。PR 合并后可使用 main 或固定发布标签。已运行版本不因文档更新自动变化；当前已导入镜像标签为 `video-processor-web:1.3.8-787b7c9`，编排中的健康检查覆盖兼容修复。
