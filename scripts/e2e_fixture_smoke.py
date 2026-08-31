#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Final

from ensure_phase6_fixture import RAW_DUMMY_VALUES, ensure_phase6_fixture


DEFAULT_OPTS: Final = {
    "output_artifacts": "pdf_safe_report",
    "profile": "legal",
    "region_scope": "national",
    "display_mode": "black",
    "pdf_redaction": True,
    "return_text_preview": False,
}


def run_masking_engine(repo_root: Path, fixture: Path, outdir: Path, engine_path: Path | None) -> dict[str, object]:
    # A packaged engine is used only when the caller explicitly supplies it.
    # Developer tests must not silently pick up an ignored, stale binary from a
    # previous release build and mistake it for the current source tree.
    resolved_engine = engine_path if engine_path and engine_path.exists() else None
    if resolved_engine:
        command = [str(resolved_engine)]
    else:
        command = [sys.executable, str(repo_root / "scripts" / "masking_engine_entry.py")]

    command.extend(
        [
            "--repo-root",
            str(repo_root),
            "--input",
            str(fixture),
            "--outdir",
            str(outdir),
            "--opts",
            json.dumps(DEFAULT_OPTS, separators=(",", ":")),
        ]
    )
    allowed_roots = os.pathsep.join(
        dict.fromkeys(str(path.resolve()) for path in (repo_root, fixture.parent, outdir))
    )
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "MASK_TOOL_ALLOWED_DIRS": allowed_roots},
    )
    event = json.loads(completed.stdout)
    if (
        not isinstance(event, dict)
        or event.get("event") != "engine_result"
        or event.get("schemaVersion") != 1
        or not isinstance(event.get("result"), dict)
    ):
        raise RuntimeError("ENGINE_RESULT_SCHEMA_INVALID")
    return event["result"]


def run_smoke(repo_root: Path, fixture: Path, workdir: Path, engine_path: Path | None) -> dict[str, object]:
    fixture_meta = ensure_phase6_fixture(fixture, force=not fixture.exists())
    outdir = workdir / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    # 문서 일괄(batch) 흐름: outdir=사용자 산출 폴더로 직접 실행한다. 안전 리포트는
    # 이 outdir이 아닌 내부 세션 디렉터리에만 생성되어야 한다.
    result = run_masking_engine(repo_root, fixture, outdir, engine_path)
    runtime_manifest = result.get("runtimeManifest")
    runtime_outputs = runtime_manifest.get("outputs") if isinstance(runtime_manifest, dict) else None
    if not isinstance(runtime_outputs, dict):
        raise RuntimeError("ENGINE_RUNTIME_MANIFEST_INVALID")
    masked_pdfs = sorted(outdir.glob("*.pdf"))
    masked_pdf = masked_pdfs[0] if len(masked_pdfs) == 1 else None
    outdir_resolved = outdir.resolve()

    # 불변식 1: 사용자 outdir 안에 safe_report JSON이 절대 없어야 한다.
    safe_report_in_outdir = sorted(str(p) for p in outdir.rglob("*safe_report*.json"))
    # 불변식 2: 엔진의 공개 이벤트는 리포트 존재 여부만 알리고 내부 경로는 노출하지 않는다.
    report_is_internal = runtime_outputs.get("report_path") is True and not safe_report_in_outdir
    # 불변식 3: 사용자 outdir에는 마스킹 PDF만 남고 리포트는 없어야 한다.
    masked_pdf_in_outdir = (
        runtime_outputs.get("masked_pdf_file") is True
        and masked_pdf is not None
        and masked_pdf.resolve().is_relative_to(outdir_resolved)
    )

    masked_text = ""
    if masked_pdf is not None:
        import fitz

        with fitz.open(masked_pdf) as document:
            masked_text = "\n".join(page.get_text() for page in document).replace("\xa0", " ")
    raw_values_found = [value for value in RAW_DUMMY_VALUES if value in masked_text]
    extracted_txt_default_saved = any(outdir.glob("*.extracted.*.txt"))
    raw_text_returned = result.get("rawTextReturned") is not False

    status = "pass"
    if (
        raw_values_found
        or extracted_txt_default_saved
        or raw_text_returned
        or safe_report_in_outdir
        or not report_is_internal
        or not masked_pdf_in_outdir
    ):
        status = "fail"

    return {
        "status": status,
        "fixture_path": fixture_meta["fixture_path"],
        "fixture_sha256": fixture_meta["sha256"],
        "safe_report_generated": runtime_outputs.get("report_path") is True,
        "safe_report_path_exposed": False,
        "safe_report_is_internal": report_is_internal,
        "safe_report_files_in_user_outdir": safe_report_in_outdir,
        "masked_pdf_path": str(masked_pdf) if masked_pdf is not None else "",
        "masked_pdf_in_user_outdir": masked_pdf_in_outdir,
        "raw_text_returned": raw_text_returned,
        "extracted_txt_default_saved": extracted_txt_default_saved,
        "raw_values_found_in_public_output": raw_values_found,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Phase 6 fixture-backed masking smoke.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--fixture", default="")
    parser.add_argument("--workdir", default="")
    parser.add_argument("--engine-path", default="")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    fixture = Path(args.fixture).resolve() if args.fixture else repo_root / "tests" / "fixtures" / "phase6_non_sensitive.pdf"
    engine_path = Path(args.engine_path).resolve() if args.engine_path else None

    if args.workdir:
        workdir = Path(args.workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
        result = run_smoke(repo_root, fixture, workdir, engine_path)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "pass" else 1

    with tempfile.TemporaryDirectory(prefix="makiiing-phase6-smoke-") as tmp:
        result = run_smoke(repo_root, fixture, Path(tmp), engine_path)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
