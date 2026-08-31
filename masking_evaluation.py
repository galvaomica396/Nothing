"""PII-safe independent gold-manifest validation and deterministic evaluation.

This module deliberately operates on annotations and hashes, never document text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

SCHEMA_VERSION = "IndependentGoldManifestV1"
GEOMETRY_POLICY_VERSION = "GeometryPolicyV1"
COORDINATE_SPACE = "pdf_points_top_left"
CANONICAL_PROFILES = frozenset({"internal_review", "official_dispatch", "mixed", "legal"})
_MANIFEST_FIELDS = {
    "schema_version", "geometry_policy_version", "coordinate_space", "profile",
    "policy_version", "document", "source_class", "form", "provenance", "pages",
    "segments", "regions", "occurrences", "negatives", "protected_neighbors",
    "annotation_completion", "annotation_status", "manifest_sha256",
}
_DOCUMENT_FIELDS = {"document_id", "input_sha256", "output_sha256"}
_PROVENANCE_FIELDS = {"author", "reviewer", "detector_output_imported"}
_AUTHOR_FIELDS = {"id"}
_REVIEWER_FIELDS = {"id", "decision", "adjudication"}
_PAGE_FIELDS = {"page_index", "width", "height", "raster_transform"}
_SEGMENT_FIELDS = {"id", "page_index", "type", "offsets", "state", "confirmation_source"}
_REGION_FIELDS = {"id", "page_index", "type", "rects", "rect_list", "state", "confirmation_source"}
_OCCURRENCE_FIELDS = {
    "id", "segment_id", "region_id", "page_index", "category", "offsets",
    "text_hash", "ocr_confidence", "rects", "rect_list", "required", "disposition",
}
_NEGATIVE_FIELDS = {"id", "page_index", "kind", "category", "rects", "rect_list"}
_PROTECTED_NEIGHBOR_FIELDS = {"id", "page_index", "rects", "rect_list"}
_OFFSETS_FIELDS = {"start", "end"}
_RECT_FIELDS = {"x0", "y0", "x1", "y1"}
_RASTER_TRANSFORM_FIELDS = {"dpi", "scale_x", "scale_y", "translate_x", "translate_y"}
_COMPLETION_FIELDS = {
    "pages", "segments", "regions", "occurrences", "negatives",
    "protected_neighbors",
}
_COUNTER_KEYS = frozenset({
    "tp", "fp", "fn", "detection_tp", "detection_fp", "detection_fn",
    "detection_name_tp", "detection_name_fp", "detection_name_fn",
    "automatic_tp", "automatic_fp", "automatic_fn",
    "automatic_name_tp", "automatic_name_fp", "automatic_name_fn",
    "detected_count", "target_count", "automatic_name_fp_count", "automatic_name_count",
    "automatic_candidate_count", "automatic_name_candidate_count",
    "candidate_occurrence_count", "gold_occurrence_count", "gold_name_count",
    "blocked_document_count", "failed_document_count", "analysis_failed", "review_candidate_count",
    "review_item_count", "common_only_segment_count", "product_region_count", "label_overlap_count",
    "protected_neighbor_overlap_count", "protected_neighbor_denominator", "document_count",
    "document_denominator", "page_denominator", "positive_denominator", "negative_denominator",
    "segment_denominator", "region_denominator", "occurrence_denominator", "issued_documents",
    "review_documents", "total_documents", "public_content_read_count", "oof_read_count",
    "final_lock_read_count", "holdout_read_count", "oof_run_count",
    "final_threshold_lock_count", "holdout_consumption_count", "report_generation_count",
    "body_occurrence_tp", "body_occurrence_fp", "body_occurrence_fn",
    "region_tp", "region_fp", "region_fn", "fixed_region_omission_count",
    "unscoped_fixed_region_omission_count", "body_gold_pii_count",
    "fixed_region_gold_pii_count", "unscoped_fixed_region_gold_pii_count",
})
_PUBLIC_OOF_REQUIRED_COUNT_KEYS = frozenset({
    "detection_tp", "detection_fp", "detection_fn",
    "automatic_tp", "automatic_fp", "automatic_fn",
    "automatic_name_tp", "automatic_name_fp", "automatic_name_fn",
    "protected_neighbor_overlap_count", "blocked_document_count",
    "body_occurrence_tp", "body_occurrence_fp", "body_occurrence_fn",
    "region_tp", "region_fp", "region_fn", "fixed_region_omission_count",
    "unscoped_fixed_region_omission_count",
})
@dataclass(frozen=True, slots=True)
class GeometryPolicyV1:
    """PDF-point geometry policy; masks retain separate rectangles per line."""

    epsilon_pt: float = 0.5
    version: str = GEOMETRY_POLICY_VERSION

    def faults(self, expected_rects: Sequence[Mapping[str, float]], applied_rects: Sequence[Mapping[str, float]],
               protected_rects: Sequence[Mapping[str, float]] = ()) -> list[str]:
        return geometry_faults(expected_rects, applied_rects, protected_rects, self.epsilon_pt)


@dataclass(frozen=True, slots=True)
class IndependentGoldManifestV1:
    """Thin typed entry point for the versioned JSON contract."""

    value: Mapping[str, Any]

    def validate(self, *, require_locked: bool = False) -> None:
        validate_manifest(self.value, require_locked=require_locked)




class ManifestValidationError(ValueError):
    """Raised when an independent-gold contract is invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical representation used for every manifest hash."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the hashable payload, omitting the non-self-hash lock field."""
    return {key: value for key, value in manifest.items() if key != "manifest_sha256"}


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return canonical_json_sha256(manifest_payload(manifest))


def _require(value: Mapping[str, Any], key: str, where: str) -> Any:
    if key not in value:
        raise ManifestValidationError(f"{where}.{key} is required")
    return value[key]


