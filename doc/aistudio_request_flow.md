# AI Studio 请求全流程说明

本文档记录本项目如何从 OpenAI/Gemini 兼容 API 请求，转换并转发为 Google AI Studio 请求，再把响应解析回兼容格式。内容基于当前代码实现整理，重点代码路径如下：

- `src/aistudio_api/api/routes_openai.py`
- `src/aistudio_api/api/routes_gemini.py`
- `src/aistudio_api/api/schemas.py`
- `src/aistudio_api/application/api_service.py`
- `src/aistudio_api/application/chat_service.py`
- `src/aistudio_api/infrastructure/gateway/client.py`
- `src/aistudio_api/infrastructure/gateway/capture.py`
- `src/aistudio_api/infrastructure/gateway/wire_codec.py`
- `src/aistudio_api/infrastructure/gateway/wire_types.py`
- `src/aistudio_api/infrastructure/gateway/session.py`
- `src/aistudio_api/domain/models.py`
- `src/aistudio_api/api/responses.py`

## 1. 外部 API 鉴权

所有 OpenAI/Gemini 兼容接口都会经过 `require_api_key` 鉴权。

支持的 API Key 传递方式：

```http
Authorization: Bearer sk-aistudio-xxx
```

```http
X-API-Key: sk-aistudio-xxx
```

```http
X-Goog-Api-Key: sk-aistudio-xxx
```

也支持查询参数：

```text
?key=sk-aistudio-xxx
```

校验逻辑在 `src/aistudio_api/api/dependencies.py`，由 `api_key_store.verify_key(raw_key)` 完成。

管理端接口使用 `aistudio_admin` Cookie：

```http
Cookie: aistudio_admin=<session_token>
```

该 Cookie 只用于 Web 管理后台，不会作为请求 Google AI Studio 的 Cookie。

## 2. AI Studio 登录 Cookie

请求 Google AI Studio 需要 Google 登录态 Cookie。项目不要求外部 API 调用方每次传 Google Cookie，而是把 Google Cookie 保存成账号的 Playwright storage state。

默认账号目录：

```text
data/accounts/{account_id}/auth.json
data/accounts/{account_id}/meta.json
data/accounts/{account_id}/profile/
data/accounts/registry.json
```

也可通过环境变量覆盖：

```text
AISTUDIO_ACCOUNTS_DIR=/path/to/accounts
```

### 2.1 导入 Cookie 字符串

管理接口：

```http
POST /accounts/import-cookies
Cookie: aistudio_admin=<admin_session>
Content-Type: application/json
```

请求体：

```json
{
  "cookies": "SID=...; HSID=...; SSID=...; APISID=...; SAPISID=...; __Secure-1PSID=...; __Secure-3PSID=...",
  "name": "主账号",
  "email": "example@gmail.com"
}
```

`cookies` 支持浏览器开发者工具或 Cookie 扩展导出的 `key=value; key=value` 格式。

保存账号时会为该账号创建独立的持久浏览器 Profile。首次加载账号时，代码会把 `auth.json` 中的 cookies/localStorage 种子写入 Profile，并创建：

```text
data/accounts/{account_id}/profile/.aistudio_storage_seed
```

之后浏览器会直接复用该 Profile，Google 刷新的登录态也会在浏览器关闭、切号、认证失败重建 context 或导出账号前同步回 `auth.json`。重新导入 Cookie 或覆盖账号时会重置 Profile/seed marker，确保新 Cookie 被重新注入。

服务器或 Docker 部署时必须持久化账号目录，例如 `/app/data` 或 `AISTUDIO_ACCOUNTS_DIR` 指向的目录；否则重启后 Profile 与 Cookie 都会丢失。

解析逻辑在 `src/aistudio_api/infrastructure/account/cookie_parser.py`：

