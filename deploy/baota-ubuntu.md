# Ubuntu 宝塔：从导入镜像到 CPU / GPU 部署

本文按宝塔「添加容器编排」窗口的 **compose 内容** 与 **.env 内容** 两个输入框编写。部署只使用已导入的 `video-processor-web:1.3.8-787b7c9` 镜像，不拉取网络镜像、不构建源码。

2026-10-15 已重新校验原镜像并完成 CPU/GPU 隔离运行检查。原镜像包含一个条件性健康检查问题：继承 HTTP 代理且未排除本地地址时可能误判异常。本文两份新编排均覆盖修正后的健康检查，因此**已有镜像可以继续使用**。

如果此前已经成功制作过视频，先保留现有编排和数据挂载。本流程的新部署使用 Docker 命名卷；已有宿主机目录挂载的数据，应继续沿用原挂载，不要改挂到空卷。旧目录挂载配置仍可参考 [compose.baota.yaml](compose.baota.yaml)，其健康检查也已更新。

## 1. 确认镜像与现有项目

在宝塔「Docker → 本地镜像」确认镜像标签为 `video-processor-web:1.3.8-787b7c9`。镜像 ID 应以 `abea9d0849a2` 开头，平台是 Linux/amd64；镜像导入后的大小与压缩包大小不同属于正常现象。

尚未导入时，上传原交付的 `image.tar`，使用「导入镜像」。同一服务器运行一个应用项目和一个容器。

关闭宝塔弹窗不会停止已创建的容器。先在「容器编排」查看是否已有 `video-processor-web` 项目：有就编辑该项目，没有再添加。保留数据卷 `video-processor-web-data`；不要删除已有卷或使用会删除卷的清理操作。

## 2. 第一阶段：先启动基础服务

进入「Docker → 容器编排 → 添加容器编排」：

| 界面字段 | 内容 |
| --- | --- |
| 编排名称 | `video-processor-web` |
| 来源 | 编辑 |
| compose 内容 | [compose.baota-panel.cpu.yaml](compose.baota-panel.cpu.yaml) 的全部文本 |
| .env 内容 | 下方内容，填入自己的非空密码 |

```dotenv
VIDEO_PROCESSOR_IMAGE_TAG=1.3.8-787b7c9
VIDEO_PROCESSOR_PORT=8899
VIDEO_PROCESSOR_AUTH_USER=admin
VIDEO_PROCESSOR_AUTH_PASSWORD='填写自己的登录密码'
```

也可以从 [baota-panel.env.example](baota-panel.env.example) 复制后填写密码。**这次 .env 内容必须填写密码，空密码会在配置解析时拒绝部署。** 这里使用单引号包住密码，保留 `$` 与 `#` 等字符；密码本身含单引号时，按 Compose 环境文件规则转义，或先使用不含单引号的密码。

点击「确定」，查看部署日志。服务启动后可能先显示 `starting`，等待健康检查，通常在两分钟内转为 `healthy`。这阶段不申请 GPU，用于确认镜像、Docker、数据卷、健康检查和网页基本链路。

浏览器打开：

```text
http://Ubuntu服务器实际IP:8899
```

用户名为 `admin`，密码为 .env 中填写的值。浏览器再打开 `/health`，应返回 `ok: true`、版本 `1.3.8` 和提交号 `787b7c94c2ea47f398890f54a69ce72d1faa167d`。

用一个短视频完成「预估成品 → 开始制作 → 下载」，确认基础服务正常。若 8899 已被占用，只将 .env 中的 `VIDEO_PROCESSOR_PORT` 改为其他未占用端口，例如 `8898`，访问地址同步修改，容器内端口仍是 8899。

编排自动创建 `video-processor-web-data` 数据卷，挂载到容器 `/data/video-processor`。原视频、成品、数据库、水印和续传数据均在此卷内；镜像普通用户 UID/GID 1000 的首次读写已在隔离测试验证。可在宝塔「存储卷」查看该卷。

**数据卷所在磁盘至少需要 20 GiB 空闲，另外预留原片和成品空间。** 低于阈值时网页上传返回 HTTP 507；这不会单独导致应用容器启动失败。不要把 20 GiB 误认为内存需求。

## 3. 第二阶段：在同一项目启用 GPU

