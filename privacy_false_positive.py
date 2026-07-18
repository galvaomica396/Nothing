from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final


MASKED_TOKEN_PAT: Final = re.compile(r"\[[A-Z_]+\]")
KOREAN_SINGLE_SURNAME_CHARS: Final = frozenset(
    # 기존 상용 성씨
    "김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민류나진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁"
    # 리뷰가 확인한 누락 실존 성씨 보강(C-2)
    "은태국편봉피사예계간갈감견승시상빈부판팽후좌"
    # 추가 실존 단성(單姓) 보강
    "경궁근단돈두려로룡매목범복빙삼순아어옹용운음점종준즙창초춘탄평포풍필학해형호화환흥"
)
KOREAN_COMPOUND_SURNAMES: Final = frozenset({"남궁", "황보", "제갈", "사공", "선우", "독고", "서문"})
COMMON_NON_PERSON_VALUES = {
    "관리", "시스템", "제도", "정보", "접수", "신청", "처리", "개선", "등록", "변경",
    "작성", "담당", "요청", "서식", "절차", "안내", "민원", "직통", "주장", "반박",
    "계획", "기준", "업무", "결과", "권한", "문서", "부서", "대장", "책임자",
    "전결", "대결",
    # 결재/업무 흐름 비인명 어절 — 성씨 화이트리스트 보강(C-2)으로 인한 과탐 억제.
    "승인", "상신", "반려", "공람", "계약", "시행", "통보", "회신", "종결", "이송",
    "각하", "기각", "인용", "취하", "보류", "완료", "확인", "협의", "합의", "재검토",
}
COMMON_BUSINESS_VALUES = {
    "관리시스템", "하자관리", "품질관리팀", "하자관리팀", "관리부서", "관리부",
    "시스템개선요청", "하자관리사무실", "관리자료", "시스템안내",
}
DOCUMENT_PHRASE_VALUES: Final = {
    "검토", "결과", "의견", "자료", "보고", "계획", "기준", "절차", "안내",
    "검토결과", "검토의견", "사건자료", "처리결과", "업무보고",
}
DOCUMENT_PHRASE_SUFFIXES: Final = (
    "결과", "의견", "자료", "계획", "기준", "보고", "절차", "안내", "요청", "처리", "검토",
)
COURT_BRANCH_VALUES: Final = frozenset(
    {
        "안양지원", "성남지원", "여주지원", "평택지원", "안산지원", "부천지원", "고양지원", "남양주지원",
        "강릉지원", "원주지원", "속초지원", "영월지원", "홍성지원", "공주지원", "논산지원", "서산지원",
        "천안지원", "충주지원", "제천지원", "영동지원", "안동지원", "경주지원", "포항지원", "김천지원",
        "상주지원", "의성지원", "영덕지원", "마산지원", "진주지원", "통영지원", "밀양지원", "거창지원",
        "목포지원", "장흥지원", "순천지원", "해남지원", "군산지원", "정읍지원", "남원지원",
    }
)
NON_COURT_SUPPORT_SUFFIXES: Final = (
    "자립지원", "복지지원", "생활지원", "고용지원", "주거지원", "의료지원", "교육지원", "보육지원",
    "돌봄지원", "취업지원", "창업지원", "민원지원", "행정지원", "업무지원", "서비스지원", "기술지원",
    "고객지원", "운영지원", "사업지원", "재정지원", "법률지원",
)
COURT_CONTEXT_PAT: Final = re.compile(r"(?:법\s*원|재\s*판\s*소|재\s*판\s*부|\[COURT\])")
COURT_CONTEXT_RADIUS: Final = 40
PERSON_NAME_BACKUP_BLOCKLIST: Final = frozenset(
    {
        "안전", "장애인", "점검", "관리", "지원", "담당", "업무", "확인", "검토", "처리", "완료",
        "접수", "신고", "허가", "계획", "사업", "운영", "규정", "조례", "행정", "민원", "시설",
        "건축", "토목", "환경", "보건", "위생", "교통", "예산", "회계", "총무", "기획", "감사",
        "복지", "교육", "공사", "준공", "착공", "승인", "결재", "반려", "보완", "시행", "공람",
        "상신", "계약", "결과", "자료",
    }
)
PERSON_LABEL_CONTEXT_PAT: Final = re.compile(
    r"(?:이름|성명|민원인|신청인|보호자|대표자|제출인)\s*(?:[:：]\s*|\s+)$"
)
APPROVAL_ROLE_TOKEN_PAT: Final = re.compile(
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|서기보?|주사보?|팀장|과장|국장|실장|센터장|"
    r"담당자?|계장|부시장|시장|부구청장|구청장|인턴|사원|주임|선임|책임|수석|연구원|대리|"
    r"차장|부장|본부장|사업부장|파트장|매니저|이사|상무|전무|부사장|사장|대표(?:이사)?|"
    r"CEO|CTO|CFO|COO|[1-9Bb]\s*[급긍금])",
    re.IGNORECASE,
)
PERSON_CONTEXT_RADIUS: Final = 96
PERSON_ORG_SUFFIXES: Final = ("센터", "공단", "공사", "과", "팀", "국", "실", "부", "처", "청", "원", "소")


def compact_korean_value(value: str) -> str:
    return re.sub(r"\s+", "", value.strip())


def has_masked_token(value: str) -> bool:
    return bool(MASKED_TOKEN_PAT.search(value))


def is_common_non_person_value(value: str) -> bool:
    compact = compact_korean_value(value)
    return compact in COMMON_NON_PERSON_VALUES or compact in COMMON_BUSINESS_VALUES


