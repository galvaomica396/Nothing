#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF native redaction and manual-correction engine extracted from
document_masker_ocr_gui.

Behavior-preserving move of native PDF redaction, post-verification, and
manual redaction/correction application. Pure code movement.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        for i, (s, e) in enumerate(index.spans):
            if e <= idx:
                continue
            if s >= match_end:
                break
            if first_word is None:
                first_word = i
            last_word = i

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
                    rects = page.search_for(variant)
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
    matches: list[RedactionMatch],
    exclude_boxes: list[ManualRedactionBox] | None = None,
    display_mode: str = "black",
    transform_state: TransformState | None = None,
) -> dict[str, Any]:
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
    finally:
        doc.close()

    # 사후 무결성 검증: 결과 PDF에서 잔존 문자열 재검색(exact/compact + 퍼지 R1).
    residual_hits, residual_fuzzy_hits, residual_terms, residual_review_items = _verify_redaction_output(
        fitz, output_pdf_path, search_terms, display_mode
    )
    review_items.extend(residual_review_items)

    missing_targets_count = max(len(search_terms) - len(terms_hit), 0)
    verified = residual_hits == 0 and residual_fuzzy_hits == 0 and missing_targets_count == 0
    status = "applied" if verified else "failed"
    reason = (
        "레닥션 적용 및 잔존 0건 검증 완료"
        if verified
        else "PDF 검색/레닥션 누락 또는 결과 PDF 잔존 항목이 있어 수동 검토가 필요합니다"
    )

    return {
        "enabled": True,
        "status": status,
        "output_file": output_pdf_path,
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


def _normalized_pdf_save_target(_source_pdf_path: str, output_pdf_path: str) -> tuple[str, None]:
    return _fresh_pdf_output_path(output_pdf_path), None


def _finish_pdf_save(temp_path: str, _final_path: None) -> str:
    return temp_path


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
    doc = fitz.open(source_pdf_path)
    try:
        applied = 0
        grouped: dict[int, list[tuple[float, float, float, float]]] = {}
        for box in boxes:
            if box.mode != "mask":
                continue
            grouped.setdefault(box.page_index, []).append(box.rect)

        label_overlays: dict[int, list[tuple[Any, str]]] = {}
        for page_index, rects in grouped.items():
            if page_index < 0 or page_index >= doc.page_count:
                continue
            page = doc[page_index]
            for rect in rects:
                x0, y0, x1, y1 = rect
                norm = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                if norm.width < 2 or norm.height < 2:
                    continue
                label = add_redaction_annotation(page, norm, normalize_redaction_tag(box.tag), display_mode)
                if label:
                    label_overlays.setdefault(page_index, []).append((fitz.Rect(norm), label))
                applied += 1

        if applied == 0:
            raise RuntimeError("유효한 수동 마스킹 영역이 없어 저장하지 못했습니다.")

        for page_index in grouped:
            if 0 <= page_index < doc.page_count:
                doc[page_index].apply_redactions()
                for rect, label in label_overlays.get(page_index, []):
                    _insert_pdf_label(doc[page_index], rect, label)

        doc.save(save_path, garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    return {
        "status": "applied",
        "output_file": _finish_pdf_save(save_path, replace_target),
        "boxes_applied": applied,
        "pages_touched": sorted(grouped.keys()),
        "display_mode": display_mode,
        # 이 경로는 마스킹 박스만 적용한다(box.mode != "mask"는 건너뜀). 마스킹은
        # 노출을 줄이기만 하므로 재검증이 필요치 않다.
        "requires_revalidation": False,
        "raw_value_saved": False,
    }


def apply_manual_pdf_corrections(
    source_pdf_path: str,
    original_pdf_path: str,
    output_pdf_path: str,
    boxes: list[ManualCorrectionBox],
    display_mode: str = "black",
) -> dict[str, Any]:
    try:
        import fitz  # type: ignore
    except Exception as e:
        raise RuntimeError(f"PyMuPDF 미설치로 수동 보정을 수행할 수 없습니다: {e}")

    if not boxes:
        raise RuntimeError("저장할 수동 보정 영역이 없습니다.")

    display_mode = normalize_display_mode(display_mode)
    save_path, replace_target = _normalized_pdf_save_target(source_pdf_path, output_pdf_path)
    doc = fitz.open(source_pdf_path)
    original_doc = fitz.open(original_pdf_path)
    grouped: dict[int, list[tuple[int, ManualCorrectionBox]]] = {}
    mask_applied = 0
    unmask_applied = 0
    skipped_boxes = 0
    warnings: list[str] = []
    seen_boxes: set[tuple[int, str, int, int, int, int]] = set()
    try:
        for idx, box in enumerate(boxes):
            x0, y0, x1, y1 = box.rect
            rect_key = (
                box.page_index,
                box.action,
                round(min(x0, x1) * 1000),
                round(min(y0, y1) * 1000),
                round(max(x0, x1) * 1000),
                round(max(y0, y1) * 1000),
            )
            if rect_key in seen_boxes:
                skipped_boxes += 1
                warnings.append(f"box {idx} skipped: duplicate manual box")
                continue
            seen_boxes.add(rect_key)
            grouped.setdefault(box.page_index, []).append((idx, box))

        for page_index, page_boxes in grouped.items():
            if page_index < 0 or page_index >= doc.page_count:
                skipped_boxes += len(page_boxes)
                warnings.append(f"page {page_index} skipped: page out of range")
                continue
            page = doc[page_index]
            page_rect = page.rect
            pending_mask_annots = 0
            pending_labels: list[tuple[Any, str]] = []

            def flush_masks() -> None:
                nonlocal pending_mask_annots, mask_applied, skipped_boxes, pending_labels
                if not pending_mask_annots:
                    return
                try:
                    page.apply_redactions()
                    for label_rect, label in pending_labels:
                        _insert_pdf_label(page, label_rect, label)
                    mask_applied += pending_mask_annots
                except Exception as exc:
                    skipped_boxes += pending_mask_annots
                    warnings.append(f"page {page_index} redactions skipped: {exc}")
                finally:
                    pending_mask_annots = 0
                    pending_labels = []

            for box_idx, box in page_boxes:
                x0, y0, x1, y1 = box.rect
                rect = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)) & page_rect
                if rect.width < 2 or rect.height < 2:
                    skipped_boxes += 1
                    warnings.append(f"box {box_idx} skipped: clipped rectangle too small")
                    continue
                if box.action != "unmask":
                    label = add_redaction_annotation(page, rect, normalize_redaction_tag(box.tag), display_mode)
                    if label:
                        pending_labels.append((fitz.Rect(rect), label))
                    pending_mask_annots += 1
                    continue

                flush_masks()
                if page_index >= original_doc.page_count:
                    page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
                    warnings.append(f"box {box_idx}: original page missing, restored with white fill")
                    unmask_applied += 1
                    continue
                original_page = original_doc[page_index]
                source_clip = rect & original_page.rect
                if source_clip.width < 2 or source_clip.height < 2:
                    skipped_boxes += 1
                    warnings.append(f"box {box_idx} skipped: source rectangle outside original page")
                    continue
                target_rect = fitz.Rect(rect.x0, rect.y0, rect.x0 + source_clip.width, rect.y0 + source_clip.height)
                try:
                    pix = original_page.get_pixmap(clip=source_clip, matrix=fitz.Matrix(2.5, 2.5), alpha=False)
                    page.insert_image(target_rect, pixmap=pix, overlay=True)
                except Exception as exc:
                    try:
                        page.show_pdf_page(target_rect, original_doc, page_index, clip=source_clip, overlay=True)
                    except Exception as fallback_exc:
                        page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
                        warnings.append(f"box {box_idx}: restore failed, fallback white fill: {fallback_exc}")
                    warnings.append(f"box {box_idx}: pixmap restore fallback: {exc}")
                unmask_applied += 1

            flush_masks()

        status = "applied"
        if mask_applied == 0 and unmask_applied == 0:
            status = "no_effect"
            warnings.append("no valid manual boxes; unchanged preview was saved")

        doc.save(save_path, garbage=4, deflate=True, clean=True)
    finally:
        original_doc.close()
        doc.close()
    return {
        "status": status,
        "output_file": _finish_pdf_save(save_path, replace_target),
        "mask_boxes_applied": mask_applied,
        "unmask_boxes_applied": unmask_applied,
        "pages_touched": sorted(grouped.keys()),
        "skipped_boxes": skipped_boxes,
        "warnings": warnings,
        "display_mode": display_mode,
        # 복원(unmask)만 원본을 되살려 위험을 늘리므로 재검증 대상이다. 마스킹 추가는
        # 노출을 줄이기만 하므로 기존 안전 리포트를 무효화하지 않는다.
        "requires_revalidation": unmask_applied > 0,
        "raw_value_saved": False,
    }


