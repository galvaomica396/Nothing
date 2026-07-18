#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

def load_gui_module(repo_root: Path):
    target = repo_root / "document_masker_ocr_gui.py"
    if not target.exists():
        raise FileNotFoundError(f"엔진 파일 없음: {target}")
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    spec = importlib.util.spec_from_file_location("document_masker_ocr_gui", str(target))
    if spec is None or spec.loader is None:
        raise RuntimeError("엔진 모듈 로드 실패")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--original", required=False, default="")
    ap.add_argument("--outdir", required=False, default="")
    ap.add_argument("--opts", required=True, help="json string")
    args = ap.parse_args()

    repo_root = Path(args.repo_root)
    infile = args.input
    original = args.original.strip()
    outdir = args.outdir.strip() or None
    if not os.path.isfile(infile):
        raise FileNotFoundError(f"입력 파일 없음: {infile}")
    if original and not os.path.isfile(original):
        raise FileNotFoundError(f"원본 파일 없음: {original}")

    # Opt-in path allowlist (no-op unless MASK_TOOL_ALLOWED_DIRS is configured).
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from path_guard import require_allowed_path

    require_allowed_path(infile, label="입력 파일")
    if original:
        require_allowed_path(original, label="원본 파일")
    if outdir:
        require_allowed_path(outdir, label="출력 폴더")

    mod = load_gui_module(repo_root)
    opts = mod.normalize_opts(json.loads(args.opts))
    if bool(opts.get("return_text_preview", False)):
        raise ValueError("RAW_TEXT_PREVIEW_REJECTED")

    extracted_path, masked_path, report_path, report = mod.process_file(infile, outdir=outdir, opts=opts)

    out = {
        "extracted_path": extracted_path,
        "masked_path": masked_path,
        "report_path": report_path,
        "report": report,
        "runtime_manifest": mod.runtime_manifest_for_report(report),
        "input_path": infile,
        "original_path": original,
        "extracted_text": "",
        "masked_text": "",
        "raw_text_returned": False,
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("MASKING_PIPELINE_FAILED", file=sys.stderr)
        raise SystemExit(1)
