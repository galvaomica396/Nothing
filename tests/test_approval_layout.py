from __future__ import annotations

from dataclasses import dataclass

import pytest

from approval_layout import analyze_approval_layout, subword_rect
from masking_extraction import ExtractedWord, reconstruct_table_cell_words
from masking_redaction import automatic_masks_preserve_manual_neighbors
from masking_rules import EMAIL_PAT


@dataclass(frozen=True)
class Word:
    text: str
    bbox: tuple[float, float, float, float]


def test_split_role_columns_return_value_only_signer_geometry() -> None:
    words = [
        Word("결재", (0, 5, 20, 15)),
        Word("주무관", (40, 10, 70, 20)),
        Word("팀장", (100, 10, 130, 20)),
        Word("과장", (160, 10, 190, 20)),
        Word("김철수", (40, 25, 70, 35)),
        Word("이영희", (100, 25, 130, 35)),
        Word("박민수", (160, 25, 190, 35)),
        Word("04/30", (160, 40, 190, 50)),
        Word("전결", (160, 55, 190, 65)),
    ]

    result = analyze_approval_layout(words, drawings=((30.0, 5.0, 200.0, 70.0),))

    signer_values = [value for value in result.values if value.kind == "approval_staff"]
    assert [value.value_text for value in signer_values] == ["김철수", "이영희", "박민수"]
    assert [value.value_rects[0] for value in signer_values] == [word.bbox for word in words[4:7]]
    assert all(value.value_rects[0] not in value.protected_neighbor_rects for value in signer_values)
    assert all(value.box_structure_match for value in signer_values)
    assert result.coverage["approval"] == "present"


def test_approval_values_require_vector_box_or_ruled_row_evidence() -> None:
    words = [
        Word("결재", (0, 5, 20, 15)),
        Word("주무관", (40, 10, 70, 20)),
        Word("김철수", (40, 25, 70, 35)),
    ]

    without_drawing = analyze_approval_layout(words)
    with_crossing_line = analyze_approval_layout(words, drawings=((30.0, 30.0, 80.0, 30.0),))
    with_drawing = analyze_approval_layout(words, drawings=((30.0, 5.0, 80.0, 40.0),))

    assert not without_drawing.values[0].box_structure_match
    assert not with_crossing_line.values[0].box_structure_match
    assert with_drawing.values[0].box_structure_match


def test_role_alignment_without_row_cohesive_drawing_does_not_supply_box_evidence() -> None:
    # Given: a role-aligned signer whose drawing contains only the signer value.
    words = [
        Word("결재", (10, 5, 30, 15)),
        Word("주무관", (40, 10, 70, 20)),
        Word("김철수", (40, 25, 70, 35)),
    ]

    # When: the signer is found from role alignment but the role cell is outside the drawing.
    result = analyze_approval_layout(words, drawings=((35.0, 22.0, 75.0, 38.0),))

    # Then: role alignment remains a candidate signal, not box evidence by itself.
    assert not result.values[0].box_structure_match


def test_approval_box_evidence_applies_only_to_values_inside_the_box() -> None:
    words = [
        Word("결재", (0, 5, 20, 15)),
        Word("주무관", (40, 10, 70, 20)),
        Word("김철수", (40, 25, 70, 35)),
        Word("팀장", (120, 10, 150, 20)),
        Word("이영희", (120, 25, 150, 35)),
    ]

    result = analyze_approval_layout(words, drawings=((30.0, 5.0, 80.0, 40.0),))

    box_matches = {
        value.value_text: value.box_structure_match
        for value in result.values
        if value.kind == "approval_staff"
    }
    assert box_matches == {"김철수": True, "이영희": False}


def test_column_stacked_approval_box_requires_a_ruled_table() -> None:
    # Given: role cells above signer cells in adjacent rows of a three-column grid.
    words = [
        Word("결재", (5, 10, 25, 20)),
        Word("주무관", (40, 10, 70, 20)),
        Word("팀장", (80, 10, 110, 20)),
        Word("김철수", (40, 35, 70, 45)),
        Word("이영희", (80, 35, 110, 45)),
    ]
    horizontal_rules = (
        (30.0, 5.0, 120.0, 5.0),
        (30.0, 25.0, 120.0, 25.0),
        (30.0, 50.0, 120.0, 50.0),
    )
    vertical_rules = (
        (30.0, 5.0, 30.0, 50.0),
        (75.0, 5.0, 75.0, 50.0),
        (120.0, 5.0, 120.0, 50.0),
    )

    # When: the table has both rule directions, then when it has only horizontal rules.
    ruled = analyze_approval_layout(words, drawings=(*horizontal_rules, *vertical_rules))
    horizontal_only = analyze_approval_layout(words, drawings=horizontal_rules)
    letterhead_only = analyze_approval_layout(
        words,
        drawings=((0.0, 5.0, 612.0, 5.0), (0.0, 50.0, 612.0, 50.0)),
    )

    # Then: only the bounded grid supplies column-stacked box evidence.
    assert all(value.box_structure_match for value in ruled.values if value.kind == "approval_staff")
    assert not any(value.box_structure_match for value in horizontal_only.values if value.kind == "approval_staff")
    assert not any(value.box_structure_match for value in letterhead_only.values if value.kind == "approval_staff")


