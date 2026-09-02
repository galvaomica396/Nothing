#!/usr/bin/env python3
import argparse
import contextlib
import importlib.util
import io
import json
import ntpath
import os
import stat
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

from masking_engine_entry import (
    BoundedCapture,
    safe_failure_diagnostics,
    suppress_native_output,
    validate_runtime_manifest,
)
MAX_TRUSTED_REQUEST_BYTES = 1024 * 1024
_STABLE_FAILURE_CODES = frozenset({
    "ANALYZE_DESTINATION_REJECTED", "ANALYZE_INPUT_REJECTED",
    "ANALYSIS_REVISION_INVALID", "COMMON_DETECTOR_FAILED", "COMMON_DETECTOR_UNAVAILABLE",
    "AUTOMATIC_MASK_PROTECTED_NEIGHBOR_OVERLAP",
    "DETECTOR_SPAN_INVALID", "FINALIZE_ARGUMENTS_REJECTED", "FINALIZE_INPUT_REJECTED",
    "INPUT_PATH_REJECTED", "MANIFEST_PATH_REJECTED", "OPTIONS_REJECTED",
    "ORIGINAL_CHANGED", "ORIGINAL_PATH_REJECTED", "OUTPUT_DIRECTORY_REJECTED",
    "PATH_ACCESS_REJECTED", "PATH_SYMLINK_REJECTED", "PUBLIC_PAGE_EVIDENCE_UNAVAILABLE",
    "PUBLIC_PROFILE_REQUIRED", "RAW_TEXT_PREVIEW_REJECTED", "SCAN_VERIFICATION_ADAPTER_UNAVAILABLE",
    "SOURCE_UNAVAILABLE", "STALE_ANALYSIS", "TRUSTED_FINALIZE_ALIAS_BLOCKED",
    "TRUSTED_FINALIZE_ARGUMENTS_REJECTED", "TRUSTED_FINALIZE_BLOCKED",
    "TRUSTED_FINALIZE_AUTHORITY_MISSING",
    "TRUSTED_FINALIZE_CLEANUP_FAILED", "TRUSTED_FINALIZE_CLEAN_COPY_MISMATCH",
    "TRUSTED_FINALIZE_DESTINATION_REJECTED", "TRUSTED_FINALIZE_INVALID",
    "TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED",
    "TRUSTED_FINALIZE_OCCURRENCE_INVALID",
    "TRUSTED_FINALIZE_OCCURRENCE_OUTSIDE_SEGMENT",
    "TRUSTED_FINALIZE_REDACTION_EXECUTION_FAILED",
    "TRUSTED_FINALIZE_REDACTION_RESULT_FAILED",
    "TRUSTED_FINALIZE_MANUAL_EXECUTION_FAILED",
    "TRUSTED_FINALIZE_MANUAL_RESULT_FAILED",
    "TRUSTED_FINALIZE_STAGING_READ_FAILED",
    "TRUSTED_FINALIZE_INTERNAL_FAILED",
    "TRUSTED_FINALIZE_ORIGINAL_UNAVAILABLE", "TRUSTED_FINALIZE_PROMOTION_FAILED",
    "TRUSTED_FINALIZE_REQUEST_REJECTED", "UNRESOLVED_REVIEW",
    "WORD_OFFSET_EVIDENCE_INVALID", "MASKING_PROFILE_UNSUPPORTED", "STAGING_RESERVATION_FAILED",
    "ENGINE_MODULE_UNAVAILABLE", "RUNTIME_MANIFEST_INVALID",
})


def stable_failure_code(error: Exception) -> str:
    if isinstance(error, PermissionError):
        return "PATH_ACCESS_REJECTED"
    if isinstance(error, (ImportError, ModuleNotFoundError)):
        return "ENGINE_MODULE_UNAVAILABLE"
    if isinstance(error, argparse.ArgumentError):
        return "OPTIONS_REJECTED"
    if isinstance(error, FileNotFoundError):
        return "INPUT_PATH_REJECTED"
    if isinstance(error, (ValueError, RuntimeError)) and str(error) in _STABLE_FAILURE_CODES:
        return str(error)
    return "INTERNAL_FAILURE"


