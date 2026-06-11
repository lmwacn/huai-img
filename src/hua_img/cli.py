from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .backends import BackendError, probe_backends
from .generator import generate_image
from .models import GenerateRequest
from .server import serve as serve_api
from .storyboard import run_storyboard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hua-img", description="Short-film image generation CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Generate a single image")
    generate.add_argument("--prompt", help="Prompt text")
    generate.add_argument("--prompt-file", type=Path, help="Read prompt from a file")
    generate.add_argument("--image", dest="images", action="append", default=[], help="Reference image path")
    generate.add_argument("--style", help="Global style direction")
    generate.add_argument("--mode", choices=["auto", "cli", "http"], default="auto")
    generate.add_argument("--output", type=Path, help="Write output to a file")
    generate.add_argument("--timeout", type=int, default=600)
    generate.add_argument("--format", choices=["text", "json"], default="text")

    storyboard = subparsers.add_parser("storyboard", help="Generate a storyboard batch from JSON")
    storyboard.add_argument("--file", type=Path, required=True, help="Storyboard JSON file")
    storyboard.add_argument("--output-dir", type=Path, help="Output directory for shot files")
    storyboard.add_argument("--mode", choices=["auto", "cli", "http"], default="auto")
    storyboard.add_argument("--timeout", type=int, default=600)
    storyboard.add_argument("--format", choices=["text", "json"], default="text")

    probe = subparsers.add_parser("probe", help="Check backend availability")
    probe.add_argument("--format", choices=["text", "json"], default="text")

    serve_parser = subparsers.add_parser("serve", help="Start LAN API server")
    serve_parser.add_argument("--host", default=None, help=f"Bind address (default: 0.0.0.0)")
    serve_parser.add_argument("--port", type=int, default=None, help="Port (default: 9527)")
    serve_parser.add_argument("--debug", action="store_true", help="Enable verbose API/backend debug logs")

    return parser


def read_prompt(prompt: str | None, prompt_file: Path | None) -> str:
    if prompt_file:
        return prompt_file.read_text(encoding="utf-8").strip()
    if prompt:
        return prompt.strip()
    raise ValueError("Either --prompt or --prompt-file is required")


def emit(payload: object, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if isinstance(payload, dict):
        for key, value in payload.items():
            print(f"{key}: {value}")
        return

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                print("-" * 40)
                for key, value in item.items():
                    print(f"{key}: {value}")
            else:
                print(item)
        return

    print(payload)


def handle_generate(args: argparse.Namespace) -> int:
    prompt = read_prompt(args.prompt, args.prompt_file)
    request = GenerateRequest(
        prompt=prompt,
        mode=args.mode,
        references=[Path(path) for path in args.images],
        output=args.output,
        style=args.style,
        timeout=args.timeout,
    )
    result = generate_image(request)
    emit(result.to_dict(), args.format)
    return 0


def handle_storyboard(args: argparse.Namespace) -> int:
    results = run_storyboard(
        storyboard_file=args.file,
        output_dir=args.output_dir,
        mode=args.mode,
        timeout=args.timeout,
    )
    emit(results, args.format)
    return 0


def handle_probe(args: argparse.Namespace) -> int:
    result = probe_backends()
    emit(result.to_dict(), args.format)
    return 0


def _ensure_utf8_stdio() -> None:
    """Force stdout/stderr to UTF-8 on Windows so Chinese displays correctly."""
    if os.name != "nt":
        return
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "generate":
            return handle_generate(args)
        if args.command == "storyboard":
            return handle_storyboard(args)
        if args.command == "probe":
            return handle_probe(args)
        if args.command == "serve":
            serve_api(host=args.host, port=args.port, debug=args.debug)
            return 0
        parser.error(f"Unknown command: {args.command}")
        return 2
    except (ValueError, BackendError, OSError) as exc:
        if getattr(args, "format", "text") == "json":
            print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
