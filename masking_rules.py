#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PII detection rules extracted from document_masker_ocr_gui.

Behavior-preserving move of the mask-token helpers, PII regex patterns,
redaction-match primitives, review-item builders, region-data loading, and
the substitution helpers. Pure code movement; no rule or regex was altered.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

from masking_context import DocumentContext, find_masking_context
from privacy_false_positive import has_masked_token
from privacy_spans import source_for_tag
from pdf_redaction_rendering import (
    MASK_TOKEN_LABELS,
    display_token,
    insert_pdf_label,
    korean_pdf_font_file,
)


P = ParamSpec("P")
R = TypeVar("R")


class _SourceOffsetTracker:
    __slots__ = ("boundaries", "source_text")

    def __init__(self, text: str) -> None:
        self.source_text = text
        self.boundaries = list(range(len(text) + 1))

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        if start < 0 or end <= start or end >= len(self.boundaries):
            return start, end
        return self.boundaries[start], self.boundaries[end]

    def apply_edits(self, edits: list[tuple[int, int, int]]) -> None:
        for start, end, replacement_length in reversed(edits):
            source_start = self.boundaries[start]
            source_end = self.boundaries[end]
            replacement_boundaries = [source_start] * replacement_length + [source_end]
            self.boundaries[start:end + 1] = replacement_boundaries


_SOURCE_OFFSET_TRACKER: ContextVar[_SourceOffsetTracker | None] = ContextVar(
    "source_offset_tracker",
    default=None,
)


def tracked_masking_offsets(
    function: Callable[Concatenate[str, P], R],
) -> Callable[Concatenate[str, P], R]:
    @wraps(function)
    def wrapped(text: str, *args: P.args, **kwargs: P.kwargs) -> R:
        if _SOURCE_OFFSET_TRACKER.get() is not None:
            return function(text, *args, **kwargs)
        token = _SOURCE_OFFSET_TRACKER.set(_SourceOffsetTracker(text))
        try:
            return function(text, *args, **kwargs)
        finally:
            _SOURCE_OFFSET_TRACKER.reset(token)

    return wrapped


def _tracked_sub(
    pattern: re.Pattern[str],
    replacement: Callable[[re.Match[str]], str],
    text: str,
    base_offset: int = 0,
) -> str:
    edits: list[tuple[int, int, int]] = []

    def replace_and_record(match: re.Match[str]) -> str:
        value = replacement(match)
        if value != match.group(0):
            edits.append((base_offset + match.start(), base_offset + match.end(), len(value)))
        return value

    result = pattern.sub(replace_and_record, text)
    tracker = _SOURCE_OFFSET_TRACKER.get()
    if tracker is not None and edits:
        tracker.apply_edits(edits)
    return result


def _tracked_replace(text: str, old: str, new: str) -> str:
    return _tracked_sub(re.compile(re.escape(old)), lambda _match: new, text)


def current_masking_source_boundaries() -> tuple[int, ...]:
    tracker = _SOURCE_OFFSET_TRACKER.get()
    return tuple(tracker.boundaries) if tracker is not None else ()


def _mask_token(tag: str) -> str:
    return display_token(tag, "label_ko")


def _convert_mask_tokens_to_korean(text: str) -> str:
    for tag, label in MASK_TOKEN_LABELS.items():
        text = _tracked_replace(text, f"[{tag}]", f"[{label}]")
    return text


# OCR 변형 구분자 대응(C-1):
# - 유니코드 대시류(U+2010–U+2015)·마이너스 기호(U+2212)·전각 하이픈(U+FF0D)을 하이픈과 동일 취급
# - 점(.)·공백을 구분자로 허용
# - 자간(문자 사이 공백) 삽입 변형 대응
# ASCII 하이픈은 문자클래스에서 범위 연산으로 오인되므로 escape 상태로 보관한다.
_DASH_CHARS = "‐‑‒–—―−－\\-"
# 그룹 사이 구분자: 대시류/점, 또는 공백. (자간 공백은 각 숫자 사이 \s* 로도 흡수)
_ID_SEP = rf"(?:\s*[{_DASH_CHARS}.]\s*|\s+)?"
# 전화번호 세그먼트 구분자: 대시류/점/공백 중 하나(선택)
_PHONE_SEP = rf"[{_DASH_CHARS}.\s]?"


def _spaced_digits(n: int) -> str:
    # 자간 삽입 대응: 각 숫자 사이 선택적 공백을 허용해 n자리 숫자열을 매칭
    return r"\d" + r"(?:\s*\d)" * (n - 1)