def _sha(value: Any, where: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ManifestValidationError(f"{where} must be a lowercase SHA-256")


def _id(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestValidationError(f"{where} must be a non-empty string")
    return value
def _closed_fields(value: Any, allowed: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not set(value).issubset(allowed):
        raise ManifestValidationError(f"{where} contains unsupported fields")
    return value


def _validate_closed_manifest_schema(manifest: Mapping[str, Any]) -> None:
    _closed_fields(manifest, _MANIFEST_FIELDS, "manifest")
    document = manifest.get("document")
    if document is not None:
        _closed_fields(document, _DOCUMENT_FIELDS, "document")
    provenance = manifest.get("provenance")
    if provenance is not None:
        provenance = _closed_fields(provenance, _PROVENANCE_FIELDS, "provenance")
        for field, allowed in (("author", _AUTHOR_FIELDS), ("reviewer", _REVIEWER_FIELDS)):
            if field in provenance:
                _closed_fields(provenance[field], allowed, f"provenance.{field}")
    completion = manifest.get("annotation_completion")
    if completion is not None:
        _closed_fields(completion, _COMPLETION_FIELDS, "annotation_completion")
    collections = (
        ("pages", _PAGE_FIELDS),
        ("segments", _SEGMENT_FIELDS),
        ("regions", _REGION_FIELDS),
        ("occurrences", _OCCURRENCE_FIELDS),
        ("negatives", _NEGATIVE_FIELDS),
        ("protected_neighbors", _PROTECTED_NEIGHBOR_FIELDS),
    )
    for collection_name, allowed in collections:
        collection = manifest.get(collection_name)
        if collection is None:
            continue
        if not isinstance(collection, list):
            raise ManifestValidationError(f"manifest.{collection_name} must be a list")
        for index, item in enumerate(collection):
            item = _closed_fields(item, allowed, f"{collection_name}[{index}]")
            if "offsets" in item:
                _closed_fields(item["offsets"], _OFFSETS_FIELDS, f"{collection_name}[{index}].offsets")
            if collection_name == "pages" and "raster_transform" in item:
                _closed_fields(
                    item["raster_transform"],
                    _RASTER_TRANSFORM_FIELDS,
                    f"pages[{index}].raster_transform",
                )
            for rect_field in ("rects", "rect_list"):
                rects = item.get(rect_field)
                if rects is None:
                    continue
                if not isinstance(rects, list):
                    raise ManifestValidationError(f"{collection_name}[{index}].{rect_field} must be a list")
                for rect_index, rect in enumerate(rects):
                    _closed_fields(
                        rect,
                        _RECT_FIELDS,
                        f"{collection_name}[{index}].{rect_field}[{rect_index}]",
                    )

def validate_sidecar_security(sidecar: object) -> None:
    """Validate closed schema, collection identities, and PII/path safety."""
    if not isinstance(sidecar, Mapping):
        raise ManifestValidationError("SIDECAR_SCHEMA_INVALID")
    for collection, code in (
        ("segments", "SIDECAR_DUPLICATE_SEGMENT_ID"),
        ("regions", "SIDECAR_DUPLICATE_REGION_ID"),
        ("occurrences", "SIDECAR_DUPLICATE_OCCURRENCE_ID"),
        ("negatives", "SIDECAR_DUPLICATE_NEGATIVE_ID"),
        ("protected_neighbors", "SIDECAR_DUPLICATE_PROTECTED_NEIGHBOR_ID"),
    ):
        values = sidecar.get(collection)
        if not isinstance(values, list):
            continue
        seen: set[str] = set()
        for item in values:
            identifier = item.get("id") if isinstance(item, Mapping) else None
            if not isinstance(identifier, str):
                continue
            if identifier in seen:
                raise ManifestValidationError(code)
            seen.add(identifier)
    try:
        validate_manifest(sidecar, require_locked=False)
    except ManifestValidationError as error:
        raise ManifestValidationError("SIDECAR_SCHEMA_INVALID") from error

    def reject_unsafe(value: object) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if (not isinstance(key, str) or key in {
                    "text", "raw_text", "path", "locator", "source_path", "file_path", "uri",
                }):
                    raise ManifestValidationError("SIDECAR_FORBIDDEN_RAW_FIELD")
                reject_unsafe(nested)
        elif isinstance(value, list):
            for nested in value:
                reject_unsafe(nested)
        elif isinstance(value, str) and ("/" in value or "\\" in value):
            raise ManifestValidationError("SIDECAR_PATH_LIKE_VALUE")

    reject_unsafe(sidecar)

def _rect(rect: Any, page: Mapping[str, Any], where: str) -> tuple[float, float, float, float]:
    if not isinstance(rect, Mapping):
        raise ManifestValidationError(f"{where} must be an object")
    try:
        x0, y0, x1, y1 = (float(rect[name]) for name in ("x0", "y0", "x1", "y1"))
    except (KeyError, TypeError, ValueError) as error:
        raise ManifestValidationError(f"{where} must contain numeric x0/y0/x1/y1") from error
    if not all(math.isfinite(number) for number in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        raise ManifestValidationError(f"{where} must be a non-empty finite rectangle")
    if x0 < 0 or y0 < 0 or x1 > float(page["width"]) or y1 > float(page["height"]):
        raise ManifestValidationError(f"{where} is outside its page")
    return x0, y0, x1, y1


def _rects(item: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    rects = item.get("rects", item.get("rect_list"))
    if not isinstance(rects, list) or not rects:
        raise ManifestValidationError("rect_list must be a non-empty list; union bboxes are not accepted")
    return rects


def _validate_offsets(item: Mapping[str, Any], where: str) -> tuple[int, int] | None:
    offsets = item.get("offsets")
    if offsets is None:
        return None
    if not isinstance(offsets, Mapping):
        raise ManifestValidationError(f"{where}.offsets must be an object")
    start, end = offsets.get("start"), offsets.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        raise ManifestValidationError(f"{where}.offsets must use a non-empty [start,end) range")
    return start, end


def _validate_confirmation_fields(item: Mapping[str, Any], where: str) -> None:
    state = item.get("state")
    source = item.get("confirmation_source")
    if state is not None and state not in {"confirmed", "review_required", "unconfirmed"}:
        raise ManifestValidationError(f"{where}.state is invalid")
    if source not in {None, "automatic", "user"}:
        raise ManifestValidationError(f"{where}.confirmation_source is invalid")


def validate_manifest(manifest: Mapping[str, Any], *, require_locked: bool = False) -> None:
    """Validate an ``IndependentGoldManifestV1`` without accessing a document."""
    if not isinstance(manifest, Mapping):
        raise ManifestValidationError("manifest must be an object")
    _validate_closed_manifest_schema(manifest)
    declared_hash = manifest.get("manifest_sha256")
    if declared_hash is not None:
        _sha(declared_hash, "manifest.manifest_sha256")
        if declared_hash != manifest_sha256(manifest):
            raise ManifestValidationError("manifest hash mismatch")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ManifestValidationError(f"schema_version must be {SCHEMA_VERSION}")
    if manifest.get("geometry_policy_version") != GEOMETRY_POLICY_VERSION:
        raise ManifestValidationError(f"geometry_policy_version must be {GEOMETRY_POLICY_VERSION}")
    if manifest.get("coordinate_space") != COORDINATE_SPACE:
        raise ManifestValidationError(f"coordinate_space must be {COORDINATE_SPACE}")
    _id(_require(manifest, "policy_version", "manifest"), "manifest.policy_version")
    profile = _id(_require(manifest, "profile", "manifest"), "manifest.profile")
    if profile not in CANONICAL_PROFILES:
        raise ManifestValidationError("manifest.profile must be a canonical document profile")
    document = _require(manifest, "document", "manifest")
    if not isinstance(document, Mapping):
        raise ManifestValidationError("manifest.document must be an object")
    _id(_require(document, "document_id", "document"), "document.document_id")
    _sha(_require(document, "input_sha256", "document"), "document.input_sha256")
    if "output_sha256" in document:
        _sha(document["output_sha256"], "document.output_sha256")
    provenance = _require(manifest, "provenance", "manifest")
    if not isinstance(provenance, Mapping):
        raise ManifestValidationError("manifest.provenance must be an object")
    author = provenance.get("author")
    reviewer = provenance.get("reviewer")
    if not isinstance(author, Mapping) or not isinstance(reviewer, Mapping):
        raise ManifestValidationError("provenance requires author and reviewer objects")
    author_id = _id(author.get("id"), "provenance.author.id")
    reviewer_id = _id(reviewer.get("id"), "provenance.reviewer.id")
    if author_id == reviewer_id:
        raise ManifestValidationError("author and reviewer must be different people")
    if provenance.get("detector_output_imported") is not False:
        raise ManifestValidationError("detector_output_imported must be explicitly false")
    if require_locked:
        if reviewer.get("decision") != "approved" or reviewer.get("adjudication") != "independent_review":
            raise ManifestValidationError("a verified lock requires independent reviewer approval")
        if manifest.get("annotation_status") != "reviewed_approved":
            raise ManifestValidationError("a verified lock requires reviewed_approved status")
        source_class = manifest.get("source_class")
        form = manifest.get("form")
        if source_class not in {"issued", "review", "synthetic"} or form != source_class:
            raise ManifestValidationError("a verified lock requires a valid source/form classification")
        expected_profile = {
            "issued": "official_dispatch",
            "review": "internal_review",
        }.get(source_class)
        if expected_profile is not None and profile != expected_profile:
            raise ManifestValidationError("a verified lock profile does not match its source class")

    pages = _require(manifest, "pages", "manifest")
    if not isinstance(pages, list) or (require_locked and not pages):
        raise ManifestValidationError("a locked manifest must contain at least one page")
    page_map: dict[int, Mapping[str, Any]] = {}
    for index, page in enumerate(pages):
        if not isinstance(page, Mapping) or page.get("page_index") != index:
            raise ManifestValidationError("pages must be ordered with contiguous 0-based page_index values")
        try:
            width, height = float(page["width"]), float(page["height"])
        except (KeyError, TypeError, ValueError) as error:
            raise ManifestValidationError(f"pages[{index}] needs numeric width and height") from error
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
            raise ManifestValidationError(f"pages[{index}] has invalid dimensions")
        raster_transform = page.get("raster_transform")
        if raster_transform is not None:
            if not isinstance(raster_transform, Mapping):
                raise ManifestValidationError(f"pages[{index}].raster_transform must be an object")
            for field in ("scale_x", "scale_y"):
                value = raster_transform.get(field)
                if (not isinstance(value, (int, float)) or isinstance(value, bool)
                        or not math.isfinite(float(value)) or float(value) <= 0):
                    raise ManifestValidationError(
                        f"pages[{index}].raster_transform.{field} must be a positive finite number"
                    )
            if "dpi" in raster_transform:
                dpi = raster_transform["dpi"]
                if (not isinstance(dpi, (int, float)) or isinstance(dpi, bool)
                        or not math.isfinite(float(dpi)) or float(dpi) <= 0):
                    raise ManifestValidationError(
                        f"pages[{index}].raster_transform.dpi must be a positive finite number"
                    )
            for field in ("translate_x", "translate_y"):
                if field in raster_transform:
                    value = raster_transform[field]
                    if (not isinstance(value, (int, float)) or isinstance(value, bool)
                            or not math.isfinite(float(value))):
                        raise ManifestValidationError(
                            f"pages[{index}].raster_transform.{field} must be a finite number"
                        )
        page_map[index] = page

    segments = _require(manifest, "segments", "manifest")
    regions = _require(manifest, "regions", "manifest")
    occurrences = _require(manifest, "occurrences", "manifest")
    for name, collection in (("segments", segments), ("regions", regions), ("occurrences", occurrences)):
        if not isinstance(collection, list):
            raise ManifestValidationError(f"manifest.{name} must be a list")
    segment_ids: set[str] = set()
    segment_map: dict[str, Mapping[str, Any]] = {}
    segments_by_page: dict[int, list[tuple[int, int]]] = {}
    for index, segment in enumerate(segments):
        if not isinstance(segment, Mapping):
            raise ManifestValidationError(f"segments[{index}] must be an object")
        segment_id = _id(segment.get("id"), f"segments[{index}].id")
        if segment_id in segment_ids:
            raise ManifestValidationError("duplicate segment id")
        segment_ids.add(segment_id)
        page_index = segment.get("page_index")
        if page_index not in page_map:
            raise ManifestValidationError("segment page_index is unknown")
        _id(segment.get("type"), f"segments[{index}].type")
        _validate_confirmation_fields(segment, f"segments[{index}]")
        offset_range = _validate_offsets(segment, f"segments[{index}]")
        if offset_range is None:
            raise ManifestValidationError(f"segments[{index}].offsets is required")
        start, end = offset_range
        segment_map[segment_id] = segment
        segments_by_page.setdefault(page_index, []).append((start, end))
    for page_index, ranges in segments_by_page.items():
        ordered = sorted(ranges)
        for (_, previous_end), (start, _) in zip(ordered, ordered[1:]):
            if start != previous_end:
                raise ManifestValidationError(
                    f"segments on page {page_index} must have non-overlapping contiguous offset coverage"
                )
    region_ids: set[str] = set()
    region_map: dict[str, Mapping[str, Any]] = {}
    for index, region in enumerate(regions):
        if not isinstance(region, Mapping):
            raise ManifestValidationError(f"regions[{index}] must be an object")
        region_id = _id(region.get("id"), f"regions[{index}].id")
        if region_id in region_ids:
            raise ManifestValidationError("duplicate region id")
        region_ids.add(region_id)
        page_index = region.get("page_index")
        if page_index not in page_map:
            raise ManifestValidationError("region page_index is unknown")
        _id(region.get("type"), f"regions[{index}].type")
        _validate_confirmation_fields(region, f"regions[{index}]")
        for rect_index, rect in enumerate(_rects(region)):
            _rect(rect, page_map[page_index], f"regions[{index}].rects[{rect_index}]")
        region_map[region_id] = region
    occurrence_ids: set[str] = set()
    for index, occurrence in enumerate(occurrences):
        if not isinstance(occurrence, Mapping):
            raise ManifestValidationError(f"occurrences[{index}] must be an object")
        occurrence_id = _id(occurrence.get("id"), f"occurrences[{index}].id")
        if occurrence_id in occurrence_ids:
            raise ManifestValidationError("duplicate occurrence id")
        occurrence_ids.add(occurrence_id)
        segment_id, region_id = occurrence.get("segment_id"), occurrence.get("region_id")
        if segment_id not in segment_ids:
            raise ManifestValidationError("occurrence must reference a known segment")
        segment = segment_map[segment_id]
        if region_id is None:
            if segment.get("type") != "body":
                raise ManifestValidationError("only body occurrences may omit region_id")
            region = None
        else:
            if region_id not in region_ids:
                raise ManifestValidationError("occurrence must reference a known region")
            region = region_map[region_id]
        _id(occurrence.get("category"), f"occurrences[{index}].category")
        if occurrence.get("page_index") not in page_map:
            raise ManifestValidationError("occurrence page_index is unknown")
        if occurrence["page_index"] != segment["page_index"]:
            raise ManifestValidationError("occurrence and segment must be on the same page")
        if region is not None and occurrence["page_index"] != region["page_index"]:
            raise ManifestValidationError("occurrence and region must be on the same page")
        occurrence_rects = _rects(occurrence)
        if not occurrence_rects:
            raise ManifestValidationError("occurrence rects must be a non-empty same-page list")
        for rect_index, rect in enumerate(occurrence_rects):
            _rect(rect, page_map[occurrence["page_index"]], f"occurrences[{index}].rects[{rect_index}]")
        occurrence_range = _validate_offsets(occurrence, f"occurrences[{index}]")
        segment_range = _validate_offsets(segment, f"segments[{index}]")
        if occurrence_range is not None and segment_range is not None and not (
                segment_range[0] <= occurrence_range[0] < occurrence_range[1] <= segment_range[1]):
            raise ManifestValidationError("occurrence offsets must be contained in its segment")
        if "text_hash" in occurrence:
            _sha(occurrence["text_hash"], f"occurrences[{index}].text_hash")
        confidence = _require(occurrence, "ocr_confidence", f"occurrences[{index}]")
        if confidence is not None and (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                                       or not math.isfinite(float(confidence)) or not 0 <= float(confidence) <= 1):
            raise ManifestValidationError(f"occurrences[{index}].ocr_confidence must be null or a value in [0,1]")
        if "required" in occurrence and occurrence["required"] is not True:
            raise ManifestValidationError(f"occurrences[{index}].required must be true")
        if "disposition" in occurrence and occurrence["disposition"] not in {"mask", "masked"}:
            raise ManifestValidationError(f"occurrences[{index}].disposition is invalid")

    negative_ids: set[str] = set()
    for negative in manifest.get("negatives", []):
        if not isinstance(negative, Mapping) or negative.get("page_index") not in page_map:
            raise ManifestValidationError("negative must belong to a known page")
        negative_id = _id(negative.get("id"), "negative.id")
        if negative_id in negative_ids:
            raise ManifestValidationError("duplicate negative id")
        negative_ids.add(negative_id)
        _id(negative.get("kind"), "negative.kind")
        if negative.get("kind") == "name" and negative.get("category") != "person_name":
            raise ManifestValidationError("name negative.category must be person_name")
        for rect_index, rect in enumerate(_rects(negative)):
            _rect(rect, page_map[negative["page_index"]], f"negative.rects[{rect_index}]")
    protected_neighbor_ids: set[str] = set()
    for neighbor in manifest.get("protected_neighbors", []):
        if not isinstance(neighbor, Mapping) or neighbor.get("page_index") not in page_map:
            raise ManifestValidationError("protected neighbor must belong to a known page")
        neighbor_id = _id(neighbor.get("id"), "protected_neighbor.id")
        if neighbor_id in protected_neighbor_ids:
            raise ManifestValidationError("duplicate protected neighbor id")
        protected_neighbor_ids.add(neighbor_id)
        for rect_index, rect in enumerate(_rects(neighbor)):
            _rect(rect, page_map[neighbor["page_index"]], f"protected_neighbor.rects[{rect_index}]")
    if require_locked:
        collections = ("pages", "segments", "regions", "occurrences", "negatives", "protected_neighbors")
        completion = manifest.get("annotation_completion")
        if not isinstance(completion, Mapping) or set(completion) != set(collections):
            raise ManifestValidationError("a verified lock requires exact completion attestations")
        if not segments:
            raise ManifestValidationError("a verified lock requires explicit segment coverage")
        for name in collections:
            expected = "completed" if manifest.get(name) else "none_confirmed"
            if completion.get(name) != expected:
                raise ManifestValidationError(f"a verified lock has invalid {name} completion")
    if require_locked and declared_hash is None:
        raise ManifestValidationError("a verified lock requires manifest_sha256")



def lock_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a verified, deeply detached content-addressed copy."""
    validate_manifest(manifest, require_locked=False)
    try:
        locked = json.loads(canonical_json_bytes(manifest))
    except (TypeError, ValueError) as error:
        raise ManifestValidationError("manifest must be canonical JSON") from error
    if not isinstance(locked, dict):
        raise ManifestValidationError("manifest root must be an object")
    locked.pop("manifest_sha256", None)
    locked["manifest_sha256"] = manifest_sha256(locked)
    validate_manifest(locked, require_locked=True)
    return locked
SPLIT_LOCK_VERSION = "IndependentGoldSplitLockV1"
SPLIT_ASSIGNMENT_ALGORITHM = "sha256-rank-round-robin-v1"


def _document_hashes(document_sha256s: Iterable[str]) -> list[str]:
    hashes = list(document_sha256s)
    if not hashes:
        raise ManifestValidationError("split lock requires at least one document hash")
    for index, value in enumerate(hashes):
        _sha(value, f"document_sha256s[{index}]")
    if len(set(hashes)) != len(hashes):
        raise ManifestValidationError("split lock document hashes must be unique")
    return sorted(hashes)


def deterministic_fold_assignment(document_sha256s: Iterable[str], *, fold_count: int = 5,
                                  split_seed: str = "public-document-pilot-v1") -> list[dict[str, Any]]:
    """Assign immutable document identities to balanced deterministic folds."""
    if not isinstance(fold_count, int) or fold_count < 2:
        raise ManifestValidationError("fold_count must be an integer of at least two")
    if not isinstance(split_seed, str) or not split_seed:
        raise ManifestValidationError("split_seed must be a non-empty string")
    ranked = sorted(
        _document_hashes(document_sha256s),
        key=lambda value: (hashlib.sha256(f"{split_seed}:{value}".encode("ascii")).hexdigest(), value),
    )
    return [{"document_sha256": value, "fold": index % fold_count} for index, value in enumerate(ranked)]


def split_lock_payload(split_lock: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in split_lock.items() if key != "split_sha256"}


def split_lock_sha256(split_lock: Mapping[str, Any]) -> str:
    return canonical_json_sha256(split_lock_payload(split_lock))


def create_split_lock(document_sha256s: Iterable[str], *, fold_count: int = 5,
                      split_seed: str = "public-document-pilot-v1") -> dict[str, Any]:
    """Create a content-addressed split lock without document content or labels."""
    hashes = _document_hashes(document_sha256s)
    lock = {
        "schema_version": SPLIT_LOCK_VERSION,
        "assignment_algorithm": SPLIT_ASSIGNMENT_ALGORITHM,
        "split_seed": split_seed,
        "fold_count": fold_count,
        "document_count": len(hashes),
        "document_sha256s": hashes,
        "assignments": deterministic_fold_assignment(hashes, fold_count=fold_count, split_seed=split_seed),
    }
    lock["split_sha256"] = split_lock_sha256(lock)
    return lock


def validate_split_lock(split_lock: Mapping[str, Any], *, require_locked: bool = True) -> None:
    """Verify deterministic assignments and the immutable split lock hash."""
    if not isinstance(split_lock, Mapping):
        raise ManifestValidationError("split lock must be an object")
    if split_lock.get("schema_version") != SPLIT_LOCK_VERSION:
        raise ManifestValidationError(f"split schema_version must be {SPLIT_LOCK_VERSION}")
    if split_lock.get("assignment_algorithm") != SPLIT_ASSIGNMENT_ALGORITHM:
        raise ManifestValidationError("unsupported split assignment algorithm")
    fold_count = split_lock.get("fold_count")
    seed = split_lock.get("split_seed")
    if not isinstance(fold_count, int) or fold_count < 2 or not isinstance(seed, str) or not seed:
        raise ManifestValidationError("split lock has invalid fold configuration")
    hashes = _document_hashes(_require(split_lock, "document_sha256s", "split lock"))
    if hashes != split_lock["document_sha256s"] or split_lock.get("document_count") != len(hashes):
        raise ManifestValidationError("split lock document identities are not canonical")
    expected = deterministic_fold_assignment(hashes, fold_count=fold_count, split_seed=seed)
    if split_lock.get("assignments") != expected:
        raise ManifestValidationError("split lock assignments are not deterministic")
    declared_hash = split_lock.get("split_sha256")
    if require_locked and declared_hash is None:
        raise ManifestValidationError("split lock requires split_sha256")
    if declared_hash is not None:
        _sha(declared_hash, "split_lock.split_sha256")
        if declared_hash != split_lock_sha256(split_lock):
            raise ManifestValidationError("split lock hash mismatch")


def rect_iou(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    x0, y0 = max(float(left["x0"]), float(right["x0"])), max(float(left["y0"]), float(right["y0"]))
    x1, y1 = min(float(left["x1"]), float(right["x1"])), min(float(left["y1"]), float(right["y1"]))
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = _area(left) + _area(right) - intersection
    return intersection / union if union else 0.0


def _area(rect: Mapping[str, float]) -> float:
    return max(0.0, float(rect["x1"]) - float(rect["x0"])) * max(0.0, float(rect["y1"]) - float(rect["y0"]))


def rect_coverage(expected: Mapping[str, float], applied: Sequence[Mapping[str, float]], epsilon: float = 0.5) -> float:
    """Coverage by a rect list; each expected rect remains independent (no union bbox)."""
    # Grid partition produces exact union coverage for axis-aligned rectangles.
    xs = {float(expected["x0"]), float(expected["x1"])}
    ys = {float(expected["y0"]), float(expected["y1"])}
    expanded = []
    for rect in applied:
        expanded.append({"x0": float(rect["x0"]) - epsilon, "y0": float(rect["y0"]) - epsilon,
                         "x1": float(rect["x1"]) + epsilon, "y1": float(rect["y1"]) + epsilon})
        xs.update((expanded[-1]["x0"], expanded[-1]["x1"]))
        ys.update((expanded[-1]["y0"], expanded[-1]["y1"]))
    x_values, y_values = sorted(xs), sorted(ys)
    covered = 0.0
    for x0, x1 in zip(x_values, x_values[1:]):
        for y0, y1 in zip(y_values, y_values[1:]):
            cell = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
            if rect_iou(cell, expected) > 0 and any(_contains(rect, cell) for rect in expanded):
                covered += _area(cell)
    return min(1.0, covered / _area(expected)) if _area(expected) else 0.0


def _contains(outer: Mapping[str, float], inner: Mapping[str, float]) -> bool:
    return all(float(outer[key]) <= float(inner[key]) for key in ("x0", "y0")) and all(float(outer[key]) >= float(inner[key]) for key in ("x1", "y1"))


def geometry_faults(expected_rects: Sequence[Mapping[str, float]], applied_rects: Sequence[Mapping[str, float]], protected_rects: Sequence[Mapping[str, float]] = (), epsilon: float = 0.5) -> list[str]:
    if not expected_rects or not applied_rects:
        return ["missing_geometry"]
    faults = ["incomplete_coverage" for rect in expected_rects if rect_coverage(rect, applied_rects, epsilon) < 1.0]
    for protected in protected_rects:
        shrunken = {"x0": float(protected["x0"]) + epsilon, "y0": float(protected["y0"]) + epsilon,
                    "x1": float(protected["x1"]) - epsilon, "y1": float(protected["y1"]) - epsilon}
        if _area(shrunken) and any(_intersection_area(shrunken, rect) > 0 for rect in applied_rects):
            faults.append("protected_neighbor_intrusion")
    return faults


def _intersection_area(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    return max(0.0, min(float(left["x1"]), float(right["x1"])) - max(float(left["x0"]), float(right["x0"]))) * max(0.0, min(float(left["y1"]), float(right["y1"])) - max(float(left["y0"]), float(right["y0"])))


def _item_rects(item: Mapping[str, Any]) -> list[Mapping[str, float]]:
    value = item.get("rects", item.get("rect_list", []))
    return value if isinstance(value, list) else []


def _compatible(
    gold: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    threshold: float,
    require_gold_coverage: bool = False,
) -> tuple[bool, float]:
    if gold.get("page_index") != candidate.get("page_index") or gold.get("category") != candidate.get("category"):
        return False, 0.0
    gold_text_hash = gold.get("text_hash")
    if isinstance(gold_text_hash, str) and gold_text_hash:
        candidate_text_hash = candidate.get("text_hash")
        if not isinstance(candidate_text_hash, str) or candidate_text_hash != gold_text_hash:
            return False, 0.0
    gold_rects, candidate_rects = _item_rects(gold), _item_rects(candidate)
    if gold_rects and candidate_rects:
        iou = max(rect_iou(left, right) for left in gold_rects for right in candidate_rects)
        coverage = min(rect_coverage(rect, candidate_rects, 0.0) for rect in gold_rects)
        if require_gold_coverage:
            if iou < threshold or coverage < 0.8:
                return False, 0.0
            return True, min(iou, coverage)
        if iou < threshold and coverage < 0.8:
            return False, 0.0
        return True, max(iou, coverage)
    if bool(gold_rects) != bool(candidate_rects):
        return False, 0.0
    gold_offsets, candidate_offsets = gold.get("offsets"), candidate.get("offsets")
    if isinstance(gold_offsets, Mapping) and isinstance(candidate_offsets, Mapping):
        overlap = min(gold_offsets["end"], candidate_offsets["end"]) - max(gold_offsets["start"], candidate_offsets["start"])
        return overlap > 0, float(overlap)
    return False, 0.0
def _negative_geometry_match(negative: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    if (
        negative.get("category") != candidate.get("category")
        or negative.get("page_index") != candidate.get("page_index")
    ):
        return False
    negative_rects, candidate_rects = _item_rects(negative), _item_rects(candidate)
    return bool(
        negative_rects
        and candidate_rects
        and any(
            _intersection_area(left, right) > 0
            for left in negative_rects
            for right in candidate_rects
        )
    )


def _negative_name_geometry_match(negative: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    return negative.get("kind") == "name" and _negative_geometry_match(negative, candidate)




def _metric_threshold(value: Any) -> float:
    """Validate a local metric cutoff without misclassifying type errors as protocol failures."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("threshold must be a real number")
    threshold = float(value)
    if not math.isfinite(threshold):
        raise ValueError("threshold must be finite")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between zero and one")
    return threshold


def one_to_one_match(
    gold: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    threshold: float = 0.5,
    require_gold_coverage: bool = False,
) -> list[tuple[int, int]]:
    """Deterministic maximum-cardinality matching with stable quality tie-breaks."""
    threshold = _metric_threshold(threshold)
    adjacency: dict[int, list[int]] = {}
    for gi, gold_item in enumerate(gold):
        options: list[tuple[float, str, int]] = []
        for ci, candidate in enumerate(candidates):
            matched, score = _compatible(
                gold_item,
                candidate,
                threshold=threshold,
                require_gold_coverage=require_gold_coverage,
            )
            if matched:
                options.append((-score, str(candidate.get("id", ci)), ci))
        adjacency[gi] = [ci for _, _, ci in sorted(options)]

    # Augmenting paths guarantee maximum cardinality.  Sorted input makes among
    # equivalent matchings choose geometry/lexical/ID-preferred candidates.
    candidate_to_gold: dict[int, int] = {}
    def augment(gi: int, seen: set[int]) -> bool:
        for ci in adjacency[gi]:
            if ci in seen:
                continue
            seen.add(ci)
            prior = candidate_to_gold.get(ci)
            if prior is None or augment(prior, seen):
                candidate_to_gold[ci] = gi
                return True
        return False

    for gi in sorted(range(len(gold)), key=lambda index: str(gold[index].get("id", index))):
        augment(gi, set())
    return sorted(((gi, ci) for ci, gi in candidate_to_gold.items()))


def metric(numerator: int, denominator: int) -> dict[str, int | float | None | str]:
    return {"value": numerator / denominator if denominator else None, "numerator": numerator, "denominator": denominator, "status": "ok" if denominator else "not_applicable"}


def evaluate_occurrences(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], negatives: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    positive = [item for item in gold if item.get("required", True) and item.get("disposition", "masked") in {"mask", "masked"}]
    matches = one_to_one_match(positive, candidates)
    matched_candidates = {candidate for _, candidate in matches}
    automatic = [item for item in candidates if item.get("action", "mask") == "mask"]
    negative_names = [
        item for item in negatives
        if item.get("kind") == "name" and item.get("category") == "person_name"
    ]
    false_auto = sum(any(_negative_name_geometry_match(negative, item) for item in automatic)
                     for negative in negative_names)
    false_alert = sum(any(_negative_name_geometry_match(negative, item) for item in candidates)
                      for negative in negative_names)
    false_positive = sum(any(_negative_geometry_match(negative, item) for item in candidates)
                         for negative in negatives)
    return {
        "tp": len(matches), "fp": len(candidates) - len(matched_candidates), "fn": len(positive) - len(matches),
        "recall": metric(len(matches), len(positive)),
        "false_positive_rate": metric(false_positive, len(negatives)),
        "name_auto_false_positive_rate": metric(false_auto, len(negative_names)),
        "name_false_alert_rate": metric(false_alert, len(negative_names)),
    }


def _required_mask_occurrence(item: Mapping[str, Any]) -> bool:
    return item.get("required", True) is not False and item.get("disposition", "masked") in {"mask", "masked"}


def _fixed_region_scope(region: Mapping[str, Any], segment: Mapping[str, Any] | None) -> bool:
    region_state = region.get("state")
    region_source = region.get("confirmation_source")
    if region_source == "user":
        return True
    if region_state in {"review_required", "unconfirmed"}:
        return False
    if region_state == "confirmed" and region_source == "automatic":
        return True
    if segment is None:
        return False
    source = segment.get("confirmation_source")
    return source == "user" or (segment.get("state") == "confirmed" and source == "automatic")


def _fixed_region_occurrences(
    gold: Sequence[Mapping[str, Any]],
    regions: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], set[str], set[str]]:
    regions_by_id = {
        str(region["id"]): region
        for region in regions
        if isinstance(region.get("id"), str) and region.get("type") != "body"
    }
    segments_by_id = {
        str(segment["id"]): segment
        for segment in segments
        if isinstance(segment.get("id"), str)
    }
    scoped: list[Mapping[str, Any]] = []
    unscoped: list[Mapping[str, Any]] = []
    scoped_region_ids: set[str] = set()
    unscoped_region_ids: set[str] = set()
    for occurrence in gold:
        if not _required_mask_occurrence(occurrence):
            continue
        region_id = occurrence.get("region_id")
        if not isinstance(region_id, str) or region_id not in regions_by_id:
            continue
        region = regions_by_id[region_id]
        segment_id = occurrence.get("segment_id")
        segment = segments_by_id.get(segment_id) if isinstance(segment_id, str) else None
        if _fixed_region_scope(region, segment):
            scoped.append(occurrence)
            scoped_region_ids.add(region_id)
        else:
            unscoped.append(occurrence)
            unscoped_region_ids.add(region_id)
    return scoped, unscoped, scoped_region_ids, unscoped_region_ids


def evaluate_fixed_region_occurrences(
    gold: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    regions: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Measure fixed-region masking, excluding unconfirmed areas from the zero-omission target."""
    scoped, unscoped, scoped_region_ids, unscoped_region_ids = _fixed_region_occurrences(gold, regions, segments)
    # Exclude unlinked masks so lost region linkage remains a conservative omission,
    # while reporting their count instead of silently treating them as matched.
    unlinked_mask_candidate_count = sum(
        candidate.get("action", "mask") == "mask" and candidate.get("region_id") is None
        for candidate in candidates
    )
    scoped_candidates = [
        candidate for candidate in candidates
        if candidate.get("region_id") in scoped_region_ids and candidate.get("action", "mask") == "mask"
    ]
    unscoped_candidates = [
        candidate for candidate in candidates
        if candidate.get("region_id") in unscoped_region_ids and candidate.get("action", "mask") == "mask"
    ]
    scoped_result = evaluate_occurrences(scoped, scoped_candidates)
    unscoped_result = evaluate_occurrences(unscoped, unscoped_candidates)
    return {
        "region_tp_fp_fn": {
            "tp": scoped_result["tp"], "fp": scoped_result["fp"], "fn": scoped_result["fn"],
        },
        "fixed_region_omission_count": scoped_result["fn"],
        "fixed_region_gold_pii_count": len(scoped),
        "unscoped_fixed_region_gold_pii_count": len(unscoped),
        "unscoped_fixed_region_omission_count": unscoped_result["fn"],
        "unlinked_mask_candidate_count": unlinked_mask_candidate_count,
    }


def evaluate_body_occurrences(
    gold: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    negatives: Sequence[Mapping[str, Any]],
    regions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Measure body-only occurrences; fixed-region values are excluded from this target."""
    fixed_region_ids = {
        str(region["id"])
        for region in regions
        if isinstance(region.get("id"), str) and region.get("type") != "body"
    }
    return evaluate_occurrences(
        [item for item in gold if item.get("region_id") not in fixed_region_ids],
        [item for item in candidates if item.get("region_id") not in fixed_region_ids],
        negatives,
    )


def evaluate_regions(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """One-to-one region TP/FP/FN using type/page plus IoU>=.5 and coverage>=.8."""
    normalized_gold = [{**item, "category": item.get("type")} for item in gold]
    normalized_candidates = [{**item, "category": item.get("type")} for item in candidates]
    matches = one_to_one_match(
        normalized_gold,
        normalized_candidates,
        threshold=0.5,
        require_gold_coverage=True,
    )
    return {"tp": len(matches), "fp": len(candidates) - len(matches), "fn": len(gold) - len(matches)}


def _segment_pages(item: Mapping[str, Any]) -> set[int]:
    pages = item.get("page_indices")
    if isinstance(pages, list):
        return {page for page in pages if isinstance(page, int)}
    page = item.get("page_index")
    return {page} if isinstance(page, int) else set()


def evaluate_segments(gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Maximum-cardinality one-to-one segment TP/FP/FN by type and page-set IoU."""
    adjacency: dict[int, list[int]] = {}
    for gi, expected in enumerate(gold):
        expected_pages = _segment_pages(expected)
        choices: list[tuple[str, int]] = []
        for ci, actual in enumerate(candidates):
            actual_pages = _segment_pages(actual)
            union = expected_pages | actual_pages
            if expected.get("type") == actual.get("type") and union and len(expected_pages & actual_pages) / len(union) >= 0.8:
                choices.append((str(actual.get("id", ci)), ci))
        adjacency[gi] = [ci for _, ci in sorted(choices)]
    candidate_to_gold: dict[int, int] = {}

    def augment(gi: int, seen: set[int]) -> bool:
        for ci in adjacency[gi]:
            if ci in seen:
                continue
            seen.add(ci)
            prior = candidate_to_gold.get(ci)
            if prior is None or augment(prior, seen):
                candidate_to_gold[ci] = gi
                return True
        return False

    for gi in sorted(range(len(gold)), key=lambda index: str(gold[index].get("id", index))):
        augment(gi, set())
    matches = len(candidate_to_gold)
    return {"tp": matches, "fp": len(candidates) - matches, "fn": len(gold) - matches}



EVALUATION_PROTOCOL_VERSION = "IndependentEvaluationProtocolV1"
RECEIPT_VERSION = "ImmutableReceiptV2"
THRESHOLD_CALIBRATION_SCHEMA = "ImmutableThresholdCalibrationV2"
THRESHOLD_CALIBRATION_SCHEMA_LEGACY = "ImmutableThresholdCalibrationV1"


class ProtocolValidationError(ValueError):
    """Raised when a one-way evaluation artifact is absent, altered, or replayed."""


def _protocol_root(eval_root: str | Path) -> Path:
    root = Path(eval_root).expanduser().resolve()
    repository = Path(__file__).resolve().parent
    if root == repository or repository in root.parents or ".gjc" in root.parts:
        raise ProtocolValidationError("evaluation root must be local and outside the repository/.gjc")
    return root

def _protocol_run_marker(root: Path, stage: str, protocol_run_id: str, suffix: str) -> Path:
    if not isinstance(protocol_run_id, str) or not protocol_run_id or protocol_run_id == "unassigned":
        raise ProtocolValidationError("protocol run identity is invalid")
    digest = hashlib.sha256(protocol_run_id.encode("utf-8")).hexdigest()
    return root / f".{stage}-run-{digest}.{suffix}"


def _safe_relative(root: Path, path: str | Path) -> str:
    try:
        return str(Path(path).expanduser().resolve().relative_to(root))
    except ValueError as error:
        raise ProtocolValidationError("artifact path must live below eval_root") from error


def _artifact_reference(root: Path, path: str | Path) -> dict[str, str]:
    target = Path(path).expanduser()
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    if not target.is_file():
        raise ProtocolValidationError("required artifact is missing")
    return {"path": _safe_relative(root, target), "sha256": file_sha256(target)}


def _verify_reference(root: Path, reference: Mapping[str, Any]) -> Path:
    if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
        raise ProtocolValidationError("artifact reference must have exactly path and sha256")
    if not isinstance(reference.get("path"), str):
        raise ProtocolValidationError("artifact reference must include a path")
    _sha(reference.get("sha256"), "artifact reference sha256")
    target = (root / reference["path"]).resolve()
    if _safe_relative(root, target) != reference["path"] or not target.is_file():
        raise ProtocolValidationError("referenced artifact is missing or escapes eval_root")
    if file_sha256(target) != reference["sha256"]:
        raise ProtocolValidationError("referenced artifact hash mismatch")
    return target


def _verified_artifact_path(root: Path, path: str | Path) -> Path:
    """Confine and require a regular artifact before its bytes are read."""
    return _verify_reference(root, _artifact_reference(root, path))


def _read_protocol_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolValidationError("artifact is not readable JSON") from error
    if not isinstance(value, dict):
        raise ProtocolValidationError("artifact root must be an object")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_immutable_bytes(path: Path, value: bytes) -> None:
    """Publish fully fsynced bytes once, without exposing a partial final artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
            published = True
        except FileExistsError as error:
            raise ProtocolValidationError("refusing to overwrite immutable artifact") from error
        _fsync_directory(path.parent)
    except Exception as error:
        cleanup_failure: OSError | None = None
        if published:
            try:
                path.unlink()
                _fsync_directory(path.parent)
            except OSError as cleanup_error:
                cleanup_failure = cleanup_error
        if temporary.exists():
            try:
                temporary.unlink()
                _fsync_directory(path.parent)
            except OSError as cleanup_error:
                cleanup_failure = cleanup_failure or cleanup_error
        if cleanup_failure is not None:
            raise ProtocolValidationError("immutable artifact publication indeterminate") from cleanup_failure
        if isinstance(error, ProtocolValidationError):
            raise
        raise ProtocolValidationError("immutable artifact publication failed") from error
    try:
        temporary.unlink()
        _fsync_directory(path.parent)
    except OSError as error:
        try:
            path.unlink()
            _fsync_directory(path.parent)
        except OSError as cleanup_error:
            raise ProtocolValidationError("immutable artifact publication indeterminate") from cleanup_error
        raise ProtocolValidationError("immutable artifact cleanup failed") from error


def _write_immutable_json(path: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    _write_immutable_bytes(path, canonical_json_bytes(value) + b"\n")
    return dict(value)


def _finite_rate(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProtocolValidationError(f"{name} must be a finite number")
    rate = float(value)
    if not 0.0 <= rate <= 1.0:
        raise ProtocolValidationError(f"{name} must be between zero and one")
    return rate


def _validated_counts(counts: Mapping[str, Any] | tuple) -> dict[str, int]:
    if counts == ():
        return {}
    if not isinstance(counts, Mapping):
        raise ProtocolValidationError("COUNT_EVIDENCE_REJECTED")
    normalized = dict(counts)
    if (
        any(not isinstance(key, str) or key not in _COUNTER_KEYS or isinstance(value, bool)
            or not isinstance(value, int) or value < 0 for key, value in normalized.items())
    ):
        raise ProtocolValidationError("COUNT_EVIDENCE_REJECTED")
    return dict(sorted(normalized.items()))


def _holdout_target_result(result: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, int], bool]:
    if not isinstance(result, Mapping):
        raise ProtocolValidationError("holdout evaluator result must be a mapping")
    counts = _validated_counts(result.get("counts", {}))
    measured = _validated_counts(result.get("measured_counters", {}))
    if measured != counts:
        raise ProtocolValidationError("holdout measured_counters must exactly bind counts")
    required = {"detected_count", "target_count", "automatic_name_fp_count", "automatic_name_count"}
    if not required <= counts.keys() or counts["target_count"] == 0 or counts["automatic_name_count"] == 0:
        raise ProtocolValidationError("holdout counts must provide non-zero target and automatic-name denominators")
    if counts["detected_count"] > counts["target_count"] or counts["automatic_name_fp_count"] > counts["automatic_name_count"]:
        raise ProtocolValidationError("holdout counts exceed their denominators")
    normalized_metrics = {
        "detection": counts["detected_count"] / counts["target_count"],
        "automatic_name_fp": counts["automatic_name_fp_count"] / counts["automatic_name_count"],
    }
    supplied = result.get("metrics")
    if supplied is not None:
        if not isinstance(supplied, Mapping) or set(supplied) != set(normalized_metrics):
            raise ProtocolValidationError("holdout metrics must exactly name count-derived metrics")
        for name, derived in normalized_metrics.items():
            if _finite_rate(supplied.get(name), f"holdout {name}") != derived:
                raise ProtocolValidationError("holdout metrics do not match counts")
    return normalized_metrics, counts, normalized_metrics["detection"] >= .99 and normalized_metrics["automatic_name_fp"] <= .05


def _validated_threshold_candidate(
    item: Mapping[str, Any],
    *,
    allow_empty_automatic_names: bool = False,
) -> tuple[float, float, float, float]:
    if not isinstance(item, Mapping) or set(item) != {
        "auto_threshold", "review_threshold", "detected_count", "target_count",
        "automatic_name_fp_count", "automatic_name_count",
    }:
        raise ProtocolValidationError("THRESHOLD_CANDIDATE_REJECTED")
    try:
        counts = _validated_counts({
            "detected_count": item.get("detected_count"),
            "target_count": item.get("target_count"),
            "automatic_name_fp_count": item.get("automatic_name_fp_count"),
            "automatic_name_count": item.get("automatic_name_count"),
        })
        if allow_empty_automatic_names and counts["automatic_name_count"] == 0:
            if (
                counts["target_count"] == 0
                or counts["detected_count"] > counts["target_count"]
                or counts["automatic_name_fp_count"] != 0
            ):
                raise ProtocolValidationError("threshold candidate counts exceed their denominators")
            metrics = {
                "detection": counts["detected_count"] / counts["target_count"],
                "automatic_name_fp": 0.0,
            }
        else:
            metrics, _, _ = _holdout_target_result(
                {"counts": counts, "measured_counters": counts}
            )
        auto_threshold = _finite_rate(item.get("auto_threshold"), "auto_threshold")
        review_threshold = _finite_rate(item.get("review_threshold"), "review_threshold")
    except ProtocolValidationError as error:
        raise ProtocolValidationError("THRESHOLD_CANDIDATE_REJECTED") from error
    if auto_threshold < review_threshold:
        raise ProtocolValidationError("THRESHOLD_CANDIDATE_REJECTED")
    return auto_threshold, review_threshold, metrics["detection"], metrics["automatic_name_fp"]
def _verified_candidate_evidence(root: Path, path: str | Path) -> tuple[dict[str, str], list[Mapping[str, Any]]]:
    reference = _artifact_reference(root, path)
    payload = _read_protocol_json(_verify_reference(root, reference))
    candidates = payload.get("threshold_candidates")
    schema_version = payload.get("schema_version")
    if schema_version == THRESHOLD_CALIBRATION_SCHEMA_LEGACY:
        if (
            set(payload) != {"schema_version", "immutable", "threshold_candidates"}
            or payload.get("immutable") is not True
            or not isinstance(candidates, list)
        ):
            raise ProtocolValidationError("THRESHOLD_CALIBRATION_SCHEMA_REJECTED")
    elif schema_version == THRESHOLD_CALIBRATION_SCHEMA:
        expected_fields = {
            "schema_version", "protocol_version", "producer_role", "immutable",
            "threshold_candidates", "calibration_sha256",
        }
        body = {key: value for key, value in payload.items() if key != "calibration_sha256"}
        if (
            set(payload) != expected_fields
            or payload.get("protocol_version") != EVALUATION_PROTOCOL_VERSION
            or not isinstance(payload.get("producer_role"), str) or not payload["producer_role"]
            or payload.get("immutable") is not True
            or payload.get("calibration_sha256") != canonical_json_sha256(body)
            or not isinstance(candidates, list)
        ):
            raise ProtocolValidationError("THRESHOLD_CALIBRATION_SCHEMA_REJECTED")
    else:
        raise ProtocolValidationError("THRESHOLD_CALIBRATION_SCHEMA_REJECTED")
    if not candidates:
        raise ProtocolValidationError("threshold candidates require immutable calibration evidence")
    for item in candidates:
        _validated_threshold_candidate(item)
    return reference, candidates
def _select_threshold_candidate(candidates: Sequence[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    if not candidates:
        raise ProtocolValidationError("threshold lock requires candidate evidence")
    return min(
        candidates,
        key=lambda candidate: (
            0 if candidate[2] >= .99 and candidate[3] <= .05 else 1,
            -candidate[2], candidate[3], candidate[0], candidate[1],
        ),
    )

def _receipt_artifact_reference(
    root: Path,
    item: str | Path | Mapping[str, Any],
    *,
    role: str,
) -> dict[str, str]:
    if isinstance(item, Mapping):
        path = item.get("path")
        kind = item.get("kind", "payload")
        if not isinstance(path, (str, Path)) or not isinstance(kind, str) or not kind:
            raise ProtocolValidationError("receipt artifact descriptor requires path and kind")
    else:
        path = item
        kind = "payload"
    return {**_artifact_reference(root, path), "role": role, "kind": kind}


_AUTHORIZATION_FIELDS = frozenset({
    "author_digest", "reviewer_digest", "role", "decision", "independent", "content_sha256",
})
_AUTHORIZATION_ROLES = frozenset({"annotator", "independent_reviewer", "adjudicator"})
_AUTHORIZATION_DECISIONS = frozenset({"approved", "reviewed", "adjudicated"})


def _validated_authorization(
    authorization: Mapping[str, Any] | None,
) -> dict[str, str | bool] | None:
    """Accept the closed, PII-free authorization record shared by gold producers."""
    if authorization is None:
        return None
    if not isinstance(authorization, Mapping) or not authorization or set(authorization) - _AUTHORIZATION_FIELDS:
        raise ProtocolValidationError("receipt authorization is invalid")
    safe = dict(authorization)
    for field in ("author_digest", "reviewer_digest"):
        if field in safe:
            _sha(safe[field], f"receipt authorization {field}")
    identity_fields = {field for field in ("author_digest", "reviewer_digest") if field in safe}
    role = safe.get("role")
    if role not in _AUTHORIZATION_ROLES:
        raise ProtocolValidationError("receipt authorization is invalid")
    expected_identity = "author_digest" if role == "annotator" else "reviewer_digest"
    if identity_fields != {expected_identity}:
        raise ProtocolValidationError("receipt authorization is invalid")
    if safe.get("decision") not in _AUTHORIZATION_DECISIONS:
        raise ProtocolValidationError("receipt authorization is invalid")
    independent = safe.get("independent")
    if not isinstance(independent, bool) or independent != (role == "independent_reviewer"):
        raise ProtocolValidationError("receipt authorization is invalid")
    if "content_sha256" not in safe:
        raise ProtocolValidationError("receipt authorization is invalid")
    _sha(safe["content_sha256"], "receipt authorization content_sha256")
    return safe


def make_protocol_receipt(*, eval_root: str | Path, output_path: str | Path, artifact_kind: str,
                          producer_role: str, status: str,
                          inputs: Sequence[str | Path | Mapping[str, Any]] = (),
                          counts: Mapping[str, int] = (), output_payload: str | Path | None = None,
                          output_payloads: Sequence[str | Path | Mapping[str, Any]] = (),
                          protocol_run_id: str, command: str = "library",
                          authorization: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write an immutable, content-addressed receipt without source text or PII."""
    root = _protocol_root(eval_root)
    receipt_path = Path(output_path).expanduser().resolve()
    _safe_relative(root, receipt_path)
    if (not artifact_kind or not producer_role or not status or not command
            or not isinstance(protocol_run_id, str) or not protocol_run_id or protocol_run_id == "unassigned"):
        raise ProtocolValidationError("stage, producer, status, non-sentinel protocolRunId, and command are required")
    count_map = _validated_counts(counts)
    requested_outputs: list[str | Path | Mapping[str, Any]] = list(output_payloads)
    if output_payload is not None:
        requested_outputs.append(output_payload)
    if not requested_outputs:
        raise ProtocolValidationError("receipt requires explicit stage output artifacts")
    value = {
        "schema": "ImmutableReceipt", "version": "V2", "stage": artifact_kind,
        "protocolRunId": protocol_run_id, "producer": {"role": producer_role}, "command": command,
        "timestamp": datetime.now(timezone.utc).isoformat(), "immutable": True, "status": status,
        "inputs": [_receipt_artifact_reference(root, item, role="input") for item in inputs],
        "outputs": [_receipt_artifact_reference(root, item, role="output") for item in requested_outputs],
        "counts": dict(sorted(count_map.items())), "protocol_version": EVALUATION_PROTOCOL_VERSION,
    }
    safe_authorization = _validated_authorization(authorization)
    if safe_authorization is not None:
        value["authorization"] = safe_authorization
    value["receipt_sha256"] = canonical_json_sha256(value)
    return _write_immutable_json(receipt_path, value)


def verify_protocol_receipt(eval_root: str | Path, path: str | Path, *, artifact_kind: str | None = None,
                            required_status: str | None = None) -> dict[str, Any]:
    root = _protocol_root(eval_root)
    raw_receipt_path = Path(path).expanduser()
    if raw_receipt_path.is_symlink():
        raise ProtocolValidationError("receipt path must not be a symlink")
    try:
        receipt_path = raw_receipt_path.resolve(strict=True)
    except OSError as error:
        raise ProtocolValidationError("receipt path is unavailable") from error
    _safe_relative(root, receipt_path)
    receipt = _read_protocol_json(receipt_path)
    required_fields = {
        "schema", "version", "stage", "protocolRunId", "producer", "command",
        "timestamp", "immutable", "status", "inputs", "outputs", "counts",
        "protocol_version", "receipt_sha256",
    }
    permitted = required_fields | {"authorization"}
    if set(receipt) != required_fields and set(receipt) != permitted:
        raise ProtocolValidationError("RECEIPT_SCHEMA_REJECTED")
    if (receipt.get("schema") != "ImmutableReceipt" or receipt.get("version") != "V2"
            or receipt.get("protocol_version") != EVALUATION_PROTOCOL_VERSION):
        raise ProtocolValidationError("unsupported receipt schema")
    outputs = receipt.get("outputs")
    if isinstance(outputs, list):
        for reference in outputs:
            path_key = reference.get("path") if isinstance(reference, Mapping) else None
            if isinstance(path_key, str) and (root / path_key).resolve() == receipt_path:
                raise ProtocolValidationError("RECEIPT_SELF_REFERENCE_REJECTED")
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt.get("immutable") is not True or receipt.get("receipt_sha256") != canonical_json_sha256(payload):
        raise ProtocolValidationError("receipt hash mismatch")
    if artifact_kind and receipt.get("stage") != artifact_kind:
        raise ProtocolValidationError("receipt stage mismatch")
    if required_status and receipt.get("status") != required_status:
        raise ProtocolValidationError("receipt status does not satisfy gate")
    required = ("protocolRunId", "command", "timestamp", "status")
    if any(not isinstance(receipt.get(key), str) or not receipt[key] for key in required):
        raise ProtocolValidationError("receipt common fields are missing")
    producer = receipt.get("producer")
    if not isinstance(producer, Mapping) or set(producer) != {"role"} or not isinstance(producer.get("role"), str) or not producer["role"]:
        raise ProtocolValidationError("receipt producer is missing")
    _validated_authorization(receipt.get("authorization"))
    inputs, outputs = receipt.get("inputs"), receipt.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list) or not outputs:
        raise ProtocolValidationError("receipt requires inputs and outputs")
    seen_paths: set[str] = set()
    for references, expected_role in ((inputs, "input"), (outputs, "output")):
        for reference in references:
            if (not isinstance(reference, Mapping) or set(reference) != {"path", "sha256", "role", "kind"}
                    or reference.get("role") != expected_role or not isinstance(reference.get("kind"), str)
                    or not reference["kind"]):
                raise ProtocolValidationError("receipt artifact metadata is invalid")
            path_key = reference.get("path")
            if not isinstance(path_key, str) or path_key in seen_paths:
                raise ProtocolValidationError("receipt contains duplicate artifact references")
            seen_paths.add(path_key)
            _verify_reference(root, {"path": path_key, "sha256": reference.get("sha256")})
    if receipt.get("stage") == "core_gate":
        if receipt.get("counts") != {"public_content_read_count": 0}:
            raise ProtocolValidationError("core gate requires public_content_read_count=0")
        if (len(outputs) != 1 or outputs[0].get("role") != "output"
                or outputs[0].get("kind") != "core_gate_output"):
            raise ProtocolValidationError("core gate artifact containers are invalid")
        _verify_core_gate_output(_verify_reference(root, {"path": outputs[0]["path"], "sha256": outputs[0]["sha256"]}))
    return receipt


def _verify_core_gate_output(target: Path) -> None:
    try:
        payload = json.loads(target.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolValidationError("CORE_GATE_OUTPUT_INVALID") from error
    if (not isinstance(payload, Mapping) or set(payload) != {"schema", "verified"}
            or payload.get("schema") != "CoreGateOutputV1" or payload.get("verified") is not True):
        raise ProtocolValidationError("CORE_GATE_OUTPUT_INVALID")


def _core_gate_output_reference(root: Path, output_payload: str | Path) -> dict[str, str]:
    reference = _artifact_reference(root, output_payload)
    _verify_core_gate_output(_verify_reference(root, reference))
    return reference


def write_core_gate_receipt(*, eval_root: str | Path, output_path: str | Path, producer_role: str,
                            inputs: Sequence[str | Path], public_content_read_count: int,
                            output_payload: str | Path, protocol_run_id: str) -> dict[str, Any]:
    if public_content_read_count != 0:
        raise ProtocolValidationError("core gate requires public_content_read_count=0")
    root = _protocol_root(eval_root)
    output = _core_gate_output_reference(root, output_payload)
    return make_protocol_receipt(eval_root=root, output_path=output_path, artifact_kind="core_gate",
                                 producer_role=producer_role, status="passed", inputs=inputs,
                                 counts={"public_content_read_count": 0},
                                 output_payload={"path": output["path"], "kind": "core_gate_output"},
                                 protocol_run_id=protocol_run_id)


def _validated_oof_prediction(prediction: Any) -> dict[str, Any]:
    allowed_fields = {"document_sha256", "profile", "status", "counts"}
    if not isinstance(prediction, Mapping) or set(prediction) != allowed_fields:
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    document_hash = prediction.get("document_sha256")
    if (not isinstance(document_hash, str) or len(document_hash) != 64
            or any(char not in "0123456789abcdef" for char in document_hash)):
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    profile = prediction.get("profile")
    if profile not in {"internal_review", "official_dispatch", "mixed"}:
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    status = prediction.get("status")
    if status not in {"evaluated", "failed_closed"}:
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    try:
        counts = _validated_counts(prediction.get("counts"))
    except ProtocolValidationError as error:
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED") from error
    if set(counts) != _PUBLIC_OOF_REQUIRED_COUNT_KEYS:
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    if (
        counts["automatic_tp"] > counts["detection_tp"]
        or counts["automatic_fn"] < counts["detection_fn"]
        or counts["automatic_name_tp"] > counts["automatic_tp"]
        or counts["automatic_name_fn"] > counts["automatic_fn"]
        or counts["automatic_name_fp"] > counts["automatic_fp"]
        or counts["fixed_region_omission_count"] != counts["region_fn"]
        or counts["blocked_document_count"] > 1
        or (status == "failed_closed" and (
            counts["detection_tp"] != 0
            or counts["detection_fp"] != 0
            or counts["automatic_tp"] != 0
            or counts["automatic_fp"] != 0
            or counts["automatic_name_tp"] != 0
            or counts["automatic_name_fp"] != 0
            or counts["body_occurrence_tp"] != 0
            or counts["body_occurrence_fp"] != 0
            or counts["region_tp"] != 0
            or counts["region_fp"] != 0
            or counts["protected_neighbor_overlap_count"] != 0
            or counts["blocked_document_count"] != 1
        ))
        or (status == "evaluated" and counts["blocked_document_count"] != 0)
    ):
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    return {
        "document_sha256": document_hash,
        "profile": profile,
        "status": status,
        "counts": counts,
    }


def create_synthetic_split_lock(*, eval_root: str | Path, output_path: str | Path,
                                calibration_sha256s: Iterable[str], holdout_sha256s: Iterable[str],
                                producer_role: str) -> dict[str, Any]:
    root = _protocol_root(eval_root)
    calibration, holdout = _document_hashes(calibration_sha256s), _document_hashes(holdout_sha256s)
    if set(calibration) & set(holdout) or not isinstance(producer_role, str) or not producer_role:
        raise ProtocolValidationError("calibration and untouched holdout require a distinct producer")
    value = {"schema_version": "SyntheticSplitLockV1", "protocol_version": EVALUATION_PROTOCOL_VERSION,
             "producer_role": producer_role, "calibration_sha256s": calibration,
             "untouched_holdout_sha256s": holdout, "immutable": True}
    value["split_sha256"] = canonical_json_sha256(value)
    output = Path(output_path).expanduser().resolve()
    _safe_relative(root, output)
    return _write_immutable_json(output, value)


def _verify_synthetic_split_lock(path: Path) -> dict[str, Any]:
    synthetic = _read_protocol_json(path)
    expected_fields = {
        "schema_version", "protocol_version", "producer_role", "calibration_sha256s",
        "untouched_holdout_sha256s", "immutable", "split_sha256",
    }
    payload = {key: value for key, value in synthetic.items() if key != "split_sha256"}
    if (
        set(synthetic) != expected_fields
        or synthetic.get("schema_version") != "SyntheticSplitLockV1"
        or synthetic.get("protocol_version") != EVALUATION_PROTOCOL_VERSION
        or not isinstance(synthetic.get("producer_role"), str) or not synthetic["producer_role"]
        or synthetic.get("immutable") is not True
        or synthetic.get("split_sha256") != canonical_json_sha256(payload)
    ):
        raise ProtocolValidationError("SYNTHETIC_SPLIT_SCHEMA_REJECTED")
    calibration = _document_hashes(synthetic.get("calibration_sha256s", ()))
    holdout = _document_hashes(synthetic.get("untouched_holdout_sha256s", ()))
    if (calibration != synthetic.get("calibration_sha256s") or holdout != synthetic.get("untouched_holdout_sha256s")
            or not holdout or set(calibration) & set(holdout)):
        raise ProtocolValidationError("synthetic split provenance is invalid")
    return synthetic


def _verify_threshold_lock(root: Path, path: Path) -> dict[str, Any]:
    threshold = _read_protocol_json(path)
    expected_fields = {
        "schema_version", "protocol_version", "producer_role", "protocol_run_id", "oof",
        "synthetic_split", "auto_threshold", "review_threshold", "target_shortfall",
        "selection_policy", "selection_evidence", "selection_evidence_sha256",
        "locked_once", "immutable", "threshold_sha256",
    }
    payload = {key: value for key, value in threshold.items() if key != "threshold_sha256"}
    if (
        set(threshold) != expected_fields
        or threshold.get("schema_version") != "FinalThresholdLockV2"
        or threshold.get("protocol_version") != EVALUATION_PROTOCOL_VERSION
        or not isinstance(threshold.get("producer_role"), str) or not threshold["producer_role"]
        or threshold.get("locked_once") is not True
        or threshold.get("immutable") is not True
        or threshold.get("threshold_sha256") != canonical_json_sha256(payload)
    ):
        raise ProtocolValidationError("FINAL_THRESHOLD_SCHEMA_REJECTED")
    oof_path = _verify_reference(root, threshold.get("oof", {}))
    oof = verify_oof(root, oof_path)
    evidence = threshold.get("selection_evidence")
    if not isinstance(evidence, list):
        raise ProtocolValidationError("final threshold lock binding is invalid")
    selected = _select_threshold_candidate([_validated_threshold_candidate(item) for item in evidence])
    auto_threshold, review_threshold, detection, _ = selected
    if (_finite_rate(threshold.get("auto_threshold"), "auto_threshold") != auto_threshold
            or _finite_rate(threshold.get("review_threshold"), "review_threshold") != review_threshold
            or threshold.get("target_shortfall") != max(0.0, .99 - detection)
            or threshold.get("selection_policy") != "attain_target_then_detection_then_automatic_name_fp_then_deterministic_pair"
            or threshold.get("protocol_run_id") != oof.get("protocol_run_id")
            or threshold.get("protocol_run_id") == "unassigned"
            or evidence != oof.get("threshold_candidates")
            or threshold.get("selection_evidence_sha256") != canonical_json_sha256(evidence)):
        raise ProtocolValidationError("final threshold lock binding is invalid")
    synthetic_path = _verify_reference(root, threshold.get("synthetic_split", {}))
    _verify_synthetic_split_lock(synthetic_path)
    replay_identity = canonical_json_sha256({
        "oof_sha256": file_sha256(oof_path),
        "synthetic_split_sha256": file_sha256(synthetic_path),
        "protocol_run_id": threshold["protocol_run_id"],
        "selection_evidence_sha256": canonical_json_sha256(evidence),
    })
    lease = _read_protocol_json(
        _verified_artifact_path(root, _protocol_run_marker(root, "final-threshold", threshold["protocol_run_id"], "lease.json"))
    )
    if (
        lease.get("schema_version") != "FinalThresholdLeaseV1"
        or lease.get("protocol_run_id") != threshold["protocol_run_id"]
        or lease.get("input_tuple_sha256") != replay_identity
        or lease.get("status") != "claimed"
        or lease.get("immutable") is not True
    ):
        raise ProtocolValidationError("final threshold protocol-run lease is invalid")
    return threshold


def _validated_fold_threshold_evidence(
    value: Any,
    *,
    fold: int,
    training_document_sha256s: Sequence[str],
    synthetic_candidates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_fields = {"training_document_sha256s", "threshold_candidates"}
    normalized_fields = raw_fields | {"fold", "evidence_sha256"}
    if (
        not isinstance(value, Mapping)
        or set(value) not in {frozenset(raw_fields), frozenset(normalized_fields)}
        or value.get("training_document_sha256s") != list(training_document_sha256s)
        or not isinstance(value.get("threshold_candidates"), list)
        or ("fold" in value and value.get("fold") != fold)
    ):
        raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
    public_candidates = [dict(item) for item in value["threshold_candidates"]]
    for item in public_candidates:
        _validated_threshold_candidate(item, allow_empty_automatic_names=True)
    synthetic_by_pair: dict[tuple[float, float], dict[str, Any]] = {}
    for item in synthetic_candidates:
        normalized = dict(item)
        pair = _validated_threshold_candidate(normalized)[:2]
        synthetic_by_pair[pair] = normalized
    public_by_pair = {
        _validated_threshold_candidate(item, allow_empty_automatic_names=True)[:2]: item
        for item in public_candidates
    }
    if (
        len(synthetic_by_pair) != len(synthetic_candidates)
        or len(public_by_pair) != len(public_candidates)
        or set(public_by_pair) != set(synthetic_by_pair)
    ):
        raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
    combined = []
    for pair in sorted(synthetic_by_pair, reverse=True):
        synthetic = synthetic_by_pair[pair]
        public = public_by_pair[pair]
        combined.append({
            "auto_threshold": pair[0],
            "review_threshold": pair[1],
            "detected_count": synthetic["detected_count"] + public["detected_count"],
            "target_count": synthetic["target_count"] + public["target_count"],
            "automatic_name_fp_count": (
                synthetic["automatic_name_fp_count"] + public["automatic_name_fp_count"]
            ),
            "automatic_name_count": (
                synthetic["automatic_name_count"] + public["automatic_name_count"]
            ),
        })
    normalized = {
        "fold": fold,
        "training_document_sha256s": list(training_document_sha256s),
        "threshold_candidates": [dict(item) for item in public_candidates],
    }
    normalized["evidence_sha256"] = canonical_json_sha256(normalized)
    if "evidence_sha256" in value and value.get("evidence_sha256") != normalized["evidence_sha256"]:
        raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
    return normalized, combined


def public_oof_once(*, eval_root: str | Path, output_path: str | Path, split_lock_path: str | Path,
                    public_gold_path: str | Path, core_gate_receipt: str | Path, producer_role: str,
                    predictions_by_fold: Mapping[int, Sequence[Mapping[str, Any]]] | None = None,
                    evaluator: Callable[..., Sequence[Mapping[str, Any]]] | None = None,
                    synthetic_calibration_path: str | Path | None = None,
                    threshold_candidates: Sequence[Mapping[str, Any]] = (),
                    fold_threshold_evidence: Mapping[int, Mapping[str, Any]] | None = None,
                    protocol_run_id: str) -> dict[str, Any]:
    """Persist held-out-only, count-only OOF artifacts after complete input validation."""
    supplied_candidates: list[dict[str, Any]] = []
    try:
        for candidate in threshold_candidates:
            if not isinstance(candidate, Mapping):
                raise ProtocolValidationError("THRESHOLD_CANDIDATE_REJECTED")
            _validated_threshold_candidate(candidate)
            supplied_candidates.append(dict(candidate))
    except (TypeError, ValueError) as error:
        raise ProtocolValidationError("THRESHOLD_CANDIDATE_REJECTED") from error
    root = _protocol_root(eval_root)
    core_receipt = verify_protocol_receipt(root, core_gate_receipt, artifact_kind="core_gate", required_status="passed")
    core_run_id = core_receipt["protocolRunId"]
    if (not isinstance(protocol_run_id, str) or not protocol_run_id
            or protocol_run_id == "unassigned" or core_run_id != protocol_run_id):
        raise ProtocolValidationError("OOF_CORE_GATE_BINDING_INVALID")
    split_path = _verify_reference(root, _artifact_reference(root, split_lock_path))
    split = _read_protocol_json(split_path)
    validate_split_lock(split)
    public_gold = _artifact_reference(root, public_gold_path)
    core_gate = _artifact_reference(root, core_gate_receipt)
    if synthetic_calibration_path is None:
        raise ProtocolValidationError("OOF threshold selection requires immutable calibration evidence")
    synthetic_calibration, immutable_candidates = _verified_candidate_evidence(root, synthetic_calibration_path)
    if supplied_candidates != [dict(item) for item in immutable_candidates]:
        raise ProtocolValidationError("OOF threshold candidates must exactly match immutable calibration evidence")
    selected_candidates = [dict(item) for item in immutable_candidates]
    expected = {item["document_sha256"]: item["fold"] for item in split["assignments"]}
    normalized_input_evidence: dict[int, Mapping[str, Any]] = {}
    if not isinstance(fold_threshold_evidence, Mapping):
        raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
    for key, evidence in fold_threshold_evidence.items():
        if isinstance(key, bool):
            raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
        if isinstance(key, str):
            if not key.isdecimal() or str(int(key)) != key:
                raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
        elif not isinstance(key, int):
            raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
        fold_key = int(key)
        if fold_key in normalized_input_evidence:
            raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
        normalized_input_evidence[fold_key] = evidence
    if set(normalized_input_evidence) != set(range(split["fold_count"])):
        raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
    normalized_fold_evidence: dict[int, dict[str, Any]] = {}
    combined_candidates_by_fold: dict[int, list[dict[str, Any]]] = {}
    for fold in range(split["fold_count"]):
        held_out = sorted(value for value, assigned in expected.items() if assigned == fold)
        training = sorted(value for value, assigned in expected.items() if assigned != fold)
        normalized, combined = _validated_fold_threshold_evidence(
            normalized_input_evidence[fold],
            fold=fold,
            training_document_sha256s=training,
            synthetic_candidates=selected_candidates,
        )
        if set(held_out) & set(normalized["training_document_sha256s"]):
            raise ProtocolValidationError("OOF_FOLD_THRESHOLD_EVIDENCE_REJECTED")
        normalized_fold_evidence[fold] = normalized
        combined_candidates_by_fold[fold] = combined
    output = Path(output_path).expanduser().resolve()
    _safe_relative(root, output)
    input_tuple = canonical_json_sha256({
        "split": file_sha256(split_path), "gold": public_gold["sha256"],
        "core_gate": core_gate["sha256"], "calibration": synthetic_calibration["sha256"],
        "fold_threshold_evidence_sha256": canonical_json_sha256([
            normalized_fold_evidence[fold] for fold in range(split["fold_count"])
        ]),
        "run_id": protocol_run_id,
    })
    lease = _protocol_run_marker(root, "public-oof", protocol_run_id, "lease.json")
    if output.exists() or lease.exists():
        raise ProtocolValidationError("OOF input tuple has already been leased")
    if evaluator is None and predictions_by_fold is not None and not isinstance(predictions_by_fold, Mapping):
        raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    if evaluator is None:
        supplied_hashes: set[str] = set()
        for fold in range(split["fold_count"]):
            held_out = sorted(value for value, assigned in expected.items() if assigned == fold)
            predictions = [_validated_oof_prediction(item) for item in (predictions_by_fold or {}).get(fold, ())]
            hashes = [item["document_sha256"] for item in predictions]
            if (sorted(hashes) != held_out or len(hashes) != len(set(hashes))
                    or any(expected.get(value) != fold or value in supplied_hashes for value in hashes)):
                raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
            supplied_hashes.update(hashes)
        if supplied_hashes != set(expected):
            raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    _write_immutable_json(lease, {
        "schema_version": "PublicOofLeaseV2",
        "status": "claimed",
        "protocol_run_id": protocol_run_id,
        "input_tuple_sha256": input_tuple,
        "immutable": True,
    })

    seen: set[str] = set()
    prepared: list[
        tuple[
            int, list[str], list[str], dict[str, float], dict[str, Any],
            list[dict[str, Any]], list[dict[str, Any]], Path, Path,
        ]
    ] = []
    raw_folds: list[
        tuple[
            int, list[str], list[str], dict[str, float], dict[str, Any],
            list[dict[str, Any]], list[Any],
        ]
    ] = []
    try:
        for fold in range(split["fold_count"]):
            held_out = sorted(hash_value for hash_value, assigned in expected.items() if assigned == fold)
            training = sorted(hash_value for hash_value, assigned in expected.items() if assigned != fold)
            combined_candidates = combined_candidates_by_fold[fold]
            auto_threshold, review_threshold, _, _ = _select_threshold_candidate([
                _validated_threshold_candidate(item) for item in combined_candidates
            ])
            selected = {
                "auto_threshold": auto_threshold,
                "review_threshold": review_threshold,
            }
            supplied = list(evaluator(
                fold=fold,
                held_out_document_sha256s=held_out,
                training_document_sha256s=training,
                thresholds=selected,
                actor="independent-fold-evaluator",
                capabilities=frozenset({"evaluate_held_out_fold"}),
            ) if evaluator else (predictions_by_fold or {}).get(fold, ()))
            raw_folds.append((
                fold,
                held_out,
                training,
                selected,
                normalized_fold_evidence[fold],
                combined_candidates,
                supplied,
            ))
        for fold, held_out, training, selected, fold_evidence, combined_candidates, supplied in raw_folds:
            predictions = [_validated_oof_prediction(prediction) for prediction in supplied]
            hashes: list[str] = []
            for prediction in predictions:
                document_hash = prediction["document_sha256"]
                if expected.get(document_hash) != fold or document_hash in seen:
                    raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
                seen.add(document_hash)
                hashes.append(document_hash)
            if sorted(hashes) != held_out:
                raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
            fold_dir = output.parent / f"fold-{fold}"
            threshold_path, predictions_path = fold_dir / "threshold.json", fold_dir / "predictions.jsonl"
            if threshold_path.exists() or predictions_path.exists():
                raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
            prepared.append((
                fold,
                held_out,
                training,
                selected,
                fold_evidence,
                combined_candidates,
                predictions,
                threshold_path,
                predictions_path,
            ))
        if seen != set(expected):
            raise ProtocolValidationError("OOF_PREDICTION_REJECTED")
    except ProtocolValidationError:
        failure = output.with_suffix(".failure.json")
        _write_immutable_json(failure, {"schema_version": "PublicOofFailureV2", "status": "rejected_prediction",
                                        "lease_sha256": file_sha256(lease), "input_tuple_sha256": input_tuple,
                                        "protocol_run_id": protocol_run_id, "immutable": True})
        raise
    except Exception as error:
        failure = output.with_suffix(".failure.json")
        _write_immutable_json(failure, {"schema_version": "PublicOofFailureV2", "status": "failed_execution",
                                        "lease_sha256": file_sha256(lease), "input_tuple_sha256": input_tuple,
                                        "protocol_run_id": protocol_run_id, "immutable": True})
        raise ProtocolValidationError("OOF_EVALUATOR_FAILED") from error

    # The lease is claimed before any evaluator access; all later failures leave
    # a durable consumption record and cannot trigger another held-out probe.
    folds = []
    try:
        for (
            fold,
            held_out,
            training,
            selected,
            fold_evidence,
            combined_candidates,
            predictions,
            threshold_path,
            predictions_path,
        ) in prepared:
            selection_inputs = {
                "synthetic_calibration": synthetic_calibration,
                "public_gold": public_gold,
                "public_training_document_sha256s": training,
                "held_out_document_sha256s": held_out,
                "synthetic_selection_evidence_sha256": canonical_json_sha256(
                    [dict(item) for item in immutable_candidates]
                ),
                "public_training_evidence_sha256": fold_evidence["evidence_sha256"],
                "combined_selection_evidence_sha256": canonical_json_sha256(
                    combined_candidates
                ),
            }
            selection_inputs["input_sha256"] = canonical_json_sha256(selection_inputs)
            _write_immutable_json(threshold_path, {
                "schema_version": "FoldThresholdV3", "fold": fold,
                "auto_threshold": selected["auto_threshold"], "review_threshold": selected["review_threshold"],
                "training_document_sha256s": training, "held_out_document_sha256s": held_out,
                "public_training_evidence": fold_evidence,
                "combined_selection_evidence": combined_candidates,
                "selection_inputs": selection_inputs, "immutable": True,
            })
            _write_immutable_bytes(predictions_path, b"".join(canonical_json_bytes(item) + b"\n" for item in predictions))
            folds.append({"fold": fold, "held_out_document_sha256s": held_out,
                          "training_document_sha256s": training, "threshold": _artifact_reference(root, threshold_path),
                          "predictions": _artifact_reference(root, predictions_path), "prediction_count": len(predictions)})
    except Exception as error:
        failure = output.with_suffix(".failure.json")
        _write_immutable_json(failure, {"schema_version": "PublicOofFailureV2", "status": "failed_execution",
                                        "lease_sha256": file_sha256(lease), "input_tuple_sha256": input_tuple,
                                        "protocol_run_id": protocol_run_id, "immutable": True})
        raise ProtocolValidationError("OOF failed after one-shot lease acquisition") from error
    value = {"schema_version": "PublicOofIndexV2", "protocol_version": EVALUATION_PROTOCOL_VERSION,
             "producer_role": producer_role, "protocol_run_id": protocol_run_id,
             "split_lock": _artifact_reference(root, split_path), "public_gold": public_gold,
             "core_gate_receipt": core_gate, "core_gate_receipt_sha256": core_receipt["receipt_sha256"],
             "synthetic_calibration": synthetic_calibration,
             "folds": folds, "threshold_candidates": [dict(item) for item in immutable_candidates],
             "document_count": len(seen), "status": "complete", "immutable": True}
    value["oof_sha256"] = canonical_json_sha256(value)
    return _write_immutable_json(output, value)


def verify_oof(eval_root: str | Path, oof_path: str | Path) -> dict[str, Any]:
    root = _protocol_root(eval_root)
    raw_oof_path = Path(oof_path).expanduser()
    if raw_oof_path.is_symlink():
        raise ProtocolValidationError("OOF index path must not be a symlink")
    try:
        verified_oof_path = raw_oof_path.resolve(strict=True)
    except OSError as error:
        raise ProtocolValidationError("OOF index path is unavailable") from error
    _safe_relative(root, verified_oof_path)
    value = _read_protocol_json(verified_oof_path)
    expected_fields = {
        "schema_version", "protocol_version", "producer_role", "protocol_run_id",
        "split_lock", "public_gold", "core_gate_receipt", "core_gate_receipt_sha256",
        "synthetic_calibration", "folds", "threshold_candidates", "document_count",
        "status", "immutable", "oof_sha256",
    }
    if (
        set(value) != expected_fields
        or value.get("schema_version") != "PublicOofIndexV2"
        or value.get("protocol_version") != EVALUATION_PROTOCOL_VERSION
        or value.get("status") != "complete"
        or value.get("immutable") is not True
        or not isinstance(value.get("producer_role"), str) or not value["producer_role"]
    ):
        raise ProtocolValidationError("OOF_SCHEMA_REJECTED")
    if value.get("oof_sha256") != canonical_json_sha256({key: item for key, item in value.items() if key != "oof_sha256"}):
        raise ProtocolValidationError("OOF index hash mismatch")
    split_path = _verify_reference(root, value.get("split_lock", {}))
    split = _read_protocol_json(split_path)
    validate_split_lock(split)
    public_gold_path = _verify_reference(root, value.get("public_gold", {}))
    core_receipt_path = _verify_reference(root, value.get("core_gate_receipt", {}))
    core_receipt = verify_protocol_receipt(
        root, core_receipt_path, artifact_kind="core_gate", required_status="passed",
    )
    core_run_id = core_receipt["protocolRunId"]
    recorded_receipt_hash = value.get("core_gate_receipt_sha256")
    candidates = value.get("threshold_candidates")
    if not isinstance(candidates, list):
        raise ProtocolValidationError("OOF threshold selection evidence is invalid")
    calibration_path = _verify_reference(root, value.get("synthetic_calibration", {}))
    calibration_reference, calibration_candidates = _verified_candidate_evidence(root, calibration_path)
    if (calibration_reference != value.get("synthetic_calibration")
            or [dict(item) for item in candidates] != [dict(item) for item in calibration_candidates]):
        raise ProtocolValidationError("OOF threshold evidence is not provenance-bound")
    if (value.get("protocol_run_id") != core_run_id or core_run_id == "unassigned"
            or recorded_receipt_hash != core_receipt["receipt_sha256"]):
        raise ProtocolValidationError("OOF_CORE_GATE_BINDING_INVALID")
    fold_evidence_digest = canonical_json_sha256([
        _read_protocol_json(_verify_reference(root, fold.get("threshold", {}))).get(
            "public_training_evidence"
        )
        for fold in value.get("folds", [])
    ])
    input_tuple = canonical_json_sha256({
        "split": file_sha256(split_path),
        "gold": file_sha256(public_gold_path),
        "core_gate": file_sha256(core_receipt_path),
        "calibration": calibration_reference["sha256"],
        "fold_threshold_evidence_sha256": fold_evidence_digest,
        "run_id": core_run_id,
    })
    lease_path = _protocol_run_marker(root, "public-oof", core_run_id, "lease.json")
    lease = _read_protocol_json(lease_path)
    if (
        lease.get("schema_version") != "PublicOofLeaseV2"
        or lease.get("status") != "claimed"
        or lease.get("protocol_run_id") != core_run_id
        or lease.get("input_tuple_sha256") != input_tuple
        or lease.get("immutable") is not True
    ):
        raise ProtocolValidationError("OOF protocol-run lease is invalid")
    expected = {item["document_sha256"]: item["fold"] for item in split["assignments"]}
    actual: dict[str, int] = {}
    for fold in value.get("folds", []):
        threshold_path = _verify_reference(root, fold.get("threshold", {}))
        predictions_path = _verify_reference(root, fold.get("predictions", {})
)
        threshold = _read_protocol_json(threshold_path)
        training = fold.get("training_document_sha256s")
        held_out = fold.get("held_out_document_sha256s")
        fold_evidence, combined_candidates = _validated_fold_threshold_evidence(
            threshold.get("public_training_evidence"),
            fold=fold.get("fold"),
            training_document_sha256s=training,
            synthetic_candidates=[dict(item) for item in candidates],
        )
        selected = _select_threshold_candidate([
            _validated_threshold_candidate(item) for item in combined_candidates
        ])
        expected_thresholds = {
            "auto_threshold": selected[0],
            "review_threshold": selected[1],
        }
        selection_inputs = threshold.get("selection_inputs")
        expected_selection_inputs = {
            "synthetic_calibration": calibration_reference,
            "public_gold": value.get("public_gold"),
            "public_training_document_sha256s": training,
            "held_out_document_sha256s": held_out,
            "synthetic_selection_evidence_sha256": canonical_json_sha256(
                [dict(item) for item in candidates]
            ),
            "public_training_evidence_sha256": fold_evidence["evidence_sha256"],
            "combined_selection_evidence_sha256": canonical_json_sha256(
                combined_candidates
            ),
        }
        expected_selection_inputs["input_sha256"] = canonical_json_sha256(expected_selection_inputs)
        if (
            threshold.get("schema_version") != "FoldThresholdV3"
            or threshold.get("fold") != fold.get("fold")
            or threshold.get("held_out_document_sha256s") != held_out
            or threshold.get("training_document_sha256s") != training
            or threshold.get("public_training_evidence") != fold_evidence
            or threshold.get("combined_selection_evidence") != combined_candidates
            or selection_inputs != expected_selection_inputs
            or {key: threshold.get(key) for key in expected_thresholds} != expected_thresholds
        ):
            raise ProtocolValidationError("OOF fold threshold is not bound to calibration and non-held-out public folds")
        prediction_hashes: list[str] = []
        try:
            for line in predictions_path.read_text(encoding="utf-8").splitlines():
                prediction = _validated_oof_prediction(json.loads(line))
                prediction_hashes.append(prediction["document_sha256"])
        except (OSError, json.JSONDecodeError) as error:
            raise ProtocolValidationError("OOF predictions are unreadable") from error
        held_out = fold.get("held_out_document_sha256s")
        if (not isinstance(held_out, list) or sorted(prediction_hashes) != held_out
                or fold.get("prediction_count") != len(prediction_hashes)):
            raise ProtocolValidationError("OOF prediction payload does not cover its held-out documents")
        for document_hash in held_out:
            if document_hash in actual or expected.get(document_hash) != fold.get("fold"):
                raise ProtocolValidationError("OOF fold coverage is invalid")
            if document_hash in fold.get("training_document_sha256s", []):
                raise ProtocolValidationError("OOF fold leaked held-out document into training")
            actual[document_hash] = fold["fold"]
    if actual != expected or value.get("document_count") != len(expected):
        raise ProtocolValidationError("OOF coverage is incomplete")
    return value


def lock_final_threshold(*, eval_root: str | Path, output_path: str | Path, oof_path: str | Path,
                         synthetic_lock_path: str | Path, producer_role: str,
                         candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    root = _protocol_root(eval_root)
    output = Path(output_path).expanduser().resolve()
    _safe_relative(root, output)
    if output.exists():
        raise ProtocolValidationError("refusing to overwrite immutable artifact")
    verified_oof = verify_oof(root, oof_path)
    synthetic_path = _verified_artifact_path(root, synthetic_lock_path)
    _verify_synthetic_split_lock(synthetic_path)
    if [dict(item) for item in candidates] != verified_oof.get("threshold_candidates"):
        raise ProtocolValidationError("threshold candidates must be exactly the verified OOF evidence")
    selected = _select_threshold_candidate([_validated_threshold_candidate(item) for item in candidates])
    auto_threshold, review_threshold, detection, _ = selected
    oof_reference = _artifact_reference(root, oof_path)
    synthetic_reference = _artifact_reference(root, synthetic_path)
    replay_identity = canonical_json_sha256({
        "oof_sha256": oof_reference["sha256"],
        "synthetic_split_sha256": synthetic_reference["sha256"],
        "protocol_run_id": verified_oof["protocol_run_id"],
        "selection_evidence_sha256": canonical_json_sha256([dict(item) for item in candidates]),
    })
    lease = _protocol_run_marker(root, "final-threshold", verified_oof["protocol_run_id"], "lease.json")
    if lease.exists():
        raise ProtocolValidationError("final threshold input tuple has already been leased")
    _write_immutable_json(lease, {
        "schema_version": "FinalThresholdLeaseV1",
        "protocol_run_id": verified_oof["protocol_run_id"],
        "input_tuple_sha256": replay_identity,
        "immutable": True,
        "status": "claimed",
    })
    value = {"schema_version": "FinalThresholdLockV2", "protocol_version": EVALUATION_PROTOCOL_VERSION,
             "producer_role": producer_role, "protocol_run_id": verified_oof["protocol_run_id"],
             "oof": oof_reference,
             "synthetic_split": synthetic_reference, "auto_threshold": auto_threshold,
             "review_threshold": review_threshold, "target_shortfall": max(0.0, .99 - detection),
             "selection_policy": "attain_target_then_detection_then_automatic_name_fp_then_deterministic_pair",
             "selection_evidence": [dict(item) for item in candidates],
             "selection_evidence_sha256": canonical_json_sha256([dict(item) for item in candidates]),
             "locked_once": True, "immutable": True}
    value["threshold_sha256"] = canonical_json_sha256(value)
    try:
        return _write_immutable_json(output, value)
    except Exception as error:
        _write_immutable_json(output.with_suffix(".failure.json"), {
            "schema_version": "FinalThresholdFailureV1", "status": "failed_execution",
            "lease_sha256": file_sha256(lease), "input_tuple_sha256": replay_identity,
            "protocol_run_id": verified_oof["protocol_run_id"], "immutable": True,
        })
        raise ProtocolValidationError("final threshold failed after one-shot lease acquisition") from error


def synthetic_holdout_once(*, eval_root: str | Path, output_path: str | Path, threshold_lock_path: str | Path,
                           synthetic_lock_path: str | Path, producer_role: str,
                           counts: Mapping[str, int] = (), evaluator: Callable[..., Mapping[str, Any]] | None = None,
                           untouched_holdout_payload: str | Path | None = None) -> dict[str, Any]:
    root = _protocol_root(eval_root)
    threshold_path = _verified_artifact_path(root, threshold_lock_path)
    synthetic_path = _verified_artifact_path(root, synthetic_lock_path)
    output = Path(output_path).expanduser().resolve()
    _safe_relative(root, output)
    threshold = _verify_threshold_lock(root, threshold_path)
    synthetic = _verify_synthetic_split_lock(synthetic_path)
    if (threshold.get("synthetic_split", {}).get("sha256") != file_sha256(synthetic_path)
            or not isinstance(threshold.get("protocol_run_id"), str)
            or threshold["protocol_run_id"] == "unassigned"):
        raise ProtocolValidationError("holdout synthetic split does not match final threshold provenance")
    if untouched_holdout_payload is None:
        raise ProtocolValidationError("holdout requires an immutable payload artifact")
    holdout_reference = _artifact_reference(root, untouched_holdout_payload)
    holdout_payload = _read_protocol_json(_verify_reference(root, holdout_reference))
    if (holdout_payload.get("immutable") is not True
            or holdout_payload.get("document_sha256s") != synthetic["untouched_holdout_sha256s"]):
        raise ProtocolValidationError("holdout payload does not exactly bind the untouched holdout")
    lease_identity = canonical_json_sha256({
        "threshold_sha256": file_sha256(threshold_path),
        "split_sha256": file_sha256(synthetic_path),
        "protocol_run_id": threshold["protocol_run_id"],
    })
    marker = _protocol_run_marker(root, "synthetic-holdout", threshold["protocol_run_id"], "consumed.json")
    if output.exists() or marker.exists():
        raise ProtocolValidationError("untouched holdout was already consumed")
    if evaluator is None:
        _holdout_target_result({"counts": counts, "measured_counters": counts})
    _write_immutable_json(marker, {
        "schema_version": "SyntheticHoldoutConsumptionV2",
        "input_tuple_sha256": lease_identity,
        "threshold": _artifact_reference(root, threshold_path),
        "synthetic_split": _artifact_reference(root, synthetic_path),
        "holdout_payload": holdout_reference,
        "protocol_run_id": threshold["protocol_run_id"],
        "immutable": True,
        "status": "claimed",
    })
    try:
        result = (evaluator(threshold=threshold, holdout=holdout_payload)
                  if evaluator else {"counts": counts, "measured_counters": counts})
        metrics, count_map, passed = _holdout_target_result(result)
        value = {"schema_version": "SyntheticHoldoutResultV2", "protocol_version": EVALUATION_PROTOCOL_VERSION,
                 "producer_role": producer_role, "protocol_run_id": threshold["protocol_run_id"],
                 "threshold": _artifact_reference(root, threshold_path),
                 "synthetic_split": _artifact_reference(root, synthetic_path),
                 "holdout_payload": holdout_reference,
                 "status": "success" if passed else "failed_target", "metrics": metrics, "counts": count_map,
                 "measured_counters": count_map, "immutable": True}
        value["result_sha256"] = canonical_json_sha256(value)
        return _write_immutable_json(output, value)
    except ProtocolValidationError:
        _write_immutable_json(output.with_suffix(".failure.json"), {
            "schema_version": "SyntheticHoldoutFailureV1", "status": "rejected_result",
            "consumption_sha256": file_sha256(marker), "input_tuple_sha256": lease_identity,
            "protocol_run_id": threshold["protocol_run_id"], "immutable": True,
        })
        raise
    except Exception as error:
        _write_immutable_json(output.with_suffix(".failure.json"), {
            "schema_version": "SyntheticHoldoutFailureV1", "status": "failed_execution",
            "consumption_sha256": file_sha256(marker), "input_tuple_sha256": lease_identity,
            "protocol_run_id": threshold["protocol_run_id"], "immutable": True,
        })
        raise ProtocolValidationError("holdout failed after one-shot consumption") from error


def write_threshold_e2e_receipt(*, eval_root: str | Path, output_path: str | Path, threshold_lock_path: str | Path,
                                holdout_path: str | Path, producer_role: str, output_payload: str | Path,
                                measured_counters: Mapping[str, int],
                                protocol_run_id: str | None = None) -> dict[str, Any]:
    root = _protocol_root(eval_root)
    threshold_ref = _artifact_reference(root, threshold_lock_path)
    threshold = _verify_threshold_lock(root, _verified_artifact_path(root, threshold_lock_path))
    holdout = _read_protocol_json(_verified_artifact_path(root, holdout_path))
    holdout_payload = {key: value for key, value in holdout.items() if key != "result_sha256"}
    if (holdout.get("schema_version") != "SyntheticHoldoutResultV2"
            or holdout.get("result_sha256") != canonical_json_sha256(holdout_payload)
            or holdout.get("status") not in {"success", "failed_target"}
            or holdout.get("threshold", {}).get("sha256") != threshold_ref["sha256"]
            or holdout.get("protocol_run_id") != threshold.get("protocol_run_id")):
        raise ProtocolValidationError("threshold E2E requires a verified holdout result")
    if protocol_run_id is not None and protocol_run_id != threshold["protocol_run_id"]:
        raise ProtocolValidationError("threshold E2E run identity mismatch")
    synthetic_ref = _verify_reference(root, holdout.get("synthetic_split", {}))
    if threshold.get("synthetic_split", {}).get("sha256") != file_sha256(synthetic_ref):
        raise ProtocolValidationError("threshold E2E holdout provenance mismatch")
    holdout_payload_ref = holdout.get("holdout_payload", {})
    _verify_reference(root, holdout_payload_ref)
    lease_identity = canonical_json_sha256({
        "threshold_sha256": threshold_ref["sha256"],
        "split_sha256": file_sha256(synthetic_ref),
        "protocol_run_id": threshold["protocol_run_id"],
    })
    marker = _read_protocol_json(
        _verified_artifact_path(root, _protocol_run_marker(root, "synthetic-holdout", threshold["protocol_run_id"], "consumed.json"))
    )
    if (
        marker.get("schema_version") != "SyntheticHoldoutConsumptionV2"
        or marker.get("input_tuple_sha256") != lease_identity
        or marker.get("threshold") != threshold_ref
        or marker.get("synthetic_split") != holdout.get("synthetic_split")
        or marker.get("holdout_payload") != holdout_payload_ref
        or marker.get("protocol_run_id") != threshold["protocol_run_id"]
        or marker.get("status") != "claimed"
        or marker.get("immutable") is not True
    ):
        raise ProtocolValidationError("threshold E2E holdout consumption marker is invalid")
    _metrics, holdout_counts, _passed = _holdout_target_result(
        {"metrics": holdout.get("metrics"), "counts": holdout.get("counts", {}),
         "measured_counters": holdout.get("measured_counters", {})}
    )
    independently_derived_counters = {
        "public_content_read_count": 0,
        "oof_read_count": 0,
        "final_lock_read_count": 0,
        "holdout_read_count": 0,
    }
    if measured_counters != independently_derived_counters:
        raise ProtocolValidationError("THRESHOLD_E2E_COUNTERS_REJECTED")
    receipt_counts = {**holdout_counts, **independently_derived_counters}
    return make_protocol_receipt(eval_root=root, output_path=output_path, artifact_kind="threshold_e2e",
                                 producer_role=producer_role, status="passed", inputs=(threshold_lock_path, holdout_path),
                                 counts=receipt_counts, output_payload=output_payload,
                                 protocol_run_id=threshold["protocol_run_id"])


def _public_gold_sources(public_gold: Mapping[str, Any]) -> dict[str, str]:
    payload = {key: value for key, value in public_gold.items() if key != "manifest_sha256"}
    if (public_gold.get("schema_version") != "LockedPublicGoldManifestV2"
            or public_gold.get("status") != "locked"
            or public_gold.get("immutable") is not True
            or public_gold.get("manifest_sha256") != canonical_json_sha256(payload)):
        raise ProtocolValidationError("verified public-gold artifact must be a locked V2 manifest")
    documents = public_gold.get("documents")
    if not isinstance(documents, list) or len(documents) != 25:
        raise ProtocolValidationError("verified public-gold artifact requires complete document provenance")
    source_by_hash: dict[str, str] = {}
    for item in documents:
        if not isinstance(item, Mapping) or not isinstance(item.get("document"), Mapping):
            raise ProtocolValidationError("public-gold document provenance is invalid")
        try:
            validate_manifest(item, require_locked=True)
        except ManifestValidationError as error:
            raise ProtocolValidationError("public-gold document provenance is invalid") from error
        reviewer = item.get("provenance", {}).get("reviewer")
        if (not isinstance(reviewer, Mapping) or reviewer.get("decision") != "approved"
                or reviewer.get("adjudication") != "independent_review"
                or item.get("annotation_status") != "reviewed_approved"):
            raise ProtocolValidationError("public-gold document review is invalid")
        completion = item.get("annotation_completion")
        collections = ("pages", "segments", "regions", "occurrences", "negatives", "protected_neighbors")
        if (not isinstance(completion, Mapping) or set(completion) != set(collections)
                or any(completion[name] != ("completed" if item.get(name) else "none_confirmed")
                       for name in collections)):
            raise ProtocolValidationError("public-gold document completion is invalid")
        document_hash = item["document"].get("input_sha256")
        source_class, form = item.get("source_class"), item.get("form")
        expected_profile = "official_dispatch" if source_class == "issued" else "internal_review"
        if (not isinstance(document_hash, str) or len(document_hash) != 64
                or source_class not in {"issued", "review"} or form != source_class
                or item.get("profile") != expected_profile or document_hash in source_by_hash):
            raise ProtocolValidationError("public-gold cohort provenance is invalid")
        source_by_hash[document_hash] = source_class
    if sum(source == "issued" for source in source_by_hash.values()) != 15:
        raise ProtocolValidationError("public-gold cohort provenance is invalid")
    return source_by_hash


def _count_rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _public_oof_report_projection(
    root: Path,
    oof: Mapping[str, Any],
    public_gold: Mapping[str, Any],
    source_by_hash: Mapping[str, str],
) -> tuple[dict[str, float | None], dict[str, int], dict[str, Any]]:
    documents = public_gold.get("documents")
    if not isinstance(documents, list):
        raise ProtocolValidationError("public-gold report provenance is invalid")
    expected_hashes = set(source_by_hash)
    gold_by_hash: dict[str, Mapping[str, Any]] = {}
    for document in documents:
        if not isinstance(document, Mapping):
            raise ProtocolValidationError("public-gold report provenance is invalid")
        document_hash = document.get("document", {}).get("input_sha256")
        if document_hash not in expected_hashes or document_hash in gold_by_hash:
            raise ProtocolValidationError("public-gold report provenance is invalid")
        gold_by_hash[document_hash] = document
    if set(gold_by_hash) != expected_hashes:
        raise ProtocolValidationError("public-gold report provenance is invalid")

    def required_occurrences(document: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        occurrences = document.get("occurrences", [])
        if not isinstance(occurrences, list):
            raise ProtocolValidationError("public-gold report provenance is invalid")
        return [
            occurrence for occurrence in occurrences
            if _required_mask_occurrence(occurrence)
        ]

    def document_denominators(document: Mapping[str, Any]) -> tuple[int, int, int, int, int, int, int]:
        occurrences = required_occurrences(document)
        regions = document.get("regions", [])
        segments = document.get("segments", [])
        negatives = document.get("negatives", [])
        if not all(isinstance(value, list) for value in (regions, segments, negatives)):
            raise ProtocolValidationError("public-gold report provenance is invalid")
        fixed = evaluate_fixed_region_occurrences(occurrences, [], regions, segments)
        body = evaluate_body_occurrences(occurrences, [], negatives, regions)
        return (
            len(occurrences),
            sum(occurrence.get("category") in {"name", "person_name"} for occurrence in occurrences),
            len(document.get("protected_neighbors", [])),
            len(negatives),
            body["recall"]["denominator"],
            fixed["fixed_region_gold_pii_count"],
            fixed["unscoped_fixed_region_gold_pii_count"],
        )

    required_denominators = {
        document_hash: document_denominators(document)
        for document_hash, document in gold_by_hash.items()
    }
    if any(not isinstance(document.get("protected_neighbors", []), list) for document in gold_by_hash.values()):
        raise ProtocolValidationError("public-gold report provenance is invalid")

    aggregate: dict[str, int] = {}
    seen: set[str] = set()
    failed_documents = 0
    prediction_references: list[dict[str, Any]] = []
    for fold in oof.get("folds", []):
        if not isinstance(fold, Mapping):
            raise ProtocolValidationError("OOF report fold provenance is invalid")
        prediction_reference = fold.get("predictions", {})
        predictions_path = _verify_reference(root, prediction_reference)
        prediction_references.append({
            "fold": fold.get("fold"),
            "path": prediction_reference.get("path"),
            "sha256": prediction_reference.get("sha256"),
        })
        try:
            lines = predictions_path.read_text(encoding="utf-8").splitlines()
            predictions = [_validated_oof_prediction(json.loads(line)) for line in lines if line]
        except (OSError, json.JSONDecodeError) as error:
            raise ProtocolValidationError("OOF report predictions are unreadable") from error
        for prediction in predictions:
            document_hash = prediction["document_sha256"]
            if document_hash not in expected_hashes or document_hash in seen:
                raise ProtocolValidationError("OOF report document coverage is invalid")
            (
                occurrence_denominator, name_denominator, protected_denominator,
                negative_denominator, body_denominator, scoped_fixed_denominator,
                unscoped_fixed_denominator,
            ) = required_denominators[document_hash]
            counts = prediction["counts"]
            if (
                counts["detection_tp"] + counts["detection_fn"] != occurrence_denominator
                or counts["automatic_tp"] + counts["automatic_fn"] != occurrence_denominator
                or counts["automatic_name_tp"] + counts["automatic_name_fn"] != name_denominator
                or counts["protected_neighbor_overlap_count"] > protected_denominator
                or counts["body_occurrence_tp"] + counts["body_occurrence_fn"] != body_denominator
                or counts["body_occurrence_fp"] > negative_denominator
                or counts["region_tp"] + counts["region_fn"] != scoped_fixed_denominator
                or counts["fixed_region_omission_count"] != counts["region_fn"]
                or counts["unscoped_fixed_region_omission_count"] > unscoped_fixed_denominator
                or counts["blocked_document_count"] != int(prediction["status"] == "failed_closed")
            ):
                raise ProtocolValidationError("OOF report prediction disagrees with locked public-gold denominators")
            seen.add(document_hash)
            failed_documents += counts["blocked_document_count"]
            for name, count in counts.items():
                aggregate[name] = aggregate.get(name, 0) + count
    if seen != expected_hashes or len(seen) != oof.get("document_count"):
        raise ProtocolValidationError("OOF report document coverage is incomplete")

    page_denominator = sum(len(item.get("pages", [])) for item in documents)
    segment_denominator = sum(len(item.get("segments", [])) for item in documents)
    region_denominator = sum(len(item.get("regions", [])) for item in documents)
    occurrence_denominator = sum(len(item.get("occurrences", [])) for item in documents)
    negative_denominator = sum(len(item.get("negatives", [])) for item in documents)
    protected_neighbor_denominator = sum(len(item.get("protected_neighbors", [])) for item in documents)
    positive_denominator = sum(required_denominators[document_hash][0] for document_hash in expected_hashes)
    name_denominator = sum(required_denominators[document_hash][1] for document_hash in expected_hashes)
    non_pii_lookalike_denominator = sum(required_denominators[document_hash][3] for document_hash in expected_hashes)
    body_denominator = sum(required_denominators[document_hash][4] for document_hash in expected_hashes)
    scoped_fixed_denominator = sum(required_denominators[document_hash][5] for document_hash in expected_hashes)
    unscoped_fixed_denominator = sum(required_denominators[document_hash][6] for document_hash in expected_hashes)
    aggregate.update({
        "document_count": len(seen),
        "document_denominator": len(documents),
        "page_denominator": page_denominator,
        "positive_denominator": positive_denominator,
        "gold_occurrence_count": positive_denominator,
        "gold_name_count": name_denominator,
        "body_gold_pii_count": body_denominator,
        "fixed_region_gold_pii_count": scoped_fixed_denominator,
        "unscoped_fixed_region_gold_pii_count": unscoped_fixed_denominator,
        "negative_denominator": negative_denominator,
        "segment_denominator": segment_denominator,
        "region_denominator": region_denominator,
        "occurrence_denominator": occurrence_denominator,
        "protected_neighbor_denominator": protected_neighbor_denominator,
        "issued_documents": sum(source == "issued" for source in source_by_hash.values()),
        "review_documents": sum(source == "review" for source in source_by_hash.values()),
        "total_documents": len(documents),
        "failed_document_count": failed_documents,
    })
    normalized_counts = _validated_counts(aggregate)
    if (
        normalized_counts["detection_tp"] + normalized_counts["detection_fn"] != positive_denominator
        or normalized_counts["automatic_tp"] + normalized_counts["automatic_fn"] != positive_denominator
        or normalized_counts["automatic_name_tp"] + normalized_counts["automatic_name_fn"] != name_denominator
        or normalized_counts["body_occurrence_tp"] + normalized_counts["body_occurrence_fn"] != body_denominator
        or normalized_counts["body_occurrence_fp"] > non_pii_lookalike_denominator
        or normalized_counts["region_tp"] + normalized_counts["region_fn"] != scoped_fixed_denominator
        or normalized_counts["fixed_region_omission_count"] != normalized_counts["region_fn"]
        or normalized_counts["unscoped_fixed_region_omission_count"] > unscoped_fixed_denominator
        or normalized_counts["gold_occurrence_count"] != positive_denominator
        or normalized_counts["gold_name_count"] != name_denominator
        or normalized_counts["protected_neighbor_overlap_count"] > protected_neighbor_denominator
        or normalized_counts["blocked_document_count"] != failed_documents
        or failed_documents > len(documents)
    ):
        raise ProtocolValidationError("OOF report aggregate disagrees with locked public-gold denominators")
    detection_tp = normalized_counts.get("detection_tp", 0)
    detection_fn = normalized_counts.get("detection_fn", 0)
    detection_fp = normalized_counts.get("detection_fp", 0)
    automatic_tp = normalized_counts.get("automatic_tp", 0)
    automatic_fn = normalized_counts.get("automatic_fn", 0)
    automatic_name_tp = normalized_counts.get("automatic_name_tp", 0)
    automatic_name_fn = normalized_counts.get("automatic_name_fn", 0)
    automatic_name_fp = normalized_counts.get("automatic_name_fp", 0)
    body_occurrence_tp = normalized_counts["body_occurrence_tp"]
    body_occurrence_fn = normalized_counts["body_occurrence_fn"]
    body_occurrence_fp = normalized_counts["body_occurrence_fp"]
    metrics = {
        "occurrence_detection_recall": _count_rate(detection_tp, detection_tp + detection_fn),
        "occurrence_candidate_precision": _count_rate(detection_tp, detection_tp + detection_fp),
        "automatic_occurrence_recall": _count_rate(automatic_tp, automatic_tp + automatic_fn),
        "automatic_name_recall": _count_rate(
            automatic_name_tp,
            automatic_name_tp + automatic_name_fn,
        ),
        "automatic_name_false_alert_rate": _count_rate(
            automatic_name_fp,
            automatic_name_tp + automatic_name_fp,
        ),
        "blocked_document_rate": _count_rate(
            normalized_counts.get("blocked_document_count", 0),
            normalized_counts["document_denominator"],
        ),
        "protected_neighbor_overlap_rate": _count_rate(
            normalized_counts.get("protected_neighbor_overlap_count", 0),
            normalized_counts["protected_neighbor_denominator"],
        ),
        "body_occurrence_recall": _count_rate(body_occurrence_tp, body_occurrence_tp + body_occurrence_fn),
        "false_positive_rate": _count_rate(body_occurrence_fp, non_pii_lookalike_denominator),
        "fixed_region_omission_count": normalized_counts["fixed_region_omission_count"],
        "region_tp_fp_fn": {
            "tp": normalized_counts["region_tp"],
            "fp": normalized_counts["region_fp"],
            "fn": normalized_counts["region_fn"],
        },
    }
    provenance = {
        "protocol_run_id": oof.get("protocol_run_id"),
        "public_gold": oof.get("public_gold"),
        "split_lock": oof.get("split_lock"),
        "core_gate_receipt": oof.get("core_gate_receipt"),
        "prediction_artifacts": sorted(prediction_references, key=lambda item: item["fold"]),
        "schema_versions": sorted({
            str(item.get("schema_version")) for item in documents if item.get("schema_version")
        }),
        "policy_versions": sorted({
            str(item.get("policy_version")) for item in documents if item.get("policy_version")
        }),
        "geometry_policy_versions": sorted({
            str(item.get("geometry_policy_version"))
            for item in documents
            if item.get("geometry_policy_version")
        }),
        "profiles": sorted({
            str(item.get("profile")) for item in documents if item.get("profile")
        }),
    }
    return metrics, normalized_counts, provenance


def _observational_accuracy_report(
    metrics: Mapping[str, float | int | None],
    counts: Mapping[str, int],
) -> tuple[dict[str, dict[str, float | int | str | bool | None]], dict[str, int]]:
    denominators = {
        "documents": counts["document_denominator"],
        "pages": counts["page_denominator"],
        "gold_pii": counts["gold_occurrence_count"],
        "non_pii_lookalikes": counts["negative_denominator"],
    }
    target_metrics = {
        "fixed_region_omission_count": (metrics["fixed_region_omission_count"], 0, "equal", counts["fixed_region_gold_pii_count"]),
        "body_occurrence_recall": (metrics["body_occurrence_recall"], 0.99, "greater_than_or_equal", counts["body_gold_pii_count"]),
        "false_positive_rate": (metrics["false_positive_rate"], 0.05, "less_than_or_equal", counts["negative_denominator"]),
    }
    accuracy_targets: dict[str, dict[str, float | int | str | bool | None]] = {}
    for name, (value, target, comparison, denominator) in target_metrics.items():
        if value is None:
            met: bool | None = None
        elif comparison == "equal":
            met = value == target
        elif comparison == "greater_than_or_equal":
            met = value >= target
        else:
            met = value <= target
        accuracy_targets[name] = {
            "value": value,
            "target": target,
            "comparison": comparison,
            "met": met,
            "denominator": denominator,
        }
    return accuracy_targets, denominators


def _public_holdout_report_projection(
    root: Path,
    e2e_receipt: Mapping[str, Any],
    protocol_run_id: str,
) -> dict[str, Any]:
    """Return holdout status/metrics without mixing holdout counts into the pilot."""
    inputs = e2e_receipt.get("inputs")
    if not isinstance(inputs, list):
        raise ProtocolValidationError("threshold E2E receipt inputs are invalid")
    holdout: Mapping[str, Any] | None = None
    holdout_reference: Mapping[str, Any] | None = None
    for reference in inputs:
        if not isinstance(reference, Mapping):
            raise ProtocolValidationError("threshold E2E receipt inputs are invalid")
        target = _verify_reference(root, {
            "path": reference.get("path"),
            "sha256": reference.get("sha256"),
        })
        payload = _read_protocol_json(target)
        if payload.get("schema_version") != "SyntheticHoldoutResultV2":
            continue
        if holdout is not None:
            raise ProtocolValidationError("threshold E2E receipt must bind one holdout result")
        holdout = payload
        holdout_reference = reference
    if holdout is None or holdout_reference is None:
        raise ProtocolValidationError("threshold E2E receipt must bind a holdout result")
    if holdout.get("protocol_run_id") != protocol_run_id:
        raise ProtocolValidationError("holdout result does not share the OOF protocol run")
    payload = {key: item for key, item in holdout.items() if key != "result_sha256"}
    expected_fields = {
        "schema_version", "protocol_version", "producer_role", "protocol_run_id",
        "threshold", "synthetic_split", "holdout_payload", "status", "metrics",
        "counts", "measured_counters", "immutable", "result_sha256",
    }
    if (
        set(holdout) != expected_fields
        or holdout.get("schema_version") != "SyntheticHoldoutResultV2"
        or holdout.get("protocol_version") != EVALUATION_PROTOCOL_VERSION
        or not isinstance(holdout.get("producer_role"), str)
        or not holdout["producer_role"]
        or holdout.get("result_sha256") != canonical_json_sha256(payload)
        or holdout.get("status") not in {"success", "failed_target"}
        or holdout.get("immutable") is not True
    ):
        raise ProtocolValidationError("holdout result is not a verified immutable artifact")
    metrics, _counts, _passed = _holdout_target_result({
        "metrics": holdout.get("metrics"),
        "counts": holdout.get("counts", {}),
        "measured_counters": holdout.get("measured_counters", {}),
    })
    return {"status": holdout["status"], "metrics": metrics}


def report_from_oof(*, eval_root: str | Path, output_path: str | Path, oof_path: str | Path,
                    threshold_e2e_receipt: str | Path, producer_role: str) -> dict[str, Any]:
    """Derive OOF count-only pilot metrics; keep synthetic holdout status separate."""
    if not isinstance(producer_role, str) or not producer_role:
        raise ProtocolValidationError("report producer_role is required")
    root = _protocol_root(eval_root)
    output = Path(output_path).expanduser().resolve()
    _safe_relative(root, output)
    oof = verify_oof(root, oof_path)
    oof_reference = _artifact_reference(root, oof_path)
    e2e_reference = _artifact_reference(root, threshold_e2e_receipt)
    e2e = verify_protocol_receipt(
        root,
        threshold_e2e_receipt,
        artifact_kind="threshold_e2e",
        required_status="passed",
    )
    if e2e.get("protocolRunId") != oof.get("protocol_run_id"):
        raise ProtocolValidationError("report artifacts must share the OOF protocol run")
    synthetic_holdout = _public_holdout_report_projection(root, e2e, oof["protocol_run_id"])
    required_zero_counters = {
        "public_content_read_count", "oof_read_count", "final_lock_read_count", "holdout_read_count",
    }
    receipt_counts = e2e.get("counts")
    if (
        not isinstance(receipt_counts, Mapping)
        or not required_zero_counters <= set(receipt_counts)
        or any(receipt_counts[name] != 0 for name in required_zero_counters)
    ):
        raise ProtocolValidationError("report stage counter delta must be zero")
    public_gold = _read_protocol_json(_verify_reference(root, oof["public_gold"]))
    source_by_hash = _public_gold_sources(public_gold)
    split = _read_protocol_json(_verify_reference(root, oof["split_lock"]))
    if set(source_by_hash) != {item["document_sha256"] for item in split["assignments"]}:
        raise ProtocolValidationError("public-gold cohort provenance does not match the verified split")
    metrics, counts, provenance = _public_oof_report_projection(root, oof, public_gold, source_by_hash)
    accuracy_targets, accuracy_denominators = _observational_accuracy_report(metrics, counts)
    provenance = {
        **provenance,
        "oof_sha256": oof_reference["sha256"],
        "threshold_e2e_receipt_sha256": e2e_reference["sha256"],
        "threshold_e2e_inputs": e2e.get("inputs", []),
    }
    value = {
        "schema_version": "PublicPilotReportV4",
        "protocol_version": EVALUATION_PROTOCOL_VERSION,
        "producer_role": producer_role,
        "oof": oof_reference,
        "threshold_e2e_receipt": e2e_reference,
        "pilot_only": True,
        "synthetic_holdout": synthetic_holdout,
        "status": "reported",
        "fold_provenance": [
            {"fold": item["fold"], "prediction_sha256": item["predictions"]["sha256"]}
            for item in oof["folds"]
        ],
        "metrics": metrics,
        "counts": counts,
        "accuracy_targets": accuracy_targets,
        "accuracy_denominators": accuracy_denominators,
        "provenance": provenance,
        "unavailable_metrics": [
            "segment_tp_fp_fn",
            "text_scan_breakdown",
        ],
        "limitations": "pilot_only; count-only; locked OOF fields only; no population generalization",
        "immutable": True,
    }
    value["report_sha256"] = canonical_json_sha256(value)
    report_identity = canonical_json_sha256({
        "protocol_run_id": oof["protocol_run_id"],
        "oof_sha256": oof_reference["sha256"],
        "threshold_e2e_receipt_sha256": e2e_reference["sha256"],
    })
    marker_path = _protocol_run_marker(root, "pilot-report", oof["protocol_run_id"], "lease.json")
    if output.exists() or marker_path.exists():
        raise ProtocolValidationError("pilot report protocol run was already consumed")
    _write_immutable_json(marker_path, {
        "schema_version": "PilotReportLeaseV1",
        "protocol_run_id": oof["protocol_run_id"],
        "input_tuple_sha256": report_identity,
        "status": "claimed",
        "immutable": True,
    })
    try:
        return _write_immutable_json(output, value)
    except Exception as error:
        _write_immutable_json(output.with_suffix(".failure.json"), {
            "schema_version": "PilotReportFailureV1", "status": "failed_execution",
            "lease_sha256": file_sha256(marker_path), "input_tuple_sha256": report_identity,
            "protocol_run_id": oof["protocol_run_id"], "immutable": True,
        })
        raise ProtocolValidationError("pilot report failed after one-shot lease acquisition") from error


def verify_pilot_report(eval_root: str | Path, report_path: str | Path) -> dict[str, Any]:
    root = _protocol_root(eval_root)
    raw_report_path = Path(report_path).expanduser()
    if raw_report_path.is_symlink():
        raise ProtocolValidationError("pilot report path must not be a symlink")
    try:
        verified_report_path = raw_report_path.resolve(strict=True)
    except OSError as error:
        raise ProtocolValidationError("pilot report path is unavailable") from error
    _safe_relative(root, verified_report_path)
    value = _read_protocol_json(verified_report_path)
    if value.get("schema_version") != "PublicPilotReportV4" or value.get("pilot_only") is not True:
        raise ProtocolValidationError("report must be pilot_only")
    if value.get("report_sha256") != canonical_json_sha256({key: item for key, item in value.items() if key != "report_sha256"}):
        raise ProtocolValidationError("pilot report hash mismatch")
    oof_reference = value.get("oof", {})
    e2e_reference = value.get("threshold_e2e_receipt", {})
    oof = verify_oof(root, _verify_reference(root, oof_reference))
    e2e = verify_protocol_receipt(
        root,
        _verify_reference(root, e2e_reference),
        artifact_kind="threshold_e2e",
        required_status="passed",
    )
    if e2e.get("protocolRunId") != oof.get("protocol_run_id"):
        raise ProtocolValidationError("report artifacts must share the OOF protocol run")
    synthetic_holdout = _public_holdout_report_projection(root, e2e, oof["protocol_run_id"])
    receipt_counts = e2e.get("counts")
    required_zero_counters = {
        "public_content_read_count", "oof_read_count", "final_lock_read_count", "holdout_read_count",
    }
    if (
        not isinstance(receipt_counts, Mapping)
        or not required_zero_counters <= set(receipt_counts)
        or any(receipt_counts.get(name) != 0 for name in required_zero_counters)
    ):
        raise ProtocolValidationError("pilot report requires explicit zero E2E counter deltas")
    public_gold = _read_protocol_json(_verify_reference(root, oof.get("public_gold", {})))
    source_by_hash = _public_gold_sources(public_gold)
    split = _read_protocol_json(_verify_reference(root, oof["split_lock"]))
    if set(source_by_hash) != {item["document_sha256"] for item in split["assignments"]}:
        raise ProtocolValidationError("public-gold cohort provenance is invalid")
    metrics, counts, provenance = _public_oof_report_projection(root, oof, public_gold, source_by_hash)
    accuracy_targets, accuracy_denominators = _observational_accuracy_report(metrics, counts)
    provenance = {
        **provenance,
        "oof_sha256": oof_reference.get("sha256"),
        "threshold_e2e_receipt_sha256": e2e_reference.get("sha256"),
        "threshold_e2e_inputs": e2e.get("inputs", []),
    }
    producer_role = value.get("producer_role")
    if not isinstance(producer_role, str) or not producer_role:
        raise ProtocolValidationError("pilot report producer role is missing")
    expected_payload = {
        "schema_version": "PublicPilotReportV4",
        "protocol_version": EVALUATION_PROTOCOL_VERSION,
        "producer_role": producer_role,
        "oof": oof_reference,
        "threshold_e2e_receipt": e2e_reference,
        "pilot_only": True,
        "status": "reported",
        "synthetic_holdout": synthetic_holdout,
        "fold_provenance": [
            {"fold": item["fold"], "prediction_sha256": item["predictions"]["sha256"]}
            for item in oof["folds"]
        ],
        "metrics": metrics,
        "counts": counts,
        "accuracy_targets": accuracy_targets,
        "accuracy_denominators": accuracy_denominators,
        "provenance": provenance,
        "unavailable_metrics": [
            "segment_tp_fp_fn",
            "text_scan_breakdown",
        ],
        "limitations": "pilot_only; count-only; locked OOF fields only; no population generalization",
        "immutable": True,
    }
    if {key: item for key, item in value.items() if key != "report_sha256"} != expected_payload:
        raise ProtocolValidationError("pilot report does not match verified OOF artifacts")
    report_identity = canonical_json_sha256({
        "protocol_run_id": oof["protocol_run_id"],
        "oof_sha256": oof_reference["sha256"],
        "threshold_e2e_receipt_sha256": e2e_reference["sha256"],
    })
    lease = _read_protocol_json(_protocol_run_marker(root, "pilot-report", oof["protocol_run_id"], "lease.json"))
    if (
        lease.get("schema_version") != "PilotReportLeaseV1"
        or lease.get("protocol_run_id") != oof["protocol_run_id"]
        or lease.get("input_tuple_sha256") != report_identity
        or lease.get("status") != "claimed"
        or lease.get("immutable") is not True
    ):
        raise ProtocolValidationError("pilot report protocol-run lease is invalid")
    return value


def _protocol_cli() -> int:
    parser = argparse.ArgumentParser(description="PII-safe evaluation protocol")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("write-core-gate-receipt", "public-oof-once", "verify-oof", "lock-final-threshold",
                 "synthetic-holdout-once", "write-threshold-e2e-receipt", "report-from-oof", "verify-pilot-report"):
        command = commands.add_parser(name)
        command.add_argument("--eval-root", required=True)
        command.add_argument("--input-json", required=True)
    args = parser.parse_args()
    handlers = {
        "write-core-gate-receipt": write_core_gate_receipt, "public-oof-once": public_oof_once,
        "verify-oof": verify_oof, "lock-final-threshold": lock_final_threshold,
        "synthetic-holdout-once": synthetic_holdout_once, "write-threshold-e2e-receipt": write_threshold_e2e_receipt,
        "report-from-oof": report_from_oof, "verify-pilot-report": verify_pilot_report,
    }
    try:
        arguments = _read_protocol_json(Path(args.input_json))
        result = handlers[args.command](eval_root=args.eval_root, **arguments)
    except (ProtocolValidationError, ManifestValidationError, OSError, TypeError):
        print(json.dumps({"status": "invalid", "code": "EVALUATION_INPUT_REJECTED"}, sort_keys=True))
        return 2
    print(json.dumps({"status": result.get("status", "verified")}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_protocol_cli())