基础服务正常后，在宝塔终端执行宿主机命令 `nvidia-smi`，确认 RTX 3090 和驱动正常。服务器还需要 NVIDIA Container Toolkit，并已配置 Docker 的 GPU 访问。来源：[NVIDIA 安装与 Docker 配置说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

驱动正常且已有可用的 GPU 容器时，先保留现有驱动与运行时配置。没有证据前，不需要重装整个 Docker。安装或调整 Container Toolkit 若涉及重启 Docker，安排在其他业务容器允许重启的时间进行。

等待视频任务结束后，**编辑第一阶段的同一个编排项目**：

1. 将 compose 内容完整替换成 [compose.baota-panel.gpu.yaml](compose.baota-panel.gpu.yaml)。
2. .env 内容保持第一阶段的账号、密码、端口和镜像标签。
3. 更新/重新部署该项目。两份文件使用同一个数据卷，已验证 CPU 切换 GPU 后数据库记录和视频校验值保持一致。

GPU 编排使用 NVIDIA 第 0 张卡，包含 `compute,video,utility` 驱动能力，配置形式符合 [Docker Compose GPU 官方说明](https://docs.docker.com/compose/how-tos/gpu-support/)。

GPU 请求由 Docker 在启动应用前处理。**即使应用选择 auto，驱动或 GPU 运行时未配置好时，Docker 仍会拒绝创建 GPU 容器，应用没有机会回退 CPU。** 第一阶段正是为了把这种故障与应用启动故障分开。

启用后等待 `healthy`，在该容器的终端验证：

```bash
nvidia-smi
python -c 'from web_app.encoding import get_gpu_capabilities; print(get_gpu_capabilities())'
```

应显示 RTX 3090，且 H.264/H.265 的 `available` 为 `True`。网页实际试编码器应显示 `h264_nvenc` 或 `hevc_nvenc`。仅看到 FFmpeg 编码器名称不能确认显卡能实际编码。

CPU CRF 与 GPU CQ 默认均为 32；资源达到 80% 后停止增加新任务，默认最多 8 路，已运行任务仍可能超过阈值。

## 4. 异常时一次收集信息

先区分：

| 现象 | 检查重点 |
| --- | --- |
| 编排异常，容器列表没有应用容器 | 编排解析、镜像标签、端口占用、GPU 分配；查看编排部署日志 |
| 容器反复重启或已退出 | 容器日志的 Python 异常、目录权限和数据库错误 |
| 容器运行但 unhealthy | 健康检查输出；新编排已绕过 HTTP 代理干扰 |
| 容器 healthy，浏览器连不上 | 实际服务器 IP、端口映射、网络防火墙和反向代理 |
| 网页正常，上传 HTTP 507 | 数据卷所在磁盘剩余空间 |
| 第一阶段正常，第二阶段创建失败 | NVIDIA 驱动、Container Toolkit、设备编号和 GPU 分配错误 |

上传 [diagnose_baota.sh](diagnose_baota.sh) 到服务器任意方便的目录，在 **Ubuntu 宿主机的宝塔终端** 运行：

```bash
bash diagnose_baota.sh video-processor-web > video-processor-diagnostic.txt 2>&1
```

脚本只读取版本、指定项目的容器状态/日志、镜像信息、数据权限和 NVIDIA 环境；不安装软件、不启动或停止服务、不修改权限、不删除数据，也不打印 .env 或全部环境变量。报告可能包含内部地址、视频文件名和应用日志，分享前可自行核对。

如果项目名不是 `video-processor-web`，替换命令里的项目名。没有容器时脚本会明确提示，需要同时保留宝塔的编排部署日志。提供完整日志比单独的“异常”标签更容易定位问题。

原交付 `image.tar` 的 SHA-256：

```text
575327f7f07a0822c710123c193fdb85f18ad0bfa6855c24398efa6233fcb2f6
```

## 5. 更新与数据保护

以后在同一项目中更新镜像标签并重新部署，保留 .env、端口和数据卷。备份时，等待任务完成并停止应用容器后，备份完整 `video-processor-web-data` 卷和编排/.env 配置；备份不放在同一卷中。

当前验证环境为本机 Docker Desktop 的 Linux/amd64 容器和 RTX 3060 Laptop；已验证镜像完整性、CPU/GPU 真实编码、网页登录与全批制作。**公司 Ubuntu / RTX 3090 的现场异常尚无日志，不能把本机通过视为公司已经部署成功。** 按本文分阶段部署，可将故障定位到明确的一层。
