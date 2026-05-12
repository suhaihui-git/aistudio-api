# AI Studio API

Google AIStudio Playgroud 反代，支持 Google 会员（Pro/Ultra），支持 Gemini 原生协议格式，包含生图、工具调用、Google搜索。

[English](./README_EN.md)

## 功能

- **OpenAI 兼容** — 支持 `/v1/chat/completions`、`/v1/models`、`/v1/images/generations`
- **Gemini 原生 API** — 同时支持 `/v1beta/models/{model}:generateContent`
- **流式输出** — SSE 流式返回
- **多轮对话** — 正确的 user/model 交替结构
- **图片输入** — 支持 base64 内联和 HTTP URL，单图/多图
- **Google 搜索** — 通过 `googleSearchRetrieval` 实时联网搜索
- **Thinking** — 返回模型思考过程（`thinking` 字段）
- **图片生成** — 通过 Gemini 图片模型生成图片
- **反检测** — 使用 Camoufox
- **BotGuard** — 自动特征匹配定位 snapshot 函数
- **多账号轮询** — round-robin / LRU / 最少限流
![alt text](image/chat.png)
## 快速开始
### 直接启动
```bash
# 克隆项目
git clone https://github.com/chrysoljq/aistudio-api.git
cd aistudio-api

# 安装依赖
pip install -r requirements.txt

# 启动服务
python3 main.py server --port 8080 --camoufox-port 9222
```

### Docker 部署


```bash
docker run -d \
  --name aistudio-api \
  --restart unless-stopped \
  -p 8080:8080 \
  -v aistudio-api-data:/app/data \
  ghcr.io/chrysoljq/aistudio-api:latest
```

首次启动后，访问 http://localhost:8080 进入管理后台并添加 Google 账号。服务器/Docker 环境通常没有可见桌面，推荐在本地浏览器登录 Google 后导入 Cookie，或在本地导出账号包再导入服务器。
![alt text](image/login.png)

### 服务器账号登录建议

- 推荐长期方案：每个账号会保存独立的持久浏览器 Profile，目录为 `data/accounts/{account_id}/profile`，同时保留 `auth.json` 用于导入导出和兼容旧流程。
- Docker 部署必须持久化 `/app/data`，否则重启后账号、Cookie 和浏览器 Profile 都会丢失。
- 线上环境可启用内置远程登录桌面：容器会启动 Xvfb + x11vnc + noVNC，管理后台点击“登录账号”后会打开 noVNC 页面，在里面完成 Google 登录。
- Docker Compose 默认把 noVNC 绑定到服务器本机 `127.0.0.1:6080`，外部电脑不能直接访问。线上请用反向代理/VPN 暴露它，并设置 `AISTUDIO_LOGIN_NOVNC_URL` 为可访问地址，例如 `https://你的域名/novnc/vnc.html`；如果确实要直接开放端口，把 `AISTUDIO_LOGIN_NOVNC_BIND` 改成 `0.0.0.0:6080`，并设置 `AISTUDIO_LOGIN_VNC_PASSWORD`。
- `AISTUDIO_LOGIN_NOVNC_URL` 留空时，管理后台会按当前访问地址自动生成 `http(s)://同主机:6080/vnc.html`。如果 noVNC 走反代路径或非 6080 端口，请显式配置 `AISTUDIO_LOGIN_NOVNC_URL`，或设置 `AISTUDIO_LOGIN_NOVNC_PUBLIC_PORT`。
- noVNC 默认会带 `resize=scale` 自动缩放；如果仍只看到部分页面，可以手动在 noVNC 左侧设置里选择 `Scaling Mode: Local Scaling`，或调大 `AISTUDIO_LOGIN_DESKTOP_GEOMETRY`。
- noVNC 登录桌面可以操作 Google 账号，必须放在内网/VPN/反代鉴权后面，或至少设置 `AISTUDIO_LOGIN_VNC_PASSWORD`；当 `AISTUDIO_LOGIN_NOVNC_BIND` 不是 localhost 且未设置密码时，容器会拒绝启动。
- 不使用 Docker 时，需要自行提供 `DISPLAY`/`WAYLAND_DISPLAY`，例如安装并启动 Xvfb/VNC，然后再点击“登录账号”。

## 使用示例

### OpenAI 兼容接口

```bash
# 对话（流式）
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemma-4-31b-it",
    "messages": [{"role": "user", "content": "你好！"}],
    "stream": true
  }'

# 图片理解
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-3-flash-preview",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}},
        {"type": "text", "text": "这是什么？"}
      ]
    }]
  }'

# 查看模型列表
curl http://localhost:8080/v1/models
```

### Gemini 原生接口

```bash
# 联网搜索
curl http://localhost:8080/v1beta/models/gemini-3-flash-preview:generateContent \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "今天上海天气怎么样？"}]}],
    "tools": [{"googleSearchRetrieval": {}}]
  }'
```
### Python（OpenAI SDK）

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8080/v1", api_key="unused")

# 流式对话
response = client.chat.completions.create(
    model="gemini-3-flash-preview",
    messages=[{"role": "user", "content": "你好！"}],
    stream=True,
)
for chunk in response:
    print(chunk.choices[0].delta.content or "", end="")
```

### 命令行客户端

```bash
# 快速对话
python3 main.py client "今天天气怎么样？" --search

# 附带图片
python3 main.py client "这张图是什么？" -a photo.jpg

