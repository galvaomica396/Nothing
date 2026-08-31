"""Geometry-only detection for Korean approval tables and document metadata.

The detector deliberately returns rectangles for values separately from protected
labels, roles, dates, and approval-state cells.  It accepts extractor word objects
with ``text`` and ``bbox`` attributes and never infers geometry from plain text.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Iterable, Mapping, Sequence

from masking_extraction import ruled_table_cells
from privacy_false_positive import is_likely_approval_person_name_value

COVERAGE_STATES = frozenset({"present", "absent", "indeterminate"})
INTERNAL_REQUIRED_KINDS = ("approval", "header_meta", "labeled_staff")
DISPATCH_REQUIRED_KINDS = (
    "recipient_reference", "sender_institution", "approval_staff",
    "dispatch_metadata", "footer_contact",
)

_ROLE = re.compile(
    r"(?:주무관|사무관|서기관|부이사관|이사관|관리관|지방시설서기보|지방시설서기|"
    r"사무주사보|사무주사|주사보|주사|서기보|서기|팀장|과장|국장|실장|센터장|"
    r"계장|본부장|부시장|시장|부구청장|구청장|담당자|담당|"
    r"(?:행정|시설|건축|토목|전산|세무|사회복지|보건|환경|녹지|기계|전기|화공|"
    r"농업|임업|해양수산|지적|사서|간호|의료기술|운전|방호|통신|방재안전)\s*[1-9Bb]\s*[급긍금])"
)
_NAME = re.compile(r"[가-힣]{2,4}\Z")
_DATE = re.compile(r"(?:\d{1,2}\s*/\s*\d{1,2}|(?:19|20)\d{2}[.\-/]\s*\d{1,2}[.\-/]\s*\d{1,2}\.?)\Z")
_STATUS = re.compile(r"(?:전\s*결|대\s*결|결\s*재|승\s*인|권한\s*대행|代\s*決|專\s*決)\Z")
_APPROVAL_LABEL = re.compile(r"(?:결재|검토|승인|기안|협조자?)")
_DISCLOSURE = re.compile(r"(?:대시민\s*공개|시민\s*공개|전부\s*공개|부분\s*공개|비\s*공개|공개\s*여부|공개\s*구분)")
_DOC_LABEL = re.compile(r"(?:생산\s*등록\s*번호|문서\s*번호)")
_DOC_VALUE = re.compile(r"[가-힣A-Za-z0-9][가-힣A-Za-z0-9_.]*(?:[-–—][가-힣A-Za-z0-9_.-]+)+")
_LABEL_VALUE_SAME_ROW_SCORE = 1
_LABEL_VALUE_DISTANCE_SCORE = 2
_STRUCTURAL_ALIGNMENT_DISTANCE_MAX = 200.0
_LABEL_VALUE_ALIGNMENT_SCORE = _LABEL_VALUE_SAME_ROW_SCORE + _LABEL_VALUE_DISTANCE_SCORE
_COLUMN_OVERLAP_MINIMUM = 0.5
_TABLE_BOUNDARY_TOLERANCE = 1.5
_APPROVAL_EXPECTED_TOP_RATIO = 0.35

# Real 협조 rows measure 91–100pt from role label to signer, while column-stacked
# approval roles remain at a measured maximum of 52.8pt.
APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX = 110.0

Rect = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class LayoutValue:
    kind: str
    page_index: int
    value_rects: tuple[Rect, ...]
    protected_neighbor_rects: tuple[Rect, ...]
    value_text: str
    source: str = "structural_approval_layout"
    label_value_distance: float | None = None
    box_structure_match: bool = False
    approval_row_pattern: bool = False


@dataclass(frozen=True, slots=True)
class ApprovalLayoutResult:
    values: tuple[LayoutValue, ...]
    coverage: Mapping[str, str]

    def __post_init__(self) -> None:
        if any(state not in COVERAGE_STATES for state in self.coverage.values()):
            raise ValueError("LAYOUT_COVERAGE_INVALID")

    @property
    def blocks(self) -> bool:
        return "indeterminate" in self.coverage.values()


def _rect(word: Any) -> Rect | None:
    try:
        values = tuple(float(value) for value in word.bbox)
    except (AttributeError, TypeError, ValueError):
        return None
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        return None
    x0, y0, x1, y1 = values
    return values if 0 <= x0 < x1 and 0 <= y0 < y1 else None


def subword_rect(word: Any, start: int, end: int) -> Rect:
    """Return exact linear PyMuPDF subword geometry for ``text[start:end]``."""
    text = str(word.text)
    rect = _rect(word)
    if rect is None or not text or not (0 <= start < end <= len(text)):
        raise ValueError("SUBWORD_GEOMETRY_INVALID")
    x0, y0, x1, y1 = rect
    unit = (x1 - x0) / len(text)
    return (x0 + unit * start, y0, x0 + unit * end, y1)


def _parts(word: Any, pattern: re.Pattern[str]) -> list[tuple[str, Rect]]:
    return [(match.group(), subword_rect(word, match.start(), match.end())) for match in pattern.finditer(str(word.text))]


def _approval_role_and_name_parts(
    word: Any, drawings: Sequence[Rect], page_rect: Rect | None,
) -> tuple[list[tuple[str, Rect]], list[tuple[str, Rect]]]:
    text = str(word.text).strip()
    matches = list(_ROLE.finditer(text))
    if not matches:
        return [], []
    if _ROLE.fullmatch(text):
        rect = _rect(word)
        return ([(text, rect)], []) if rect is not None else ([], [])
    role_parts: list[tuple[str, Rect]] = []
    name_parts: list[tuple[str, Rect]] = []
    matched_text = "".join(match.group() for match in matches)
    if matched_text == text:
        role_parts.extend((match.group(), subword_rect(word, match.start(), match.end())) for match in matches)
        return role_parts, name_parts
    for index, match in enumerate(matches):
        if index != len(matches) - 1:
            continue
        suffix_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        suffix = text[match.end():suffix_end]
        if not _NAME.fullmatch(suffix):
            continue
        role_parts.append((match.group(), subword_rect(word, match.start(), match.end())))
        name_parts.append((suffix, subword_rect(word, match.end(), suffix_end)))
    if role_parts:
        return role_parts, name_parts
    word_rect = _rect(word)
    cells = ruled_table_cells(drawings)
    if word_rect is not None and any(_drawing_contains(word_rect, cell) for cell in cells):
        role_parts.extend((match.group(), subword_rect(word, match.start(), match.end())) for match in matches)
    elif page_rect is not None:
        role_parts.extend(
            (match.group(), role_rect)
            for match in matches
            for role_rect in (subword_rect(word, match.start(), match.end()),)
            if _approval_position_expected(role_rect, page_rect)
            and any(_drawing_contains(role_rect, cell) for cell in cells)
        )
    return role_parts, name_parts


def _approval_position_expected(value_rect: Rect, page_rect: Rect | None) -> bool:
    if page_rect is None:
        return False
    page_height = page_rect[3] - page_rect[1]
    return page_height > 0 and value_rect[1] <= page_rect[1] + page_height * _APPROVAL_EXPECTED_TOP_RATIO


def _same_row(a: Rect, b: Rect) -> bool:
    ah, bh = a[3] - a[1], b[3] - b[1]
    return abs((a[1] + a[3] - b[1] - b[3]) / 2) <= max(4.0, min(ah, bh) * .65)


def _x_center(rect: Rect) -> float:
    return (rect[0] + rect[2]) / 2


def _label_value_alignment_score(label_rect: Rect, value_rect: Rect) -> int:
    if not _same_row(label_rect, value_rect):
        return 0
    distance = value_rect[0] - label_rect[2]
    return (
        _LABEL_VALUE_ALIGNMENT_SCORE
        if 0 <= distance <= _STRUCTURAL_ALIGNMENT_DISTANCE_MAX
        else _LABEL_VALUE_SAME_ROW_SCORE
    )


def _rect_distance(left: Rect, right: Rect) -> float:
    horizontal = max(left[0] - right[2], right[0] - left[2], 0.0)
    vertical = max(left[1] - right[3], right[1] - left[3], 0.0)
    return math.hypot(horizontal, vertical)


def _drawing_contains(rect: Rect, drawing: Rect) -> bool:
    left, top, right, bottom = drawing
    return (
        left - 1.0 <= rect[0] <= rect[2] <= right + 1.0
        and top - 1.0 <= rect[1] <= rect[3] <= bottom + 1.0
    )


def _drawing_supports(value_rect: Rect, related_rects: Sequence[Rect], drawings: Sequence[Rect]) -> bool:
    for drawing in drawings:
        if _drawing_contains(value_rect, drawing) and any(
            _drawing_contains(related_rect, drawing) for related_rect in related_rects
        ):
            return True
    return False


def _substantial_horizontal_overlap(left: Rect, right: Rect) -> bool:
    overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    return overlap / min(left[2] - left[0], right[2] - right[0]) >= _COLUMN_OVERLAP_MINIMUM


def _same_table_cells(upper: Rect, lower: Rect) -> bool:
    return (
        _substantial_horizontal_overlap(upper, lower)
        and abs(upper[3] - lower[1]) <= _TABLE_BOUNDARY_TOLERANCE
    )


def _column_table_supports(value_rect: Rect, related_rects: Sequence[Rect], drawings: Sequence[Rect]) -> bool:
    cells = ruled_table_cells(drawings)
    for value_cell in cells:
        if not _drawing_contains(value_rect, value_cell):
            continue
        for related_rect in related_rects:
            if any(
                _drawing_contains(related_rect, related_cell)
                and _same_table_cells(related_cell, value_cell)
                for related_cell in cells
            ):
                return True
    return False


def _dedupe_rects(rects: Iterable[Rect]) -> tuple[Rect, ...]:
    return tuple(dict.fromkeys(rects))


def analyze_approval_layout(
    words: Sequence[Any], *, page_index: int = 0, drawings: Sequence[Rect] = (), page_rect: Rect | None = None,
) -> ApprovalLayoutResult:
    """Detect structurally grounded signer and concatenated document-number values."""
    if isinstance(page_index, bool) or not isinstance(page_index, int) or page_index < 0:
        raise ValueError("PAGE_INDEX_INVALID")
    if page_rect is not None and (
        len(page_rect) != 4
        or not all(math.isfinite(value) for value in page_rect)
        or page_rect[0] >= page_rect[2]
        or page_rect[1] >= page_rect[3]
    ):
        raise ValueError("PAGE_RECT_INVALID")
    valid = [(word, _rect(word)) for word in words]
    valid = [(word, rect) for word, rect in valid if rect is not None and str(word.text)]
    vector_drawings = tuple(
        rect for rect in drawings
        if len(rect) == 4 and all(math.isfinite(value) for value in rect)
    )
    coverage = {kind: "absent" for kind in (*INTERNAL_REQUIRED_KINDS, *DISPATCH_REQUIRED_KINDS)}
    values: list[LayoutValue] = []

    # Concatenated labels are one PyMuPDF word. Split by character position so the
    # label remains untouched and only the number's subword rectangle is emitted.
    for word, rect in valid:
        compact = re.sub(r"\s+", "", str(word.text))
        label = _DOC_LABEL.match(compact)
        if label is None:
            continue
        value = _DOC_VALUE.fullmatch(compact[label.end():])
        if value is None:
            coverage["header_meta"] = "indeterminate"
            coverage["dispatch_metadata"] = "indeterminate"
            continue
        value_start = len(str(word.text)) - len(compact[label.end():])
        value_rect = subword_rect(word, value_start, len(str(word.text)))
        label_rect = subword_rect(word, 0, value_start)
        values.append(LayoutValue(
            "header_meta", page_index, (value_rect,), (label_rect,), compact[label.end():],
            "pymupdf_subword", 0.0, _drawing_supports(value_rect, (label_rect,), vector_drawings),
        ))
        values.append(LayoutValue(
            "dispatch_metadata", page_index, (value_rect,), (label_rect,), compact[label.end():],
            "pymupdf_subword", 0.0, _drawing_supports(value_rect, (label_rect,), vector_drawings),
        ))
        coverage["header_meta"] = "present"
        coverage["dispatch_metadata"] = "present"

    role_parts: list[tuple[str, Rect]] = []
    contextual_role_parts: list[tuple[str, Rect]] = []
    label_rects: list[Rect] = []
    name_parts: list[tuple[str, Rect]] = []
    compact_name_rects: set[Rect] = set()
    protected: list[Rect] = []
    approval_seen = False
    for word, rect in valid:
        text = str(word.text).strip()
        if _DISCLOSURE.search(text):
            protected.append(rect)
            continue
        roles, compact_names = _approval_role_and_name_parts(word, vector_drawings, page_rect)
        if roles:
            role_parts.extend(roles)
            protected.extend(part_rect for _, part_rect in roles)
            approval_seen = True
        else:
            contextual_role_parts.extend(_parts(word, _ROLE))
        if _APPROVAL_LABEL.fullmatch(text):
            protected.append(rect)
            label_rects.append(rect)
            approval_seen = True
        if _DATE.fullmatch(text) or _STATUS.fullmatch(text):
            protected.append(rect)
        # A standalone name is accepted only after structural column alignment.
        if (
            _NAME.fullmatch(text)
            and not _STATUS.fullmatch(text)
            and not _DISCLOSURE.search(text)
            and not roles
            and not _ROLE.search(text)
            and not _APPROVAL_LABEL.fullmatch(text)
        ):
            name_parts.append((text, rect))
        name_parts.extend(compact_names)
        compact_name_rects.update(name_rect for _, name_rect in compact_names)

    name_parts = [
        (name, name_rect) for name, name_rect in name_parts
        if is_likely_approval_person_name_value(name)
    ]

    contextual_row_roles = [
        role_part for role_part in contextual_role_parts
        if any(
            _same_row(role_part[1], name_rect)
            and role_part[1][2] <= name_rect[0]
            and name_rect[0] - role_part[1][2] <= APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX
            for _, name_rect in name_parts
        )
    ]
    row_role_count = sum(
        any(
            _same_row(role_rect, name_rect)
            and role_rect[2] <= name_rect[0]
            and name_rect[0] - role_rect[2] <= APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX
            for _, name_rect in name_parts
        )
        for _, role_rect in (*role_parts, *contextual_row_roles)
    )
    if row_role_count >= 2:
        role_parts.extend(contextual_row_roles)
        protected.extend(part_rect for _, part_rect in contextual_row_roles)
        approval_seen = approval_seen or bool(contextual_row_roles)

    potential_signer_values: list[tuple[str, Rect, tuple[Rect, ...], bool, bool, bool, bool]] = []
    for name, name_rect in name_parts:
        column_aligned = [role_rect for _, role_rect in role_parts if
                          abs(_x_center(role_rect) - _x_center(name_rect)) <= max(role_rect[2] - role_rect[0], name_rect[2] - name_rect[0])
                          and (name_rect[1] >= role_rect[1] or _same_row(role_rect, name_rect))]
        row_aligned = [role_rect for _, role_rect in role_parts if
                       _same_row(role_rect, name_rect) and role_rect[2] <= name_rect[0]]
        label_aligned = tuple(
            label_rect for label_rect in label_rects
            if _label_value_alignment_score(label_rect, name_rect) >= _LABEL_VALUE_ALIGNMENT_SCORE
        )
        related_rects = tuple((*column_aligned, *row_aligned, *label_aligned))
        if related_rects:
            # Alignment admits a signer candidate; box evidence needs one drawing
            # to contain that signer and its corresponding role or label cell.
            column_table_match = _column_table_supports(name_rect, related_rects, vector_drawings)
            box_structure_match = _drawing_supports(name_rect, related_rects, vector_drawings) or column_table_match
            potential_signer_values.append((
                name, name_rect, related_rects, bool(row_aligned), box_structure_match,
                column_table_match, name_rect in compact_name_rects,
            ))

    strong_approval_row_rects = {
        name_rect
        for _, name_rect, _, approval_row_pattern, _, _, _ in potential_signer_values
        if approval_row_pattern
        and sum(
            other_row_pattern and _same_row(name_rect, other_name_rect)
            for _, other_name_rect, _, other_row_pattern, _, _, _ in potential_signer_values
        ) >= 2
    }
    signer_values = [
        (name, name_rect, related_rects, approval_row_pattern, box_structure_match or (
            name_rect in strong_approval_row_rects
        ))
        for name, name_rect, related_rects, approval_row_pattern, box_structure_match, column_table_match, is_compact_name
        in potential_signer_values
        if page_rect is None
        or column_table_match
        or name_rect in strong_approval_row_rects
        or _approval_position_expected(name_rect, page_rect)
        or is_compact_name
    ]

    if signer_values:
        all_protected = _dedupe_rects(protected)
        for name, name_rect, related_rects, approval_row_pattern, box_structure_match in signer_values:
            # This nearest-protected-cell Euclidean distance is a row-cohesion proxy,
            # distinct from the GUI's horizontal-only label-to-value gap; both are
            # scored against EVIDENCE_LABEL_VALUE_DISTANCE_MAX.
            label_value_distance = (
                min(_rect_distance(protected_rect, name_rect) for protected_rect in all_protected)
                if all_protected else None
            )
            values.append(LayoutValue(
                "approval_staff", page_index, (name_rect,), all_protected, name,
                label_value_distance=label_value_distance,
                box_structure_match=box_structure_match,
                approval_row_pattern=approval_row_pattern,
            ))
        coverage["approval"] = "present"
        coverage["approval_staff"] = "present"
    elif approval_seen:
        coverage["approval"] = "indeterminate"
        coverage["approval_staff"] = "indeterminate"

    return ApprovalLayoutResult(tuple(values), coverage)
