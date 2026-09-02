#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import warnings
import json
import os
import re
import sys
from pathlib import Path
from typing import Final


SAFE_DISPLAY_MODES: Final = {"black", "label_en", "label_ko", "pseudonym"}
EVENT_SCHEMA_VERSION: Final = 1
_KNOWN_FAILURE_CODES: Final = {
    "PATH_ACCESS_REJECTED", "INPUT_UNAVAILABLE", "RAW_TEXT_PREVIEW_REJECTED",
    "KO_PII_DETECTOR_UNAVAILABLE", "STAGING_DESTINATION_REJECTED",
    "STAGING_CLEANUP_FAILED", "MANUAL_BATCH_INVALID", "MANUAL_RESTORE_FAILED",
    "MANUAL_RESTORE_SOURCE_UNAVAILABLE", "MANUAL_RESTORE_VERIFICATION_FAILED",
    "MANUAL_OUTPUT_INVALID", "MASKING_PROFILE_UNSUPPORTED", "OPTIONS_REJECTED",
    "PUBLIC_PROFILE_REQUIRED", "PUBLIC_PAGE_EVIDENCE_UNAVAILABLE",
    "SOURCE_UNAVAILABLE", "ANALYSIS_REVISION_INVALID", "COMMON_DETECTOR_FAILED",
    "COMMON_DETECTOR_UNAVAILABLE", "DETECTOR_SPAN_INVALID", "ORIGINAL_CHANGED",
    "WORD_OFFSET_EVIDENCE_INVALID", "ENGINE_MODULE_UNAVAILABLE",
    "RUNTIME_MANIFEST_INVALID",
}


def failure_code(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "PATH_ACCESS_REJECTED"
    if isinstance(exc, FileNotFoundError):
        return "INPUT_UNAVAILABLE"
    if isinstance(exc, (ImportError, ModuleNotFoundError)):
        return "ENGINE_MODULE_UNAVAILABLE"
    if isinstance(exc, argparse.ArgumentError):
        return "OPTIONS_REJECTED"
    if isinstance(exc, RuntimeError) and str(exc) in _KNOWN_FAILURE_CODES:
        return str(exc)
    if isinstance(exc, ValueError) and str(exc) in _KNOWN_FAILURE_CODES:
        return str(exc)
    return "ENGINE_FAILURE"


class BoundedCapture(io.TextIOBase):
    def __init__(self, limit: int = 4096) -> None:
        self.limit = limit
        self.size = 0
        self.wrote = False

    def write(self, text: str) -> int:
        self.wrote = self.wrote or bool(text)
        self.size = min(self.limit, self.size + len(text.encode("utf-8", "ignore")))
        return len(text)


class EngineExecutionFailure(ValueError):
    def __init__(self, code: str, diagnostics: list[dict[str, str]]) -> None:
        super().__init__(code)
        self.diagnostics = diagnostics


def sanitized_diagnostics(capture: BoundedCapture) -> list[dict[str, str]]:
    return [{"code": "ENGINE_RUNTIME_OUTPUT_SUPPRESSED"}] if capture.wrote else []


def safe_failure_diagnostics(exc: Exception) -> list[dict[str, object]]:
    raw = getattr(exc, "diagnostics", None)
    if not isinstance(raw, list):
        return []
    safe: list[dict[str, object]] = []
    for item in raw[:16]:
        if not isinstance(item, dict) or not {"kind", "reason_code", "count"} <= set(item):
            continue
        kind = item.get("kind")
        reason_code = item.get("reason_code")
        count = item.get("count")
        if (
            not isinstance(kind, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind) is None
            or not isinstance(reason_code, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason_code) is None
            or type(count) is not int
            or not 0 < count <= 10_000
        ):
            continue
        diagnostic: dict[str, object] = {
            "kind": kind,
            "reason_code": reason_code,
            "count": count,
        }
        allowed_optional = {
            "occurrence_id",
            "category",
            "page",
            "rect_fingerprint",
            "expected_text_hash",
            "observed_text_hash",
        }
        if set(item) - {"kind", "reason_code", "count"} - allowed_optional:
            continue
        occurrence_id = item.get("occurrence_id")
        if occurrence_id is not None:
            if (
                not isinstance(occurrence_id, str)
                or re.fullmatch(r"occ_[0-9a-f]{24}", occurrence_id) is None
            ):
                continue
            diagnostic["occurrence_id"] = occurrence_id
        category = item.get("category")
        if category is not None:
            if (
                not isinstance(category, str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category) is None
            ):
                continue
            diagnostic["category"] = category
        page = item.get("page")
        if page is not None:
            if type(page) is not int or not 0 <= page <= 2_000:
                continue
            diagnostic["page"] = page
        for field in ("rect_fingerprint", "expected_text_hash", "observed_text_hash"):
            value = item.get(field)
            if value is None:
                continue
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None
            ):
                continue
            diagnostic[field] = value.lower()
        safe.append(diagnostic)
    return safe