- 核心认证 Cookie 会复制到多个 Google 域：`.google.com`、`.youtube.com`、`.google.com.tw`
- 部分 Cookie 有固定域名覆盖，例如 `OTZ`、`__Host-GAPS`、`__Secure-BUCKET`
- 输出格式为 Playwright storage state：

```json
{
  "cookies": [
    {
      "name": "SID",
      "value": "...",
      "domain": ".google.com",
      "path": "/",
      "secure": true,
      "httpOnly": false,
      "sameSite": "None",
      "expires": 1780000000
    }
  ],
  "origins": []
}
```

常见关键 Cookie 名称：

```text
SID
HSID
SSID
APISID
SAPISID
__Secure-1PSID
__Secure-3PSID
__Secure-1PAPISID
__Secure-3PAPISID
NID
```

代码会在加载账号时检查这些 Cookie 是否存在，但不会输出敏感值。检查逻辑在 `AccountService._log_cookie_health`。

### 2.2 浏览器登录

也可以通过管理端启动浏览器登录：

```http
POST /accounts/login/start
Cookie: aistudio_admin=<admin_session>
Content-Type: application/json
```

请求体：

```json
{
  "name": "浏览器登录账号"
}
```

登录完成后同样保存为 `auth.json`。

线上/Docker 环境可以启用内置远程登录桌面：

```text
AISTUDIO_ENABLE_LOGIN_DESKTOP=1
AISTUDIO_LOGIN_NOVNC_PORT=6080
AISTUDIO_LOGIN_NOVNC_BIND=127.0.0.1:6080
AISTUDIO_LOGIN_NOVNC_URL=
AISTUDIO_LOGIN_NOVNC_PUBLIC_PORT=6080
AISTUDIO_LOGIN_NOVNC_SCHEME=
AISTUDIO_LOGIN_DESKTOP_GEOMETRY=1600x900x24
AISTUDIO_LOGIN_BROWSER_WIDTH=1440
AISTUDIO_LOGIN_BROWSER_HEIGHT=800
AISTUDIO_LOGIN_VNC_PASSWORD=一个强密码
```

启用后容器会启动 Xvfb + x11vnc + noVNC。管理后台调用 `/accounts/login/start` 后，响应会包含：

```json
{
  "session_id": "login_xxx",
  "browser_url": "http://服务器IP:6080/vnc.html"
}
```

前端会自动打开 `browser_url`，在远程浏览器里完成 Google 登录。登录成功后，后端保存该账号的 `auth.json` 和持久 Profile。

安全注意：noVNC 页面可以直接操作 Google 登录态，线上必须放在内网/VPN/反代鉴权后面，或设置 `AISTUDIO_LOGIN_VNC_PASSWORD`，不要裸露公网。当 `AISTUDIO_LOGIN_NOVNC_BIND` 不是 localhost 且未设置 VNC 密码时，容器会拒绝启动。

部署注意：`AISTUDIO_LOGIN_NOVNC_BIND=127.0.0.1:6080` 只允许服务器本机访问 noVNC。如果你在自己电脑访问管理后台，不能只把打开的新窗口里的 `localhost` 手动改成服务器 IP，必须选择其一：

- 通过 Nginx/Caddy/VPN/SSH 隧道把服务器本机的 6080 暴露出来，并把 `AISTUDIO_LOGIN_NOVNC_URL` 配成最终可访问地址
- 或把 `AISTUDIO_LOGIN_NOVNC_BIND` 改成 `0.0.0.0:6080`，同时设置 `AISTUDIO_LOGIN_VNC_PASSWORD` 并放行服务器防火墙/安全组 6080 端口

`AISTUDIO_LOGIN_NOVNC_URL` 留空时，后端会按访问管理后台的 Host 自动推导 `http(s)://同主机:6080/vnc.html`。如果你使用反代路径、HTTPS 域名或非 6080 外部端口，请显式配置 `AISTUDIO_LOGIN_NOVNC_URL`。

