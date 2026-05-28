# huaimg

huaimg 是一个基于 OpenAI Codex imagegen 的轻量级图片生成工具，提供 CLI 和 REST API 两种使用方式。支持单图与批量生成、参考图输入、多种后端模式，内置 Web 界面，开箱即用。

## 关于

[codex](https://github.com/openai/codex) 是 OpenAI 开源的命令行智能体工具，可在终端中直接调用 AI 模型完成编程、文件操作、图片生成等多种任务。其中图片生成通过内置的 `imagegen` 工具实现，能够根据自然语言描述生成高质量图片。

但 codex 作为通用智能体，其图片生成流程存在以下不便：

- 需要手动构造复杂的提示词包装，才能触发 imagegen 工具
- 生成结果散落在本地目录，需要手动查找和整理
- 不支持批量任务，每次只能生成一张图片
- 无法被其他设备或程序方便地调用

huaimg 正是为解决这些问题而生。它将 codex 的图片生成能力封装为标准化的服务，无论你是开发者、设计师还是普通用户，都能通过简洁的命令或直观的 Web 界面快速生成图片。

### 核心功能

**单图生成** — 一条命令或一个 HTTP 请求即可生成图片。支持自定义提示词、风格指导和参考图输入，生成结果自动保存并返回下载链接。

**批量生成** — 通过 JSON 文件定义多个生成任务，支持全局风格和全局参考图。一次性提交，自动逐个处理，适合需要批量出图的工作流。

**参考图支持** — 上传参考图片，让 AI 在生成时参考其风格、构图或内容。支持多张参考图混合使用。

**多种后端模式** — `auto` 模式自动探测可用后端并智能回退；`cli` 模式直接调用 codex；`http` 模式连接本地图像生成服务。按需选择，灵活适配不同环境。

**局域网 API 服务** — 一行命令启动 HTTP 服务，局域网内的任何设备（手机、平板、其他电脑）都可以通过浏览器或 API 调用生成图片，无需安装 codex。

**Web 界面** — 内置现代化 Web UI，打开浏览器即可使用。支持提示词输入、风格设置、参考图上传、批量任务提交和图片画廊浏览。

**结构化输出** — 所有操作返回统一的 JSON 格式，包含成功状态、输出路径、错误信息等字段，便于脚本和程序集成。

**零依赖** — 纯 Python 标准库实现，无需安装任何第三方包，`pip install -e .` 即可使用。

## 功能

- 单张图片生成
- 批量图片生成（从 JSON 驱动）
- 参考图支持
- `auto`、`cli`、`http` 三种后端模式，自动回退
- 局域网 API 服务，供多人共享使用
- JSON / 文本两种输出格式
- 零运行时依赖，仅依赖 Python 标准库

## 安装

```bash
pip install -e .
```

## 命令行

### 探测后端

```bash
huaimg probe
huaimg probe --format json
```

### 生成图片

```bash
huaimg generate \
  --prompt "rainy neon alley at night" \
  --style "cinematic, 35mm lens, soft haze" \
  --image refs/photo.png \
  --format json
```

从文件读取提示词：

```bash
huaimg generate --prompt-file prompt.txt --format json
```

### 批量生成

通过 JSON 文件一次提交多个生成任务，支持全局风格和全局参考图。

```bash
huaimg storyboard --file batch.json --output-dir outputs/project-01 --format json
```

示例 `batch.json`：

```json
{
  "global_style": "watercolor, soft lighting",
  "references": ["refs/style.png"],
  "shots": [
    {"id": "img-1", "prompt": "sunset over ocean"},
    {"id": "img-2", "prompt": "mountain village at dawn"}
  ]
}
```

## 局域网 API 服务

启动 HTTP API 服务，供局域网内其他设备调用。

```bash
# 默认绑定 0.0.0.0:9527
huaimg serve

# 指定端口
huaimg serve --port 9090

# 指定绑定地址和端口
huaimg serve --host 192.168.1.100 --port 9090
```

也可通过环境变量配置：

```bash
HUAIMG_HOST=192.168.1.100 HUAIMG_PORT=9090 huaimg serve
```

### API 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/health` | 健康检查 |
| `GET` | `/api/probe` | 探测后端（codex CLI / HTTP） |
| `POST` | `/api/generate` | 生成图片 |
| `POST` | `/api/storyboard` | 批量生成 |
| `POST` | `/api/upload` | 上传文件到服务端 |
| `GET` | `/api/images/<filename>` | 下载生成的图片 |

### 调用示例

#### 生成图片

```bash
curl -X POST http://192.168.1.100:9527/api/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"一只猫在雨中打伞", "style":"水彩画风"}'
```

响应：

```json
{
  "success": true,
  "mode": "cli",
  "prompt": "一只猫在雨中打伞",
  "output": "outputs/api/a1b2c3d4.png",
  "download_url": "http://192.168.1.100:9527/api/images/a1b2c3d4.png"
}
```

#### 上传参考图后生成

```bash
# 先上传参考图
curl -X POST http://192.168.1.100:9527/api/upload \
  -F "file=@style_ref.png"

# 用返回的 server_path 作为 references
curl -X POST http://192.168.1.100:9527/api/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt":"延续这个风格","references":["/absolute/path/to/style_ref.png"]}'
```

或直接通过 multipart 上传参考图：

```bash
curl -X POST http://192.168.1.100:9527/api/generate \
  -F "prompt=延续这个风格" \
  -F "images=@style_ref.png"
```

#### 批量生成

```bash
curl -X POST http://192.168.1.100:9527/api/storyboard \
  -H "Content-Type: application/json" \
  -d '{
    "global_style": "赛博朋克",
    "shots": [
      {"prompt": "雨夜街道全景"},
      {"prompt": "霓虹灯下的人物特写"}
    ]
  }'
```

## 后端模式

| 模式 | 说明 |
|---|---|
| `auto` | 优先使用本地 HTTP 图像服务，不可用时回退到 `codex exec` |
| `cli` | 始终使用 `codex exec` |
| `http` | 始终使用本地 HTTP 图像服务 |

HTTP 服务地址通过环境变量 `CODEX_IMAGEGEN_PORT` 指定，默认为 `http://127.0.0.1:4312`。

## 前置要求

- 安装 [codex](https://github.com/openai/codex) 并完成认证（支持 ChatGPT 订阅或 API Key）
- Python >= 3.10

## 注意事项

- CLI 模式需要系统 `PATH` 中有可用的 `codex` 命令
- HTTP 模式需要本地运行兼容的图像生成服务
- API 服务默认绑定 `0.0.0.0`，请确保局域网防火墙允许相应端口
- 生成的图片保存在 `outputs/api/` 目录，可通过 API 下载链接直接访问

## 许可证

MIT
