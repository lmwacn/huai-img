from __future__ import annotations

import json
from pathlib import Path

from .generator import generate_image
from .models import GenerateRequest


def run_storyboard_from_data(
    data: dict,
    output_dir: Path | None,
    mode: str,
    timeout: int,
    shot_id_prefix: str = "",
) -> list[dict]:
    """Run storyboard generation from a parsed JSON dict."""
    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("Storyboard file must contain a non-empty 'shots' array")

    global_style = data.get("global_style")
    global_references = data.get("references") or []
    if not isinstance(global_references, list):
        raise ValueError("'references' must be an array when provided")

    global_output_dir = output_dir or Path("outputs") / shot_id_prefix
    global_output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise ValueError("Each shot must be a JSON object")
        prompt = shot.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Each shot must have a non-empty string 'prompt'")

        shot_id = shot.get("id")
        if shot_id is None:
            shot_id = f"shot-{index:03d}"
        shot_style = shot.get("style") if isinstance(shot.get("style"), str) else global_style
        shot_references = list(global_references)
        extra_references = shot.get("references") or []
        if extra_references:
            if not isinstance(extra_references, list):
                raise ValueError("Shot 'references' must be an array when provided")
            shot_references.extend(extra_references)

        output_path = global_output_dir / f"{shot_id}.png"
        request = GenerateRequest(
            prompt=prompt,
            mode=mode,
            references=[Path(path) for path in shot_references],
            output=output_path,
            style=shot_style,
            timeout=timeout,
        )
        result = generate_image(request)
        result_dict = result.to_dict()
        result_dict["shot_id"] = str(shot_id)
        results.append(result_dict)

    return results


def run_storyboard(
    storyboard_file: Path,
    output_dir: Path | None,
    mode: str,
    timeout: int,
) -> list[dict]:
    data = json.loads(storyboard_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Storyboard file must be a JSON object")

    shots = data.get("shots")
    if not isinstance(shots, list) or not shots:
        raise ValueError("Storyboard file must contain a non-empty 'shots' array")

    resolved_output_dir = output_dir or Path("outputs") / storyboard_file.stem
    return run_storyboard_from_data(data, resolved_output_dir, mode, timeout, shot_id_prefix=storyboard_file.stem)
