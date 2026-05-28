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
        wrapper = build_wrapper_prompt(request.prompt, request.style, bool(request.references))
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

        completed = subprocess.run(
            command,
            input=wrapper,
            encoding="utf-8",
            capture_output=True,
            timeout=request.timeout,
            shell=os.name == "nt",
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
            "prompt": merge_style(request.prompt, request.style),
            "timeout_sec": request.timeout,
        }
        if request.references:
            payload["images"] = [str(path) for path in request.references]

        job = self._post_json(f"{self.service_url}/v1/images/generations", payload)
        job_id = (((job or {}).get("job") or {}).get("id"))
        if not job_id:
            raise BackendError("HTTP backend did not return a job id")

        deadline = time.time() + request.timeout
        current_job_id = str(job_id)
        while time.time() < deadline:
            time.sleep(POLL_INTERVAL_SECONDS)
            job_response = self._get_json(f"{self.service_url}/v1/jobs/{current_job_id}")
            job_data = (job_response or {}).get("job") or {}
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

    def _get_json(self, url: str) -> dict:
        request = Request(url, method="GET")
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, url: str, payload: dict[str, object]) -> dict:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))


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


def build_wrapper_prompt(prompt: str, style: str | None, has_references: bool) -> str:
    merged = merge_style(prompt, style)
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


def merge_style(prompt: str, style: str | None) -> str:
    if not style:
        return prompt
    return f"{prompt}\n\nStyle direction: {style}"


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