def apply_manual_edits_with_restore(
    source_pdf_path: str,
    output_pdf_path: str,
    auto_matches: list[RedactionMatch],
    manual_boxes: list[ManualRedactionBox],
    display_mode: str = "black",
) -> dict[str, Any]:
    restore_boxes = [b for b in manual_boxes if b.mode == "restore"]
    mask_boxes = [b for b in manual_boxes if b.mode == "mask"]

    with tempfile.TemporaryDirectory(prefix="masker_manual_restore_") as temp_dir:
        temp_auto_pdf = os.path.join(temp_dir, "auto_filtered.pdf")
        auto_result = redact_pdf_native(source_pdf_path, temp_auto_pdf, auto_matches, exclude_boxes=restore_boxes)

        if mask_boxes:
            manual_result = apply_manual_redactions(temp_auto_pdf, output_pdf_path, mask_boxes, display_mode=display_mode)
        else:
            shutil.copyfile(temp_auto_pdf, output_pdf_path)
            manual_result = {
                "status": "skipped",
                "output_file": output_pdf_path,
                "boxes_applied": 0,
                "pages_touched": [],
            }
        auto_result = {**auto_result, "output_file": None}

    return {
        "status": "applied",
        "output_file": output_pdf_path,
        "restore_boxes": len(restore_boxes),
        "mask_boxes": len(mask_boxes),
        "auto_redaction": auto_result,
        "manual_redaction": manual_result,
    }