def _safe_exception_basename(error: Exception) -> str | None:
    filename = getattr(error, "filename", None)
    if not isinstance(filename, (str, bytes, os.PathLike)):
        return None
    try:
        value = os.fsdecode(filename).rstrip("\\/")
    except (TypeError, ValueError):
        return None
    basename = ntpath.basename(value)
    if not basename or basename in {".", ".."}:
        return None
    return basename


def safe_debug_trace(error: Exception) -> dict[str, str]:
    """Return a bounded, non-document-bearing diagnostic for opt-in tracing."""
    try:
        summary = str(error).strip()
    except Exception:
        summary = ""
    if summary in _STABLE_FAILURE_CODES:
        safe_message = summary
    elif isinstance(error, OSError):
        details = [type(error).__name__]
        if getattr(error, "errno", None) is not None:
            details.append(f"errno={error.errno}")
        if getattr(error, "winerror", None) is not None:
            details.append(f"winerror={error.winerror}")
        basename = _safe_exception_basename(error)
        if basename is not None:
            details.append(f"basename={basename}")
        safe_message = " ".join(details)
    elif re.fullmatch(r"[A-Z0-9_]{3,64}", summary):
        safe_message = summary
    else:
        safe_message = "exception_message_suppressed"
    last_frame = ""
    tb = error.__traceback__
    while tb is not None:
        frame_file = os.path.basename(tb.tb_frame.f_code.co_filename)
        last_frame = f"{frame_file}:{tb.tb_lineno}"
        tb = tb.tb_next
    return {
        "exceptionType": type(error).__name__,
        "message": safe_message,
        "lastFrame": last_frame,
    }


def trusted_finalize_request() -> dict:
    raw = sys.stdin.buffer.read(MAX_TRUSTED_REQUEST_BYTES + 1)
    if len(raw) > MAX_TRUSTED_REQUEST_BYTES:
        raise ValueError("TRUSTED_FINALIZE_REQUEST_REJECTED")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("TRUSTED_FINALIZE_REQUEST_REJECTED") from error
    if not isinstance(request, dict) or set(request) != {
        "input", "original", "manifest", "staging_output", "options"
    }:
        raise ValueError("TRUSTED_FINALIZE_REQUEST_REJECTED")
    if not all(isinstance(request[key], str) and request[key].strip()
               for key in ("input", "original", "manifest", "staging_output")):
        raise ValueError("TRUSTED_FINALIZE_REQUEST_REJECTED")
    if not isinstance(request["options"], dict):
        raise ValueError("TRUSTED_FINALIZE_REQUEST_REJECTED")
    return request

def load_gui_module(repo_root: Path):
    target = repo_root / "document_masker_ocr_gui.py"
    if not target.is_file():
        raise ValueError("ENGINE_MODULE_UNAVAILABLE")
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    spec = importlib.util.spec_from_file_location("document_masker_ocr_gui", str(target))
    if spec is None or spec.loader is None:
        raise ValueError("ENGINE_MODULE_UNAVAILABLE")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore", DeprecationWarning)
            spec.loader.exec_module(mod)
    except (ImportError, ModuleNotFoundError) as exc:
        raise ValueError("ENGINE_MODULE_UNAVAILABLE") from exc
    return mod
def resolve_guarded_path(value: str, *, code: str, require_file: bool = False, require_directory: bool = False) -> Path:
    from path_guard import require_allowed_path

    supplied = Path(value).expanduser()
    if require_file and not supplied.is_file():
        raise ValueError(code)
    if require_directory and not supplied.is_dir():
        raise ValueError(code)
    try:
        resolved = require_allowed_path(supplied, label=code)
    except PermissionError as error:
        # This entry point has historically exposed one stable path-security
        # failure code. Keep that contract independent of whether the host's
        # temporary directory happens to contain a symlink (macOS commonly
        # does; Windows commonly does not).
        raise ValueError("PATH_SYMLINK_REJECTED") from error
    if require_file and not resolved.is_file():
        raise ValueError(code)
    if require_directory and not resolved.is_dir():
        raise ValueError(code)
    return resolved