# 주민등록번호: 뒤 첫자리 1~4. 대시류/점/공백/자간 변형 허용.
RRN_PAT = re.compile(rf"(?<!\d){_spaced_digits(6)}{_ID_SEP}[1-4](?:\s*\d){{6}}(?!\d)")
# 외국인등록번호: 주민번호와 동일한 자리수이지만 뒤 첫자리가 5~8
FOREIGN_REG_PAT = re.compile(rf"(?<!\d){_spaced_digits(6)}{_ID_SEP}[5-8](?:\s*\d){{6}}(?!\d)")
# 사업자등록번호: 123-45-67890 (하이픈/대시류/점/공백 생략 허용)
BUSINESS_REG_PAT = re.compile(rf"(?<!\d)\d{{3}}{_ID_SEP}\d{{2}}{_ID_SEP}\d{{5}}(?!\d)")
# 카드/여권/계좌는 전화번호보다 먼저 처리해 숫자 조각 오탐을 막는다.
CARD_PAT = re.compile(rf"(?<!\d)(?:\d{{4}}{_ID_SEP}){{3}}\d{{4}}(?!\d)")
PASSPORT_PAT = re.compile(r"(?<![A-Za-z0-9])(?:[MSRODmsrod]\d{8}|[A-Za-z]{2}\d{7})(?![A-Za-z0-9])")
ACCOUNT_CONTEXT_PAT = re.compile(
    rf"(?P<label>\b(?:계좌번호|입금계좌|환급계좌|납부계좌|은행계좌)\b\s*[:：]?\s*)"
    rf"(?P<value>(?:\d{{2,6}}{_ID_SEP}){{2,5}}\d{{2,6}})(?!\d)"
)
# 전화번호: 대시류/점/공백 구분자, 지역번호 괄호 (02) 등 OCR 변형 허용.
MOBILE_PAT = re.compile(
    rf"(?<![\d{_DASH_CHARS}])(?:\+?82{_PHONE_SEP})?0?1[016789]{_PHONE_SEP}\d{{3,4}}{_PHONE_SEP}\d{{4}}(?![\d{_DASH_CHARS}])"
)
LANDLINE_PAT = re.compile(
    rf"(?<![\d{_DASH_CHARS}])\(?(?:0(?:2|[3-6][1-5]|70|50\d))\)?{_PHONE_SEP}\d{{3,4}}{_PHONE_SEP}\d{{4}}(?![\d{_DASH_CHARS}])"
)
REP_PHONE_PAT = re.compile(rf"(?<![\d{_DASH_CHARS}])1[5-8]\d{{2}}{_PHONE_SEP}\d{{4}}(?![\d{_DASH_CHARS}])")
PHONE_VALUE_BODY = (
    rf"(?:\+?82{_PHONE_SEP})?(?:0?1[016789]|\(?0(?:2|[3-6][1-5]|70|50\d)\)?){_PHONE_SEP}\d{{3,4}}{_PHONE_SEP}\d{{4}}"
    rf"|1[5-8]\d{{2}}{_PHONE_SEP}\d{{4}}"
)
PHONE_VALUE_PAT = re.compile(PHONE_VALUE_BODY)
PHONE_LABEL_PAT = re.compile(
    rf"(?P<label>(?:전화번호|대표전화|휴대전화|휴대폰|전화|연락처|팩스|FAX)\s*[:：]?\s*)"
    rf"(?P<value>(?:{PHONE_VALUE_BODY})(?:\s*[,;/·]?\s*(?:{PHONE_VALUE_BODY}))*)",
    re.IGNORECASE,
)
PHONE_PATS = [PHONE_LABEL_PAT, MOBILE_PAT, LANDLINE_PAT, REP_PHONE_PAT]

NAME_CONTEXT_PAT = re.compile(
    r"(?P<label>\b(?:이름|성명|민원인|신청인|담당자|보호자|대표자|제출인)\b(?:\s*[:：]\s*|\s+))(?P<name>(?!(?:제도|정보|접수|신청|처리|개선|등록|변경|작성|관리|담당|요청|서식|절차|안내|민원|직통|시스템|계획|기준|업무)(?=\s|[:：]|$))[가-힣]{2,4})"
)

ADDRESS_CONTEXT_PAT = re.compile(
    r"(?P<label>\b주소\b\s*[:：]\s*)(?P<addr>[^\n,;]+)"
)

SEOUL_GU_PAT = re.compile(
    r"(?:서울특별시|서울시|서울)\s*(?:종로구|중구|용산구|성동구|광진구|동대문구|중랑구|성북구|강북구|도봉구|노원구|은평구|서대문구|마포구|양천구|강서구|구로구|금천구|영등포구|동작구|관악구|서초구|강남구|송파구|강동구)"
)
SEOUL_GU_ONLY_PAT = re.compile(
    r"(?<![가-힣])(?:종로구|중구|용산구|성동구|광진구|동대문구|중랑구|성북구|강북구|도봉구|노원구|은평구|서대문구|마포구|양천구|강서구|구로구|금천구|영등포구|동작구|관악구|서초구|강남구|송파구|강동구)(?![가-힣])"
)
SEOUL_GU_OFFICE_PAT = re.compile(
    r"(?<![가-힣])(?:종로구|중구|용산구|성동구|광진구|동대문구|중랑구|성북구|강북구|도봉구|노원구|은평구|서대문구|마포구|양천구|강서구|구로구|금천구|영등포구|동작구|관악구|서초구|강남구|송파구|강동구)(?=청장|청)"
)

PLACE_PATS = [
    SEOUL_GU_PAT,
    SEOUL_GU_ONLY_PAT,
    SEOUL_GU_OFFICE_PAT,
    re.compile(r"(서울특별시|서울시|서울)\s*양천구"),
    re.compile(r"양천구"),
    re.compile(r"목\s*(?:[1-5]\s*)?동"),
    re.compile(r"신정\s*(?:(?:1|2|3|4|6|7)\s*)?동"),
    re.compile(r"신월\s*(?:[1-7]\s*)?동"),
]

