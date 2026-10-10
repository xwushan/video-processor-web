# Ubuntu + NVIDIA 正式环境发布

适用：Ubuntu x86_64、RTX 3090、Docker Engine + Compose 插件。NVIDIA 驱动及 Container Toolkit
按 [ubuntu-nvidia.md](ubuntu-nvidia.md) 安装。维持单个应用容器、单个 Uvicorn worker。
CPU 与 GPU 并发均根据资源增加，GPU 默认上限 8，新增任务的资源阈值为 80%。

## 发布配置与版本

首次部署复制 `.env.example` 到 `.env`；已有部署保留原 `.env`，补充新增配置。
设置独立的 `VIDEO_PROCESSOR_AUTH_PASSWORD`，可同时设置登录名、内网地址和端口。
使用正式配置时空密码会在 Compose 解析阶段被拒绝。设置 `chmod 600 .env`，密码和 Webhook 不进 Git。
当前应用版本为 1.3.7；每次构建用 Git 提交号作为镜像标签及 revision。

客户端可通过服务器内网地址和端口上传视频，浏览器请求使用当前站点地址。若出现连接重置、空响应，
且状态接口也无法访问，先在该客户端打开 `http://服务器地址:8899/health` 检查连通性，再检查服务日志及容器状态。
短暂断线会自动重试；持续断线时已接收分块保留。恢复后使用同一浏览器和访问地址，重新选择相同批次的视频，
点击“预估成品”续传；不要清除浏览器的站点数据。上传会话默认保留 24 小时，由
`VIDEO_PROCESSOR_UPLOAD_SESSION_RETENTION_HOURS` 配置；更新服务前先停止上传。

批量预估最多随机选取 3 个视频，只上传这些样本；开始制作后复用样本原文件并补传其余视频。
预估列表只显示抽中的最多 3 个试编码样本；整批大小和参考范围由实际样本及其余视频的时长推算。浏览器无法读取时长时
使用抽样压缩比例，素材差异较大时误差会增加。预估样片不进入处理记录：制作、刷新、离开页面、
修改参数等操作清理临时样片。服务端每 30 秒检查一次，最后续期超过 10 分钟时自动清理；
服务启动时清理全部遗留试编码样片和工作目录，保留原视频分块与正式成品。

在源码根目录执行以下命令。三个 Compose 文件始终一起使用；原部署的工作目录和 Compose
项目名保持一致，避免创建一个新的空数据卷。更换目录前通过 `docker compose ls` 确认原项目名，
必要时设置 `COMPOSE_PROJECT_NAME` 为该名称。

```bash
export VIDEO_PROCESSOR_IMAGE_TAG="$(git rev-parse HEAD)"
export VIDEO_PROCESSOR_REVISION="$VIDEO_PROCESSOR_IMAGE_TAG"
dc() { docker compose -f compose.yaml -f compose.nvidia.yaml -f compose.production.yaml "$@"; }
dc config --quiet
nvidia-smi
dc build
```

先构建镜像，验证后再切换服务；避免服务停机期间下载依赖。同一个提交标签发布后保留原镜像，
不要重新覆盖该标签。GPU 编码可用性通过容器内实际试编码确认，不能只根据 FFmpeg 编码器列表判断。

## 更新前备份

已有部署先在页面确认任务已完成或暂停，停止上传。下面适用于默认命名卷配置；
若改成宿主机目录挂载，停止服务后直接备份该整个目录。
暂停状态会在重启后保留；运行中意外重启的任务会重新制作尚未完成的视频。

```bash
cid="$(dc ps -q video-processor)"
test -n "$cid" || { echo '未找到现有容器，首次部署可跳过备份'; exit 1; }
volume="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data/video-processor"}}{{.Name}}{{end}}{{end}}' "$cid")"
test -n "$volume" || { echo '不是默认命名卷，请按实际挂载目录备份'; exit 1; }
stamp="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups
docker inspect --format '{{.Image}}' "$cid" > "backups/$stamp-image-id.txt"
docker inspect --format '{{.Config.Image}}' "$cid" > "backups/$stamp-image-name.txt"
dc stop
docker run --rm --user 0:0 \
  -v "$volume:/data:ro" -v "$PWD/backups:/backup" \
  "$(cat "backups/$stamp-image-id.txt")" \
  tar -czf "/backup/$stamp-data.tgz" -C /data .
chmod 600 "backups/$stamp-data.tgz"
tar -tzf "backups/$stamp-data.tgz" > /dev/null
```