# 生图
python3 main.py client "画一只猫" --image --save cat.png
```

## 支持的模型

| 模型 | ID | 默认 Google Search | 说明 |
|------|-----|-------------------|------|
| Gemma 4 31B | `gemma-4-31b-it` | ✅ | 默认文本模型 |
| Gemma 4 26B A4B | `gemma-4-26b-a4b-it` | ✅ | MoE，4B 激活 |
| Gemini 3 Flash | `gemini-3-flash-preview` | ❌ | 快速 |
| Gemini 3.1 Pro | `gemini-3.1-pro-preview` | ❌ | |
| Gemini 3.1 Flash Lite | `gemini-3.1-flash-lite` | ❌ | |
| Gemini 3.1 Flash Image | `gemini-3.1-flash-image-preview` | ❌ | 默认图片模型，仅限 Pro/Ultra |
| Gemini 3 Pro Image | `gemini-3-pro-image-preview` | ❌ | |


## 配置

通过环境变量或 `.env` 文件配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AISTUDIO_PORT` | `8080` | API 服务端口 |
| `AISTUDIO_CAMOUFOX_PORT` | `9222` | Camoufox 调试端口 |
| `AISTUDIO_PROXY` | 空 | 浏览器代理地址 |
| `AISTUDIO_DEFAULT_TEXT_MODEL` | `gemma-4-31b-it` | 默认对话模型 |
| `AISTUDIO_DEFAULT_IMAGE_MODEL` | `gemini-3.1-flash-image-preview` | 默认图片模型 |
| `AISTUDIO_CAMOUFOX_HEADLESS` | `1` | 无头模式运行浏览器 |
| `AISTUDIO_TIMEOUT_REPLAY` | `120` | 请求超时（秒） |
| `AISTUDIO_TIMEOUT_STREAM` | `120` | 流式超时（秒） |
| `AISTUDIO_SNAPSHOT_CACHE_TTL` | `3600` | BotGuard snapshot 缓存时间 |
| `AISTUDIO_ACCOUNT_ROTATION_MODE` | `round_robin` | 轮询模式：`round_robin`、`lru`、`least_rl` |
| `AISTUDIO_ACCOUNT_COOLDOWN_SECONDS` | `60` | 限流后冷却时间 |
| `AISTUDIO_ACCOUNT_OPERATION_TIMEOUT` | `30` | 账号切换、导入、导出等独占操作等待请求结束的最长时间 |
| `AISTUDIO_LOGIN_NOVNC_BIND` | `127.0.0.1:6080` | Docker noVNC 暴露地址，公网绑定必须设置 VNC 密码或放在反代鉴权后 |
| `AISTUDIO_LOGIN_NOVNC_URL` | 空 | 管理后台打开远程登录桌面的 URL |
| `AISTUDIO_LOGIN_NOVNC_PUBLIC_PORT` | `6080` | 自动推导 noVNC URL 时使用的外部端口 |
| `AISTUDIO_LOGIN_NOVNC_SCHEME` | 空 | 自动推导 noVNC URL 时强制使用的协议，例如 `https` |
| `AISTUDIO_LOGIN_DESKTOP_GEOMETRY` | `1600x900x24` | 登录远程桌面的虚拟屏幕尺寸 |
| `AISTUDIO_LOGIN_BROWSER_WIDTH` | `1440` | 登录浏览器视口宽度 |
| `AISTUDIO_LOGIN_BROWSER_HEIGHT` | `800` | 登录浏览器视口高度 |
| `AISTUDIO_LOGIN_VNC_PASSWORD` | 空 | noVNC/VNC 登录密码 |
| `AISTUDIO_DUMP_RAW_RESPONSE` | `0` | 保存原始响应到磁盘（调试） |

## 架构

```
客户端（OpenAI SDK / curl）
    │
    ▼
┌─────────────────────┐
│   FastAPI 服务器      │  ← OpenAI + Gemini API 路由
│   /v1/chat/...       │
│   /v1beta/...        │
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Wire Codec         │  ← API 格式 → AI Studio gRPC body
│   + BotGuard         │     自动特征匹配 snapshot 函数
└─────────┬───────────┘
          │
          ▼
┌─────────────────────┐
│   Camoufox 浏览器    │  ← 反指纹 Firefox，注入 cookies
│   （无头模式）        │     通过 XHR hook 发送请求
└─────────┬───────────┘
          │
          ▼
    Google AI Studio
```

**工作原理：**
1. API 请求进入，转换为 AI Studio 的 wire 格式
2. 生成 BotGuard snapshot（自动检测函数，带缓存）
3. 构造完整的 gRPC body，通过 XHR hook 注入浏览器
4. 浏览器带 cookies + BotGuard 发送请求到 Google
5. 解析响应，按请求的 API 格式返回

轮询模式：
- `round_robin` — 轮流使用
- `lru` — 最久未使用
- `least_rl` — 最少被限流

## BotGuard 原理

Google 每次请求都要求一个 BotGuard "snapshot" —— 证明请求来自真实浏览器的加密凭证。本项目：

1. 在运行时 hook 前端的 snapshot 生成函数
2. 通过特征匹配自动定位（`.snapshot({` + `content` + `yield`），无惧 Google 更新
3. 为每个请求生成合法的 snapshot

snapshot 函数名随 Google bundle 更新持续变化（Mv → Ov → Sv → ...），但特征模式保持不变。

## TODO
- [ ] 完整 webui 支持
- [ ] 完整真流式支持
- [ ] 兼容 /v1/messages

## 致谢
- https://github.com/LuanRT/BgUtils
- https://github.com/iBUHub/AIStudioToAPI
- https://linux.do

## License

MIT
