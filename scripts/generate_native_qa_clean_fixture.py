#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymupdf>=1.26.7",
# ]
# ///

# ─── How to run ───
# 1. `uv run scripts/generate_native_qa_clean_fixture.py`
# 2. Or: `.venv/bin/python scripts/generate_native_qa_clean_fixture.py`
# ──────────────────

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pymupdf


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPOSITORY_ROOT / "src-tauri/resources/public_native_qa_clean_fixture.pdf"
PIPELINE_PATH = REPOSITORY_ROOT / "scripts/run_masking_pipeline.py"
SESSION_HASH_KEY_HEX = "0" * 64
CLEAN_SEGMENT_SHAPE = (("attachment", "confirmed", False, 0, 0),)
ANALYSIS_OPTIONS: dict[str, bool | float | str] = {
    "rrn": True,
    "phone": True,
    "business_reg": True,
    "name": True,
    "address": True,
    "place": True,
    "legal_party": True,
    "company": True,
    "court": True,
    "case_title": True,
    "case_number": True,
    "law_firm": True,
    "attorney": True,
    "approval_line": True,
    "region_context": True,
    "doc_meta": True,
    "email": True,
    "pdf_redaction": True,
    "custom_keywords": "",
    "extract_engine": "auto",
    "profile": "mixed",
    "output_artifacts": "pdf_safe_report",
    "display_mode": "black",
    "deidentification_policy": "token",
    "region_scope": "national",
    "custom_regions": "",
    "return_text_preview": False,
    "auto_mask_threshold": 0.85,
    "review_threshold": 0.5,
}


def write_fixture(destination: Path) -> None:
    document = pymupdf.open()
    fallback_font = pymupdf.Font(fontname="cjk")
    try:
        page = document.new_page(width=595, height=842)
        page.insert_font(fontname="qa_cjk", fontbuffer=fallback_font.buffer)
        page.insert_text((72, 48), "붙임", fontname="qa_cjk", fontsize=14)
        document.subset_fonts()
        document.save(destination, garbage=4, deflate=True)
    finally:
        document.close()


def assert_clean_manifest(path: Path) -> None:
    environment = os.environ | {
        "MASK_TOOL_ALLOWED_DIRS": str(path.parent),
        "MASKING_SESSION_HASH_KEY_HEX": SESSION_HASH_KEY_HEX,
    }
    completed = subprocess.run(
        [
            sys.executable,
            str(PIPELINE_PATH),
            "--repo-root",
            str(REPOSITORY_ROOT),
            "--mode",
            "analyze",
            "--input",
            str(path),
            "--opts",
            json.dumps(ANALYSIS_OPTIONS, separators=(",", ":")),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError("pipeline CLI rejected the clean fixture")
    payload = json.loads(completed.stdout)
    manifest = payload.get("analysis_manifest") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict):
        raise RuntimeError("pipeline CLI returned no analysis manifest")
    segments = manifest.get("segments")
    regions = manifest.get("regions")
    occurrences = manifest.get("occurrences")
    reviews = manifest.get("review_items")
    approval_coverage = manifest.get("approval_coverage")
    required_region_coverage = manifest.get("required_region_coverage")
    if (
        not isinstance(segments, list)
        or [
            (
                segment.get("kind"),
                segment.get("state"),
                segment.get("common_only"),
                segment.get("page_start"),
                segment.get("page_end"),
            )
            for segment in segments
            if isinstance(segment, dict)
        ] != list(CLEAN_SEGMENT_SHAPE)
        or not isinstance(regions, list)
        or any(region.get("state") in {"review_required", "unconfirmed"}
               for region in regions if isinstance(region, dict))
        or occurrences != []
        or not isinstance(reviews, list)
        or any(review.get("status") == "pending" for review in reviews if isinstance(review, dict))
        or not isinstance(approval_coverage, dict)
        or approval_coverage.get("state") == "indeterminate"
        or not isinstance(required_region_coverage, dict)
        or required_region_coverage.get("blocking") is not False
    ):
        raise RuntimeError("clean fixture must pass finalize precommit without redactions or reviews")


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix="native-qa-clean-", dir=OUTPUT_PATH.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        write_fixture(temporary_path)
        assert_clean_manifest(temporary_path)
        temporary_path.replace(OUTPUT_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"generated {OUTPUT_PATH.name}: finalize-precommit clean")


if __name__ == "__main__":
    main()
