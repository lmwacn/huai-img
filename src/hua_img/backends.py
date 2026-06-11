from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import GenerateRequest, GenerateResult, ProbeResult

DEFAULT_PORT = 4312
POLL_INTERVAL_SECONDS = 3


class BackendError(RuntimeError):
    pass


def _debug(message: str) -> None:
    if os.getenv("HUA_IMG_DEBUG", "0").lower() not in ("0", "false", "no", "off"):
        print(f"[hua-img-backend] {message}", flush=True)


def _clean_codex_error(output: str) -> str:
    """Extract the final meaningful error from codex output, removing noisy repeated lines."""
    lines = output.strip().splitlines()
    # Find lines that look like actual errors (start with "ERROR:" after noise)
    error_lines = [line for line in lines if line.strip().startswith("ERROR:")]
    if error_lines:
        # Return the last unique error message
        last_error = error_lines[-1].strip()
        # Remove "ERROR: " prefix for cleaner display
        if last_error.startswith("ERROR: "):
            return last_error[7:]
        return last_error
    # Fallback: return last 3 non-empty lines
    non_empty = [line for line in lines if line.strip()]
    return "\n".join(non_empty[-3:]) if non_empty else "codex exec failed"


class CliBackend:
    def is_available(self) -> tuple[bool, str | None]:
        executable = resolve_codex_executable()
        if executable:
            return True, None
        return False, "codex command not found"

    def generate(self, request: GenerateRequest) -> GenerateResult:
        executable = resolve_codex_executable()
        if not executable:
            raise BackendError("codex command not found")

        before_mtime = latest_generated_image_mtime()
        wrapper = build_wrapper_prompt(request.prompt, request.style, bool(request.references), request.ratio)
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
        ]
        for image in request.references:
            command.extend(["--image", str(image)])
        command.append("-")

        try:
            completed = subprocess.run(
                command,
                input=wrapper,
                encoding="utf-8",
                capture_output=True,
                timeout=request.timeout,
                shell=os.name == "nt",
            )
        except subprocess.TimeoutExpired:
            raise BackendError(
                f"Image generation timed out after {request.timeout}s. "
                "Try increasing the timeout or use mode='http' if available."
            )
        if completed.returncode != 0:
            raw = completed.stderr.strip() or completed.stdout.strip() or "codex exec failed"
            raise BackendError(_clean_codex_error(raw))

        output_text = completed.stdout.strip()
        generated_image = find_latest_generated_image(after_mtime=before_mtime)

        final_output: Path | None = None
        if generated_image:
            final_output = persist_generated_image(generated_image, request.output)

        return GenerateResult(
            success=True,
            mode="cli",
            prompt=request.prompt,
            references=[str(path) for path in request.references],
            output=str(final_output) if final_output else str(generated_image) if generated_image else None,
            raw={
                "stdout": output_text,
                "detected_image": str(generated_image) if generated_image else None,
            },
        )