# 지번은 '번지' 접미가 있을 때만 마스킹해 사건번호/연도 숫자 오탐을 줄임
LOT_NO_PAT = re.compile(r"(?<!\d)\d{1,4}(?:-\d{1,4})?\s*번지(?!\d)")
ROAD_NO_PAT = re.compile(r"[가-힣0-9·\-\s]{1,20}(?:로|길)\s*\d{1,4}(?:-\d{1,4})?")

LEGAL_PARTY_PAT = re.compile(
    r"(?P<label>\b(?:원고|피고|신청인|피신청인|상대방|항고인|피항고인|청구인|피청구인|채권자|채무자|고소인|피고소인|진정인|피진정인)\b\s*[:：]\s*)(?P<value>[^\n,;()]{2,40})"
)
LEGAL_PARTY_INLINE_NAME_PAT = re.compile(
    r"(?P<label>\b(?:원고|피고|신청인|피신청인|상대방|항고인|피항고인|청구인|피청구인|채권자|채무자|고소인|피고소인|진정인|피진정인)\b\s+)"
    r"(?P<value>(?!(?:측|측은|측이|대리인|소송대리인|법무법인|법률사무소)(?=\s|$))(?:[가-힣]{2,4}|[가-힣](?:\s*[가-힣]){1,3}))"
    r"(?=[^가-힣]|$)"
)
# 법률문서 헤더 보강: "피고 소송대리인 홍길동" / "원고 대리인 홍 길 동" 등
LEGAL_PARTY_REP_INLINE_PAT = re.compile(
    r"(?P<label>\b(?:원고|피고|신청인|피신청인|항고인|피항고인|청구인|피청구인|채권자|채무자)\b\s*"
    r"(?:소송\s*대리인|법률\s*대리인|대리인)\b\s*(?:[:：]\s*|\s+))"
    r"(?P<value>(?!(?:법무법인|법률사무소|담당변호사|변호사)(?=\s|$))(?:[가-힣]{2,4}|[가-힣](?:\s*[가-힣]){1,3}))"
    r"(?=[^가-힣]|$)"
)
COMPANY_LABEL_PAT = re.compile(
    r"(?P<label>\b(?:회사명|법인명|상호|업체명|기관명)\b(?:\s*[:：]\s*|\s+))(?P<value>[^\n]{2,80})"
)
COMPANY_INLINE_PAT = re.compile(
    r"(?P<value>(?:주식회사|유한회사|합자회사|합명회사|의료법인|학교법인|사회복지법인|재단법인|사단법인)\s*[A-Za-z0-9가-힣&.,()\- ]{1,60}|[(（]주[)）]\s*[A-Za-z0-9가-힣&.,()\- ]{1,60})"
)
COURT_PAT = re.compile(
    r"(?<![가-힣])(?P<value>(?:대법원|특허법원|회생법원|행정법원|가정법원|고등법원|[가-힣]{2,12}(?:지방법원|고등법원|가정법원|행정법원|회생법원|지원))(?:\s*[가-힣0-9]{1,12}(?:지원|재판부))?)(?![가-힣])"
)
# official 프로파일 법원명 자간(문자 사이 공백) 변형 보강(M-4): '서 울 행 정 법 원'
_LOOSE_COURT_BODY = (
    r"(?:"
    r"대\s*법\s*원|헌\s*법\s*재\s*판\s*소|특\s*허\s*법\s*원|회\s*생\s*법\s*원|행\s*정\s*법\s*원|가\s*정\s*법\s*원|고\s*등\s*법\s*원|"
    r"(?:[가-힣]\s*){2,12}(?:지\s*방\s*법\s*원|고\s*등\s*법\s*원|가\s*정\s*법\s*원|행\s*정\s*법\s*원|회\s*생\s*법\s*원|지\s*원)"
    r")"
)
COURT_SPACED_PAT = re.compile(rf"(?<![가-힣])(?P<value>{_LOOSE_COURT_BODY})(?![가-힣])")
CASE_TITLE_LABEL_PAT = re.compile(
    r"(?P<label>\b(?:사건명|사건제목|사건명칭)\b\s*[:：]\s*)(?P<value>[^\n]+)"
)
CASE_TITLE_GENERIC_LABEL_PAT = re.compile(
    r"(?P<label>\b제목\b\s*[:：]\s*)(?P<value>[^\n]+)"
)
CASE_LINE_TITLE_PAT = re.compile(
    r"(?m)^(?P<number>(?:19|20)\d{2}\s*[가-힣]{1,4}\s*\d{1,10})\s+(?P<title>[^\n]{2,100})$"
)
CASE_NUMBER_PAT = re.compile(r"(?<![\w\]])(?P<value>(?:19|20)\d{2}\s*(?!구상)[가-힣]{1,4}\s*\d{1,10})(?![\w\[])")
CASE_NUMBER_CONTEXT_PAT = re.compile(
    # 라벨/문맥 게이트는 유지하되 사건번호 부호부(가단 등)의 자간 변형 허용(M-4)
    r"(?P<label>\b(?:사건번호|사건|당해\s*사건|이\s*사건|본건)\b\s*[:：]?\s*)"
    r"(?P<value>(?:19|20)\d{2}\s*(?!구\s*상)(?:[가-힣]\s*){1,4}\d{1,10})"
)
LAW_FIRM_LABEL_PAT = re.compile(
    r"(?P<label>\b(?:법무법인|법률사무소|변호사사무실|소속법무법인)\b\s*[:：]\s*)(?P<value>[^\n,;]{2,60})"
)
LAW_FIRM_INLINE_PAT = re.compile(
    r"(?P<label>\b(?:법무법인|법률사무소|변호사사무실)\b\s+)(?P<value>[A-Za-z0-9가-힣&.()\-]{2,30})(?=$|[\s,;])"
)
ATTORNEY_PAT = re.compile(
    r"(?P<label>\b(?:변호사|담당변호사|소송대리인|법률대리인|선임변호사)\b\s*[:：]\s*)(?P<value>[가-힣A-Za-z ]{2,30})"
)
# 한국 행정공문 결재선/기안 라벨
APPROVAL_LINE_PAT = re.compile(
    r"(?P<label>\b(?:기안자|기안|검토자|검토|협조자|협조|결재권자|최종결재권자|결재자|전결권자|대결권자|전결|대결|결재)\b(?:\s*[:：]\s*|\s+))(?P<value>(?!(?:지침|절차|기준|양식|규정|문서|작성|검토|결재|업무|계획|방법|지시|공문|시행|요청|사유|완료|안내|결과|권한|하여|위하여|대하여)(?=\s|$))[가-힣A-Za-z]{2,20})"
)
# 공직/사기업 직책·직급 + 이름 inline (예: "주무관 홍길동", "건축8급 김철수", "대리 박영수")
OFFICIAL_ROLE_NAME_PAT = re.compile(
    r"(?P<label>\b(?:"
    r"주무관|사무관|서기관|부이사관|이사관|관리관|"
    r"지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|담당자|담당|계장|행정팀장|민원팀장|복지팀장|건축과장|총무과장|"
    r"부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|과장|차장|부장|본부장|사업부장|파트장|매니저|"
    r"이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO"
    r")\b(?:\s*[:：]\s*|\s+))"
    r"(?P<value>(?!(?:지침|절차|기준|양식|규정|문서|작성|검토|결재|업무|계획|방법|지시|공문|시행|요청|사유|완료|안내|결과|권한|하여|위하여|대하여)(?=\s|$))(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3}))"
)
# 공문 지역/관할/소재지 라벨
REGION_CONTEXT_PAT = re.compile(
    r"(?P<label>\b(?:소재지|관할지역|관할구역|관할|해당지역|위치|지역|행정구역)\b\s*[:：]\s*)(?P<value>[^\n,;]{2,80})"
)
# 공문 헤더/결재 메타데이터 라벨
DOC_META_PAT = re.compile(
    r"(?P<label>(?:^|[\r\n])\s*(?:"
    r"수신|참조|경유|"
    r"문서\s*번호|문서번호|공문\s*번호|공문번호|방침\s*번호|방침번호|"
    r"시행\s*문서번호|시행문서번호|시행\s*번호|시행번호|시행|"
    r"접수\s*번호|접수번호|접수|"
    r"결재\s*일자|결재일자|작성\s*일자|작성일자|시행\s*일자|시행일자|"
    r"공개\s*여부|공개여부|공개\s*구분|공개구분|공개\s*등급|공개등급|"
    r"우편\s*번호|우편번호|우\.?|"
    r"전송|팩스|FAX|"
    r""
    r"홈페이지|누리집|웹사이트|"
    r"담당\s*부서|담당부서|부서|"
    r"담당자(?:\s*\(\s*직통(?:전화)?\s*\))?|담당자\s*직통(?:전화)?"
    r")\s*(?:[:：]\s*|\s+))(?P<value>[^\n]{1,140})"
)
# 본문 내 공문 참조표현 확장:
# - "건축과-1526(2026.4.22.)호", "건축과-1526호"
# - "건축과 제1526호", "건축과-1526호(2026.4.22.)", "건축과-1526('26.4.22.)호"
DOC_REF_INLINE_PAT = re.compile(
    r"(?P<value>(?:"
    r"[가-힣A-Za-z0-9]+과\s*[-–]\s*\d{1,6}(?:\s*\(\s*(?:(?:19|20)\d{2}|['’](?:\d{2}))\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?\s*호(?:\s*\(\s*(?:(?:19|20)\d{2}|['’](?:\d{2}))\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?"
    r"|[가-힣A-Za-z0-9]+과\s*제\s*\d{1,6}\s*호(?:\s*\(\s*(?:(?:19|20)\d{2}|['’](?:\d{2}))\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?"
    r"))"
)

