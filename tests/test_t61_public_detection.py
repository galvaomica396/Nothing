from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pymupdf

from document_masker_ocr_gui import (
    _prereview_routing_signal,
    mask_text,
    trusted_analysis_manifest,
)
from masking_extraction import ExtractResult, ExtractedPage, ExtractedWord
from masking_redaction import OccurrenceRedactionInput, redact_pdf_native
from masking_rules import _national_address_patterns, load_region_data
from privacy_detection import (
    score_public_institution_address,
    score_public_institution_value,
    score_public_region_value,
)
from privacy_spans import action_for_tag
from public_detection import build_public_candidates
from document_routing import _signal_kinds


SESSION_HASH_KEY = bytes(range(32))


def _word_entries(text: str, rows: list[tuple[str, float]]) -> tuple[ExtractedWord, ...]:
    words: list[ExtractedWord] = []
    cursor = 0
    for index, (value, y0) in enumerate(rows):
        start = text.index(value, cursor)
        end = start + len(value)
        x0 = 24.0 + (index % 10) * 55.0
        rect = (x0, y0, x0 + max(24.0, len(value) * 6.0), y0 + 14.0)
        words.append(ExtractedWord(
            value,
            rect,
            page_start=start,
            page_end=end,
            source="pymupdf_text_layer",
        ))
        cursor = end
    return tuple(words)


def _word_hash(words: tuple[ExtractedWord, ...], rects: list[dict[str, float]]) -> str:
    wanted = set()
    for rect in rects:
        if isinstance(rect, dict):
            wanted.add(tuple(float(rect[key]) for key in ("x0", "y0", "x1", "y1")))
        else:
            wanted.add(tuple(float(value) for value in rect))
    values = [
        word.text
        for word in sorted(words, key=lambda item: (item.bbox[1], item.bbox[0]))
        if tuple(float(value) for value in word.bbox) in wanted
    ]
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def test_public_candidates_keep_footer_dispatch_numbers_and_addresses_geometry_scoped():
    text = (
        "본문 서울특별시 동작구청장에게 안내합니다. 주식회사 한빛 사업입니다.\n"
        "우편번호 03718 주소 서울특별시 동작구 장승배기로 161\n"
        "시행 총무과-1234"
    )
    rows = [
        ("본문", 100.0),
        ("서울특별시", 100.0),
        ("동작구청장에게", 100.0),
        ("안내합니다.", 100.0),
        ("주식회사", 130.0),
        ("한빛", 130.0),
        ("사업입니다.", 130.0),
        ("우편번호", 680.0),
        ("03718", 680.0),
        ("주소", 680.0),
        ("서울특별시", 680.0),
        ("동작구", 680.0),
        ("장승배기로", 680.0),
        ("161", 680.0),
        ("시행", 735.0),
        ("총무과-1234", 735.0),
    ]
    words = _word_entries(text, rows)
    candidates = build_public_candidates(
        text,
        tuple(
            (
                word.page_start,
                word.page_end,
                word,
                {
                    "x0": word.bbox[0],
                    "y0": word.bbox[1],
                    "x1": word.bbox[2],
                    "y1": word.bbox[3],
                },
            )
            for word in words
        ),
        page_height=792.0,
        region_data=load_region_data(),
        address_patterns=_national_address_patterns(),
        options={
            "address": True,
            "company": True,
            "doc_meta": True,
            "email": True,
            "place": True,
            "region_context": True,
            "auto_threshold": 0.85,
            "review_threshold": 0.5,
        },
        footer_contact_value_kind=lambda value: value in {"03718"},
    )

    by_category = {(item.category, item.value): item for item in candidates}
    assert ("dispatch_metadata", "총무과-1234") in by_category
    assert ("institution_address", "서울특별시 동작구 장승배기로 161") in by_category
    assert ("institution_value", "주식회사 한빛") in by_category
    assert not any(item.value == "동작구" for item in candidates)
    assert not any(item.category == "institution_value" and "청장" in item.value for item in candidates)
    assert mask_text("동작구청장에게 안내합니다.", profile="mixed")[1] == {}
    assert by_category[("dispatch_metadata", "총무과-1234")].action == "mask"
    assert by_category[("institution_address", "서울특별시 동작구 장승배기로 161")].action == "review"