@contextlib.contextmanager
def suppress_native_output():
    saved_fds = tuple(os.dup(fd) for fd in (1, 2))
    try:
        with open(os.devnull, "w", encoding="utf-8") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            yield
    finally:
        for fd, saved_fd in zip((1, 2), saved_fds):
            os.dup2(saved_fd, fd)
            os.close(saved_fd)


def validate_runtime_manifest(manifest: object) -> dict[str, bool]:
    if not isinstance(manifest, dict):
        raise ValueError("RUNTIME_MANIFEST_INVALID")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or any(
        not isinstance(key, str) or not isinstance(value, bool)
        for key, value in outputs.items()
    ):
        raise ValueError("RUNTIME_MANIFEST_INVALID")
    return dict(outputs)


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[1]


def import_engine(repo_root: Path):
    import contextlib
    import io
    import warnings
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    try:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore", DeprecationWarning)
            import document_masker_ocr_gui  # type: ignore
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("ENGINE_MODULE_UNAVAILABLE") from exc
    return document_masker_ocr_gui


def run_mask(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else runtime_root()
    repo_root_text = str(repo_root)
    if repo_root_text not in sys.path:
        sys.path.insert(0, repo_root_text)

    try:
        opts = json.loads(args.opts)
    except json.JSONDecodeError:
        raise ValueError("OPTIONS_REJECTED") from None
    if not isinstance(opts, dict):
        raise ValueError("OPTIONS_REJECTED")
    if "display_mode" in opts and (
        not isinstance(opts["display_mode"], str) or opts["display_mode"] not in SAFE_DISPLAY_MODES
    ):
        raise ValueError("OPTIONS_REJECTED")
    if "profile" in opts and (
        not isinstance(opts["profile"], str)
        or opts["profile"] not in {"internal_review", "official_dispatch", "mixed", "legal"}
    ):
        raise ValueError("MASKING_PROFILE_UNSUPPORTED")
    if opts.get("return_text_preview", False) is not False:
        raise ValueError("RAW_TEXT_PREVIEW_REJECTED")
    infile = str(Path(args.input).expanduser())
    original = str(args.original or "").strip()
    outdir = str(args.outdir or "").strip() or None

    if not os.path.isfile(infile):
        raise FileNotFoundError("input file not found")
    if original and not os.path.isfile(original):
        raise FileNotFoundError("original file not found")

    # The Rust launcher supplies the mandatory capability allowlist. An unset
    # allowlist fails closed through ``require_allowed_path``.
    from path_guard import require_allowed_path

    infile = str(require_allowed_path(infile, label="input"))
    if original:
        original = str(require_allowed_path(original, label="original"))
    if outdir:
        outdir = str(require_allowed_path(outdir, label="outdir"))

    engine = import_engine(repo_root)
    capture = BoundedCapture()
    try:
        with (
            contextlib.redirect_stdout(capture),
            contextlib.redirect_stderr(capture),
            suppress_native_output(),
        ):
            _extracted_path, _masked_path, _report_path, report = engine.process_file(
                infile, outdir=outdir, opts=opts
            )
    except Exception as exc:
        if failure_code(exc) != "ENGINE_FAILURE":
            raise
        raise EngineExecutionFailure("ENGINE_FAILURE", sanitized_diagnostics(capture)) from exc
    runtime_manifest = getattr(report, "runtime_manifest", None)
    if runtime_manifest is None and isinstance(report, dict):
        runtime_manifest = report.get("runtime_manifest")
    safe_outputs = validate_runtime_manifest(runtime_manifest)
    print(
        json.dumps(
            {
                "event": "engine_result",
                "schemaVersion": EVENT_SCHEMA_VERSION,
                "result": {
                    "status": "ok",
                    "runtimeManifest": {"outputs": safe_outputs},
                    "rawTextReturned": False,
                    "enginePackaged": bool(getattr(sys, "frozen", False)),
                },
            },
            ensure_ascii=True,
        )
    )
    return 0


def run_manual_boxes(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else runtime_root()
    scripts_dir = Path(__file__).resolve().parent
    for import_path in (repo_root, scripts_dir):
        if str(import_path) not in sys.path:
            sys.path.insert(0, str(import_path))
    # Single source of truth: the packaged engine and the standalone helper both
    # run the same manual-box core from scripts/apply_manual_boxes.py.
    try:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore", DeprecationWarning)
            import apply_manual_boxes as manual_boxes_module
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("ENGINE_MODULE_UNAVAILABLE") from exc

    try:
        boxes = json.loads(args.boxes)
    except json.JSONDecodeError:
        raise ValueError("MANUAL_BATCH_INVALID") from None
    if not isinstance(boxes, list):
        raise ValueError("MANUAL_BATCH_INVALID")
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        warnings.simplefilter("ignore", DeprecationWarning)
        result = manual_boxes_module.apply_manual_boxes(
            str(Path(args.input).expanduser()),
            str(args.original or "").strip(),
            str(args.outdir or "").strip(),
            boxes,
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
        raise ValueError("KO_PII_DETECTOR_UNAVAILABLE")
    print(json.dumps({"detector_available": True, "engine_packaged": bool(getattr(sys, "frozen", False))}))
    return 0


class StableArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("OPTIONS_REJECTED")


def build_parser() -> argparse.ArgumentParser:
    parser = StableArgumentParser(
        description="Packaged makiiing-v2 masking engine", exit_on_error=False
    )
    parser.add_argument("--manual-boxes", action="store_true", help="Apply manual mask/restore boxes to a PDF")
    parser.add_argument("--detector-smoke", action="store_true", help="Verify the packaged ko-pii detector")
    parser.add_argument("--mode", choices=("analyze", "trusted-finalize"), default="")
    parser.add_argument("--request-stdin", action="store_true")
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
    try:
        args = parser.parse_args()
    except argparse.ArgumentError as exc:
        raise ValueError("OPTIONS_REJECTED") from exc
    if args.mode:
        import run_masking_pipeline

        try:
            return run_masking_pipeline.main()
        except Exception as error:
            stable_code = run_masking_pipeline.stable_failure_code(error)
            failure = {"code": f"MASKING_PIPELINE_{stable_code}"}
            if os.environ.get("MASK_TOOL_DEBUG_TRACE") == "1":
                failure["debug"] = run_masking_pipeline.safe_debug_trace(error)
            diagnostics = safe_failure_diagnostics(error)
            if diagnostics:
                failure["diagnostics"] = diagnostics
            print(
                json.dumps(
                    {
                        "event": "pipeline_failure",
                        "schemaVersion": 1,
                        "rawTextReturned": False,
                        "error": failure,
                    },
                    ensure_ascii=True,
                ),
                file=sys.stderr,
            )
            return 1
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
    except Exception as exc:
        error = {"code": failure_code(exc)}
        diagnostics = safe_failure_diagnostics(exc)
        if diagnostics:
            error["diagnostics"] = diagnostics
        print(
            json.dumps(
                {
                    "event": "engine_failure",
                    "schemaVersion": EVENT_SCHEMA_VERSION,
                    "rawTextReturned": False,
                    "error": error,
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
