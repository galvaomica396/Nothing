#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "pymupdf>=1.26.7",
# ]
# ///

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

from scripts.generate_native_qa_fixture import (
    ANALYSIS_OPTIONS,
    PIPELINE_PATH,
    SESSION_HASH_KEY_HEX,
    stabilize_pdf_id,
)


OUTPUT_PATH = REPOSITORY_ROOT / "src-tauri/resources/public_native_qa_geometry_fixture.pdf"
GEOMETRY_PENDING_REVIEW_COUNT = 1
GEOMETRY_REGION_KIND = "recipient_reference"


def write_fixture(destination: Path) -> None:
    document = pymupdf.open()
    fallback_font = pymupdf.Font(fontname="cjk")
    try:
        page = document.new_page(width=612, height=792)
        page.insert_font(fontname="qa_cjk", fontbuffer=fallback_font.buffer)
        page.insert_text((72, 72), "수신 중구청장", fontname="qa_cjk", fontsize=12)
        page.insert_text((72, 110), "제목 영역 검토 시나리오", fontname="qa_cjk", fontsize=14)
        page.insert_text((72, 280), "연락처 010-1234-5678", fontname="qa_cjk", fontsize=12)
        document.subset_fonts()
        document.save(destination, garbage=4, deflate=True, reproducible=True)
        stabilize_pdf_id(destination)
    finally:
        document.close()


def assert_text_native(path: Path) -> None:
    document = pymupdf.open(path)
    try:
        if document.page_count != 1 or not document[0].get_text().strip():
            raise RuntimeError("geometry fixture must contain one text-native page")
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
        raise RuntimeError("pipeline CLI rejected the geometry fixture")
    payload = json.loads(completed.stdout)
    manifest = payload.get("analysis_manifest") if isinstance(payload, dict) else None
    if not isinstance(manifest, dict):
        raise RuntimeError("pipeline CLI returned no analysis manifest")
    return manifest


def assert_geometry_review_manifest(manifest: dict[str, object]) -> None:
    regions = manifest.get("regions")
    reviews = manifest.get("review_items")
    if not isinstance(regions, list) or not isinstance(reviews, list):
        raise RuntimeError("geometry fixture manifest has invalid collections")
    geometry_reviews = [
        review
        for review in reviews
        if isinstance(review, dict)
        and review.get("kind") == "region_geometry"
        and review.get("status") == "pending"
    ]
    geometry_regions = [
        region
        for region in regions
        if isinstance(region, dict)
        and region.get("kind") == GEOMETRY_REGION_KIND
        and region.get("state") == "review_required"
        and "box_structure_missing" in region.get("reason_codes", [])
    ]
    if (
        len(geometry_reviews) != GEOMETRY_PENDING_REVIEW_COUNT
        or len(geometry_regions) != GEOMETRY_PENDING_REVIEW_COUNT
        or any("box_structure_missing" not in review.get("reason_codes", []) for review in geometry_reviews)
    ):
        raise RuntimeError("geometry fixture must retain one box-evidence region-geometry review")


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix="native-qa-geometry-", dir=OUTPUT_PATH.parent, delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        write_fixture(temporary_path)
        assert_text_native(temporary_path)
        assert_geometry_review_manifest(pipeline_manifest(temporary_path))
        temporary_path.replace(OUTPUT_PATH)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"generated {OUTPUT_PATH.name}: one pending region-geometry review")


if __name__ == "__main__":
    main()
