# Nginx、域名和 HTTPS

适用于 Docker、宝塔 Docker、普通 Python 三种后端。先让后端 `http://服务器IP:8899` 能登录并处理短视频，再配置代理。以下示例使用域名 `video.example.com`，替换为自己的域名或内部 DNS 记录。

## 1. 后端绑定本地地址

同一台服务器上的 Nginx 代理目标为 `http://127.0.0.1:8899`：

- Docker：`.env` 设置 `VIDEO_PROCESSOR_BIND_IP=127.0.0.1`，在原项目重新部署端口映射。
- 普通部署：环境文件设置相同变量，重启本应用。

Nginx 在另一台机器时，需要使用后端可达的内网 IP 并限制相应网络访问。不要将 127.0.0.1 当作另一台服务器的地址。

## 2. 宝塔网站配置

在宝塔创建网站/反向代理，绑定实际域名，目标设置为上述后端地址。沿用宝塔现有 Nginx 和站点 `server`，只在代理 `location` 中补充下面的指令；不要把整个新 server 重复粘贴到已有 server 里面。

```nginx
client_max_body_size 64m;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Authorization $http_authorization;
proxy_connect_timeout 30s;
proxy_send_timeout 300s;
proxy_read_timeout 300s;
proxy_request_buffering off;
proxy_buffering off;
proxy_cache off;
```

保留宝塔生成的 `proxy_pass http://127.0.0.1:8899`，同一层级已有同名指令时修改原值，不重复添加。代理覆盖根路径 `/`，不要只代理 `/api/`，不要额外挂一个未经适配的 `/video/` 前缀。

## 3. 独立 Ubuntu Nginx

仅在服务器没有现成网站面板/Nginx 时安装系统 Nginx：

```bash
sudo apt-get update
sudo apt-get install -y nginx
```

把 [nginx-video-processor.conf](nginx-video-processor.conf) 安装为站点配置，修改域名与后端端口：

```bash
sudo install -m 0644 deploy/nginx-video-processor.conf /etc/nginx/sites-available/video-processor.conf
sudoedit /etc/nginx/sites-available/video-processor.conf
sudo ln -s /etc/nginx/sites-available/video-processor.conf /etc/nginx/sites-enabled/video-processor.conf
sudo nginx -t
sudo systemctl reload nginx
```

已有对应链接时直接编辑并验证。只有 `nginx -t` 成功才 reload；保留其他业务站点的配置。宝塔通常使用自己的 Nginx 路径和管理入口，不能套用本节的系统服务命令。

## 4. HTTPS 和访问范围

域名解析到服务器后，通过宝塔证书界面或已有组织证书系统配置 HTTPS，再验证网页登录、上传和样片。公网 ACME 签发需要满足 DNS/HTTP 验证条件，纯内网域名可使用公司 CA，客户端也要信任该 CA。

站点应允许实际需要的内网网段或经批准的入口访问；Docker 发布端口的防火墙规则需按实际连通性检查。不要将目录中的环境文件、数据库、备份映射为静态下载。

## 5. 上传与播放检查

网页使用 8 MiB 分块上传，因此 64m 是对单个 HTTP 请求的限制，不是整段视频总大小。完整文件上传 API 则需按实际请求调整；应用自身仍有上传总量与磁盘余量限制。

日志和登录/API/样片不使用缓存，保留客户端的 Range 请求。测试登录、长视频分块上传、缩略图、样片拖动播放、整批制作及 ZIP 下载。默认无单独 WebSocket 服务，无需为本项目额外引入另一个后端进程。

| 错误 | 重点 |
| --- | --- |
| 502 | 后端退出、IP/端口错误、监听不匹配 |
| 413 | Nginx 单请求 body 限制 |
| 504 | 后端处理/代理超时，结合两侧日志判断 |
| 401/登录反复失效 | 代理认证头、Cookie、是否缓存接口、是否同一访问地址 |
| 页面正常但样片异常 | Range、代理路径、失效样片缓存、浏览器编码支持 |

参考：[Nginx 官方代理指令](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。