def test_concatenated_roles_are_split_into_columns_and_disclosure_is_excluded() -> None:
    words = [
        Word("주무관팀장과장", (30, 10, 150, 20)),
        Word("김철수", (30, 25, 60, 35)),
        Word("이영희", (75, 25, 105, 35)),
        Word("박민수", (120, 25, 150, 35)),
        Word("대시민공개", (160, 25, 220, 35)),
    ]

    result = analyze_approval_layout(words)

    assert [item.value_text for item in result.values] == ["김철수", "이영희", "박민수"]
    assert "대시민공개" not in [item.value_text for item in result.values]


def test_merged_role_word_is_reconstructed_per_table_cell_for_column_evidence() -> None:
    # Given: a text-layer word that spans three ruled role cells and its raw character boxes.
    role_prefix = (
        ExtractedWord("주", (32.0, 10.0, 40.0, 20.0)),
        ExtractedWord("무", (42.0, 10.0, 50.0, 20.0)),
    )
    role_word = ExtractedWord("관팀장과장", (52.0, 10.0, 120.0, 20.0))
    names = (
        ExtractedWord("김철수", (35.0, 35.0, 55.0, 45.0)),
        ExtractedWord("이영희", (65.0, 35.0, 85.0, 45.0)),
        ExtractedWord("박민수", (95.0, 35.0, 115.0, 45.0)),
    )
    horizontal_rules = (
        (30.0, 5.0, 120.0, 5.0),
        (30.0, 25.0, 120.0, 25.0),
        (30.0, 50.0, 120.0, 50.0),
    )
    vertical_rules = (
        (30.0, 5.0, 30.0, 50.0),
        (60.0, 5.0, 60.0, 50.0),
        (90.0, 5.0, 90.0, 50.0),
        (120.0, 5.0, 120.0, 50.0),
    )
    chars = tuple(
        (character, (x0, 10.0, x0 + 8.0, 20.0))
        for character, x0 in zip("주무관팀장과장", (32.0, 42.0, 52.0, 65.0, 75.0, 95.0, 105.0))
    )

    # When: the extraction layer reconstructs only table-spanning words.
    words = reconstruct_table_cell_words(
        (*role_prefix, role_word, *names), (*horizontal_rules, *vertical_rules), chars,
    )
    result = analyze_approval_layout(words, drawings=(*horizontal_rules, *vertical_rules))

    # Then: each cell supplies a role label and its signer has column box evidence.
    assert [word.text for word in words[:3]] == ["주무관", "팀장", "과장"]
    signer_values = [value for value in result.values if value.kind == "approval_staff"]
    assert [value.value_text for value in signer_values] == ["김철수", "이영희", "박민수"]
    assert all(value.box_structure_match for value in signer_values)


def test_table_cell_reconstruction_does_not_split_words_outside_ruled_tables() -> None:
    word = ExtractedWord("주무관팀장", (30.0, 10.0, 90.0, 20.0))
    chars = tuple(
        (character, (30.0 + index * 10.0, 10.0, 40.0 + index * 10.0, 20.0))
        for index, character in enumerate(word.text)
    )

    reconstructed = reconstruct_table_cell_words((word,), (), chars)

    assert reconstructed == (word,)


def test_approval_row_pattern_preserves_a_100_point_role_to_name_gap() -> None:
    result = analyze_approval_layout(
        [
            Word("협조", (270.0, 10.0, 290.0, 20.0)),
            Word("주무관", (301.0, 10.0, 330.0, 20.0)),
            Word("신나현", (430.0, 10.0, 461.0, 20.0)),
            Word("김철수", (301.0, 35.0, 330.0, 45.0)),
        ],
        drawings=((270.0, 5.0, 470.0, 25.0),),
    )

    signer = next(value for value in result.values if value.value_text == "신나현")

    assert signer.label_value_distance == 100.0
    assert signer.approval_row_pattern