显示注意：自动推导的 noVNC 地址会带 `resize=scale`，让远程桌面按当前浏览器窗口缩放。如果仍只能看到部分页面，可以在 noVNC 左侧设置里把 `Scaling Mode` 改成 `Local Scaling`，或调大 `AISTUDIO_LOGIN_DESKTOP_GEOMETRY`、`AISTUDIO_LOGIN_BROWSER_WIDTH`。

输入注意：noVNC 原生粘贴受浏览器剪贴板权限限制。容器内会启动 `autocutsel` 同步 X11 剪贴板；如果仍无法粘贴，账号管理页会在登录会话期间显示“远程登录输入”。先点击 noVNC 里的目标输入框，再在管理页粘贴文本并发送，后端会通过 Playwright 直接写入当前焦点输入框。

### 2.3 浏览器登录账号的注意事项

通过 `/accounts/login/start` 登录成功后，后端会保存两类状态：

- `auth.json`：Playwright storage state，包含 Google Cookie 和 localStorage
- `profile/`：Camoufox/Firefox 持久化浏览器 Profile，包含登录过程中浏览器写入的本地状态

线上部署必须持久化账号目录。Docker 场景至少要持久化 `/app/data`，否则重启后 `auth.json`、`profile/` 和账号注册表会丢失，表现为管理页仍能打开但请求 Google AI Studio 时认证失败。

账号登录成功只表示远程登录浏览器已经进入 AI Studio 页面；真正调用生成接口时，还需要主请求浏览器用该账号重新打开 AI Studio，并生成本次请求需要的 Google Web 认证头。当前实现会在主请求浏览器启动时输出不含敏感值的检查日志：

```text
主请求浏览器 cookie 检查: cookies=31, google_cookies=20, auth_cookies=['APISID', 'HSID', 'SAPISID', ...]
```

如果 `auth_cookies=[]`，说明账号登录态没有被主请求浏览器加载成功，优先检查账号目录是否持久化、是否刚覆盖导入过账号、`profile/` 是否被清空，以及是否需要重新登录。

如果 `auth_cookies` 中包含 `SAPISID`、`APISID`、`__Secure-*PAPISID` 等核心 Cookie，但仍出现：

```text
CREDENTIALS_MISSING
```

通常不是外部 `sk-aistudio-*` API Key 的问题，而是请求 Google RPC 时的 Web 认证头无效。流式请求会优先走浏览器内 XHR；如果浏览器返回 `network error`，会进入 HTTP fallback。fallback 不能复用抓包时的旧 `authorization`，需要根据当前 Cookie 重新生成 `SAPISIDHASH`、`SAPISID1PHASH`、`SAPISID3PHASH`，并去掉重复的 `Origin/origin`、`Referer/referer` 头。正常日志应类似：

```text
HTTP 流式回退请求: ..., target_cookies=0, auth=['SAPISIDHASH', 'SAPISID1PHASH', 'SAPISID3PHASH']
```

这里 `target_cookies=0` 不一定异常，因为请求目标是 `alkalimakersuite-pa.clients6.google.com`，浏览器不会把 `.google.com` Cookie 直接发给这个域名；真正关键是 `auth=[...]` 是否能生成。如果 `auth=[]`，说明当前账号 Cookie 中缺少可用于生成 Google Web 认证头的 `SAPISID` 或 `__Secure-*PAPISID`。

账号切换、删除、导入、导出会独占浏览器状态。默认单账号只启动 1 个浏览器 worker：

```text
AISTUDIO_SINGLE_ACCOUNT_MAX_CONCURRENCY=1
```

如果需要单账号并发，可以把 `AISTUDIO_SINGLE_ACCOUNT_MAX_CONCURRENCY` 调大。服务会为每个并发槽启动独立的 browser worker，每个 worker 有自己的 AI Studio 页面、Hook 模板、snapshot 缓存和请求流状态；请求从开始到流式结束都会独占其中一个 worker。

