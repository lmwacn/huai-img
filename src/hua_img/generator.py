from __future__ import annotations

from .backends import BackendError, CliBackend, HttpBackend, refine_prompt, service_url_from_env
from .models import GenerateRequest, GenerateResult


def generate_image(request: GenerateRequest) -> GenerateResult:
    # Refine prompt if requested
    if request.refine:
        refined = refine_prompt(
            prompt=request.prompt,
            references=request.references,
            style=request.style,
            ratio=request.ratio,
        )
        if refined != request.prompt:
            request.prompt = refined

    mode = request.mode.lower()
    service_url = service_url_from_env()
    http_backend = HttpBackend(service_url)
    cli_backend = CliBackend()

    if mode == "http":
        return http_backend.generate(request)
    if mode == "cli":
        return cli_backend.generate(request)
    if mode != "auto":
        raise BackendError(f"Unsupported mode: {request.mode}")

    if http_backend.is_available():
        return http_backend.generate(request)
    return cli_backend.generate(request)
