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
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

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
    "profile": "official",
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


def normalize_opts(opts: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(DEFAULT_MASKING_OPTIONS)
    if opts:
        normalized.update(opts)
    return normalized


from masking_context import DocumentContext, build_document_context, safe_document_context_summary
from privacy_false_positive import (
    is_likely_address_value,
    is_likely_company_value,
    is_likely_court_value,
    is_likely_doc_meta_value,
    is_likely_law_firm_value,
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
)
from privacy_transformers import TransformState, apply_deidentification_policy, normalize_deidentification_policy
from ko_pii_detector import build_ko_pii_detector
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
    extract_document,
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
    ManualCorrectionBox,
    ManualRedactionBox,
    ScannedPdfRedactionError,
    apply_manual_edits_with_restore,
    apply_manual_pdf_corrections,
    apply_manual_redactions,
    redact_pdf_native,
    _finish_pdf_save,
    _fresh_pdf_output_path,
    _normalized_pdf_save_target,
    _redaction_search_terms,
)
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
    SEOUL_GU_OFFICE_PAT,
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
    _sub_approval_table_line,
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

APP_VERSION = "v4.6.1"

PROFILE_DISPLAY_TO_VALUE = {
    "공공문서": "official",
    "법률문서": "legal",
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
    value = (label_or_value or "official").strip()
    return PROFILE_DISPLAY_TO_VALUE.get(value, value).lower()


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
    profile = str(opts.get("profile", "official") or "official").lower()

    use_approval_line = bool(opts.get("approval_line", True))
    use_region_context = bool(opts.get("region_context", True))
    use_doc_meta = bool(opts.get("doc_meta", True))
    use_court = bool(opts.get("court", True))

    # 법률문서 전용: 공문 메타/결재선 규칙 비활성화
    if profile == "legal":
        use_approval_line = False
        use_region_context = False
        use_doc_meta = False

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

    return ChunkProcessResult(
        masked_text=masked,
        counts=counts,
        matches=matches,
        source_boundaries=current_masking_source_boundaries(),
    )


def _document_match(match: RedactionMatch, base_offset: int) -> RedactionMatch:
    if match.start < 0 or match.end <= match.start:
        return match
    return replace(match, start=base_offset + match.start, end=base_offset + match.end)


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


def _apply_source_occurrences(
    masked_text: str,
    matches: list[RedactionMatch],
    source_to_output: dict[int, int],
    korean_tokens: bool,
) -> str:
    edits: list[tuple[int, int, str]] = []
    unmapped: list[RedactionMatch] = []
    for match in matches:
        output_start = source_to_output.get(match.start)
        output_end = source_to_output.get(match.end)
        if output_start is None or output_end is None or output_end <= output_start:
            unmapped.append(match)
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
    for match in unmapped:
        token = _mask_token(match.tag) if korean_tokens else f"[{match.tag}]"
        result = re.sub(re.escape(match.text), lambda _matched: token, result)
    return result


def _ai_redaction_matches(text: str, detector: PrivacyDetector) -> list[RedactionMatch]:
    matches: list[RedactionMatch] = []
    for index, span in enumerate(detector.detect(text), 1):
        if span.start < 0 or span.end <= span.start or span.end > len(text):
            continue
        value = text[span.start:span.end]
        if len(value.strip()) < 2:
            continue
        matches.append(
            RedactionMatch(
                tag=tag_for_label(span.label),
                text=value,
                start=span.start,
                end=span.end,
                occurrence_id=f"ai_occ_{index:06d}",
                source="optional_ai_detector",
            )
        )
    return matches


def _apply_ai_redactions(
    masked_text: str,
    source_text: str,
    matches: list[RedactionMatch],
    existing_matches: list[RedactionMatch],
    korean_tokens: bool,
) -> str:
    result = masked_text
    existing_spans = {(item.start, item.end) for item in existing_matches if item.start >= 0}
    grouped: dict[tuple[str, str], list[RedactionMatch]] = {}
    for match in matches:
        grouped.setdefault((match.tag, match.text), []).append(match)
    for (tag, value), grouped_matches in sorted(grouped.items(), key=lambda item: -len(item[0][1])):
        source_spans = [
            (found.start(), found.end())
            for found in re.finditer(re.escape(value), source_text)
            if (found.start(), found.end()) not in existing_spans
        ]
        targets = {(match.start, match.end) for match in grouped_matches}
        target_ordinals = [index for index, span in enumerate(source_spans) if span in targets]
        output_spans = [(found.start(), found.end()) for found in re.finditer(re.escape(value), result)]
        token = _mask_token(tag) if korean_tokens else f"[{tag}]"
        if not target_ordinals or max(target_ordinals) >= len(output_spans):
            result = re.sub(re.escape(value), lambda _matched: token, result)
            continue
        for ordinal in reversed(target_ordinals):
            start, end = output_spans[ordinal]
            result = result[:start] + token + result[end:]
    return result


def _deidentification_matches(text: str, matches: list[RedactionMatch]) -> list[RedactionMatch]:
    unique: list[RedactionMatch] = []
    seen: set[tuple[int, int, str, str]] = set()
    for match in matches:
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
                result = chunk_processor(chunk, opts)
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
                logger(f"[청크] {idx}/{len(chunks)} 실패 - 기본 규칙 엔진으로 계속 진행")
                fallback = _mask_text_chunk(chunk, opts)
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
            boundary_result = _mask_text_chunk(text[window_start:window_end], opts)
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
        joined_masked = _apply_source_occurrences(
            joined_masked,
            boundary_matches,
            _source_to_output_offsets(chunks, chunk_results),
            korean_tokens=bool(opts.get("korean_tokens", False)),
        )

    detector = opts.get("_privacy_detector")
    uses_default_detector = detector is None
    if uses_default_detector:
        detector = build_ko_pii_detector(logger)
    if detector is not None:
        existing_matches = list(total_matches)
        try:
            ai_matches = _ai_redaction_matches(text, detector)
        except Exception:  # noqa: BROAD_EXCEPT_OK - preserve regex masking at the optional detector boundary
            logger("[AI] 선택 탐지기 처리 실패 - 규칙 기반 결과 유지 (count=0)")
            ai_matches = []
        unique_ai_matches: list[RedactionMatch] = []
        for match in ai_matches:
            key = _occurrence_key(text, match)
            if key is None:
                continue
            if key in occurrence_keys:
                if not uses_default_detector:
                    total_matches.append(match)
                continue
            occurrence_keys.add(key)
            unique_ai_matches.append(match)
            total_matches.append(match)
            _count_up(total_counts, match.tag)
        joined_masked = _apply_ai_redactions(
            joined_masked,
            text,
            unique_ai_matches,
            existing_matches,
            korean_tokens=bool(opts.get("korean_tokens", False)),
        )

    total_matches.sort(key=lambda match: (match.start if match.start >= 0 else len(text), match.end, match.tag, match.text))
    total_matches = [
        match
        if match.source == "optional_ai_detector"
        else replace(match, occurrence_id=f"occ_{index:06d}")
        for index, match in enumerate(total_matches, 1)
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
    profile: str = "official",
) -> tuple[str, dict[str, int], list[RedactionMatch]]:
    profile = profile.lower()
    if profile == "legal":
        use_approval_line = False
        use_region_context = False
        use_doc_meta = False

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
            text = _sub_simple(text, p, "WEAK_PLACE", report, matches=matches, value_group="value")

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
        text = _sub_keep_label_when(
            text,
            APPROVAL_LINE_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            OFFICIAL_ROLE_NAME_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            APPROVAL_GRADE_OCR_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            DEPT_ROLE_NAME_COMPACT_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            OFFICIAL_COMBINED_ROLE_NAME_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            ACTING_APPROVER_NAME_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_approval_table_line(text, APPROVAL_TABLE_LINE_PAT, "APPROVAL_LINE", report, matches=matches)
        text = _sub_simple(text, APPROVAL_TABLE_INLINE_MULTI_PAT, "APPROVAL_LINE", report, matches=matches, value_group="value")
        text = _sub_keep_label_when(
            text,
            APPROVAL_ROLE_PAREN_NAME_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            APPROVAL_ROLE_DATE_NAME_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
        text = _sub_keep_label_when(
            text,
            APPROVAL_DATE_NAME_PAT,
            "APPROVAL_LINE",
            report,
            value_group="value",
            should_mask=lambda m: is_likely_person_name_value(m.group("value")),
            matches=matches,
        )
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


def runtime_manifest_for_report(report: dict[str, Any]) -> dict[str, Any]:
    manifest = getattr(report, "runtime_manifest", None)
    return dict(manifest) if isinstance(manifest, dict) else {"outputs": {}, "review_items": []}



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
        "outputs": dict(output_paths),
        "review_items": runtime_review_items,
        "detected_spans": detected_spans,
        "detection_candidates": detection_candidates,
    }
    return SafeReport({
        "app_version": APP_VERSION,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "raw_values_saved": False,
        "raw_text_returned": False,
        "input": {
            "kind": document_kind,
            "path_saved_in_report": False,
        },
        "extract": {
            "engine_selected": extract_meta.get("engine_selected", opts.get("extract_engine", "auto")),
            "engine_used": extract_meta.get("engine_used"),
            "duration_sec": extract_meta.get("duration_sec"),
            "notes_count": len(extract_meta.get("notes", []) or []),
            "chars": extract_meta.get("chars", 0),
        },
        "rules": {
            "profile": str(opts.get("profile", "official") or "official"),
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




def process_file(
    infile: str,
    outdir: str | None = None,
    opts: dict[str, Any] | None = None,
) -> tuple[str | None, str | None, str | None, dict[str, Any]]:
    opts = normalize_opts(opts)
    artifacts = resolve_output_artifacts(opts)
    output_paths = safe_output_paths(infile, outdir=outdir)

    extract_engine = opts.get("extract_engine", "auto")
    extract_result = extract_document(infile, engine=extract_engine)
    document_context = build_document_context(
        extract_result.text,
        chunk_size=max(int(opts.get("context_chunk_size", 1200) or 1200), 1),
        overlap=max(int(opts.get("context_chunk_overlap", 120) or 120), 0),
    )

    transform_state = TransformState()
    masked, counts, redaction_matches, chunk_queue = process_masking_queue(
        extract_result.text,
        opts,
        transform_state=transform_state,
    )

    masked, llm_refine = llm_refine_masking(masked, opts, counts)
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
        try:
            target_pdf_path = masked_pdf_path if "pdf" in artifacts else os.path.join(
                tempfile.mkdtemp(prefix="yangcheon_masker_pdf_preview_"),
                Path(masked_pdf_path).name,
            )
            pdf_redaction_result = redact_pdf_native(
                infile,
                target_pdf_path,
                redaction_matches,
                display_mode=display_mode,
                transform_state=transform_state,
            )
            preview_pdf_source_path = target_pdf_path
            if "pdf" not in artifacts:
                pdf_redaction_result["output_file"] = None
        except Exception as e:
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
            try:
                labeled_result = redact_pdf_native(infile, labeled_pdf_path, redaction_matches, display_mode="label_en")
                pdf_redaction_result["labeled_output_file"] = labeled_result.get("output_file")
            except Exception:
                pdf_redaction_result["labeled_output_error"] = "PDF_LABEL_RENDER_FAILED"
    elif Path(infile).suffix.lower() == ".pdf":
        preview_pdf_source_path = infile

    report = build_safe_report(
        input_file=infile,
        opts=opts,
        counts=counts,
        redaction_matches=redaction_matches,
        extract_meta={
            "engine_selected": extract_engine,
            "engine_used": extract_result.engine_used,
            "duration_sec": round(extract_result.duration_sec, 3),
            "notes": extract_result.notes,
            "chars": len(extract_result.text),
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

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    if bool(opts.get("strict_quality_gate", False)):
        enforce_quality_gate_or_raise(report)

    return extracted_path, masked_path, report_path, report


def extract_result_text_for_preview(infile: str, opts: dict[str, Any]) -> str:
    opts = normalize_opts(opts)
    return extract_document(infile, engine=opts.get("extract_engine", "auto")).text


def mask_text_for_preview(extracted_text: str, opts: dict[str, Any]) -> str:
    opts = normalize_opts(opts)
    masked, _counts, _matches, _meta = process_masking_queue(extracted_text, opts)
    masked, _llm_meta = llm_refine_masking(masked, opts, {})
    return masked


def run_cli_mode(argv: list[str]) -> bool:
    if len(argv) <= 1:
        return False

    files = [a for a in argv[1:] if os.path.isfile(a)]
    if not files:
        return False

    opts = normalize_opts(None)

    opts["log_callback"] = print

    print("[CLI 모드] 파일 처리 시작")
    for fp in files:
        try:
            extracted_path, masked_path, report_path, report = process_file(fp, outdir=None, opts=opts)
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
        except Exception:
            print("[실패] 문서 1건: PROCESS_FAILED")

    return True


def main() -> None:
    run_cli_mode(sys.argv)


if __name__ == "__main__":
    main()