def test_public_context_toggles_suppress_new_candidates():
    text = "서울특별시 동작구 주식회사 한빛\n주소 서울특별시 동작구 샘플로 1"
    words = _word_entries(text, [
        ("서울특별시", 100.0),
        ("동작구", 100.0),
        ("주식회사", 100.0),
        ("한빛", 100.0),
        ("주소", 700.0),
        ("서울특별시", 700.0),
        ("동작구", 700.0),
        ("샘플로", 700.0),
        ("1", 700.0),
    ])
    entries = tuple(
        (
            word.page_start,
            word.page_end,
            word,
            {"x0": word.bbox[0], "y0": word.bbox[1], "x1": word.bbox[2], "y1": word.bbox[3]},
        )
        for word in words
    )
    candidates = build_public_candidates(
        text,
        entries,
        page_height=792.0,
        region_data=load_region_data(),
        address_patterns=_national_address_patterns(),
        options={
            "address": False,
            "company": False,
            "doc_meta": False,
            "email": False,
            "place": False,
            "region_context": False,
        },
    )
    assert candidates == []


def test_labeled_institution_address_is_candidate_independent_of_page_position():
    text = "기관주소 서울특별시 동작구 샘플로 1\n본문"
    words = _word_entries(text, [
        ("기관주소", 100.0),
        ("서울특별시", 100.0),
        ("동작구", 100.0),
        ("샘플로", 100.0),
        ("1", 100.0),
        ("본문", 200.0),
    ])
    entries = tuple(
        (
            word.page_start,
            word.page_end,
            word,
            {"x0": word.bbox[0], "y0": word.bbox[1], "x1": word.bbox[2], "y1": word.bbox[3]},
        )
        for word in words
    )
    candidates = build_public_candidates(
        text,
        entries,
        page_height=792.0,
        region_data=load_region_data(),
        address_patterns=_national_address_patterns(),
        options={"address": True, "company": False, "place": False, "region_context": False},
    )
    assert [(item.category, item.value, item.action) for item in candidates] == [
        ("institution_address", "서울특별시 동작구 샘플로 1", "review"),
    ]


def test_address_candidate_is_location_independent_and_review_only():
    text = "본문 주소지 서울특별시 동작구 샘플로 1"
    words = _word_entries(text, [
        ("본문", 100.0),
        ("주소지", 100.0),
        ("서울특별시", 100.0),
        ("동작구", 100.0),
        ("샘플로", 100.0),
        ("1", 100.0),
    ])
    entries = tuple(
        (
            word.page_start,
            word.page_end,
            word,
            {"x0": word.bbox[0], "y0": word.bbox[1], "x1": word.bbox[2], "y1": word.bbox[3]},
        )
        for word in words
    )
    candidates = build_public_candidates(
        text,
        entries,
        page_height=792.0,
        region_data=load_region_data(),
        address_patterns=_national_address_patterns(),
        options={"address": True, "company": False, "place": False, "region_context": False, "email": False},
    )
    assert [(item.category, item.action, item.reason_codes[0]) for item in candidates] == [
        ("institution_address", "review", "institution_address_review_required"),
    ]


def test_text_layer_email_fallback_covers_spacing_when_common_detector_misses():
    text = "문의 privacy @ example.go.kr"
    words = _word_entries(text, [
        ("문의", 100.0),
        ("privacy", 100.0),
        ("@", 100.0),
        ("example.go.kr", 100.0),
    ])
    entries = tuple(
        (
            word.page_start,
            word.page_end,
            word,
            {"x0": word.bbox[0], "y0": word.bbox[1], "x1": word.bbox[2], "y1": word.bbox[3]},
        )
        for word in words
    )
    candidates = build_public_candidates(
        text,
        entries,
        page_height=792.0,
        region_data=load_region_data(),
        address_patterns=_national_address_patterns(),
        options={"email": True, "address": False, "company": False, "place": False, "region_context": False},
    )
    assert [(item.category, item.value, item.action) for item in candidates] == [
        ("email", "privacy @ example.go.kr", "mask"),
    ]