浏览器登录账号会保存持久化 `profile/`。当单账号并发数大于 1 时，worker 不会同时打开同一个 Firefox profile，而是从账号 profile 复制出运行时副本，并用当前 `auth.json` 补种子。这样可以避免 profile 文件锁冲突，同时保证 HTTP fallback 能用最新 Cookie 生成 Google Web 认证头。

建议从 2 开始压测。并发过高仍可能触发 AI Studio/Google 账号侧风控，出现 `429`、`network error` 或 `CREDENTIALS_MISSING`。多账号轮询只负责失败/限流后的账号切换，不代表同一个账号可以无限并发。

## 3. OpenAI 兼容请求格式

入口：

```http
POST /v1/chat/completions
Authorization: Bearer sk-aistudio-xxx
Content-Type: application/json
```

请求模型在 `ChatRequest`：

```json
{
  "model": "gemini-3.1-pro-preview",
  "messages": [
    {"role": "system", "content": "你是一个助手"},
    {"role": "user", "content": "你好"}
  ],
  "stream": true,
  "temperature": 1,
  "top_p": 0.95,
  "top_k": 64,
  "max_tokens": 65536,
  "stream_options": {
    "include_usage": true
  },
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "getWeather",
        "description": "获取天气",
        "parameters": {
          "type": "object",
          "properties": {
            "city": {"type": "string"}
          },
          "required": ["city"]
        }
      }
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| `model` | string | 模型 ID，默认来自 `AISTUDIO_DEFAULT_TEXT_MODEL` |
| `messages` | array | OpenAI 风格消息列表 |
| `stream` | bool | 是否 SSE 流式返回，默认 `false` |
| `temperature` | float/null | 写入 AI Studio generation config |
| `top_p` | float/null | 写入 AI Studio generation config |
| `top_k` | int/null | 写入 AI Studio generation config |
| `max_tokens` | int/null | 写入 AI Studio `generation_config[3]` |
| `tools` | array/null | 仅支持 `type=function` 的 OpenAI 工具声明 |
| `stream_options.include_usage` | bool | 流式响应是否发送 usage，默认 `true` |

图片输入支持 OpenAI vision 风格：

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "描述这张图"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBOR..."}}
  ]
}
```

也支持 HTTP 图片 URL，代码会先下载到临时文件再转成 inline data。

## 4. Gemini 兼容请求格式

入口：

```http
POST /v1beta/models/gemini-3.1-pro-preview:generateContent
Authorization: Bearer sk-aistudio-xxx
Content-Type: application/json
```

流式入口：

```http
POST /v1beta/models/gemini-3.1-pro-preview:streamGenerateContent
```

请求体：

```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {"text": "你好"}
      ]
    }
  ],
  "systemInstruction": {
    "role": "user",
    "parts": [
      {"text": "你是一个助手"}
    ]
  },
  "tools": [
    {
      "googleSearch": {}
    }
  ],
  "generationConfig": {
    "stopSequences": ["STOP"],
    "temperature": 1,
    "topP": 0.95,
    "topK": 64,
    "maxOutputTokens": 65536,
    "responseMimeType": "text/plain",
    "responseSchema": null,
    "presencePenalty": 0,
    "frequencyPenalty": 0,
    "responseLogprobs": false,
    "logprobs": null,
    "mediaResolution": null,
    "thinkingConfig": [1, null, null, 3]
  }
}
```

`generationConfig.maxOutputTokens` 会归一化为内部 `max_tokens`，最终同样写到 `generation_config[3]`。

支持的 Gemini part：

```json
{"text": "文本"}
```

```json
{
  "inlineData": {
    "mimeType": "image/png",
    "data": "base64..."
  }
}
```

暂不支持：

```json
{"fileData": {"fileUri": "..."}}
```

代码会返回 `fileData is not supported yet`。

## 5. 请求归一化

OpenAI 请求由 `normalize_chat_request` 处理：