# 하단 메타 '시행 ○○과-1234' 전용 보강 (띄어쓰기/OCR 변형 포함)
SIHAENG_DOCNO_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)시\s*행(?:\s+|\s*[:：]\s*))(?P<value>[^\n]{0,60}?"
    r"(?:[가-힣A-Za-z0-9]{1,24}(?:과|팀|국|실|센터|사업소)?\s*[-–—]\s*\d{1,8}(?:-\d{1,8})?)"
    r"(?:\s*\(\s*(?:(?:19|20)\d{2}|['’]?\d{2})\.\s*\d{1,2}\.\s*\d{1,2}\.?\s*\))?)"
)

# 이메일
EMAIL_PAT = re.compile(r"(?P<value>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")

# 공개등급 단독 표기 (예: 부분공개(5), 비공개(6), 공개)
PUBLIC_LEVEL_PAT = re.compile(r"(?P<value>(?:부분공개\s*\(\s*\d{1,2}\s*\)|비공개\s*\(\s*\d{1,2}\s*\)|전부공개))")

# 부서/팀 + 내선 번호 (예: 급수운영2팀-8464)
TEAM_EXT_PAT = re.compile(
    r"(?P<value>(?:[가-힣A-Za-z0-9]{1,20}(?:팀|과|국|실|센터|사업소))\s*[-:]\s*\d{3,5})"
)

