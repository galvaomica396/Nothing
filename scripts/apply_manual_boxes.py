#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path
import warnings

with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    import fitz  # pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from path_guard import require_allowed_path
from pdf_redaction_rendering import MANUAL_REDACTION_TAG, add_redaction_annotation, insert_pdf_label, normalize_display_mode, normalize_redaction_tag


def safe_output_path(input_pdf: str, outdir: str, original_pdf: str = "") -> str:
    stem = Path(original_pdf or input_pdf).stem
    target_dir = Path(outdir)
    base = target_dir / f"{stem}_manual_redacted.pdf"
    if not base.exists():
        return str(base)
    for idx in range(2, 1000):
        candidate = target_dir / f"{stem}_manual_redacted_{idx}.pdf"
        if not candidate.exists():
            return str(candidate)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{stem}_manual_redacted_", suffix=".pdf", dir=target_dir)
    os.close(fd)
    os.unlink(tmp_path)
    return tmp_path


def normalized_save_target(input_pdf: str, output_pdf: str) -> tuple[str, str]:
    input_abs = os.path.abspath(input_pdf)
    output_abs = os.path.abspath(output_pdf)
    if input_abs == output_abs or os.path.exists(output_abs):
        raise ValueError("STAGING_DESTINATION_REJECTED")
    fd, staging_path = tempfile.mkstemp(
        prefix="tauri_manual_save_",
        suffix=".pdf",
        dir=os.path.dirname(output_abs) or ".",
    )
    os.close(fd)
    os.unlink(staging_path)
    return staging_path, output_abs


def finish_save(staging_path: str, final_path: str) -> str:
    os.replace(staging_path, final_path)
    return final_path


def normalize_rect(x0: float, y0: float, x1: float, y1: float):
    nx0 = min(x0, x1)
    ny0 = min(y0, y1)
    nx1 = max(x0, x1)
    ny1 = max(y0, y1)
    if nx1 <= nx0 or ny1 <= ny0:
        return None
    return fitz.Rect(nx0, ny0, nx1, ny1)


def manual_box_key(page_idx: int, mode: str, rect: fitz.Rect) -> tuple[int, str, int, int, int, int]:
    return (
        page_idx,
        mode,
        round(rect.x0 * 1000),
        round(rect.y0 * 1000),
        round(rect.x1 * 1000),
        round(rect.y1 * 1000),
    )


def warning_message(message: str, exc: Exception | None = None) -> str:
    if exc is None:
        return message
    return f"{message}: operation_failed"
