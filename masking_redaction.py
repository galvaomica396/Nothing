#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF native redaction and manual-correction engine extracted from
document_masker_ocr_gui.

Behavior-preserving move of native PDF redaction, post-verification, and
manual redaction/correction application. Pure code movement.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from pdf_redaction_rendering import (
    MANUAL_REDACTION_TAG,
    add_redaction_annotation,
    insert_pdf_pseudonym_label,
    normalize_display_mode,
    normalize_redaction_tag,
)
from privacy_transformers import TransformState, pseudonym_value
from masking_rules import (
    RedactionMatch,
    _DASH_CHARS,
    _display_token,
    _insert_pdf_label,
    _review_item_for_rect,
)


@dataclass(frozen=True)
class ManualRedactionBox:
    page_index: int
    rect: tuple[float, float, float, float]
    mode: str = "mask"  # mask | restore
    tag: str = MANUAL_REDACTION_TAG


@dataclass(frozen=True)
class ManualCorrectionBox:
    page_index: int
    rect: tuple[float, float, float, float]
    action: str = "mask"
    tag: str = MANUAL_REDACTION_TAG
@dataclass(frozen=True)
class OccurrenceRedactionInput:
    """Revision-scoped, geometry-grounded native PDF redaction request.

    This is intentionally distinct from ``RedactionMatch``.  Supplying one of
    these requests selects the occurrence-only path; it never falls back to
    document-wide text search.
    """

    occurrence_id: str
    run_id: str
    document_sha256: str
    analysis_revision: int
    page_index: int
    rect_list: tuple[tuple[float, float, float, float], ...]
    action: str
    provenance: str | Mapping[str, Any]
    expected_text_hash: str
    category: str | None = None
    schema_version: str = "occurrence-redaction/v1"
    coordinate_space: str = "pdf_points_top_left"


@dataclass(frozen=True)
class _ValidatedOccurrence:
    request: OccurrenceRedactionInput
    rects: tuple[Any, ...]
    protected_rects: tuple[Any, ...]
@dataclass(frozen=True)
class ManualActionV1:
    """Versioned manual action; all values are geometry evidence, never PII."""

    manual_action_id: str
    run_id: str
    document_sha256: str
    analysis_revision: int
    page_index: int
    rect_list: tuple[tuple[float, float, float, float], ...]
    mode: str
    source_kind: str
    linked_occurrence_id: str | None = None
    expected_text_hash: str | None = None
    protected_neighbor_refs: tuple[tuple[float, float, float, float], ...] = ()
    restore_authorization_hash: str | None = None
    schema_version: str = "manual-action/v1"
    coordinate_space: str = "pdf_points_top_left"


