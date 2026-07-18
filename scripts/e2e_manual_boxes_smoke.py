#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import fitz


def write_fixture(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((32, 52), "manual smoke target")
    page.insert_text((32, 92), "phone 010-0000-0000")
    doc.save(path)
    doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Packaged manual-box masking smoke")
    parser.add_argument("--engine-path", required=True)
    parser.add_argument("--workdir", required=True)
    args = parser.parse_args()

    engine_path = Path(args.engine_path).resolve()
    workdir = Path(args.workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / "manual_boxes_fixture.pdf"
    write_fixture(fixture)

    boxes = [{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}]
    completed = subprocess.run(
        [
            str(engine_path),
            "--manual-boxes",
            "--input",
            str(fixture),
            "--original",
            str(fixture),
            "--outdir",
            str(workdir),
            "--boxes",
            json.dumps(boxes),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    output_file = Path(str(payload.get("output_file", "")))
    if payload.get("status") != "applied":
        raise RuntimeError(f"manual boxes were not applied: {payload}")
    if int(payload.get("mask_boxes_applied", 0)) < 1:
        raise RuntimeError(f"manual mask count missing: {payload}")
    if not output_file.exists():
        raise RuntimeError(f"manual output missing: {output_file}")

    print(
        json.dumps(
            {
                "status": "pass",
                "input_pdf": str(fixture),
                "output_pdf": str(output_file),
                "mask_boxes_applied": payload.get("mask_boxes_applied"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