class HttpBackend:
    def __init__(self, service_url: str) -> None:
        self.service_url = service_url.rstrip("/")

    def is_available(self) -> bool:
        try:
            with urlopen(f"{self.service_url}/health", timeout=3) as response:
                return 200 <= response.status < 300
        except (URLError, HTTPError):
            return False

    def generate(self, request: GenerateRequest) -> GenerateResult:
        payload: dict[str, object] = {
            "prompt": merge_style(request.prompt, request.style, request.ratio),
            "timeout_sec": request.timeout,
        }
        if request.references:
            payload["images"] = [str(path) for path in request.references]

        _debug(f"POST {self.service_url}/v1/images/generations payload={_format_backend_payload(payload)}")
        job = self._post_json(f"{self.service_url}/v1/images/generations", payload)
        _debug(f"generation response type={type(job).__name__} body={_format_backend_payload(job)}")
        job_id = _extract_job_id(job)
        if not job_id:
            raise BackendError(f"HTTP backend did not return a job id: {_format_backend_payload(job)}")

        deadline = time.time() + request.timeout
        current_job_id = str(job_id)
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            job_response = self._get_json(f"{self.service_url}/v1/jobs/{current_job_id}")
            _debug(f"GET {self.service_url}/v1/jobs/{current_job_id} type={type(job_response).__name__} body={_format_backend_payload(job_response)}")
            if not isinstance(job_response, dict):
                raise BackendError(f"HTTP backend returned an invalid job payload: {_format_backend_payload(job_response)}")
            job_data = job_response.get("job") or {}
            if not isinstance(job_data, dict):
                raise BackendError(f"HTTP backend returned an invalid job payload: {_format_backend_payload(job_response)}")
            status = job_data.get("status")

            if status == "completed":
                images = job_data.get("images") or []
                first_path = None
                if images and isinstance(images, list):
                    first = images[0]
                    if isinstance(first, dict):
                        first_path = first.get("path")
                    elif isinstance(first, str):
                        first_path = first
                return GenerateResult(
                    success=True,
                    mode="http",
                    prompt=request.prompt,
                    references=[str(path) for path in request.references],
                    output=str(first_path) if first_path else None,
                    job_id=current_job_id,
                    raw=job_response,
                )

            if status == "failed":
                raise BackendError(json.dumps(job_response, ensure_ascii=False))

            if status == "promoted":
                replacement = job_data.get("replacementJobId")
                if replacement:
                    current_job_id = str(replacement)

        raise BackendError(f"HTTP generation timed out after {request.timeout} seconds")

    def _get_json(self, url: str) -> object:
        request = Request(url, method="GET")
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, url: str, payload: dict[str, object]) -> object:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))


def _extract_job_id(response: object) -> str | None:
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return None

    job = response.get("job")
    if isinstance(job, dict):
        job_id = job.get("id")
        return str(job_id) if job_id else None
    if isinstance(job, str):
        return job

    job_id = response.get("id")
    return str(job_id) if job_id else None


def _format_backend_payload(payload: object) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False)
    except TypeError:
        return str(payload)


def service_url_from_env() -> str:
    port = os.getenv("CODEX_IMAGEGEN_PORT", str(DEFAULT_PORT))
    return f"http://127.0.0.1:{port}"


def probe_backends() -> ProbeResult:
    service_url = service_url_from_env()
    cli = CliBackend()
    codex_available, codex_error = cli.is_available()
    http_available = HttpBackend(service_url).is_available()
    return ProbeResult(
        codex_available=codex_available,
        http_available=http_available,
        service_url=service_url,
        codex_error=codex_error,
    )


def _is_chinese(text: str) -> bool:
    """Check if text contains Chinese characters."""
    for ch in text:
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
            return True
    return False


def build_wrapper_prompt(prompt: str, style: str | None, has_references: bool, ratio: str | None = None) -> str:
    merged = merge_style(prompt, style, ratio)
    if _is_chinese(merged):
        instructions = f"使用 imagegen 按以下请求生成一张图片：\n{merged}\n\n要求：\n- 直接生成图片\n- 不要解释\n- 只返回图片结果"
        if has_references:
            instructions += "\n- 参考已提供的参考图以保持一致性"
        return instructions

    if has_references:
        return (
            "Use imagegen to create an image with this request:\n"
            f"{merged}\n\n"
            "Reference image(s) are attached. Use them for consistency.\n"
            "Requirements:\n"
            "- Generate the image directly\n"
            "- Do not provide explanation\n"
            "- Return only the image result"
        )
    return (
        "Use imagegen to create an image with this request:\n"
        f"{merged}\n\n"
        "Requirements:\n"
        "- Generate the image directly\n"
        "- Do not provide explanation\n"
        "- Return only the image result"
    )


def merge_style(prompt: str, style: str | None, ratio: str | None = None) -> str:
    parts = [prompt]
    if ratio:
        parts.append(f"Aspect ratio: {ratio}")
    if style:
        parts.append(f"Style direction: {style}")
    if len(parts) == 1:
        return prompt
    return "\n\n".join(parts)


def codex_generated_images_dir() -> Path:
    return Path.home() / ".codex" / "generated_images"