- `system`、`developer` 消息合并为 `system_instruction`
- `assistant` role 转换为 AI Studio 的 `model`
- 其他 role 默认转为 `user`
- 文本转为 `AistudioPart(text=...)`
- 图片转为 `AistudioPart(inline_data=(mime, base64))`
- 捕获 snapshot 使用 `capture_prompt` 和可选图片

Gemini 请求由 `normalize_gemini_request` 处理：

- `contents[].parts[].text` 转为文本 part
- `inlineData` 转为 inline data part，并落临时图片文件用于 capture
- `tools.googleSearch`/`googleSearchRetrieval` 转为 Google Search wire 模板
- `tools.codeExecution` 转为 Code Execution wire 模板
- `functionDeclarations` 转为 AI Studio function declaration wire
- `generationConfig` 转成 `generation_config_overrides`

## 6. AI Studio Wire Body 格式

最终发送给 AI Studio 的 body 是 JSON 数组，不是公开 Gemini JSON。核心索引由 `AistudioWireCodec` 定义：

| body 索引 | 名称 | 说明 |
|---:|---|---|
| `0` | model | 模型名，通常是 `models/{model}` |
| `1` | contents | 对话内容 |
| `2` | safety_settings | 安全配置 |
| `3` | generation_config | 生成参数数组 |
| `4` | snapshot | BotGuard snapshot |
| `5` | system_instruction | 系统指令 |
| `6` | tools | 工具配置 |
| `10` | request_flag | 请求标记 |
| `11` | cached_content | 缓存内容 |
| `13` | location | 时区等位置信息 |

典型 body：

```json
[
  "models/gemini-3.1-pro-preview",
  [
    [
      [[null, "你好"]],
      "user"
    ]
  ],
  [
    [null, null, 7, 5],
    [null, null, 8, 5],
    [null, null, 9, 5],
    [null, null, 10, 5]
  ],
  [
    null,
    null,
    null,
    65536,
    1,
    0.95,
    64,
    "text/plain",
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    null,
    [1, null, null, 3],
    1
  ],
  "<BotGuard snapshot>",
  null,
  null,
  null,
  null,
  null,
  1
]
```

### 6.1 generation_config 索引

由 `AistudioGenerationConfig` 定义：

| generation_config 索引 | 字段 | 外部来源 |
|---:|---|---|
| `1` | `stop_sequences` | Gemini `stopSequences` |
| `3` | `max_tokens` | OpenAI `max_tokens` / Gemini `maxOutputTokens` |
| `4` | `temperature` | `temperature` |
| `5` | `top_p` | OpenAI `top_p` / Gemini `topP` |
| `6` | `top_k` | `top_k` / `topK` |
| `7` | `response_mime_type` | Gemini `responseMimeType` |
| `8` | `response_schema` | Gemini `responseSchema` |
| `9` | `presence_penalty` | Gemini `presencePenalty` |
| `10` | `frequency_penalty` | Gemini `frequencyPenalty` |
| `11` | `response_logprobs` | Gemini `responseLogprobs` |
| `12` | `logprobs` | Gemini `logprobs` |
| `14` | `media_resolution` | Gemini `mediaResolution` |
| `16` | `thinking_config` | 默认或 Gemini `thinkingConfig` |
| `17` | `request_flag` | 默认 `1` |
| `26` | `output_resolution` | 图片输出分辨率 |

重点：`max_tokens` 正确位置是：

```text
body[3][3]
```

例如：

```json
[
  null,
  null,
  null,
  65536,
  1,
  0.95,
  64
]
```

表示：

```text
max_tokens = 65536
temperature = 1
top_p = 0.95
top_k = 64
```

## 7. BotGuard Snapshot 与模板捕获

AI Studio 请求需要 BotGuard snapshot。项目通过浏览器上下文生成合法 snapshot。

流程：

1. `BrowserSession` 打开 AI Studio 页面：

