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
#      uv run scripts/generate_native_qa_fixture.py
# 3. Or run in this repository's existing environment:
#      .venv/bin/python scripts/generate_native_qa_fixture.py
# ──────────────────

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import pymupdf


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPOSITORY_ROOT / "src-tauri/resources/public_native_qa_fixture.pdf"
PIPELINE_PATH = REPOSITORY_ROOT / "scripts/run_masking_pipeline.py"
SESSION_HASH_KEY_HEX = "0" * 64
INTERNAL_REVIEW_TITLE = "「○○ 사업」 공사기간 연장 검토보고"
NON_PERSON_NAME_VALUE = "공사기간"
OFFICIAL_DISPATCH_OCCURRENCE_COUNT = 21
OFFICIAL_DISPATCH_PENDING_REVIEW_COUNT = 12
MIXED_OCCURRENCE_COUNT = 21
MIXED_PENDING_REVIEW_COUNT = 12
PDF_ID_VALUE_PATTERN = rb"(?:<[^>]*>|\((?:\\.|[^\\)])*\))"
PDF_TRAILER_ID_PATTERN = re.compile(
    rb"/ID\s*\[\s*" + PDF_ID_VALUE_PATTERN + rb"\s*" + PDF_ID_VALUE_PATTERN + rb"\s*\]"
)
LEGAL_TAGS = frozenset(
    {"CASE_NUMBER", "LEGAL_PARTY", "COURT", "CASE_TITLE", "LAW_FIRM", "ATTORNEY"}
)
MIXED_SEGMENT_SHAPE = (
    ("internal_review", "confirmed", False, 0, 1),
    ("official_dispatch", "confirmed", False, 2, 2),
)
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


def write_page(
    document: pymupdf.Document, lines: tuple[tuple[str, int], ...], font_buffer: bytes
) -> None:
    page = document.new_page(width=595, height=842)
    page.insert_font(fontname="qa_cjk", fontbuffer=font_buffer)
    for index, (line, font_size) in enumerate(lines):
        page.insert_text((60, 70 + index * 34), line, fontname="qa_cjk", fontsize=font_size)


def stabilize_pdf_id(destination: Path) -> None:
    raw_pdf = destination.read_bytes()
    placeholder = b"/ID[<00000000000000000000000000000000><00000000000000000000000000000000>]"
    normalized_pdf, replacements = PDF_TRAILER_ID_PATTERN.subn(placeholder, raw_pdf)
    if replacements != 1:
        raise RuntimeError("fixture PDF must contain exactly one trailer identifier")
    digest = hashlib.sha256(normalized_pdf).hexdigest().upper().encode()
    stable_id = b"/ID[<" + digest[:32] + b"><" + digest[32:] + b">]"
    destination.write_bytes(PDF_TRAILER_ID_PATTERN.sub(stable_id, raw_pdf))


def write_fixture(destination: Path) -> None:
    document = pymupdf.open()
    fallback_font = pymupdf.Font(fontname="cjk")
    try:
        write_page(
            document,
            (
                (INTERNAL_REVIEW_TITLE, 16),
                ("결재 검토 승인", 12),
                ("담당 김민준 검토 이서연 승인 박지훈", 12),
                ("연락처 010-1234-5678", 12),
                ("이메일 qa.public@example.go.kr", 12),
                ("주소 서울특별시 중구 세종대로 110", 12),
                ("공통 이름 김민준 담당자 공사기간", 12),
            ),
            fallback_font.buffer,
        )
        page = document.new_page(width=595, height=842)
        page.insert_font(fontname="qa_cjk", fontbuffer=fallback_font.buffer)
        running_title_size = 20
        running_title_width = fallback_font.text_length(
            INTERNAL_REVIEW_TITLE, fontsize=running_title_size
        )
        page.insert_text(
            ((595 - running_title_width) / 2, 48),
            INTERNAL_REVIEW_TITLE,
            fontname="qa_cjk",
            fontsize=running_title_size,
        )
        page.insert_text((60, 112), "본문 담당자 김민준", fontname="qa_cjk", fontsize=12)
        page.insert_text((60, 146), "전화 02-1234-5678", fontname="qa_cjk", fontsize=12)
        write_page(
            document,
            (
                ("서울특별시 중구", 12),
                ("수신 중구청장", 12),
                ("참조 행정지원과", 12),
                ("제목 회의 결과 알림", 12),
                ("담당 김민준 전화 02-2094-2153", 12),
                ("시행 중구의회-3952", 12),
            ),
            fallback_font.buffer,
        )
        document.subset_fonts()
        document.save(destination, garbage=4, deflate=True, reproducible=True)
        stabilize_pdf_id(destination)
    finally:
        document.close()


def assert_text_native(path: Path) -> None:
    document = pymupdf.open(path)
    try:
        page_text = [page.get_text().strip() for page in document]
    finally:
        document.close()
    if len(page_text) != 3 or any(not text for text in page_text):
        raise RuntimeError("fixture must contain text-native content on three pages")
    if INTERNAL_REVIEW_TITLE not in page_text[0]:
        raise RuntimeError("fixture is missing the internal-review title")