def has_likely_korean_surname(value: str) -> bool:
    compact = compact_korean_value(value)
    if len(compact) < 2 or len(compact) > 4:
        return False
    if len(compact) >= 3 and compact[:2] in KOREAN_COMPOUND_SURNAMES:
        return True
    return compact[0] in KOREAN_SINGLE_SURNAME_CHARS


def is_likely_person_name_value(value: str) -> bool:
    compact = compact_korean_value(value)
    return (
        bool(re.fullmatch(r"[가-힣]{2,4}", compact))
        and has_likely_korean_surname(compact)
        and not is_common_non_person_value(compact)
    )


def _is_person_false_positive_value(value: str) -> bool:
    compact = compact_korean_value(value)
    return compact in PERSON_NAME_BACKUP_BLOCKLIST


def _has_dense_approval_role_context(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - PERSON_CONTEXT_RADIUS):min(len(text), end + PERSON_CONTEXT_RADIUS)]
    roles = {compact_korean_value(match.group(0)).casefold() for match in APPROVAL_ROLE_TOKEN_PAT.finditer(window)}
    return len(roles) >= 2


def _has_person_name_shape(value: str, text: str, end: int) -> bool:
    compact = compact_korean_value(value)
    if any(text[end:].startswith(suffix) for suffix in PERSON_ORG_SUFFIXES):
        return False
    if compact[:2] in KOREAN_COMPOUND_SURNAMES:
        return len(compact) in (3, 4)
    return compact[0] in KOREAN_SINGLE_SURNAME_CHARS and len(compact) in (2, 3)


def is_likely_person_name(value: str, text: str, start: int, end: int) -> bool:
    compact = compact_korean_value(value)
    if not re.fullmatch(r"[가-힣]{2,4}", compact):
        return False
    if start < 0 or end <= start or end > len(text):
        return False
    before = text[max(0, start - PERSON_CONTEXT_RADIUS):start]
    if PERSON_LABEL_CONTEXT_PAT.search(before):
        return True
    if is_common_non_person_value(compact) or _is_person_false_positive_value(compact):
        return False
    if _has_dense_approval_role_context(text, start, end):
        return True
    return _has_person_name_shape(compact, text, end)


def is_labeled_person_name_value(value: str) -> bool:
    """강한 라벨 컨텍스트(성명/이름/원고 등 확정 라벨) 전용 완화 판정(C-2).

    성씨 화이트리스트를 하드 게이트로 쓰지 않는다. 라벨이 인명임을 이미 강하게
    지시하므로, 2~4자 한글이며 비인명 상용어(COMMON_NON_PERSON_VALUES)가 아니면
    인명으로 본다. 이렇게 하면 화이트리스트에 없는 실존 성씨(예: 은지원, 태영호)도
    라벨 컨텍스트에서 마스킹된다.
    """
    compact = compact_korean_value(value)
    return bool(re.fullmatch(r"[가-힣]{2,4}", compact)) and not is_common_non_person_value(compact)


def is_likely_address_value(value: str, sido_terms: Sequence[str]) -> bool:
    compact = compact_korean_value(value)
    if is_common_non_person_value(compact):
        return False
    if re.search(r"(?:로|길)\s*\d{1,4}(?:-\d{1,4})?", value):
        return True
    if re.search(r"\d{1,4}(?:-\d{1,4})?\s*번지", value):
        return True
    # 라벨된 지번 주소(C-3): 행정동/리 토큰 1개 + 지번(예: 역삼동 123-45, 반포동 20)
    if re.search(r"[가-힣]{1,10}(?:동|리)\s*\d{1,4}(?:-\d{1,4})?(?!\d)", value):
        return True
    if any(term and term in value for term in sido_terms):
        return True
    admin_tokens = re.findall(r"[가-힣]{2,20}(?:시|군|구|읍|면|동|리)", value)
    return len(admin_tokens) >= 2


def is_likely_company_value(value: str) -> bool:
    compact = compact_korean_value(value)
    if is_common_non_person_value(compact):
        return False
    return bool(re.search(r"(?:주식회사|유한회사|합자회사|합명회사|법인|㈜|\(주\)|[A-Za-z]{2,})", value))


def is_likely_law_firm_value(value: str) -> bool:
    compact = compact_korean_value(value).strip(" ,;:/")
    if has_masked_token(value) or not compact:
        return False
    if is_common_non_person_value(compact):
        return False
    if compact in DOCUMENT_PHRASE_VALUES or compact.endswith(DOCUMENT_PHRASE_SUFFIXES):
        return False
    if re.search(r"\s", value.strip()):
        return False
    if re.search(r"[A-Za-z0-9&]", compact):
        return True
    return bool(re.fullmatch(r"[가-힣]{2,12}", compact))


def is_likely_doc_meta_value(value: str) -> bool:
    if has_masked_token(value):
        return False
    compact = compact_korean_value(value)
    if is_common_non_person_value(compact):
        return False
    return bool(re.search(r"\d|@|https?://|www\.|[-–—]\s*\d", value))


def is_likely_court_value(value: str, text: str, start: int, end: int) -> bool:
    compact = compact_korean_value(value)
    if "법원" in compact or "재판소" in compact:
        return True
    if not compact.endswith("지원") or compact.endswith(NON_COURT_SUPPORT_SUFFIXES):
        return False
    if compact in COURT_BRANCH_VALUES:
        return True
    before = text[max(0, start - COURT_CONTEXT_RADIUS):start]
    after = text[end:min(len(text), end + COURT_CONTEXT_RADIUS)]
    return bool(COURT_CONTEXT_PAT.search(before + after))
