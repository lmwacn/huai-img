from __future__ import annotations

import base64
import inspect
import json
import os
import re
import tempfile
import time
import traceback
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, unquote

from .backends import BackendError, probe_backends
from .generator import generate_image
from .models import GenerateRequest
from .storyboard import run_storyboard_from_data

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 9527
OUTPUT_DIR = Path("outputs/api")


def _debug_enabled() -> bool:
    return os.getenv("HUAIMG_DEBUG", "0").lower() not in ("0", "false", "no", "off")


def _debug(message: str) -> None:
    if _debug_enabled():
        print(f"[huaimg-api] {message}", flush=True)


def _ensure_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>huaimg</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#1a1a2e;--surface:#16213e;--surface2:#0f3460;--accent:#e94560;--text:#eee;--text2:#aab;--radius:8px}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
.header{background:var(--surface);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #ffffff10}
.header h1{font-size:20px;font-weight:600}
.header .status{font-size:13px;color:var(--text2);display:flex;align-items:center;gap:6px}
.header .dot{width:8px;height:8px;border-radius:50%;background:#4caf50;display:inline-block}
.tabs{display:flex;gap:0;background:var(--surface);border-bottom:1px solid #ffffff10}
.tab{padding:12px 24px;cursor:pointer;font-size:14px;color:var(--text2);border-bottom:2px solid transparent;transition:all .2s}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.content{max-width:960px;margin:0 auto;padding:24px}
.panel{display:none}.panel.active{display:block}
label{display:block;font-size:13px;color:var(--text2);margin-bottom:6px}
input[type=text],textarea,select{width:100%;padding:10px 12px;background:var(--surface);border:1px solid #ffffff15;border-radius:var(--radius);color:var(--text);font-size:14px;outline:none;transition:border .2s}
input[type=text]:focus,textarea:focus{border-color:var(--accent)}
textarea{min-height:160px;font-family:monospace;font-size:13px;resize:vertical}
.btn{padding:10px 20px;border:none;border-radius:var(--radius);font-size:14px;cursor:pointer;transition:all .2s;font-weight:500}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover{opacity:.9}
.btn-secondary{background:var(--surface2);color:var(--text)}.btn-secondary:hover{background:#1a4a8a}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-loading{position:relative;padding-left:40px;min-width:200px}
.btn-loading .spinner{position:absolute;left:12px;top:50%;transform:translateY(-50%);width:20px;height:20px;border:2px solid #fff4;border-top-color:#fff;border-radius:50%;animation:spin .7s linear infinite}
.field{margin-bottom:16px}
.row{display:flex;gap:12px;align-items:end}
.row>*{flex:1}
.file-drop{border:2px dashed #ffffff20;border-radius:var(--radius);padding:24px;text-align:center;color:var(--text2);cursor:pointer;transition:all .2s}
.file-drop:hover,.file-drop.dragover{border-color:var(--accent);background:#e9456010}
.file-drop input{display:none}
.result{margin-top:16px;padding:16px;background:var(--surface);border-radius:var(--radius);display:none}
.result.show{display:block}
.result img{max-width:100%;border-radius:var(--radius);margin-top:8px}
.result pre{font-size:12px;color:var(--text2);white-space:pre-wrap;word-break:break-all;margin-top:8px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.gallery-item{position:relative;border-radius:var(--radius);overflow:hidden;background:var(--surface);cursor:pointer;aspect-ratio:1}
.gallery-item img{width:100%;height:100%;object-fit:cover}
.gallery-item:hover img{opacity:.8}
.status-card{padding:20px;background:var(--surface);border-radius:var(--radius);margin-bottom:12px}
.status-card h3{font-size:15px;margin-bottom:12px}
.status-item{display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid #ffffff08;font-size:14px}
.status-item:last-child{border:none}
.badge{padding:2px 8px;border-radius:4px;font-size:12px;font-weight:500}
.badge.ok{background:#4caf5030;color:#4caf50}
.badge.err{background:#e9456030;color:#e94560}
.loading{display:inline-block;width:16px;height:16px;border:2px solid #ffffff30;border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.modal-overlay{display:none;position:fixed;inset:0;background:#000a;z-index:100;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal-overlay img{max-width:90vw;max-height:90vh;border-radius:var(--radius)}
.empty{text-align:center;color:var(--text2);padding:60px 0;font-size:14px}
.progress-panel{margin-top:16px;padding:20px;background:var(--surface);border-radius:var(--radius);border-left:3px solid var(--accent);display:none}
.progress-panel.show{display:block}
.progress-steps{display:flex;flex-direction:column;gap:10px}
.progress-step{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--text2)}
.progress-step.active{color:var(--text)}
.progress-step.done{color:#4caf50}
.progress-step.error{color:var(--accent)}
.step-icon{width:20px;height:20px;border-radius:50%;border:2px solid #ffffff20;display:flex;align-items:center;justify-content:center;font-size:11px;flex-shrink:0}
.progress-step.active .step-icon{border-color:var(--accent);animation:pulse 1.5s ease-in-out infinite}
.progress-step.done .step-icon{border-color:#4caf50;background:#4caf5030}
.progress-step.error .step-icon{border-color:var(--accent);background:#e9456030}
.progress-timer{margin-top:12px;padding-top:12px;border-top:1px solid #ffffff10;font-size:12px;color:var(--text2);display:flex;align-items:center;gap:6px}
.progress-timer .dots{display:inline-block;width:4px;height:4px;border-radius:50%;background:var(--accent);animation:blink 1s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
</style>
</head>
<body>

<div class="header">
  <h1>huaimg</h1>
  <div class="status"><span class="dot"></span><span id="serverStatus">检查中...</span></div>
</div>

<div class="tabs">
  <div class="tab active" data-tab="generate">生成图片</div>
  <div class="tab" data-tab="batch">批量生成</div>
  <div class="tab" data-tab="gallery">图片画廊</div>
  <div class="tab" data-tab="status">后端状态</div>
</div>

<div class="content">

  <!-- Generate -->
  <div class="panel active" id="panel-generate">
    <div class="field">
      <label>提示词 (Prompt)</label>
      <input type="text" id="prompt" placeholder="描述你想要生成的图片...">
    </div>
    <div class="row">
      <div class="field">
        <label>风格 (Style)</label>
        <input type="text" id="style" placeholder="如：水彩画风、赛博朋克...">
      </div>
      <div class="field">
        <label>模式</label>
        <select id="mode">
          <option value="auto">auto</option>
          <option value="cli">cli</option>
          <option value="http">http</option>
        </select>
      </div>
    </div>
    <div class="field">
      <label>参考图 (可选，可多选)</label>
      <div class="file-drop" id="fileDrop">
        <input type="file" id="refFiles" multiple accept="image/*">
        <div>点击选择或拖拽图片到此处</div>
        <div id="fileNames" style="margin-top:8px;font-size:12px"></div>
      </div>
    </div>
    <button class="btn btn-primary" id="btnGenerate" onclick="doGenerate()">生成图片</button>
    <div class="progress-panel" id="generateProgress">
      <div class="progress-steps">
        <div class="progress-step" id="step-connect"><span class="step-icon">1</span>连接后端</div>
        <div class="progress-step" id="step-generate"><span class="step-icon">2</span>生成图片</div>
        <div class="progress-step" id="step-save"><span class="step-icon">3</span>保存结果</div>
      </div>
      <div class="progress-timer"><span class="dots"></span><span id="timerText">准备中...</span></div>
    </div>
    <div class="result" id="generateResult">
      <pre id="generateResultText"></pre>
      <img id="generateResultImg" style="display:none">
    </div>
  </div>

  <!-- Batch -->
  <div class="panel" id="panel-batch">
    <div class="field">
      <label>批量任务 JSON</label>
      <textarea id="batchJson">{
  "global_style": "水彩画风",
  "shots": [
    {"id": "img-1", "prompt": "日落海面"},
    {"id": "img-2", "prompt": "山村晨曦"}
  ]
}</textarea>
    </div>
    <button class="btn btn-primary" id="btnBatch" onclick="doBatch()">提交批量任务</button>
    <div class="progress-panel" id="batchProgress">
      <div class="progress-steps">
        <div class="progress-step" id="bstep-connect"><span class="step-icon">1</span>连接后端</div>
        <div class="progress-step" id="bstep-generate"><span class="step-icon">2</span>批量生成中</div>
        <div class="progress-step" id="bstep-save"><span class="step-icon">3</span>保存结果</div>
      </div>
      <div class="progress-timer"><span class="dots"></span><span id="batchTimerText">准备中...</span></div>
    </div>
    <div class="result" id="batchResult">
      <pre id="batchResultText"></pre>
    </div>
  </div>

  <!-- Gallery -->
  <div class="panel" id="panel-gallery">
    <div style="margin-bottom:16px;display:flex;justify-content:space-between;align-items:center">
      <h3 style="font-size:15px">已生成的图片</h3>
      <button class="btn btn-secondary" onclick="loadGallery()">刷新</button>
    </div>
    <div class="gallery" id="galleryGrid"></div>
    <div class="empty" id="galleryEmpty" style="display:none">暂无图片</div>
  </div>

  <!-- Status -->
  <div class="panel" id="panel-status">
    <button class="btn btn-secondary" onclick="loadStatus()" style="margin-bottom:16px">刷新状态</button>
    <div class="status-card">
      <h3>后端探测</h3>
      <div id="statusContent"><div class="loading"></div></div>
    </div>
  </div>

</div>

<div class="modal-overlay" id="modal" onclick="this.classList.remove('show')">
  <img id="modalImg">
</div>

<script>
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
let selectedFiles = [];

// Tabs
$$('.tab').forEach(t => t.onclick = () => {
  $$('.tab').forEach(x => x.classList.remove('active'));
  $$('.panel').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  $(`#panel-${t.dataset.tab}`).classList.add('active');
  if (t.dataset.tab === 'gallery') loadGallery();
  if (t.dataset.tab === 'status') loadStatus();
});

// File drop
const drop = $('#fileDrop');
const fileInput = $('#refFiles');
drop.onclick = () => fileInput.click();
drop.ondragover = e => { e.preventDefault(); drop.classList.add('dragover'); };
drop.ondragleave = () => drop.classList.remove('dragover');
drop.ondrop = e => { e.preventDefault(); drop.classList.remove('dragover'); handleFiles(e.dataTransfer.files); };
fileInput.onchange = () => handleFiles(fileInput.files);
function handleFiles(files) {
  for (const f of files) {
    if (!selectedFiles.some(s => s.name === f.name && s.size === f.size)) {
      selectedFiles.push(f);
    }
  }
  updateFileNames();
}
function updateFileNames() {
  $('#fileNames').textContent = selectedFiles.map(f => f.name).join(', ') || '未选择文件';
}

// Health check
fetch('/api/health').then(r => r.json()).then(d => {
  $('#serverStatus').textContent = '运行中';
}).catch(() => {
  $('#serverStatus').textContent = '连接失败';
  document.querySelector('.dot').style.background = '#e94560';
});

// Generate
let generateTimer = null;
function startProgress(prefix, timerId) {
  const panel = $(prefix === 'b' ? '#batchProgress' : '#generateProgress');
  panel.classList.add('show');
  let seconds = 0;
  $(timerId).textContent = '0s';
  generateTimer = setInterval(() => {
    seconds++;
    $(timerId).textContent = seconds + 's';
  }, 1000);
  return seconds;
}
function stopProgress() {
  if (generateTimer) { clearInterval(generateTimer); generateTimer = null; }
}
function setStep(id, state, text) {
  const el = document.getElementById(id);
  el.className = 'progress-step ' + state;
  if (text) el.querySelector('.step-icon').textContent = state === 'done' ? '✓' : state === 'error' ? '✕' : el.querySelector('.step-icon').textContent;
}

async function doGenerate() {
  const prompt = $('#prompt').value.trim();
  if (!prompt) { alert('请输入提示词'); return; }
  const btn = $('#btnGenerate');
  const inputs = [$('#prompt'), $('#style'), $('#mode')];

  // Disable UI
  btn.disabled = true;
  btn.classList.add('btn-loading');
  btn.innerHTML = '<span class="spinner"></span>生成中...';
  inputs.forEach(el => el.disabled = true);

  // Reset and show progress
  ['step-connect','step-generate','step-save'].forEach(id => { setStep(id, '', '1'); });
  startProgress('', '#timerText');
  setStep('step-connect', 'active');

  const fd = new FormData();
  fd.append('prompt', prompt);
  fd.append('style', $('#style').value);
  fd.append('mode', $('#mode').value);
  for (const f of selectedFiles) fd.append('images', f);

  try {
    setStep('step-connect', 'done');
    setStep('step-generate', 'active');

    const res = await fetch('/api/generate', { method: 'POST', body: fd });
    const data = await res.json();

    setStep('step-generate', data.success ? 'done' : 'error');
    if (data.success) setStep('step-save', 'done');

    stopProgress();
    const elapsed = $('#timerText').textContent;
    $('#generateResult').classList.add('show');
    $('#generateResultText').textContent = (data.success ? '✓ 生成成功 (' + elapsed + ')\n\n' : '') + JSON.stringify(data, null, 2);
    const img = $('#generateResultImg');
    if (data.success && data.download_url) {
      img.src = data.download_url + '?t=' + Date.now();
      img.style.display = 'block';
    } else {
      img.style.display = 'none';
    }
  } catch (e) {
    setStep('step-generate', 'error');
    stopProgress();
    $('#generateResult').classList.add('show');
    $('#generateResultText').textContent = '请求失败: ' + e.message;
    $('#generateResultImg').style.display = 'none';
  } finally {
    btn.disabled = false;
    btn.classList.remove('btn-loading');
    btn.textContent = '生成图片';
    inputs.forEach(el => el.disabled = false);
  }
}

// Batch
async function doBatch() {
  const raw = $('#batchJson').value.trim();
  if (!raw) { alert('请输入 JSON'); return; }
  let data;
  try { data = JSON.parse(raw); } catch { alert('JSON 格式错误'); return; }
  const btn = $('#btnBatch');

  btn.disabled = true;
  btn.classList.add('btn-loading');
  btn.innerHTML = '<span class="spinner"></span>生成中...';
  $('#batchJson').disabled = true;

  ['bstep-connect','bstep-generate','bstep-save'].forEach(id => { setStep(id, '', '1'); });
  startProgress('b', '#batchTimerText');
  setStep('bstep-connect', 'active');

  try {
    setStep('bstep-connect', 'done');
    setStep('bstep-generate', 'active');

    const res = await fetch('/api/storyboard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    const result = await res.json();

    const success = result.success !== false;
    setStep('bstep-generate', success ? 'done' : 'error');
    if (success) setStep('bstep-save', 'done');

    stopProgress();
    const elapsed = $('#batchTimerText').textContent;
    $('#batchResult').classList.add('show');
    $('#batchResultText').textContent = (success ? '✓ 批量生成完成 (' + elapsed + ')\n\n' : '') + JSON.stringify(result, null, 2);
  } catch (e) {
    setStep('bstep-generate', 'error');
    stopProgress();
    $('#batchResult').classList.add('show');
    $('#batchResultText').textContent = '请求失败: ' + e.message;
  } finally {
    btn.disabled = false;
    btn.classList.remove('btn-loading');
    btn.textContent = '提交批量任务';
    $('#batchJson').disabled = false;
  }
}

// Gallery
async function loadGallery() {
  try {
    const res = await fetch('/api/images/list');
    const data = await res.json();
    const grid = $('#galleryGrid');
    const empty = $('#galleryEmpty');
    if (!data.images || data.images.length === 0) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = data.images.map(img => `
      <div class="gallery-item" onclick="showModal('${img.url}')">
        <img src="${img.url}" loading="lazy" alt="${img.filename}">
      </div>
    `).join('');
  } catch {
    $('#galleryGrid').innerHTML = '<div class="empty">加载失败</div>';
  }
}

function showModal(url) {
  $('#modalImg').src = url;
  $('#modal').classList.add('show');
}

// Status
async function loadStatus() {
  $('#statusContent').innerHTML = '<div class="loading"></div>';
  try {
    const res = await fetch('/api/probe');
    const d = await res.json();
    $('#statusContent').innerHTML = `
      <div class="status-item"><span>Codex CLI</span><span class="badge ${d.codex_available ? 'ok' : 'err'}">${d.codex_available ? '可用' : '不可用'}</span></div>
      <div class="status-item"><span>HTTP 服务</span><span class="badge ${d.http_available ? 'ok' : 'err'}">${d.http_available ? '可用' : '不可用'}</span></div>
      <div class="status-item"><span>服务地址</span><span>${d.service_url}</span></div>
      ${d.codex_error ? `<div class="status-item"><span>错误</span><span style="color:#e94560;font-size:12px">${d.codex_error}</span></div>` : ''}
    `;
  } catch {
    $('#statusContent').innerHTML = '<div style="color:#e94560">获取状态失败</div>';
  }
}
</script>
</body>
</html>"""


def _parse_multipart(body: bytes, boundary: str) -> tuple[dict[str, str], list[tuple[str, str, bytes]]]:
    """Simple multipart/form-data parser. Returns (fields, files)."""
    boundary_bytes = boundary.encode("utf-8")
    parts = body.split(b"--" + boundary_bytes)
    fields: dict[str, str] = {}
    files: list[tuple[str, str, bytes]] = []

    for part in parts:
        if not part.strip() or part.strip() == b"--":
            continue
        header_end = part.find(b"\r\n\r\n")
        if header_end == -1:
            continue
        headers_raw = part[:header_end].decode("utf-8", errors="replace")
        data = part[header_end + 4:]
        # Strip trailing \r\n
        if data.endswith(b"\r\n"):
            data = data[:-2]
        if data.endswith(b"\r\n"):
            data = data[:-2]

        name_match = re.search(r'name="([^"]*)"', headers_raw)
        if not name_match:
            continue
        name = name_match.group(1)

        filename_match = re.search(r'filename="([^"]*)"', headers_raw)
        if filename_match:
            files.append((name, filename_match.group(1), data))
        else:
            fields[name] = data.decode("utf-8", errors="replace")

    return fields, files


def _json_response(handler: BaseHTTPRequestHandler, status: int, data: object) -> None:
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _openai_error_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    message: str,
    *,
    error_type: str = "invalid_request_error",
    param: str | None = None,
    code: str | None = None,
) -> None:
    _json_response(
        handler,
        status,
        {
            "error": {
                "message": message,
                "type": error_type,
                "param": param,
                "code": code,
            }
        },
    )


def _base_url(handler: BaseHTTPRequestHandler) -> str:
    host_header = handler.headers.get("Host", f"localhost:{DEFAULT_PORT}")
    return f"http://{host_header}"


def _append_openai_options(prompt: str, data: dict) -> str:
    hints = []
    size = data.get("size")
    quality = data.get("quality")
    if size and str(size) != "auto":
        hints.append(f"Requested image size/aspect: {size}")
    if quality and str(quality) != "auto":
        hints.append(f"Requested quality: {quality}")
    if not hints:
        return prompt
    return f"{prompt}\n\n" + "\n".join(hints)


def _image_path_from_result(result_output: str | None, fallback: Path) -> Path | None:
    if fallback.exists():
        return fallback
    if result_output:
        path = Path(result_output)
        if path.exists() and path.is_file():
            return path
    return None


def _copy_into_api_output(source: Path, destination: Path) -> Path:
    if source.resolve() == destination.resolve():
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())
    return destination


def _log_exception(context: str, exc: BaseException) -> None:
    print(f"[huaimg-api] {context}: {type(exc).__name__}: {exc}", flush=True)
    traceback.print_exc()


class HuaimgHandler(BaseHTTPRequestHandler):
    # Silence per-request logs; we log start/stop manually.
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    # ---- OPTIONS (CORS preflight) ----

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    # ---- GET ----

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._handle_index()
        elif parsed.path == "/api/health":
            self._handle_health()
        elif parsed.path == "/api/probe":
            self._handle_probe()
        elif parsed.path == "/v1/models":
            self._handle_openai_models()
        elif parsed.path == "/__debug/routes" and _debug_enabled():
            self._handle_debug_routes()
        elif parsed.path == "/api/images/list":
            self._handle_image_list()
        elif parsed.path.startswith("/api/images/"):
            self._handle_image_download(parsed.path)
        else:
            _json_response(self, 404, {"error": "Not found"})

    def _handle_index(self) -> None:
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_image_list(self) -> None:
        _ensure_output_dir()
        images = []
        for f in OUTPUT_DIR.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                rel = f.relative_to(OUTPUT_DIR)
                images.append({
                    "filename": f.name,
                    "url": f"/api/images/{rel.as_posix()}",
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                })
        _json_response(self, 200, {"images": images})

    def _handle_health(self) -> None:
        _json_response(self, 200, {"status": "ok", "service": "huaimg-api"})

    def _handle_probe(self) -> None:
        result = probe_backends()
        _json_response(self, 200, result.to_dict())

    def _handle_openai_models(self) -> None:
        _json_response(
            self,
            200,
            {
                "object": "list",
                "data": [
                    {
                        "id": "gpt-image-2",
                        "object": "model",
                        "created": 0,
                        "owned_by": "huaimg",
                    },
                    {
                        "id": "gpt-image-1",
                        "object": "model",
                        "created": 0,
                        "owned_by": "huaimg",
                    },
                ],
            },
        )

    def _handle_debug_routes(self) -> None:
        _json_response(
            self,
            200,
            {
                "source": str(Path(__file__).resolve()),
                "debug": os.getenv("HUAIMG_DEBUG", "0"),
                "post_routes": [
                    "/api/generate",
                    "/v1/images/generations",
                    "/api/storyboard",
                    "/api/upload",
                ],
                "do_post_source": inspect.getsource(self.do_POST),
            },
        )

    def _handle_image_download(self, path: str) -> None:
        # Extract everything after /api/images/
        rel_path = path[len("/api/images/"):]
        # Normalize path separators and prevent directory traversal
        rel_path = rel_path.replace("\\", "/")
        if ".." in rel_path:
            _json_response(self, 403, {"error": "Forbidden"})
            return
        filepath = (OUTPUT_DIR / rel_path).resolve()
        # Ensure resolved path is still under OUTPUT_DIR
        if not str(filepath).startswith(str(OUTPUT_DIR.resolve())):
            _json_response(self, 403, {"error": "Forbidden"})
            return
        if not filepath.exists() or not filepath.is_file():
            _json_response(self, 404, {"error": "Image not found"})
            return
        data = filepath.read_bytes()
        ext = filepath.suffix.lower()
        content_type = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    # ---- POST ----

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        _debug(f"POST {parsed.path}")
        if parsed.path == "/api/generate":
            self._handle_generate()
        elif parsed.path == "/v1/images/generations":
            self._handle_openai_image_generation()
        elif parsed.path == "/api/storyboard":
            self._handle_storyboard()
        elif parsed.path == "/api/upload":
            self._handle_upload()
        else:
            _json_response(self, 404, {"error": "Not found"})

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _handle_generate(self) -> None:
        _ensure_output_dir()
        content_type = self.headers.get("Content-Type", "")
        _debug("/api/generate request received")

        try:
            prompt: str = ""
            style: str | None = None
            mode: str = "auto"
            timeout: int = 180
            references: list[Path] = []
            temp_files: list[Path] = []

            if "application/json" in content_type:
                body = self._read_body()
                data = json.loads(body)
                prompt = str(data.get("prompt", "")).strip()
                if not prompt:
                    raise ValueError("prompt is required")
                style = data.get("style")
                mode = str(data.get("mode", "auto"))
                timeout = int(data.get("timeout", 180))
                for ref in data.get("references", []):
                    p = Path(str(ref))
                    if not p.exists():
                        raise ValueError(f"Reference image not found: {str(ref)}")
                    references.append(p)

            elif "multipart/form-data" in content_type:
                boundary_match = re.search(r"boundary=(?:(?:\"([^\"]+)\")|([^;\s]+))", content_type)
                if not boundary_match:
                    raise ValueError("Could not parse multipart boundary")
                boundary = boundary_match.group(1) or boundary_match.group(2)
                body = self._read_body()
                fields, files = _parse_multipart(body, boundary)

                prompt = fields.get("prompt", "").strip()
                if not prompt:
                    raise ValueError("prompt is required")
                style = fields.get("style")
                mode = fields.get("mode", "auto")
                timeout = int(fields.get("timeout", "180"))

                for _, fname, fdata in files:
                    suffix = Path(fname).suffix or ".png"
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                    tmp.write(fdata)
                    tmp.close()
                    tmp_path = Path(tmp.name)
                    temp_files.append(tmp_path)
                    references.append(tmp_path)
            else:
                _json_response(
                    self, 400,
                    {"success": False, "error": "Unsupported Content-Type, use application/json or multipart/form-data"},
                )
                return

            output_filename = f"{uuid.uuid4().hex}.png"
            output_path = OUTPUT_DIR / output_filename

            request = GenerateRequest(
                prompt=prompt,
                mode=mode,
                references=references,
                output=output_path,
                style=style,
                timeout=timeout,
            )
            result = generate_image(request)

            # Clean up temp uploaded files
            for tmp in temp_files:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

            result_dict = result.to_dict()
            if result.success and output_path.exists():
                result_dict["download_url"] = f"{_base_url(self)}/api/images/{output_filename}"

            status = 200 if result.success else 500
            _json_response(self, status, result_dict)

        except (ValueError, BackendError, OSError) as e:
            _log_exception("/api/generate failed", e)
            _json_response(self, 400, {"success": False, "error": str(e)})
        except Exception as e:
            _log_exception("/api/generate crashed", e)
            _json_response(self, 500, {"success": False, "error": str(e)})

    def _handle_openai_image_generation(self) -> None:
        _ensure_output_dir()
        content_type = self.headers.get("Content-Type", "")
        _debug("OpenAI-compatible generation request received")

        if "application/json" not in content_type:
            _openai_error_response(
                self,
                400,
                "Unsupported Content-Type, use application/json",
                param="Content-Type",
            )
            return

        try:
            body = self._read_body()
            data = json.loads(body or b"{}")
            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")

            prompt = str(data.get("prompt", "")).strip()
            if not prompt:
                _openai_error_response(self, 400, "prompt is required", param="prompt")
                return

            response_format = str(data.get("response_format", "url"))
            if response_format not in ("url", "b64_json"):
                _openai_error_response(
                    self,
                    400,
                    "response_format must be one of: url, b64_json",
                    param="response_format",
                )
                return

            n = int(data.get("n", 1))
            if n < 1 or n > 10:
                _openai_error_response(self, 400, "n must be between 1 and 10", param="n")
                return

            mode = str(data.get("mode", "auto"))
            timeout = int(data.get("timeout", 180))
            style = data.get("style")
            merged_prompt = _append_openai_options(prompt, data)
            created = int(time.time())
            image_items: list[dict[str, str]] = []

            for index in range(n):
                output_filename = f"{uuid.uuid4().hex}.png"
                output_path = OUTPUT_DIR / output_filename
                _debug(f"Generating OpenAI-compatible image {index + 1}/{n}...")
                request = GenerateRequest(
                    prompt=merged_prompt,
                    mode=mode,
                    references=[],
                    output=output_path,
                    style=str(style) if style else None,
                    timeout=timeout,
                )
                result = generate_image(request)
                if not result.success:
                    raise BackendError(result.error or "image generation failed")

                image_path = _image_path_from_result(result.output, output_path)
                if image_path is None:
                    raise BackendError("image generation completed but no output image was found")

                if response_format == "b64_json":
                    image_items.append({
                        "b64_json": base64.b64encode(image_path.read_bytes()).decode("ascii")
                    })
                else:
                    downloadable = _copy_into_api_output(image_path, output_path)
                    image_items.append({
                        "url": f"{_base_url(self)}/api/images/{downloadable.name}"
                    })

            _debug(f"OpenAI-compatible generation completed: {len(image_items)} image(s)")
            response: dict[str, object] = {
                "created": created,
                "data": image_items,
            }

            _json_response(self, 200, response)

        except json.JSONDecodeError as e:
            _log_exception("OpenAI-compatible request JSON parse failed", e)
            _openai_error_response(self, 400, "Invalid JSON request body")
        except (ValueError, BackendError, OSError) as e:
            _log_exception("OpenAI-compatible generation failed", e)
            _openai_error_response(self, 500 if isinstance(e, BackendError) else 400, str(e))
        except Exception as e:
            _log_exception("OpenAI-compatible generation crashed", e)
            _openai_error_response(self, 500, str(e), error_type="server_error")

    def _handle_storyboard(self) -> None:
        _ensure_output_dir()
        content_type = self.headers.get("Content-Type", "")

        try:
            body = self._read_body()

            if "application/json" in content_type:
                data = json.loads(body)
            else:
                _json_response(
                    self, 400,
                    {"success": False, "error": "Storyboard requires Content-Type: application/json"},
                )
                return

            if not isinstance(data, dict):
                raise ValueError("Request body must be a JSON object")

            mode = str(data.get("mode", "auto"))
            timeout = int(data.get("timeout", 180))
            output_dir = data.get("output_dir")
            resolved_output_dir = Path(str(output_dir)) if output_dir else OUTPUT_DIR / f"storyboard-{uuid.uuid4().hex[:8]}"

            # Handle shot-level references - resolve paths from the server
            resolved = _resolve_storyboard_refs(data)
            results = run_storyboard_from_data(
                resolved,
                output_dir=resolved_output_dir,
                mode=mode,
                timeout=timeout,
            )

            # Add download_url for each shot
            for r in results:
                output = r.get("output")
                if output and r.get("success"):
                    out_path = Path(output)
                    if out_path.exists():
                        # Use relative path from OUTPUT_DIR for the URL
                        try:
                            rel = out_path.relative_to(OUTPUT_DIR)
                        except ValueError:
                            rel = out_path.name
                        r["download_url"] = f"{_base_url(self)}/api/images/{rel.as_posix()}"

            _json_response(self, 200, {"success": True, "results": results})

        except (ValueError, BackendError, OSError) as e:
            _json_response(self, 400, {"success": False, "error": str(e)})

    def _handle_upload(self) -> None:
        _ensure_output_dir()
        content_type = self.headers.get("Content-Type", "")

        if "multipart/form-data" not in content_type:
            _json_response(self, 400, {"error": "Upload requires multipart/form-data"})
            return

        boundary_match = re.search(r"boundary=(?:(?:\"([^\"]+)\")|([^;\s]+))", content_type)
        if not boundary_match:
            _json_response(self, 400, {"error": "Could not parse multipart boundary"})
            return
        boundary = boundary_match.group(1) or boundary_match.group(2)
        body = self._read_body()
        _, files = _parse_multipart(body, boundary)

        if not files:
            _json_response(self, 400, {"error": "No files uploaded"})
            return

        saved: list[dict[str, str]] = []
        for _, fname, fdata in files:
            suffix = Path(fname).suffix or ".png"
            filename = f"{uuid.uuid4().hex}{suffix}"
            filepath = OUTPUT_DIR / filename
            filepath.write_bytes(fdata)
            saved.append({
                "original_name": fname,
                "server_path": str(filepath.resolve()),
                "download_url": f"{_base_url(self)}/api/images/{filename}",
            })

        _json_response(self, 200, {"success": True, "files": saved})


def _resolve_storyboard_refs(data: dict) -> dict:
    """Resolve storyboard JSON reference paths — returns a copy with validated paths."""
    resolved = dict(data)
    global_refs = data.get("references", [])
    if isinstance(global_refs, list):
        validated = []
        for ref in global_refs:
            p = Path(str(ref))
            if p.exists():
                validated.append(str(p.resolve()))
            else:
                validated.append(str(ref))
        resolved["references"] = validated

    shots = data.get("shots", [])
    if isinstance(shots, list):
        resolved_shots = []
        for shot in shots:
            if isinstance(shot, dict):
                s = dict(shot)
                shot_refs = shot.get("references", [])
                if isinstance(shot_refs, list):
                    validated = []
                    for ref in shot_refs:
                        p = Path(str(ref))
                        if p.exists():
                            validated.append(str(p.resolve()))
                        else:
                            validated.append(str(ref))
                    s["references"] = validated
                resolved_shots.append(s)
        resolved["shots"] = resolved_shots
    return resolved


def serve(host: str | None = None, port: int | None = None, debug: bool | None = None) -> None:
    host = host or os.getenv("HUAIMG_HOST", DEFAULT_HOST)
    port = port or int(os.getenv("HUAIMG_PORT", str(DEFAULT_PORT)))
    if debug is not None:
        os.environ["HUAIMG_DEBUG"] = "1" if debug else "0"
    _ensure_output_dir()

    server = ThreadingHTTPServer((host, port), HuaimgHandler)
    print(f"[huaimg-api] Web UI:  http://{host}:{port}/")
    print(f"[huaimg-api] API:     http://{host}:{port}/api/")
    if _debug_enabled():
        print(f"[huaimg-api] Source:  {Path(__file__).resolve()}")
        print(f"[huaimg-api] Debug:   {os.getenv('HUAIMG_DEBUG', '0')}")
        print(f"[huaimg-api] do_POST: {inspect.getsource(HuaimgHandler.do_POST).strip().replace(chr(10), ' ')}")
    print(f"[huaimg-api] Endpoints:")
    print(f"  GET  /                 Web UI")
    print(f"  GET  /api/health       Health check")
    print(f"  GET  /api/probe        Backend probe")
    print(f"  GET  /v1/models        OpenAI-compatible model list")
    if _debug_enabled():
        print(f"  GET  /__debug/routes   Debug route info")
    print(f"  GET  /api/images/list  List generated images")
    print(f"  GET  /api/images/<f>   Download image")
    print(f"  POST /api/generate     Generate image (JSON / multipart)")
    print(f"  POST /v1/images/generations  OpenAI-compatible image generation")
    print(f"  POST /api/storyboard   Batch generation (JSON)")
    print(f"  POST /api/upload       Upload file (multipart)")
    print(f"[huaimg-api] Output: {OUTPUT_DIR.resolve()}")
    print(f"[huaimg-api] Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[huaimg-api] Shutting down...")
        server.shutdown()
