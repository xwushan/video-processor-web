# 视频处理器 Web 版

面向内网批量视频处理，使用 FastAPI + FFmpeg 提供视频上传、固定/动态水印、CPU 与 NVIDIA GPU 编码、成品预估和下载。当前应用版本 1.3.8。

## 部署入口

完整说明见 **[部署与运维总览](deploy/README.md)**：

| 部署方式 | 操作指南 |
| --- | --- |
| Ubuntu + 宝塔 Docker，导入镜像后编排 | [宝塔 Docker 部署](deploy/baota-ubuntu.md) |
| Docker 命令行，在线构建或离线部署 | [Docker 部署](deploy/docker.md) |
| Ubuntu 普通部署，不使用 Docker | [Python + FFmpeg + systemd](deploy/native-ubuntu.md) |
| 宝塔普通 Python 网站 | [宝塔 Python 后端与网站代理](deploy/baota-native.md) |
| 域名与 HTTPS | [Nginx 反向代理](deploy/reverse-proxy.md) |
| 参数、备份、升级、回滚、迁移和排错 | [运维手册](deploy/operations.md) |

公司 Ubuntu + 宝塔 Docker + RTX 3090 已由用户确认部署完成；宿主机驱动和容器内 H.264/H.265 NVENC 实际试编码通过，见 [现场复查记录](deploy/baota-recheck-20261015.md)。已有部署保留原项目、镜像标签、登录密码和数据卷。

当前交付代码在 `codex/docker-gpu-release` 分支。新部署从该分支或已审核的固定提交取代码；PR 合并后可使用 main。

## Docker 首次部署简例

镜像包含 Python、FFmpeg/FFprobe 和运行依赖；GPU 另外需要宿主机 NVIDIA 驱动与 Container Toolkit。已有部署使用原配置更新，不重复执行首次部署流程。

```bash
git clone --branch codex/docker-gpu-release --single-branch https://github.com/xwushan/video-processor-web.git
cd video-processor-web
test -f .env || cp .env.example .env
```

编辑 `.env`，填写自己的 `VIDEO_PROCESSOR_AUTH_PASSWORD`，再在当前终端执行：

```bash
chmod 600 .env
export VIDEO_PROCESSOR_REVISION="$(git rev-parse HEAD)"
export VIDEO_PROCESSOR_IMAGE_TAG="$VIDEO_PROCESSOR_REVISION"
docker compose -f compose.yaml -f compose.production.yaml up -d --build --wait
```

这是 CPU 生产配置，访问 `http://服务器IP:8899`。把这两个版本值记入 `.env`，以后仍使用同一镜像版本。GPU 部署使用 `compose.yaml`、`compose.nvidia.yaml`、`compose.production.yaml` 三个文件，后续管理命令始终保持同一组合；详见 [Docker 文档](deploy/docker.md)。Windows PowerShell 的环境变量写法不同，示例命令按 Linux Bash 编写。

已经导入 `video-processor-web:1.3.8-787b7c9` 镜像的宝塔服务器，直接使用 [panel 编排与环境模板](deploy/README.md#模板文件)，无需重新构建源码。

## 使用流程

1. 选择视频或文件夹，确认待处理列表。
2. 设置固定/动态水印，预览中可拖动固定水印；选择编码设备、格式、质量和策略。
3. 点击“预估成品”，查看下方预计大小与实际试编码样片，按画质调整参数。
4. 预估确认后点击“开始制作”，完成整批处理。处理页可暂停、继续或取消，记录页可单个或打包下载。

超过 3 个视频时随机选 3 个试编码，列表只显示抽中的视频；调整参数沿用同组样本，正式制作补传其余视频。长视频在前、中、后各试编码 8 秒，不超过 24 秒的视频完整试编码。整批体积根据样本码率和时长推算，无法读取时长时使用抽样压缩比例；不同素材差异会扩大误差，参考范围不是大小保证。

预估样片不进入处理记录，也不发送完成通知。开始制作、修改参数/视频、刷新、离开页面等操作会清理样片；清理请求未送达时，最后续期超过 10 分钟后由服务端回收，服务重启也清理遗留样片。续传所需原视频分块单独按上传会话期限保留。

## 编码与资源

- CPU CRF、GPU CQ **默认均为 32**，值越小通常画质越高、体积越大；两者数值不能直接等同。浏览器会保留用户之前修改的参数。
- CPU 策略在速度和压缩效率之间取舍；GPU 默认均衡 p4，不同素材上策略差异可能较小，实际样片和总耗时更有参考价值。
- 设备支持 auto、cpu、nvidia。auto 优先使用实际试编码成功的 GPU；执行中遇到 GPU 专属错误时使用独立 CPU 参数重新制作。明确选择 nvidia 时报告 GPU 错误。
- GPU 负责 NVENC 编码，解码、水印和音频仍使用 CPU。H.265 的浏览器播放支持取决于客户端，可下载样片或选择 H.264。
- GPU 并发从 1 路自动增加，默认安全上限 8；编码器、显存、GPU、CPU 或内存达到 80% 时停止新增，负载回落再增加。80% 是调度阈值，已运行任务可能超过它；监控缺失时保持单路。
- 状态应按采集范围理解：Docker Desktop 有 Linux 虚拟机边界；Ubuntu Docker 的全局指标与宝塔显示的单容器占用也不同。磁盘空间按数据盘计算，GPU 指标可能包含其他 GPU 应用。

环境变量、实际资源范围与故障处理见 [运维手册](deploy/operations.md)。

## 数据与运行约定

必须保持单个应用进程、一个 Uvicorn worker；当前任务队列在进程内，不能直接多副本共用 SQLite。生产配置要求非空登录密码，`/health` 保持公开供健康检查使用；密码、企业微信 Webhook 和实际环境文件不进入 Git。

Docker 默认数据目录 `/data/video-processor`；普通部署示例 `/data/video-processor-data`。完整数据包含 SQLite、成品、水印、上传会话等，升级沿用原目录与应用内绝对路径。不同配置族的默认卷名可能不同，迁移前先核对。**不要使用 `docker compose down -v` 更新服务。**

默认成品/源文件保留 14 天，历史记录 90 天，下载压缩包与未完成上传会话 24 小时；单个视频制作成功后会及时删除对应源文件。取消任务或删除记录会清理该任务关联文件。数据盘默认至少保留 20 GiB 空闲空间，另需预留整批原视频及成品的空间。

备份完整数据与配置，不能只复制数据库；升级、回滚、普通部署与 Docker 迁移步骤见 [运维手册](deploy/operations.md)。

## 验证与开发

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
node --check web_app/static/app.js
node --test tests/network.test.cjs
```

本地需要 Python 3.12、FFmpeg/FFprobe；也可使用隔离镜像测试阶段：

```bash
docker build --target test -t video-processor-web:test .
docker run --rm video-processor-web:test
```

GitHub Actions 执行 Python/Node 回归、依赖漏洞审计、生产编排检查、普通部署配置检查、镜像构建及 CPU HTTP 全流程。空闲服务器可使用 [validate_docker.py](deploy/validate_docker.py) 验收真实编码与上传下载；参数与密码输入见 [正式发布手册](deploy/production-release.md)。历史实测见 [Docker 验证](deploy/docker-validation.md) 和 [上线复查](deploy/baota-recheck-20261015.md)。