```text
https://aistudio.google.com/prompts/new_chat
https://aistudio.google.com/app/prompts/new_chat
```

2. 注入 `INSTALL_HOOKS_JS`
3. 在 `window.default_MakerSuite` 中自动寻找包含 `.snapshot({`、`content`、`yield` 特征的函数
4. Hook snapshot 函数，生成并保存 `window.__bg_snapshot`
5. Hook XHR/fetch，用于捕获或替换 `GenerateContent` 请求体

模板捕获由 `RequestCaptureService.capture` 完成：

- 如果 snapshot 缓存命中，直接复用缓存的 `url`、`headers`、`body`
- 否则调用 `BrowserSession.capture_template(model)` 捕获模板请求
- 再调用 `BrowserSession.generate_snapshot(contents)` 生成本次内容对应 snapshot
- 最后用 `modify_body(...)` 把模板 body 改成真实请求 body

## 8. 请求发送流程

### 8.1 非流式

入口：

```text
handle_chat -> AIStudioClient.generate_content
```

核心步骤：

1. `capture_request(...)` 获取 AI Studio 请求模板和 snapshot
2. `modify_body(...)` 写入模型、内容、system instruction、tools、generation config
3. `RequestReplayService.replay(...)` 发送请求
4. 浏览器模式下调用 `BrowserSession.send_hooked_request(...)`
5. 在浏览器页面内发 XHR：

```javascript
xhr.open('POST', args.url)
xhr.withCredentials = true
xhr.timeout = args.timeout * 1000
xhr.send(args.body)
```

`withCredentials = true` 会让浏览器带上已加载的 Google Cookie。

### 8.2 流式

入口：

```text
handle_chat -> _build_streaming_response -> AIStudioClient.stream_generate_content
```

核心步骤：

1. 与非流式一样先 capture 和 `modify_body`
2. `StreamingGateway.stream_chat(...)`
3. 优先使用 `BrowserSession.send_streaming_request(...)`
4. 如果浏览器流式重放出现网络错误，尝试 HTTP fallback
5. 使用 `IncrementalJSONStreamParser` 增量解析 AI Studio 返回的 JSON chunk
6. 转换为 OpenAI SSE 或 Gemini SSE

流式超时会根据 `max_tokens` 动态放大，避免大输出仍被默认 120 秒总超时截断。逻辑在 `completion_timeout_seconds`。

## 9. OpenAI 响应格式

非流式响应：

```json
{
  "id": "chatcmpl-xxxxxxxxxxxx",
  "object": "chat.completion",
  "created": 1760000000,
  "model": "models/gemini-3.1-pro-preview",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "你好，有什么可以帮你？",
        "thinking": "可选思考内容",
        "tool_calls": [
          {
            "id": "call_xxx_0",
            "type": "function",
            "function": {
              "name": "getWeather",
              "arguments": "{\"city\":\"上海\"}"
            }
          }
        ]
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 5,
    "completion_tokens": 161,
    "total_tokens": 166,
    "completion_tokens_details": {
      "reasoning_tokens": 153
    }
  }
}
```

如果返回工具调用：

```text
finish_reason = "tool_calls"
```

流式响应：

```text
data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1760000000,"model":"models/gemini-3.1-pro-preview","choices":[{"index":0,"delta":{"role":"assistant","content":"你好"},"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1760000000,"model":"models/gemini-3.1-pro-preview","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":"stop"}],"usage":null}

data: {"id":"chatcmpl-xxx","object":"chat.completion.chunk","created":1760000000,"model":"models/gemini-3.1-pro-preview","choices":[],"usage":{"prompt_tokens":5,"completion_tokens":161,"total_tokens":166,"completion_tokens_details":{"reasoning_tokens":153}}}

data: [DONE]
```

如果有思考内容，delta 中会出现：

```json
{
  "thinking": "模型思考内容"
}
```

## 10. Gemini 响应格式

