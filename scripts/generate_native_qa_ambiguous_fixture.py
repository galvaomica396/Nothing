#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymupdf>=1.26.7",
# ]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly (no venv, no pip install needed):
#      uv run scripts/generate_native_qa_ambiguous_fixture.py
# 3. Or run in this repository's existing environment:
#      .venv/bin/python scripts/generate_native_qa_ambiguous_fixture.py
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
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.generate_native_qa_fixture import stabilize_pdf_id


OUTPUT_PATH = (
    REPOSITORY_ROOT
    / "src-tauri/resources/public_native_qa_ambiguous_fixture.pdf"
)
PIPELINE_PATH = REPOSITORY_ROOT / "scripts/run_masking_pipeline.py"
SESSION_HASH_KEY_HEX = "0" * 64
AMBIGUOUS_SEGMENT_SHAPE = (("unknown", "review_required", True, 0, 1),)
AMBIGUOUS_OCCURRENCE_COUNT = 3
AMBIGUOUS_PENDING_REVIEW_COUNT = 1
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
        body_pages = (
            (
                "본문에는 검토 담당자 김민준과 시행 문구가 함께 있으나 표제 줄은 없습니다.",
                "연락처 010-1234-5678 및 이메일 qa.ambiguous@example.go.kr",
            ),
            (
                "본문 참고: 수신 기관과 승인 검토 절차를 함께 기록한 연속 문장입니다.",
                "담당자 이서연 / 전화 02-2094-2153",
            ),
        )
        for lines in body_pages:
            page = document.new_page(width=595, height=842)
            page.insert_font(fontname="qa_cjk", fontbuffer=fallback_font.buffer)
            page.insert_text((72, 250), lines[0], fontname="qa_cjk", fontsize=12)
            page.insert_text((72, 292), lines[1], fontname="qa_cjk", fontsize=12)
        document.subset_fonts()
        document.save(destination, garbage=4, deflate=True, reproducible=True)
        stabilize_pdf_id(destination)
    finally:
        document.close()


def assert_text_native(path: Path) -> None:
    document = pymupdf.open(path)
    try:
        if document.page_count != 2 or any(not page.get_text().strip() for page in document):
            raise RuntimeError("fixture must contain text-native content on two pages")
    finally:
        document.close()


def pipeline_manifest(path: Path) -> dict[str, object]:
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
        raise RuntimeError("pipeline CLI rejected the ambiguous fixture")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise RuntimeError("pipeline CLI returned an invalid envelope")
    manifest = payload.get("analysis_manifest")
    if not isinstance(manifest, dict):
        raise RuntimeError("pipeline CLI returned no analysis manifest")
    return manifest


def assert_ambiguous_common_only(manifest: dict[str, object]) -> int:
    segments = manifest.get("segments")
    occurrences = manifest.get("occurrences")
    reviews = manifest.get("review_items")
    if not isinstance(segments, list) or not isinstance(occurrences, list) or not isinstance(reviews, list):
        raise RuntimeError("ambiguous manifest has invalid collections")
    segment_values = [segment for segment in segments if isinstance(segment, dict)]
    segment_shape = tuple(
        (
            segment.get("kind"),
            segment.get("state"),
            segment.get("common_only"),
            segment.get("page_start"),
            segment.get("page_end"),
        )
        for segment in segment_values
    )
    if segment_shape != AMBIGUOUS_SEGMENT_SHAPE:
        raise RuntimeError(
            "ambiguous fixture segment shape drifted from native_qa full(): "
            f"expected {AMBIGUOUS_SEGMENT_SHAPE!r}, got {segment_shape!r}"
        )
    ambiguous_reviews = [
        review
        for review in reviews
        if isinstance(review, dict)
        and review.get("status") == "pending"
        and review.get("kind") == "acknowledge"
        and review.get("common_only") is True
        and review.get("requires_acknowledgment") is True
        and isinstance(review.get("reason_codes"), list)
        and "ambiguous_boundary" in review["reason_codes"]
    ]
    if not ambiguous_reviews:
        raise RuntimeError("ambiguous fixture must retain its pending common-only boundary acknowledgement")
    pending_reviews = [
        review for review in reviews
        if isinstance(review, dict) and review.get("status") == "pending"
    ]
    if (
        len(occurrences) != AMBIGUOUS_OCCURRENCE_COUNT
        or len(pending_reviews) != AMBIGUOUS_PENDING_REVIEW_COUNT
    ):
        raise RuntimeError("ambiguous fixture semantic counts drifted from native_qa full()")
    return len(ambiguous_reviews)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix="native-qa-ambiguous-", dir=OUTPUT_PATH.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        write_fixture(temporary_path)
        assert_text_native(temporary_path)
        review_count = assert_ambiguous_common_only(pipeline_manifest(temporary_path))
        temporary_path.replace(OUTPUT_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"generated {OUTPUT_PATH.name}: {review_count} ambiguous common-only acknowledgement(s)")


if __name__ == "__main__":
    main()