def _remove_staging_output(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as error:
        raise RuntimeError("STAGING_CLEANUP_FAILED") from error
def _assert_fresh_staging_output(source_pdf_path: str, output_pdf_path: str, *protected_paths: str) -> None:
    source = Path(source_pdf_path)
    output = Path(output_pdf_path)
    if source.is_symlink() or not source.is_file() or output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        raise ValueError("STAGING_DESTINATION_REJECTED")
    destination = output.resolve(strict=False)
    for candidate in (source, *(Path(path) for path in protected_paths if path)):
        try:
            if candidate.resolve(strict=True) == destination:
                raise ValueError("STAGING_DESTINATION_REJECTED")
        except FileNotFoundError:
            continue



def _manual_review(action_id: str, reason_code: str, *, page_index: int | None = None, count: int = 1) -> dict[str, Any]:
    return {
        "manual_action_id": action_id,
        "page": page_index,
        "status": "review_required",
        "reason_code": reason_code,
        "count": count,
        "raw_value_saved": False,
    }


def _review_evidence(
    occurrence_id: str,
    reason_code: str,
    *,
    page_index: int | None = None,
    count: int = 1,
    category: str | None = None,
    rects: Sequence[Any] = (),
    expected_text_hash: str | None = None,
    observed_text_hash: str | None = None,
) -> dict[str, Any]:
    evidence = {
        "occurrence_id": occurrence_id,
        "page": page_index,
        "status": "review_required",
        "reason_code": reason_code,
        "count": count,
        "raw_value_saved": False,
    }
    if (
        isinstance(category, str)
        and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category) is not None
    ):
        evidence["category"] = category
        rect_fingerprint = _rect_fingerprint(rects)
        if rect_fingerprint is not None:
            evidence["rect_fingerprint"] = rect_fingerprint
        if (
            isinstance(expected_text_hash, str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", expected_text_hash) is not None
        ):
            evidence["expected_text_hash"] = expected_text_hash.lower()
        if (
            isinstance(observed_text_hash, str)
            and re.fullmatch(r"[0-9a-fA-F]{64}", observed_text_hash) is not None
        ):
            evidence["observed_text_hash"] = observed_text_hash.lower()
    return evidence


def _rect_fingerprint(rects: Sequence[Any]) -> str | None:
    """Return a geometry-only fingerprint for safe intrinsic diagnostics."""
    if (
        not isinstance(rects, Sequence)
        or isinstance(rects, (str, bytes))
        or not rects
    ):
        return None
    normalized: list[list[int]] = []
    for value in rects:
        try:
            if isinstance(value, Mapping):
                coordinates = [float(value[key]) for key in ("x0", "y0", "x1", "y1")]
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                coordinates = [float(coordinate) for coordinate in value]
            else:
                coordinates = [
                    float(getattr(value, key))
                    for key in ("x0", "y0", "x1", "y1")
                ]
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        if len(coordinates) != 4:
            return None
        if (
            not all(math.isfinite(coordinate) and coordinate >= 0.0 for coordinate in coordinates)
            or coordinates[2] <= coordinates[0]
            or coordinates[3] <= coordinates[1]
        ):
            return None
        normalized.append([
            int(math.floor(coordinate * 1_000_000 + 0.5))
            for coordinate in coordinates
        ])
    normalized.sort()
    return hashlib.sha256(
        json.dumps(normalized, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _rect_from_values(fitz_module: Any, values: Sequence[Any]) -> Any | None:
    if len(values) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(value) for value in values)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return fitz_module.Rect(x0, y0, x1, y1)


def _provenance_protected_rects(provenance: str | Mapping[str, Any]) -> Sequence[Any]:
    if not isinstance(provenance, Mapping):
        return ()
    protected = provenance.get("protected_neighbor_rects", provenance.get("protected_neighbors", ()))
    return protected if isinstance(protected, Sequence) and not isinstance(protected, (str, bytes)) else ()


def automatic_masks_preserve_manual_neighbors(
    automatic_rects: Sequence[Sequence[float]],
    protected_neighbor_rects: Sequence[Sequence[float]],
) -> bool:
    """Return whether automatic masking remains disjoint from manual protections.

    Boundary contact is safe; positive-area overlap is not. Invalid geometry is
    fail-closed rather than silently treated as disjoint.
    """
    def normalized(values: Sequence[float]) -> tuple[float, float, float, float] | None:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or len(values) != 4:
            return None
        try:
            rect = tuple(float(value) for value in values)
        except (TypeError, ValueError):
            return None
        if (
            not all(math.isfinite(value) for value in rect)
            or rect[2] <= rect[0]
            or rect[3] <= rect[1]
        ):
            return None
        return rect

    masks = [normalized(rect) for rect in automatic_rects]
    neighbors = [normalized(rect) for rect in protected_neighbor_rects]
    if any(rect is None for rect in (*masks, *neighbors)):
        return False
    return not any(
        mask[0] < neighbor[2] and mask[2] > neighbor[0]
        and mask[1] < neighbor[3] and mask[3] > neighbor[1]
        for mask in masks for neighbor in neighbors
    )


def _source_pdf_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def _validate_occurrence_input(
    fitz_module: Any,
    doc: Any,
    request: OccurrenceRedactionInput,
) -> tuple[_ValidatedOccurrence | None, str | None]:
    if request.schema_version != "occurrence-redaction/v1":
        return None, "unsupported_occurrence_schema"
    if request.coordinate_space != "pdf_points_top_left":
        return None, "invalid_coordinate_space"
    if not isinstance(request.occurrence_id, str) or not request.occurrence_id:
        return None, "invalid_occurrence_id"
    if not isinstance(request.run_id, str) or not request.run_id:
        return None, "invalid_run_id"
    if not isinstance(request.document_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", request.document_sha256) is None:
        return None, "invalid_document_sha256"
    if isinstance(request.analysis_revision, bool) or not isinstance(request.analysis_revision, int) or request.analysis_revision < 1:
        return None, "invalid_analysis_revision"
    if isinstance(request.page_index, bool) or not isinstance(request.page_index, int) or not 0 <= request.page_index < doc.page_count:
        return None, "invalid_page_index"
    if request.action not in {"mask", "exclude", "review"}:
        return None, "invalid_occurrence_action"
    if not request.provenance:
        return None, "missing_provenance"
    if not isinstance(request.expected_text_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", request.expected_text_hash):
        return None, "invalid_expected_text_hash"
    if not isinstance(request.rect_list, Sequence) or isinstance(request.rect_list, (str, bytes)) or not request.rect_list:
        return None, "missing_grounded_rectangles"

    page = doc[request.page_index]
    page_bounds = page.rect
    rects: list[Any] = []
    for values in request.rect_list:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return None, "invalid_pdf_points_top_left_rect"
        rect = _rect_from_values(fitz_module, values)
        if rect is None or not page_bounds.contains(rect):
            return None, "invalid_pdf_points_top_left_rect"
        rects.append(rect)

    protected_rects: list[Any] = []
    for values in _provenance_protected_rects(request.provenance):
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            return None, "invalid_protected_neighbor_rect"
        rect = _rect_from_values(fitz_module, values)
        if rect is None or not page_bounds.contains(rect):
            return None, "invalid_protected_neighbor_rect"
        if any(rect.intersects(mask_rect) for mask_rect in rects):
            return None, "protected_neighbor_overlap"
        protected_rects.append(rect)
    return _ValidatedOccurrence(request, tuple(rects), tuple(protected_rects)), None


def _rect_text_hash(page: Any, rects: Sequence[Any]) -> str | None:
    """Hash only text geometrically tied to this occurrence; never return it."""
    if not rects:
        return None

    page_words = page.get_text("words")
    words: list[str] = []
    # The Rust authority canonicalizes every rectangle list by the same
    # x0/y0/x1/y1 key before storing the manifest. Hash the canonical order
    # here as well; detector iteration order must not change the fingerprint.
    ordered_rects = sorted(
        rects,
        key=lambda rect: (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
    )
    for rect in ordered_rects:
        exact_words = [
            word
            for word in page_words
            if abs(word[0] - rect.x0) <= 0.1
            and abs(word[1] - rect.y0) <= 0.1
            and abs(word[2] - rect.x1) <= 0.1
            and abs(word[3] - rect.y1) <= 0.1
        ]
        if exact_words:
            words.append(" ".join(str(word[4]) for word in exact_words))
            continue
        containing_words = [
            word for word in page_words
            if word[0] <= rect.x0 + 0.1 and word[1] <= rect.y0 + 0.1
            and word[2] >= rect.x1 - 0.1 and word[3] >= rect.y1 - 0.1
        ]
        strict_subword = any(
            rect.x0 > word[0] + 0.1 or rect.x1 < word[2] - 0.1
            for word in containing_words
        )
        if strict_subword:
            text = str(page.get_textbox(rect)).strip()
            if not text:
                return None
            words.append(text)
            continue
        rect_words = [
            str(word[4])
            for word in page_words
            if word[0] < rect.x1 and word[2] > rect.x0 and word[1] < rect.y1 and word[3] > rect.y0
        ]
        if not rect_words:
            return None
        words.append(" ".join(rect_words))
    return hashlib.sha256("\n".join(words).encode("utf-8")).hexdigest()


def _has_text_glyph_center_in_rect(page: Any, rect: Any) -> bool:
    """Detect residual glyphs by character ownership, not word-box touch."""
    left = min(float(rect.x0), float(rect.x1))
    top = min(float(rect.y0), float(rect.y1))
    right = max(float(rect.x0), float(rect.x1))
    bottom = max(float(rect.y0), float(rect.y1))
    try:
        raw = page.get_text("rawdict")
    except Exception:
        return _rect_text_hash(page, (rect,)) is not None
    if not isinstance(raw, Mapping):
        return _rect_text_hash(page, (rect,)) is not None
    for block in raw.get("blocks", ()):
        for line in block.get("lines", ()):
            for span in line.get("spans", ()):
                for character in span.get("chars", ()):
                    value = character.get("c")
                    bbox = character.get("bbox")
                    if not isinstance(value, str) or not value.strip() or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                        continue
                    try:
                        center_x = (float(bbox[0]) + float(bbox[2])) / 2
                        center_y = (float(bbox[1]) + float(bbox[3])) / 2
                    except (TypeError, ValueError):
                        continue
                    if left <= center_x <= right and top <= center_y <= bottom:
                        return True
    return False


def _has_residual_text(page: Any, rects: Sequence[Any]) -> bool:
    return any(_has_text_glyph_center_in_rect(page, rect) for rect in rects)


class RectTextHashError(RuntimeError):
    """Safe, typed rectangle-text fingerprint failure."""

    def __init__(self, reason_code: str, cause: Exception | None = None) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.__cause__ = cause


def occurrence_rect_text_hash(
    pdf_path: str, page_index: int, rect_list: Sequence[tuple[float, float, float, float]],
) -> str | None:
    """Return the finalizer's rectangle-text fingerprint without exposing text."""
    try:
        import fitz  # type: ignore
    except Exception as error:
        raise RectTextHashError("rect_text_hash_dependency_unavailable", error) from error
    try:
        doc = fitz.open(pdf_path)
    except Exception as error:
        raise RectTextHashError("rect_text_hash_open_failed", error) from error
    try:
        if isinstance(page_index, bool) or not isinstance(page_index, int) or not 0 <= page_index < doc.page_count:
            raise RectTextHashError("rect_text_hash_invalid_page")
        rects = [_rect_from_values(fitz, rect) for rect in rect_list]
        if any(rect is None for rect in rects):
            raise RectTextHashError("rect_text_hash_invalid_geometry")
        try:
            return _rect_text_hash(doc[page_index], [rect for rect in rects if rect is not None])
        except Exception as error:
            raise RectTextHashError("rect_text_hash_extract_failed", error) from error
    finally:
        doc.close()





def _redact_pdf_occurrences_native(
    pdf_path: str,
    output_pdf_path: str,
    occurrence_inputs: Sequence[OccurrenceRedactionInput],
    display_mode: str,
    expected_run_id: str,
    expected_document_sha256: str,
    expected_analysis_revision: int,
) -> dict[str, Any]:
    try:
        import fitz  # type: ignore
    except Exception as e:
        raise RuntimeError(f"PyMuPDF 미설치로 PDF 레닥션을 수행할 수 없습니다: {e}")
    _assert_fresh_staging_output(pdf_path, output_pdf_path)

    display_mode = normalize_display_mode(display_mode)
    doc = fitz.open(pdf_path)
    blocking_review_items: list[dict[str, Any]] = []
    unresolved_review_items: list[dict[str, Any]] = []
    action_items: list[dict[str, Any]] = []
    validated: list[_ValidatedOccurrence] = []
    try:
        seen_occurrence_ids: set[str] = set()
        source_document_sha256 = _source_pdf_sha256(pdf_path)
        if (not isinstance(expected_run_id, str) or not expected_run_id
                or not isinstance(expected_document_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_document_sha256) is None
                or source_document_sha256 != expected_document_sha256):
            blocking_review_items.append(_review_evidence("unknown", "trusted_document_run_identity_required"))
        if (
            isinstance(expected_analysis_revision, bool)
            or not isinstance(expected_analysis_revision, int)
            or expected_analysis_revision < 1
        ):
            blocking_review_items.append(_review_evidence("unknown", "trusted_analysis_revision_required"))
        for request in occurrence_inputs:
            if not isinstance(request, OccurrenceRedactionInput):
                blocking_review_items.append(_review_evidence("unknown", "invalid_occurrence_input"))
                continue
            if request.occurrence_id in seen_occurrence_ids:
                blocking_review_items.append(_review_evidence(
                    request.occurrence_id,
                    "duplicate_occurrence_id",
                    page_index=request.page_index,
                    category=request.category,
                    rects=request.rect_list,
                    expected_text_hash=request.expected_text_hash,
                ))
                continue
            seen_occurrence_ids.add(request.occurrence_id)
            if request.run_id != expected_run_id or request.document_sha256 != expected_document_sha256:
                blocking_review_items.append(_review_evidence(
                    request.occurrence_id,
                    "stale_occurrence_identity",
                    page_index=request.page_index,
                    category=request.category,
                    rects=request.rect_list,
                    expected_text_hash=request.expected_text_hash,
                ))
                continue
            if request.analysis_revision != expected_analysis_revision:
                blocking_review_items.append(_review_evidence(
                    request.occurrence_id,
                    "stale_analysis_revision",
                    page_index=request.page_index,
                    category=request.category,
                    rects=request.rect_list,
                    expected_text_hash=request.expected_text_hash,
                ))
                continue
            item, reason_code = _validate_occurrence_input(fitz, doc, request)
            if item is None:
                blocking_review_items.append(_review_evidence(
                    request.occurrence_id,
                    reason_code or "invalid_occurrence_input",
                    page_index=request.page_index,
                    category=request.category,
                    rects=request.rect_list,
                    expected_text_hash=request.expected_text_hash,
                ))
            else:
                validated.append(item)

        mutable_items = [item for item in validated if item.request.action in {"mask", "exclude"}]
        for item in validated:
            if item.request.action == "review":
                unresolved_review_items.append(_review_evidence(
                    item.request.occurrence_id,
                    "review_action_unresolved",
                    page_index=item.request.page_index,
                    count=len(item.rects),
                    category=item.request.category,
                    rects=item.request.rect_list,
                    expected_text_hash=item.request.expected_text_hash,
                ))

        # Every mutation decision, including exclusions, is source-hash verified.
        verified_inputs: list[_ValidatedOccurrence] = []
        for item in mutable_items:
            source_hash = _rect_text_hash(doc[item.request.page_index], item.rects)
            if source_hash is None or not hmac.compare_digest(source_hash, item.request.expected_text_hash.lower()):
                blocking_review_items.append(_review_evidence(
                    item.request.occurrence_id,
                    "expected_text_hash_mismatch",
                    page_index=item.request.page_index,
                    count=len(item.rects),
                    category=item.request.category,
                    rects=item.request.rect_list,
                    expected_text_hash=item.request.expected_text_hash,
                    observed_text_hash=source_hash,
                ))
            else:
                verified_inputs.append(item)
                if item.request.action == "exclude":
                    action_items.append({
                        "occurrence_id": item.request.occurrence_id,
                        "page": item.request.page_index,
                        "status": "excluded",
                        "reason_code": "occurrence_excluded",
                        "count": len(item.rects),
                        "raw_value_saved": False,
                    })
        # Review actions are nonfatal only after their immutable geometry and
        # source hash have been checked. They are deliberately never added to
        # the mutation list, so the corresponding text remains unmasked.
        for item in [item for item in validated if item.request.action == "review"]:
            source_hash = _rect_text_hash(doc[item.request.page_index], item.rects)
            if source_hash is None or not hmac.compare_digest(source_hash, item.request.expected_text_hash.lower()):
                blocking_review_items.append(_review_evidence(
                    item.request.occurrence_id,
                    "expected_text_hash_mismatch",
                    page_index=item.request.page_index,
                    count=len(item.rects),
                    category=item.request.category,
                    rects=item.request.rect_list,
                    expected_text_hash=item.request.expected_text_hash,
                    observed_text_hash=source_hash,
                ))

        mask_inputs = [item for item in verified_inputs if item.request.action == "mask"]

        if blocking_review_items:
            _remove_staging_output(output_pdf_path)
            return {
                "enabled": True,
                "status": "blocked",
                "output_file": None,
                "display_mode": display_mode,
                "occurrences_requested": len(occurrence_inputs),
                "occurrences_applied": 0,
                "annotations_added": 0,
                "review_items": blocking_review_items + unresolved_review_items + action_items,
                "verification": {"verified": False, "reason_code": "occurrence_intrinsic_verification_failed"},
            }

        if not verified_inputs:
            shutil.copyfile(pdf_path, output_pdf_path)
            unchanged = hmac.compare_digest(
                hashlib.sha256(Path(pdf_path).read_bytes()).digest(),
                hashlib.sha256(Path(output_pdf_path).read_bytes()).digest(),
            )
            if not unchanged:
                _remove_staging_output(output_pdf_path)
                return {
                    "enabled": True,
                    "status": "failed",
                    "output_file": None,
                    "display_mode": display_mode,
                    "occurrences_requested": len(occurrence_inputs),
                    "occurrences_applied": 0,
                    "annotations_added": 0,
                    "review_items": unresolved_review_items + action_items,
                    "verification": {"verified": False, "reason_code": "unchanged_staging_copy_mismatch"},
                }
            return {
                "enabled": True,
                "status": "applied",
                "output_file": output_pdf_path,
                "display_mode": display_mode,
                "occurrences_requested": len(occurrence_inputs),
                "occurrences_applied": 0,
                "annotations_added": 0,
                "review_items": unresolved_review_items + action_items,
                "verification": {"verified": True, "reason_code": "unchanged_staging_copy_verified"},
            }

        protected_hashes = [
            (item, _rect_text_hash(doc[item.request.page_index], item.protected_rects))
            for item in mask_inputs
            if item.protected_rects
        ]
        annotations_added = 0
        for item in mask_inputs:
            page = doc[item.request.page_index]
            for rect in item.rects:
                add_redaction_annotation(page, rect, "OCCURRENCE", display_mode)
                annotations_added += 1
        for page_index in {item.request.page_index for item in mask_inputs}:
            doc[page_index].apply_redactions()
        try:
            doc.save(output_pdf_path, garbage=4, deflate=True, clean=True)
        except Exception:
            _remove_staging_output(output_pdf_path)
            raise
    finally:
        doc.close()

    try:
        verify_doc = fitz.open(output_pdf_path)
        try:
            for item in mask_inputs:
                page = verify_doc[item.request.page_index]
                for rect in item.rects:
                    if _has_residual_text(page, (rect,)):
                        blocking_review_items.append(_review_evidence(
                            item.request.occurrence_id,
                            "residual_text_in_saved_rectangle",
                            page_index=item.request.page_index,
                            count=1,
                            category=item.request.category,
                            rects=item.request.rect_list,
                            expected_text_hash=item.request.expected_text_hash,
                        ))
            for item, before_hash in protected_hashes:
                after_hash = _rect_text_hash(verify_doc[item.request.page_index], item.protected_rects)
                if before_hash is None or after_hash is None or not hmac.compare_digest(before_hash, after_hash):
                    blocking_review_items.append(_review_evidence(
                        item.request.occurrence_id,
                        "protected_neighbor_changed",
                        page_index=item.request.page_index,
                        count=len(item.protected_rects),
                        category=item.request.category,
                        rects=item.request.rect_list,
                        expected_text_hash=item.request.expected_text_hash,
                    ))
        finally:
            verify_doc.close()
    except Exception:
        _remove_staging_output(output_pdf_path)
        raise

    verified = not blocking_review_items
    if not verified:
        _remove_staging_output(output_pdf_path)
    return {
        "enabled": True,
        "status": "applied" if verified else "failed",
        "output_file": output_pdf_path if verified else None,
        "display_mode": display_mode,
        "occurrences_requested": len(occurrence_inputs),
        "occurrences_applied": len(mask_inputs),
        "annotations_added": annotations_added,
        "review_items": blocking_review_items + unresolved_review_items + action_items,
        "verification": {
            "verified": verified,
            "reason_code": "occurrence_intrinsic_verified" if verified else "occurrence_intrinsic_verification_failed",
            "residual_occurrences": sum(item["reason_code"] == "residual_text_in_saved_rectangle" for item in blocking_review_items),
            "protected_neighbor_failures": sum(item["reason_code"] == "protected_neighbor_changed" for item in blocking_review_items),
        },
    }



class ScannedPdfRedactionError(RuntimeError):
    """레닥션 대상 문서가 텍스트 레이어 없는 스캔(이미지) PDF일 때 발생(E2-2).

    일반 예외와 구분해 명확한 사유를 표시할 수 있도록 ``reason_code`` 를 둔다.
    Rust/TS 계약은 바뀌지 않는다. 경계에서는 원문 예외 문자열을 공개하지 않고
    ``reason_code`` 로만 이 실패를 다른 네이티브 레닥션 실패와 구분해 집계한다.
    """

    reason_code = "scanned_pdf_no_text_layer"


SCANNED_PDF_ERROR_MESSAGE = (
    "스캔 PDF는 텍스트 레이어가 없어 자동 마스킹을 적용할 수 없습니다 — "
    "수동 마스킹 캔버스를 사용하세요."
)

# 워드 bbox 폴백 정규화(E2-1): 공백류(자간 삽입 포함) 제거 + 유니코드 대시 통일.
_FALLBACK_DASH_PAT = re.compile(f"[{_DASH_CHARS}]")
_FALLBACK_WS_PAT = re.compile("[\\s​‌‍﻿]+")

# R1(사후검증 퍼지 매칭): exact/compact search_for 가 놓친 "또 다른 표기 변형"의
# 잔존을 워드 시퀀스 정규화 매칭으로 잡되, 짧은 값의 우연 일치(오탐)를 막기 위한
# 정규화 문자열 최소 길이 가드. 숫자형 PII(주민/전화/계좌/카드/여권/사업자)는 모두
# 정규화 후 7자 이상이고, 주소·사건번호도 충분히 길다. 4자 미만은 사실상 2~3자
# 한글 이름뿐인데, 그 exact 형태는 이미 기존 exact/compact search_for 잔존 검사가
# 커버하므로 퍼지 그물은 이 밴드를 제외해 남은 본문 텍스트와의 우연 부분일치를
# 배제한다(검증 강화만; 완화 아님).
_RESIDUAL_FUZZY_MIN_NORMALIZED_LEN = 4


def _search_hit_is_partial(page: Any, rect: Any, variant: str) -> bool:
    """Reject exact-search hits embedded in a larger alphanumeric token."""
    target = _normalize_fallback_text(variant)
    if not target:
        return False
    for word in page.get_text("words"):
        word_rect = word[:4]
        if not (word_rect[0] <= rect.x0 and word_rect[1] <= rect.y0 and word_rect[2] >= rect.x1 and word_rect[3] >= rect.y1):
            continue
        text = _normalize_fallback_text(str(word[4]))
        if text == target:
            return False
        if target in text:
            start = text.find(target)
            end = start + len(target)
            if (start > 0 and text[start - 1].isalnum()) or (end < len(text) and text[end].isalnum()):
                return True
    return False


def _normalize_fallback_text(value: str) -> str:
    """search_for 가 실패한 값을 워드 시퀀스와 비교하기 위한 정규화.

    - 유니코드 대시류(‐‑‒–—―−－ 등)를 ASCII '-' 로 통일
    - 공백(자간 공백/개행 포함) 및 제로폭 문자 제거
    """
    unified = _FALLBACK_DASH_PAT.sub("-", value)
    return _FALLBACK_WS_PAT.sub("", unified)


def _group_words_into_line_rects(fitz_module: Any, words: list[tuple[Any, ...]]) -> list[Any]:
    """워드 튜플 목록을 (block, line) 단위로 묶어 라인별 union rect 리스트로 변환.

    search_for 가 여러 줄에 걸친 구(phrase)를 줄 단위 rect 여러 개로 반환하는
    것과 동일한 의미론을 유지한다(과커버는 허용, 언더커버는 금지 — 매치된
    워드 전체를 통째로 rect 에 포함한다).
    """
    groups: dict[tuple[int, int], list[tuple[Any, ...]]] = {}
    order: list[tuple[int, int]] = []
    for w in words:
        key = (w[5], w[6])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(w)

    rects: list[Any] = []
    for key in order:
        group = groups[key]
        x0 = min(w[0] for w in group)
        y0 = min(w[1] for w in group)
        x1 = max(w[2] for w in group)
        y1 = max(w[3] for w in group)
        rects.append(fitz_module.Rect(x0, y0, x1, y1))
    return rects


@dataclass(frozen=True)
class _PageWordIndex:
    words: list[tuple[Any, ...]]
    spans: list[tuple[int, int]]
    concat: str


def _build_page_word_index(page: Any) -> _PageWordIndex:
    words = sorted(page.get_text("words"), key=lambda w: (w[5], w[6], w[7]))
    norm_words = [_normalize_fallback_text(w[4]) for w in words]
    spans: list[tuple[int, int]] = []
    cursor = 0
    for norm in norm_words:
        spans.append((cursor, cursor + len(norm)))
        cursor += len(norm)
    return _PageWordIndex(words=words, spans=spans, concat="".join(norm_words))


def _page_word_fallback_rect_groups(
    fitz_module: Any,
    page: Any,
    normalized_target: str,
    *,
    word_index: _PageWordIndex | None = None,
) -> list[list[Any]]:
    """search_for 로 찾지 못한 값을 페이지 워드 목록에서 유연 매칭으로 탐색(E2-1).

    페이지의 ``get_text("words")`` 결과를 읽기 순서로 정렬한 뒤, 각 워드를
    ``_normalize_fallback_text`` 로 정규화해 이어붙인 문자열에서
    ``normalized_target`` 의 모든(비중첩) 출현을 찾는다. 출현 하나당 걸쳐 있는
    워드들을 (block, line) 단위로 묶어 rect 그룹을 만들어 반환한다 — 값이 워드
    경계에 걸치거나 개행으로 끊긴 경우에도 시작/끝 워드를 통째로 포함해
    언더커버를 방지한다(과커버는 허용).
    """
    if len(normalized_target) < 2:
        return []

    index = word_index if word_index is not None else _build_page_word_index(page)
    if not index.words:
        return []
    if not index.concat:
        return []

    target_len = len(normalized_target)
    rect_groups: list[list[Any]] = []
    search_start = 0
    while True:
        idx = index.concat.find(normalized_target, search_start)
        if idx < 0:
            break
        match_end = idx + target_len

        first_word: int | None = None
        last_word: int | None = None
        partial_hit = False
        for i, (s, e) in enumerate(index.spans):
            if e <= idx:
                continue
            if s >= match_end:
                break
            if first_word is None:
                first_word = i
            last_word = i

            if first_word == last_word:
                word_start, word_end = index.spans[first_word]
                word_text = _normalize_fallback_text(str(index.words[first_word][4]))
                local_start = idx - word_start
                local_end = match_end - word_start
                if (local_start > 0 and word_text[local_start - 1].isalnum()) or (
                    local_end < len(word_text) and word_text[local_end].isalnum()
                ):
                    partial_hit = True
                    break
        if partial_hit:
            search_start = match_end
            continue
        if first_word is not None and last_word is not None:
            matched_words = index.words[first_word:last_word + 1]
            rect_groups.append(_group_words_into_line_rects(fitz_module, matched_words))

        search_start = match_end

    return rect_groups


def _document_has_no_text_layer(doc: Any) -> bool:
    for page_num in range(doc.page_count):
        if doc[page_num].get_text("text").strip():
            return False
    return True


def _document_has_any_image(doc: Any) -> bool:
    for page_num in range(doc.page_count):
        if doc[page_num].get_images(full=False):
            return True
    return False


def _redaction_search_terms(matches: list[RedactionMatch]) -> list[RedactionMatch]:
    seen: set[tuple[str, str]] = set()
    ordered: list[RedactionMatch] = []
    for item in sorted(matches, key=lambda x: (-len(x.text), x.tag, x.text)):
        key = (item.tag, item.text)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def _verify_redaction_output(
    fitz_module: Any,
    output_pdf_path: str,
    search_terms: list[RedactionMatch],
    display_mode: str,
) -> tuple[int, int, list[RedactionMatch], list[dict[str, Any]]]:
    """결과 PDF에서 잔존(residual) 검사 — exact/compact + 퍼지(정규화 워드 매칭).

    R1: 기존 사후검증은 exact/compact ``search_for`` 만 사용해, 레닥션 단계가 이미
    쓰는 정규화(자간/개행/유니코드 대시 무시) 워드 시퀀스 매칭보다 약했다. 따라서
    exact/compact 로 표면화되지 않는 "또 다른 표기 변형"의 잔존이 있어도
    ``verified=True`` 로 통과할 수 있었다. 여기서 레닥션 폴백과 동일한
    ``_page_word_fallback_rect_groups`` 를 결과 PDF 전 페이지에 적용해 그 갭을 닫는다.

    반환: (residual_hits, residual_fuzzy_hits, residual_terms, residual_review_items).
    - ``residual_hits``: 기존 의미 그대로 exact/compact search_for 잔존 rect 수.
    - ``residual_fuzzy_hits``: exact 로 이미 계수한 위치와 겹치지 않는(중복 제거)
      정규화 워드 매칭 잔존 출현 수(신규 카운트, 기존 필드 의미 불변).

    오탐 방지: (1) 정규화 문자열 길이 ``_RESIDUAL_FUZZY_MIN_NORMALIZED_LEN`` 미만은
    스킵(짧은 값 우연 일치 배제). (2) exact 잔존 rect 와 교차하는 퍼지 그룹은
    중복 계수하지 않음. (3) 라벨 모드에서 삽입한 라벨 텍스트(예 ``[NAME]``/``[이름]``)는
    마스킹 원본 값(PII)과 문자열이 다르므로 정규화 타깃과 매칭되지 않는다 — 별도
    라벨 제거 없이도 오탐이 되지 않는다.
    """
    residual_hits = 0
    residual_fuzzy_hits = 0
    residual_terms: list[RedactionMatch] = []
    residual_review_items: list[dict[str, Any]] = []

    verify_doc = fitz_module.open(output_pdf_path)
    page_word_indexes: dict[int, _PageWordIndex] = {}
    try:
        for item in search_terms:
            variants = [item.text]
            compact = re.sub(r"\s+", "", item.text)
            if compact and compact != item.text:
                variants.append(compact)
            normalized_target = _normalize_fallback_text(item.text)

            found = False
            exact_rects_by_page: dict[int, list[Any]] = {}
            for page_num in range(verify_doc.page_count):
                page = verify_doc[page_num]
                for variant in variants:
                    if len(variant) < 2:
                        continue
                    rects = [rect for rect in page.search_for(variant) if not _search_hit_is_partial(page, rect, variant)]
                    if rects:
                        residual_hits += len(rects)
                        found = True
                        for rect in rects:
                            exact_rects_by_page.setdefault(page_num, []).append(rect)
                            residual_review_items.append(
                                _review_item_for_rect(item, rect, page_num, "residual_found", display_mode)
                            )
                if found:
                    break

            # 퍼지 잔존 검사(R1): 레닥션 폴백과 동일한 정규화/워드 시퀀스 매칭을
            # 결과 PDF 전 페이지에 적용. exact 가 조기 종료(break)로 놓친 다른
            # 페이지의 잔존까지 포함해, exact 위치와 겹치지 않는 출현만 계수한다.
            if len(normalized_target) >= _RESIDUAL_FUZZY_MIN_NORMALIZED_LEN:
                term_fuzzy_hit = False
                for page_num in range(verify_doc.page_count):
                    page = verify_doc[page_num]
                    page_exact = exact_rects_by_page.get(page_num, [])
                    word_index = page_word_indexes.get(page_num)
                    if word_index is None:
                        word_index = _build_page_word_index(page)
                        page_word_indexes[page_num] = word_index
                    for rect_group in _page_word_fallback_rect_groups(
                        fitz_module,
                        page,
                        normalized_target,
                        word_index=word_index,
                    ):
                        if not rect_group:
                            continue
                        if page_exact and any(
                            any(rect.intersects(hit) for hit in page_exact) for rect in rect_group
                        ):
                            # exact/compact 가 이미 이 위치를 잔존으로 계수함 - 중복 방지.
                            continue
                        residual_fuzzy_hits += 1
                        term_fuzzy_hit = True
                        for rect in rect_group:
                            review_item = _review_item_for_rect(item, rect, page_num, "residual_found", display_mode)
                            review_item["match_source"] = "word_bbox_fallback"
                            residual_review_items.append(review_item)
                if term_fuzzy_hit:
                    found = True

            if found:
                residual_terms.append(item)
    finally:
        verify_doc.close()

    return residual_hits, residual_fuzzy_hits, residual_terms, residual_review_items


def redact_pdf_native(
    pdf_path: str,
    output_pdf_path: str,
    matches: list[RedactionMatch] | None = None,
    exclude_boxes: list[ManualRedactionBox] | None = None,
    display_mode: str = "black",
    transform_state: TransformState | None = None,
    occurrence_inputs: Sequence[OccurrenceRedactionInput] | None = None,
    expected_run_id: str | None = None,
    expected_document_sha256: str | None = None,
    expected_analysis_revision: int | None = None,
    profile: str | None = None,
    legal_compatibility: bool = False,
) -> dict[str, Any]:
    if occurrence_inputs is not None:
        return _redact_pdf_occurrences_native(
            pdf_path, output_pdf_path, occurrence_inputs, display_mode,
            expected_run_id or "", expected_document_sha256 or "", expected_analysis_revision,
        )
    if profile != "legal" or not legal_compatibility:
        raise ValueError("PUBLIC_OCCURRENCE_INPUTS_REQUIRED")
    matches = matches or []
    _assert_fresh_staging_output(pdf_path, output_pdf_path)
    try:
        import fitz  # type: ignore
    except Exception as e:
        raise RuntimeError(f"PyMuPDF 미설치로 PDF 레닥션을 수행할 수 없습니다: {e}")

    display_mode = normalize_display_mode(display_mode)
    pseudonym_state = transform_state if transform_state is not None else TransformState()
    search_terms = _redaction_search_terms(matches)
    if not search_terms:
        raise RuntimeError("PDF 레닥션 대상 문자열이 없어 레닥션을 건너뜁니다.")

    doc = fitz.open(pdf_path)
    exclusion_rects: dict[int, list[Any]] = {}
    if exclude_boxes:
        for box in exclude_boxes:
            if box.mode != "restore":
                continue
            exclusion_rects.setdefault(box.page_index, []).append(
                fitz.Rect(min(box.rect[0], box.rect[2]), min(box.rect[1], box.rect[3]), max(box.rect[0], box.rect[2]), max(box.rect[1], box.rect[3]))
            )
    try:
        annotations_added = 0
        rects_from_word_fallback = 0
        terms_hit: list[RedactionMatch] = []
        excluded_hits = 0
        review_items: list[dict[str, Any]] = []
        label_overlays: dict[int, list[tuple[Any, str]]] = {}
        pseudonym_overlays: dict[int, list[tuple[Any, str]]] = {}
        page_word_indexes: dict[int, _PageWordIndex] = {}

        for item in search_terms:
            found_for_term = False
            variants = [item.text]
            compact = re.sub(r"\s+", "", item.text)
            if compact and compact != item.text:
                variants.append(compact)
            normalized_target = _normalize_fallback_text(item.text)

            for page_num in range(doc.page_count):
                page = doc[page_num]
                page_hit_rects: list[Any] = []
                for variant in variants:
                    if len(variant) < 2:
                        continue
                    rects = page.search_for(variant)
                    if rects:
                        found_for_term = True
                    for rect in rects:
                        if _search_hit_is_partial(page, rect, variant):
                            continue
                        page_exclusions = exclusion_rects.get(page_num, [])
                        if page_exclusions and any(rect.intersects(ex) for ex in page_exclusions):
                            excluded_hits += 1
                            continue
                        label = add_redaction_annotation(page, rect, item.tag, display_mode)
                        if display_mode == "pseudonym":
                            pseudonym_overlays.setdefault(page_num, []).append(
                                (fitz.Rect(rect), pseudonym_value(item.tag, item.text, pseudonym_state))
                            )
                        elif label:
                            label_overlays.setdefault(page_num, []).append((fitz.Rect(rect), label))
                        annotations_added += 1
                        page_hit_rects.append(fitz.Rect(rect))
                        review_items.append(_review_item_for_rect(item, rect, page_num, "applied", display_mode))

                # E2-1: 워드 bbox 유연 매칭 폴백은 이 페이지에서 search_for 가
                # "하나라도" 찾았는지와 무관하게 항상 실행한다. 같은 페이지에
                # 같은 값이 서로 다른 표현으로 여러 번 나타나는 경우(예: 본문은
                # 정상 표기 "홍길동", 서명란은 자간 삽입 "홍 길 동")
                # page_hits > 0 이라는 이유로 나머지 출현을 건너뛰면 언더커버가
                # 발생한다 — search_for 가 이미 커버한 위치와 겹치는 폴백 rect는
                # 건너뛰어 중복 레닥션만 피한다. 과커버는 허용, 언더커버는 금지.
                word_index = page_word_indexes.get(page_num)
                if word_index is None and len(normalized_target) >= 2:
                    word_index = _build_page_word_index(page)
                    page_word_indexes[page_num] = word_index
                for rect_group in _page_word_fallback_rect_groups(
                    fitz,
                    page,
                    normalized_target,
                    word_index=word_index,
                ):
                    if not rect_group:
                        continue
                    if any(any(rect.intersects(hit) for hit in page_hit_rects) for rect in rect_group):
                        # search_for 가 이미 이 위치를 레닥션함 - 중복 방지.
                        continue
                    found_for_term = True
                    for rect in rect_group:
                        page_exclusions = exclusion_rects.get(page_num, [])
                        if page_exclusions and any(rect.intersects(ex) for ex in page_exclusions):
                            excluded_hits += 1
                            continue
                        label = add_redaction_annotation(page, rect, item.tag, display_mode)
                        if display_mode == "pseudonym":
                            pseudonym_overlays.setdefault(page_num, []).append(
                                (fitz.Rect(rect), pseudonym_value(item.tag, item.text, pseudonym_state))
                            )
                        elif label:
                            label_overlays.setdefault(page_num, []).append((fitz.Rect(rect), label))
                        annotations_added += 1
                        rects_from_word_fallback += 1
                        review_item = _review_item_for_rect(item, rect, page_num, "applied", display_mode)
                        review_item["match_source"] = "word_bbox_fallback"
                        review_items.append(review_item)

            if found_for_term:
                terms_hit.append(item)
            else:
                review_items.append(
                    {
                        "page": None,
                        "tag": item.tag,
                        "display_token": _display_token(item.tag, display_mode),
                        "status": "missing_pdf_rect",
                        "count": 1,
                        "raw_value_saved": False,
                    }
                )

        if annotations_added == 0:
            if _document_has_no_text_layer(doc) and _document_has_any_image(doc):
                raise ScannedPdfRedactionError(SCANNED_PDF_ERROR_MESSAGE)
            raise RuntimeError("검색 가능한 PDF 텍스트를 찾지 못해 네이티브 레닥션을 적용하지 못했습니다.")

        for page_num in range(doc.page_count):
            doc[page_num].apply_redactions()
            for rect, label in label_overlays.get(page_num, []):
                _insert_pdf_label(doc[page_num], rect, label)
            for rect, pseudonym in pseudonym_overlays.get(page_num, []):
                insert_pdf_pseudonym_label(doc[page_num], rect, pseudonym)

        doc.save(output_pdf_path, garbage=4, deflate=True, clean=True)
    except Exception:
        _remove_staging_output(output_pdf_path)
        raise
    finally:
        doc.close()

    # 사후 무결성 검증: 결과 PDF에서 잔존 문자열 재검색(exact/compact + 퍼지 R1).
    try:
        residual_hits, residual_fuzzy_hits, residual_terms, residual_review_items = _verify_redaction_output(
            fitz, output_pdf_path, search_terms, display_mode
        )
    except Exception:
        _remove_staging_output(output_pdf_path)
        raise
    review_items.extend(residual_review_items)

    missing_targets_count = max(len(search_terms) - len(terms_hit), 0)
    verified = residual_hits == 0 and residual_fuzzy_hits == 0 and missing_targets_count == 0
    status = "applied" if verified else "failed"
    reason = (
        "레닥션 적용 및 잔존 0건 검증 완료"
        if verified
        else "PDF 검색/레닥션 누락 또는 결과 PDF 잔존 항목이 있어 수동 검토가 필요합니다"
    )
    if not verified:
        _remove_staging_output(output_pdf_path)

    return {
        "enabled": True,
        "status": status,
        "output_file": output_pdf_path if verified else None,
        "display_mode": display_mode,
        "targets_requested": len(search_terms),
        "targets_hit": len(terms_hit),
        "missing_targets_count": missing_targets_count,
        "annotations_added": annotations_added,
        "rects_from_word_fallback": rects_from_word_fallback,
        "matched_terms_preview": [_display_token(item.tag, display_mode) for item in terms_hit[:20]],
        "excluded_hits": excluded_hits,
        "excluded_regions": sum(len(v) for v in exclusion_rects.values()),
        "review_items": review_items,
        "verification": {
            "residual_hits": residual_hits,
            "residual_fuzzy_hits": residual_fuzzy_hits,
            "residual_terms_preview": [_display_token(item.tag, display_mode) for item in residual_terms[:20]],
            "verified": verified,
            "reason": reason,
        },
    }


def _fresh_pdf_output_path(output_pdf_path: str) -> str:
    out_path = Path(output_pdf_path)
    if not out_path.exists():
        return str(out_path)
    for idx in range(2, 1000):
        candidate = out_path.with_name(f"{out_path.stem}_{idx}{out_path.suffix}")
        if not candidate.exists():
            return str(candidate)
    fd, tmp_path = tempfile.mkstemp(prefix=f"{out_path.stem}_", suffix=out_path.suffix or ".pdf", dir=str(out_path.parent or Path(".")))
    os.close(fd)
    os.unlink(tmp_path)
    return tmp_path


def _normalized_pdf_save_target(_source_pdf_path: str, output_pdf_path: str) -> tuple[str, str]:
    final_path = _fresh_pdf_output_path(output_pdf_path)
    fd, staging_path = tempfile.mkstemp(
        prefix=f"{Path(final_path).stem}_staging_",
        suffix=Path(final_path).suffix or ".pdf",
        dir=str(Path(final_path).parent or Path(".")),
    )
    os.close(fd)
    os.unlink(staging_path)
    return staging_path, final_path


def _finish_pdf_save(staging_path: str, final_path: str) -> str:
    os.replace(staging_path, final_path)
    return final_path


def apply_manual_actions_v1(
    source_pdf_path: str,
    output_pdf_path: str,
    actions: Sequence[ManualActionV1],
    *,
    expected_run_id: str,
    expected_document_sha256: str,
    expected_analysis_revision: int,
    display_mode: str = "black",
    raster_adapter: Any | None = None,
    ocr_adapter: Any | None = None,
    restore_source_pdf_path: str | None = None,
) -> dict[str, Any]:
    """Versioned staged manual application with fail-closed intrinsic checks.

    Scan adapters must implement ``render(path, page, *, dpi, color_profile)``,
    mask ``verify``/``no_residual`` checks, and ``verify_restore`` for restore
    actions.  All adapter uncertainty is a hard block.
    """
    _assert_fresh_staging_output(source_pdf_path, output_pdf_path)
    try:
        import fitz  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"PyMuPDF 미설치로 수동 레닥션을 수행할 수 없습니다: {exc}")

    doc = fitz.open(source_pdf_path)
    restore_doc = None
    if restore_source_pdf_path:
        try:
            restore_doc = fitz.open(restore_source_pdf_path)
        except Exception:
            restore_doc = None
    evidence: list[dict[str, Any]] = []
    valid: list[tuple[ManualActionV1, tuple[Any, ...], tuple[Any, ...], str | None]] = []
    before_scan: dict[str, Any] = {}
    restore_scan: dict[str, Any] = {}
    text_backed_scan: set[str] = set()
    protected_hashes: dict[str, str | None] = {}
    try:
        seen_action_ids: set[str] = set()
        source_document_sha256 = _source_pdf_sha256(source_pdf_path)
        if (
            not isinstance(expected_run_id, str)
            or not expected_run_id
            or not isinstance(expected_document_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_document_sha256) is None
            or source_document_sha256 != expected_document_sha256
            or isinstance(expected_analysis_revision, bool)
            or not isinstance(expected_analysis_revision, int)
            or expected_analysis_revision < 1
        ):
            evidence.append(_manual_review("unknown", "trusted_manual_identity_required"))
        for action in actions:
            action_id = getattr(action, "manual_action_id", "unknown")
            if not isinstance(action, ManualActionV1) or action.schema_version != "manual-action/v1":
                evidence.append(_manual_review(action_id, "invalid_manual_action_schema"))
                continue
            if action_id in seen_action_ids:
                evidence.append(_manual_review(action_id, "duplicate_manual_action_id", page_index=getattr(action, "page_index", None)))
                continue
            seen_action_ids.add(action_id)
            if (action.run_id != expected_run_id or action.document_sha256 != expected_document_sha256
                    or action.analysis_revision != expected_analysis_revision):
                evidence.append(_manual_review(action_id, "stale_manual_action_identity", page_index=action.page_index))
                continue
            if (
                not action_id
                or not action.run_id
                or re.fullmatch(r"[0-9a-f]{64}", action.document_sha256) is None
                or action.mode not in {"mask", "restore"}
                or action.source_kind not in {"text_pdf", "scan"}
                or action.coordinate_space != "pdf_points_top_left"
                or (
                    action.restore_authorization_hash is not None
                    and re.fullmatch(r"[0-9a-fA-F]{64}", action.restore_authorization_hash) is None
                )
            ):
                evidence.append(_manual_review(action_id, "invalid_manual_action"))
                continue
            if action.mode == "restore" and action.source_kind == "text_pdf" and (
                not action.linked_occurrence_id
                or not action.expected_text_hash
                or not action.restore_authorization_hash
                or action.protected_neighbor_refs
            ):
                evidence.append(_manual_review(action_id, "restore_authorization_missing", page_index=action.page_index))
                continue
            if action.mode == "mask" and action.restore_authorization_hash is not None:
                evidence.append(_manual_review(action_id, "mask_restore_authorization_mismatch", page_index=action.page_index))
                continue
            if not isinstance(action.analysis_revision, int) or action.analysis_revision < 1 or not 0 <= action.page_index < doc.page_count or not action.rect_list:
                evidence.append(_manual_review(action_id, "missing_manual_geometry", page_index=action.page_index))
                continue
            if action.mode == "restore" and (
                restore_doc is None
                or restore_source_pdf_path is None
                or not 0 <= action.page_index < restore_doc.page_count
            ):
                evidence.append(_manual_review(action_id, "restore_source_unavailable", page_index=action.page_index))
                continue
            rects = tuple(_rect_from_values(fitz, raw) for raw in action.rect_list)
            protected = tuple(_rect_from_values(fitz, raw) for raw in action.protected_neighbor_refs)
            if any(rect is None for rect in (*rects, *protected)):
                evidence.append(_manual_review(action_id, "invalid_manual_rect", page_index=action.page_index))
                continue
            page = doc[action.page_index]
            if any(not page.rect.contains(rect) for rect in (*rects, *protected)) or any(neighbor.intersects(rect) for neighbor in protected for rect in rects):
                evidence.append(_manual_review(action_id, "protected_neighbor_overlap", page_index=action.page_index))
                continue
            evidence_page = restore_doc[action.page_index] if action.mode == "restore" else page
            text_evidence = _rect_text_hash(evidence_page, rects)
            source_hash = (
                text_evidence
                if action.linked_occurrence_id or action.expected_text_hash
                else None
            )
            protected_hashes[action_id] = _rect_text_hash(page, protected) if protected else None
            if action.expected_text_hash and (
                source_hash is None
                or not hmac.compare_digest(source_hash, action.expected_text_hash.lower())
            ):
                evidence.append(_manual_review(action_id, "linked_occurrence_evidence_missing", page_index=action.page_index))
                continue
            if action.source_kind == "scan":
                if raster_adapter is None or ocr_adapter is None:
                    evidence.append(_manual_review(action_id, "scan_verification_adapter_unavailable", page_index=action.page_index))
                    continue
                try:
                    before_scan[action_id] = raster_adapter.render(source_pdf_path, action.page_index, dpi=300, color_profile="sRGB")
                    if action.mode == "restore":
                        restore_scan[action_id] = raster_adapter.render(
                            restore_source_pdf_path, action.page_index, dpi=300, color_profile="sRGB"
                        )
                    elif text_evidence is not None:
                        text_backed_scan.add(action_id)
                except Exception:
                    evidence.append(_manual_review(action_id, "scan_before_render_failed", page_index=action.page_index))
                    continue
            valid.append((action, rects, protected, source_hash))
        if not valid:
            evidence.append(_manual_review("unknown", "no_valid_manual_actions"))
            _remove_staging_output(output_pdf_path)
            return {"status": "blocked", "output_file": None, "actions_requested": len(actions), "actions_applied": 0, "review_items": evidence, "raw_value_saved": False}
        if evidence:
            _remove_staging_output(output_pdf_path)
            return {"status": "blocked", "output_file": None, "actions_requested": len(actions), "actions_applied": 0, "review_items": evidence, "raw_value_saved": False}
        for page_index in {action.page_index for action, *_ in valid}:
            page = doc[page_index]
            pending_masks = False
            for action, rects, _protected, _hash in valid:
                if action.page_index != page_index:
                    continue
                if action.mode == "mask":
                    for rect in rects:
                        add_redaction_annotation(
                            page, rect, MANUAL_REDACTION_TAG, normalize_display_mode(display_mode)
                        )
                    pending_masks = True
                    continue
                if pending_masks:
                    page.apply_redactions()
                    pending_masks = False
                source_page = restore_doc[page_index]
                current_hash = (
                    _rect_text_hash(page, rects)
                    if action.source_kind == "text_pdf"
                    else None
                )
                already_visible = (
                    action.expected_text_hash is not None
                    and current_hash is not None
                    and hmac.compare_digest(current_hash, action.expected_text_hash.lower())
                )
                for rect in rects:
                    source_clip = rect & source_page.rect
                    target_rect = fitz.Rect(
                        rect.x0, rect.y0, rect.x0 + source_clip.width, rect.y0 + source_clip.height
                    )
                    if action.source_kind == "text_pdf":
                        if not already_visible:
                            page.show_pdf_page(
                                target_rect, restore_doc, page_index, clip=source_clip, overlay=True
                            )
                    else:
                        pixmap = source_page.get_pixmap(
                            clip=source_clip, dpi=300, alpha=False
                        )
                        page.insert_image(
                            target_rect, pixmap=pixmap, overlay=True, keep_proportion=False
                        )
            if pending_masks:
                page.apply_redactions()
        try:
            doc.save(output_pdf_path, garbage=4, deflate=True, clean=True)
        except Exception:
            _remove_staging_output(output_pdf_path)
            raise
    finally:
        if restore_doc is not None:
            restore_doc.close()
        doc.close()

    try:
        verify_doc = fitz.open(output_pdf_path)
        try:
            for action, rects, protected, _hash in valid:
                page = verify_doc[action.page_index]
                if action.mode == "mask":
                    if _has_residual_text(page, rects):
                        evidence.append(_manual_review(
                            action.manual_action_id,
                            "residual_text_in_saved_rectangle",
                            page_index=action.page_index,
                            count=1,
                        ))
                elif action.source_kind == "text_pdf":
                    restored_hash = _rect_text_hash(page, rects)
                    if restored_hash is None:
                        evidence.append(_manual_review(
                            action.manual_action_id,
                            "restore_text_missing",
                            page_index=action.page_index,
                        ))
                    elif action.expected_text_hash and not hmac.compare_digest(
                        restored_hash,
                        action.expected_text_hash.lower(),
                    ):
                        evidence.append(_manual_review(
                            action.manual_action_id,
                            "restore_text_hash_mismatch",
                            page_index=action.page_index,
                        ))
                if protected and (
                    protected_hashes[action.manual_action_id] is None
                    or _rect_text_hash(page, protected) is None
                    or not hmac.compare_digest(protected_hashes[action.manual_action_id] or "", _rect_text_hash(page, protected) or "")
                ):
                    evidence.append(_manual_review(action.manual_action_id, "protected_neighbor_changed", page_index=action.page_index, count=len(protected)))
                if action.source_kind == "scan":
                    try:
                        after = raster_adapter.render(output_pdf_path, action.page_index, dpi=300, color_profile="sRGB")
                    except Exception:
                        evidence.append(_manual_review(action.manual_action_id, "scan_after_render_failed", page_index=action.page_index))
                        continue
                    try:
                        if action.mode == "restore":
                            verdict = raster_adapter.verify_restore(
                                before_scan[action.manual_action_id],
                                after,
                                restore_scan[action.manual_action_id],
                                rects,
                                protected,
                                boundary_px=2,
                            )
                        elif action.manual_action_id in text_backed_scan and hasattr(raster_adapter, "verify_text_mask"):
                            verdict = raster_adapter.verify_text_mask(
                                before_scan[action.manual_action_id],
                                after,
                                rects,
                                protected,
                                boundary_px=2,
                            )
                        else:
                            verdict = raster_adapter.verify(
                                before_scan[action.manual_action_id], after, rects, protected, boundary_px=2
                            )
                        if not isinstance(verdict, Mapping):
                            evidence.append(_manual_review(
                                action.manual_action_id,
                                "scan_raster_verification_malformed",
                                page_index=action.page_index,
                            ))
                            continue
                        failures = (
                            {
                                "target_matches_source": "scan_restore_source_mismatch",
                                "protected_ratio_ok": "scan_protected_roi_changed",
                                "no_connected_diff": "scan_connected_diff_into_protected_glyph",
                            }
                            if action.mode == "restore"
                            else {
                                "coverage_100": "scan_target_coverage_incomplete",
                                "protected_ratio_ok": "scan_protected_roi_changed",
                                "no_connected_diff": "scan_connected_diff_into_protected_glyph",
                            }
                        )
                        for field, reason_code in failures.items():
                            if verdict.get(field) is not True:
                                evidence.append(_manual_review(action.manual_action_id, reason_code, page_index=action.page_index))
                    except Exception:
                        evidence.append(_manual_review(action.manual_action_id, "scan_raster_verification_failed", page_index=action.page_index))
                        continue
                    if action.mode == "mask":
                        try:
                            if ocr_adapter.no_residual(after, rects) is not True:
                                evidence.append(_manual_review(action.manual_action_id, "scan_target_ocr_residual", page_index=action.page_index))
                        except Exception:
                            evidence.append(_manual_review(action.manual_action_id, "scan_ocr_verification_failed", page_index=action.page_index))
        finally:
            verify_doc.close()
    except Exception:
        _remove_staging_output(output_pdf_path)
        raise
    if evidence:
        _remove_staging_output(output_pdf_path)
    return {
        "status": "applied" if not evidence else "failed",
        "output_file": output_pdf_path if not evidence else None,
        "actions_requested": len(actions),
        "actions_applied": len(valid) if not evidence else 0,
        "review_items": evidence,
        "text_check": "linked_occurrence" if any(action.expected_text_hash for action, *_ in valid) else "not_applicable",
        "mask_actions_applied": sum(action.mode == "mask" for action, *_ in valid) if not evidence else 0,
        "restore_actions_applied": sum(action.mode == "restore" for action, *_ in valid) if not evidence else 0,
        "raw_value_saved": False,
        "verification": {"verified": not evidence, "reason_code": "manual_actions_intrinsically_verified" if not evidence else "manual_residual_verification_failed"},
    }

def apply_manual_redactions(
    source_pdf_path: str,
    output_pdf_path: str,
    boxes: list[ManualRedactionBox],
    display_mode: str = "black",
) -> dict[str, Any]:
    try:
        import fitz  # type: ignore
    except Exception as e:
        raise RuntimeError(f"PyMuPDF 미설치로 수동 레닥션을 수행할 수 없습니다: {e}")

    if not boxes:
        raise RuntimeError("저장할 수동 마스킹 박스가 없습니다.")

    display_mode = normalize_display_mode(display_mode)
    save_path, replace_target = _normalized_pdf_save_target(source_pdf_path, output_pdf_path)
    try:
        doc = fitz.open(source_pdf_path)
    except Exception:
        _remove_staging_output(save_path)
        raise
    grouped: dict[int, list[tuple[ManualRedactionBox, Any]]] = {}
    try:
        for box in boxes:
            if (
                not isinstance(box, ManualRedactionBox)
                or box.mode != "mask"
                or isinstance(box.page_index, bool)
                or not isinstance(box.page_index, int)
                or not 0 <= box.page_index < doc.page_count
                or not isinstance(box.rect, tuple)
            ):
                raise ValueError("MANUAL_BATCH_INVALID")
            rect = _rect_from_values(fitz, box.rect)
            if rect is None or rect.width < 2 or rect.height < 2 or not doc[box.page_index].rect.contains(rect):
                raise ValueError("MANUAL_BATCH_INVALID")
            grouped.setdefault(box.page_index, []).append((box, rect))

        label_overlays: dict[int, list[tuple[Any, str]]] = {}
        for page_index, page_boxes in grouped.items():
            page = doc[page_index]
            for box, rect in page_boxes:
                label = add_redaction_annotation(page, rect, normalize_redaction_tag(box.tag), display_mode)
                if label:
                    label_overlays.setdefault(page_index, []).append((fitz.Rect(rect), label))
        for page_index in grouped:
            doc[page_index].apply_redactions()
            for rect, label in label_overlays.get(page_index, []):
                _insert_pdf_label(doc[page_index], rect, label)
        doc.save(save_path, garbage=4, deflate=True, clean=True)
        if not os.path.isfile(save_path) or os.path.getsize(save_path) <= 0:
            raise RuntimeError("MANUAL_OUTPUT_INVALID")
    except Exception:
        _remove_staging_output(save_path)
        raise
    finally:
        doc.close()

    try:
        verify_doc = fitz.open(save_path)
        try:
            for page_index, page_boxes in grouped.items():
                page = verify_doc[page_index]
                for _box, rect in page_boxes:
                    if _rect_text_hash(page, (rect,)) is not None:
                        _remove_staging_output(save_path)
                        return {
                            "status": "failed",
                            "output_file": None,
                            "boxes_applied": 0,
                            "pages_touched": [],
                            "display_mode": display_mode,
                            "requires_revalidation": True,
                            "raw_value_saved": False,
                        }
        finally:
            verify_doc.close()
    except Exception:
        _remove_staging_output(save_path)
        raise
    try:
        final_path = _finish_pdf_save(save_path, replace_target)
    except Exception:
        _remove_staging_output(save_path)
        raise
    return {
        "status": "applied",
        "output_file": final_path,
        "boxes_applied": len(boxes),
        "pages_touched": sorted(grouped),
        "display_mode": display_mode,
        "requires_revalidation": False,
        "raw_value_saved": False,
        "verification": {"verified": True, "reason_code": "manual_rectangles_intrinsically_verified"},
    }


def apply_manual_pdf_corrections(
    source_pdf_path: str,
    original_pdf_path: str,
    output_pdf_path: str,
    boxes: list[ManualCorrectionBox],
    display_mode: str = "black",
) -> dict[str, Any]:
    """Apply the GUI's legacy mask/restore actions in a local staged flow.

    This compatibility API is intentionally not a trusted-finalize path: it
    accepts no run authority and therefore only publishes a fresh local
    preview after source/original immutability and rectangle verification.
    Trusted saves must use :func:`apply_manual_actions_v1`.
    """
    try:
        import pymupdf as fitz  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"PyMuPDF 미설치로 수동 보정을 수행할 수 없습니다: {exc}") from exc

    if not isinstance(boxes, list) or not boxes:
        raise RuntimeError("MANUAL_BATCH_INVALID")
    display_mode = normalize_display_mode(display_mode)
    source_pdf_path = str(source_pdf_path)
    original_pdf_path = str(original_pdf_path or "")
    requested_output = str(output_pdf_path)

    save_pdf, final_pdf = _normalized_pdf_save_target(source_pdf_path, requested_output)
    _assert_fresh_staging_output(source_pdf_path, final_pdf, original_pdf_path)
    source_hash = _source_pdf_sha256(source_pdf_path)
    original_hash = _source_pdf_sha256(original_pdf_path) if original_pdf_path else None

    try:
        doc = fitz.open(source_pdf_path)
        original_doc = fitz.open(original_pdf_path) if original_pdf_path else None
    except Exception:
        _remove_staging_output(save_pdf)
        raise

    page_ops: dict[int, list[tuple[int, str, Any, str]]] = {}
    seen_boxes: set[tuple[int, str, int, int, int, int]] = set()
    mask_count = 0
    restore_count = 0
    skipped_boxes = 0
    warnings: list[str] = []
    final_actions: dict[tuple[int, int, int, int, int], str] = {}

    def warn(index: int, reason: str) -> None:
        nonlocal skipped_boxes
        skipped_boxes += 1
        warnings.append(f"box {index} skipped: {reason}")

    try:
        for index, box in enumerate(boxes):
            if not isinstance(box, ManualCorrectionBox):
                warn(index, "invalid manual box")
                continue
            action = str(box.action or "").lower().strip()
            if action not in {"mask", "unmask"}:
                warn(index, "invalid mode")
                continue
            if (
                isinstance(box.page_index, bool)
                or not isinstance(box.page_index, int)
                or box.page_index < 0
                or box.page_index >= doc.page_count
            ):
                warn(index, "page out of range")
                continue
            if not isinstance(box.rect, tuple) or len(box.rect) != 4:
                warn(index, "invalid rectangle")
                continue
            try:
                coords = tuple(float(value) for value in box.rect)
            except (TypeError, ValueError):
                warn(index, "invalid rectangle")
                continue
            if not all(math.isfinite(value) for value in coords):
                warn(index, "invalid rectangle")
                continue
            x0, y0, x1, y1 = coords
            rect = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            if rect.width < 2 or rect.height < 2:
                warn(index, "invalid rectangle")
                continue
            key = (
                box.page_index,
                action,
                round(rect.x0 * 1000),
                round(rect.y0 * 1000),
                round(rect.x1 * 1000),
                round(rect.y1 * 1000),
            )
            if key in seen_boxes:
                warn(index, "duplicate manual box")
                continue
            inverse = (
                box.page_index,
                "unmask" if action == "mask" else "mask",
                key[2],
                key[3],
                key[4],
                key[5],
            )
            seen_boxes.discard(inverse)
            seen_boxes.add(key)
            final_actions[(box.page_index, key[2], key[3], key[4], key[5])] = action
            if action == "mask":
                mask_count += 1
            else:
                restore_count += 1
            tag = normalize_redaction_tag(str(box.tag or MANUAL_REDACTION_TAG))
            page_ops.setdefault(box.page_index, []).append((index, action, rect, tag))

        mask_applied = 0
        restore_applied = 0
        pending_mask_annots = 0
        pending_labels: list[tuple[Any, str]] = []
        restore_verifications: list[tuple[int, Any, bytes]] = []

        for page_index in sorted(page_ops):
            page = doc[page_index]
            page_rect = page.rect

            def flush_masks() -> None:
                nonlocal pending_mask_annots, mask_applied, pending_labels
                if not pending_mask_annots:
                    return
                page.apply_redactions()
                for label_rect, label in pending_labels:
                    _insert_pdf_label(page, label_rect, label)
                mask_applied += pending_mask_annots
                pending_mask_annots = 0
                pending_labels = []

            for box_index, action, rect, tag in page_ops[page_index]:
                clipped = rect & page_rect
                if clipped.width < 2 or clipped.height < 2:
                    warn(box_index, "clipped rectangle too small")
                    continue
                if action == "mask":
                    label = add_redaction_annotation(page, clipped, tag, display_mode)
                    if label:
                        pending_labels.append((fitz.Rect(clipped), label))
                    pending_mask_annots += 1
                    continue

                flush_masks()
                if original_doc is None or page_index >= original_doc.page_count:
                    raise RuntimeError("MANUAL_RESTORE_SOURCE_UNAVAILABLE")
                original_page = original_doc[page_index]
                source_clip = clipped & original_page.rect
                if source_clip.width < 2 or source_clip.height < 2:
                    raise RuntimeError("MANUAL_RESTORE_SOURCE_UNAVAILABLE")
                target_rect = fitz.Rect(
                    clipped.x0,
                    clipped.y0,
                    clipped.x0 + source_clip.width,
                    clipped.y0 + source_clip.height,
                )
                try:
                    # Keep the source PDF content searchable while the raster
                    # overlay makes the restored pixels deterministic.
                    page.show_pdf_page(target_rect, original_doc, page_index, clip=source_clip, overlay=True)
                    pix = original_page.get_pixmap(
                        clip=source_clip, matrix=fitz.Matrix(2.5, 2.5), alpha=False
                    )
                    page.insert_image(target_rect, pixmap=pix, overlay=True)
                except Exception as exc:
                    raise RuntimeError("MANUAL_RESTORE_FAILED") from exc
                restore_applied += 1
                restore_verifications.append((page_index, target_rect, bytes(pix.samples)))

            flush_masks()

        status = "applied" if mask_applied or restore_applied else "no_effect"
        if status == "no_effect":
            warnings.append("no valid manual boxes; unchanged preview was saved")
        doc.save(save_pdf, garbage=4, deflate=True, clean=True)
        if not os.path.isfile(save_pdf) or os.path.getsize(save_pdf) <= 0:
            raise RuntimeError("MANUAL_OUTPUT_INVALID")
    except Exception:
        _remove_staging_output(save_pdf)
        raise
    finally:
        doc.close()
        if original_doc is not None:
            original_doc.close()

    if _source_pdf_sha256(source_pdf_path) != source_hash or (
        original_hash is not None and _source_pdf_sha256(original_pdf_path) != original_hash
    ):
        _remove_staging_output(save_pdf)
        raise RuntimeError("MANUAL_SOURCE_CHANGED")

    try:
        verify_doc = fitz.open(save_pdf)
        try:
            for page_index, target_rect, expected_pixels in restore_verifications:
                key = (
                    page_index,
                    round(target_rect.x0 * 1000),
                    round(target_rect.y0 * 1000),
                    round(target_rect.x1 * 1000),
                    round(target_rect.y1 * 1000),
                )
                if final_actions.get(key) == "mask":
                    continue
                actual_pixels = verify_doc[page_index].get_pixmap(
                    clip=target_rect, matrix=fitz.Matrix(2.5, 2.5), alpha=False
                ).samples
                if actual_pixels != expected_pixels:
                    raise RuntimeError("MANUAL_RESTORE_VERIFICATION_FAILED")
            for (page_index, x0, y0, x1, y1), action in final_actions.items():
                if action != "mask":
                    continue
                # A display label is intentionally inserted inside a mask
                # rectangle.  The redaction annotation itself has removed the
                # source text; residual verification therefore only runs for
                # black/pseudonym output where no replacement label is expected.
                if display_mode in {"label_en", "label_ko"}:
                    continue
                if _rect_text_hash(verify_doc[page_index], (fitz.Rect(x0 / 1000, y0 / 1000, x1 / 1000, y1 / 1000),)) is not None:
                    raise RuntimeError("MANUAL_RESIDUAL_TEXT")
        finally:
            verify_doc.close()
    except Exception:
        _remove_staging_output(save_pdf)
        raise

    try:
        final_path = _finish_pdf_save(save_pdf, final_pdf)
    except Exception:
        _remove_staging_output(save_pdf)
        raise
    return {
        "status": status,
        "output_file": final_path,
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
        "raw_value_saved": False,
        "verification": {
            "verified": True,
            "reason_code": "manual_legacy_preview_verified",
        },
    }
