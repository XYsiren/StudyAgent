"""Run real, small model adapter checks without touching application data or credentials.

Run from any directory with the cook-rag-1 Python interpreter. The default tries
Hugging Face's existing cache only; --allow-download permits normal model retrieval.
It writes a sanitized JSON report and an ignored, synthetic image fixture.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
REPORT = ROOT / "docs" / "artifacts" / "model-adapters-2026-10-16.json"
FIXTURE = ROOT / "artifacts" / "model-adapters-2026-10-16" / "vision-policy.png"
CROSS_ENCODER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
FIXTURE_LINES = ["Travel policy", "Hotel limit: CNY 500", "Submit within 30 days"]


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def safe_error(exc: Exception) -> dict:
    """Never serialize settings, connection strings, credentials, or a raw traceback."""
    message = str(exc)
    try:
        from app.config import get_settings

        settings = get_settings()
        for name in type(settings).model_fields:
            if any(part in name for part in ("key", "secret", "password", "database_url", "redis_url")):
                value = getattr(settings, name, None)
                if isinstance(value, str) and value:
                    message = message.replace(value, "[redacted]")
    except Exception:
        message = "Model adapter validation failed; inspect local service logs."
    message = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", message)
    message = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[redacted]", message)
    return {"error_type": type(exc).__name__, "error": message[:1000]}


def validate_cross_encoder(allow_download: bool) -> dict:
    from sentence_transformers import CrossEncoder

    pairs = [
        ["What is the hotel reimbursement limit?", "Hotel expenses are reimbursed up to CNY 500 per night."],
        ["What is the hotel reimbursement limit?", "Employees can request annual leave three working days in advance."],
    ]
    began = time.perf_counter()
    source = "existing-cache"
    try:
        model = CrossEncoder(CROSS_ENCODER, device="cpu", trust_remote_code=False, local_files_only=True)
    except OSError:
        if not allow_download:
            raise
        source = "normal-hugging-face-download-or-cache"
        model = CrossEncoder(CROSS_ENCODER, device="cpu", trust_remote_code=False)
    values = [float(value) for value in model.predict(pairs, show_progress_bar=False)]
    finite = len(values) == len(pairs) and all(math.isfinite(value) for value in values)
    return {
        "status": "passed" if finite else "failed",
        "model": CROSS_ENCODER,
        "device": "cpu",
        "source": source,
        "pairs": pairs,
        "scores": values,
        "checks": {"two_scores": len(values) == 2, "all_finite": finite,
                   "related_score_above_unrelated": len(values) == 2 and values[0] > values[1]},
        "duration_seconds": round(time.perf_counter() - began, 3),
        "scope": "Real CrossEncoder load and two-pair predict; not a retrieval-quality benchmark.",
    }


def make_fixture() -> str:
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for candidate in (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/segoeui.ttf"),
                      Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if candidate.is_file():
            font = ImageFont.truetype(str(candidate), 52)
            break
    font = font or ImageFont.load_default(size=52)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (1400, 500), "white") as image:
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(FIXTURE_LINES):
            draw.text((70, 65 + index * 125), line, fill="black", font=font)
        image.save(FIXTURE, format="PNG")
    return FIXTURE.relative_to(ROOT).as_posix()


def validate_vision() -> dict:
    from app.config import get_settings
    from app.services.parsing import parse_document

    settings = get_settings()
    fixture = make_fixture()
    if not settings.vision_model:
        raise ValueError("RAG_VISION_MODEL is not configured; a real vision check cannot run.")
    if settings.ocr_enabled:
        raise ValueError("Disable RAG_OCR_ENABLED for this isolated vision-model test.")
    began = time.perf_counter()
    pages = parse_document(FIXTURE, FIXTURE.name)
    extracted = "\n\n".join(page.text for page in pages)
    checks = {"one_image_page": len(pages) == 1,
              "contains_500": bool(re.search(r"(?<!\d)500(?!\d)", extracted)),
              "contains_30": bool(re.search(r"(?<!\d)30(?!\d)", extracted))}
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "provider": settings.llm_provider,
        "model": settings.vision_model,
        "entrypoint": "app.services.parsing.parse_document",
        "fixture_path": fixture,
        "fixture_text": FIXTURE_LINES,
        "ocr_enabled": settings.ocr_enabled,
        "extracted_text": extracted,
        "checks": checks,
        "duration_seconds": round(time.perf_counter() - began, 3),
        "scope": "Synthetic black-on-white English image only; not a Chinese OCR or scanned-document benchmark.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--only", choices=("all", "cross-encoder", "vision"), default="all")
    args = parser.parse_args()
    sys.path.insert(0, str(BACKEND))
    os.chdir(BACKEND)
    report = {}
    if REPORT.is_file():
        report = json.loads(REPORT.read_text(encoding="utf-8"))
    report.update({
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "environment": "cook-rag-1",
        "python_version": sys.version.split()[0],
        "packages": {name: package_version(name) for name in
                     ("sentence-transformers", "torch", "transformers", "langchain-ollama", "Pillow")},
        "credentials_or_connection_strings_recorded": False,
        "limitations": [
            "These are real adapter smoke tests, not quality, latency, or cost-improvement benchmarks.",
            "No API upload, production data, code_c8 files, or application service source is modified.",
            "The synthetic PNG is under ignored artifacts/; this JSON is the reproducible evidence record.",
        ],
    })
    tasks = {"cross_encoder": lambda: validate_cross_encoder(args.allow_download), "vision": validate_vision}
    selected = {"cross-encoder": ["cross_encoder"], "vision": ["vision"], "all": list(tasks)}[args.only]
    for name in selected:
        began = time.perf_counter()
        try:
            report[name] = tasks[name]()
        except Exception as exc:
            report[name] = {"status": "unavailable", **safe_error(exc),
                            "duration_seconds": round(time.perf_counter() - began, 3)}
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"check": name, "status": report[name]["status"],
                          "duration_seconds": report[name]["duration_seconds"]}, ensure_ascii=False), flush=True)
    return 0 if all(report[name]["status"] == "passed" for name in selected) else 1


if __name__ == "__main__":
    raise SystemExit(main())