def test_public_context_policy_retains_weak_hits_as_review_and_separates_categories():
    assert action_for_tag("PLACE") == "review"
    assert action_for_tag("INSTITUTION_VALUE") == "review"
    assert score_public_region_value(
        dictionary_match=True,
        exact_boundary=True,
    )["action"] == "review"
    assert score_public_institution_value(
        strong_institution_pattern=True,
        exact_boundary=True,
    )["action"] == "review"
    assert score_public_institution_address(
        address_pattern=True,
        footer_contact_context=True,
    )["action"] == "review"


def test_prereview_routing_requires_title_contest_label_and_independent_approval_structure():
    text = "사전검토서\n공모명 지역혁신사업\n주무관 홍길동 과장 김철수"
    words = _word_entries(text, [
        ("사전검토서", 80.0),
        ("공모명", 130.0),
        ("지역혁신사업", 130.0),
        ("주무관", 260.0),
        ("홍길동", 260.0),
        ("과장", 260.0),
        ("김철수", 260.0),
    ])
    entries = [
        (
            word.page_start,
            word.page_end,
            word,
            {"x0": word.bbox[0], "y0": word.bbox[1], "x1": word.bbox[2], "y1": word.bbox[3]},
        )
        for word in words
    ]
    layout = type("Layout", (), {
        "values": (
            type("Value", (), {"kind": "approval_staff"})(),
            type("Value", (), {"kind": "approval_staff"})(),
        ),
    })()
    assert _prereview_routing_signal(entries, layout)
    assert not _prereview_routing_signal(entries[:1] + entries[3:], layout)
    assert _signal_kinds({"prereview", "dispatch"}) == {"internal_review"}


def test_prereview_signal_routes_manifest_to_internal_review_without_common_only_downgrade():
    text = "사전검토서\n공모명 지역혁신사업\n주무관 홍길동 과장 김철수"
    words = _word_entries(text, [
        ("사전검토서", 80.0),
        ("공모명", 130.0),
        ("지역혁신사업", 130.0),
        ("주무관", 260.0),
        ("홍길동", 260.0),
        ("과장", 260.0),
        ("김철수", 260.0),
    ])
    extracted = ExtractResult(
        text=text,
        engine_used="fixture",
        duration_sec=0.0,
        notes=[],
        pages=(ExtractedPage(0, 612.0, 792.0, text, words, source="pymupdf_text_layer"),),
    )
    detector = type("Detector", (), {"detect": lambda _self, _text: []})()
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "prereview-routing.pdf"
        source.write_bytes(b"%PDF-1.7\nprereview-routing-fixture")
        with (
            patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
            patch(
                "document_masker_ocr_gui.occurrence_rect_text_hash",
                side_effect=lambda _path, _page, rects: _word_hash(words, rects),
            ),
        ):
            manifest = trusted_analysis_manifest(
                str(source),
                {"profile": "mixed", "auto_threshold": 0.85, "review_threshold": 0.5},
                session_hash_key=SESSION_HASH_KEY,
                extracted=extracted,
            )

    assert [(segment["kind"], segment["common_only"]) for segment in manifest["segments"]] == [
        ("internal_review", False),
    ]
    assert sum(item["category"] == "approval_staff" for item in manifest["occurrences"]) >= 2