@dataclass
class StagingReservation:
    path: Path
    inode: int
    device: int
    lock_path: Path
    lock_descriptor: int


def safe_staging_destination(
    manifest_path: Path, staging_output: str, *source_paths: Path | None
) -> StagingReservation:
    from path_guard import same_path

    supplied = Path(staging_output).expanduser()
    if supplied.exists() or supplied.is_symlink() or supplied.suffix.lower() != ".pdf":
        raise ValueError("TRUSTED_FINALIZE_DESTINATION_REJECTED")
    try:
        parent = supplied.parent.resolve(strict=True)
        parent_stat = parent.stat()
    except OSError:
        raise ValueError("TRUSTED_FINALIZE_DESTINATION_REJECTED") from None
    if not same_path(parent, manifest_path.parent) or not parent.is_dir():
        raise ValueError("TRUSTED_FINALIZE_DESTINATION_REJECTED")
    if os.name != "nt" and (
        parent_stat.st_uid != os.getuid()
        or stat.S_IMODE(parent_stat.st_mode) & 0o077
    ):
        raise ValueError("TRUSTED_FINALIZE_DESTINATION_REJECTED")
    # Use the canonical parent for every later operation. In particular, do
    # not retain ``supplied``: a junction can be replaced after this check,
    # while the canonical parent remains the checked physical directory.
    destination = parent / supplied.name
    if any(source is not None and same_path(source, destination) for source in source_paths):
        raise ValueError("TRUSTED_FINALIZE_DESTINATION_REJECTED")

    lock_path = parent / f".{supplied.name}.reservation"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_descriptor = os.open(
            lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR | nofollow, 0o600
        )
    except FileExistsError:
        raise ValueError("TRUSTED_FINALIZE_DESTINATION_REJECTED") from None
    except OSError:
        raise ValueError("STAGING_RESERVATION_FAILED") from None
    try:
        descriptor = os.open(
            destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow, 0o600
        )
        try:
            staging_stat = os.fstat(descriptor)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError:
        try:
            os.close(lock_descriptor)
            lock_path.unlink()
        except OSError:
            raise ValueError("STAGING_RESERVATION_FAILED") from None
        raise ValueError("TRUSTED_FINALIZE_DESTINATION_REJECTED") from None
    except OSError:
        try:
            os.close(lock_descriptor)
            lock_path.unlink()
        except OSError:
            raise ValueError("STAGING_RESERVATION_FAILED") from None
        raise ValueError("STAGING_RESERVATION_FAILED") from None
    return StagingReservation(
        destination, staging_stat.st_ino, staging_stat.st_dev, lock_path, lock_descriptor
    )


def verify_staging_reservation(reservation: StagingReservation) -> None:
    try:
        current = reservation.path.lstat()
        lock = os.fstat(reservation.lock_descriptor)
    except OSError:
        raise ValueError("STAGING_RESERVATION_FAILED") from None
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_ino != reservation.inode
        or current.st_dev != reservation.device
        or not stat.S_ISREG(lock.st_mode)
    ):
        raise ValueError("STAGING_RESERVATION_FAILED")


def release_staging_reservation(reservation: StagingReservation, *, remove_output: bool) -> None:
    cleanup_failed = False
    if remove_output:
        try:
            reservation.path.unlink(missing_ok=True)
        except OSError:
            cleanup_failed = True
    try:
        os.close(reservation.lock_descriptor)
    except OSError:
        cleanup_failed = True
    try:
        reservation.lock_path.unlink(missing_ok=True)
    except OSError:
        cleanup_failed = True
    if cleanup_failed:
        if not remove_output:
            try:
                reservation.path.unlink(missing_ok=True)
            except OSError as exc:
                raise ValueError("TRUSTED_FINALIZE_CLEANUP_FAILED") from exc
        raise ValueError("TRUSTED_FINALIZE_CLEANUP_FAILED")


class StableArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError("OPTIONS_REJECTED")


def main() -> int:
    ap = StableArgumentParser(exit_on_error=False)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--mode", choices=("analyze", "finalize", "trusted-finalize"), default="finalize")
    ap.add_argument("--input", required=False, default="")
    ap.add_argument("--original", required=False, default="")
    ap.add_argument("--outdir", required=False, default="")
    ap.add_argument("--opts", required=False, default="", help="json string")
    ap.add_argument("--opts-stdin", action="store_true")
    ap.add_argument("--manifest", required=False, default="")
    ap.add_argument("--staging-output", required=False, default="")
    ap.add_argument("--request-stdin", action="store_true")
    try:
        args = ap.parse_args()
    except argparse.ArgumentError as exc:
        raise ValueError("OPTIONS_REJECTED") from exc

    repo_root = Path(args.repo_root).expanduser().resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if args.mode == "trusted-finalize":
        if not args.request_stdin or args.opts_stdin or any((
            args.input.strip(), args.original.strip(), args.outdir.strip(),
            args.opts.strip(), args.manifest.strip(), args.staging_output.strip(),
        )):
            raise ValueError("TRUSTED_FINALIZE_ARGUMENTS_REJECTED")
        request = trusted_finalize_request()
        raw_opts = request["options"]
        if not isinstance(raw_opts, dict):
            raise ValueError("TRUSTED_FINALIZE_REQUEST_REJECTED")
        if bool(raw_opts.get("return_text_preview", False)):
            raise ValueError("RAW_TEXT_PREVIEW_REJECTED")
        infile_path = resolve_guarded_path(request["input"], code="INPUT_PATH_REJECTED", require_file=True)
        original_path = resolve_guarded_path(request["original"], code="ORIGINAL_PATH_REJECTED", require_file=True)
        manifest_path = resolve_guarded_path(request["manifest"], code="MANIFEST_PATH_REJECTED", require_file=True)
        staging_output_value = request["staging_output"]
        infile = str(infile_path)
        original = str(original_path)
        outdir_path = None
        outdir = None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("TRUSTED_FINALIZE_INVALID") from None
    else:
        if args.request_stdin and args.mode != "analyze":
            raise ValueError("TRUSTED_FINALIZE_ARGUMENTS_REJECTED")
        if args.mode == "analyze" and args.request_stdin:
            if any((args.input.strip(), args.original.strip(), args.outdir.strip(), args.opts.strip(),
                    args.manifest.strip(), args.staging_output.strip())) or args.opts_stdin:
                raise ValueError("ANALYZE_DESTINATION_REJECTED")
            try:
                request_bytes = sys.stdin.buffer.read(MAX_TRUSTED_REQUEST_BYTES + 1)
                request = json.loads(request_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("ANALYZE_INPUT_REJECTED") from None
            if (len(request_bytes) > MAX_TRUSTED_REQUEST_BYTES or not isinstance(request, dict)
                    or set(request) not in ({"input", "options"}, {"input", "options", "reanalysis"})
                    or not isinstance(request.get("input"), str) or not request["input"].strip()
                    or not isinstance(request.get("options"), dict)):
                raise ValueError("ANALYZE_INPUT_REJECTED")
            raw_opts = request["options"]
            reanalysis = request.get("reanalysis")
            if reanalysis is not None and (
                not isinstance(reanalysis, dict)
                or set(reanalysis) not in (
                    {"kind", "page_start", "page_end", "segment_kind", "analysis_revision"},
                    {"kind", "page_start", "page_end", "analysis_revision"},
                )
                or reanalysis.get("kind") not in {"boundary", "ocr", "manual_preserving"}
                or type(reanalysis.get("analysis_revision")) is not int
                or reanalysis["analysis_revision"] < 2
                or type(reanalysis.get("page_start")) is not int
                or type(reanalysis.get("page_end")) is not int
                or reanalysis["page_start"] < 0 or reanalysis["page_end"] < reanalysis["page_start"]
                or (reanalysis["kind"] == "boundary"
                    and reanalysis.get("segment_kind") not in {"internal_review", "official_dispatch", "attachment", "legal"})
                or (reanalysis["kind"] == "manual_preserving"
                    and (reanalysis["page_start"] != 0 or reanalysis["page_end"] != 0))
            ):
                raise ValueError("ANALYSIS_REVISION_INVALID")
            infile_path = resolve_guarded_path(request["input"], code="INPUT_PATH_REJECTED", require_file=True)
            original_path = None
            outdir_path = None
            infile = str(infile_path)
            original = ""
            outdir = None
        else:
            reanalysis = None
            if args.mode == "analyze":
                if not args.input.strip():
                    raise ValueError("ANALYZE_INPUT_REJECTED")
                if args.original.strip() or args.outdir.strip() or args.manifest.strip() or args.staging_output.strip():
                    raise ValueError("ANALYZE_DESTINATION_REJECTED")
            else:
                if not args.input.strip():
                    raise ValueError("FINALIZE_INPUT_REJECTED")
                if args.manifest.strip() or args.staging_output.strip():
                    raise ValueError("FINALIZE_ARGUMENTS_REJECTED")
            if args.opts_stdin == bool(args.opts.strip()):
                raise ValueError("OPTIONS_REJECTED")
            try:
                option_bytes = (
                    sys.stdin.buffer.read(MAX_TRUSTED_REQUEST_BYTES + 1)
                    if args.opts_stdin else args.opts.encode("utf-8")
                )
                if len(option_bytes) > MAX_TRUSTED_REQUEST_BYTES:
                    raise ValueError("OPTIONS_REJECTED")
                raw_opts = json.loads(option_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("OPTIONS_REJECTED") from None
            if not isinstance(raw_opts, dict):
                raise ValueError("OPTIONS_REJECTED")
            if raw_opts.get("return_text_preview", False) is not False:
                raise ValueError("RAW_TEXT_PREVIEW_REJECTED")
            infile_path = resolve_guarded_path(args.input, code="INPUT_PATH_REJECTED", require_file=True) if args.input.strip() else None
            original_path = resolve_guarded_path(args.original.strip(), code="ORIGINAL_PATH_REJECTED", require_file=True) if args.original.strip() else None
            outdir_path = resolve_guarded_path(args.outdir.strip(), code="OUTPUT_DIRECTORY_REJECTED", require_directory=True) if args.outdir.strip() else None
            infile = str(infile_path) if infile_path else ""
            original = str(original_path) if original_path else ""
            outdir = str(outdir_path) if outdir_path else None

    if (
        not isinstance(raw_opts.get("display_mode", "black"), str)
        or raw_opts.get("display_mode", "black") not in {"black", "label_en", "label_ko", "pseudonym"}
    ):
        raise ValueError("OPTIONS_REJECTED")
    if (
        not isinstance(raw_opts.get("profile", "mixed"), str)
        or raw_opts.get("profile", "mixed") not in {"internal_review", "official_dispatch", "mixed", "legal"}
    ):
        raise ValueError("MASKING_PROFILE_UNSUPPORTED")
    if raw_opts.get("return_text_preview", False) is not False:
        raise ValueError("RAW_TEXT_PREVIEW_REJECTED")
    mod = load_gui_module(repo_root)
    opts = mod.normalize_opts(raw_opts)
    if args.mode == "trusted-finalize":
        reservation = safe_staging_destination(
            manifest_path,
            staging_output_value,
            original_path,
            manifest_path,
            infile_path,
        )
    if args.mode == "analyze":
        if infile_path is None:
            raise ValueError("ANALYZE_INPUT_REJECTED")
        session_hash_key_hex = os.environ.pop("MASKING_SESSION_HASH_KEY_HEX", "")
        try:
            session_hash_key = bytes.fromhex(session_hash_key_hex)
        except ValueError as error:
            raise ValueError("SESSION_HASH_KEY_INVALID") from error
        if len(session_hash_key) != 32:
            raise ValueError("SESSION_HASH_KEY_INVALID")
        capture = BoundedCapture()
        with (
            contextlib.redirect_stdout(capture),
            contextlib.redirect_stderr(capture),
            suppress_native_output(),
        ):
            analysis_manifest = mod.trusted_analysis_manifest(
                infile, opts, reanalysis=reanalysis, session_hash_key=session_hash_key,
            )
        print(json.dumps({"analysis_manifest": analysis_manifest}, ensure_ascii=False))
        return 0
    if args.mode == "trusted-finalize":
        capture = BoundedCapture()
        try:
            verify_staging_reservation(reservation)
            with (
                contextlib.redirect_stdout(capture),
                contextlib.redirect_stderr(capture),
                suppress_native_output(),
            ):
                finalized = mod.trusted_finalize_manifest(
                    original, manifest, opts, str(reservation.path)
                )
            verify_staging_reservation(reservation)
        except Exception:
            release_staging_reservation(reservation, remove_output=True)
            raise
        release_staging_reservation(reservation, remove_output=False)
        output: dict[str, object] = {}
        for key in (
            "status",
            "verification",
            "staging_hash",
            "occurrence_count",
            "applied_mask_count",
            "manual_mask_count",
            "restore_count",
            "effective_mask_count",
            "restore_authorization",
            "save_confirmation",
        ):
            if key not in finalized:
                continue
            output_key = {
                "occurrence_count": "occurrenceCount",
                "applied_mask_count": "appliedMaskCount",
                "manual_mask_count": "manualMaskCount",
                "restore_count": "restoreCount",
                "effective_mask_count": "effectiveMaskCount",
                "restore_authorization": "restoreAuthorization",
                "save_confirmation": "saveConfirmation",
            }.get(key, key)
            value = finalized[key]
            if key == "restore_authorization" and isinstance(value, dict):
                value = {
                    "actionIdHash": value.get("action_id_hash"),
                    "targetOccurrenceIdHash": value.get("target_occurrence_id_hash"),
                    "authorizationEvent": value.get("authorization_event"),
                }
            if key == "save_confirmation" and isinstance(value, dict):
                value = {
                    "status": value.get("status"),
                    "unresolvedReviews": [
                        {
                            "kind": warning.get("kind"),
                            "targetId": warning.get("target_id"),
                            "category": warning.get("category"),
                            "pageStart": warning.get("page_start"),
                            "pageEnd": warning.get("page_end"),
                            "reasonCodes": warning.get("reason_codes"),
                        }
                        for warning in value.get("unresolved_reviews", [])
                        if isinstance(warning, dict)
                    ],
                }
            output[output_key] = value
        print(json.dumps(output, ensure_ascii=True))
        return 0
    if infile_path is None:
        raise ValueError("FINALIZE_INPUT_REJECTED")

    capture = BoundedCapture()
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        _extracted_path, _masked_path, _report_path, report = mod.process_file(
            infile, outdir=outdir, opts=opts
        )
    safe_outputs = validate_runtime_manifest(getattr(report, "runtime_manifest", None))
    print(json.dumps(
        {
            "status": "ok",
            "runtimeManifest": {"outputs": safe_outputs},
            "rawTextReturned": False,
        },
        ensure_ascii=True,
    ))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        stable_code = stable_failure_code(error)
        print(
            json.dumps(
                {
                    "event": "pipeline_failure",
                    "schemaVersion": 1,
                    "rawTextReturned": False,
                    "error": {
                        "code": f"MASKING_PIPELINE_{stable_code}",
                        **(
                            {"debug": safe_debug_trace(error)}
                            if os.environ.get("MASK_TOOL_DEBUG_TRACE") == "1"
                            else {}
                        ),
                        **(
                            {"diagnostics": diagnostics}
                            if (diagnostics := safe_failure_diagnostics(error))
                            else {}
                        ),
                    },
                },
                ensure_ascii=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