def latest_generated_image_mtime() -> float:
    base = codex_generated_images_dir()
    if not base.exists():
        return 0.0

    mtimes = [path.stat().st_mtime for path in base.rglob("*.png") if path.is_file()]
    return max(mtimes, default=0.0)


def find_latest_generated_image(after_mtime: float) -> Path | None:
    base = codex_generated_images_dir()
    if not base.exists():
        return None

    candidates = [
        path for path in base.rglob("*.png")
        if path.is_file() and path.stat().st_mtime >= after_mtime
    ]
    if not candidates:
        return None

    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def persist_generated_image(source: Path, output: Path | None) -> Path:
    if output is None:
        return source

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, output)
    return output


def resolve_codex_executable() -> str | None:
    for candidate in ("codex", "codex.cmd", "codex.CMD", "codex.exe"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def refine_prompt(prompt: str, references: list[Path], style: str | None = None, ratio: str | None = None, timeout: int = 180) -> str:
    """Use Codex to search, analyze, and enhance the user's prompt before image generation."""
    executable = resolve_codex_executable()
    if not executable:
        _debug("codex not available, skipping refine")
        return prompt

    # Build the refinement prompt
    context_parts = []
    if style:
        context_parts.append(f"风格要求: {style}")
    if ratio:
        context_parts.append(f"画面比例: {ratio}")
    context_info = "\n".join(context_parts) if context_parts else ""

    has_refs = len(references) > 0
    ref_instruction = ""
    if has_refs:
        ref_instruction = """
参考图已附上，请仔细分析图中的：
- 视觉风格（画风、色调、光影）
- 构图特点（视角、布局、景深）
- 主体元素（人物姿态、表情、服装）
- 环境氛围（背景、天气、时间）
将这些特征融入到优化后的提示词中。"""

    refine_instruction = f"""你是一个专业的封面设计提示词工程师，擅长为车评、科技/AI工具、自媒体等内容生成高质量封面图提示词。

用户原始描述：{prompt}
{context_info}
{ref_instruction}

请按以下步骤执行：

第一步：分析与搜索
1. 识别内容类型（车评、AI工具、科技评测、生活分享等）
2. 搜索并补充专业信息：
   - 车评类：搜索该车型的设计语言、外观特征（前脸、腰线、轮毂）、品牌配色、目标受众审美
   - AI/科技类：搜索科技感视觉元素（光效、粒子、线条、全息投影）、极简/未来感设计趋势
   - 其他类：搜索该领域的视觉风格、配色方案、流行构图

第二步：封面设计优化
1. 构图原则：确保画面留有标题文字空间（通常上方或左侧）
2. 视觉层次：主体突出，背景简洁但有氛围感
3. 吸引力法则：使用对比色、光影焦点、视觉引导线
4. 平台适配：适合缩略图展示，主体清晰可辨

第三步：输出规则
1. 保留用户描述中的所有文案内容（引号内的文字、标题、数字等），一字不改
2. 只润色视觉描述部分（构图、风格、光影、氛围、配色等）
3. 输出中文，控制在 100-200 字之间
4. 不要有任何解释、前缀或编号，只输出提示词本身"""

    command = [
        executable,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "read-only",
        "--color",
        "never",
    ]
    for ref in references:
        command.extend(["--image", str(ref)])
    command.append("-")

    _debug(f"refine_prompt: calling codex with {len(references)} reference(s)")
    try:
        completed = subprocess.run(
            command,
            input=refine_instruction,
            encoding="utf-8",
            capture_output=True,
            timeout=timeout,
            shell=os.name == "nt",
        )
        if completed.returncode != 0:
            _debug(f"refine_prompt failed: {completed.stderr[:200]}")
            return prompt

        refined = completed.stdout.strip()
        if not refined or len(refined) < 10:
            _debug("refine_prompt: output too short, using original")
            return prompt

        _debug(f"refine_prompt success: {refined[:100]}...")
        return refined

    except (subprocess.TimeoutExpired, Exception) as e:
        _debug(f"refine_prompt error: {e}")
        return prompt