# 부서명+직책+이름 결합형(공백 없거나 약한 구분) 보강
DEPT_ROLE_NAME_COMPACT_PAT = re.compile(
    r"(?P<label>(?:[가-힣A-Za-z0-9]{1,20}(?:팀|과|국|실|센터|사업소|본부|사업부))\s*"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO)\s*)"
    r"(?P<value>(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3}))"
)

# 직책이 부서명에 붙은 결합형 + 이름 (예: 급수관리팀장 이한수)
OFFICIAL_COMBINED_ROLE_NAME_PAT = re.compile(
    r"(?P<label>(?:[가-힣A-Za-z0-9]{1,24}(?:"
    r"주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO))\s*)"
    r"(?P<value>(?:[가-힣]{2,4}?|[가-힣]\s*[가-힣]{1,3}?))"
    r"(?=(?:연락처|전화번호|대표전화|휴대전화|휴대폰|전화|팩스|FAX|[\s\d:：,;/·]|$))"
)
ACTING_APPROVER_NAME_PAT = re.compile(
    r"(?P<label>代\s*)"
    r"(?P<value>[가-힣]{2,4}?)"
    r"(?=(?:친|협조자|수신|시행|접수|[\s\d:：,;/·]|$))"
)

# 결재선 표 전용: 직책행 다음 줄 이름행(2~4자)을 결재선으로 간주
APPROVAL_TABLE_LINE_PAT = re.compile(
    r"(?m)^(?P<label>(?:"
    r"주무관|사무관|서기관|부이사관|이사관|관리관|"
    r"지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9]\s*[급긍금급]|"
    r"[가-힣A-Za-z0-9]{1,20}(?:팀장|과장|국장|실장|센터장|본부장|사업부장|부시장|시장|부구청장|구청장)"
    r"))\s*$\n^(?P<value>[가-힣]{2,4})\s*$"
)

# 결재선 표 전용: 한 줄에 직책+이름이 여러 쌍으로 붙는 OCR 케이스
APPROVAL_TABLE_INLINE_MULTI_PAT = re.compile(
    r"(?P<value>"
    r"(?:"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])"
    r"\s*(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3})"
    r")"
    r"(?:\s*(?:\||/|,)\s*|\s+)"
    r"(?:"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])"
    r"\s*(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3})"
    r")"
    r"(?:\s*(?:\||/|,)\s*|\s+)"
    r"?(?:"
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기|지방시설서기보|서기|서기보|주사|주사보|사무주사|사무주사보|"
    r"팀장|과장|국장|실장|센터장|계장|부시장|시장|부구청장|구청장|"
    r"인턴|사원|주임|선임|책임|수석|연구원|선임연구원|책임연구원|수석연구원|"
    r"대리|차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표|대표이사|CEO|CTO|CFO|COO|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])"
    r"\s*(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3})"
    r")*"
    r")"
)

# 결재선 OCR 보정 전용: 직렬+급수 표기의 띄어쓰기/오인식 허용 (결재선 단계에서만 사용)
APPROVAL_GRADE_OCR_PAT = re.compile(
    r"(?m)^\s*(?P<label>(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])(?:\s*[:：]\s*|\s+)"
    r"(?P<value>(?:[가-힣]{2,4}|[가-힣]\s*[가-힣]{1,3}))\s*$"
)

# 결재선 결재구분 토큰(전결/대결/代決) 보강
# 본문 일반문장 오탐 방지를 위해 결재선에서 자주 나오는 짧은 셀/구분자 맥락만 허용
APPROVAL_FLOW_CONTEXT_PAT = re.compile(
    r"(?P<label>\b(?:결재\s*구분|결재구분|결재\s*방식|결재방식|결재\s*방법|결재방법)\b\s*[:：]?\s*)"
    r"(?P<value>(?:전\s*결|대\s*결|代\s*決|專\s*決|代))(?![가-힣A-Za-z0-9])",
    re.IGNORECASE,
)
APPROVAL_FLOW_LINE_PAT = re.compile(
    r"(?mi)^\s*(?P<value>(?:전\s*결|대\s*결|代\s*決|專\s*決|代))\s*$"
)

# 결재선 하단 최종결재자 누락 보강: "04/30 홍길동" 형태에서 이름 마스킹
APPROVAL_DATE_NAME_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)(?:\d{1,2}\s*/\s*\d{1,2}|(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.?)(?:\s*[:：]?\s*))(?P<value>[가-힣]{2,4})(?=\s|$)"
)

# 결재선 직책+일자+최종결재자 결합형 보강 (예: 공공주택과장 04/30 하대근)
APPROVAL_ROLE_DATE_NAME_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)(?:[가-힣A-Za-z0-9]{1,24}(?:팀장|과장|국장|실장|센터장|본부장|사업부장|부시장|시장|부구청장|구청장)\s*)"
    r"(?:\d{1,2}\s*/\s*\d{1,2}|(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.?)(?:\s*[:：]?\s*))(?P<value>[가-힣]{2,4})(?=\s|$)"
)