非流式：

```json
{
  "candidates": [
    {
      "content": {
        "role": "model",
        "parts": [
          {"text": "思考内容", "thought": true},
          {"text": "最终回答"}
        ]
      },
      "finishReason": "STOP"
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 5,
    "candidatesTokenCount": 8,
    "thoughtsTokenCount": 153,
    "totalTokenCount": 166
  }
}
```

流式：

```text
data: {"candidates":[{"content":{"role":"model","parts":[{"text":"你好"}]},"finishReason":null}]}

data: {"candidates":[],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":8,"thoughtsTokenCount":153,"totalTokenCount":166}}

data: [DONE]
```

## 11. AI Studio 原始响应解析

解析逻辑在 `src/aistudio_api/domain/models.py`。

核心行为：

- `extract_outer_json(raw)` 从原始文本中提取 JSON
- `_iter_response_chunks(...)` 兼容单 chunk 和多 chunk
- `parse_response_chunk(...)` 解析候选内容
- 文本 part 拼接到 `Candidate.text`
- thought part 拼接到 `Candidate.thinking`
- inline image part 解码为 `GeneratedImage`
- function call/response 转为统一 dict
- usage 从 chunk 的索引 `2` 解析

usage 映射：

| AI Studio usage 索引 | 内部字段 |
|---:|---|
| `0` | `prompt_tokens` |
| `1` | visible completion tokens |
| `2` | `total_tokens` |
| `3` | `cached_tokens` |
| `4` | `prompt_tokens_details` |
| `9` | reasoning tokens |

内部 `completion_tokens` 会把 visible tokens 和 reasoning tokens 相加。

## 12. 常见排查点

### 12.1 max_tokens 是否生效

检查 raw dump 或抓包 body：

```text
body[3][3] == 请求的 max_tokens
```

例如：

```text
body[3][3] = 65536
```

如果这个位置正确，说明字段已经写入 AI Studio 请求体。

### 12.2 长输出 5000 token 左右中断

如果 `body[3][3]` 已经是大值，但仍在长输出时中断，优先检查：

- `AISTUDIO_TIMEOUT_STREAM`
- `AISTUDIO_TIMEOUT_REPLAY`
- 服务日志里的 `timeout`、`network error`、`aborted`

当前代码会根据 `max_tokens` 自动放大总超时，但环境变量仍是基础下限。

### 12.3 Cookie 失效

典型错误：

```text
AI Studio 账号未登录或 Cookie 已失效：当前页面跳转到了 Google 登录页
```

处理方式：

- 本地浏览器重新登录后，通过管理后台导入 Cookie 或导入账号包
- 有可见桌面/VNC/Xvfb 时，重新通过管理端浏览器登录
- 或重新导入完整 Google Cookie
- 确认 `auth.json` 中包含常见认证 Cookie

### 12.4 BotGuard hook 失败

可能错误：

```text
no_default_MakerSuite
no_snapshot_fn
Hook install failed
```

排查方向：

- AI Studio 页面是否正常加载
- Google 前端 bundle 是否大改
- Cookie 是否跳转登录页
- 是否有弹窗/地区提示遮挡

## 13. 调试原始请求和响应

可开启原始请求/响应落盘：

```text
AISTUDIO_DUMP_RAW_RESPONSE=1
AISTUDIO_DUMP_RAW_RESPONSE_DIR=/tmp
```

非流式落盘内容包含：

```json
{
  "kind": "generate_content",
  "model": "models/...",
  "capture_prompt": "...",
  "modified_body": [],
  "raw_response": "..."
}
```

流式落盘内容包含：

```json
{
  "kind": "stream_generate_content",
  "model": "models/...",
  "url": "...",
  "status_code": 200,
  "modified_body": [],
  "raw_response": "..."
}
```

注意：dump 文件可能包含 prompt、响应正文、请求 body、snapshot 等敏感信息，不要提交到仓库。