def pipeline_manifest(path: Path, profile: str) -> dict[str, object]:
    environment = os.environ | {
        "MASK_TOOL_ALLOWED_DIRS": str(path.parent),
        "MASKING_SESSION_HASH_KEY_HEX": SESSION_HASH_KEY_HEX,
    }
    options = ANALYSIS_OPTIONS | {"profile": profile}
    if profile == "official_dispatch":
        options["profile_authority"] = {
            "document_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "analysis_revision": 1,
            "profile": profile,
            "decision_code": "profile_confirmed",
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
            json.dumps(options, separators=(",", ":")),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pipeline CLI rejected the {profile} fixture")
    payload = json.loads(completed.stdout)
    manifest = payload.get("analysis_manifest") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict):
        raise RuntimeError("pipeline CLI returned no analysis manifest")
    return manifest


def assert_mixed_manifest(manifest: dict[str, object]) -> tuple[int, int]:
    segments = manifest.get("segments")
    occurrences = manifest.get("occurrences")
    reviews = manifest.get("review_items")
    if not isinstance(segments, list) or not isinstance(occurrences, list) or not isinstance(reviews, list):
        raise RuntimeError("mixed manifest has invalid collections")
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
    if segment_shape != MIXED_SEGMENT_SHAPE:
        raise RuntimeError(
            "mixed fixture segment shape drifted from native_qa full(): "
            f"expected {MIXED_SEGMENT_SHAPE!r}, got {segment_shape!r}"
        )
    boundary_reviews = [
        review
        for review in reviews
        if isinstance(review, dict)
        and review.get("status") == "pending"
        and review.get("kind") in {"boundary", "region_geometry"}
    ]
    legal_occurrences = [
        occurrence
        for occurrence in occurrences
        if isinstance(occurrence, dict) and occurrence.get("tag") in LEGAL_TAGS
    ]
    if not boundary_reviews:
        raise RuntimeError("mixed fixture must retain a pending boundary or region-geometry review")
    if legal_occurrences:
        raise RuntimeError("mixed fixture must not emit legal-profile occurrences")
    pending_reviews = [
        review for review in reviews
        if isinstance(review, dict) and review.get("status") == "pending"
    ]
    if (
        len(occurrences) != MIXED_OCCURRENCE_COUNT
        or len(pending_reviews) != MIXED_PENDING_REVIEW_COUNT
    ):
        raise RuntimeError("mixed fixture semantic counts drifted from native_qa full()")
    return len(occurrences), len(boundary_reviews)


def assert_official_dispatch_manifest(manifest: dict[str, object]) -> int:
    occurrences = manifest.get("occurrences")
    reviews = manifest.get("review_items")
    if not isinstance(occurrences, list) or not isinstance(reviews, list):
        raise RuntimeError("official-dispatch manifest has invalid collections")
    pending_reviews = [
        review for review in reviews
        if isinstance(review, dict) and review.get("status") == "pending"
    ]
    has_profile_layout = any(
        isinstance(occurrence, dict)
        and occurrence.get("source") == "profile_layout"
        for occurrence in occurrences
    )
    kinds = {review.get("kind") for review in pending_reviews}
    has_authority_missing = any(
        isinstance(review.get("reason_codes"), list)
        and "profile_authority_missing" in review["reason_codes"]
        for review in pending_reviews
    )
    non_person_hash = hashlib.sha256(NON_PERSON_NAME_VALUE.encode()).hexdigest()
    has_non_person_candidate = any(
        isinstance(occurrence, dict)
        and occurrence.get("value_hash") == non_person_hash
        for occurrence in occurrences
    )
    if (
        len(occurrences) != OFFICIAL_DISPATCH_OCCURRENCE_COUNT
        or len(pending_reviews) != OFFICIAL_DISPATCH_PENDING_REVIEW_COUNT
        or not has_profile_layout
        or not {"name", "institution"}.issubset(kinds)
        or "acknowledge" in kinds
        or has_authority_missing
        or has_non_person_candidate
    ):
        raise RuntimeError("official-dispatch fixture must preserve plumbing() semantics")
    return len(occurrences)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix="native-qa-mixed-", dir=OUTPUT_PATH.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        write_fixture(temporary_path)
        assert_text_native(temporary_path)
        mixed_occurrences, boundary_reviews = assert_mixed_manifest(
            pipeline_manifest(temporary_path, "mixed")
        )
        official_occurrences = assert_official_dispatch_manifest(
            pipeline_manifest(temporary_path, "official_dispatch")
        )
        temporary_path.replace(OUTPUT_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(
        f"generated {OUTPUT_PATH.name}: {mixed_occurrences} mixed occurrence(s), "
        f"{boundary_reviews} pending boundary review(s), "
        f"{official_occurrences} official-dispatch occurrence(s)"
    )


if __name__ == "__main__":
    main()