# 결재선 직책 뒤 괄호 이름형 보강 (예: 급수운영과장 (한현숙), 팀장(안승현))
APPROVAL_ROLE_PAREN_NAME_PAT = re.compile(
    r"(?mi)(?P<label>(?:^|\s)(?:[가-힣A-Za-z0-9]{0,24}(?:주무관|팀장|과장|국장|실장|센터장|본부장|사업부장|부시장|시장|부구청장|구청장))"
    r"(?:\s*(?:\d{1,2}\s*/\s*\d{1,2}|(?:19|20)\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.?))?\s*[\(\[]\s*)(?P<value>[가-힣]{2,6})(?=\s*[\)\]])"
)

# 메타 라벨 OCR 보정 전용: 라벨 변형(띄어쓰기/영문 대소문자/FAX) 허용
DOC_META_OCR_PAT = re.compile(
    r"(?P<label>(?:^|[\r\n])\s*(?:"
    r"문서\s*번호|공문\s*번호|방침\s*번호|"
    r"시행\s*문서\s*번호|시행\s*번호|시행|"
    r"접수\s*번호|접수|"
    r"결재\s*일자|작성\s*일자|시행\s*일자|"
    r"공개\s*여부|공개\s*구분|공개\s*등급|"
    r"우\.?|우편\s*번호|"
    r"전\s*송|팩\s*스|fax|FAX|"
    r""
    r"담당\s*부서|담당자\s*직통(?:전화)?"
    r")\s*(?:[:：]\s*|\s+))(?P<value>[^\n]{1,140})"
)


def _count_up(report: dict[str, int], key: str, n: int = 1) -> None:
    report[key] = report.get(key, 0) + n


# 2자 당사자 실명 전역 치환 시 붙는 한국어 조사(길이순 정렬로 긴 조사 우선)
_KOREAN_JOSA_ALT = (
    r"(?:이라고|라고|이라|께서|에게|한테|더러|보고|"
    r"이|가|은|는|을|를|와|과|의|에|께|도|만|씨|군|양)"
)


def _replace_two_char_party_name(
    name: str,
    text: str,
    report: dict[str, int] | None = None,
    matches: list[RedactionMatch] | None = None,
) -> tuple[str, int]:
    """2자 당사자 실명을 한글 경계 조건으로만 전역 치환(H-5).

    - 앞 문자가 한글이면 치환 안 함(다른 단어의 일부)
    - 뒤가 한글이 아니거나(공백/문장부호/문말) 조사가 붙는 경우에만 치환
    이렇게 하면 '이가방'(이가+방)은 보존되고 '이가는/이가 대표'는 마스킹된다.
    """
    pat = re.compile(
        r"(?<![가-힣])" + re.escape(name) + rf"(?=(?:{_KOREAN_JOSA_ALT})(?![가-힣])|[^가-힣]|$)"
    )
    count = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        if matches is not None:
            _record_redaction_match(matches, "LEGAL_PARTY", match.group(0), match.start(), match.end())
        if report is not None:
            _count_up(report, "LEGAL_PARTY")
        return "[LEGAL_PARTY]"

    return _tracked_sub(pat, repl, text), count


@dataclass(frozen=True, slots=True)
class RedactionMatch:
    tag: str
    text: str
    start: int = field(default=-1, compare=False)
    end: int = field(default=-1, compare=False)
    occurrence_id: str = field(default="", compare=False)
    source: str = field(default="", compare=False)


def _record_redaction_match(
    matches: list[RedactionMatch],
    tag: str,
    value: str,
    start: int = -1,
    end: int = -1,
) -> None:
    tracker = _SOURCE_OFFSET_TRACKER.get()
    if tracker is not None:
        start, end = tracker.source_span(start, end)
        if 0 <= start < end <= len(tracker.source_text):
            value = tracker.source_text[start:end]
    cleaned = " ".join(value.split()).strip(" ,;:/")
    if len(cleaned) < 2 or cleaned.startswith("[") or cleaned.endswith("]"):
        return
    matches.append(
        RedactionMatch(
            tag=tag,
            text=cleaned,
            start=start,
            end=end,
            occurrence_id=f"occ_{len(matches) + 1:06d}",
            source=source_for_tag(tag),
        )
    )


def _display_token(tag: str, mode: str = "label_en") -> str:
    return display_token(tag, mode)


REVIEW_REQUIRED_TAGS = {"ACCOUNT", "ADDRESS", "NAME", "LEGAL_PARTY", "WEAK_PLACE", "CASE_NUMBER", "DOC_META"}


def _review_tag(tag: str) -> str:
    return "PLACE" if tag == "WEAK_PLACE" else tag


def _review_status_for_tag(tag: str, status: str) -> str:
    return "needs_review" if tag in REVIEW_REQUIRED_TAGS else status


def _safe_rect_bbox(rect: Any) -> dict[str, float]:
    return {
        "x": float(rect.x0),
        "y": float(rect.y0),
        "width": float(rect.width),
        "height": float(rect.height),
    }


def _review_item_for_rect(
    match: RedactionMatch,
    rect: Any,
    page_num: int,
    status: str,
    display_mode: str,
) -> dict[str, Any]:
    tag = _review_tag(match.tag)
    bbox = _safe_rect_bbox(rect)
    return {
        "id": f"{tag}-p{page_num + 1}-{bbox['x']:.1f}-{bbox['y']:.1f}-{bbox['width']:.1f}-{bbox['height']:.1f}",
        "page": page_num,
        "bbox": bbox,
        "tag": tag,
        "display_token": _display_token(tag, display_mode),
        "status": _review_status_for_tag(match.tag, status),
        "count": 1,
        "raw_value_saved": False,
    }