确认以上备份成功再发布；备份失败时执行 `dc start` 恢复原服务，排查后重试。
备份包含数据库、视频、水印和临时文件，应保存在有足够空间的独立磁盘；`.env` 另行保密备份。
不执行 `docker compose down -v`，不清理旧镜像，直至发布验证完成且备份已转存。

## 切换与验证

```bash
dc up -d --no-build --wait --wait-timeout 90
dc ps
dc logs --tail=100
curl --fail http://127.0.0.1:8899/health
dc exec -T video-processor nvidia-smi
dc exec -T video-processor python -c \
  'from web_app.encoding import get_gpu_capabilities; print(get_gpu_capabilities())'
```

端口按 `.env` 配置替换。确认 `/health` 的 revision 与发布提交一致，容器 healthy，
GPU 检测中 H.264/H.265 试编码成功；登录后检查原任务记录、下载已有成品。
系统已暂停任务时先按需要恢复，任务完全空闲时再运行自检。

自检使用 Python 标准库，无需宿主机安装 Web 依赖。为了验证登录保护，在当前终端临时提供
与 `.env` 相同的账号和密码，密码通过隐藏输入读取：

```bash
export VIDEO_PROCESSOR_AUTH_USER=admin
read -r -s -p '验证用登录密码: ' VIDEO_PROCESSOR_AUTH_PASSWORD; echo
export VIDEO_PROCESSOR_AUTH_PASSWORD
python3 deploy/validate_docker.py --gpu --container "$(dc ps -q video-processor)" \
  --url http://127.0.0.1:8899
unset VIDEO_PROCESSOR_AUTH_PASSWORD
```

自检制作 H.264/H.265/MKV，检查水印、音轨、下载、打包、断点续传、同名文件和暂停恢复，
最后只删除本次创建的测试任务。测试可能触发已配置的任务通知，安排在维护窗口运行。
内部反向代理可在前端配置 HTTPS；限制端口只允许需要访问的内网网段。

## 回滚

保留本次备份的 `$stamp` 和旧镜像。仅回退代码时，以备份保存的镜像 ID 创建回滚标签，
复用当前数据卷。当前新增数据库字段为向后兼容的附加列。

```bash
dc stop
export VIDEO_PROCESSOR_IMAGE_TAG="rollback-$stamp"
docker image tag "$(cat "backups/$stamp-image-id.txt")" \
  "video-processor-web:$VIDEO_PROCESSOR_IMAGE_TAG"
dc up -d --no-build --wait --wait-timeout 90
```

如果需要同时恢复数据，停止服务后将备份解压到**新建的恢复卷**，验证其中的 SQLite 完整性，
通过独立 Compose 覆盖文件将该卷挂到 `/data/video-processor` 后启动旧镜像。
保留当前卷供调查，恢复前先额外备份当前状态；恢复旧快照会缺少发布之后新增的任务和成品。
不要直接往仍在写入的数据库上覆盖备份文件。

## 内网离线交付

在可联网的 Linux/amd64 Docker 环境完成构建及验证，或显式使用 `docker build --platform linux/amd64`。
保留镜像标签和 Git 提交号后导出：

```bash
docker save "video-processor-web:$VIDEO_PROCESSOR_IMAGE_TAG" | gzip > video-processor-image.tar.gz
sha256sum video-processor-image.tar.gz > video-processor-image.tar.gz.sha256
```

将镜像、校验文件、源码中的三个 Compose 文件及 `.env.example` 复制到 Ubuntu 内网服务器。
服务器执行 `sha256sum -c video-processor-image.tar.gz.sha256` 和
`gunzip -c video-processor-image.tar.gz | docker load`，在 `.env` 设置导入镜像的标签和真实密码，
再执行 `dc up -d --no-build --wait`。NVIDIA 驱动与 Container Toolkit 需提前安装。
部署过程不会依赖服务器从 Docker Hub 或 PyPI 下载应用镜像与 Python 包。
