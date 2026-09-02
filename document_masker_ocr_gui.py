#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
서울시 자치구 일반 패턴 기반 로컬 문서 마스킹 GUI v2
- TXT/PDF 입력 지원
- PDF는 로컬 OCR/추출 엔진으로 텍스트·레이아웃 추출 후 마스킹
- 기본값은 오프라인 처리 (외부 API 없음)

지원 엔진(우선순위):
1) marker-pdf (권장: 스캔/한글/표/레이아웃 상대적으로 강함)
2) paddleocr (한글 OCR + 레이아웃 보완)
3) pymupdf4llm (텍스트 PDF + 마크다운 레이아웃)
4) pypdf (순수 텍스트 추출)
"""

from __future__ import annotations

import json
import hashlib
import hmac
import math
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from contextvars import ContextVar
from typing import Any

from path_guard import same_path

DEFAULT_MASKING_OPTIONS = {
    "rrn": True,
    "phone": True,
    "business_reg": True,
    "name": True,
    "address": True,
    "place": True,
    "legal_party": True,
    "company": True,
    "court": True,
    "case_title": True,
    "case_number": True,
    "law_firm": True,
    "attorney": True,
    "approval_line": True,
    "region_context": True,
    "doc_meta": True,
    "email": True,
    "korean_tokens": False,
    "custom_keywords": "",
    "custom_regions": "",
    "profile": "mixed",
    "extract_engine": "auto",
    "output_artifacts": "pdf_safe_report",
    "display_mode": "black",
    "deidentification_policy": "token",
    "region_scope": "national",
    "pdf_redaction": True,
    "return_text_preview": False,
    "chunk_size": 4000,
    "chunk_overlap": 128,
    "chunk_retries": 2,
    "context_chunk_size": 1200,
    "context_chunk_overlap": 120,
    "strict_quality_gate": False,
    "local_llm_refine": False,
    "local_llm_cmd": "llama-cli",
    "local_llm_model": "",
    "local_llm_max_calls": 25,
}
_ACTIVE_SOURCE_SNAPSHOT: ContextVar[str | None] = ContextVar("_ACTIVE_SOURCE_SNAPSHOT", default=None)


def normalize_opts(opts: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(DEFAULT_MASKING_OPTIONS)
    if opts:
        normalized.update(opts)
    if "autoMaskThreshold" in normalized:
        normalized["auto_threshold"] = normalized["autoMaskThreshold"]
    elif "auto_mask_threshold" in normalized:
        normalized["auto_threshold"] = normalized["auto_mask_threshold"]
    if "reviewThreshold" in normalized:
        normalized["review_threshold"] = normalized["reviewThreshold"]
    raw_profile = normalized.get("profile", "mixed")
    normalized["profile"] = _profile_value(raw_profile if isinstance(raw_profile, str) else "")
    return normalized


from masking_context import DocumentContext, build_document_context, safe_document_context_summary
from privacy_false_positive import (
    is_likely_address_value,
    is_likely_company_value,
    is_likely_court_value,
    is_likely_doc_meta_value,
    is_likely_law_firm_value,
    is_likely_person_name,
    is_labeled_person_name_value,
    is_likely_person_name_value,
)
from privacy_detection import safe_detection_candidate_reports
from privacy_spans import (
    PrivacyDetector,
    clean_match_text,
    detection_spans_from_matches,
    merge_detection_spans,
    tag_for_label,
    match_occurrence_id,
)
from document_routing import (
    BoundaryCorrection,
    COMMON_DOCUMENT_HEADER,
    CONTINUATION_NO_START_SIGNAL,
    CONTINUATION_PAGE_NUMBER_SEQUENCE,
    CONTINUATION_REPEATED_HEADER_FOOTER,
    PageEvidence,
    PdfRect,
    apply_boundary_correction,
    route_logical_documents,
)
from official_layout import (
    EVIDENCE_LABEL_VALUE_DISTANCE_MAX,
    RegionEvidence,
    detect_internal_review_regions,
    detect_official_dispatch_regions,
)
from approval_layout import (
    DISPATCH_REQUIRED_KINDS,
    INTERNAL_REQUIRED_KINDS,
    analyze_approval_layout,
)
from privacy_detection import (
    PUBLIC_NAME_TEST_AUTO_MASK_THRESHOLD,
    PUBLIC_NAME_TEST_REVIEW_THRESHOLD,
    score_public_body_name,
)
from privacy_transformers import TransformState, apply_deidentification_policy, normalize_deidentification_policy
from ko_pii_detector import build_ko_pii_detector
from public_detection import build_public_candidates
from pdf_redaction_rendering import (
    MANUAL_REDACTION_TAG,
    MASK_TOKEN_LABELS,
    add_redaction_annotation,
    display_token,
    insert_pdf_label,
    korean_pdf_font_file,
    normalize_display_mode,
    normalize_redaction_tag,
)
from masking_extraction import (
    ExtractResult,
    ExtractedPage,
    extract_document,
    extract_document_for_public_analysis,
    read_text_file,
    write_text_file,
    _extract_pdf_with_marker,
    _extract_pdf_with_marker_cleanup,
    _extract_pdf_with_paddle,
    _extract_pdf_with_pymupdf4llm,
    _extract_pdf_with_pypdf,
    _run_cmd,
)
from masking_reporting import (
    LLMCandidate,
    LLMCandidateOccurrence,
    LLM_DECISION_LINE_PAT,
    LLM_DECISION_TOKEN_PAT,
    _apply_llm_candidate_replacements,
    _collect_llm_candidates,
    _llm_bin_exists,
    _llm_failure_reason,
    _llm_yes_no_decision,
    _normalize_llm_candidate_value,
    _parse_llm_yes_no_output,
    _safe_pdf_redaction_summary,
    _score_llm_candidate,
    classify_redaction_failure_reason_code,
    enforce_quality_gate_or_raise,
    evaluate_quality_gate,
    llm_refine_masking,
    sanitize_for_logging,
    safe_review_item_summaries,
)
from masking_redaction import (
    ManualActionV1,
    ManualCorrectionBox,
    ManualRedactionBox,
    OccurrenceRedactionInput,
    ScannedPdfRedactionError,
    apply_manual_actions_v1,
    apply_manual_pdf_corrections,
    apply_manual_redactions,
    automatic_masks_preserve_manual_neighbors,
    redact_pdf_native,
    occurrence_rect_text_hash,
    _finish_pdf_save,
    _fresh_pdf_output_path,
    _normalized_pdf_save_target,
    _redaction_search_terms,
)
from scan_raster_verification import ScanManualRasterVerifier
from masking_rules import (
    ACCOUNT_CONTEXT_PAT,
    ACTING_APPROVER_NAME_PAT,
    ADDRESS_CONTEXT_PAT,
    ADDRESS_LABEL_PAT,
    APPROVAL_DATE_NAME_PAT,
    APPROVAL_FLOW_CONTEXT_PAT,
    APPROVAL_FLOW_LINE_PAT,
    APPROVAL_GRADE_OCR_PAT,
    APPROVAL_LINE_PAT,
    APPROVAL_ROLE_DATE_NAME_PAT,
    APPROVAL_ROLE_PAREN_NAME_PAT,
    APPROVAL_TABLE_INLINE_MULTI_PAT,
    APPROVAL_TABLE_LINE_PAT,
    ATTORNEY_PAT,
    BUSINESS_REG_PAT,
    CARD_PAT,
    CASE_LINE_TITLE_PAT,
    CASE_NUMBER_CONTEXT_PAT,
    CASE_NUMBER_PAT,
    CASE_TITLE_GENERIC_LABEL_PAT,
    CASE_TITLE_LABEL_PAT,
    COMPANY_INLINE_PAT,
    COMPANY_LABEL_PAT,
    COURT_PAT,
    COURT_SPACED_PAT,
    DEPT_ROLE_NAME_COMPACT_PAT,
    DOC_META_OCR_PAT,
    DOC_META_PAT,
    DOC_REF_INLINE_PAT,
    EMAIL_PAT,
    FOREIGN_REG_PAT,
    LANDLINE_PAT,
    LAW_FIRM_INLINE_PAT,
    LAW_FIRM_LABEL_PAT,
    LEGAL_PARTY_INLINE_NAME_PAT,
    LEGAL_PARTY_PAT,
    LEGAL_PARTY_REP_INLINE_PAT,
    LOT_NO_PAT,
    MASK_TOKEN_SEGMENT_PAT,
    MOBILE_PAT,
    NAME_CONTEXT_PAT,
    OFFICIAL_COMBINED_ROLE_NAME_PAT,
    OFFICIAL_ROLE_NAME_PAT,
    PASSPORT_PAT,
    POSTAL_CODE_ADDRESS_LABEL_PAT,
    POSTAL_CODE_PREFIX_PAT,
    PHONE_LABEL_PAT,
    PHONE_PATS,
    PHONE_VALUE_BODY,
    PHONE_VALUE_PAT,
    PLACE_PATS,
    PUBLIC_LEVEL_PAT,
    REGION_CONTEXT_PAT,
    REGION_DATA_PATH,
    REGION_SEED_DATA_PATH,
    REP_PHONE_PAT,
    REVIEW_REQUIRED_TAGS,
    ROAD_NO_PAT,
    RRN_PAT,
    RedactionMatch,
    SEOUL_GU_ONLY_PAT,
    SEOUL_GU_PAT,
    SIHAENG_DOCNO_PAT,
    TEAM_EXT_PAT,
    _DASH_CHARS,
    _ID_SEP,
    _KOREAN_JOSA_ALT,
    _LOOSE_COURT_BODY,
    _PHONE_SEP,
    _convert_mask_tokens_to_korean,
    _count_up,
    _display_token,
    _insert_pdf_label,
    _korean_pdf_font_file,
    _literal_alt,
    _mask_token,
    _national_address_patterns,
    _parse_custom_keywords,
    _record_redaction_match,
    _region_terms,
    _replace_two_char_party_name,
    _tracked_replace,
    _tracked_sub,
    _resolve_region_data_path,
    _review_item_for_rect,
    _review_status_for_tag,
    _review_tag,
    _safe_rect_bbox,
    _spaced_digits,
    _sub_case_title_line,
    _sub_keep_label,
    _sub_keep_label_when,
    _sub_phone_label_sequence,
    _sub_simple,
    _weak_place_patterns,
    apply_custom_keyword_masking,
    current_masking_source_boundaries,
    load_region_data,
    region_data_metadata,
    review_items_for_matches,
    tracked_masking_offsets,
)

APP_VERSION = "v4.7.3"

PROFILE_DISPLAY_TO_VALUE = {
    "내부 검토": "internal_review",
    "공문 발송": "official_dispatch",
    "혼합": "mixed",
    "법률문서": "legal",
    "공공문서": "mixed",
}
PROFILE_VALUE_TO_DISPLAY = {value: label for label, value in PROFILE_DISPLAY_TO_VALUE.items()}

ENGINE_DISPLAY_TO_VALUE = {
    "자동 선택": "auto",
    "마커 PDF": "marker",
    "Paddle OCR": "paddle",
    "기본 텍스트 추출": "pymupdf",
    "간단 텍스트 추출": "pypdf",
}
ENGINE_VALUE_TO_DISPLAY = {value: label for label, value in ENGINE_DISPLAY_TO_VALUE.items()}

REGION_SCOPE_DISPLAY_TO_VALUE = {
    "서울/수도권": "seoul",
    "전국": "national",
    "사용자 지정 지역": "custom",
}
REGION_SCOPE_VALUE_TO_DISPLAY = {value: label for label, value in REGION_SCOPE_DISPLAY_TO_VALUE.items()}

OUTPUT_ARTIFACT_DISPLAY_TO_VALUE = {
    "PDF + 안전 리포트": "pdf_safe_report",
    "PDF + 비식별 TXT": "pdf_masked_txt_safe_report",
    "PDF만 저장": "pdf_only",
    "검정 PDF + 라벨 PDF": "pdf_black_and_labeled",
}
OUTPUT_ARTIFACT_VALUE_TO_DISPLAY = {value: label for label, value in OUTPUT_ARTIFACT_DISPLAY_TO_VALUE.items()}
OUTPUT_ARTIFACT_LABELS = list(OUTPUT_ARTIFACT_DISPLAY_TO_VALUE)
OUTPUT_ARTIFACTS_MAP: dict[str, set[str]] = {
    "pdf_safe_report": {"pdf", "report"},
    "pdf_masked_txt_safe_report": {"pdf", "masked_txt", "report"},
    "pdf_only": {"pdf"},
    "pdf_black_and_labeled": {"pdf", "report", "labeled_pdf"},
    "pdf만": {"pdf"},
    "pdf+report": {"pdf", "report"},
}
def _profile_value(label_or_value: str) -> str:
    value = label_or_value
    normalized = PROFILE_DISPLAY_TO_VALUE.get(value, value).lower()
    if normalized not in {"internal_review", "official_dispatch", "mixed", "legal"}:
        raise ValueError("MASKING_PROFILE_UNSUPPORTED")
    return normalized




def _engine_value(label_or_value: str) -> str:
    value = (label_or_value or "auto").strip()
    return ENGINE_DISPLAY_TO_VALUE.get(value, value).lower()


def _region_scope_value(label_or_value: str) -> str:
    value = (label_or_value or "national").strip()
    return REGION_SCOPE_DISPLAY_TO_VALUE.get(value, value).lower()


def _output_artifact_value(label_or_value: str) -> str:
    value = (label_or_value or "pdf_safe_report").strip()
    return OUTPUT_ARTIFACT_DISPLAY_TO_VALUE.get(value, value)


# PaddleOCR 모델 호스트 연결 체크 생략(사내망/폐쇄망 지연 방지)
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


# -----------------------------
# 1) 마스킹 규칙
# -----------------------------




@dataclass(frozen=True, slots=True)
class ChunkProcessResult:
    masked_text: str
    counts: dict[str, int]
    matches: list[RedactionMatch]
    source_boundaries: tuple[int, ...] = ()
@dataclass(frozen=True, slots=True)
class RequiredMaskMappingError(RuntimeError):
    failures: tuple[dict[str, Any], ...]

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "degraded": True,
            "stage_failure_codes": ["REQUIRED_MASK_MAPPING_FAILED"],
            "review_items": list(self.failures),
        }

    def __str__(self) -> str:
        return "REQUIRED_MASK_MAPPING_FAILED"






@dataclass(frozen=True)
class PreviewRenderState:
    page_index: int
    scale: float
    image_width: int
    image_height: int






def _default_chunk_logger(message: str) -> None:
    return None


def _merge_counts(total: dict[str, int], part: dict[str, int]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0) + value


def _chunk_text(text: str, chunk_size: int, overlap: int = 0) -> list[tuple[str, int]]:
    if chunk_size <= 0 or len(text) <= chunk_size:
        return [(text, 0)]

    lines = text.splitlines(keepends=True)
    chunks: list[tuple[str, int]] = []
    current: list[str] = []
    current_start = 0
    current_len = 0
    source_cursor = 0

    for line in lines:
        if current and current_len + len(line) > chunk_size:
            chunks.append(("".join(current), current_start))
            current = []
            current_len = 0

        if len(line) > chunk_size:
            if current:
                chunks.append(("".join(current), current_start))
                current = []
                current_len = 0
            for start in range(0, len(line), chunk_size):
                chunks.append((line[start:start + chunk_size], source_cursor + start))
            source_cursor += len(line)
            continue

        if not current:
            current_start = source_cursor
        current.append(line)
        current_len += len(line)
        source_cursor += len(line)

    if current:
        chunks.append(("".join(current), current_start))

    if not chunks:
        return [(text, 0)]
    effective_overlap = max(0, overlap)
    if effective_overlap == 0:
        return chunks
    overlapped = [chunks[0]]
    for chunk, base in chunks[1:]:
        overlap_start = max(0, base - effective_overlap)
        chunk_end = base + len(chunk)
        overlapped.append((text[overlap_start:chunk_end], overlap_start))
    return overlapped


@tracked_masking_offsets
def _mask_text_chunk(chunk: str, opts: dict[str, Any]) -> ChunkProcessResult:
    opts = normalize_opts(opts)
    profile = _profile_value(str(opts.get("profile", "mixed") or "mixed"))

    use_approval_line = bool(opts.get("approval_line", True))
    use_region_context = bool(opts.get("region_context", True))
    use_doc_meta = bool(opts.get("doc_meta", True))
    use_court = bool(opts.get("court", True))

    # 법률문서 전용: 공문 메타/결재선 규칙 비활성화
    if profile == "legal":
        use_approval_line = False
        use_region_context = False
        use_doc_meta = False
    # Public approval-name masking is emitted only by trusted geometry finalization;
    # text-queue candidates remain review evidence.
    if profile in {"internal_review", "official_dispatch", "mixed"}:
        use_approval_line = False

    masked, counts, matches = mask_text(
        chunk,
        use_rrn=bool(opts.get("rrn", True)),
        use_phone=bool(opts.get("phone", True)),
        use_business_reg=bool(opts.get("business_reg", True)),
        use_name=bool(opts.get("name", True)),
        use_address=bool(opts.get("address", True)),
        use_place=bool(opts.get("place", True)),
        use_legal_party=bool(opts.get("legal_party", True)),
        use_company=bool(opts.get("company", True)),
        use_court=use_court,
        use_case_title=bool(opts.get("case_title", True)),
        use_case_number=bool(opts.get("case_number", True)),
        use_law_firm=bool(opts.get("law_firm", True)),
        use_attorney=bool(opts.get("attorney", True)),
        use_approval_line=use_approval_line,
        use_region_context=use_region_context,
        use_doc_meta=use_doc_meta,
        use_email=bool(opts.get("email", True)),
        profile=profile,
    )
    if bool(opts.get("korean_tokens", False)):
        masked = _convert_mask_tokens_to_korean(masked)
    keywords = _parse_custom_keywords(opts.get("custom_keywords", ""))
    masked = apply_custom_keyword_masking(
        masked,
        keywords,
        counts,
        matches,
        korean_tokens=bool(opts.get("korean_tokens", False)),
    )
    custom_regions = _parse_custom_keywords(opts.get("custom_regions", ""))
    masked = apply_custom_keyword_masking(
        masked,
        custom_regions,
        counts,
        matches,
        korean_tokens=bool(opts.get("korean_tokens", False)),
        tag="REGION",
    )

    if profile == "legal":
        matches = [
            replace(item, action="mask") if item.action == "review" else item
            for item in matches
        ]
    # Review/exclude actions and ungrounded approval candidates are reporting-only.
    boundaries = list(current_masking_source_boundaries())
    unauthorized_approval = [
        item for item in matches if item.tag in {"APPROVAL_LINE", "APPROVAL_FLOW"}
    ]
    restore = [
        item for item in matches
        if item.action != "mask" or item in unauthorized_approval
    ]
    restoration_failures: list[dict[str, Any]] = []
    if len(boundaries) != len(masked) + 1:
        restoration_failures.extend(_mapping_failure(match, "source_boundary_invalid") for match in restore)
    for match in sorted(restore, key=lambda item: item.start, reverse=True):
        try:
            output_start = boundaries.index(match.start)
            output_end = boundaries.index(match.end)
        except ValueError:
            restoration_failures.append(_mapping_failure(match, "source_boundary_missing"))
            continue
        if output_end <= output_start:
            restoration_failures.append(_mapping_failure(match, "source_boundary_invalid"))
            continue
        masked = masked[:output_start] + chunk[match.start:match.end] + masked[output_end:]
        boundaries = boundaries[:output_start] + list(range(match.start, match.end + 1)) + boundaries[output_end + 1:]
    if restoration_failures:
        raise RequiredMaskMappingError(tuple(restoration_failures))
    if unauthorized_approval:
        unauthorized_ids = {id(item) for item in unauthorized_approval}
        matches = [
            replace(item, action="review") if id(item) in unauthorized_ids else item
            for item in matches
        ]
    return ChunkProcessResult(
        masked_text=masked,
        counts=counts,
        matches=matches,
        source_boundaries=tuple(boundaries),
    )


def _document_match(match: RedactionMatch, base_offset: int) -> RedactionMatch:
    if match.start < 0 or match.end <= match.start:
        return match
    return replace(
        match,
        start=base_offset + match.start,
        end=base_offset + match.end,
        occurrence_id="",
    )


def _occurrence_key(text: str, match: RedactionMatch) -> tuple[int, int, str, str] | None:
    if match.start < 0 or match.end <= match.start:
        return None
    if clean_match_text(text[match.start:match.end]) != clean_match_text(match.text):
        return None
    return (match.start, match.end, match.tag, match.text)


def _overlapping_occurrence_index(matches: list[RedactionMatch], candidate: RedactionMatch) -> int | None:
    for index, existing in enumerate(matches):
        if existing.tag != candidate.tag or existing.start < 0:
            continue
        if existing.start < candidate.end and candidate.start < existing.end:
            return index
    return None


def _defer_partial_name_matches(
    source_text: str,
    chunk_entry: tuple[str, int],
    result: ChunkProcessResult,
) -> ChunkProcessResult:
    chunk, base_offset = chunk_entry
    partials = [
        match
        for match in result.matches
        if match.tag in {"NAME", "APPROVAL_LINE"}
        and (
            (
                match.end == len(chunk)
                and base_offset + match.end < len(source_text)
                and "가" <= source_text[base_offset + match.end] <= "힣"
            )
            or (
                match.start == 0
                and base_offset > 0
                and "가" <= source_text[base_offset - 1] <= "힣"
            )
        )
    ]
    if not partials or len(result.source_boundaries) != len(result.masked_text) + 1:
        return result

    edits: list[tuple[int, int, RedactionMatch]] = []
    for match in partials:
        output_end = result.source_boundaries.index(match.end)
        tokens = (f"[{match.tag}]", _mask_token(match.tag))
        token = next((item for item in tokens if result.masked_text[:output_end].endswith(item)), "")
        if not token:
            return result
        edits.append((output_end - len(token), output_end, match))

    masked_text = result.masked_text
    source_boundaries = list(result.source_boundaries)
    for output_start, output_end, match in sorted(edits, reverse=True):
        masked_text = masked_text[:output_start] + chunk[match.start:match.end] + masked_text[output_end:]
        source_boundaries = (
            source_boundaries[:output_start]
            + list(range(match.start, match.end + 1))
            + source_boundaries[output_end + 1:]
        )

    counts = dict(result.counts)
    for match in partials:
        counts[match.tag] -= 1
        if counts[match.tag] == 0:
            del counts[match.tag]
    partial_ids = {id(match) for match in partials}
    return replace(
        result,
        masked_text=masked_text,
        counts=counts,
        matches=[match for match in result.matches if id(match) not in partial_ids],
        source_boundaries=tuple(source_boundaries),
    )


def _source_to_output_offsets(
    chunks: list[tuple[str, int]],
    results: list[ChunkProcessResult],
) -> dict[int, int]:
    offsets: dict[int, int] = {}
    output_base = 0
    for (_chunk, source_base), result in zip(chunks, results):
        if len(result.source_boundaries) != len(result.masked_text) + 1:
            return {}
        for output_offset, source_offset in enumerate(result.source_boundaries):
            offsets.setdefault(source_base + source_offset, output_base + output_offset)
        output_base += len(result.masked_text)
    return offsets


def _mapping_failure(match: RedactionMatch, reason: str) -> dict[str, Any]:
    return {
        "code": "REQUIRED_MASK_MAPPING_FAILED",
        "reason": reason,
        "review_required": True,
        "tag": match.tag,
        "start": match.start,
        "end": match.end,
        "source": match.source,
        "occurrence_id": match.occurrence_id,
    }


def _apply_source_occurrences(
    masked_text: str,
    matches: list[RedactionMatch],
    source_to_output: dict[int, int],
    korean_tokens: bool,
) -> tuple[str, list[dict[str, Any]]]:
    edits: list[tuple[int, int, str]] = []
    failures: list[dict[str, Any]] = []
    for match in matches:
        if match.action != "mask":
            continue
        output_start = source_to_output.get(match.start)
        output_end = source_to_output.get(match.end)
        if output_start is None or output_end is None or output_end <= output_start:
            failures.append(_mapping_failure(match, "source_output_offset_missing"))
            continue
        token = _mask_token(match.tag) if korean_tokens else f"[{match.tag}]"
        edits.append((output_start, output_end, token))
    result = masked_text
    last_start = len(masked_text) + 1
    for start, end, token in sorted(edits, reverse=True):
        if end > last_start:
            continue
        result = result[:start] + token + result[end:]
        last_start = start
    return result, failures


def _ai_redaction_matches(text: str, detector: PrivacyDetector) -> list[RedactionMatch]:
    matches: list[RedactionMatch] = []
    for span in detector.detect(text):
        if span.start < 0 or span.end <= span.start or span.end > len(text):
            continue
        value = text[span.start:span.end]
        if len(value.strip()) < 2:
            continue
        tag = tag_for_label(span.label)
        action = "mask" if tag == "ACCOUNT" else (
            span.action if span.action in {"mask", "review", "exclude"} else "review"
        )
        matches.append(
            RedactionMatch(
                tag=tag,
                text=value,
                start=span.start,
                end=span.end,
                source="optional_ai_detector",
                sources=("optional_ai_detector",),
                action=action,
                occurrence_id=span.occurrence_id,
                page=span.page,
                analysis_revision=span.analysis_revision,
                bbox=span.bbox,
                rects=span.rects,
                evidence=span.evidence,
                provenance=span.provenance,
                coordinate_space=span.coordinate_space,
                confidence=span.confidence,
            )
        )
    return matches


def _eligible_source_occurrences(
    source_text: str,
    match: RedactionMatch,
    existing_matches: list[RedactionMatch],
) -> list[tuple[int, int]]:
    """Return source occurrences whose text remains eligible in the rendered output."""
    masked_spans = [
        (item.start, item.end)
        for item in existing_matches
        if item.action == "mask" and item.start >= 0 and item.end > item.start
    ]
    return [
        (found.start(), found.end())
        for found in re.finditer(re.escape(match.text), source_text)
        if not any(start < found.end() and found.start() < end for start, end in masked_spans)
    ]


def _apply_ai_redactions(
    masked_text: str,
    source_text: str,
    matches: list[RedactionMatch],
    existing_matches: list[RedactionMatch],
    korean_tokens: bool,
) -> tuple[str, list[dict[str, Any]]]:
    result = masked_text
    failures: list[dict[str, Any]] = []
    existing_spans = {(item.start, item.end) for item in existing_matches if item.start >= 0}
    for match in sorted(matches, key=lambda item: item.start, reverse=True):
        if match.action != "mask":
            continue
        if (match.start, match.end) in existing_spans:
            continue
        output_spans = [(found.start(), found.end()) for found in re.finditer(re.escape(match.text), result)]
        source_spans = _eligible_source_occurrences(source_text, match, existing_matches)
        try:
            ordinal = source_spans.index((match.start, match.end))
        except ValueError:
            failures.append(_mapping_failure(match, "source_ordinal_missing"))
            continue
        if ordinal >= len(output_spans):
            failures.append(_mapping_failure(match, "output_ordinal_missing"))
            continue
        start, end = output_spans[ordinal]
        token = _mask_token(match.tag) if korean_tokens else f"[{match.tag}]"
        result = result[:start] + token + result[end:]
    return result, failures


def _deidentification_matches(text: str, matches: list[RedactionMatch]) -> list[RedactionMatch]:
    unique: list[RedactionMatch] = []
    seen: set[tuple[int, int, str, str]] = set()
    for match in matches:
        if match.action != "mask":
            continue
        key = _occurrence_key(text, match)
        if key is not None and key in seen:
            continue
        if key is not None:
            seen.add(key)
        unique.append(match)
    return unique


def process_masking_queue(
    text: str,
    opts: dict[str, Any],
    *,
    transform_state: TransformState | None = None,
) -> tuple[str, dict[str, int], list[RedactionMatch], dict[str, Any]]:
    opts = normalize_opts(opts)
    chunk_size = max(int(opts.get("chunk_size", 4000) or 4000), 1)
    keywords = _parse_custom_keywords(opts.get("custom_keywords", ""))
    custom_regions = _parse_custom_keywords(opts.get("custom_regions", ""))
    longest_custom_term = max((len(term) for term in [*keywords, *custom_regions]), default=0)
    chunk_overlap = max(int(opts.get("chunk_overlap", 128) or 128), 64, longest_custom_term)
    max_retries = max(int(opts.get("chunk_retries", 2) or 2), 0)
    logger = opts.get("log_callback") or _default_chunk_logger
    chunk_processor = opts.get("_chunk_processor") or _mask_text_chunk

    chunks = _chunk_text(text, chunk_size)
    masked_chunks: list[str] = []
    chunk_results: list[ChunkProcessResult] = []
    total_counts: dict[str, int] = {}
    total_matches: list[RedactionMatch] = []
    retried_chunks = 0
    failed_chunks = 0
    fallback_chunks = 0

    for idx, (chunk, base_offset) in enumerate(chunks, 1):
        logger(f"[청크] {idx}/{len(chunks)} 처리 시작 (chars={len(chunk)})")
        attempts = 0
        while True:
            attempts += 1
            try:
                result = chunk_processor(chunk, {**opts, "_chunk_base_offset": base_offset})
                if chunk_processor is _mask_text_chunk:
                    result = _defer_partial_name_matches(text, (chunk, base_offset), result)
                masked_chunks.append(result.masked_text)
                chunk_results.append(result)
                _merge_counts(total_counts, result.counts)
                total_matches.extend(_document_match(match, base_offset) for match in result.matches)
                if attempts > 1:
                    retried_chunks += 1
                logger(f"[청크] {idx}/{len(chunks)} 처리 완료 (attempt={attempts})")
                break
            except Exception:
                if attempts <= max_retries:
                    logger(f"[청크] {idx}/{len(chunks)} 재시도 {attempts}/{max_retries}")
                    continue

                failed_chunks += 1
                failure_code = "CHUNK_PROCESSOR_FAILED"
                if bool(opts.get("require_chunk_processor", False)):
                    raise RuntimeError(failure_code) from None
                logger(f"[청크] {idx}/{len(chunks)} 실패 - 기본 규칙 엔진으로 계속 진행 ({failure_code})")
                fallback = _mask_text_chunk(chunk, {**opts, "_chunk_base_offset": base_offset})
                if chunk_processor is _mask_text_chunk:
                    fallback = _defer_partial_name_matches(text, (chunk, base_offset), fallback)
                fallback_chunks += 1
                masked_chunks.append(fallback.masked_text)
                chunk_results.append(fallback)
                _merge_counts(total_counts, fallback.counts)
                total_matches.extend(_document_match(match, base_offset) for match in fallback.matches)
                logger(f"[청크] {idx}/{len(chunks)} 대체 처리 완료")
                break

    joined_masked = "".join(masked_chunks)
    occurrence_keys = {key for match in total_matches if (key := _occurrence_key(text, match)) is not None}
    boundary_matches: list[RedactionMatch] = []
    if chunk_processor is _mask_text_chunk:
        for _boundary_index, (_chunk, boundary_offset) in enumerate(chunks[1:]):
            window_start = max(0, boundary_offset - chunk_overlap)
            window_end = min(len(text), boundary_offset + chunk_overlap)
            boundary_result = _mask_text_chunk(
                text[window_start:window_end],
                {**opts, "_chunk_base_offset": window_start},
            )
            for local_match in boundary_result.matches:
                match = _document_match(local_match, window_start)
                key = _occurrence_key(text, match)
                if key is None or key in occurrence_keys:
                    continue
                overlapping_index = _overlapping_occurrence_index(total_matches, match)
                if overlapping_index is not None:
                    existing = total_matches[overlapping_index]
                    if match.end - match.start > existing.end - existing.start:
                        existing_key = _occurrence_key(text, existing)
                        if existing_key is not None:
                            occurrence_keys.discard(existing_key)
                        total_matches[overlapping_index] = match
                        occurrence_keys.add(key)
                        boundary_matches.append(match)
                    continue
                occurrence_keys.add(key)
                boundary_matches.append(match)
                total_matches.append(match)
                _count_up(total_counts, match.tag)

    if boundary_matches:
        joined_masked, mapping_failures = _apply_source_occurrences(
            joined_masked,
            boundary_matches,
            _source_to_output_offsets(chunks, chunk_results),
            korean_tokens=bool(opts.get("korean_tokens", False)),
        )
        if mapping_failures:
            raise RequiredMaskMappingError(tuple(mapping_failures))

    detector_failure = False
    detector = opts.get("_privacy_detector")
    uses_default_detector = detector is None
    if uses_default_detector:
        detector = build_ko_pii_detector(logger)
    if detector is None:
        detector_failure = True
        logger("[AI] 선택 탐지기를 사용할 수 없음 (code=OPTIONAL_DETECTOR_FAILED)")
        if bool(opts.get("require_privacy_detector", False)):
            raise RuntimeError("OPTIONAL_DETECTOR_FAILED")
    if detector is not None:
        existing_matches = list(total_matches)
        restored_occurrence_keys = {
            key
            for match in existing_matches
            if match.action != "mask" and (key := _occurrence_key(text, match)) is not None
        }
        try:
            ai_matches = _ai_redaction_matches(text, detector)
        except Exception:  # noqa: BROAD_EXCEPT_OK - optional detector boundary
            detector_failure = True
            logger("[AI] 선택 탐지기 처리 실패 - 규칙 기반 결과 유지 (code=OPTIONAL_DETECTOR_FAILED)")
            if bool(opts.get("require_privacy_detector", False)):
                raise RuntimeError("OPTIONAL_DETECTOR_FAILED") from None
            ai_matches = []
        unique_ai_matches: list[RedactionMatch] = []
        for match in ai_matches:
            key = _occurrence_key(text, match)
            if key is None:
                continue
            if key in occurrence_keys:
                if not uses_default_detector:
                    duplicate_index = next(
                        (
                            index
                            for index, existing in enumerate(total_matches)
                            if _occurrence_key(text, existing) == key
                        ),
                        None,
                    )
                    if duplicate_index is not None:
                        existing = total_matches[duplicate_index]
                        sources = tuple(dict.fromkeys((
                            existing.source,
                            *existing.sources,
                            match.source,
                            *match.sources,
                        )))
                        action = (
                            existing.action
                            if key in restored_occurrence_keys
                            else "mask" if "mask" in {existing.action, match.action} else existing.action
                        )
                        merged = replace(
                            existing,
                            source=existing.source,
                            sources=sources,
                            action=action,
                            occurrence_id=match.occurrence_id or existing.occurrence_id,
                        )
                        total_matches[duplicate_index] = replace(
                            merged,
                            occurrence_id=match_occurrence_id(merged, merged.start, merged.end),
                        )
                continue
            occurrence_keys.add(key)
            unique_ai_matches.append(match)
            total_matches.append(match)
            _count_up(total_counts, match.tag)
        joined_masked, mapping_failures = _apply_ai_redactions(
            joined_masked,
            text,
            unique_ai_matches,
            existing_matches,
            korean_tokens=bool(opts.get("korean_tokens", False)),
        )
        if mapping_failures:
            raise RequiredMaskMappingError(tuple(mapping_failures))

    total_matches.sort(key=lambda match: (match.start if match.start >= 0 else len(text), match.end, match.tag, match.text))
    total_matches = [
        replace(
            match,
            occurrence_id=match_occurrence_id(match, match.start, match.end),
        )
        for match in total_matches
    ]
    deidentification_policy = normalize_deidentification_policy(opts.get("deidentification_policy", "token"))
    joined_masked = apply_deidentification_policy(
        joined_masked,
        _deidentification_matches(text, total_matches),
        deidentification_policy,
        state=transform_state,
    )

    meta = {
        "enabled": True,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "max_retries": max_retries,
        "total_chunks": len(chunks),
        "retried_chunks": retried_chunks,
        "failed_chunks": failed_chunks,
        "fallback_chunks": fallback_chunks,
        "degraded": bool(failed_chunks or detector_failure),
        "stage_failure_codes": (
            (["CHUNK_PROCESSOR_FAILED"] if failed_chunks else [])
            + (["OPTIONAL_DETECTOR_FAILED"] if detector_failure else [])
        ),
        "deidentification_policy": deidentification_policy,
    }
    return joined_masked, total_counts, total_matches, meta


def _sub_court_when(
    text: str,
    pattern: re.Pattern[str],
    report: dict[str, int],
    matches: list[RedactionMatch],
) -> str:
    def repl(match: re.Match[str]) -> str:
        value = match.group("value")
        start = match.start("value")
        end = match.end("value")
        if not is_likely_court_value(value, text, start, end):
            return match.group(0)
        _record_redaction_match(matches, "COURT", value, start, end)
        _count_up(report, "COURT")
        return "[COURT]"

    return _tracked_sub(pattern, repl, text)



_APPROVAL_INLINE_NAME_PAT = re.compile(
    r"(?:^|[\s|/,])(?P<label>[가-힣A-Za-z0-9]{1,30}?(?:관|장|급|긍|금))\s*"
    r"(?P<value>[가-힣]{2,4}?)(?=\s*(?:[|/,]|$)|\s+[가-힣A-Za-z0-9]{1,30}?(?:관|장|급|긍|금))"
)


def _sub_approval_name_when(
    text: str,
    pattern: re.Pattern[str],
    report: dict[str, int],
    matches: list[RedactionMatch],
    *,
    table_line: bool = False,
) -> str:
    def repl(match: re.Match[str]) -> str:
        value = match.group("value")
        start = match.start("value")
        end = match.end("value")
        decision = score_public_body_name(
            approval_role=True,
            punctuation_or_label_boundary=True,
            distance_from_label=start - match.start(),
            page_position_match=True,
            region_state="confirmed",
        )
        if decision["action"] == "preserve" or not is_likely_person_name(value, text, start, end):
            return match.group(0)
        _record_redaction_match(matches, "APPROVAL_LINE", value, start, end)
        _count_up(report, "APPROVAL_LINE")
        separator = "\n" if table_line else ""
        return f"{match.group('label')}{separator}[APPROVAL_LINE]"

    return _tracked_sub(pattern, repl, text)


def _sub_approval_inline_multi_when(
    text: str,
    pattern: re.Pattern[str],
    report: dict[str, int],
    matches: list[RedactionMatch],
) -> str:
    def repl(match: re.Match[str]) -> str:
        block = match.group("value")
        block_start = match.start("value")
        candidates = list(_APPROVAL_INLINE_NAME_PAT.finditer(block))
        pieces: list[str] = []
        cursor = 0
        for candidate in candidates:
            value = candidate.group("value")
            value_start = block_start + candidate.start("value")
            value_end = block_start + candidate.end("value")
            pieces.append(block[cursor:candidate.start("value")])
            if is_likely_person_name(value, text, value_start, value_end):
                _record_redaction_match(matches, "APPROVAL_LINE", value, value_start, value_end)
                _count_up(report, "APPROVAL_LINE")
                pieces.append("[APPROVAL_LINE]")
            else:
                pieces.append(value)
            cursor = candidate.end("value")
        pieces.append(block[cursor:])
        return "".join(pieces)

    return _tracked_sub(pattern, repl, text)


def _mask_legal_courts_with_citation_preserve(
    text: str,
    report: dict[str, int],
    matches: list[RedactionMatch],
) -> str:
    """
    법률문서 전용 법원명 처리:
    - 판례 인용(법원 + 선고 + 판결/결정) 문맥의 법원명은 보존
    - 그 외 법원명은 마스킹
    """
    loose_court_pat = (
        r"(?:"
        r"대\s*법\s*원|헌\s*법\s*재\s*판\s*소|특\s*허\s*법\s*원|회\s*생\s*법\s*원|행\s*정\s*법\s*원|가\s*정\s*법\s*원|고\s*등\s*법\s*원|"
        r"(?:[가-힣]\s*){2,12}(?:지\s*방\s*법\s*원|고\s*등\s*법\s*원|가\s*정\s*법\s*원|행\s*정\s*법\s*원|회\s*생\s*법\s*원|지\s*원)"
        r")"
    )
    citation_court_pat = re.compile(
        rf"(?P<court>{loose_court_pat})"
        r"(?=\s*(?:"
        r"(?:19|20)\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*선\s*고\s*"
        r"(?:19|20)\d{2}\s*(?:[가-힣]\s*){1,4}\s*\d{1,10}\s*(?:판\s*결|결\s*정)"
        r"|(?:19|20)\d{2}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.?\s*자\s*"
        r"(?:19|20)\d{2}\s*(?:[가-힣]\s*){1,4}\s*\d{1,10}\s*결\s*정"
        r"|(?:19|20)\d{2}\s*(?:[가-힣]\s*){1,4}\s*\d{1,10}\s*(?:판\s*결|결\s*정)"
        r"))"
    )
    legal_spaced_court_pat = re.compile(rf"(?<![가-힣])(?P<value>{loose_court_pat})(?![가-힣])")
    preserved: dict[str, str] = {}

    def _preserve(m: re.Match) -> str:
        idx = len(preserved)
        key = f"__CIT_COURT_{idx}__"
        court = m.group("court")
        preserved[key] = court
        return key

    work = _tracked_sub(citation_court_pat, _preserve, text)
    work = _sub_court_when(work, COURT_PAT, report, matches)
    work = _sub_court_when(work, legal_spaced_court_pat, report, matches)

    for key, court in preserved.items():
        work = _tracked_replace(work, key, court)

    return work


def _mask_legal_case_numbers_with_citation_preserve(
    text: str,
    report: dict[str, int],
    matches: list[RedactionMatch],
) -> str:
    """
    법률문서 전용 사건번호 처리:
    - 판례 인용 사건번호(대법원/법원 + 선고 + 판결/결정)는 보존
    - 현재 사건 메타(사건번호/사건/당해 사건/본건/이 사건)는 마스킹
    """
    citation_pat = re.compile(
        r"((?:대법원|헌법재판소|[가-힣]{2,12}(?:지방법원|고등법원|가정법원|행정법원|회생법원|지원))"
        r"\s*(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*선고\s*"
        r"(?P<num>(?:19|20)\d{2}\s*[가-힣]{1,4}\s*\d{1,10})\s*(?:판결|결정))"
    )
    preserved: dict[str, str] = {}

    def _preserve(m: re.Match) -> str:
        idx = len(preserved)
        key = f"__CIT_CASE_NO_{idx}__"
        num = m.group("num")
        preserved[key] = num
        return m.group(0).replace(num, key)

    work = _tracked_sub(citation_pat, _preserve, text)

    work = _sub_keep_label(work, CASE_NUMBER_CONTEXT_PAT, "CASE_NUMBER", report, value_group="value", matches=matches)

    # 라벨 없이 단독 등장하는 사건번호는 법률문서에서 과마스킹 위험이 커서 보존 (헤더 패턴에서 대부분 처리)

    for key, num in preserved.items():
        work = _tracked_replace(work, key, num)

    return work


@tracked_masking_offsets
def mask_text(
    text: str,
    use_rrn: bool = True,
    use_phone: bool = True,
    use_business_reg: bool = True,
    use_name: bool = True,
    use_address: bool = True,
    use_place: bool = True,
    use_legal_party: bool = True,
    use_company: bool = True,
    use_court: bool = True,
    use_case_title: bool = True,
    use_case_number: bool = True,
    use_law_firm: bool = True,
    use_attorney: bool = True,
    use_approval_line: bool = True,
    use_region_context: bool = True,
    use_doc_meta: bool = True,
    use_email: bool = True,
    profile: str = "mixed",
) -> tuple[str, dict[str, int], list[RedactionMatch]]:
    profile = _profile_value(profile)
    if profile == "legal":
        use_approval_line = False
        use_region_context = False
        use_doc_meta = False
    elif profile in {"internal_review", "official_dispatch", "mixed"}:
        use_legal_party = False
        use_court = False
        use_case_title = False
        use_case_number = False
        use_law_firm = False
        use_attorney = False
        use_approval_line = False

    report: dict[str, int] = {}
    matches: list[RedactionMatch] = []

    # 1. 이미 마스킹된 토큰은 MASK_TOKEN_SEGMENT_PAT 기반 후속 사용자 키워드 단계에서 보호된다.
    text = _sub_simple(text, CARD_PAT, "CARD", report, matches=matches)
    if use_rrn:
        text = _sub_simple(text, RRN_PAT, "RRN", report, matches=matches)
        text = _sub_simple(text, FOREIGN_REG_PAT, "FOREIGN_REG", report, matches=matches)
    if use_business_reg:
        text = _sub_simple(text, BUSINESS_REG_PAT, "BUSINESS_REG_NO", report, matches=matches)
    if use_phone:
        text = _sub_phone_label_sequence(text, report, matches)
        for phone_pat in [MOBILE_PAT, LANDLINE_PAT, REP_PHONE_PAT]:
            text = _sub_simple(text, phone_pat, "PHONE", report, matches=matches)
    text = _sub_simple(text, PASSPORT_PAT, "PASSPORT", report, matches=matches)
    text = _sub_keep_label(text, ACCOUNT_CONTEXT_PAT, "ACCOUNT", report, value_group="value", matches=matches)
    if use_email:
        text = _sub_simple(text, EMAIL_PAT, "EMAIL", report, matches=matches, value_group="value")
    if use_name:
        text = _sub_keep_label_when(
            text,
            NAME_CONTEXT_PAT,
            "NAME",
            report,
            value_group="name",
            # 강한 인명 라벨(성명/이름/신청인/담당자 등) 컨텍스트 → 성씨 화이트리스트 하드게이트 해제(C-2)
            should_mask=lambda m: is_labeled_person_name_value(m.group("name")),
            matches=matches,
        )
    if use_address:
        text = _sub_keep_label(
            text,
            POSTAL_CODE_PREFIX_PAT,
            "ADDRESS",
            report,
            value_group="value",
            matches=matches,
        )
        text = _sub_keep_label(
            text,
            POSTAL_CODE_ADDRESS_LABEL_PAT,
            "ADDRESS",
            report,
            value_group="value",
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            ADDRESS_CONTEXT_PAT,
            "ADDRESS",
            report,
            value_group="addr",
            should_mask=lambda m: is_likely_address_value(m.group("addr"), _region_terms("sido")[:40]),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            ADDRESS_LABEL_PAT,
            "ADDRESS",
            report,
            value_group="addr",
            should_mask=lambda m: is_likely_address_value(m.group("addr"), _region_terms("sido")[:40]),
            matches=matches,
        )
        for address_pat in _national_address_patterns():
            text = _sub_simple(text, address_pat, "ADDRESS", report, matches=matches, value_group="value")

    if use_place:
        for p in PLACE_PATS:
            text = _sub_simple(text, p, "PLACE", report, matches=matches)
        for p in _weak_place_patterns():
            text = _sub_simple(
                text,
                p,
                "WEAK_PLACE",
                report,
                matches=matches,
                value_group="value",
                action="review",
                transform=False,
            )

    # 주소 상세는 주소/지명 문맥이 있을 때만
    if use_address and (("[PLACE]" in text) or ("주소" in text)):
        text = _sub_simple(text, ROAD_NO_PAT, "ADDR_DETAIL", report, matches=matches)
        text = _sub_simple(text, LOT_NO_PAT, "LOT_NO", report, matches=matches)

    if use_legal_party:
        text = _sub_keep_label_when(
            text,
            LEGAL_PARTY_PAT,
            "LEGAL_PARTY",
            report,
            value_group="value",
            # 콜론 라벨(원고:/피고: 등) 확정 당사자 → 성씨 화이트리스트 하드게이트 해제(C-2)
            should_mask=lambda m: is_labeled_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            LEGAL_PARTY_INLINE_NAME_PAT,
            "LEGAL_PARTY",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            LEGAL_PARTY_REP_INLINE_PAT,
            "LEGAL_PARTY",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )

        if profile == "legal":
            # OCR/PDF 추출에서 공백이 붙는 헤더형 보강: "사건원고홍길동피고김철수"
            head_len = min(len(text), 1200)
            head = text[:head_len]
            compact_party_pat = re.compile(
                r"(?P<label>(?:원\s*고|피\s*고|신\s*청\s*인|피\s*신\s*청\s*인|청\s*구\s*인|피\s*청\s*구\s*인|항\s*고\s*인|피\s*항\s*고\s*인|채\s*권\s*자|채\s*무\s*자))"
                r"\s*(?P<value>(?!(?:측|대리인|소송대리인|법무법인|법률사무소|주장|주장의|주장과|주장에))[가-힣]{2,6}?)"
                r"(?=(?:원\s*고|피\s*고|신\s*청\s*인|피\s*신\s*청\s*인|청\s*구\s*인|피\s*청\s*구\s*인|항\s*고\s*인|피\s*항\s*고\s*인|채\s*권\s*자|채\s*무\s*자|\s|\n|$))"
            )
            head = _sub_keep_label(head, compact_party_pat, "LEGAL_PARTY", report, value_group="value", matches=matches)
            text = head + text[head_len:]

            # 헤더에서 식별된 당사자 실명은 본문 전역에서 동일 토큰으로 추가 치환
            party_names = sorted(
                {
                    m.text for m in (matches or [])
                    if getattr(m, "tag", "") == "LEGAL_PARTY" and m.text and len(m.text) >= 2
                },
                key=len,
                reverse=True,
            )
            for name in party_names:
                if not name:
                    continue
                if len(name) >= 3:
                    # 3자 이상 실명은 우연한 부분일치 위험이 낮아 무조건 전역 치환
                    text = _sub_simple(
                        text,
                        re.compile(re.escape(name)),
                        "LEGAL_PARTY",
                        report,
                        matches=matches,
                    )
                else:
                    # 2자 실명은 한글 경계 처리(H-5): 앞이 한글이 아니고, 뒤가 한글이 아니거나
                    # 조사가 붙는 경우에만 치환. '이가방'→'[LEGAL_PARTY]방' 문서 파괴 방지.
                    text, _n = _replace_two_char_party_name(name, text, report, matches)

    if use_company:
        text = _sub_keep_label_when(
            text,
            COMPANY_LABEL_PAT,
            "COMPANY",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_company_value(m.group("value")),
            matches=matches,
        )
        text = _sub_simple(text, COMPANY_INLINE_PAT, "COMPANY", report, matches=matches, value_group="value")

    if use_court:
        if profile == "legal":
            text = _mask_legal_courts_with_citation_preserve(text, report, matches)
        else:
            text = _sub_court_when(text, COURT_PAT, report, matches)
            # 자간 변형 법원명 보강(M-4): 비자간형은 위에서 이미 처리됨
            text = _sub_court_when(text, COURT_SPACED_PAT, report, matches)

    if use_case_title:
        text = _sub_keep_label(text, CASE_TITLE_LABEL_PAT, "CASE_TITLE", report, value_group="value", matches=matches)
        if profile == "legal":
            text = _sub_keep_label(text, CASE_TITLE_GENERIC_LABEL_PAT, "CASE_TITLE", report, value_group="value", matches=matches)
            text = _sub_case_title_line(text, CASE_LINE_TITLE_PAT, "CASE_TITLE", report, matches=matches)

    if use_case_number:
        if profile == "legal":
            text = _mask_legal_case_numbers_with_citation_preserve(text, report, matches)
        else:
            text = _sub_keep_label(text, CASE_NUMBER_CONTEXT_PAT, "CASE_NUMBER", report, value_group="value", matches=matches)

    if use_law_firm:
        text = _sub_keep_label_when(
            text,
            LAW_FIRM_LABEL_PAT,
            "LAW_FIRM",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_law_firm_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            LAW_FIRM_INLINE_PAT,
            "LAW_FIRM",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_law_firm_value(m.group("value")),
            matches=matches,
        )

    if use_attorney:
        text = _sub_keep_label(text, ATTORNEY_PAT, "ATTORNEY", report, value_group="value", matches=matches)

    if use_approval_line:
        text = _sub_approval_name_when(text, APPROVAL_LINE_PAT, report, matches)
        text = _sub_approval_name_when(text, OFFICIAL_ROLE_NAME_PAT, report, matches)
        text = _sub_approval_name_when(text, APPROVAL_GRADE_OCR_PAT, report, matches)
        text = _sub_approval_name_when(text, DEPT_ROLE_NAME_COMPACT_PAT, report, matches)
        text = _sub_approval_name_when(text, OFFICIAL_COMBINED_ROLE_NAME_PAT, report, matches)
        text = _sub_approval_name_when(text, ACTING_APPROVER_NAME_PAT, report, matches)
        text = _sub_approval_name_when(text, APPROVAL_TABLE_LINE_PAT, report, matches, table_line=True)
        text = _sub_approval_inline_multi_when(text, APPROVAL_TABLE_INLINE_MULTI_PAT, report, matches)
        text = _sub_approval_name_when(text, APPROVAL_ROLE_PAREN_NAME_PAT, report, matches)
        text = _sub_approval_name_when(text, APPROVAL_ROLE_DATE_NAME_PAT, report, matches)
        text = _sub_approval_name_when(text, APPROVAL_DATE_NAME_PAT, report, matches)
        text = _sub_keep_label(text, APPROVAL_FLOW_CONTEXT_PAT, "APPROVAL_FLOW", report, value_group="value", matches=matches)
        text = _sub_simple(text, APPROVAL_FLOW_LINE_PAT, "APPROVAL_FLOW", report, matches=matches, value_group="value")

    if use_region_context:
        text = _sub_keep_label_when(
            text,
            REGION_CONTEXT_PAT,
            "REGION",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_address_value(m.group("value"), _region_terms("sido")[:40]),
            matches=matches,
        )

    if use_doc_meta:
        # '시행 ○○과-1234'는 현장 누락이 잦아 DOC_META 일반룰보다 먼저 고정 처리
        text = _sub_keep_label_when(
            text,
            SIHAENG_DOCNO_PAT,
            "DOC_META",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_doc_meta_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            DOC_META_PAT,
            "DOC_META",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_doc_meta_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            DOC_META_OCR_PAT,
            "DOC_META",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_doc_meta_value(m.group("value")),
            matches=matches,
        )
        text = _sub_simple(text, DOC_REF_INLINE_PAT, "DOC_META", report, matches=matches, value_group="value")
        text = _sub_simple(text, TEAM_EXT_PAT, "DOC_META", report, matches=matches, value_group="value")
        text = _sub_simple(text, PUBLIC_LEVEL_PAT, "DOC_META", report, matches=matches, value_group="value")

    matches = [
        replace(match, occurrence_id=match_occurrence_id(match, match.start, match.end))
        for match in matches
    ]
    return text, report, matches


# -----------------------------
# 2) 입력 추출 엔진
# -----------------------------



def resolve_output_artifacts(opts: dict[str, Any] | None = None) -> set[str]:
    opts = normalize_opts(opts)
    label = _output_artifact_value(str(opts.get("output_artifacts", "pdf_safe_report") or "pdf_safe_report"))
    if label not in OUTPUT_ARTIFACTS_MAP:
        label = "pdf_safe_report"
    return set(OUTPUT_ARTIFACTS_MAP[label])


def document_kind_for_path(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".txt", ".md", ".csv", ".log"}:
        return "text"
    return "unsupported"




# -----------------------------
# 3) PDF 네이티브 레닥션
# -----------------------------




# -----------------------------
# 4) 처리 파이프라인
# -----------------------------


# 안전 리포트(safe_report JSON)는 사용자가 지정한 출력 폴더(outdir)에 절대 남기지
# 않는다. outdir이 어디든(단일 미리보기 작업폴더든, 문서 일괄 실행 시 사용자 폴더든)
# 리포트는 항상 아래 내부 디렉터리에만 생성한다. 검증/게이트(Rust
# report_allows_final_save·프론트 게이트)는 이 내부 파일을 계속 읽는다.
#   - 위치: 시스템 임시 폴더 아래 makiiing_v2_internal_reports/<세션 mkdtemp>
#   - 수명: Python 엔진 프로세스는 호출마다 단명하므로 auto-delete 임시폴더는 쓸 수
#     없다(Rust가 프로세스 종료 후 읽어야 함). mkdtemp로 만든 세션 디렉터리는
#     명시적으로 지우기 전까지 남으므로 리포트가 살아 있는다. 누적 방지를 위해 오래된
#     세션 디렉터리는 best-effort로 정리한다.
_INTERNAL_REPORT_BASE_NAME = "makiiing_v2_internal_reports"
_INTERNAL_REPORT_MAX_AGE_SEC = 24 * 60 * 60


def _internal_report_base_dir() -> str:
    base = os.path.join(tempfile.gettempdir(), _INTERNAL_REPORT_BASE_NAME)
    os.makedirs(base, exist_ok=True)
    return base


def _prune_stale_internal_report_dirs(base: str) -> None:
    # 오래된 세션 리포트 디렉터리를 best-effort로 제거(누적 방지). 실패는 무시한다.
    now = time.time()
    try:
        entries = os.listdir(base)
    except OSError:
        return
    for entry in entries:
        path = os.path.join(base, entry)
        try:
            if not os.path.isdir(path):
                continue
            if now - os.path.getmtime(path) <= _INTERNAL_REPORT_MAX_AGE_SEC:
                continue
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def internal_report_dir() -> str:
    """리포트 전용 내부 세션 디렉터리를 만들어 경로를 반환한다."""
    base = _internal_report_base_dir()
    _prune_stale_internal_report_dirs(base)
    return tempfile.mkdtemp(prefix="report_", dir=base)


def safe_output_paths(infile: str, outdir: str | None = None) -> dict[str, str]:
    base = os.path.basename(infile)
    name, _ = os.path.splitext(base)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = outdir or os.path.dirname(infile)
    # 리포트는 outdir이 아닌 내부 세션 디렉터리에만 생성한다(파일명 패턴은 유지).
    report_dir = internal_report_dir()
    return {
        "masked_txt": os.path.join(out_dir, f"{name}.masked.{ts}.txt"),
        "extracted_txt": os.path.join(out_dir, f"{name}.extracted.{ts}.txt"),
        "report_json": os.path.join(report_dir, "safe_report.json"),
        "masked_pdf": os.path.join(out_dir, f"{name}.final_masked_black.{ts}.pdf"),
        "labeled_pdf": os.path.join(out_dir, f"{name}.final_masked_labeled.{ts}.pdf"),
        "manual_pdf": os.path.join(out_dir, f"{name}_manual_redacted.pdf"),
    }


class SafeReport(dict):
    """Serializable count-only report with a non-serialized runtime manifest."""

    def __init__(self, payload: dict[str, Any], runtime_manifest: dict[str, Any]) -> None:
        super().__init__(payload)
        self.runtime_manifest = runtime_manifest


def _safe_runtime_review_items(items: Any) -> list[dict[str, Any]]:
    """Project runtime-only review evidence onto a PII-free geometry schema."""
    if not isinstance(items, list):
        return []
    safe_items: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        safe: dict[str, Any] = {}
        for key in ("tag", "status", "display_token", "source", "reason_code"):
            value = item.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_\-\[\] :]{1,128}", value):
                safe[key] = value
        for key in ("page", "count"):
            value = item.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[key] = value
        bbox = item.get("bbox")
        if isinstance(bbox, dict):
            normalized_bbox: dict[str, float] = {}
            for key in ("x", "y", "width", "height"):
                value = bbox.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
                    normalized_bbox[key] = float(value)
            if len(normalized_bbox) == 4 and normalized_bbox["width"] >= 0 and normalized_bbox["height"] >= 0:
                safe["bbox"] = normalized_bbox
        rects = item.get("rects")
        if isinstance(rects, list):
            normalized_rects: list[dict[str, float]] = []
            for rect in rects:
                if not isinstance(rect, dict):
                    continue
                values = {key: rect.get(key) for key in ("x0", "y0", "x1", "y1")}
                if all(
                    isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
                    for value in values.values()
                ):
                    normalized = {key: float(value) for key, value in values.items()}
                    if normalized["x1"] >= normalized["x0"] and normalized["y1"] >= normalized["y0"]:
                        normalized_rects.append(normalized)
            if normalized_rects:
                safe["rects"] = normalized_rects
        reason_codes = item.get("reason_codes")
        if isinstance(reason_codes, list):
            codes = [
                code for code in reason_codes
                if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_:-]{0,95}", code)
            ]
            if codes:
                safe["reason_codes"] = codes
        if safe:
            safe_items.append(safe)
    return safe_items

def runtime_manifest_for_report(report: dict[str, Any]) -> dict[str, Any]:
    manifest = getattr(report, "runtime_manifest", None)
    if not isinstance(manifest, dict):
        return {"outputs": {}, "review_items": []}
    return {
        "outputs": {
            key: value for key, value in manifest.get("outputs", {}).items()
            if isinstance(key, str) and isinstance(value, bool)
        } if isinstance(manifest.get("outputs"), dict) else {},
        "review_items": _safe_runtime_review_items(manifest.get("review_items")),
        "detected_spans": manifest.get("detected_spans", []),
        "detection_candidates": manifest.get("detection_candidates", []),
    }



def build_safe_report(
    input_file: str,
    opts: dict[str, Any],
    counts: dict[str, int],
    redaction_matches: list[RedactionMatch],
    extract_meta: dict[str, Any],
    pdf_redaction_result: dict[str, Any],
    output_paths: dict[str, Any],
    chunk_queue: dict[str, Any] | None = None,
    llm_refine: dict[str, Any] | None = None,
    document_context: DocumentContext | None = None,
    source_text: str = "",
) -> dict[str, Any]:
    opts = normalize_opts(opts)
    artifacts = resolve_output_artifacts(opts)
    region_meta = region_data_metadata()
    deidentification_policy = normalize_deidentification_policy(opts.get("deidentification_policy", "token"))
    safe_pdf = _safe_pdf_redaction_summary(pdf_redaction_result)
    document_kind = document_kind_for_path(input_file)
    quality_gate_passed = evaluate_quality_gate(safe_pdf)
    runtime_review_items = list(pdf_redaction_result.get("review_items", []))
    if not runtime_review_items:
        runtime_review_items = review_items_for_matches(
            redaction_matches,
            counts,
            document_context=document_context,
        )
    review_items = safe_review_item_summaries(runtime_review_items)
    detected_spans = merge_detection_spans(detection_spans_from_matches(source_text, redaction_matches)) if source_text else []
    detection_candidates = safe_detection_candidate_reports(source_text, redaction_matches) if source_text else []
    needs_manual_review = (not quality_gate_passed) or any(
        item.get("status") in {"missing_pdf_rect", "needs_review", "residual_found"} for item in review_items
    )
    active_verification = safe_pdf.get("verification", {})
    runtime_manifest = {
        "outputs": {name: bool(path) for name, path in output_paths.items()},
        "review_items": _safe_runtime_review_items(runtime_review_items),
        "detected_spans": detected_spans,
        "detection_candidates": detection_candidates,
    }
    return SafeReport({
        "app_version": APP_VERSION,
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "raw_values_saved": False,
        "raw_text_returned": False,
        "input": {
            "kind": document_kind,
            "path_saved_in_report": False,
        },
        "extract": {
            "schema_version": extract_meta.get("schema_version"),
            "engine_selected": extract_meta.get("engine_selected", opts.get("extract_engine", "auto")),
            "engine_used": extract_meta.get("engine_used"),
            "engine_chain": list(extract_meta.get("engine_chain", []) or []),
            "fallback_chain": list(extract_meta.get("fallback_chain", []) or []),
            "duration_sec": extract_meta.get("duration_sec"),
            "notes_count": len(extract_meta.get("notes", []) or []),
            "chars": extract_meta.get("chars", 0),
            "page_count": extract_meta.get("page_count", 0),
        },
        "rules": {
            "profile": _profile_value(str(opts.get("profile", "mixed") or "mixed")),
            "region_scope": str(opts.get("region_scope", "national") or "national"),
            "region_data_source": region_meta["region_data_source"],
            "region_data_version": region_meta["region_data_version"],
            "region_data_is_seed": region_meta["region_data_is_seed"],
            "display_mode": str(opts.get("display_mode", "black") or "black"),
            "deidentification_policy": deidentification_policy,
            "output_artifacts": sorted(artifacts),
            "custom_keywords_count": len(_parse_custom_keywords(opts.get("custom_keywords", ""))),
            "custom_regions_count": len(_parse_custom_keywords(opts.get("custom_regions", ""))),
            "raw_custom_keywords_saved": False,
        },
        "counts": counts,
        "detection_summary": {
            "span_count": len(detected_spans),
            "candidate_count": len(detection_candidates),
        },
        "review_items": review_items,
        "document_context": safe_document_context_summary(document_context),
        "text_deidentification": {
            "policy": deidentification_policy,
            "scope": "text_preview_and_txt_output_only",
            "final_submission_evidence": False,
            "native_redaction_required": True,
            "warning": "partial/pseudonym text output is for review or test-data readability and is not proof of final document redaction",
        },
        "chunk_queue": chunk_queue or {},
        "local_llm_refine": sanitize_for_logging(llm_refine or {}),
        "pdf_redaction": safe_pdf,
        "document_redaction": safe_pdf,
        "product_checks": {
            "pdf_input": Path(input_file).suffix.lower() == ".pdf",
            "native_redaction_verified": bool(active_verification.get("verified", False)),
            "text_surface_verified": bool(active_verification.get("text_surface_verified", active_verification.get("verified", False))),
            "final_submission_allowed": quality_gate_passed,
            "text_deidentification_final_submission_evidence": False,
            "native_redaction_status": safe_pdf.get("status"),
            "native_redaction_reason_code": safe_pdf.get("reason_code"),
            "quality_gate_passed": quality_gate_passed,
            "needs_manual_review": needs_manual_review,
            "raw_values_saved": False,
        },
        "warnings": (
            ([region_meta["region_data_warning"]] if region_meta.get("region_data_warning") else [])
            + (["partial/pseudonym text de-identification is review-only; use verified PDF redaction for submission"] if deidentification_policy != "token" else [])
        ),
        "outputs": {
            "extracted_file": None,
            "masked_file": None,
            "masked_pdf_file": None,
            "labeled_pdf_file": None,
            "safe_report_path": None,
            "report_path": None,
            "manual_pdf_path": None,
            "preview_pdf_source_file": None,
        },
    }, runtime_manifest=runtime_manifest)





def _manifest_digest(material: dict[str, Any], prefix: str = "") -> str:
    """Semantic IDs deliberately bind evidence, never candidate ordering or text."""
    return prefix + hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()[:24]


def _canonical_json_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


_OCCURRENCE_ID_PATTERN = re.compile(r"occ_[0-9a-f]{24}\Z")
_IDENTITY_COORDINATE_SCALE = 1_000_000


def _identity_coordinate(value: float) -> int:
    return int(math.floor(value * _IDENTITY_COORDINATE_SCALE + 0.5))


def _occurrence_id(document_hash: str, revision: int, page: int, rects: list[dict[str, float]],
                   tag: str, category: str, value_hash: str, source: str, policy: str,
                   proposed_action: str, *, segment_id: str | None = None, region_id: str | None = None) -> str:
    """Return the canonical, occurrence-scoped public identity."""
    if (
        not isinstance(document_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", document_hash.lower()) is None
        or type(revision) is not int
        or revision < 1
        or type(page) is not int
        or page < 0
        or not all(isinstance(value, str) and value for value in (tag, category, source, policy))
        or (segment_id is not None and (not isinstance(segment_id, str) or not segment_id))
        or (region_id is not None and (not isinstance(region_id, str) or not region_id))
        or re.fullmatch(r"[0-9a-f]{64}", value_hash.lower()) is None
        or proposed_action not in {"mask", "review", "exclude"}
    ):
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    normalized_rects: list[list[int]] = []
    for rect in rects:
        if not isinstance(rect, dict) or set(rect) != {"x0", "y0", "x1", "y1"}:
            raise ValueError("OCCURRENCE_IDENTITY_INVALID")
        try:
            values = {key: float(rect[key]) for key in ("x0", "y0", "x1", "y1")}
        except (TypeError, ValueError):
            raise ValueError("OCCURRENCE_IDENTITY_INVALID") from None
        if (
            not all(math.isfinite(value) and value >= 0.0 for value in values.values())
            or values["x1"] <= values["x0"]
            or values["y1"] <= values["y0"]
        ):
            raise ValueError("OCCURRENCE_IDENTITY_INVALID")
        normalized_rects.append([
            _identity_coordinate(values[key])
            for key in ("x0", "y0", "x1", "y1")
        ])
    if not normalized_rects:
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    material = {
        "analysisRevision": revision, "category": category, "documentHash": document_hash.lower(),
        "page": page, "policy": policy, "proposedAction": proposed_action,
        "rects": sorted(normalized_rects),
        "source": source, "tag": tag, "valueHash": value_hash.lower(),
        **(
            {"segmentId": segment_id, "regionId": region_id}
            if segment_id is not None else {}
        ),
    }
    occurrence_id = "occ_" + hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()[:24]
    if _OCCURRENCE_ID_PATTERN.fullmatch(occurrence_id) is None:
        raise ValueError("OCCURRENCE_IDENTITY_INVALID")
    return occurrence_id



def _manifest_rect(word: Any) -> dict[str, float] | None:
    x0, y0, x1, y1 = (float(value) for value in word.bbox)
    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
        return None
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _page_word_offsets(page: Any) -> list[tuple[int, int, Any, dict[str, float]]]:
    """Return only extractor-proven, ordered page-local word offsets."""
    cursor = 0
    result: list[tuple[int, int, Any, dict[str, float]]] = []
    for word in page.words:
        rect = _manifest_rect(word)
        token = str(word.text)
        start = getattr(word, "page_start", None)
        end = getattr(word, "page_end", None)
        if (
            rect is None
            or not token
            or type(start) is not int
            or type(end) is not int
            or start < cursor
            or end <= start
            or end > len(page.text)
            or (
                page.text[start:end] != token
                and (
                    getattr(word, "source", "") != "pymupdf_table_cell"
                    or "".join(character for character in page.text[start:end] if not character.isspace())
                    != "".join(character for character in token if not character.isspace())
                )
            )
        ):
            raise ValueError("WORD_OFFSET_EVIDENCE_INVALID")
        cursor = end
        result.append((start, end, word, rect))
    if not result:
        raise ValueError("WORD_OFFSET_EVIDENCE_INVALID")
    return result


def _compact_keyword_text(value: str) -> str:
    return "".join(
        character.casefold()
        for character in value
        if character.isalnum() or character == "_"
    )


def _same_keyword_line(left: dict[str, float], right: dict[str, float]) -> bool:
    line_height = max(left["y1"] - left["y0"], right["y1"] - right["y0"])
    left_center = (left["y0"] + left["y1"]) / 2
    right_center = (right["y0"] + right["y1"]) / 2
    return abs(right_center - left_center) <= max(2.0, line_height * 0.75)


def _source_offset_for_compact_index(value: str, compact_index: int) -> int:
    seen = 0
    for index, character in enumerate(value):
        if not (character.isalnum() or character == "_"):
            continue
        if seen == compact_index:
            return index
        seen += 1
    return 0


def _keyword_words_are_neighbors(
    first: dict[str, float], previous: dict[str, float], candidate: dict[str, float],
) -> bool:
    line_height = max(
        first["y1"] - first["y0"],
        previous["y1"] - previous["y0"],
        candidate["y1"] - candidate["y0"],
    )
    if _same_keyword_line(first, candidate):
        return _same_keyword_line(previous, candidate) and (
            -line_height <= candidate["x0"] - previous["x1"] <= max(36.0, line_height * 4)
        )
    first_to_candidate_gap = candidate["y0"] - first["y1"]
    if not (
        0 <= first_to_candidate_gap <= max(24.0, line_height * 2)
        and abs(candidate["x0"] - first["x0"]) <= max(144.0, line_height * 12)
    ):
        return False
    if _same_keyword_line(previous, candidate):
        return -line_height <= candidate["x0"] - previous["x1"] <= max(36.0, line_height * 4)
    return 0 <= candidate["y0"] - previous["y1"] <= max(24.0, line_height * 2)


def _custom_keyword_word_matches(
    page_text: str,
    words: list[tuple[int, int, Any, dict[str, float]]],
    keyword: str,
) -> list[tuple[int, int, list[tuple[Any, dict[str, float]]]]]:
    target = _compact_keyword_text(keyword)
    if not target:
        return []
    matches: list[tuple[int, int, list[tuple[Any, dict[str, float]]]]] = []
    for first_index, (start, end, word, rect) in enumerate(words):
        value = _compact_keyword_text(str(word.text))
        if not value:
            continue
        contained_start = value.find(target)
        if contained_start >= 0:
            source_text = page_text[start:end]
            matches.append((
                start + _source_offset_for_compact_index(source_text, contained_start),
                end,
                [(word, rect)],
            ))
            continue
        suffix_start = next((index for index in range(len(value)) if target.startswith(value[index:])), None)
        if suffix_start is None:
            continue
        selected = [(word, rect)]
        matched = value[suffix_start:]
        match_end = end
        for next_start, next_end, next_word, next_rect in words[first_index + 1:]:
            if page_text[match_end:next_start].strip() or not _keyword_words_are_neighbors(rect, selected[-1][1], next_rect):
                break
            next_value = _compact_keyword_text(str(next_word.text))
            if not next_value or not target.startswith(matched + next_value):
                break
            selected.append((next_word, next_rect))
            matched += next_value
            match_end = next_end
            if matched == target:
                matches.append((start, match_end, selected))
                break
    return matches


def _exact_pdf_subtext_rect(
    pdf_path: str,
    page_index: int,
    value_text: str,
    container: dict[str, float],
) -> dict[str, float] | None:
    """Resolve value glyph geometry from the PDF text layer, never by width estimation."""
    if not value_text:
        return None
    try:
        import fitz  # type: ignore

        with fitz.open(pdf_path) as document:
            if not 0 <= page_index < document.page_count:
                return None
            matches = document[page_index].search_for(value_text)
    except Exception:
        return None
    contained = [
        match for match in matches
        if container["x0"] - 0.5 <= match.x0
        and container["y0"] - 0.5 <= match.y0
        and match.x1 <= container["x1"] + 0.5
        and match.y1 <= container["y1"] + 0.5
    ]
    if len(contained) != 1:
        return None
    match = contained[0]
    return {"x0": match.x0, "y0": match.y0, "x1": match.x1, "y1": match.y1}




def _effective_policy_material(opts: dict[str, Any]) -> dict[str, Any]:
    public_option_keys = (
        "rrn", "phone", "business_reg", "name", "address", "place",
        "legal_party", "company", "court", "case_title", "case_number",
        "law_firm", "attorney", "approval_line", "region_context", "doc_meta", "email",
        "pdf_redaction", "custom_keywords", "extract_engine", "profile",
        "output_artifacts", "display_mode", "deidentification_policy",
        "region_scope", "custom_regions", "return_text_preview",
    )
    material = {key: opts[key] for key in public_option_keys}
    thresholds = {
        "auto_mask_threshold": opts.get("auto_mask_threshold", opts.get("auto_threshold")),
        "review_threshold": opts.get("review_threshold"),
    }
    for key, value in thresholds.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError("THRESHOLD_ARTIFACT_MISSING")
        material[key] = float(value)
    if not 0.0 <= material["review_threshold"] <= material["auto_mask_threshold"] <= 1.0:
        raise ValueError("THRESHOLD_ARTIFACT_INVALID")
    return material
def _threshold_artifact(auto_mask_threshold: Any, review_threshold: Any) -> dict[str, Any]:
    if (
        not isinstance(auto_mask_threshold, (int, float))
        or isinstance(auto_mask_threshold, bool)
        or not isinstance(review_threshold, (int, float))
        or isinstance(review_threshold, bool)
        or not math.isfinite(float(auto_mask_threshold))
        or not math.isfinite(float(review_threshold))
        or not 0.0 <= float(review_threshold) <= float(auto_mask_threshold) <= 1.0
    ):
        raise ValueError("THRESHOLD_ARTIFACT_INVALID")
    auto_value = float(auto_mask_threshold)
    review_value = float(review_threshold)
    material = {
        "auto_threshold": auto_value,
        "policy_version": "masking-policy-v1",
        "review_threshold": review_value,
    }
    content_hash = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "version": "thresholds-v2",
        "content_hash": content_hash,
        "auto_mask_threshold": auto_value,
        "review_threshold": review_value,
    }
def _measured_region_position(marker: str, rect: dict[str, float], page_rects: tuple[PdfRect, ...]) -> bool:
    """Accept only measured header/footer placement for a profile label."""
    if not page_rects:
        return False
    page_top = min(item.y0 for item in page_rects)
    page_bottom = max(item.y1 for item in page_rects)
    page_height = page_bottom - page_top
    if page_height <= 0:
        return False
    relative_y = ((rect["y0"] + rect["y1"]) / 2 - page_top) / page_height
    return relative_y >= 0.65 if marker in {"발신", "담당", *_FOOTER_CONTACT_LABELS} else relative_y <= 0.35


_PROFILE_REGION_BOUNDARY_TOLERANCE = 1e-6


def _rects_within_region(rects: list[dict[str, float]], region_rects: tuple[PdfRect, ...]) -> bool:
    return bool(rects) and all(any(
        region.x0 <= rect["x0"] + _PROFILE_REGION_BOUNDARY_TOLERANCE
        and region.y0 <= rect["y0"] + _PROFILE_REGION_BOUNDARY_TOLERANCE
        and rect["x1"] <= region.x1 + _PROFILE_REGION_BOUNDARY_TOLERANCE
        and rect["y1"] <= region.y1 + _PROFILE_REGION_BOUNDARY_TOLERANCE
        for region in region_rects
    ) for rect in rects)


def _profile_candidate_matches_occurrence(
    occurrence: dict[str, Any],
    page: int,
    rects: list[dict[str, float]],
    value_hash: str,
) -> bool:
    return (
        occurrence["page"] == page
        and occurrence["value_hash"] == value_hash
        and any(
            max(existing_rect["x0"], candidate_rect["x0"])
            <= min(existing_rect["x1"], candidate_rect["x1"])
            and max(existing_rect["y0"], candidate_rect["y0"])
            <= min(existing_rect["y1"], candidate_rect["y1"])
            for existing_rect in occurrence["rects"]
            for candidate_rect in rects
        )
    )


def _label_value_distance(label_rect: dict[str, float], value_rects: list[dict[str, float]]) -> float | None:
    """Measure the intentional horizontal-only gap used by fixed label rows."""
    if not value_rects:
        return None
    return min(max(value_rect["x0"] - label_rect["x1"], 0.0) for value_rect in value_rects)


_HEADER_LABEL_SUFFIXES = {
    "결재": frozenset({"결재"}),
    "검토": frozenset({"검토"}),
    "승인": frozenset({"승인"}),
    "attachment": frozenset({"붙임", "첨부"}),
    "수신": frozenset({"수신", "수신자"}),
    "참조": frozenset({"참조"}),
    "시행": frozenset({"시행"}),
    "발신": frozenset({"발신"}),
    "담당": frozenset({"담당"}),
    "문서번호": frozenset({"문서번호"}),
    "방침번호": frozenset({"방침번호"}),
    "생산등록번호": frozenset({"생산등록번호"}),
    "등록일": frozenset({"등록일", "생산등록일"}),
    "결재일자": frozenset({"결재일자"}),
    "결재일": frozenset({"결재일"}),
    "공개여부": frozenset({"공개여부"}),
    "우편번호": frozenset({"우편번호", "우"}),
    "전화": frozenset({"전화", "전화번호", "대표전화"}),
    "전송": frozenset({"전송", "팩스", "fax"}),
    "이메일": frozenset({"이메일", "email", "전자우편"}),
    "title": frozenset({"제목"}),
}

_COMMON_DOCUMENT_HEADER_LABELS = frozenset({"문서번호", "결재일자", "공개여부"})
_DISPATCH_HEADER_LABELS = frozenset({"수신", "시행"})
_INTERNAL_REVIEW_HEADER_LABELS = frozenset({"검토"})
_FOOTER_CONTACT_LABELS = frozenset({"우편번호", "전화", "전송", "이메일"})
_COMPACT_FOOTER_POSTAL_CODE_RE = re.compile(r"우\s?\d{5}\Z")
_DISPATCH_TITLE_TOKENS = frozenset({"알림", "통보", "송부"})
_INTERNAL_REVIEW_TITLE_RE = re.compile(r"검토보고(?:서)?(?:\([^()]{1,12}\))?\Z")
_PREREVIEW_TITLE_RE = re.compile(r"(?:사전\s*검토서|사전\s*검토\s*보고서)\Z")
_PREREVIEW_LABELS = frozenset({"공모명", "공모사업명"})
_TITLE_LINE_MIN_HEIGHT = 18.0
_RUNNING_TITLE_LINE_MIN_HEIGHT = 14.0
_TITLE_LINE_MAX_PAGE_RATIO = 0.7
_TITLE_LINE_LEFT_RATIO = 0.4
_TITLE_LINE_CENTER_TOLERANCE_RATIO = 0.12
_TITLE_LINE_MAX_CHARACTERS = 32
_RUNNING_TITLE_TOP_RATIO = 0.2
_RUNNING_TITLE_MIN_CHARACTERS = 8
_RUNNING_TITLE_MIN_WIDTH_RATIO = 0.5
_RUNNING_TITLE_NON_TITLE_PREFIX_RE = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ□■▪●○]")
_TITLE_LINE_PREDICATE_RE = re.compile(
    r"(?:합니다|드립니다|됩니다|알립니다|했습니다|하였다|했다|한다|된다|입니다|였습니다|습니다|이었다|이다)[.!?]?"
)

_CONTINUATION_EDGE_RATIO = 0.15
_CONTINUATION_SIGNATURE_MIN_TOKENS = 2
_CONTINUATION_SIGNATURE_MIN_CHARACTERS = 4
_FOOTER_PAGE_NUMBER_RE = re.compile(
    r"(?<!\d)(?:-\s*)?(?P<page>[1-9]\d{0,3})(?:\s*-\s*|\s*/\s*(?P<total>[1-9]\d{0,3}))(?!\d)"
)
_FOOTER_STANDALONE_PAGE_NUMBER_RE = re.compile(r"\A\s*(?P<page>[1-9]\d{0,3})\s*\Z")


def _edge_tokens(
    words: list[tuple[int, int, Any, dict[str, float]]], page_height: float,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if page_height <= 0:
        return (), ()
    edge = page_height * _CONTINUATION_EDGE_RATIO
    header = tuple(str(word.text).strip() for _, _, word, rect in words if rect["y1"] <= edge)
    footer = tuple(str(word.text).strip() for _, _, word, rect in words if rect["y0"] >= page_height - edge)
    return header, footer


def _edge_signatures(header: tuple[str, ...], footer: tuple[str, ...]) -> frozenset[str]:
    signatures: set[str] = set()
    for tokens in (header, footer):
        normalized = tuple(re.sub(r"\W+", "", token) for token in tokens)
        source = " ".join(token for token in normalized if token)
        if len(normalized) >= _CONTINUATION_SIGNATURE_MIN_TOKENS and len(source) >= _CONTINUATION_SIGNATURE_MIN_CHARACTERS:
            signatures.add(hashlib.sha256(source.encode("utf-8")).hexdigest())
    return frozenset(signatures)


def _footer_page_number(footer: tuple[str, ...]) -> int | None:
    footer_text = " ".join(footer)
    match = _FOOTER_STANDALONE_PAGE_NUMBER_RE.fullmatch(footer_text)
    if match is None:
        match = _FOOTER_PAGE_NUMBER_RE.search(footer_text)
    return int(match.group("page")) if match is not None else None


def _continuity_evidence(
    words: list[tuple[int, int, Any, dict[str, float]]],
    page_height: float,
    start_signals: set[str],
    previous_page_number: int | None,
    previous_edge_signatures: frozenset[str],
) -> tuple[frozenset[str], int | None, frozenset[str]]:
    header, footer = _edge_tokens(words, page_height)
    page_number = _footer_page_number(footer)
    edge_signatures = _edge_signatures(header, footer)
    signals = {CONTINUATION_NO_START_SIGNAL} if not start_signals else set()
    if (
        not start_signals
        and page_number != 1
        and previous_page_number is not None
        and page_number == previous_page_number + 1
    ):
        signals.add(CONTINUATION_PAGE_NUMBER_SEQUENCE)
    if not start_signals and previous_edge_signatures & edge_signatures:
        signals.add(CONTINUATION_REPEATED_HEADER_FOOTER)
    return frozenset(signals), page_number, edge_signatures


def _normalized_header_label(value: str) -> str | None:
    compact = re.sub(r"[\s:：;；,./]+", "", value)
    for marker, variants in _HEADER_LABEL_SUFFIXES.items():
        if compact in variants:
            return marker
    return None


def _header_label_position(marker: str, rect: dict[str, float], page_rects: tuple[PdfRect, ...]) -> bool:
    if not page_rects:
        return False
    page_top = min(item.y0 for item in page_rects)
    page_bottom = max(item.y1 for item in page_rects)
    height = page_bottom - page_top
    if height <= 0:
        return False
    relative_y = ((rect["y0"] + rect["y1"]) / 2 - page_top) / height
    return relative_y >= 0.65 if marker in {"발신", "담당", *_FOOTER_CONTACT_LABELS} else relative_y <= 0.35


def _label_is_first_in_line(
    words: tuple[Any, ...], start: int, rect: dict[str, float],
) -> bool:
    return not any(
        (candidate_rect := _manifest_rect(candidate)) is not None
        and abs((candidate_rect["y0"] + candidate_rect["y1"] - rect["y0"] - rect["y1"]) / 2)
        <= max(8.0, rect["y1"] - rect["y0"])
        and candidate_rect["x1"] <= rect["x0"]
        for candidate in words[:start]
    )


def _header_label_candidates(
    words: tuple[Any, ...], page_rects: tuple[PdfRect, ...], *, header_only: bool = True,
) -> list[tuple[int, int, str, dict[str, float]]]:
    candidates: list[tuple[int, int, str, dict[str, float]]] = []
    for start, word in enumerate(words):
        rect = _manifest_rect(word)
        if rect is None:
            continue
        if not _label_is_first_in_line(words, start, rect):
            continue
        compact = ""
        last_rect = rect
        best_match: tuple[int, int, str, dict[str, float]] | None = None
        for end in range(start, min(len(words), start + 4)):
            current = words[end]
            current_rect = _manifest_rect(current)
            if current_rect is None:
                break
            if end > start and (
                abs((current_rect["y0"] + current_rect["y1"] - last_rect["y0"] - last_rect["y1"]) / 2)
                > max(8.0, last_rect["y1"] - last_rect["y0"])
                or current_rect["x0"] - last_rect["x1"] > max(24.0, 2 * (last_rect["y1"] - last_rect["y0"]))
            ):
                if best_match is not None:
                    candidates.append(best_match)
                break
            compact += re.sub(r"[\s:：;；,.]+", "", str(current.text))
            marker = _normalized_header_label(compact)
            if marker is not None:
                candidate_rect = {
                    "x0": rect["x0"], "y0": min(rect["y0"], current_rect["y0"]),
                    "x1": current_rect["x1"], "y1": max(rect["y1"], current_rect["y1"]),
                }
                if not header_only or _header_label_position(marker, candidate_rect, page_rects):
                    best_match = (start, end, marker, candidate_rect)
            has_longer_variant = any(
                variant.startswith(compact) and variant != compact
                for variants in _HEADER_LABEL_SUFFIXES.values()
                for variant in variants
            )
            if not has_longer_variant:
                if best_match is not None:
                    candidates.append(best_match)
                break
            last_rect = current_rect
        else:
            if best_match is not None:
                candidates.append(best_match)
    return candidates


def _drawing_supports_rect(rect: dict[str, float], drawing: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = drawing
    return (
        left - 1.0 <= rect["x0"] <= rect["x1"] <= right + 1.0
        and top - 1.0 <= rect["y0"] <= rect["y1"] <= bottom + 1.0
    )


def _row_box_structure(rects: list[dict[str, float]], drawings: tuple[tuple[float, float, float, float], ...]) -> bool:
    return bool(drawings) and bool(rects) and all(
        any(_drawing_supports_rect(rect, drawing) for drawing in drawings)
        for rect in rects
    )


def _header_row_value_words(
    words: list[tuple[int, int, Any, dict[str, float]]], label_end: int, label_rect: dict[str, float],
) -> list[tuple[int, int, Any, dict[str, float]]]:
    row_words = [entry for entry in words[label_end + 1:] if entry[3]["x0"] >= label_rect["x1"]
                 and entry[3]["x0"] - label_rect["x1"] <= EVIDENCE_LABEL_VALUE_DISTANCE_MAX
                 and abs((entry[3]["y0"] + entry[3]["y1"] - label_rect["y0"] - label_rect["y1"]) / 2)
                 <= max(8.0, label_rect["y1"] - label_rect["y0"])][:3]
    return [entry for entry in row_words if _normalized_header_label(str(entry[2].text)) is None]


def _footer_contact_value_kind(value: str) -> str | None:
    if _COMPACT_FOOTER_POSTAL_CODE_RE.fullmatch(value) is not None:
        return "postal_code"
    if PHONE_VALUE_PAT.fullmatch(value) is not None:
        return "phone"
    if EMAIL_PAT.fullmatch(value) is not None:
        return "email"
    return None


def _routing_title_evidence(
    words: list[tuple[int, int, Any, dict[str, float]]], page_rects: tuple[PdfRect, ...],
    *, top_zone_only: bool = False,
) -> tuple[set[str], tuple[str, ...], str | None]:
    if not words or not page_rects:
        return set(), (), None
    page_left = min(rect.x0 for rect in page_rects)
    page_top = min(rect.y0 for rect in page_rects)
    page_right = max(rect.x1 for rect in page_rects)
    page_bottom = max(rect.y1 for rect in page_rects)
    page_height = page_bottom - page_top
    if page_height <= 0 or page_right <= page_left:
        return set(), (), None
    lines: list[list[tuple[int, int, Any, dict[str, float]]]] = []
    for entry in sorted(words, key=lambda item: (item[3]["y0"], item[3]["x0"])):
        rect = entry[3]
        if lines:
            prior_rect = lines[-1][0][3]
            tolerance = max(3.0, (prior_rect["y1"] - prior_rect["y0"]) / 2, (rect["y1"] - rect["y0"]) / 2)
            if abs((prior_rect["y0"] + prior_rect["y1"] - rect["y0"] - rect["y1"]) / 2) <= tolerance:
                lines[-1].append(entry)
                continue
        lines.append([entry])
    signals: set[str] = set()
    titles: list[str] = []
    qualified_lines: list[tuple[str, bool]] = []
    running_title_lines: list[tuple[int, str, float, float, float, float]] = []
    for line_index, line in enumerate(lines):
        line_rects = [entry[3] for entry in line]
        line_left = min(rect["x0"] for rect in line_rects)
        line_top = min(rect["y0"] for rect in line_rects)
        line_bottom = max(rect["y1"] for rect in line_rects)
        relative_y = ((line_top + line_bottom) / 2 - page_top) / page_height
        raw_tokens = tuple(str(entry[2].text).strip() for entry in line)
        title_text = " ".join(raw_tokens)
        is_short_standalone_line = (
            0 < len(re.sub(r"\s+", "", title_text)) <= _TITLE_LINE_MAX_CHARACTERS
            and _TITLE_LINE_PREDICATE_RE.search(title_text) is None
        )
        is_left_aligned = line_left <= page_left + (page_right - page_left) * _TITLE_LINE_LEFT_RATIO
        is_centered = abs(
            (line_left + max(rect["x1"] for rect in line_rects)) / 2 - (page_left + page_right) / 2
        ) <= (page_right - page_left) * _TITLE_LINE_CENTER_TOLERANCE_RATIO
        is_qualified_title_line = (
            relative_y >= 0
            and relative_y <= (_RUNNING_TITLE_TOP_RATIO if top_zone_only else _TITLE_LINE_MAX_PAGE_RATIO)
            and is_short_standalone_line
            and (is_left_aligned or is_centered)
        )
        normalized_line = re.sub(r"[\W_]+", "", title_text)
        if (
            top_zone_only
            and is_qualified_title_line
            and max(rect["y1"] - rect["y0"] for rect in line_rects) >= _RUNNING_TITLE_LINE_MIN_HEIGHT
            and _RUNNING_TITLE_NON_TITLE_PREFIX_RE.match(title_text.strip()) is None
            and ":" not in title_text
            and normalized_line
        ):
            running_title_lines.append((
                line_index, normalized_line, line_left, max(rect["x1"] for rect in line_rects), line_top, line_bottom,
            ))
        is_dispatch_title = (
            is_qualified_title_line
            and _normalized_header_label(raw_tokens[0]) == "title"
        )
        is_dispatch_document_title = is_dispatch_title and (
            re.sub(r"[\W_]+", "", raw_tokens[-1]) in _DISPATCH_TITLE_TOKENS
        )
        compact_title_text = re.sub(r"\s+", "", title_text)
        is_internal_document_title = (
            is_qualified_title_line
            and max(rect["y1"] - rect["y0"] for rect in line_rects) >= _TITLE_LINE_MIN_HEIGHT
            and _INTERNAL_REVIEW_TITLE_RE.search(compact_title_text) is not None
        )
        if is_dispatch_document_title:
            signals.add("dispatch")
            titles.append(re.sub(r"[\W_]+", "", " ".join(raw_tokens[1:])))
        if is_internal_document_title:
            signals.add("internal")
            prior_title, prior_is_qualified = qualified_lines[-1] if qualified_lines else ("", False)
            title_source = f"{prior_title} {title_text}" if prior_is_qualified else title_text
            titles.append(re.sub(r"[\W_]+", "", title_source))
        qualified_lines.append((title_text, is_qualified_title_line))
    if top_zone_only and not signals:
        running_title_candidates = [
            title
            for _line_index, title, line_left, line_right, _line_top, _line_bottom in running_title_lines
            if len(title) >= _RUNNING_TITLE_MIN_CHARACTERS
            and line_right - line_left >= (page_right - page_left) * _RUNNING_TITLE_MIN_WIDTH_RATIO
            and abs((line_left + line_right) / 2 - (page_left + page_right) / 2)
            <= (page_right - page_left) * _TITLE_LINE_CENTER_TOLERANCE_RATIO
        ]
        running_title_candidates.extend(
            running_title_lines[index][1] + running_title_lines[index + 1][1]
            for index in range(len(running_title_lines) - 1)
            if running_title_lines[index + 1][0] == running_title_lines[index][0] + 1
            and len(running_title_lines[index][1] + running_title_lines[index + 1][1]) >= _RUNNING_TITLE_MIN_CHARACTERS
            and max(running_title_lines[index][3], running_title_lines[index + 1][3])
            - min(running_title_lines[index][2], running_title_lines[index + 1][2])
            >= (page_right - page_left) * _RUNNING_TITLE_MIN_WIDTH_RATIO
            and abs(
                (
                    min(running_title_lines[index][2], running_title_lines[index + 1][2])
                    + max(running_title_lines[index][3], running_title_lines[index + 1][3])
                ) / 2 - (page_left + page_right) / 2
            ) <= (page_right - page_left) * _TITLE_LINE_CENTER_TOLERANCE_RATIO
        )
        titles.extend(running_title_candidates)
    normalized_titles = tuple(dict.fromkeys(title for title in titles if title))
    kind = (
        "internal_review"
        if signals == {"internal"}
        else "official_dispatch"
        if signals == {"dispatch"}
        else None
    )
    return signals, normalized_titles, kind


def _routing_title_signals(
    words: list[tuple[int, int, Any, dict[str, float]]], page_rects: tuple[PdfRect, ...],
) -> set[str]:
    return _routing_title_evidence(words, page_rects)[0]


def _prereview_routing_signal(
    words: list[tuple[int, int, Any, dict[str, float]]],
    structural_layout: Any,
) -> bool:
    """Require independent title, contest-label, and approval evidence for 09.

    A generic approval box is shared by both public profiles and must not be a
    fallback route.  The prereview signal is emitted only when the page also
    carries a dedicated ``사전검토서`` title and an explicit ``공모명`` label.
    """
    if not words:
        return False
    lines: list[list[tuple[int, int, Any, dict[str, float]]]] = []
    for entry in sorted(words, key=lambda item: (item[3]["y0"], item[3]["x0"])):
        if lines:
            prior_rect = lines[-1][0][3]
            rect = entry[3]
            tolerance = max(
                3.0,
                (prior_rect["y1"] - prior_rect["y0"]) / 2,
                (rect["y1"] - rect["y0"]) / 2,
            )
            if abs((prior_rect["y0"] + prior_rect["y1"] - rect["y0"] - rect["y1"]) / 2) <= tolerance:
                lines[-1].append(entry)
                continue
        lines.append([entry])
    has_title = any(
        _PREREVIEW_TITLE_RE.search(re.sub(r"\s+", "", " ".join(str(entry[2].text) for entry in line)))
        is not None
        for line in lines
        if line and ((min(entry[3]["y0"] for entry in line) + max(entry[3]["y1"] for entry in line)) / 2)
        <= max(entry[3]["y1"] for entry in words) * 0.7
    )
    has_contest_label = any(
        re.sub(r"[\s:：]+", "", str(entry[2].text)) in _PREREVIEW_LABELS
        for entry in words
    )
    approval_values = getattr(structural_layout, "values", ())
    approval_count = sum(
        getattr(value, "kind", None) == "approval_staff"
        for value in approval_values
    )
    return has_title and has_contest_label and approval_count >= 2


def _selected_word_text_hash(
    selected: Sequence[tuple[Any, dict[str, float]]],
) -> str:
    ordered = sorted(
        selected,
        key=lambda item: (
            float(item[1]["x0"]),
            float(item[1]["y0"]),
            float(item[1]["x1"]),
            float(item[1]["y1"]),
        ),
    )
    return hashlib.sha256(
        "\n".join(str(word.text) for word, _ in ordered).encode("utf-8")
    ).hexdigest()


def _analysis_revision_for_manifest(
    opts: dict[str, Any],
    reanalysis: dict[str, Any] | None,
) -> int:
    option_revision = opts.get("analysis_revision", 1)
    if type(option_revision) is not int or option_revision < 1:
        raise ValueError("ANALYSIS_REVISION_INVALID")
    if reanalysis is None:
        return option_revision

    reanalysis_revision = reanalysis["analysis_revision"]
    if type(reanalysis_revision) is not int or reanalysis_revision < 2:
        raise ValueError("ANALYSIS_REVISION_INVALID")
    expected_revision = (
        reanalysis_revision - 1
        if reanalysis["kind"] == "boundary"
        else reanalysis_revision
    )
    if type(expected_revision) is not int or expected_revision < 1:
        raise ValueError("ANALYSIS_REVISION_INVALID")
    if "analysis_revision" in opts and option_revision != expected_revision:
        raise ValueError("ANALYSIS_REVISION_INVALID")
    return expected_revision


def trusted_analysis_manifest(
    infile: str,
    opts: dict[str, Any] | None = None,
    *,
    session_hash_key: bytes | None = None,
    source_bytes: bytes | None = None,
    extracted: ExtractResult | None = None,
    reanalysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a PII-safe, geometry-grounded analysis manifest; never write artifacts."""
    if not isinstance(session_hash_key, bytes) or len(session_hash_key) != 32:
        raise ValueError("SESSION_HASH_KEY_INVALID")
    opts = normalize_opts(opts)
    profile = _profile_value(str(opts.get("profile", "mixed") or "mixed"))
    if profile == "legal":
        raise ValueError("PUBLIC_PROFILE_REQUIRED")
    try:
        source_on_disk = Path(infile).read_bytes()
    except OSError:
        raise ValueError("SOURCE_UNAVAILABLE") from None
    if source_bytes is None:
        source_bytes = source_on_disk
    elif source_bytes != source_on_disk:
        raise ValueError("ORIGINAL_CHANGED")
    document_hash = hashlib.sha256(source_bytes).hexdigest()
    # Native reanalysis supplies the successor through options. The legacy
    # correction payload remains supported, but both inputs must describe the
    # same revision when they are present.
    revision = _analysis_revision_for_manifest(opts, reanalysis)
    extracted = extracted if extracted is not None else extract_document_for_public_analysis(
        infile,
        str(opts.get("extract_engine", "auto")),
        extractor=extract_document,
    )
    thresholds = _effective_policy_material(opts)
    if not extracted.pages:
        raise ValueError("PUBLIC_PAGE_EVIDENCE_UNAVAILABLE")

    pages: list[PageEvidence] = []
    evidence: list[RegionEvidence] = []
    fixed_value_candidates: list[tuple[int, str, list[dict[str, float]], str]] = []
    layout_coverage_by_page: dict[int, dict[str, str]] = {}
    public_footer_band_pages: set[int] = set()
    geometry_block_pages: list[int] = []
    page_words: dict[int, list[tuple[int, int, Any, dict[str, float]]]] = {}
    previous_page_number: int | None = None
    previous_edge_signatures = frozenset()
    label_kinds = {
        "결재": ("approval", "approval_staff"), "검토": ("approval", "approval_staff"),
        "승인": ("approval", "approval_staff"), "수신": ("recipient_reference",),
        "참조": ("recipient_reference",), "시행": ("dispatch_metadata",),
        "발신": ("sender_institution",), "담당": ("labeled_staff", "footer_contact"),
        "문서번호": ("header_meta", "dispatch_metadata"),
        "방침번호": ("header_meta",), "생산등록번호": ("header_meta",),
        "등록일": ("header_meta",), "결재일": ("header_meta",), "결재일자": ("header_meta",),
        "공개여부": ("header_meta",),
        "우편번호": ("footer_contact",), "전화": ("footer_contact",),
        "전송": ("footer_contact",), "이메일": ("footer_contact",),
    }
    mask_value_kind = {
        "결재": "approval_staff",
        "검토": "approval_staff",
        "승인": "approval_staff",
        "수신": "recipient_reference",
        "참조": "recipient_reference",
        "시행": "dispatch_metadata",
        "발신": "sender_institution",
        "담당": "staff_contact",
        "문서번호": "document_header",
        "방침번호": "header_meta", "생산등록번호": "header_meta",
        "등록일": "header_meta", "결재일": "header_meta", "결재일자": "header_meta",
        "공개여부": "header_meta",
        "우편번호": "footer_contact", "전화": "footer_contact",
        "전송": "footer_contact", "이메일": "footer_contact",
    }
    for page in extracted.pages:
        trustworthy_geometry = (
            page.source == "pymupdf_text_layer"
            and page.evidence_status == "available"
            and bool(page.words)
            and page.coordinate_space == "pdf_points_top_left"
        )
        try:
            words = _page_word_offsets(page) if trustworthy_geometry else []
        except ValueError:
            words = []
            trustworthy_geometry = False
        if not trustworthy_geometry:
            geometry_block_pages.append(page.page_index)
        page_words[page.page_index] = words
        page_rects = tuple(
            PdfRect(rect["x0"], rect["y0"], rect["x1"], rect["y1"])
            for _, _, _, rect in words
        )
        position_rects = (
            (PdfRect(0.0, 0.0, float(page.width), float(page.height)),)
            if isinstance(page.width, (int, float)) and isinstance(page.height, (int, float))
            and page.width > 0 and page.height > 0
            else page_rects
        )
        confidence_source = (
            "text_layer"
            if page.source == "pymupdf_text_layer" and page.evidence_reason is None
            else "ocr"
        )
        confidence = 1.0 if confidence_source == "text_layer" else min(
            (word.confidence for _, _, word, _ in words if word.confidence is not None),
            default=None,
        )
        structural_layout = analyze_approval_layout(
            [word for _, _, word, _ in words],
            page_index=page.page_index,
            drawings=page.drawings,
            page_rect=(0.0, 0.0, float(page.width), float(page.height))
            if isinstance(page.width, (int, float)) and isinstance(page.height, (int, float))
            and page.width > 0 and page.height > 0
            else None,
        )
        layout_coverage_by_page[page.page_index] = dict(structural_layout.coverage)
        for value in structural_layout.values:
            value_rects = [
                {"x0": rect[0], "y0": rect[1], "x1": rect[2], "y1": rect[3]}
                for rect in value.value_rects
            ]
            protected_rects = [
                {"x0": rect[0], "y0": rect[1], "x1": rect[2], "y1": rect[3]}
                for rect in value.protected_neighbor_rects
            ]
            if value.source == "pymupdf_subword":
                container_rects = [*value_rects, *protected_rects]
                container = {
                    "x0": min(rect["x0"] for rect in container_rects),
                    "y0": min(rect["y0"] for rect in container_rects),
                    "x1": max(rect["x1"] for rect in container_rects),
                    "y1": max(rect["y1"] for rect in container_rects),
                }
                exact_value_rect = _exact_pdf_subtext_rect(
                    infile, page.page_index, value.value_text, container,
                )
                if exact_value_rect is None:
                    layout_coverage_by_page[page.page_index][value.kind] = "indeterminate"
                    continue
                value_rects = [exact_value_rect]
            region_kinds = (
                ("approval", "approval_staff")
                if value.kind == "approval_staff"
                else (value.kind,)
            )
            all_rects = [*value_rects, *protected_rects]
            for region_kind in region_kinds:
                evidence.append(RegionEvidence(
                    region_kind,
                    page.page_index,
                    (PdfRect(
                        min(item["x0"] for item in all_rects),
                        min(item["y0"] for item in all_rects),
                        max(item["x1"] for item in all_rects),
                        max(item["y1"] for item in all_rects),
                    ),),
                    box_structure_match=value.box_structure_match,
                    label_match=True,
                    structural_match=True,
                    label_value_distance=value.label_value_distance,
                    approval_row_pattern=value.approval_row_pattern,
                    page_position_match=True,
                    ocr_confidence=confidence,
                    confidence_source=confidence_source,
                ))
            fixed_value_candidates.append((
                page.page_index,
                value.kind,
                value_rects,
                hmac.new(
                    session_hash_key, value.value_text.encode("utf-8"), hashlib.sha256,
                ).hexdigest(),
            ))
        header_labels = _header_label_candidates(
            tuple(word for _, _, word, _ in words), position_rects, header_only=True,
        )
        header_rows = [
            (label_start, label_end, marker, rect, _header_row_value_words(words, label_end, rect))
            for label_start, label_end, marker, rect in header_labels
        ]
        routing_header_labels = _header_label_candidates(
            tuple(word for _, _, word, _ in words), position_rects, header_only=False,
        )
        header_markers = {label for _, _, label, _ in header_labels}
        routing_markers = {label for _, _, label, _ in routing_header_labels}
        labeled_footer_values: list[tuple[list[dict[str, float]], str]] = []
        signals: set[str] = set()
        if _COMMON_DOCUMENT_HEADER_LABELS <= header_markers:
            signals.add(COMMON_DOCUMENT_HEADER)
        if _DISPATCH_HEADER_LABELS & routing_markers:
            signals.add("dispatch")
        if _INTERNAL_REVIEW_HEADER_LABELS & header_markers:
            signals.add("internal")
        if "attachment" in header_markers:
            signals.add("attachment")
        title_signals: set[str] = set()
        routing_titles: tuple[str, ...] = ()
        routing_title_kind: str | None = None
        if page.page_index == 0 or not {"internal", "dispatch", "attachment"} & signals:
            title_signals, routing_titles, routing_title_kind = _routing_title_evidence(
                words,
                position_rects,
                top_zone_only=page.page_index > 0,
            )
        if _prereview_routing_signal(words, structural_layout):
            signals.add("prereview")
        if page.page_index == 0:
            signals.update(title_signals)
        boundary_confidence = (
            1.0
            if confidence_source == "text_layer" and signals
            else None
        )
        continuity_signals, previous_page_number, previous_edge_signatures = _continuity_evidence(
            words,
            float(page.height) if isinstance(page.height, (int, float)) else 0.0,
            signals,
            previous_page_number,
            previous_edge_signatures,
        )
        pages.append(PageEvidence(
            page.page_index,
            frozenset(signals),
            continuity_signals,
            confidence,
            page_rects,
            boundary_confidence,
            confidence_source,
            routing_titles=routing_titles,
            routing_title_kind=routing_title_kind,
        ))
        for label_start, label_end, marker, rect, value_words in header_rows:
            kinds = label_kinds.get(marker)
            if kinds is None:
                continue
            if marker in {"결재", "검토", "승인"}:
                continue
            if not value_words:
                continue
            row = [rect, *(entry[3] for entry in value_words)]
            geometry = (PdfRect(min(item["x0"] for item in row), min(item["y0"] for item in row),
                                max(item["x1"] for item in row), max(item["y1"] for item in row)),)
            box_structure_match = _row_box_structure(
                [rect, *(entry[3] for entry in value_words)], page.drawings,
            )
            for kind in kinds:
                if kind == "footer_contact":
                    continue
                evidence.append(RegionEvidence(
                    kind,
                    page.page_index,
                    geometry,
                    box_structure_match=box_structure_match,
                    label_match=(
                        _normalized_header_label("".join(
                            str(words[index][2].text)
                            for index in range(label_start, label_end + 1)
                        )) == marker
                    ),
                    structural_match=bool(value_words),
                    label_value_distance=_label_value_distance(rect, [entry[3] for entry in value_words]),
                    page_position_match=_measured_region_position(marker, rect, position_rects),
                    ocr_confidence=confidence,
                    confidence_source=confidence_source,
                ))
                layout_coverage_by_page[page.page_index][kind] = "present"
            candidate_kind = mask_value_kind.get(marker)
            if candidate_kind is not None:
                value_rects = [entry[3] for entry in value_words]
                value_hash = hmac.new(
                    session_hash_key,
                    "".join(str(entry[2].text) for entry in value_words).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
                if candidate_kind == "footer_contact":
                    if any(
                        _footer_contact_value_kind(str(entry[2].text)) is None
                        for entry in value_words
                    ):
                        labeled_footer_values.append((value_rects, value_hash))
                    continue
                fixed_value_candidates.append((page.page_index, candidate_kind, value_rects, value_hash))
        footer_value_rect_keys = {
            (rect["x0"], rect["y0"], rect["x1"], rect["y1"])
            for rects, _value_hash in labeled_footer_values
            for rect in rects
        }
        footer_values = [*labeled_footer_values]
        for _start, _end, word, rect in words:
            if (
                not _measured_region_position("우편번호", rect, position_rects)
                or (rect["x0"], rect["y0"], rect["x1"], rect["y1"]) in footer_value_rect_keys
                or _footer_contact_value_kind(str(word.text)) is None
            ):
                continue
            footer_values.append((
                [rect],
                hmac.new(session_hash_key, str(word.text).encode("utf-8"), hashlib.sha256).hexdigest(),
            ))
        footer_labels = {
            marker
            for _start, _end, marker, rect in _header_label_candidates(
                tuple(word for _, _, word, _ in words), position_rects, header_only=False,
            )
            if marker in _FOOTER_CONTACT_LABELS
            and _measured_region_position(marker, rect, position_rects)
        }
        footer_band_structure = bool(footer_values) and (
            len(footer_values) >= 2 or len(footer_labels) >= 2
        )
        if footer_band_structure:
            public_footer_band_pages.add(page.page_index)
        for value_rects, value_hash in footer_values:
            geometry = (PdfRect(
                min(rect["x0"] for rect in value_rects),
                min(rect["y0"] for rect in value_rects),
                max(rect["x1"] for rect in value_rects),
                max(rect["y1"] for rect in value_rects),
            ),)
            evidence.append(RegionEvidence(
                "footer_contact",
                page.page_index,
                geometry,
                box_structure_match=footer_band_structure,
                label_match=bool(footer_labels) or len(footer_values) >= 2,
                structural_match=True,
                label_value_distance=0.0,
                page_position_match=True,
                ocr_confidence=confidence,
                confidence_source=confidence_source,
            ))
            layout_coverage_by_page[page.page_index]["footer_contact"] = "present"
            fixed_value_candidates.append((
                page.page_index,
                "footer_contact",
                value_rects,
                value_hash,
            ))

    routing = route_logical_documents(
        profile,
        pages,
        document_hash=document_hash,
        analysis_revision=revision,
        profile_authority=opts.get("profile_authority"),
    )
    if reanalysis is not None:
        if reanalysis["kind"] == "boundary":
            routing = apply_boundary_correction(
                routing,
                BoundaryCorrection(
                    reanalysis["page_start"], reanalysis["page_end"], reanalysis["segment_kind"],
                ),
                correction_authority={
                    "document_sha256": document_hash,
                    "prior_analysis_revision": revision,
                    "profile": profile,
                    "decision_code": "boundary_correction_confirmed",
                    "correction_sha256": hashlib.sha256(json.dumps({
                        "page_start": reanalysis["page_start"],
                        "page_end": reanalysis["page_end"],
                        "kind": reanalysis["segment_kind"],
                    }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                },
            ).routing_result
        revision = routing.analysis_revision
    layout = (
        detect_internal_review_regions(evidence, document_hash=document_hash, analysis_revision=revision)
        if profile == "internal_review" else
        detect_official_dispatch_regions(evidence, document_hash=document_hash, analysis_revision=revision)
        if profile == "official_dispatch" else
        (*detect_internal_review_regions(evidence, document_hash=document_hash, analysis_revision=revision),
         *detect_official_dispatch_regions(evidence, document_hash=document_hash, analysis_revision=revision))
    )
    scan_pages = frozenset(geometry_block_pages)
    routed_segments: list[dict[str, Any]] = []
    for routed in routing.segments:
        page_start = routed.page_start
        while page_start <= routed.page_end:
            scanned = page_start in scan_pages
            page_end = page_start
            while page_end < routed.page_end and (page_end + 1 in scan_pages) == scanned:
                page_end += 1
            if scanned:
                kind = "unknown"
                state = "review_required"
                common_only = False
                source = "scanned_geometry_unavailable"
            else:
                kind = routed.kind
                state = routed.state
                common_only = routed.common_only
                source = "routing"
            routed_segments.append({
                "segment_id": _manifest_digest({
                    "document_hash": document_hash,
                    "analysis_revision": revision,
                    "kind": kind,
                    "page_start": page_start,
                    "page_end": page_end,
                    "common_only": common_only,
                }, "seg_"),
                "analysis_revision": revision,
                "page_start": page_start,
                "page_end": page_end,
                "kind": kind,
                "state": state,
                "common_only": common_only,
                "source": source,
            })
            page_start = page_end + 1
    segment_for_page = {
        page: segment for segment in routed_segments
        for page in range(segment["page_start"], segment["page_end"] + 1)
    }
    segments = routed_segments
    regions = [{
        "region_id": region.region_id, "segment_id": segment_for_page[region.page_index]["segment_id"],
        "analysis_revision": revision, "page": region.page_index,
        "rects": [{"x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1} for rect in region.rect_list],
        "kind": region.kind, "state": region.state, "confirmation_source": region.confirmation_source,
        "reason_codes": list(region.reason_codes), "source": "official_layout",
    } for region in layout]
    reviews = [{
        "review_id": _manifest_digest({
            "document_hash": document_hash,
            "analysis_revision": revision,
            "kind": "acknowledge",
            "segment_id": segment["segment_id"],
            "reason_code": "scanned_geometry_unavailable",
        }, "review_"),
        "analysis_revision": revision,
        "kind": "acknowledge",
        "target_id": segment["segment_id"],
        "page_start": segment["page_start"],
        "page_end": segment["page_end"],
        "status": "pending",
        "reason_codes": ["scanned_geometry_unavailable"],
        "requires_acknowledgment": True,
        "common_only": False,
        "provenance": "extraction_evidence",
    } for segment in segments if segment["source"] == "scanned_geometry_unavailable"]
    for item in routing.review_items:
        for segment in segments:
            if segment["source"] != "routing":
                continue
            page_start = max(item.page_start, segment["page_start"])
            page_end = min(item.page_end, segment["page_end"])
            if page_start > page_end:
                continue
            review_id = (
                item.review_id
                if page_start == item.page_start and page_end == item.page_end
                else _manifest_digest({
                    "routing_review_id": item.review_id,
                    "segment_id": segment["segment_id"],
                    "page_start": page_start,
                    "page_end": page_end,
                }, "review_")
            )
            reviews.append({
                "review_id": review_id, "analysis_revision": revision,
                "kind": "acknowledge" if item.common_only else item.kind,
                "target_id": segment["segment_id"],
                "page_start": page_start, "page_end": page_end, "status": "pending",
                "reason_codes": list(item.reason_codes),
                "requires_acknowledgment": item.requires_acknowledgment,
                "common_only": item.common_only, "provenance": "routing",
            })
    occurrences: list[dict[str, Any]] = []
    occurrence_keys: set[tuple[int, str, tuple[tuple[float, float, float, float], ...], str]] = set()
    public_region_data = load_region_data()
    public_address_patterns = _national_address_patterns()
    profile_region_kinds = {
        "internal_review": {"approval", "header_meta", "labeled_staff"},
        "official_dispatch": {
            "recipient_reference", "sender_institution", "approval_staff",
            "dispatch_metadata", "footer_contact",
        },
    }

    def profile_fixed_category(
        segment_kind: str,
        candidate_kind: str,
    ) -> tuple[str, str]:
        category = (
            "labeled_staff" if candidate_kind == "staff_contact" and segment_kind == "internal_review"
            else "footer_contact" if candidate_kind == "staff_contact" and segment_kind == "official_dispatch"
            else "header_meta" if candidate_kind == "document_header" and segment_kind == "internal_review"
            else "dispatch_metadata" if candidate_kind == "document_header" and segment_kind == "official_dispatch"
            else candidate_kind
        )
        region_kind = (
            "approval" if category == "approval_staff" and segment_kind == "internal_review"
            else category
        )
        return category, region_kind

    def append_public_occurrence(
        *,
        page: ExtractedPage,
        segment: dict[str, Any],
        rects: list[dict[str, float]],
        value_text: str,
        tag: str,
        category: str,
        source: str,
        provenance: str,
        proposed_action: str,
        reason_codes: tuple[str, ...] = (),
        review_kind: str | None = None,
        expected_text_hash: str | None = None,
    ) -> str | None:
        if not rects or proposed_action not in {"mask", "review", "exclude"}:
            return None
        if expected_text_hash is None:
            expected_text_hash = occurrence_rect_text_hash(
                infile,
                page.page_index,
                [(rect["x0"], rect["y0"], rect["x1"], rect["y1"]) for rect in rects],
            )
        if expected_text_hash is None:
            raise ValueError("EXTRACTED_SOURCE_MISMATCH")
        key = (
            page.page_index,
            category,
            tuple(
                (rect["x0"], rect["y0"], rect["x1"], rect["y1"])
                for rect in rects
            ),
            expected_text_hash,
        )
        if key in occurrence_keys:
            return None
        occurrence_keys.add(key)
        value_hash = hmac.new(
            session_hash_key, value_text.strip().encode("utf-8"), hashlib.sha256,
        ).hexdigest()
        occurrence_id = _occurrence_id(
            document_hash,
            revision,
            page.page_index,
            rects,
            tag,
            category,
            value_hash,
            source,
            "masking-policy-v1",
            proposed_action,
            segment_id=segment["segment_id"],
            region_id=None,
        )
        occurrences.append({
            "occurrence_id": occurrence_id,
            "segment_id": segment["segment_id"],
            "region_id": None,
            "analysis_revision": revision,
            "page": page.page_index,
            "rects": rects,
            "tag": tag,
            "category": category,
            "value_hash": value_hash,
            "expected_text_hash": expected_text_hash,
            "source": source,
            "policy": "masking-policy-v1",
            "proposed_action": proposed_action,
            "state": "confirmed" if proposed_action == "mask" else "review_required",
            "provenance": provenance,
        })
        if proposed_action == "review":
            kind = review_kind or (
                "institution"
                if category in {"institution_value", "institution_address", "region_name"}
                else "name"
            )
            reviews.append({
                "review_id": _manifest_digest({
                    "document_hash": document_hash,
                    "analysis_revision": revision,
                    "kind": kind,
                    "occurrence_id": occurrence_id,
                }, "review_"),
                "analysis_revision": revision,
                "kind": kind,
                "target_id": occurrence_id,
                "page_start": page.page_index,
                "page_end": page.page_index,
                "status": "pending",
                "reason_codes": list(reason_codes or ("detector_review_required",)),
                "requires_acknowledgment": False,
                "common_only": False,
                "provenance": provenance,
            })
        return occurrence_id

    detector = build_ko_pii_detector(lambda _message: None)
    if detector is None:
        raise ValueError("COMMON_DETECTOR_UNAVAILABLE")
    for page in extracted.pages:
        words = page_words.get(page.page_index, [])
        if not words:
            continue
        try:
            detector_spans = detector.detect(page.text)
        except Exception:
            raise ValueError("COMMON_DETECTOR_FAILED") from None
        blocked_spans = [
            (span.start, span.end, str(span.label))
            for span in detector_spans
            if str(span.label) == "address"
            and bool(opts.get("address", True))
            and 0 <= span.start < span.end <= len(page.text)
        ]
        for span in detector_spans:
            if span.start < 0 or span.end <= span.start or span.end > len(page.text):
                raise ValueError("DETECTOR_SPAN_INVALID")
            option_key = {
                "rrn": "rrn",
                "foreign_id": "rrn",
                "business_number": "business_reg",
                "phone": "phone",
                "email": "email",
                "address": "address",
                "person_name": "name",
            }.get(str(span.label))
            if option_key is not None and not bool(opts.get(option_key, True)):
                continue
            selected = [(word, rect) for start, end, word, rect in words
                        if start < span.end and end > span.start]
            if not selected:
                reviews.append({
                    "review_id": _manifest_digest({"document_hash": document_hash, "analysis_revision": revision,
                        "kind": "ocr", "page": page.page_index, "tag": tag_for_label(span.label)}, "review_"),
                    "analysis_revision": revision, "kind": "ocr",
                    "target_id": segment_for_page[page.page_index]["segment_id"],
                    "page_start": page.page_index, "page_end": page.page_index, "status": "pending",
                    "reason_codes": ["candidate_geometry_missing"], "requires_acknowledgment": True,
                    "common_only": False, "provenance": "common_detector",
                })
                continue
            rects = [rect for _, rect in selected]
            if (
                str(span.label) == "address"
                and page.page_index in public_footer_band_pages
                and isinstance(page.height, (int, float))
                and page.height > 0
                and all(rect["y0"] >= float(page.height) * 0.65 for rect in rects)
            ):
                continue
            extracted_text_hash = _selected_word_text_hash(selected)
            expected_text_hash = occurrence_rect_text_hash(
                infile,
                page.page_index,
                [(rect["x0"], rect["y0"], rect["x1"], rect["y1"]) for rect in rects],
            )
            if expected_text_hash is None or extracted_text_hash != expected_text_hash:
                raise ValueError("EXTRACTED_SOURCE_MISMATCH")
            segment = segment_for_page[page.page_index]
            proposed_action = span.action if span.action in {"mask", "review"} else "review"
            tag = tag_for_label(span.label)
            append_public_occurrence(
                page=page,
                segment=segment,
                rects=rects,
                value_text=page.text[span.start:span.end],
                tag=tag,
                category=str(span.label),
                source="common_detector",
                provenance="common_detector",
                proposed_action=proposed_action,
                expected_text_hash=expected_text_hash,
            )
        for candidate in build_public_candidates(
            page.text,
            words,
            page_height=float(page.height) if isinstance(page.height, (int, float)) else 0.0,
            region_data=public_region_data,
            address_patterns=public_address_patterns,
            options=opts,
            blocked_spans=blocked_spans,
            footer_contact_value_kind=lambda value: _footer_contact_value_kind(value) is not None,
        ):
            selected = [
                (word, rect)
                for start, end, word, rect in words
                if start < candidate.end and end > candidate.start
            ]
            if not selected:
                continue
            selected_offsets = [
                (start, end)
                for start, end, word, rect in words
                if start < candidate.end and end > candidate.start
            ]
            if (
                min(start for start, _end in selected_offsets) != candidate.start
                or max(end for _start, end in selected_offsets) != candidate.end
            ):
                # Public candidates are emitted only when their word boundaries
                # prove the complete value; a partial glyph match cannot be
                # passed to the native finalizer.
                continue
            rects = [rect for _, rect in selected]
            fixed_rects = [
                fixed_rect
                for fixed_page, _fixed_kind, fixed_value_rects, _fixed_hash in fixed_value_candidates
                if fixed_page == page.page_index
                and not segment_for_page[fixed_page]["common_only"]
                and profile_fixed_category(
                    segment_for_page[fixed_page]["kind"], _fixed_kind,
                )[1] in profile_region_kinds.get(segment_for_page[fixed_page]["kind"], set())
                for fixed_rect in fixed_value_rects
            ]
            if any(
                max(rect["x0"], fixed_rect["x0"]) < min(rect["x1"], fixed_rect["x1"])
                and max(rect["y0"], fixed_rect["y0"]) < min(rect["y1"], fixed_rect["y1"])
                for rect in rects
                for fixed_rect in fixed_rects
            ):
                # Fixed profile rows already have their own category and
                # region linkage. Do not create a second public-context
                # occurrence for the same glyphs.
                continue
            extracted_text_hash = _selected_word_text_hash(selected)
            expected_text_hash = occurrence_rect_text_hash(
                infile,
                page.page_index,
                [(rect["x0"], rect["y0"], rect["x1"], rect["y1"]) for rect in rects],
            )
            if expected_text_hash is None or extracted_text_hash != expected_text_hash:
                raise ValueError("EXTRACTED_SOURCE_MISMATCH")
            append_public_occurrence(
                page=page,
                segment=segment_for_page[page.page_index],
                rects=rects,
                value_text=page.text[candidate.start:candidate.end],
                tag=candidate.tag,
                category=candidate.category,
                source=candidate.source,
                provenance=candidate.provenance,
                proposed_action=candidate.action,
                reason_codes=candidate.reason_codes,
                expected_text_hash=expected_text_hash,
            )
    custom_keywords = _parse_custom_keywords(opts.get("custom_keywords", ""))
    for page in extracted.pages:
        words = page_words.get(page.page_index, [])
        if not words:
            continue
        page_occupied_spans: list[tuple[int, int]] = []
        for keyword in custom_keywords:
            for match_start, match_end, selected in _custom_keyword_word_matches(page.text, words, keyword):
                if any(start < match_end and end > match_start for start, end in page_occupied_spans):
                    continue
                if not selected:
                    raise ValueError("EXTRACTED_SOURCE_MISMATCH")
                rects = [rect for _, rect in selected]
                value_text = page.text[match_start:match_end]
                value_hash = hmac.new(
                    session_hash_key, value_text.encode("utf-8"), hashlib.sha256,
                ).hexdigest()
                expected_text_hash = occurrence_rect_text_hash(
                    infile, page.page_index,
                    [(rect["x0"], rect["y0"], rect["x1"], rect["y1"]) for rect in rects],
                )
                extracted_text_hash = _selected_word_text_hash(selected)
                if expected_text_hash is None or extracted_text_hash != expected_text_hash:
                    raise ValueError("EXTRACTED_SOURCE_MISMATCH")
                segment = segment_for_page[page.page_index]
                occurrence_id = _occurrence_id(
                    document_hash, revision, page.page_index, rects, "KEYWORD", "custom_keyword",
                    value_hash, "custom_keyword", "masking-policy-v1", "mask",
                    segment_id=segment["segment_id"], region_id=None,
                )
                occurrences.append({
                    "occurrence_id": occurrence_id, "segment_id": segment["segment_id"], "region_id": None,
                    "analysis_revision": revision, "page": page.page_index, "rects": rects, "tag": "KEYWORD",
                    "category": "custom_keyword", "value_hash": value_hash, "expected_text_hash": expected_text_hash,
                    "source": "custom_keyword", "policy": "masking-policy-v1", "proposed_action": "mask",
                    "state": "confirmed", "provenance": "custom_keyword",
                })
                page_occupied_spans.append((match_start, match_end))
    layout_regions: dict[tuple[int, str], list[Any]] = {}
    for region in layout:
        layout_regions.setdefault((region.page_index, region.kind), []).append(region)
    for page, candidate_kind, rects, value_hash in fixed_value_candidates:
        segment = segment_for_page[page]
        if segment["common_only"]:
            continue
        category, region_kind = profile_fixed_category(segment["kind"], candidate_kind)
        if region_kind not in profile_region_kinds.get(segment["kind"], set()):
            continue
        region = next((
            item for item in sorted(
                layout_regions.get((page, region_kind), ()),
                key=lambda candidate: candidate.state != "confirmed",
            )
            if _rects_within_region(rects, item.rect_list)
        ), None)
        if region is None:
            continue
        expected_text_hash = occurrence_rect_text_hash(
            infile, page, [(rect["x0"], rect["y0"], rect["x1"], rect["y1"]) for rect in rects],
        )
        if expected_text_hash is None:
            reviews.append({
                "review_id": _manifest_digest({
                    "document_hash": document_hash, "analysis_revision": revision,
                    "kind": "ocr", "page": page, "category": category,
                    "value_hash": value_hash, "reason": "profile_rectangle_text_unavailable",
                }, "review_"),
                "analysis_revision": revision, "kind": "ocr", "target_id": segment["segment_id"],
                "page_start": page, "page_end": page, "status": "pending",
                "reason_codes": ["profile_rectangle_text_unavailable"],
                "requires_acknowledgment": True, "common_only": False,
                "provenance": "profile_layout",
            })
            continue
        if region.state == "confirmed" and category == "approval_staff":
            # A confirmed approval line has server-owned value-only geometry. Public
            # policy omits that value without scoring it as an ungrounded body name.
            action = "mask"
        elif region.state == "confirmed" and category == "footer_contact":
            action = "mask"
        elif region.state == "confirmed" and category == "labeled_staff":
            name_score = score_public_body_name(
                authoritative_label=region.confirmation_source in {"automatic", "user"},
                approval_role=False,
                punctuation_or_label_boundary=False,
                distance_from_label=None,
                page_position_match=_rects_within_region(rects, region.rect_list),
                region_state=region.state,
                auto_mask_threshold=float(opts.get("auto_threshold", PUBLIC_NAME_TEST_AUTO_MASK_THRESHOLD)),
                review_threshold=float(opts.get("review_threshold", PUBLIC_NAME_TEST_REVIEW_THRESHOLD)),
            )
            action = "mask" if name_score["action"] == "auto_mask" else "review"
        else:
            action = "mask" if region.state == "confirmed" else "review"
        existing = next(
            (
                item for item in occurrences
                if _profile_candidate_matches_occurrence(item, page, rects, value_hash)
            ),
            None,
        )
        if (
            region.state == "unconfirmed"
            and existing is not None
            and existing["proposed_action"] == "mask"
            and existing["state"] == "confirmed"
        ):
            continue
        if existing is not None:
            old_id = existing["occurrence_id"]
            updated_occurrence = dict(existing)
            if category in {"header_meta", "dispatch_metadata"}:
                updated_occurrence["rects"] = rects
                updated_occurrence["expected_text_hash"] = expected_text_hash
            updated_occurrence["region_id"] = region.region_id
            if action == "mask" and updated_occurrence["proposed_action"] == "review":
                updated_occurrence["proposed_action"] = "mask"
                updated_occurrence["state"] = "confirmed"
                updated_occurrence["provenance"] = "common_detector_profile_layout"
            updated_id = _occurrence_id(
                document_hash, revision, page, rects, updated_occurrence["tag"], updated_occurrence["category"],
                updated_occurrence["value_hash"], updated_occurrence["source"], updated_occurrence["policy"],
                updated_occurrence["proposed_action"], segment_id=updated_occurrence["segment_id"],
                region_id=updated_occurrence["region_id"],
            )
            updated_occurrence["occurrence_id"] = updated_id
            if updated_occurrence["proposed_action"] == "mask":
                updated_reviews = [item for item in reviews if item.get("target_id") != old_id]
            else:
                updated_reviews = [
                    {
                        **item,
                        "target_id": updated_id,
                        "review_id": _manifest_digest({
                            "document_hash": document_hash,
                            "analysis_revision": revision,
                            "kind": item["kind"],
                            "occurrence_id": updated_id,
                        }, "review_"),
                    }
                    if item.get("target_id") == old_id else item
                    for item in reviews
                ]
            existing.clear()
            existing.update(updated_occurrence)
            reviews = updated_reviews
            continue
        occurrence_id = _occurrence_id(
            document_hash, revision, page, rects, "profile_value", category, value_hash,
            "profile_layout", "masking-policy-v1", action,
            segment_id=segment["segment_id"], region_id=region.region_id,
        )
        occurrences.append({
            "occurrence_id": occurrence_id, "segment_id": segment["segment_id"], "region_id": region.region_id,
            "analysis_revision": revision, "page": page, "rects": rects, "tag": "profile_value",
            "category": category, "value_hash": value_hash, "expected_text_hash": expected_text_hash,
            "source": "profile_layout", "policy": "masking-policy-v1", "proposed_action": action,
            "state": "confirmed" if action == "mask" else "review_required", "provenance": "profile_layout",
        })
        if action == "review":
            review_kind = "institution" if category in {"recipient_reference", "sender_institution"} else "name"
            reviews.append({
                "review_id": _manifest_digest({
                    "document_hash": document_hash, "analysis_revision": revision,
                    "kind": review_kind, "occurrence_id": occurrence_id,
                }, "review_"),
                "analysis_revision": revision, "kind": review_kind, "target_id": occurrence_id,
                "page_start": page, "page_end": page, "status": "pending",
                "reason_codes": [
                    "unconfirmed_region_candidate"
                    if region.state == "unconfirmed"
                    else "profile_region_review_required"
                ], "requires_acknowledgment": False,
                "common_only": False, "provenance": "profile_layout",
            })
    threshold_artifact = _threshold_artifact(
        thresholds["auto_mask_threshold"],
        thresholds["review_threshold"],
    )
    required_coverage = (
        INTERNAL_REQUIRED_KINDS if profile == "internal_review"
        else DISPATCH_REQUIRED_KINDS if profile == "official_dispatch"
        else (*INTERNAL_REQUIRED_KINDS, *DISPATCH_REQUIRED_KINDS)
    )
    for region in regions:
        if region["state"] == "confirmed":
            continue
        linked_occurrences = [
            occurrence for occurrence in occurrences
            if occurrence.get("region_id") == region["region_id"]
        ]
        automatically_confirmed = not linked_occurrences or all(
            occurrence.get("proposed_action") == "mask"
            and occurrence.get("state") in {"confirmed", "user_confirmed"}
            for occurrence in linked_occurrences
        )
        if automatically_confirmed:
            region["state"] = "confirmed"
            region["confirmation_source"] = "automatic"
            layout_coverage_by_page[region["page"]][region["kind"]] = "present"
            continue
        reviews.append({
            "review_id": _manifest_digest({"document_hash": document_hash, "analysis_revision": revision,
                "kind": "region_geometry", "region_id": region["region_id"], "reason_codes": region["reason_codes"]}, "review_"),
            "analysis_revision": revision, "kind": "region_geometry", "target_id": region["region_id"],
            "page_start": region["page"], "page_end": region["page"], "status": "pending",
            "reason_codes": list(region["reason_codes"]), "requires_acknowledgment": True,
            "common_only": False, "provenance": "official_layout",
        })
    layout_coverage = {
        kind: (
            "present" if any(page.get(kind) == "present" for page in layout_coverage_by_page.values())
            else "indeterminate" if any(
                page.get(kind) == "indeterminate" for page in layout_coverage_by_page.values()
            )
            else "absent"
        )
        for kind in required_coverage
    }
    canonical_reviews: dict[tuple[Any, ...], dict[str, Any]] = {}
    for review in reviews:
        if review.get("common_only") is True:
            review = {**review, "kind": "acknowledge"}
        key = (
            review.get("kind"), review.get("target_id"), review.get("page_start"),
            review.get("page_end"), review.get("common_only"),
        )
        existing_review = canonical_reviews.get(key)
        if existing_review is None:
            canonical_reviews[key] = review
        else:
            existing_review["reason_codes"] = list(dict.fromkeys([
                *existing_review.get("reason_codes", ()),
                *review.get("reason_codes", ()),
            ]))
    reviews = list(canonical_reviews.values())
    approval_occurrences = [
        occurrence for occurrence in occurrences
        if occurrence.get("category") in {"approval", "approval_staff"}
        and occurrence.get("proposed_action") == "mask"
    ]
    approval_kind = "approval" if profile == "internal_review" else "approval_staff"
    approval_state = (
        "present" if approval_occurrences
        else "indeterminate" if layout_coverage.get(approval_kind) == "indeterminate"
        else "absent"
    )
    approval_coverage = {
        "schema_version": 1,
        "state": approval_state,
        "signer_count": len(approval_occurrences),
        "protected_neighbor_count": sum(
            len(occurrence.get("protected_neighbor_refs", ()))
            for occurrence in approval_occurrences
        ),
    }
    required_region_coverage = {
        "schema_version": 1,
        "profile": profile,
        "kinds": [
            {"kind": kind, "state": layout_coverage[kind]}
            for kind in required_coverage
        ],
        "blocking": "indeterminate" in layout_coverage.values(),
    }
    return {
        "schema_version": 1, "original_document_hash": document_hash, "analysis_revision": revision,
        "profile": profile, "coordinate_space": "pdf_points_top_left", "policy_version": "masking-policy-v1",
        "options_version": "options-v2", "options_hash": _canonical_json_hash(_effective_policy_material(opts)),
        "threshold_version": threshold_artifact["version"],
        "threshold_hash": threshold_artifact["content_hash"],
        "threshold_artifact": threshold_artifact,
        "segments": segments, "regions": regions, "occurrences": occurrences, "review_items": reviews,
        "approval_coverage": approval_coverage,
        "required_region_coverage": required_region_coverage,
        "manual_actions": [],
    }


def _trusted_finalize_cleanup(staging_output: str) -> bool:
    """Remove all finalization artifacts without exposing path details."""
    cleaned = True
    for output in (f"{staging_output}.manual.pdf", f"{staging_output}.render.pdf", staging_output):
        try:
            Path(output).unlink(missing_ok=True)
        except OSError:
            cleaned = False
    return cleaned


def _trusted_manifest_covers_pdf(
    manifest: dict[str, Any],
    original_bytes: bytes,
    revision: int,
) -> bool:
    try:
        import fitz  # type: ignore

        with fitz.open(stream=original_bytes, filetype="pdf") as document:
            page_count = document.page_count
    except Exception:
        return False
    segments = manifest.get("segments")
    if page_count < 1 or not isinstance(segments, list) or not segments:
        return False
    covered_pages: list[int] = []
    for segment in segments:
        if not isinstance(segment, dict):
            return False
        page_start = segment.get("pageStart")
        page_end = segment.get("pageEnd")
        if (
            type(segment.get("analysisRevision")) is not int
            or segment.get("analysisRevision") != revision
            or type(page_start) is not int
            or type(page_end) is not int
            or page_start < 0
            or page_end < page_start
            or page_end >= page_count
        ):
            return False
        covered_pages.extend(range(page_start, page_end + 1))
    return covered_pages == list(range(page_count))
def _trusted_occurrence_validation_error(item: object, revision: int) -> str | None:
    if not isinstance(item, dict):
        return "TRUSTED_FINALIZE_INVALID"
    occurrence_id = item.get("occurrenceId")
    expected_text_hash = item.get("expectedTextHash")
    if (
        not isinstance(occurrence_id, str)
        or _OCCURRENCE_ID_PATTERN.fullmatch(occurrence_id) is None
        or item.get("analysisRevision") != revision
        or not isinstance(expected_text_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_text_hash.lower()) is None
    ):
        return "STALE_ANALYSIS"
    page = item.get("page")
    rects = item.get("rects")
    if (
        type(page) is not int
        or page < 0
        or item.get("proposedAction") not in {"mask", "exclude", "review"}
        or not isinstance(item.get("provenance"), str)
        or re.fullmatch(r"[a-z][a-z0-9_:-]{0,95}", item["provenance"]) is None
        or not isinstance(rects, list)
        or not rects
    ):
        return "TRUSTED_FINALIZE_OCCURRENCE_INVALID"
    for rect in rects:
        if not isinstance(rect, dict) or set(rect) != {"x0", "y0", "x1", "y1"}:
            return "TRUSTED_FINALIZE_OCCURRENCE_INVALID"
        try:
            x0, y0, x1, y1 = (float(rect[key]) for key in ("x0", "y0", "x1", "y1"))
        except (TypeError, ValueError):
            return "TRUSTED_FINALIZE_OCCURRENCE_INVALID"
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
            return "TRUSTED_FINALIZE_OCCURRENCE_INVALID"
    return None


def _manual_excluded_occurrence_ids(manifest: dict[str, Any]) -> set[str]:
    linked_ids = {
        action["linkedOccurrenceId"]
        for action in manifest["manualActions"]
        if isinstance(action.get("linkedOccurrenceId"), str) and action["linkedOccurrenceId"]
    }
    protected_ids = {
        occurrence["occurrenceId"]
        for occurrence in manifest["occurrences"]
        for action in manifest["manualActions"]
        if occurrence["page"] == action["page"]
        and occurrence.get("category") != "custom_keyword"
        and occurrence["rects"] == action["protectedNeighborRefs"]
    }
    return linked_ids | protected_ids


_TRUSTED_REVIEW_KINDS = {"name", "institution", "acknowledge", "boundary", "ocr", "region_geometry"}
_TRUSTED_SAFE_CODE = re.compile(r"[a-z][a-z0-9_:-]{0,95}\Z")


class TrustedFinalizeOccurrenceIntrinsicError(ValueError):
    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        super().__init__("TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED")
        self.diagnostics = diagnostics


class TrustedFinalizeManualResultError(ValueError):
    def __init__(self, diagnostics: list[dict[str, Any]]) -> None:
        super().__init__("TRUSTED_FINALIZE_MANUAL_RESULT_FAILED")
        self.diagnostics = diagnostics


def _trusted_occurrence_intrinsic_diagnostics(result: dict[str, Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    contextual: list[dict[str, Any]] = []
    review_items = result.get("review_items")
    if isinstance(review_items, list):
        for item in review_items:
            if not isinstance(item, dict):
                continue
            reason_code = item.get("reason_code")
            if (
                item.get("status") != "review_required"
                or reason_code == "review_action_unresolved"
                or not isinstance(reason_code, str)
                or _TRUSTED_SAFE_CODE.fullmatch(reason_code) is None
            ):
                continue
            count = item.get("count")
            increment = count if type(count) is int and 0 < count <= 10_000 else 1
            category = item.get("category")
            if (
                isinstance(category, str)
                and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", category) is not None
            ):
                diagnostic: dict[str, Any] = {
                    "kind": "occurrence_failure",
                    "reason_code": reason_code,
                    "count": increment,
                    "category": category,
                }
                occurrence_id = item.get("occurrence_id")
                if (
                    isinstance(occurrence_id, str)
                    and _OCCURRENCE_ID_PATTERN.fullmatch(occurrence_id) is not None
                ):
                    diagnostic["occurrence_id"] = occurrence_id
                page = item.get("page")
                if type(page) is int and 0 <= page <= 2_000:
                    diagnostic["page"] = page
                for field in ("rect_fingerprint", "expected_text_hash", "observed_text_hash"):
                    value = item.get(field)
                    if (
                        isinstance(value, str)
                        and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None
                    ):
                        diagnostic[field] = value.lower()
                contextual.append(diagnostic)
            else:
                counts[reason_code] = counts.get(reason_code, 0) + increment
    diagnostics = [
        {
            "kind": "occurrence_failure",
            "reason_code": reason_code,
            "count": count,
        }
        for reason_code, count in sorted(counts.items())
    ]
    diagnostics.extend(sorted(
        contextual,
        key=lambda item: (
            str(item.get("reason_code")),
            str(item.get("occurrence_id", "")),
            int(item.get("page", -1)),
            str(item.get("category")),
        ),
    ))
    diagnostics = diagnostics[:15]
    if not diagnostics:
        diagnostics.append({
            "kind": "occurrence_failure",
            "reason_code": "occurrence_intrinsic_verification_failed",
            "count": 1,
        })
    diagnostics.append({
        "kind": "pii_non_exposure",
        "reason_code": "final_output_not_published",
        "count": 1,
    })
    return diagnostics


def _trusted_manual_result_diagnostics(result: object) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    review_items = result.get("review_items") if isinstance(result, dict) else None
    if isinstance(review_items, list):
        for item in review_items:
            if not isinstance(item, dict):
                continue
            reason_code = item.get("reason_code")
            if (
                item.get("status") != "review_required"
                or not isinstance(reason_code, str)
                or _TRUSTED_SAFE_CODE.fullmatch(reason_code) is None
            ):
                continue
            count = item.get("count")
            increment = count if type(count) is int and 0 < count <= 10_000 else 1
            counts[reason_code] = counts.get(reason_code, 0) + increment
    diagnostics = [
        {
            "kind": "manual_failure",
            "reason_code": reason_code,
            "count": count,
        }
        for reason_code, count in sorted(counts.items())
    ]
    if not diagnostics:
        diagnostics.append({
            "kind": "manual_failure",
            "reason_code": "manual_intrinsic_verification_failed",
            "count": 1,
        })
    diagnostics.append({
        "kind": "pii_non_exposure",
        "reason_code": "final_output_not_published",
        "count": 1,
    })
    return diagnostics


def _trusted_review_validation_error(
    item: object,
    manifest: dict[str, Any],
    revision: int,
) -> str | None:
    if not isinstance(item, dict):
        return "TRUSTED_FINALIZE_INVALID"
    if item.get("analysisRevision") != revision:
        return "STALE_ANALYSIS"
    kind = item.get("kind")
    target_id = item.get("targetId")
    page_start = item.get("pageStart")
    page_end = item.get("pageEnd")
    if (
        not isinstance(kind, str)
        or kind not in _TRUSTED_REVIEW_KINDS
        or not isinstance(target_id, str)
        or not target_id
        or type(page_start) is not int
        or page_start < 0
        or type(page_end) is not int
        or page_end < page_start
        or item.get("status") not in {"pending", "resolved"}
        or not isinstance(item.get("reasonCodes"), list)
        or not all(isinstance(code, str) and _TRUSTED_SAFE_CODE.fullmatch(code) for code in item["reasonCodes"])
        or type(item.get("requiresAcknowledgment")) is not bool
        or type(item.get("commonOnly")) is not bool
        or not isinstance(item.get("provenance"), str)
        or _TRUSTED_SAFE_CODE.fullmatch(item["provenance"]) is None
    ):
        return "TRUSTED_FINALIZE_INVALID"
    if kind in {"name", "institution"}:
        target = next(
            (occurrence for occurrence in manifest.get("occurrences", [])
             if isinstance(occurrence, dict) and occurrence.get("occurrenceId") == target_id),
            None,
        )
        target_start = target_end = target.get("page") if isinstance(target, dict) else None
    elif kind == "region_geometry":
        target = next(
            (region for region in manifest.get("regions", [])
             if isinstance(region, dict) and region.get("regionId") == target_id),
            None,
        )
        target_start = target_end = target.get("page") if isinstance(target, dict) else None
    else:
        target = next(
            (segment for segment in manifest.get("segments", [])
             if isinstance(segment, dict) and segment.get("segmentId") == target_id),
            None,
        )
        target_start = target.get("pageStart") if isinstance(target, dict) else None
        target_end = target.get("pageEnd") if isinstance(target, dict) else None
    if (
        not isinstance(target_start, int)
        or not isinstance(target_end, int)
        or page_start < target_start
        or page_end > target_end
    ):
        return "TRUSTED_FINALIZE_INVALID"
    return None


def _trusted_review_category(manifest: dict[str, Any], review: dict[str, Any]) -> str:
    kind = review.get("kind")
    target_id = review.get("targetId")
    if kind in {"name", "institution"}:
        for occurrence in manifest.get("occurrences", []):
            if isinstance(occurrence, dict) and occurrence.get("occurrenceId") == target_id:
                category = occurrence.get("category")
                if isinstance(category, str) and _TRUSTED_SAFE_CODE.fullmatch(category):
                    return category
    if kind == "region_geometry":
        for region in manifest.get("regions", []):
            if isinstance(region, dict) and region.get("regionId") == target_id:
                region_kind = region.get("kind")
                if isinstance(region_kind, str) and _TRUSTED_SAFE_CODE.fullmatch(region_kind):
                    return region_kind
    return str(kind)


def _trusted_coverage_page_range(manifest: dict[str, Any], kind: str) -> tuple[int, int]:
    pages = [
        region.get("page")
        for region in manifest.get("regions", [])
        if isinstance(region, dict) and region.get("kind") == kind and type(region.get("page")) is int
    ]
    if pages:
        return min(pages), max(pages)
    segment_pages = [
        page
        for segment in manifest.get("segments", [])
        if isinstance(segment, dict)
        for page in (segment.get("pageStart"), segment.get("pageEnd"))
        if type(page) is int
    ]
    return (min(segment_pages), max(segment_pages)) if segment_pages else (0, 0)


def _trusted_save_confirmation_reviews(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    pending_region_kinds = {
        region.get("kind")
        for review in manifest.get("reviewItems", [])
        if isinstance(review, dict)
        and review.get("status") != "resolved"
        and review.get("kind") == "region_geometry"
        for region in manifest.get("regions", [])
        if isinstance(region, dict)
        and isinstance(region.get("kind"), str)
        and region.get("regionId") == review.get("targetId")
    }
    unresolved_reviews = [
        {
            "kind": review["kind"],
            "target_id": review["targetId"],
            "category": _trusted_review_category(manifest, review),
            "page_start": review["pageStart"],
            "page_end": review["pageEnd"],
            "reason_codes": list(review["reasonCodes"]) or ["unresolved_review"],
        }
        for review in manifest.get("reviewItems", [])
        if isinstance(review, dict) and review.get("status") != "resolved"
    ]
    approval_coverage = manifest.get("approvalCoverage")
    if (
        isinstance(approval_coverage, dict)
        and approval_coverage.get("state") == "indeterminate"
        and "approval" not in pending_region_kinds
    ):
        page_start, page_end = _trusted_coverage_page_range(manifest, "approval")
        unresolved_reviews.append({
            "kind": "coverage",
            "target_id": "approval",
            "category": "approval",
            "page_start": page_start,
            "page_end": page_end,
            "reason_codes": ["indeterminate_coverage"],
        })
    required_coverage = manifest.get("requiredRegionCoverage")
    if isinstance(required_coverage, dict):
        coverage_kinds = required_coverage.get("kinds")
        if isinstance(coverage_kinds, list):
            for item in coverage_kinds:
                if (
                    not isinstance(item, dict)
                    or item.get("state") != "indeterminate"
                    or (
                        isinstance(item.get("kind"), str)
                        and item.get("kind") in pending_region_kinds
                    )
                    or (
                        item.get("kind") == "approval"
                        and isinstance(approval_coverage, dict)
                        and approval_coverage.get("state") == "indeterminate"
                    )
                ):
                    continue
                kind = item.get("kind")
                if not isinstance(kind, str) or _TRUSTED_SAFE_CODE.fullmatch(kind) is None:
                    continue
                page_start, page_end = _trusted_coverage_page_range(manifest, kind)
                unresolved_reviews.append({
                    "kind": "coverage",
                    "target_id": kind,
                    "category": kind,
                    "page_start": page_start,
                    "page_end": page_end,
                    "reason_codes": ["indeterminate_coverage"],
                })
    return unresolved_reviews



def _validate_trusted_finalize_manifest(manifest: Any, original: str, opts: dict[str, Any]) -> tuple[int, bytes]:
    """Validate immutable, Rust-owned finalization inputs before rendering."""
    if not isinstance(manifest, dict):
        raise ValueError("TRUSTED_FINALIZE_INVALID")
    try:
        original_bytes = Path(original).read_bytes()
    except OSError:
        raise ValueError("TRUSTED_FINALIZE_ORIGINAL_UNAVAILABLE") from None

    expected_hash = manifest.get("originalDocumentHash")
    if (
        not isinstance(expected_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash.lower())
        or hashlib.sha256(original_bytes).hexdigest() != expected_hash.lower()
    ):
        raise ValueError("ORIGINAL_CHANGED")

    revision = manifest.get("analysisRevision")
    run_id = manifest.get("runId")
    expected_run_id = opts.get("run_id", opts.get("runId"))
    expected_analysis_revision = opts.get("analysis_revision")
    expected_profile = opts.get("profile")
    expected_options_hash = opts.get("options_hash", opts.get("optionsHash"))
    expected_threshold_hash = opts.get("threshold_hash", opts.get("thresholdHash"))
    expected_threshold_version = opts.get("threshold_version", opts.get("thresholdVersion"))
    expected_threshold_artifact = opts.get("threshold_artifact")
    warnings_confirmed = opts.get("warnings_confirmed")
    if (
        not isinstance(expected_run_id, str)
        or not expected_run_id
        or type(expected_analysis_revision) is not int
        or expected_analysis_revision < 1
        or expected_profile not in {"internal_review", "official_dispatch", "mixed"}
        or not isinstance(expected_options_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_options_hash) is None
        or not isinstance(expected_threshold_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_threshold_hash) is None
        or not isinstance(expected_threshold_version, str)
        or not expected_threshold_version
        or not isinstance(expected_threshold_artifact, dict)
        or set(expected_threshold_artifact) != {
            "version", "content_hash", "auto_mask_threshold", "review_threshold",
        }
        or type(warnings_confirmed) is not bool
    ):
        raise ValueError("TRUSTED_FINALIZE_AUTHORITY_MISSING")
    try:
        canonical_expected_threshold = _threshold_artifact(
            opts.get("auto_mask_threshold"),
            opts.get("review_threshold"),
        )
    except ValueError:
        raise ValueError("TRUSTED_FINALIZE_AUTHORITY_MISSING") from None
    if (
        expected_threshold_version != canonical_expected_threshold["version"]
        or expected_threshold_hash != canonical_expected_threshold["content_hash"]
    ):
        raise ValueError("STALE_ANALYSIS")
    if expected_threshold_artifact != canonical_expected_threshold:
        raise ValueError("TRUSTED_FINALIZE_AUTHORITY_MISSING")

    manifest_threshold = manifest.get("thresholdArtifact")
    if not isinstance(manifest_threshold, dict) or set(manifest_threshold) != {
        "version", "contentHash", "autoMaskThreshold", "reviewThreshold",
    }:
        raise ValueError("STALE_ANALYSIS")
    normalized_manifest_threshold = {
        "version": manifest_threshold.get("version"),
        "content_hash": manifest_threshold.get("contentHash"),
        "auto_mask_threshold": manifest_threshold.get("autoMaskThreshold"),
        "review_threshold": manifest_threshold.get("reviewThreshold"),
    }
    if (
        type(revision) is not int
        or revision < 1
        or revision != expected_analysis_revision
        or not isinstance(run_id, str)
        or run_id != expected_run_id
        or manifest.get("profile") != expected_profile
        or manifest.get("policyVersion") != "masking-policy-v1"
        or manifest.get("optionsHash") != expected_options_hash
        or manifest.get("thresholdVersion") != expected_threshold_version
        or manifest.get("thresholdHash") != expected_threshold_hash
        or normalized_manifest_threshold != canonical_expected_threshold
        or manifest.get("coordinateSpace") != "pdf_points_top_left"
    ):
        raise ValueError("STALE_ANALYSIS")
    occurrences = manifest.get("occurrences")
    if not isinstance(occurrences, list):
        raise ValueError("TRUSTED_FINALIZE_INVALID")
    occurrence_ids: set[str] = set()
    for occurrence in occurrences:
        occurrence_error = _trusted_occurrence_validation_error(occurrence, revision)
        if occurrence_error is not None:
            raise ValueError(occurrence_error)
        occurrence_id = occurrence["occurrenceId"]
        if occurrence_id in occurrence_ids:
            raise ValueError("TRUSTED_FINALIZE_INVALID")
        occurrence_ids.add(occurrence_id)
    manual_actions = manifest.get("manualActions")
    if not isinstance(manual_actions, list):
        raise ValueError("TRUSTED_FINALIZE_INVALID")
    for action in manual_actions:
        scan_manual = isinstance(action, dict) and action.get("sourceKind") == "scan"
        if (
            not isinstance(action, dict)
            or type(action.get("analysisRevision")) is not int
            or action.get("analysisRevision") != revision
            or not isinstance(action.get("protectedNeighborRefs"), list)
            or (
                action.get("mode") == "restore"
                and (
                    action.get("sourceKind") != "text_pdf"
                    or not isinstance(action.get("linkedOccurrenceId"), str)
                    or not isinstance(action.get("expectedTextHash"), str)
                    or not isinstance(action.get("restoreAuthorizationHash"), str)
                    or re.fullmatch(r"[0-9a-fA-F]{64}", action["restoreAuthorizationHash"]) is None
                    or action["protectedNeighborRefs"]
                )
            )
            or (
                action.get("mode") == "mask"
                and action.get("restoreAuthorizationHash") is not None
            )
            or (scan_manual and (
                action.get("linkedOccurrenceId") is not None
                or action.get("expectedTextHash") is not None
                or action["protectedNeighborRefs"]
            ))
            or (
                not scan_manual
                and action.get("mode") != "restore"
                and not action["protectedNeighborRefs"]
            )
            or any(
                not isinstance(rect, dict)
                or set(rect) != {"x0", "y0", "x1", "y1"}
                or not all(isinstance(rect[key], (int, float)) and math.isfinite(float(rect[key]))
                           for key in ("x0", "y0", "x1", "y1"))
                or float(rect["x1"]) <= float(rect["x0"]) or float(rect["y1"]) <= float(rect["y0"])
                for rect in action["protectedNeighborRefs"]
            )
        ):
            raise ValueError("STALE_ANALYSIS")
        if action.get("mode") == "restore":
            linked = next(
                (
                    occurrence
                    for occurrence in occurrences
                    if occurrence.get("occurrenceId") == action.get("linkedOccurrenceId")
                ),
                None,
            )
            if (
                linked is None
                or linked.get("page") != action.get("page")
                or linked.get("proposedAction") != "mask"
                or linked.get("state") not in {"confirmed", "user_confirmed"}
                or linked.get("expectedTextHash") != action.get("expectedTextHash")
                or linked.get("rects") != action.get("rects")
            ):
                raise ValueError("STALE_ANALYSIS")
    # A later manual edit may add protected label/role/date/status neighbors.
    # Automatic masks are monotonic only when none of their geometry expands
    # into those protected rectangles.
    manual_excluded_occurrence_ids = _manual_excluded_occurrence_ids(manifest)
    for occurrence in occurrences:
        if (
            occurrence.get("proposedAction") != "mask"
            or occurrence.get("category") == "custom_keyword"
            or occurrence["occurrenceId"] in manual_excluded_occurrence_ids
        ):
            continue
        for action in manual_actions:
            if action.get("page") != occurrence.get("page"):
                continue
            mask_rects = [
                tuple(float(rect[key]) for key in ("x0", "y0", "x1", "y1"))
                for rect in occurrence["rects"]
            ]
            protected_rects = [
                tuple(float(rect[key]) for key in ("x0", "y0", "x1", "y1"))
                for rect in action["protectedNeighborRefs"]
            ]
            if not automatic_masks_preserve_manual_neighbors(mask_rects, protected_rects):
                raise ValueError("AUTOMATIC_MASK_PROTECTED_NEIGHBOR_OVERLAP")
    reviews = manifest.get("reviewItems")
    if not isinstance(reviews, list):
        raise ValueError("TRUSTED_FINALIZE_INVALID")
    for review in reviews:
        review_error = _trusted_review_validation_error(review, manifest, revision)
        if review_error is not None:
            raise ValueError(review_error)
    if any(review.get("status") != "resolved" for review in reviews) and not warnings_confirmed:
        raise ValueError("UNRESOLVED_REVIEW")
    if not _trusted_manifest_covers_pdf(manifest, original_bytes, revision):
        raise ValueError("TRUSTED_FINALIZE_INVALID")
    covered_pages = {
        page
        for segment in manifest["segments"]
        for page in range(segment["pageStart"], segment["pageEnd"] + 1)
    }
    if any(occurrence["page"] not in covered_pages for occurrence in occurrences):
        raise ValueError("TRUSTED_FINALIZE_OCCURRENCE_OUTSIDE_SEGMENT")
    return revision, original_bytes


def trusted_finalize_manifest(
    original: str, manifest: dict[str, Any], opts: dict[str, Any], staging_output: str,
) -> dict[str, Any]:
    """Geometry-only finalizer for the immutable Rust-owned manifest."""
    snapshot_path: str | None = None
    staging_path = Path(staging_output)
    reserved_staging = (
        staging_path.exists()
        and not staging_path.is_symlink()
        and staging_path.is_file()
        and staging_path.stat().st_size == 0
    )
    if (
        (staging_path.exists() and not reserved_staging)
        or staging_path.is_symlink()
        or Path(original).is_symlink()
    ):
        raise ValueError("TRUSTED_FINALIZE_INVALID")
    try:
        original = str(Path(original).resolve(strict=True))
        staging_output = str(staging_path.resolve(strict=reserved_staging))
        if same_path(original, staging_output):
            raise ValueError("TRUSTED_FINALIZE_ALIAS_BLOCKED")
    except (OSError, RuntimeError):
        raise ValueError("TRUSTED_FINALIZE_INVALID") from None
    render_output = f"{staging_output}.render.pdf" if reserved_staging else staging_output
    try:
        revision, original_bytes = _validate_trusted_finalize_manifest(manifest, original, opts)
        descriptor, snapshot_path = tempfile.mkstemp(prefix="trusted_finalize_", suffix=Path(original).suffix)
        os.close(descriptor)
        Path(snapshot_path).write_bytes(original_bytes)
        manual_excluded_occurrence_ids = _manual_excluded_occurrence_ids(manifest)
        occurrence_inputs = tuple(
            OccurrenceRedactionInput(
                occurrence_id=item["occurrenceId"], run_id=manifest["runId"],
                document_sha256=manifest["originalDocumentHash"], analysis_revision=item["analysisRevision"],
                page_index=item["page"], rect_list=tuple(
                    (float(rect["x0"]), float(rect["y0"]), float(rect["x1"]), float(rect["y1"]))
                    for rect in item["rects"]),
                action="exclude" if item["occurrenceId"] in manual_excluded_occurrence_ids else item["proposedAction"],
                provenance=item["provenance"],
                expected_text_hash=item["expectedTextHash"],
                category=item.get("category"),
            ) for item in manifest["occurrences"]
        )
        manual_actions = tuple(
            ManualActionV1(
                manual_action_id=item["actionId"], run_id=manifest["runId"],
                document_sha256=manifest["originalDocumentHash"],
                analysis_revision=item["analysisRevision"], page_index=item["page"],
                rect_list=tuple((float(rect["x0"]), float(rect["y0"]), float(rect["x1"]), float(rect["y1"]))
                               for rect in item["rects"]),
                mode=item["mode"], source_kind=item["sourceKind"],
                linked_occurrence_id=item.get("linkedOccurrenceId"),
                expected_text_hash=item.get("expectedTextHash"),
                protected_neighbor_refs=tuple(
                    (float(rect["x0"]), float(rect["y0"]), float(rect["x1"]), float(rect["y1"]))
                    for rect in item["protectedNeighborRefs"]
                ),
                restore_authorization_hash=item.get("restoreAuthorizationHash"),
            ) for item in manifest["manualActions"]
        )
        if hashlib.sha256(Path(snapshot_path).read_bytes()).hexdigest() != manifest["originalDocumentHash"].lower():
            raise ValueError("ORIGINAL_CHANGED")
        if occurrence_inputs:
            try:
                result = redact_pdf_native(
                    snapshot_path,
                    render_output,
                    (),
                    display_mode=str(opts.get("display_mode", "black")),
                    occurrence_inputs=occurrence_inputs,
                    expected_run_id=manifest["runId"],
                    expected_document_sha256=manifest["originalDocumentHash"].lower(),
                    expected_analysis_revision=revision,
                    profile=manifest["profile"],
                )
            except Exception:
                raise ValueError("TRUSTED_FINALIZE_REDACTION_EXECUTION_FAILED") from None
        else:
            shutil.copyfile(snapshot_path, staging_output)
            if Path(staging_output).read_bytes() != original_bytes:
                raise ValueError("TRUSTED_FINALIZE_CLEAN_COPY_MISMATCH")
            render_output = staging_output
            result = {
                "status": "applied",
                "output_file": staging_output,
                "verification": {"verified": True, "reason_code": "clean_document"},
            }
        if not isinstance(result, dict):
            raise ValueError("TRUSTED_FINALIZE_REDACTION_RESULT_FAILED")
        verification = result.get("verification")
        reason_code = verification.get("reason_code") if isinstance(verification, dict) else None
        if reason_code == "occurrence_intrinsic_verification_failed":
            raise TrustedFinalizeOccurrenceIntrinsicError(
                _trusted_occurrence_intrinsic_diagnostics(result)
            )
        if (
            result.get("status") != "applied"
            or not isinstance(verification, dict)
            or verification.get("verified") is not True
        ):
            raise ValueError("TRUSTED_FINALIZE_REDACTION_RESULT_FAILED")
        occurrence_masks_applied = result.get("occurrences_applied", 0)
        if (
            type(occurrence_masks_applied) is not int
            or occurrence_masks_applied < 0
        ):
            raise ValueError("TRUSTED_FINALIZE_REDACTION_RESULT_FAILED")

        manual_masks_applied = 0
        manual_restores_applied = 0
        scan_manual_verification: dict[str, bool] | None = None
        if manual_actions:
            try:
                manual_source_hash = hashlib.sha256(Path(render_output).read_bytes()).hexdigest()
            except OSError:
                raise ValueError("TRUSTED_FINALIZE_STAGING_READ_FAILED") from None
            manual_render_actions = tuple(
                replace(action, document_sha256=manual_source_hash)
                for action in manual_actions
            )
            manual_output = f"{staging_output}.manual.pdf"
            scan_verifier = (
                ScanManualRasterVerifier({
                    page: tuple(
                        rect
                        for action in manual_render_actions
                        if action.source_kind == "scan" and action.page_index == page
                        for rect in action.rect_list
                    )
                    for page in {action.page_index for action in manual_render_actions if action.source_kind == "scan"}
                })
                if any(action.source_kind == "scan" for action in manual_render_actions)
                else None
            )
            try:
                manual_result = apply_manual_actions_v1(
                    render_output,
                    manual_output,
                    manual_render_actions,
                    expected_run_id=manifest["runId"],
                    expected_document_sha256=manual_source_hash,
                    expected_analysis_revision=revision,
                    display_mode=str(opts.get("display_mode", "black")),
                    raster_adapter=scan_verifier,
                    ocr_adapter=scan_verifier,
                    restore_source_pdf_path=snapshot_path,
                )
            except Exception:
                raise ValueError("TRUSTED_FINALIZE_MANUAL_EXECUTION_FAILED") from None
            if (
                not isinstance(manual_result, dict)
                or manual_result.get("status") != "applied"
                or not isinstance(manual_result.get("verification"), dict)
                or manual_result["verification"].get("verified") is not True
            ):
                raise TrustedFinalizeManualResultError(
                    _trusted_manual_result_diagnostics(manual_result)
                )
            try:
                if reserved_staging:
                    shutil.copyfile(manual_output, staging_output)
                else:
                    os.replace(manual_output, staging_output)
            except OSError:
                raise ValueError("TRUSTED_FINALIZE_PROMOTION_FAILED") from None
            manual_masks_applied = manual_result.get("mask_actions_applied")
            manual_restores_applied = manual_result.get("restore_actions_applied")
            if manual_masks_applied is None and manual_restores_applied is None:
                # Test doubles and older in-process callers report only the
                # operation total. The native wrapper used by the product
                # always supplies the split counts above.
                operation_count = manual_result.get("actions_applied")
                if type(operation_count) is int and operation_count == len(manual_actions):
                    manual_masks_applied = sum(action.mode == "mask" for action in manual_actions)
                    manual_restores_applied = sum(action.mode == "restore" for action in manual_actions)
            if (
                type(manual_masks_applied) is not int
                or manual_masks_applied < 0
                or type(manual_restores_applied) is not int
                or manual_restores_applied < 0
            ):
                raise ValueError("TRUSTED_FINALIZE_MANUAL_RESULT_FAILED")
            if scan_verifier is not None:
                try:
                    scan_manual_verification = scan_verifier.summary()
                except ValueError:
                    raise ValueError("TRUSTED_FINALIZE_MANUAL_RESULT_FAILED") from None
        elif reserved_staging and occurrence_inputs:
            try:
                shutil.copyfile(render_output, staging_output)
            except OSError:
                raise ValueError("TRUSTED_FINALIZE_PROMOTION_FAILED") from None

        for temporary_output in (f"{staging_output}.manual.pdf", f"{staging_output}.render.pdf"):
            try:
                Path(temporary_output).unlink(missing_ok=True)
            except OSError:
                raise ValueError("TRUSTED_FINALIZE_CLEANUP_FAILED") from None

        try:
            final_bytes = Path(staging_output).read_bytes()
        except OSError:
            raise ValueError("TRUSTED_FINALIZE_STAGING_READ_FAILED") from None
        effective_excluded_occurrence_ids = _manual_excluded_occurrence_ids(manifest)
        manual_mask_count = sum(
            item.get("mode") == "mask"
            for item in manifest["manualActions"]
        )
        restore_count = sum(
            item.get("mode") == "restore"
            for item in manifest["manualActions"]
        )
        expected_applied_mask_count = (
            sum(
                item.get("proposedAction") == "mask"
                and item.get("state") in {"confirmed", "user_confirmed"}
                and item["occurrenceId"] not in effective_excluded_occurrence_ids
                for item in manifest["occurrences"]
            )
            + manual_mask_count
        )
        if (
            occurrence_masks_applied + manual_masks_applied != expected_applied_mask_count
            or manual_masks_applied != manual_mask_count
            or manual_restores_applied != restore_count
        ):
            raise ValueError("TRUSTED_FINALIZE_REDACTION_RESULT_FAILED")
        unresolved_reviews = _trusted_save_confirmation_reviews(manifest)
        final_verification: dict[str, Any] = {"verified": True}
        if scan_manual_verification is not None:
            final_verification["scan_manual"] = scan_manual_verification
        return {
            "status": "applied",
            "staging_hash": hashlib.sha256(final_bytes).hexdigest(),
            "verification": final_verification,
            "save_confirmation": {
                "status": "user_confirmed" if unresolved_reviews else "not_required",
                "unresolved_reviews": unresolved_reviews,
            },
            "occurrence_count": expected_applied_mask_count,
            "applied_mask_count": expected_applied_mask_count,
            "manual_mask_count": manual_mask_count,
            "restore_count": restore_count,
            "effective_mask_count": expected_applied_mask_count,
            "raw_text_returned": False,
        }
    except ValueError as error:
        if not _trusted_finalize_cleanup(staging_output):
            raise ValueError("TRUSTED_FINALIZE_CLEANUP_FAILED") from None
        if str(error) in {
            "AUTOMATIC_MASK_PROTECTED_NEIGHBOR_OVERLAP",
            "ORIGINAL_CHANGED",
            "SCAN_VERIFICATION_ADAPTER_UNAVAILABLE",
            "STALE_ANALYSIS",
            "TRUSTED_FINALIZE_AUTHORITY_MISSING",
            "TRUSTED_FINALIZE_BLOCKED",
            "TRUSTED_FINALIZE_CLEAN_COPY_MISMATCH",
            "TRUSTED_FINALIZE_INVALID",
            "TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED",
            "TRUSTED_FINALIZE_REDACTION_EXECUTION_FAILED",
            "TRUSTED_FINALIZE_MANUAL_EXECUTION_FAILED",
            "TRUSTED_FINALIZE_MANUAL_RESULT_FAILED",
            "TRUSTED_FINALIZE_STAGING_READ_FAILED",
            "TRUSTED_FINALIZE_INTERNAL_FAILED",
            "TRUSTED_FINALIZE_ORIGINAL_UNAVAILABLE",
            "TRUSTED_FINALIZE_PROMOTION_FAILED",
            "UNRESOLVED_REVIEW",
        }:
            raise
        raise ValueError("TRUSTED_FINALIZE_BLOCKED") from None
    except Exception:
        if not _trusted_finalize_cleanup(staging_output):
            raise ValueError("TRUSTED_FINALIZE_CLEANUP_FAILED") from None
        raise ValueError("TRUSTED_FINALIZE_INTERNAL_FAILED") from None
    finally:
        if snapshot_path is not None:
            try:
                Path(snapshot_path).unlink(missing_ok=True)
            except OSError:
                raise ValueError("TRUSTED_FINALIZE_CLEANUP_FAILED") from None

def _extract_and_analyze_snapshot(
    infile: str,
    opts: dict[str, Any],
    *,
    session_hash_key: bytes | None = None,
) -> tuple[bytes, str, ExtractResult, dict[str, Any] | None]:
    """Use one immutable source copy for extraction, manifest evidence, and rendering."""
    try:
        source_bytes = Path(infile).read_bytes()
    except OSError:
        raise ValueError("SOURCE_UNAVAILABLE") from None
    descriptor, snapshot_path = tempfile.mkstemp(
        prefix="masking_source_",
        suffix=Path(infile).suffix,
    )
    os.close(descriptor)
    try:
        Path(snapshot_path).write_bytes(source_bytes)
        extracted = extract_document(snapshot_path, engine=opts.get("extract_engine", "auto"))
        if Path(infile).read_bytes() != source_bytes:
            raise ValueError("ORIGINAL_CHANGED")
        manifest = None
        if _profile_value(str(opts["profile"])) != "legal":
            manifest = trusted_analysis_manifest(
                snapshot_path,
                opts,
                session_hash_key=session_hash_key,
                source_bytes=source_bytes,
                extracted=extracted,
            )
        if Path(infile).read_bytes() != source_bytes:
            raise ValueError("ORIGINAL_CHANGED")
        _ACTIVE_SOURCE_SNAPSHOT.set(snapshot_path)
        return source_bytes, snapshot_path, extracted, manifest
    except Exception:
        try:
            os.unlink(snapshot_path)
        except FileNotFoundError:
            pass
        except OSError:
            raise RuntimeError("SOURCE_SNAPSHOT_CLEANUP_FAILED") from None
        raise



def _process_file(
    infile: str,
    outdir: str | None = None,
    opts: dict[str, Any] | None = None,
    *,
    session_hash_key: bytes | None = None,
) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    opts = normalize_opts(opts)
    profile = _profile_value(str(opts.get("profile", "mixed") or "mixed"))
    artifacts = resolve_output_artifacts(opts)
    output_paths = safe_output_paths(infile, outdir=outdir)
    source_bytes, snapshot_path, extract_result, canonical_manifest = _extract_and_analyze_snapshot(
        infile, opts, session_hash_key=session_hash_key,
    )
    _ACTIVE_SOURCE_SNAPSHOT.set(snapshot_path)
    def reject_changed_source() -> None:
        try:
            os.unlink(snapshot_path)
        except FileNotFoundError:
            pass
        except OSError:
            raise RuntimeError("SOURCE_SNAPSHOT_CLEANUP_FAILED") from None
        raise ValueError("ORIGINAL_CHANGED")
    document_context = build_document_context(
        extract_result.text,
        chunk_size=max(int(opts.get("context_chunk_size", 1200) or 1200), 1),
        overlap=max(int(opts.get("context_chunk_overlap", 120) or 120), 0),
    )
    profile_analysis = (
        {
            "schema_version": "profile-analysis-v1",
            "analysis_revision": int(opts.get("analysis_revision", 1) or 1),
            "segments": [],
            "regions": [],
            "reviews": [],
            "hard_block_review_count": 0,
            "hard_block_reason_codes": [],
        }
        if canonical_manifest is None
        else {
            "schema_version": "profile-analysis-v1",
            "analysis_revision": canonical_manifest["analysis_revision"],
            "segments": canonical_manifest["segments"],
            "regions": canonical_manifest["regions"],
            "reviews": canonical_manifest["review_items"],
            "hard_block_review_count": sum(
                item["status"] == "pending" and item["requires_acknowledgment"]
                for item in canonical_manifest["review_items"]
            ),
            "hard_block_reason_codes": sorted({
                reason
                for item in canonical_manifest["review_items"]
                if item["status"] == "pending" and item["requires_acknowledgment"]
                for reason in item["reason_codes"]
            }),
        }
    )
    if canonical_manifest is not None:
        return None, None, None, {
            "schema_version": "public-analysis-only-v1",
            "analysis_manifest": canonical_manifest,
            "profile_analysis": profile_analysis,
            "raw_text_returned": False,
        }

    transform_state = TransformState()
    masked, counts, redaction_matches, chunk_queue = process_masking_queue(
        extract_result.text,
        opts,
        transform_state=transform_state,
    )

    masked, llm_refine = llm_refine_masking(masked, opts, counts)
    if Path(infile).read_bytes() != source_bytes:
        reject_changed_source()
    extracted_path = None
    masked_path = output_paths["masked_txt"] if "masked_txt" in artifacts else None
    report_path = output_paths["report_json"] if "report" in artifacts else None
    masked_pdf_path = output_paths["masked_pdf"]
    labeled_pdf_path = output_paths["labeled_pdf"] if "labeled_pdf" in artifacts else None

    if masked_path:
        write_text_file(masked_path, masked)

    pdf_redaction_enabled = bool(opts.get("pdf_redaction", True)) and "pdf" in artifacts
    display_mode = str(opts.get("display_mode", "black") or "black")
    if display_mode not in {"black", "label_en", "label_ko", "pseudonym"}:
        display_mode = "black"
    preview_pdf_source_path: str | None = None
    pdf_redaction_result: dict[str, Any] = {
        "enabled": bool(opts.get("pdf_redaction", True)),
        "status": "skipped",
        "output_file": None,
        "display_mode": display_mode,
        "targets_requested": 0,
        "targets_hit": 0,
        "missing_targets_count": 0,
        "review_items": [],
        "verification": {"verified": False, "residual_hits": 0},
        "reason": "입력 파일이 PDF가 아닙니다." if Path(infile).suffix.lower() != ".pdf" else "비활성화됨 또는 산출물 선택에서 제외됨",
    }
    if Path(infile).suffix.lower() == ".pdf" and bool(opts.get("pdf_redaction", True)):
        if Path(infile).read_bytes() != source_bytes:
            reject_changed_source()
        try:
            target_pdf_path = masked_pdf_path if "pdf" in artifacts else os.path.join(
                tempfile.mkdtemp(prefix="yangcheon_masker_pdf_preview_"),
                Path(masked_pdf_path).name,
            )
            pdf_redaction_result = redact_pdf_native(
                snapshot_path,
                target_pdf_path,
                redaction_matches,
                display_mode=display_mode,
                transform_state=transform_state,
                profile=profile,
                legal_compatibility=profile == "legal",
            )
            if Path(infile).read_bytes() != source_bytes:
                reject_changed_source()
            preview_pdf_source_path = target_pdf_path
            if "pdf" not in artifacts:
                pdf_redaction_result["output_file"] = None
        except Exception as error:
            if isinstance(error, ValueError) and str(error) == "ORIGINAL_CHANGED":
                raise
            e = error
            pdf_redaction_result = {
                "enabled": bool(opts.get("pdf_redaction", True)),
                "status": "failed",
                "output_file": None,
                "display_mode": display_mode,
                "targets_requested": len(_redaction_search_terms(redaction_matches)),
                "targets_hit": 0,
                "missing_targets_count": len(_redaction_search_terms(redaction_matches)),
                "review_items": review_items_for_matches(
                    redaction_matches,
                    counts,
                    status="missing_pdf_rect",
                    document_context=document_context,
                ),
                "verification": {
                    "verified": False,
                    "residual_hits": 0,
                    "reason_code": classify_redaction_failure_reason_code(e),
                },
                "reason": "PDF_NATIVE_REDACTION_FAILED",
                "reason_code": classify_redaction_failure_reason_code(e),
            }
        if labeled_pdf_path and display_mode == "black":
            if Path(infile).read_bytes() != source_bytes:
                reject_changed_source()
            try:
                labeled_result = redact_pdf_native(
                    snapshot_path,
                    labeled_pdf_path,
                    redaction_matches,
                    display_mode="label_en",
                    profile=profile,
                    legal_compatibility=profile == "legal",
                )
                pdf_redaction_result["labeled_output_file"] = labeled_result.get("output_file")
                if Path(infile).read_bytes() != source_bytes:
                    reject_changed_source()
            except ValueError as error:
                if str(error) == "ORIGINAL_CHANGED":
                    raise
                pdf_redaction_result["labeled_output_error"] = "PDF_LABEL_RENDER_FAILED"
            except Exception:
                pdf_redaction_result["labeled_output_error"] = "PDF_LABEL_RENDER_FAILED"
    elif Path(infile).suffix.lower() == ".pdf":
        preview_pdf_source_path = snapshot_path

    report = build_safe_report(
        input_file=infile,
        opts=opts,
        counts=counts,
        redaction_matches=redaction_matches,
        extract_meta={
            "schema_version": extract_result.schema_version,
            "engine_selected": str(opts.get("extract_engine", "auto")),
            "engine_used": extract_result.engine_used,
            "engine_chain": extract_result.engine_chain,
            "fallback_chain": extract_result.fallback_chain,
            "duration_sec": round(extract_result.duration_sec, 3),
            "notes": extract_result.notes,
            "chars": len(extract_result.text),
            "page_count": len(extract_result.pages),
        },
        pdf_redaction_result=pdf_redaction_result,
        output_paths={
            "extracted_file": extracted_path,
            "masked_file": masked_path,
            "masked_pdf_file": pdf_redaction_result.get("output_file"),
            "labeled_pdf_file": pdf_redaction_result.get("labeled_output_file") or labeled_pdf_path,
            "report_path": report_path,
            "manual_pdf_path": output_paths["manual_pdf"],
            "preview_pdf_source_file": preview_pdf_source_path,
        },
        chunk_queue=chunk_queue,
        llm_refine=llm_refine,
        document_context=document_context,
        source_text=extract_result.text,
    )
    report["profile_analysis"] = profile_analysis

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    if bool(opts.get("strict_quality_gate", False)):
        enforce_quality_gate_or_raise(report)

    return extracted_path, masked_path, report_path, report

def process_file(
    infile: str,
    outdir: str | None = None,
    opts: dict[str, Any] | None = None,
    *,
    session_hash_key: bytes | None = None,
) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    token = _ACTIVE_SOURCE_SNAPSHOT.set(None)
    try:
        return _process_file(
            infile, outdir=outdir, opts=opts, session_hash_key=session_hash_key,
        )
    finally:
        snapshot_path = _ACTIVE_SOURCE_SNAPSHOT.get()
        _ACTIVE_SOURCE_SNAPSHOT.reset(token)
        if snapshot_path is not None:
            try:
                os.unlink(snapshot_path)
            except FileNotFoundError:
                pass
            except OSError:
                raise RuntimeError("SOURCE_SNAPSHOT_CLEANUP_FAILED") from None


def extract_result_text_for_preview(infile: str, opts: dict[str, Any]) -> str:
    opts = normalize_opts(opts)
    return extract_document(infile, engine=opts.get("extract_engine", "auto")).text


def mask_text_for_preview(extracted_text: str, opts: dict[str, Any]) -> str:
    opts = normalize_opts(opts)
    masked, _counts, _matches, _meta = process_masking_queue(extracted_text, opts)
    masked, _llm_meta = llm_refine_masking(masked, opts, {})
    return masked


_CLI_RUNTIME_FAILURE_CODES = frozenset({
    "CHUNK_PROCESSOR_FAILED",
    "EXTRACTION_ALL_ENGINES_FAILED",
    "EXTRACTION_ENGINE_UNSUPPORTED",
    "EXTRACTION_MARKER_CLEANUP_FAILED",
    "EXTRACTION_MARKER_EMPTY",
    "EXTRACTION_MARKER_FAILED",
    "EXTRACTION_MARKER_UNAVAILABLE",
    "EXTRACTION_PADDLE_EMPTY",
    "EXTRACTION_PADDLE_FAILED",
    "EXTRACTION_PADDLE_INIT_FAILED",
    "EXTRACTION_PADDLE_UNAVAILABLE",
    "EXTRACTION_PYMUPDF_FAILED",
    "EXTRACTION_PYMUPDF_UNAVAILABLE",
    "EXTRACTION_PYPDF_EMPTY",
    "EXTRACTION_PYPDF_FAILED",
    "EXTRACTION_PYPDF_UNAVAILABLE",
    "OPTIONAL_DETECTOR_FAILED",
    "PAGE_EVIDENCE_ADAPTER_FAILED",
    "PAGE_EVIDENCE_ADAPTER_UNAVAILABLE",
    "SOURCE_SNAPSHOT_CLEANUP_FAILED",
    "TEXT_ENCODING_UNSUPPORTED",
    "TEXT_SOURCE_UNAVAILABLE",
})


def run_cli_mode(argv: list[str]) -> bool:
    if len(argv) <= 1:
        return False

    files = [a for a in argv[1:] if os.path.isfile(a)]
    if not files:
        return False

    opts = normalize_opts(None)
    session_hash_key = os.urandom(32)

    opts["log_callback"] = print

    print("[CLI 모드] 파일 처리 시작")
    for fp in files:
        try:
            extracted_path, masked_path, report_path, report = process_file(
                fp,
                outdir=None,
                opts=opts,
                session_hash_key=session_hash_key,
            )
        except ValueError as error:
            print(f"[실패] 문서 1건: {error}")
            continue
        except RuntimeError as error:
            code = str(error)
            print(f"[실패] 문서 1건: {code if code in _CLI_RUNTIME_FAILURE_CODES else 'PROCESS_FAILED'}")
            continue
        except OSError:
            print("[실패] 문서 1건: PROCESS_FAILED")
            continue
        if report.get("schema_version") == "public-analysis-only-v1":
            analysis = report["profile_analysis"]
            print("[분석 완료] public analysis only; finalization is blocked pending review")
            print(f"  - hard_block_review_count: {analysis['hard_block_review_count']}")
            print(f"  - hard_block_reason_codes: {','.join(analysis['hard_block_reason_codes'])}")
            continue
        print("[완료] 문서 1건")
        print(f"  - engine_used: {report['extract']['engine_used']} ({report['extract']['duration_sec']}s)")
        print(f"  - output_artifacts: {','.join(report['rules'].get('output_artifacts', []))}")
        print(f"  - masked_txt_created: {bool(masked_path)}")
        print(f"  - report_created: {bool(report_path)}")
        print(f"  - counts   : {json.dumps(report['counts'], ensure_ascii=False)}")
        print(f"  - chunk_queue: {json.dumps(report.get('chunk_queue', {}), ensure_ascii=False)}")
        print(f"  - local_llm_refine: {json.dumps(sanitize_for_logging(report.get('local_llm_refine', {})), ensure_ascii=False)}")
        print(f"  - pdf_redaction: {json.dumps(sanitize_for_logging(report['pdf_redaction']), ensure_ascii=False)}")
        print(f"  - product_checks: {json.dumps(sanitize_for_logging(report.get('product_checks', {})), ensure_ascii=False)}")

    return True


def main() -> None:
    run_cli_mode(sys.argv)


if __name__ == "__main__":
    main()