@lru_cache(maxsize=1)
def _korean_pdf_font_file() -> str | None:
    return korean_pdf_font_file()


def _insert_pdf_label(page: Any, rect: Any, label: str) -> None:
    insert_pdf_label(page, rect, label)


def review_items_for_matches(
    matches: list[RedactionMatch],
    counts: dict[str, int] | None = None,
    status: str = "applied",
    document_context: DocumentContext | None = None,
) -> list[dict[str, Any]]:
    counts = counts or {}
    contexts = find_masking_context(matches, document_context) if document_context is not None else tuple()
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for idx, match in enumerate(matches):
        item_status = _review_status_for_tag(match.tag, status)
        tag = _review_tag(match.tag)
        context = contexts[idx] if idx < len(contexts) else None
        context_key = (
            context.get("context_id"),
            context.get("page"),
            context.get("chunk_index"),
        ) if context is not None else None
        key = (tag, item_status, context_key)
        if key in seen:
            continue
        seen.add(key)
        items.append(
            {
                "page": None,
                "tag": tag,
                "display_token": _display_token(tag),
                "status": item_status,
                "count": counts.get(match.tag, counts.get(tag, 1)),
                "raw_value_saved": False,
            }
        )
        if context is not None:
            items[-1]["context"] = context
    return items