def test_concatenated_document_number_preserves_label_subword() -> None:
    word = Word("생산등록번호건축과-1526", (10, 10, 130, 20))

    result = analyze_approval_layout([word])

    header = next(value for value in result.values if value.kind == "header_meta")
    label_end = len("생산등록번호")
    assert header.value_rects == (subword_rect(word, label_end, len(word.text)),)
    assert header.protected_neighbor_rects == (subword_rect(word, 0, label_end),)
    assert header.value_text == "건축과-1526"
    assert not header.box_structure_match


def test_approval_marker_without_structural_signer_is_indeterminate() -> None:
    result = analyze_approval_layout([Word("결재", (10, 10, 30, 20))])
    assert result.blocks
    assert result.coverage["approval"] == "indeterminate"


def test_body_role_fragment_with_accidentally_aligned_words_emits_no_approval_candidate() -> None:
    # Given: prose in the middle of a page happens to align like an approval column.
    words = [
        Word("감사팀장", (220.0, 555.0, 269.0, 565.0)),
        Word("목적", (220.0, 590.0, 254.0, 600.0)),
        Word("사진", (220.0, 630.0, 254.0, 640.0)),
    ]

    # When: approval geometry is analyzed with the real page boundary.
    result = analyze_approval_layout(words, page_rect=(0.0, 0.0, 612.0, 792.0))

    # Then: an embedded role word cannot mint a profile candidate or review card.
    assert not [value for value in result.values if value.kind == "approval_staff"]
    assert result.coverage["approval"] == "absent"


def test_box_backed_independent_role_cell_keeps_approval_candidate() -> None:
    # Given: a ruled approval grid with an independently labelled role cell.
    words = [
        Word("결", (10.0, 10.0, 20.0, 20.0)),
        Word("재", (10.0, 22.0, 20.0, 32.0)),
        Word("일", (10.0, 34.0, 20.0, 44.0)),
        Word("팀장", (40.0, 10.0, 70.0, 20.0)),
        Word("김철수", (40.0, 30.0, 70.0, 40.0)),
    ]
    rules = (
        (30.0, 5.0, 80.0, 5.0), (30.0, 25.0, 80.0, 25.0), (30.0, 45.0, 80.0, 45.0),
        (30.0, 5.0, 30.0, 45.0), (80.0, 5.0, 80.0, 45.0),
    )

    # When: the grid is evaluated as an approval layout.
    result = analyze_approval_layout(words, drawings=rules, page_rect=(0.0, 0.0, 612.0, 792.0))

    # Then: the signer remains a boxed approval candidate.
    signer = next(value for value in result.values if value.kind == "approval_staff")
    assert ("김철수", True) == (signer.value_text, signer.box_structure_match)


def test_boxed_compound_role_label_keeps_its_aligned_signer() -> None:
    words = [
        Word("보육행정팀장", (40.0, 10.0, 100.0, 20.0)),
        Word("김철수", (80.0, 30.0, 110.0, 40.0)),
    ]
    rules = (
        (30.0, 5.0, 110.0, 5.0), (30.0, 25.0, 110.0, 25.0), (30.0, 45.0, 110.0, 45.0),
        (30.0, 5.0, 30.0, 45.0), (110.0, 5.0, 110.0, 45.0),
    )

    result = analyze_approval_layout(words, drawings=rules, page_rect=(0.0, 0.0, 612.0, 792.0))

    signer = next(value for value in result.values if value.kind == "approval_staff")
    assert ("김철수", True) == (signer.value_text, signer.box_structure_match)


def test_repeated_dispatch_rows_do_not_mask_compound_role_text_as_a_name() -> None:
    words = [
        Word("주무관", (40.0, 700.0, 70.0, 710.0)),
        Word("노하정", (100.0, 700.0, 130.0, 710.0)),
        Word("의사팀장", (180.0, 700.0, 220.0, 710.0)),
        Word("전순일", (250.0, 700.0, 280.0, 710.0)),
    ]

    result = analyze_approval_layout(words, page_rect=(0.0, 0.0, 612.0, 792.0))

    assert [value.value_text for value in result.values if value.kind == "approval_staff"] == ["노하정", "전순일"]


def test_concatenated_department_role_and_name_splits_only_the_name_value() -> None:
    # Given: a dispatch footer row stores department, role, and name in one token.
    word = Word("지역건강과장이병삼", (40.0, 700.0, 130.0, 710.0))

    # When: it is paired with its boxed approval row.
    result = analyze_approval_layout(
        [word],
        drawings=((30.0, 695.0, 140.0, 715.0),),
        page_rect=(0.0, 0.0, 612.0, 792.0),
    )

    # Then: only the name suffix is a masking value; its role stays protected.
    signer = next(value for value in result.values if value.kind == "approval_staff")
    assert signer.value_text == "이병삼"
    assert signer.value_rects == (subword_rect(word, len("지역건강과장"), len(word.text)),)