def test_public_manifest_emits_new_candidates_and_text_email_fallback():
    text = (
        "본문 서울특별시 동작구\n"
        "주식회사 한빛\n"
        "우편번호 03718 주소 서울특별시 동작구 샘플로 1\n"
        "이메일 privacy@example.go.kr\n"
        "시행 총무과-1234"
    )
    words = _word_entries(text, [
        ("본문", 100.0),
        ("서울특별시", 100.0),
        ("동작구", 100.0),
        ("주식회사", 130.0),
        ("한빛", 130.0),
        ("우편번호", 680.0),
        ("03718", 680.0),
        ("주소", 680.0),
        ("서울특별시", 680.0),
        ("동작구", 680.0),
        ("샘플로", 680.0),
        ("1", 680.0),
        ("이메일", 705.0),
        ("privacy@example.go.kr", 705.0),
        ("시행", 735.0),
        ("총무과-1234", 735.0),
    ])
    extracted = ExtractResult(
        text=text,
        engine_used="fixture",
        duration_sec=0.0,
        notes=[],
        pages=(ExtractedPage(
            0,
            612.0,
            792.0,
            text,
            words,
            source="pymupdf_text_layer",
        ),),
    )
    detector = type("Detector", (), {"detect": lambda _self, _text: []})()
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "public-candidates.pdf"
        source.write_bytes(b"%PDF-1.7\npublic-candidates-fixture")
        with (
            patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
            patch(
                "document_masker_ocr_gui.occurrence_rect_text_hash",
                side_effect=lambda _path, _page, rects: _word_hash(words, rects),
            ),
        ):
            manifest = trusted_analysis_manifest(
                str(source),
                {
                    "profile": "mixed",
                    "auto_threshold": 0.85,
                    "review_threshold": 0.5,
                },
                session_hash_key=SESSION_HASH_KEY,
                extracted=extracted,
            )

    categories = [item["category"] for item in manifest["occurrences"]]
    assert "region_name" in categories
    assert "institution_value" in categories
    assert "institution_address" in categories
    assert "dispatch_metadata" in categories
    assert "footer_contact" in categories
    email_rect = {
        "x0": words[13].bbox[0],
        "y0": words[13].bbox[1],
        "x1": words[13].bbox[2],
        "y1": words[13].bbox[3],
    }
    assert any(
        item["category"] in {"email", "footer_contact"}
        and item["rects"] == [email_rect]
        and item["proposed_action"] == "mask"
        and item["state"] == "confirmed"
        for item in manifest["occurrences"]
    )
    assert any(
        item["category"] == "region_name"
        and item["proposed_action"] == "review"
        and review["target_id"] == item["occurrence_id"]
        for item in manifest["occurrences"]
        for review in manifest["review_items"]
    )


def test_footer_dispatch_occurrence_reaches_native_redaction(tmp_path):
    source = tmp_path / "dispatch-footer.pdf"
    output = tmp_path / "dispatch-footer-masked.pdf"
    document = pymupdf.open()
    try:
        page = document.new_page(width=595.0, height=842.0)
        font = pymupdf.Font(fontname="cjk")
        page.insert_font(fontname="qa_cjk", fontbuffer=font.buffer)
        page.insert_text((60.0, 700.0), "시행 총무과-1234", fontname="qa_cjk", fontsize=12.0)
        document.save(source)
    finally:
        document.close()

    document_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = trusted_analysis_manifest(
        str(source),
        {
            "profile": "official_dispatch",
            "auto_threshold": 0.85,
            "review_threshold": 0.5,
            "profile_authority": {
                "document_sha256": document_hash,
                "analysis_revision": 1,
                "profile": "official_dispatch",
                "decision_code": "profile_confirmed",
            },
        },
        session_hash_key=SESSION_HASH_KEY,
    )
    occurrence = next(
        item for item in manifest["occurrences"]
        if item["category"] == "dispatch_metadata"
        and item["source"] == "public_footer_dispatch_metadata"
    )
    request = OccurrenceRedactionInput(
        occurrence_id=occurrence["occurrence_id"],
        run_id="t61-native-redaction",
        document_sha256=document_hash,
        analysis_revision=1,
        page_index=occurrence["page"],
        rect_list=tuple(
            tuple(rect[key] for key in ("x0", "y0", "x1", "y1"))
            for rect in occurrence["rects"]
        ),
        action="mask",
        provenance="public_footer_dispatch_metadata",
        expected_text_hash=occurrence["expected_text_hash"],
    )
    result = redact_pdf_native(
        str(source),
        str(output),
        occurrence_inputs=[request],
        expected_run_id="t61-native-redaction",
        expected_document_sha256=document_hash,
        expected_analysis_revision=1,
    )

    assert result["status"] == "applied"
    assert result["occurrences_applied"] == 1
    with pymupdf.open(output) as masked:
        assert "총무과-1234" not in masked[0].get_text("text")
        assert "시행" in masked[0].get_text("text")
