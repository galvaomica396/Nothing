#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Final


SAFE_DISPLAY_MODES: Final = {"black", "label_en", "label_ko", "pseudonym"}

def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def import_engine(repo_root: Path):
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import document_masker_ocr_gui  # type: ignore

    return document_masker_ocr_gui


def run_mask(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else runtime_root()
    engine = import_engine(repo_root)

    opts = json.loads(args.opts)
    if opts.get("display_mode") not in SAFE_DISPLAY_MODES:
        opts["display_mode"] = "black"
    if bool(opts.get("return_text_preview", False)):
        raise ValueError("RAW_TEXT_PREVIEW_REJECTED")
    infile = str(Path(args.input).expanduser())
    original = str(args.original or "").strip()
    outdir = str(args.outdir or "").strip() or None

    if not os.path.isfile(infile):
        raise FileNotFoundError("input file not found")
    if original and not os.path.isfile(original):
        raise FileNotFoundError("original file not found")

    # Opt-in path allowlist (no-op unless MASK_TOOL_ALLOWED_DIRS is configured).
    from path_guard import require_allowed_path

    require_allowed_path(infile, label="input")
    if original:
        require_allowed_path(original, label="original")
    if outdir:
        require_allowed_path(outdir, label="outdir")

    extracted_path, masked_path, report_path, report = engine.process_file(infile, outdir=outdir, opts=opts)
    out = {
        "extracted_path": extracted_path,
        "masked_path": masked_path,
        "report_path": report_path,
        "report": report,
        "runtime_manifest": engine.runtime_manifest_for_report(report),
        "input_path": infile,
        "original_path": original,
        "extracted_text": "",
        "masked_text": "",
        "raw_text_returned": False,
        "engine_packaged": bool(getattr(sys, "frozen", False)),
    }
    print(json.dumps(out, ensure_ascii=True))
    return 0


def run_manual_boxes(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else runtime_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    # Single source of truth: the packaged engine and the standalone helper both
    # run the same manual-box core from scripts/apply_manual_boxes.py.
    import apply_manual_boxes as manual_boxes_module

    result = manual_boxes_module.apply_manual_boxes(
        str(Path(args.input).expanduser()),
        str(args.original or "").strip(),
        str(args.outdir or "").strip(),
        json.loads(args.boxes),
        str(args.display_mode or "black"),
    )
    # Preserve this entry point's CLI contract: it adds the packaged-engine
    # fields on top of the shared result and serializes with ensure_ascii=True.
    #
    # requires_revalidation은 "이 수동 보정이 개인정보 노출 위험을 늘렸으니 저장 전
    # 재검증하라"는 신호다. 마스킹 박스 추가는 노출을 오직 줄이므로(기존 안전 리포트를
    # 무효화할 이유가 없다) 재검증이 필요치 않다. 복원(restore/unmask)만이 원본 영역을
    # 되살려 위험을 늘리므로 재검증 대상이다. 따라서 unmask 적용 여부만으로 판정한다.
    result["requires_revalidation"] = bool(result["unmask_boxes_applied"] > 0)
    result["raw_value_saved"] = False
    result["engine_packaged"] = bool(getattr(sys, "frozen", False))
    print(json.dumps(result, ensure_ascii=True))
    return 0


def run_detector_smoke(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else runtime_root()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from ko_pii_detector import build_ko_pii_detector

    detector = build_ko_pii_detector(lambda _message: None)
    if detector is None:
        raise RuntimeError("KO_PII_DETECTOR_UNAVAILABLE")
    print(json.dumps({"detector_available": True, "engine_packaged": bool(getattr(sys, "frozen", False))}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Packaged makiiing-v2 masking engine")
    parser.add_argument("--manual-boxes", action="store_true", help="Apply manual mask/restore boxes to a PDF")
    parser.add_argument("--detector-smoke", action="store_true", help="Verify the packaged ko-pii detector")
    parser.add_argument("--repo-root", default="", help="Runtime root containing document_masker_ocr_gui.py and data")
    parser.add_argument("--input", default="")
    parser.add_argument("--original", default="")
    parser.add_argument("--outdir", default="")
    parser.add_argument("--opts", default="", help="JSON options for masking pipeline")
    parser.add_argument("--boxes", default="", help="JSON manual box payload for --manual-boxes")
    parser.add_argument("--display-mode", default="black", choices=("black", "label_en", "label_ko", "pseudonym"), help="Manual redaction display mode")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.detector_smoke:
        return run_detector_smoke(args)
    if args.manual_boxes:
        if not args.input:
            parser.error("--input is required with --manual-boxes")
        if not args.boxes:
            parser.error("--boxes is required with --manual-boxes")
        return run_manual_boxes(args)
    if not args.input:
        parser.error("--input is required unless --manual-boxes is used")
    if not args.opts:
        parser.error("--opts is required unless --manual-boxes is used")
    return run_mask(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("MASKING_ENGINE_FAILED", file=sys.stderr)
        raise SystemExit(1)