def test_top_boxed_fragmented_role_tokens_restore_their_column_signers() -> None:
    # Given: PDF word segmentation attaches prose fragments to the role-cell text.
    words = [
        Word("관조경팀장공원녹지과", (100.0, 10.0, 200.0, 20.0)),
        Word("부구청장구", (220.0, 10.0, 270.0, 20.0)),
        Word("임도현", (130.0, 35.0, 160.0, 45.0)),
        Word("차민규", (225.0, 35.0, 255.0, 45.0)),
    ]
    rules = (
        (95.0, 5.0, 275.0, 5.0), (95.0, 25.0, 275.0, 25.0), (95.0, 50.0, 275.0, 50.0),
        (95.0, 5.0, 95.0, 50.0), (150.0, 5.0, 150.0, 50.0), (205.0, 5.0, 205.0, 50.0),
        (260.0, 5.0, 260.0, 50.0), (275.0, 5.0, 275.0, 50.0),
    )

    # When: role substrings are independently contained by upper approval cells.
    result = analyze_approval_layout(
        words, drawings=rules, page_rect=(0.0, 0.0, 612.0, 792.0),
    )

    # Then: fragmented labels restore only their column-aligned signer values.
    assert [value.value_text for value in result.values if value.kind == "approval_staff"] == [
        "임도현", "차민규",
    ]


def test_approval_candidates_exclude_document_words_despite_box_and_role_evidence() -> None:
    # Given: title/body terms happen to share an approval-table column and box.
    words = [
        Word("과장", (40.0, 10.0, 70.0, 20.0)),
        Word("김철수", (40.0, 30.0, 70.0, 40.0)),
        Word("공사기간", (40.0, 50.0, 90.0, 60.0)), Word("정함", (40.0, 50.0, 90.0, 60.0)),
        Word("연장", (40.0, 70.0, 70.0, 80.0)), Word("최대", (40.0, 70.0, 70.0, 80.0)),
        Word("검토보고", (40.0, 90.0, 90.0, 100.0)), Word("지급한", (40.0, 90.0, 90.0, 100.0)),
        Word("의원발의", (40.0, 110.0, 90.0, 120.0)), Word("장학금", (40.0, 110.0, 90.0, 120.0)),
        Word("보고", (40.0, 130.0, 70.0, 140.0)), Word("로서", (40.0, 130.0, 70.0, 140.0)),
        Word("드림", (40.0, 150.0, 70.0, 160.0)), Word("주어진", (40.0, 150.0, 70.0, 160.0)),
        Word("권한을", (40.0, 150.0, 70.0, 160.0)),
    ]

    # When: every value has otherwise-valid approval geometry.
    result = analyze_approval_layout(
        words, drawings=((30.0, 5.0, 100.0, 170.0),), page_rect=(0.0, 0.0, 612.0, 792.0),
    )

    # Then: only a surname-shaped person value reaches automatic approval masking.
    assert [value.value_text for value in result.values if value.kind == "approval_staff"] == ["김철수"]


def test_non_top_broad_box_does_not_confirm_a_column_only_name() -> None:
    # Given: a body value aligns under a role inside one broad drawing rectangle.
    words = [
        Word("과장", (40.0, 10.0, 70.0, 20.0)),
        Word("김철수", (40.0, 350.0, 70.0, 360.0)),
    ]

    # When: the rectangle lacks adjacent ruled cells or a repeated approval row.
    result = analyze_approval_layout(
        words, drawings=((30.0, 5.0, 100.0, 370.0),), page_rect=(0.0, 0.0, 612.0, 792.0),
    )

    # Then: broad drawing containment cannot turn body prose into approval masking.
    assert not [value for value in result.values if value.kind == "approval_staff"]


@pytest.mark.parametrize("email", ["user@example.go.kr", "user @ example . go . kr", "u.ser+tag @ example.com"])
def test_spaced_email_is_detected(email: str) -> None:
    match = EMAIL_PAT.fullmatch(email)
    assert match is not None
    assert match.group("value") == email


def test_automatic_masks_are_monotonic_against_manual_neighbors() -> None:
    assert automatic_masks_preserve_manual_neighbors([(10, 10, 20, 20)], [(20, 10, 30, 20)])
    assert not automatic_masks_preserve_manual_neighbors([(10, 10, 21, 20)], [(20, 10, 30, 20)])
    assert not automatic_masks_preserve_manual_neighbors([(10, 10, 10, 20)], [(20, 10, 30, 20)])
