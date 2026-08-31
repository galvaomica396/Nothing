from __future__ import annotations

import document_masker_ocr_gui as masker


LEGAL_SOURCE = (
    "사건번호: 2023가단12345\n"
    "사건명: 손해배상\n"
    "원고: 홍길동\n"
    "피고: 김철수\n"
    "대법원 2020. 1. 2. 선고 2019다12345 판결\n"
    "서울중앙지방법원"
)

LEGAL_MASKED_GOLDEN = (
    "사건번호: [CASE_NUMBER]\n"
    "사건명: [CASE_TITLE]\n"
    "원고: [LEGAL_PARTY]\n"
    "피고: [LEGAL_PARTY]\n"
    "대법원 2020. 1. 2. 선고 2019다12345 판결\n"
    "[COURT]"
)


def test_legal_text_pipeline_golden() -> None:
    masked, counts, matches = masker.mask_text(LEGAL_SOURCE, profile="legal")

    assert masked == LEGAL_MASKED_GOLDEN
    assert counts == {
        "CASE_NUMBER": 1,
        "CASE_TITLE": 1,
        "COURT": 1,
        "LEGAL_PARTY": 2,
    }
    assert [(match.tag, LEGAL_SOURCE[match.start : match.end]) for match in matches] == [
        ("LEGAL_PARTY", "홍길동"),
        ("LEGAL_PARTY", "김철수"),
        ("COURT", "서울중앙지방법원"),
        ("CASE_TITLE", "손해배상"),
        ("CASE_NUMBER", "2023가단12345"),
    ]


def test_legal_profile_disables_public_document_only_rules() -> None:
    text = "기안자 김철수\n시행 건축과-1234"
    masked, counts, matches = masker.mask_text(text, profile="legal")

    assert masked == text
    assert "APPROVAL_LINE" not in counts
    assert "DOC_META" not in counts
    assert all(match.tag not in {"APPROVAL_LINE", "DOC_META"} for match in matches)
def test_profile_isolation_uses_identical_input_without_public_rule_leakage() -> None:
    shared = (
        "기안자 김철수\n"
        "시행 건축과-1234\n"
        "사건번호: 2023가단12345\n"
        "사건명: 손해배상\n"
        "원고: 홍길동\n"
        "서울중앙지방법원"
    )
    legal, legal_counts, legal_matches = masker.mask_text(shared, profile="legal")
    public_profiles = {
        "internal_review": masker.mask_text(shared, profile="internal_review"),
        "official_dispatch": masker.mask_text(shared, profile="official_dispatch"),
        "mixed": masker.mask_text(shared, profile="mixed"),
    }

    assert legal == (
        "기안자 김철수\n"
        "시행 건축과-1234\n"
        "사건번호: [CASE_NUMBER]\n"
        "사건명: [CASE_TITLE]\n"
        "원고: [LEGAL_PARTY]\n"
        "[COURT]"
    )
    assert legal_counts == {"CASE_NUMBER": 1, "CASE_TITLE": 1, "LEGAL_PARTY": 1, "COURT": 1}
    assert [(match.tag, shared[match.start : match.end]) for match in legal_matches] == [
        ("LEGAL_PARTY", "홍길동"),
        ("COURT", "서울중앙지방법원"),
        ("CASE_TITLE", "손해배상"),
        ("CASE_NUMBER", "2023가단12345"),
    ]

    for profile, (masked, counts, matches) in public_profiles.items():
        assert masked == (
            "기안자 김철수\n"
            "시행 [DOC_META]\n"
            "사건번호: 2023가단12345\n"
            "사건명: 손해배상\n"
            "원고: 홍길동\n"
            "서울중앙지방법원"
        ), profile
        assert counts == {"DOC_META": 1}, profile
        assert [(match.tag, shared[match.start : match.end]) for match in matches] == [
            ("DOC_META", "건축과-1234"),
        ], profile
