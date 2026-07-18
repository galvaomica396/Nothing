#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Final

import fitz


REPO_ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH: Final = REPO_ROOT / "tests" / "fixtures" / "phase6_non_sensitive.pdf"
RAW_DUMMY_VALUES: Final = (
    "010-0000-0000",
    "4000-0000-0000-0000",
    "M00000000",
    "부산광역시 해운대구 우동 테스트로 0",
)
KOREAN_FONT_CANDIDATES: Final = (
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/Library/Fonts/AppleGothic.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_phase6_fixture(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    try:
        font_path = next((candidate for candidate in KOREAN_FONT_CANDIDATES if candidate.exists()), None)
        font_kwargs = {"fontname": "helv"}
        if font_path is not None:
            font_kwargs = {"fontname": "phase6ko", "fontfile": str(font_path)}

        page = doc.new_page(width=420, height=260)
        page.insert_text((36, 44), "MAKIIING V2 PHASE 6 NON-SENSITIVE FIXTURE", **font_kwargs)
        page.insert_text((36, 76), "연락처: 010-0000-0000", **font_kwargs)
        page.insert_text((36, 108), "카드번호: 4000-0000-0000-0000", **font_kwargs)
        page.insert_text((36, 140), "여권번호: M00000000", **font_kwargs)
        page.insert_text((36, 172), "주소: 부산광역시 해운대구 우동 테스트로 0", **font_kwargs)
        page.draw_rect(fitz.Rect(24, 24, 396, 214), color=(0, 0, 0), width=1)

        legal = doc.new_page(width=420, height=260)
        legal.insert_text((36, 44), "법률문서 더미 케이스", **font_kwargs)
        legal.insert_text((36, 76), "사건번호: 2026가단0000", **font_kwargs)
        legal.insert_text((36, 108), "원고: 테스트원고", **font_kwargs)
        legal.insert_text((36, 140), "피고: 테스트피고", **font_kwargs)
        legal.insert_text((36, 172), "담당변호사: 테스트변호사", **font_kwargs)
        legal.draw_rect(fitz.Rect(24, 24, 396, 214), color=(0, 0, 0), width=1)

        doc.save(path)
    finally:
        doc.close()


def ensure_phase6_fixture(path: Path, force: bool) -> dict[str, str | bool | int]:
    created = force or not path.exists()
    if created:
        write_phase6_fixture(path)
    return {
        "fixture_path": str(path),
        "created": created,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the Phase 6 non-sensitive PDF fixture.")
    parser.add_argument("--output", default=str(DEFAULT_FIXTURE_PATH))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = ensure_phase6_fixture(Path(args.output).resolve(), args.force)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
