#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import fitz


TEXT_PAGE_MARKER = "OFFICIAL DISPATCH: construction-period extension"


def write_fixture(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    try:
        document.set_metadata({
            "format": "PDF 1.7",
            "title": "",
            "author": "",
            "subject": "",
            "keywords": "",
            "creator": "T35 fixture",
            "producer": "T35 fixture",
            "creationDate": "",
            "modDate": "",
            "trapped": "",
        })
        text_page = document.new_page(width=612, height=792)
        text_page.insert_text((54, 72), TEXT_PAGE_MARKER, fontsize=12)
        for scan_page_number in (2, 3):
            scan_page = document.new_page(width=612, height=792)
            scan_page.draw_rect(scan_page.rect, color=None, fill=(0.82, 0.82, 0.82))
            for y in range(36, 760, 24):
                scan_page.draw_line((36, y), (576, y), color=(0.68, 0.68, 0.68), width=0.5)
            scan_page.draw_rect((36, 36, 576, 756), color=(0.56, 0.56, 0.56), width=1)
            scan_page.draw_rect((72, 96, 540, 168), color=(0.72, 0.72, 0.72), fill=(0.76, 0.76, 0.76), width=1)
            scan_page.draw_circle((306, 430), 96, color=(0.62, 0.62, 0.62), width=2)
            _ = scan_page_number
        document.save(destination, garbage=4, deflate=True, no_new_id=True)
    finally:
        document.close()


def assert_fixture(path: Path) -> dict[str, int | str]:
    with fitz.open(path) as document:
        if document.page_count != 3:
            raise RuntimeError("T35 fixture must contain exactly one text page and two scan pages")
        if TEXT_PAGE_MARKER not in document[0].get_text():
            raise RuntimeError("T35 text page marker is missing")
        for page_index in (1, 2):
            page = document[page_index]
            if page.get_text().strip():
                raise RuntimeError("T35 scan pages must not expose text geometry")
            if not page.get_pixmap(alpha=False).samples:
                raise RuntimeError("T35 scan page has no raster-visible content")
    return {
        "fixture_path": str(path.resolve()),
        "pages": 3,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the deterministic T35 mixed text-and-scan PDF fixture.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    destination = Path(args.output).resolve()
    write_fixture(destination)
    print(json.dumps(assert_fixture(destination), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
