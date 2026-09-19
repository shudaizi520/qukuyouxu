# 曲库有序

曲库有序是一个面向中文 Plex 音乐库的自托管助手。它根据真实播放行为生成每日推荐和智能歌单，并可选择使用 QQ 音乐补充中文歌曲的分类资料。

## 主要功能

- 为多个 Plex 用户分别学习播放习惯和生成每日推荐。
- 生成每周常听、时光胶囊、最近新增和自定义智能歌单。
- 发布前预览，避免覆盖手工歌单；助手管理的歌单支持安全更新。
- 自动隔离儿歌，避免儿童播放记录影响成人推荐。
- QQ 音乐作为可选增强；不授权、关闭或暂时不可用时，Plex 核心功能仍可运行。
- 新增歌曲的 QQ 资料查询支持缓存、暂停和续跑。

## 快速开始

从源码启动：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
DATA_ROOT=./data qukuyouxu
```

打开 `http://localhost:9511`。第一次使用时直接填写管理员用户名和两遍密码即可建立账户，不需要额外的设置码。随后按页面引导连接 Plex。

设置中的每一项对应“一个 Plex 用户 + 一个音乐资料库”。同一用户可以添加多个音乐资料库；如果当前项目已经拥有托管歌单，选择另一个资料库会建立独立项目，原来的歌单、缓存和学习记录不会被搬过去或覆盖。

Docker 用户可使用仓库中的 `compose.yaml`。应用数据统一保存到 `/data`，升级镜像不会删除设置和缓存。

## QQ 音乐扩展

QQ 音乐只用于补充中文歌曲资料和主题来源，不负责下载音乐。标准 Docker 镜像和 `requirements.lock` 已包含扩展依赖，但只有用户主动扫码授权后才会启用；未授权或使用只安装核心依赖的源码环境时，应用会隐藏相应能力，不影响 Plex 推荐、播放学习和智能歌单。

## 数据与备份

最耗时的数据是逐首建立的 QQ 歌曲匹配缓存。安装应用时会同时安装缓存迁移命令，迁移时不需要重新扫描全部歌曲。缓存归档包含歌曲资料，请保存在私有目录，不要提交到 Git。

```bash
# 导出并校验（输出只显示数量和摘要，不显示歌曲资料）
qukuyouxu-cache export \
  --database /data/helper.sqlite3 --output music-cache.json
qukuyouxu-cache inspect --archive music-cache.json

# 导入新数据库；如目标存在不同缓存，默认停止而不是覆盖
qukuyouxu-cache import \
  --archive music-cache.json --database /data/helper.sqlite3
```

完整设置、登录、播放记录和托管状态位于 `/data`。升级或迁移前先停止应用并备份整个数据目录；Docker 和 TrueNAS 的可复制命令见安装文档。

## 隐私

Plex Token、QQ 授权、播放记录和缓存只保存在用户自己的数据目录。请勿把数据库、`.env`、日志或包含凭据的截图上传到公开 Issue。

管理页面默认面向家庭局域网使用。不要把 `9511` 端口直接转发到公网；需要远程访问时，应通过可信 VPN，或在启用 HTTPS 和访问控制的反向代理后使用。通过 HTTPS 反向代理访问时，在 `.env` 中设置 `PUBLIC_ORIGIN=https://你的访问域名`。

## 支持平台

目标镜像支持 `linux/amd64` 和 `linux/arm64`，适用于通用 Docker、TrueNAS SCALE、群晖和 Unraid。

## 开发

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.lock
pip install --no-deps -e .
PYTHONPATH=src python -m unittest discover -s tests -q
```

## 许可证

曲库有序使用 [GNU Affero General Public License v3.0](LICENSE)。
可选的 `qqmusic-api-python` 依赖使用 GPLv3-or-later；发布二进制或镜像时应保留相应第三方许可证说明。
