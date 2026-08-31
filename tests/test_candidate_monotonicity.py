from __future__ import annotations

import document_masker_ocr_gui as masker
import pytest


def _signature(match: object) -> tuple[object, ...]:
    occurrence_id = getattr(match, "occurrence_id")
    source = getattr(match, "source")
    assert isinstance(occurrence_id, str) and occurrence_id
    assert isinstance(source, str) and source
    return (
        getattr(match, "tag"),
        getattr(match, "text"),
        getattr(match, "start"),
        getattr(match, "end"),
        occurrence_id,
        source,
    )


def test_public_boolean_approval_flags_never_enrich_text_candidates() -> None:
    text = "성명: 홍길동\n기안자 김철수\n기관명: 서울특별시 담당부서: 건축과"
    disabled = masker.mask_text(
        text,
        profile="mixed",
        use_approval_line=False,
    )
    requested = masker.mask_text(
        text,
        profile="mixed",
        use_approval_line=True,
    )

    assert requested[0] == disabled[0]
    assert requested[1] == disabled[1] == {"NAME": 1}
    assert [_signature(match) for match in requested[2]] == [_signature(match) for match in disabled[2]]
    assert [(match.tag, match.text) for match in requested[2]] == [("NAME", "홍길동")]
    assert "APPROVAL_LINE" not in requested[1]
    assert "INSTITUTION_VALUE" not in requested[1]
    assert "DEPARTMENT_VALUE" not in requested[1]

def test_canonical_public_profiles_preserve_common_offsets() -> None:
    text = "성명: 홍길동 / 연락처: 010-1234-5678"
    _masked, _counts, baseline = masker.mask_text(
        text,
        profile="mixed",
        use_approval_line=False,
        use_region_context=False,
        use_doc_meta=False,
    )
    expected = {_signature(match) for match in baseline}
    assert {(signature[0], signature[1]) for signature in expected} == {
        ("NAME", "홍길동"),
        ("PHONE", "010-1234-5678"),
    }

    for profile in ("internal_review", "official_dispatch", "mixed"):
        _masked, _counts, matches = masker.mask_text(text, profile=profile)
        assert expected <= {_signature(match) for match in matches}


def test_legacy_official_alias_is_rejected() -> None:
    with pytest.raises(ValueError, match="MASKING_PROFILE_UNSUPPORTED"):
        masker.mask_text("성명: 홍길동 / 기안자 김철수", profile="official")
def test_public_name_context_policy_retains_plausible_review_and_reports_features() -> None:
    from privacy_detection import score_public_body_name
    strong = score_public_body_name(authoritative_label=True, approval_role=True,
                                    punctuation_or_label_boundary=True, distance_from_label=2,
                                    page_position_match=True, region_state="confirmed")
    plausible = score_public_body_name(authoritative_label=True)
    assert strong["action"] == "auto_mask"
    assert plausible["action"] == "review"
    assert "authoritative_label" in strong["reason_codes"]
    assert strong["policy_version"] == "public-name-context-policy-v1"
    calibrated_review = score_public_body_name(
        authoritative_label=True,
        punctuation_or_label_boundary=True,
        distance_from_label=1,
        page_position_match=True,
        region_state="confirmed",
        auto_mask_threshold=.90,
        review_threshold=.50,
    )
    assert calibrated_review["action"] == "review"
    with pytest.raises(ValueError):
        score_public_body_name(auto_mask_threshold=.40, review_threshold=.50)


def test_text_api_does_not_enrich_fixed_public_values() -> None:
    text = "기관명: 서울특별시 담당부서: 건축과"
    masked, counts, matches = masker.mask_text(text, profile="mixed")

    assert masked == text
    assert "INSTITUTION_VALUE" not in counts
    assert "DEPARTMENT_VALUE" not in counts
    assert not matches
