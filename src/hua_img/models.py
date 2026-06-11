from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class GenerateRequest:
    prompt: str
    mode: str = "auto"
    references: list[Path] = field(default_factory=list)
    output: Path | None = None
    style: str | None = None
    ratio: str | None = None
    timeout: int = 600
    refine: bool = False


@dataclass(slots=True)
class GenerateResult:
    success: bool
    mode: str
    prompt: str
    references: list[str]
    output: str | None = None
    job_id: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "mode": self.mode,
            "prompt": self.prompt,
            "references": self.references,
            "output": self.output,
            "job_id": self.job_id,
            "error": self.error,
            "raw": self.raw,
        }


@dataclass(slots=True)
class ProbeResult:
    codex_available: bool
    http_available: bool
    service_url: str
    codex_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "codex_available": self.codex_available,
            "http_available": self.http_available,
            "service_url": self.service_url,
            "codex_error": self.codex_error,
        }
