# 宝塔普通网站部署：Python 后端 + 网站代理

本页适用于不使用 Docker 的部署。可以由 systemd 或宝塔 Python 项目管理后端，再由宝塔网站管理域名与 HTTPS。程序使用 FastAPI/ASGI，入口 `web_app.main:app`，不能按纯静态站点或普通 WSGI 项目启动。

已经使用宝塔 Docker 部署的服务器继续维护原容器即可；本页是另一种部署选择，不要同时让两套进程访问同一数据目录。

## 方案 A：systemd 管后端，宝塔管网站

1. 按 [Ubuntu 普通部署](native-ubuntu.md) 创建虚拟环境、配置和专用账号，完成 systemd 启动。
2. 先验证 `http://服务器IP:8899/health` 与网页登录。
3. 在宝塔「网站」中添加自己的域名/反向代理网站，目标 `http://127.0.0.1:8899`。
4. 在宝塔管理该站点的证书与 HTTPS，代理细节参考 [Nginx 文档](reverse-proxy.md)。

这是后端管理方式最明确的一条路径，后端日志通过 `journalctl -u video-processor` 查看，网站日志在宝塔对应站点查看。

## 方案 B：宝塔 Python 项目管理后端

不同面板版本的入口、虚拟环境和启动方式不同。以下是实际要填的值，使用当前版本提供的自定义命令或 ASGI 运行入口：

| 项目字段 | 内容 |
| --- | --- |
| 项目名称 | video-processor-web |
| 源码目录/工作目录 | /data/video-processor-web，或实际上传目录 |
| Python | 独立的 Python 3.12 环境 |
| 依赖文件 | requirements-web.txt |
| 应用入口 | web_app.main:app |
| 监听端口 | 8899，或自己的空闲端口 |
| 进程/worker 数量 | 1 |
| 运行账号 | 优先专用 video-processor；面板固定账号时使用该实际账号 |
| 自定义启动 | 下方脚本命令，前台运行，由面板负责守护 |

如果面板自动安装仅识别 `requirements.txt`，手动使用该项目的 Python 执行 `-m pip install -r requirements-web.txt`，不把桌面版或全局 Python 依赖混进来。

源码与虚拟环境按 [普通部署文档](native-ubuntu.md) 准备；systemd 与面板守护二选一，不执行方案 A 的服务启动步骤。面板若创建了自己的虚拟环境，将环境文件中的 `VIDEO_PROCESSOR_PYTHON` 设置为它的 Python **绝对路径**，例如以面板显示的路径为准，而非假定为系统 python。

启动命令示例：

```bash
env VIDEO_PROCESSOR_ENV_FILE=/etc/video-processor/video-processor.env bash /data/video-processor-web/deploy/start-web.sh
```

环境文件从 [native.env.example](native.env.example) 创建并填写非空密码。实际源码目录不是 `/data/video-processor-web` 时，命令和 `VIDEO_PROCESSOR_APP_DIR` 一起改。脚本不使用 nohup，也不带 `--reload`，进程由面板管理。

### 面板运行账号与目录权限

运行账号必须能读取源码、虚拟环境和配置文件，并能写入数据目录。native 示例的配置目录访问组、文件组及数据属主都是 video-processor。

如果面板固定使用 `www` 等账号，应在**新部署时**将上述相关账号/组统一替换为面板实际账号；已有目录先备份并核对属主，不盲目递归改权限。可检查：

```bash
ls -ld /data/video-processor-web /data/video-processor-data /etc/video-processor
ls -l /etc/video-processor/video-processor.env
```

不要为了绕过权限问题直接把整个数据目录设为 777 或把项目改成多个 worker。需要 GPU 时，以面板实际运行账号执行普通部署文档中的 NVENC 试编码，避免管理员能用而服务账号无权访问设备。

## 网站绑定、上线检查与故障

后端运行正常后，再映射域名并启用 HTTPS；项目部署在域名根路径。宝塔已有 Nginx 时直接使用它，不额外安装另一个系统 Nginx 争用 80/443。

检查页面登录、从另一台电脑上传、样片 Range 播放、制作与下载。若网站 502，先检查 Python 后端是否运行、监听 IP/端口是否与代理一致，再查看面板项目日志；不要用反复创建项目替代查看具体错误。

配置变动后重启本项目，更新或停用时保留数据目录和环境文件。完整参数与备份流程见 [运维手册](operations.md)。

参考：[宝塔官方 Python 项目管理说明](https://www.bt.cn/bbs/thread-23295-1-1.html)。界面版本差异以安装版本为准。