def _parse_custom_keywords(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        parts = [str(item) for item in raw]
    else:
        parts = re.split(r"[\n,;]+", str(raw))

    seen: set[str] = set()
    keywords: list[str] = []
    for part in parts:
        keyword = " ".join(part.split()).strip()
        if len(keyword) < 2 or keyword.startswith("[") or keyword.endswith("]"):
            continue
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(keyword)
    return sorted(keywords, key=len, reverse=True)


REGION_DATA_PATH = Path(__file__).resolve().parent / "data" / "kr_regions.json"
REGION_SEED_DATA_PATH = Path(__file__).resolve().parent / "data" / "kr_regions.seed.json"
ADDRESS_LABEL_PAT = re.compile(
    r"(?P<label>\b(?:주소|소재지|거소|사업장\s*주소|송달장소|주민등록상\s*주소|본점\s*소재지)\b\s*[:：]\s*)"
    r"(?P<addr>[^\n,;]+)"
)


def _resolve_region_data_path(path: str | os.PathLike[str] | None = None) -> Path | None:
    if path:
        target = Path(path)
        return target if target.exists() else None
    if REGION_DATA_PATH.exists():
        return REGION_DATA_PATH
    if REGION_SEED_DATA_PATH.exists():
        return REGION_SEED_DATA_PATH
    return None


def load_region_data(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = _resolve_region_data_path(path)
    if target is None:
        return {
            "schema_version": 0,
            "source": "built-in-fallback",
            "is_seed": True,
            "sido": [
                "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시",
                "울산광역시", "세종특별자치시", "경기도", "강원특별자치도", "충청북도", "충청남도",
                "전북특별자치도", "전라남도", "경상북도", "경상남도", "제주특별자치도",
            ],
            "sigungu": [],
            "eupmyeondong": [],
            "weak_place_names": [],
        }
    with open(target, "r", encoding="utf-8") as f:
        return json.load(f)


def region_data_metadata(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = _resolve_region_data_path(path)
    data = load_region_data(path)
    source = str(data.get("source") or "unknown")
    default_seed = True if target is None else target.name.endswith(".seed.json")
    is_seed = bool(data.get("is_seed", default_seed))
    return {
        "region_data_source": source,
        "region_data_version": str(data.get("schema_version", "unknown")),
        "region_data_is_seed": is_seed,
        "region_data_path": str(target) if target else "built-in-fallback",
        "region_data_warning": (
            "전국 행정구역 데이터가 샘플/seed 상태이므로 최신 전체 지역명 탐지는 제한될 수 있음"
            if is_seed
            else ""
        ),
    }


@lru_cache(maxsize=32)
def _region_terms(key: str) -> list[str]:
    data = load_region_data()
    values = data.get(key, [])
    return sorted({str(v).strip() for v in values if str(v).strip()}, key=len, reverse=True)


def _literal_alt(values: list[str]) -> str:
    return "|".join(re.escape(v) for v in values)


@lru_cache(maxsize=1)
def _national_address_patterns() -> list[re.Pattern[str]]:
    sido = _literal_alt(_region_terms("sido"))
    sigungu = _literal_alt(_region_terms("sigungu"))
    eupmyeondong = _literal_alt(_region_terms("eupmyeondong"))
    ri = _literal_alt(_region_terms("ri"))
    single_tier_sido = _literal_alt(_region_terms("single_tier_sido"))
    local_tail = rf"(?:\s+(?:{eupmyeondong})(?:\s+(?:{ri}))?)?" if ri else rf"(?:\s+(?:{eupmyeondong}))?"
    ri_tail = rf"(?:\s+(?:{ri}))?" if ri else ""
    road_or_lot = r"(?:\s+[가-힣0-9·\-]+(?:로|길)\s*\d{1,4}(?:-\d{1,4})?|\s+\d{1,4}(?:-\d{1,4})?\s*번지?)?"
    patterns: list[re.Pattern[str]] = []
    if sido and sigungu:
        patterns.append(
            re.compile(
                rf"(?P<value>(?:{sido})\s+(?:{sigungu}){local_tail}{road_or_lot})"
            )
        )
    if sigungu and eupmyeondong:
        patterns.append(
            re.compile(
                rf"(?P<value>(?:{sigungu})\s+(?:{eupmyeondong}){ri_tail}{road_or_lot})"
            )
        )
    if single_tier_sido and eupmyeondong:
        patterns.append(
            re.compile(
                rf"(?P<value>(?:{single_tier_sido})\s+(?:{eupmyeondong}){ri_tail}{road_or_lot})"
            )
        )
    return patterns


@lru_cache(maxsize=1)
def _weak_place_patterns() -> list[re.Pattern[str]]:
    weak = _literal_alt([term for term in _region_terms("weak_place_names") if len(term) > 2])
    return [re.compile(rf"(?<![가-힣])(?P<value>{weak})(?![가-힣])")] if weak else []


MASK_TOKEN_SEGMENT_PAT = re.compile(r"(\[[A-Z_]+\])")


def apply_custom_keyword_masking(
    text: str,
    keywords: list[str],
    counts: dict[str, int],
    matches: list[RedactionMatch],
    korean_tokens: bool = False,
    tag: str = "KEYWORD",
) -> str:
    if not keywords:
        return text

    token = _mask_token(tag) if korean_tokens else f"[{tag}]"

    def mask_plain_segment(segment: str, segment_start: int) -> str:
        work = segment
        for keyword in keywords:
            pat = re.compile(re.escape(keyword))

            def repl(m: re.Match[str]) -> str:
                _record_redaction_match(
                    matches,
                    tag,
                    m.group(0),
                    segment_start + m.start(),
                    segment_start + m.end(),
                )
                _count_up(counts, tag)
                return token

            work = _tracked_sub(pat, repl, work, base_offset=segment_start)
        return work

    parts = MASK_TOKEN_SEGMENT_PAT.split(text)
    segment_start = 0
    for idx, part in enumerate(parts):
        if not part or MASK_TOKEN_SEGMENT_PAT.fullmatch(part):
            segment_start += len(part)
            continue
        parts[idx] = mask_plain_segment(part, segment_start)
        segment_start += len(parts[idx])
    return "".join(parts)


def _sub_simple(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    matches: list[RedactionMatch] | None = None,
    value_group: str | None = None,
) -> str:
    def repl(_m: re.Match[str]) -> str:
        if matches is not None:
            value = _m.group(value_group) if value_group else _m.group(0)
            start = _m.start(value_group) if value_group else _m.start()
            end = _m.end(value_group) if value_group else _m.end()
            _record_redaction_match(matches, tag, value, start, end)
        _count_up(report, tag)
        return f"[{tag}]"

    return _tracked_sub(pat, repl, text)


def _sub_keep_label(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    value_group: str,
    matches: list[RedactionMatch] | None = None,
) -> str:
    def repl(m: re.Match[str]) -> str:
        if matches is not None:
            _record_redaction_match(
                matches,
                tag,
                m.group(value_group),
                m.start(value_group),
                m.end(value_group),
            )
        _count_up(report, tag)
        return f"{m.group('label')}[{tag}]"

    return _tracked_sub(pat, repl, text)


def _sub_phone_label_sequence(text: str, report: dict[str, int], matches: list[RedactionMatch]) -> str:
    def repl(m: re.Match[str]) -> str:
        phone_matches = list(PHONE_VALUE_PAT.finditer(m.group("value")))
        if not phone_matches:
            return m.group(0)
        value_start = m.start("value")
        for phone in phone_matches:
            _record_redaction_match(
                matches,
                "PHONE",
                phone.group(0),
                value_start + phone.start(),
                value_start + phone.end(),
            )
            _count_up(report, "PHONE")
        return f"{m.group('label')}{'[PHONE]' * len(phone_matches)}"

    return _tracked_sub(PHONE_LABEL_PAT, repl, text)


def _sub_keep_label_when(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    value_group: str,
    should_mask: Callable[[re.Match[str]], bool],
    matches: list[RedactionMatch] | None = None,
) -> str:
    def repl(m: re.Match[str]) -> str:
        value = m.group(value_group)
        if has_masked_token(value) or not should_mask(m):
            return m.group(0)
        if matches is not None:
            _record_redaction_match(matches, tag, value, m.start(value_group), m.end(value_group))
        _count_up(report, tag)
        return f"{m.group('label')}[{tag}]"

    return _tracked_sub(pat, repl, text)


def _sub_case_title_line(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    matches: list[RedactionMatch] | None = None,
) -> str:
    def repl(m: re.Match[str]) -> str:
        if matches is not None:
            _record_redaction_match(matches, tag, m.group("title"), m.start("title"), m.end("title"))
        _count_up(report, tag)
        return f"{m.group('number')} [{tag}]"

    return _tracked_sub(pat, repl, text)


def _sub_approval_table_line(
    text: str,
    pat: re.Pattern[str],
    tag: str,
    report: dict[str, int],
    matches: list[RedactionMatch] | None = None,
) -> str:
    def repl(m: re.Match[str]) -> str:
        if matches is not None:
            _record_redaction_match(matches, tag, m.group("value"), m.start("value"), m.end("value"))
        _count_up(report, tag)
        return f"{m.group('label')}\n[{tag}]"

    return _tracked_sub(pat, repl, text)
