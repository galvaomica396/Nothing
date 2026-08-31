#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymupdf>=1.26.7",
# ]
# ///

# ─── How to run ───
# 1. `uv run scripts/generate_native_qa_manual_fixture.py`
# 2. Or: `.venv/bin/python scripts/generate_native_qa_manual_fixture.py`
# ──────────────────

from __future__ import annotations

from pathlib import Path
import sys
import tempfile

import pymupdf

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.generate_native_qa_fixture import (
    NON_PERSON_NAME_VALUE,
    assert_official_dispatch_manifest,
    pipeline_manifest as official_dispatch_pipeline_manifest,
    stabilize_pdf_id,
    write_page,
)


OUTPUT_PATH = REPOSITORY_ROOT / "src-tauri/resources/public_native_qa_manual_fixture.pdf"
MANUAL_REVIEW_TITLE = "내부검토보고서"


def write_fixture(destination: Path) -> None:
    document = pymupdf.open()
    fallback_font = pymupdf.Font(fontname="cjk")
    try:
        metadata = document.metadata
        metadata["subject"] = "native-qa-manual-combined"
        document.set_metadata(metadata)
        write_page(
            document,
            (
                (MANUAL_REVIEW_TITLE, 16),
                ("결재 검토 승인", 12),
                ("담당 김민준 검토 이서연 승인 박지훈", 12),
                ("연락처 010-1234-5678", 12),
                ("이메일 qa.public@example.go.kr", 12),
                ("주소 서울특별시 중구 세종대로 110", 12),
                (f"공통 이름 김민준 담당자 {NON_PERSON_NAME_VALUE}", 12),
            ),
            fallback_font.buffer,
        )
        page = document.new_page(width=595, height=842)
        page.insert_font(fontname="qa_cjk", fontbuffer=fallback_font.buffer)
        page.insert_text((60, 70), f"{MANUAL_REVIEW_TITLE} 계속", fontname="qa_cjk", fontsize=16)
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
        raise RuntimeError("manual fixture must contain text-native content on three pages")
    if MANUAL_REVIEW_TITLE not in page_text[0]:
        raise RuntimeError("manual fixture is missing its internal-review title")


def assert_manual_official_dispatch_manifest(manifest: dict[str, object]) -> int:
    return assert_official_dispatch_manifest(manifest)


def pipeline_manifest(path: Path) -> dict[str, object]:
    return official_dispatch_pipeline_manifest(path, "official_dispatch")


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix="native-qa-manual-", dir=OUTPUT_PATH.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        write_fixture(temporary_path)
        assert_text_native(temporary_path)
        occurrence_count = assert_manual_official_dispatch_manifest(
            pipeline_manifest(temporary_path)
        )
        temporary_path.replace(OUTPUT_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(
        f"generated {OUTPUT_PATH.name}: {occurrence_count} official-dispatch occurrence(s)"
    )


if __name__ == "__main__":
    main()
