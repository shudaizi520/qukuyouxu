# Docker 安装

## 启动

克隆仓库后运行：

```bash
docker compose up -d --build
```

浏览器打开 `http://服务器地址:9511`。Compose 会创建独立的 `qukuyouxu-data` 数据卷，重新构建或升级容器不会删除设置和歌曲缓存。

第一次打开页面时，直接填写管理员用户名和两遍密码即可建立账户，不需要额外的设置码。随后按页面引导连接 Plex。使用 HTTPS 反向代理时，同时设置 `PUBLIC_ORIGIN=https://你的访问域名`。

页面的脚本、样式、图标和歌曲封面都由本应用或你自己的 Plex 服务器提供，不依赖国外 CDN。QQ 音乐、网易云音乐的匹配功能需要访问各自的国内服务；Plex 官方账户授权需要访问 Plex 网站，如果所在网络无法访问，可在设置中选择手动填写局域网 Plex 地址和 Token。加载首页与播放本地曲库不要求浏览器连接国外图片或脚本服务。

## HTTPS 反向代理

反向代理请使用独立域名的根路径，例如 `https://music.example.cn/`，不要放在 `/music/` 子路径下。把 `.env` 中的 `PUBLIC_ORIGIN` 设为浏览器实际使用的完整来源（不含末尾路径），并将代理的 `Host` 头保持为该域名。设置后 HTTPS 访问使用 Secure 会话 Cookie；原有局域网 IP 地址仍可单独登录。不要把容器的 `9511` 端口直接暴露到公网。

Nginx 反代到同一主机时，核心转发规则如下；证书、访问控制和公网防护请在自己的反代中配置：

```nginx
location / {
    proxy_pass http://127.0.0.1:9511;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Range $http_range;
    proxy_read_timeout 180s;
}
```

反代与容器在同一主机时，可以把 Compose 的端口映射改成 `127.0.0.1:9511:9511`，避免旁路访问。部署后应分别检查登录、设置、歌单音频的拖动播放和 `/healthz`；代理不能改写 `/api/`、`/static/` 或音频 `Range` 请求，也不能缓存含登录状态的响应。

设置中的每一项对应“一个 Plex 用户 + 一个音乐资料库”。同一用户使用多个音乐资料库时分别添加即可；已有托管歌单的项目不会被直接改到另一个资料库，而是保留原项目并建立独立项目。

## 查看状态

```bash
docker compose ps
docker compose logs --tail=100 qukuyouxu
```

请勿把包含 Token、Cookie 或播放记录的完整日志粘贴到公开 Issue。

## 完整备份

先停止应用，取得 Compose 实际使用的数据卷名，再把整个 `/data` 打包。备份中含有账户授权和播放记录，必须私密保存。

```bash
mkdir -p backups
docker compose stop
DATA_VOLUME="$(docker inspect qukuyouxu --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}')"
test -n "$DATA_VOLUME"
docker run --rm \
  --mount source="$DATA_VOLUME",target=/from,readonly \
  --mount type=bind,source="$PWD/backups",target=/to \
  alpine:3.20 tar -C /from -czf /to/qukuyouxu-data.tgz .
docker compose start
```

恢复时使用全新空数据卷，停止应用后解压，再将文件所有者设为镜像固定用户 `10001:10001`。不要在未核对卷名时覆盖现有卷。

## 只迁移歌曲缓存

```bash
docker compose exec qukuyouxu qukuyouxu-cache export \
  --database /data/helper.sqlite3 --output /data/music-cache.json
docker cp qukuyouxu:/data/music-cache.json ./music-cache.json
docker compose exec qukuyouxu qukuyouxu-cache inspect \
  --archive /data/music-cache.json
```

向新实例导入时，先把归档复制进容器，再运行 `qukuyouxu-cache import`。默认遇到不同缓存会停止；只有确认目标缓存可以覆盖时才使用 `--replace`。

## 升级

稳定镜像发布后，以 Release 页面标明的版本标签更新 `QUKUYOUXU_IMAGE`，再运行：

```bash
docker compose pull
docker compose up -d
```

不要使用来源不明的镜像，也不要把凭据写进 Compose 文件。
