# opendesign2api

**把你的 OpenDesign Cloud 模型变成 OpenAI 兼容 API。**

[![CI](https://github.com/AstroQore/opendesign2api/actions/workflows/ci.yml/badge.svg)](https://github.com/AstroQore/opendesign2api/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![container](https://img.shields.io/badge/ghcr.io-opendesign2api-blue)](https://github.com/AstroQore/opendesign2api/pkgs/container/opendesign2api)

[English](README.md)

OpenDesign 的 Vela 登录态本身已经指向官方 OpenAI 形状的 Link 接口：
`/v1/models` 和 `/v1/chat/completions`。本项目是一个很薄的本地网关：读取官方
登录 profile，不把 runtime key 分发给下游应用，并原样转发 JSON 与 SSE 响应。

它不抓网页、不逆向私有线协议，也不内置任何凭据。

```text
OpenAI 客户端 ──► opendesign2api ──► OpenDesign Link
                   127.0.0.1:8792     官方 /v1 入口
```

| 能力 | 状态 |
|---|---|
| `GET /v1/models` | ✅ |
| `POST /v1/chat/completions` | ✅ |
| Chat Completions SSE 流式 | ✅ |
| 可选的本地 Bearer 鉴权 | ✅ |
| 按文件 mtime 热读登录 profile | ✅ |
| `/v1/responses`、Claude Messages、媒体 API | ❌ |

## 凭据文件：先看这里

OpenDesign 桌面端和 `vela login` 会把 Vela profile 写到：

```text
~/.amr/config.json
```

网关从中读取 `profiles.prod.runtimeKey` 和 `profiles.prod.linkUrl`。可以用
`OPENDESIGN2API_PROFILE` 选择别的 profile，用 `OPENDESIGN2API_CONFIG` 指定别的
文件。

这个文件是敏感凭据：

- 不要提交、上传、粘贴到聊天或打进容器镜像；
- 条件允许时保持 `0600`；
- Docker 中只读挂载；
- 只通过你控制的安全通道复制到服务器；
- `vela login` 刷新 profile 后，安全替换服务器副本。网关会在下一次请求按 mtime
  重读，通常不需要重启。

有些 agent 工具会使用名为 `auth.json` 的文件。**opendesign2api 不需要、也不会
读取任何 `auth.json`。** 这类文件同样应视为凭据，绝不能加入本仓库。`.gitignore`
已经排除了常见凭据文件名，但 ignore 不能替代每次 push 前人工检查 `git status`。

本仓库、测试、镜像和 CI 都不包含真实凭据。

## 前置条件

- OpenDesign 桌面端或捆绑的 `vela` CLI
- 已成功执行 `vela login`
- `vela model list` 至少有一个 enabled 模型
- 源码运行需要 Python 3.9+；也可以用 Docker Compose

## 快速开始

### 源码运行

```bash
git clone https://github.com/AstroQore/opendesign2api.git
cd opendesign2api

export OPENDESIGN2API_KEY="$(openssl rand -hex 32)"
python3 src/server.py
```

默认凭据路径是 `~/.amr/config.json`，默认监听 `127.0.0.1:8792`。

另开一个终端：

```bash
export OPENDESIGN2API_KEY='<服务端终端里的本地 bridge key>'
python3 scripts/probe.py
```

probe 会检查 health、模型列表、非流式 completion 和 SSE completion；成功时最后
打印 `PASS`。

### Docker Compose

```bash
git clone https://github.com/AstroQore/opendesign2api.git
cd opendesign2api
sudo install -d -m 0700 /var/lib/opendesign2api
sudo install -m 0400 -o 10001 -g 10001 \
  "$HOME/.amr/config.json" /var/lib/opendesign2api/amr-config.json
cp .env.example .env
```

编辑 `.env`，至少填这两项：

```dotenv
OPENDESIGN2API_CONFIG_FILE=/var/lib/opendesign2api/amr-config.json
OPENDESIGN2API_KEY=replace-with-output-of-openssl-rand-hex-32
```

启动：

```bash
docker compose up -d
docker compose ps
```

Compose 把宿主端口固定在 `127.0.0.1`，把 Vela config 只读挂载到
`/run/secrets/amr-config.json`，并优先拉 GHCR 镜像；拉不到时可用同一 Dockerfile
本地构建。镜像始终使用非 root UID/GID `10001:10001`；mode `0400` 的专用副本既不
改动原始登录文件，也不会向其他用户开放。`vela login` 刷新后要同步替换这个副本。

## 调用

```bash
curl http://127.0.0.1:8792/v1/models \
  -H "Authorization: Bearer $OPENDESIGN2API_KEY"

curl http://127.0.0.1:8792/v1/chat/completions \
  -H "Authorization: Bearer $OPENDESIGN2API_KEY" \
  -H 'content-type: application/json' \
  -d '{
    "model": "deepseek-v4-flash",
    "messages": [{"role": "user", "content": "只回复：OK"}]
  }'
```

OpenAI Python SDK：

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8792/v1",
    api_key=os.environ["OPENDESIGN2API_KEY"],
)

response = client.chat.completions.create(
    model="deepseek-v4-flash",
    messages=[{"role": "user", "content": "只回复：OK"}],
)
print(response.choices[0].message.content)
```

模型可见性取决于当前登录账号，也可能变化。`/v1/models` 只代表当前目录；至少跑
一次真实 completion，才能把某个模型标为可用。

## 配置

| 变量 | 默认值 | 作用 |
|---|---|---|
| `OPENDESIGN2API_HOST` | `127.0.0.1` | 监听地址 |
| `OPENDESIGN2API_PORT` | `8792` | 监听端口 |
| `OPENDESIGN2API_KEY` | 空 | 设置后，下游必须带本地 Bearer key |
| `OPENDESIGN2API_CONFIG` | `~/.amr/config.json` | 官方 Vela profile 文件 |
| `OPENDESIGN2API_PROFILE` | `prod` | config 内的 profile 名 |
| `OPENDESIGN2API_UPSTREAM_TIMEOUT` | `600` | 上游超时秒数 |
| `OPENDESIGN2API_MAX_REQUEST_BYTES` | `8388608` | 最大请求体字节数 |
| `OPENDESIGN2API_CLIENT_TIMEOUT` | `30` | 读取下游请求时的超时秒数 |

除非你明确在前面放了带鉴权的网关或私网，否则保持 loopback。只要同机以外的任何
东西能访问这个端口，就必须设置 `OPENDESIGN2API_KEY`，同时加网络层访问控制。

## 接到 CLIProxyAPI

仓库内包含经过收敛的 systemd unit 和 CPA 接入步骤：
[docs/deploy-behind-cliproxyapi.md](docs/deploy-behind-cliproxyapi.md)。

推荐拓扑：

```text
client ──► CLIProxyAPI ──► 127.0.0.1:8792 ──► OpenDesign Link
```

把它注册为一个 `openai-compatibility` provider。这里已验证的是 OpenAI 入口；没有
单独证明 Claude 兼容端点时，不要凭名称补一条 `claude-api-key` pair。

## 安全边界

- 上游 runtime key 只从 Vela config 读取，不由网关写盘；若上游错误体意外回显它，
  网关会先脱敏再返回。
- 下游 key 是另一把本地 bridge secret，可独立轮换，不影响 OpenDesign 登录态。
- 请求日志包含 method、route、model、上游状态、stream 与耗时；不会主动记录
  prompt 或 Authorization header。
- Docker 与 systemd 示例都只监听 loopback、只读挂载凭据。
- 网关限制请求体，但仍然是每连接一个线程；适合可信的本地聚合器，不适合直接做
  公网多租户 edge。

## 已知限制

- 只实现 Models 与 Chat Completions。
- `/healthz` 只证明 profile 可读，不消耗额度、不请求上游。端到端 readiness 请跑
  `scripts/probe.py`。
- 凭据刷新依赖外部：OpenDesign 桌面端或 `vela login` 更新 profile；服务器副本要由
  运维者安全替换。
- 上游模型、额度、路由、延迟和可用性由 OpenDesign 及其模型供应商控制。

## 排障

- `missing ~/.amr/config.json`：先用 OpenDesign 桌面端登录或执行 `vela login`，再确认
  所选 profile 存在。
- 上游 `401`：刷新 Vela 登录态；不要把 runtime key 写进源码。
- `CERTIFICATE_VERIFY_FAILED`：当前 Python 没有可用 CA bundle。修复该解释器的证书
  配置，或改用操作系统管理的 Python；绝不要通过关闭 TLS 校验来绕过。

## 法律说明

这是独立开源网关，与 OpenDesign 或 Powerformer, Inc. 无隶属、背书关系；只使用你
自己提供的凭据。

在服务器运行或向他人开放前，请查看最新的
[OpenDesign 服务条款](https://open-design.ai/terms/)。本仓库的软件许可证不会授予你
转售、出租、再许可、绕过限额或以其他方式滥用 OpenDesign 托管服务的权利。凭据
安全、模型调用、内容、费用和上游条款合规均由使用者负责。

## 许可证

[AGPL-3.0-only](LICENSE)。如果你修改本网关并让用户通过网络与修改版交互，AGPL 要求
向这些用户提供相应源代码。