def cleanup_owned_staging(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError("STAGING_CLEANUP_FAILED") from exc




def apply_manual_boxes(
    in_pdf: str,
    original_pdf: str,
    outdir: str,
    boxes: list,
    display_mode: str = "black",
) -> dict:
    """Apply manual mask/restore boxes to ``in_pdf`` and return the result dict.

    This is the single source of truth for manual-box application; both this
    module's ``main()`` and ``scripts/masking_engine_entry.run_manual_boxes``
    call it. It returns the shared result payload (status, output_file, counts,
    warnings, display_mode) so each caller can serialize the CLI contract it
    owns. Enforces the mandatory ``MASK_TOOL_ALLOWED_DIRS`` allowlist on all
    paths.
    """
    original_pdf = (original_pdf or "").strip()
    outdir = (outdir or "").strip()
    display_mode = normalize_display_mode(str(display_mode or "black"))

    if not os.path.isfile(in_pdf):
        raise FileNotFoundError("MANUAL_INPUT_NOT_FOUND")
    if original_pdf and not os.path.isfile(original_pdf):
        raise FileNotFoundError("MANUAL_ORIGINAL_NOT_FOUND")
    if not outdir:
        raise ValueError("출력 폴더 경로가 비어 있습니다.")
    in_pdf = str(require_allowed_path(in_pdf, label="input"))
    if original_pdf:
        original_pdf = str(require_allowed_path(original_pdf, label="original"))
    outdir = str(require_allowed_path(outdir, label="outdir"))
    os.makedirs(outdir, exist_ok=True)

    out_pdf = safe_output_path(in_pdf, outdir, original_pdf)
    save_pdf, replace_target = normalized_save_target(in_pdf, out_pdf)

    try:
        doc = fitz.open(in_pdf)
        original_doc = fitz.open(original_pdf) if original_pdf else fitz.open(in_pdf)
    except Exception:
        cleanup_owned_staging(save_pdf)
        raise

    page_ops = {}
    mask_count = 0
    restore_count = 0
    skipped_boxes = 0
    warnings = []
    seen_boxes = set()
    final_modes = {}

    for idx, box in enumerate(boxes):
        try:
            mode = str(box.get("mode", "mask")).lower().strip()
            page_idx = int(box["page"])
            if page_idx < 0 or page_idx >= doc.page_count:
                skipped_boxes += 1
                warnings.append(f"box {idx} skipped: page out of range")
                continue

            rect = normalize_rect(float(box["x0"]), float(box["y0"]), float(box["x1"]), float(box["y1"]))
            if not rect:
                skipped_boxes += 1
                warnings.append(f"box {idx} skipped: invalid rectangle")
                continue

            if mode not in {"mask", "restore"}:
                skipped_boxes += 1
                warnings.append(f"box {idx} skipped: invalid mode")
                continue
            dedupe_key = manual_box_key(page_idx, mode, rect)
            inverse_key = manual_box_key(
                page_idx, "restore" if mode == "mask" else "mask", rect
            )
            if dedupe_key in seen_boxes:
                skipped_boxes += 1
                warnings.append(f"box {idx} skipped: duplicate manual box")
                continue
            # A matching inverse action changes this rectangle's state, so a
            # later identical action is not a duplicate and must be preserved.
            seen_boxes.discard(inverse_key)
            seen_boxes.add(dedupe_key)
            final_modes[(page_idx, dedupe_key[2], dedupe_key[3], dedupe_key[4], dedupe_key[5])] = mode
            if mode == "restore":
                if not original_pdf:
                    raise RuntimeError("MANUAL_RESTORE_SOURCE_UNAVAILABLE")
                restore_count += 1
            else:
                mask_count += 1
            tag = normalize_redaction_tag(str(box.get("tag") or MANUAL_REDACTION_TAG))
            page_ops.setdefault(page_idx, []).append((idx, mode, rect, tag))
        except Exception as exc:
            if isinstance(exc, RuntimeError) and str(exc) in {
                "MANUAL_RESTORE_SOURCE_UNAVAILABLE", "MANUAL_RESTORE_FAILED",
            }:
                raise
            skipped_boxes += 1
            warnings.append(warning_message(f"box {idx} skipped", exc))
            continue

    mask_applied = 0
    restore_applied = 0
    restore_verifications: list[tuple[int, fitz.Rect, bytes]] = []
    try:
        touched_pages = sorted(page_ops)
        for page_idx in touched_pages:
            if page_idx < 0 or page_idx >= doc.page_count:
                continue
            try:
                page = doc[page_idx]
                page_rect = page.rect
            except Exception as exc:
                skipped = len(page_ops.get(page_idx, []))
                skipped_boxes += skipped
                warnings.append(warning_message(f"page {page_idx} skipped", exc))
                continue

            pending_mask_annots = 0
            pending_labels = []

            def flush_masks() -> None:
                nonlocal pending_mask_annots, mask_applied, skipped_boxes, pending_labels
                if not pending_mask_annots:
                    return
                try:
                    page.apply_redactions()
                    for label_rect, label in pending_labels:
                        insert_pdf_label(page, label_rect, label)
                    mask_applied += pending_mask_annots
                except Exception as exc:
                    skipped_boxes += pending_mask_annots
                    warnings.append(warning_message(f"page {page_idx} redactions skipped", exc))
                finally:
                    pending_mask_annots = 0
                    pending_labels = []

            for box_idx, mode, rect, tag in page_ops.get(page_idx, []):
                try:
                    clipped = rect & page_rect
                    if clipped.width < 2 or clipped.height < 2:
                        skipped_boxes += 1
                        warnings.append(f"box {box_idx} skipped: clipped rectangle too small")
                        continue
                    if mode != "restore":
                        label = add_redaction_annotation(page, clipped, tag, display_mode)
                        if label:
                            pending_labels.append((fitz.Rect(clipped), label))
                        pending_mask_annots += 1
                        continue

                    flush_masks()
                    if page_idx >= original_doc.page_count:
                        raise RuntimeError("MANUAL_RESTORE_SOURCE_UNAVAILABLE")
                    original_page = original_doc[page_idx]
                    source_clip = clipped & original_page.rect
                    if source_clip.width < 2 or source_clip.height < 2:
                        raise RuntimeError("MANUAL_RESTORE_SOURCE_UNAVAILABLE")
                    target_rect = fitz.Rect(clipped.x0, clipped.y0, clipped.x0 + source_clip.width, clipped.y0 + source_clip.height)
                    try:
                        page.show_pdf_page(target_rect, original_doc, page_idx, clip=source_clip, overlay=True)
                        pix = original_page.get_pixmap(
                            clip=source_clip, matrix=fitz.Matrix(2.5, 2.5), alpha=False
                        )
                        page.insert_image(target_rect, pixmap=pix, overlay=True)
                    except Exception as exc:
                        raise RuntimeError("MANUAL_RESTORE_FAILED") from exc
                    restore_applied += 1
                    restore_verifications.append((page_idx, fitz.Rect(target_rect), bytes(pix.samples)))
                except Exception as exc:
                    if mode == "restore":
                        raise
                    skipped_boxes += 1
                    warnings.append(warning_message(f"box {box_idx} {mode} skipped", exc))
                    continue

            flush_masks()

        status = "applied"
        if mask_applied == 0 and restore_applied == 0:
            status = "no_effect"
            warnings.append("no valid manual boxes; unchanged preview was saved")

        doc.save(save_pdf, garbage=4, deflate=True, clean=True)
        if not os.path.isfile(save_pdf) or os.path.getsize(save_pdf) <= 0:
            raise RuntimeError("MANUAL_OUTPUT_INVALID")
    except Exception:
        cleanup_owned_staging(save_pdf)
        raise
    finally:
        original_doc.close()
        doc.close()
    try:
        verify_doc = fitz.open(save_pdf)
        try:
            for page_idx, target_rect, expected_pixels in restore_verifications:
                state_key = (
                    page_idx,
                    round(target_rect.x0 * 1000),
                    round(target_rect.y0 * 1000),
                    round(target_rect.x1 * 1000),
                    round(target_rect.y1 * 1000),
                )
                if final_modes.get(state_key) == "mask":
                    continue
                actual_pixels = verify_doc[page_idx].get_pixmap(
                    clip=target_rect, matrix=fitz.Matrix(2.5, 2.5), alpha=False
                ).samples
                if actual_pixels != expected_pixels:
                    raise RuntimeError("MANUAL_RESTORE_VERIFICATION_FAILED")
        finally:
            verify_doc.close()
    except Exception:
        cleanup_owned_staging(save_pdf)
        raise
    try:
        final_pdf = finish_save(save_pdf, replace_target)
    except Exception:
        cleanup_owned_staging(save_pdf)
        raise

    return {
        "status": status,
        "output_file": final_pdf,
        "mask_count": mask_count,
        "restore_count": restore_count,
        "applied_count": mask_applied + restore_applied,
        "excluded_count": 0,
        "mask_boxes_applied": mask_applied,
        "unmask_boxes_applied": restore_applied,
        "requires_revalidation": restore_applied > 0,
        "skipped_boxes": skipped_boxes,
        "warnings": warnings,
        "display_mode": display_mode,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--original", default="")
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--boxes", required=True, help="JSON string")
    parser.add_argument("--display-mode", default="black", choices=("black", "label_en", "label_ko", "pseudonym"))
    args = parser.parse_args()

    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        warnings.simplefilter("ignore", DeprecationWarning)
        result = apply_manual_boxes(
            args.input,
            args.original,
            args.outdir,
            json.loads(args.boxes),
            args.display_mode,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("MANUAL_APPLY_FAILED", file=sys.stderr)
        raise SystemExit(1)
