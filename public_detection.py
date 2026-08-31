"""Geometry-backed candidates for public-document context values.

The ordinary ``ko_pii`` detector intentionally does not classify place or
institution names.  Public analysis still needs to expose those values, but
the candidate must remain tied to the same text-layer words that the trusted
finalizer will verify.  This module only proposes spans; it never redacts
text or invents geometry.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
import re
from typing import Any

from masking_rules import (
    COMPANY_NAME_PREFIXES,
    EMAIL_PAT,
    INSTITUTION_NAME_SUFFIXES,
    LOT_NO_PAT,
    PHONE_VALUE_PAT,
    PUBLIC_INSTITUTION_INLINE_PAT,
    PUBLIC_INSTITUTION_LABELS,
    ROAD_NO_PAT,
    SIHAENG_DOCNO_VALUE_PAT,
)
from privacy_detection import (
    score_public_institution_address,
    score_public_institution_value,
    score_public_region_value,
)


WordEntry = tuple[int, int, Any, dict[str, float]]

_REGION_CONTEXT_LABELS = frozenset({
    "소재지", "관할지역", "관할구역", "관할", "해당지역", "위치", "지역", "행정구역",
})
_PROFILE_VALUE_LABELS = frozenset({"수신", "수신자", "참조", "시행", "발신", "담당"})
_ADDRESS_LABELS = frozenset({
    "주소", "소재지", "거소", "사업장주소", "송달장소", "주민등록상주소", "본점소재지",
})
_INSTITUTION_ADDRESS_LABELS = frozenset({
    "기관주소", "기관소재지", "사업장주소", "본점소재지", "청사주소",
})
_FOOTER_LABELS = frozenset({"우편번호", "우", "전화", "전화번호", "대표전화", "전송", "팩스", "fax", "이메일", "email", "전자우편"})
_CONTACT_POSTAL_RE = re.compile(r"^우\s?\d{5}$")
_REGION_TERM_SUFFIXES = ("시", "군", "구", "읍", "면", "동", "도")
_NON_VALUE_WORDS = _FOOTER_LABELS | _REGION_CONTEXT_LABELS | _ADDRESS_LABELS | {
    "기관명", "법인명", "회사명", "상호", "업체명", "소속기관", "발신기관", "수신기관",
    "담당부서", "부서명",
}


@dataclass(frozen=True, slots=True)
class PublicCandidate:
    tag: str
    category: str
    source: str
    provenance: str
    start: int
    end: int
    value: str
    action: str
    score: float | None
    reason_codes: tuple[str, ...]


def _compact(value: str) -> str:
    return re.sub(r"[\s:：;,./]+", "", value).casefold()


def _line_groups(words: Sequence[WordEntry]) -> tuple[tuple[int, ...], ...]:
    ordered = sorted(
        range(len(words)),
        key=lambda index: (words[index][3]["y0"], words[index][3]["x0"], words[index][0]),
    )
    lines: list[list[int]] = []
    for index in ordered:
        rect = words[index][3]
        if lines:
            prior = words[lines[-1][-1]][3]
            tolerance = max(
                3.0,
                (prior["y1"] - prior["y0"]) / 2,
                (rect["y1"] - rect["y0"]) / 2,
            )
            if abs((prior["y0"] + prior["y1"] - rect["y0"] - rect["y1"]) / 2) <= tolerance:
                lines[-1].append(index)
                continue
        lines.append([index])
    return tuple(tuple(line) for line in lines)


def _label_windows(
    words: Sequence[WordEntry],
    line: Sequence[int],
    labels: frozenset[str],
) -> tuple[tuple[int, int, str], ...]:
    found: list[tuple[int, int, str]] = []
    for start in range(len(line)):
        compact = ""
        previous_rect: dict[str, float] | None = None
        for end in range(start, min(len(line), start + 4)):
            index = line[end]
            rect = words[index][3]
            if previous_rect is not None and (
                rect["x0"] - previous_rect["x1"] > max(24.0, 2 * (rect["y1"] - rect["y0"]))
            ):
                break
            compact += _compact(str(words[index][2].text))
            if compact in labels:
                found.append((start, end, compact))
                break
            previous_rect = rect
    return tuple(found)


def _value_indices_after_label(
    words: Sequence[WordEntry],
    line: Sequence[int],
    label_end: int,
) -> tuple[int, ...]:
    remaining = [
        index for index in line[label_end + 1:]
        if _compact(str(words[index][2].text)) not in {"", ":", "："}
    ]
    if not remaining:
        return ()
    first = remaining[0]
    first_value = _compact(str(words[first][2].text))
    if first_value in _NON_VALUE_WORDS:
        return ()
    selected = [first]
    if first_value in {_compact(item) for item in COMPANY_NAME_PREFIXES} or first_value in {"(주)", "㈜"}:
        if len(remaining) > 1:
            selected.append(remaining[1])
    elif not _has_institution_suffix(first_value):
        combined = first_value
        for index in remaining[1:3]:
            candidate = _compact(str(words[index][2].text))
            if candidate in _NON_VALUE_WORDS:
                break
            combined = f"{combined}{candidate}"
            selected.append(index)
            if _has_institution_suffix(combined):
                break
    return tuple(selected)


def _has_institution_suffix(value: str) -> bool:
    return any(value.endswith(suffix) for suffix in INSTITUTION_NAME_SUFFIXES)


def _span_indices(
    words: Sequence[WordEntry],
    start: int,
    end: int,
) -> tuple[int, ...]:
    selected = tuple(
        index for index, (word_start, word_end, _word, _rect) in enumerate(words)
        if word_start < end and word_end > start
    )
    if not selected:
        return ()
    if min(words[index][0] for index in selected) != start:
        return ()
    if max(words[index][1] for index in selected) != end:
        return ()
    return selected


def _line_for_indices(
    lines: Sequence[Sequence[int]],
    indices: Sequence[int],
) -> tuple[int, ...] | None:
    wanted = set(indices)
    return next((line for line in lines if wanted.issubset(line)), None)


def _footer_line(
    words: Sequence[WordEntry],
    line: Sequence[int],
    page_height: float,
    footer_contact_value_kind: Callable[[str], bool] | None,
) -> bool:
    if page_height <= 0:
        return False
    line_top = min(words[index][3]["y0"] for index in line)
    line_bottom = max(words[index][3]["y1"] for index in line)
    if (line_top + line_bottom) / 2 / page_height < 0.65:
        return False
    for index in line:
        value = str(words[index][2].text).strip()
        compact = _compact(value)
        if compact in _FOOTER_LABELS or _CONTACT_POSTAL_RE.fullmatch(compact):
            return True
        if PHONE_VALUE_PAT.fullmatch(value) is not None or EMAIL_PAT.fullmatch(value) is not None:
            return True
        if footer_contact_value_kind is not None and footer_contact_value_kind(value):
            return True
    return False


def _line_has_label_before(
    words: Sequence[WordEntry],
    line: Sequence[int],
    start: int,
    labels: frozenset[str],
) -> bool:
    for index in line:
        if words[index][1] > start:
            continue
        if _compact(str(words[index][2].text)) in labels:
            return True
    return False


def _quoted_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 80):start]
    after = text[end:min(len(text), end + 80)]
    for opening_mark, closing_mark in (('"', '"'), ("'", "'"), ("“", "”"), ("「", "」"), ("『", "』")):
        if before.rfind(opening_mark) > before.rfind(closing_mark) and after.find(closing_mark) >= 0:
            return True
    return False


@lru_cache(maxsize=8)
def _region_pattern(terms: tuple[str, ...]) -> re.Pattern[str] | None:
    normalized = tuple(dict.fromkeys(
        term for term in terms
        if len(term) >= 2 and any(term.endswith(suffix) for suffix in _REGION_TERM_SUFFIXES)
    ))
    if not normalized:
        return None
    alternative = "|".join(re.escape(term) for term in sorted(normalized, key=lambda item: (-len(item), item)))
    return re.compile(rf"(?<![가-힣])(?P<value>{alternative})(?![가-힣])")


def _region_terms(region_data: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
    values: list[str] = ["서울", "서울시"]
    for key in ("sido", "sigungu", "single_tier_sido", "eupmyeondong"):
        for value in region_data.get(key, ()):
            term = str(value).strip()
            if not term:
                continue
            if key == "eupmyeondong" and len(term) < 3:
                continue
            if key in {"sido", "single_tier_sido"} or term.endswith(("시", "군", "구")) or (
                key == "eupmyeondong" and term.endswith(("읍", "면", "동"))
            ):
                values.append(term)
    return tuple(sorted(set(values), key=lambda item: (-len(item), item)))


def _candidate(
    *,
    tag: str,
    category: str,
    source: str,
    start: int,
    end: int,
    text: str,
    action: str = "review",
    score: float | None = None,
    reason_codes: Sequence[str] = (),
) -> PublicCandidate:
    return PublicCandidate(
        tag=tag,
        category=category,
        source=source,
        provenance=source,
        start=start,
        end=end,
        value=text[start:end],
        action=action,
        score=score,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
    )


def build_public_candidates(
    text: str,
    words: Sequence[WordEntry],
    *,
    page_height: float,
    region_data: Mapping[str, Sequence[str]],
    address_patterns: Sequence[re.Pattern[str]] = (),
    options: Mapping[str, Any] | None = None,
    blocked_spans: Sequence[tuple[int, int, str]] = (),
    footer_contact_value_kind: Callable[[str], bool] | None = None,
) -> list[PublicCandidate]:
    """Return deduplicated, word-aligned public candidates for one page."""
    options = options or {}
    auto_threshold = float(options.get("auto_threshold", options.get("auto_mask_threshold", 0.85)))
    review_threshold = float(options.get("review_threshold", 0.5))
    lines = _line_groups(words)
    candidates: list[PublicCandidate] = []
    occupied: list[tuple[int, int, str]] = []
    footer_contact_context = any(
        _footer_line(words, line, page_height, footer_contact_value_kind)
        for line in lines
    )

    def add(candidate: PublicCandidate) -> None:
        if candidate.start < 0 or candidate.end <= candidate.start:
            return
        if not _span_indices(words, candidate.start, candidate.end):
            return
        key = (candidate.start, candidate.end, candidate.category)
        if key in occupied:
            return
        occupied.append(key)
        candidates.append(candidate)

    if bool(options.get("doc_meta", True)):
        for line in lines:
            if (
                page_height <= 0
                or (
                    min(words[index][3]["y0"] for index in line)
                    + max(words[index][3]["y1"] for index in line)
                ) / 2 / page_height < 0.65
            ):
                continue
            for label_start, label_end, _label in _label_windows(words, line, frozenset({"시행"})):
                value_indices = _value_indices_after_label(words, line, label_end)
                if not value_indices:
                    continue
                selected_value_indices: tuple[int, ...] = ()
                for length in range(1, len(value_indices) + 1):
                    prefix = value_indices[:length]
                    value_start = min(words[index][0] for index in prefix)
                    value_end = max(words[index][1] for index in prefix)
                    if SIHAENG_DOCNO_VALUE_PAT.fullmatch(text[value_start:value_end].strip()) is not None:
                        selected_value_indices = prefix
                        break
                if not selected_value_indices:
                    continue
                value_start = min(words[index][0] for index in selected_value_indices)
                value_end = max(words[index][1] for index in selected_value_indices)
                # 시행 문서번호는 개인정보가 아닌 기관 행정 메타데이터다.
                # 라벨은 보존하고, 정확히 검증된 값 셀만 자동 마스킹한다.
                add(_candidate(
                    tag="DOC_META",
                    category="dispatch_metadata",
                    source="public_footer_dispatch_metadata",
                    start=value_start,
                    end=value_end,
                    text=text,
                    action="mask",
                    reason_codes=("exact_dispatch_label", "dispatch_number_format", "footer_position"),
                ))

    if bool(options.get("address", True)):
        for pattern in address_patterns:
            for match in pattern.finditer(text):
                value = match.group("value") if "value" in match.groupdict() else match.group(0)
                start = match.start("value") if "value" in match.groupdict() else match.start()
                end = match.end("value") if "value" in match.groupdict() else match.end()
                indices = _span_indices(words, start, end)
                line = _line_for_indices(lines, indices)
                if line is None:
                    continue
                line_is_footer = (
                    page_height > 0
                    and (
                        min(words[index][3]["y0"] for index in line)
                        + max(words[index][3]["y1"] for index in line)
                    ) / 2 / page_height >= 0.65
                )
                explicit_institution_label = _line_has_label_before(
                    words, line, start, _INSTITUTION_ADDRESS_LABELS,
                )
                value_text = text[start:end]
                has_address_shape = (
                    ROAD_NO_PAT.search(value_text) is not None
                    or LOT_NO_PAT.search(value_text) is not None
                )
                if not line_is_footer and not explicit_institution_label and not has_address_shape:
                    # The shortest national pattern is also a valid region
                    # hierarchy (for example, city + district).  Without a
                    # road/lot suffix it is a region candidate, not an
                    # address candidate.
                    continue
                if any(
                    blocked_start < end and start < blocked_end
                    for blocked_start, blocked_end, blocked_label in blocked_spans
                    if blocked_label == "address"
                ) and not line_is_footer:
                    continue
                decision = score_public_institution_address(
                    address_pattern=True,
                    explicit_address_label=explicit_institution_label
                    or _line_has_label_before(words, line, start, _ADDRESS_LABELS),
                    footer_contact_context=footer_contact_context or line_is_footer,
                    exact_boundary=True,
                    auto_mask_threshold=auto_threshold,
                    review_threshold=review_threshold,
                )
                add(_candidate(
                    tag="ADDRESS",
                    category="institution_address",
                    source="public_footer_institution_address",
                    start=start,
                    end=end,
                    text=text,
                    action="review",
                    score=float(decision["score"]),
                    reason_codes=(
                        "institution_address_review_required",
                        *tuple(str(code) for code in decision["reason_codes"]),
                    ),
                ))

    if bool(options.get("company", True)):
        for line in lines:
            for label_start, label_end, _label in _label_windows(words, line, PUBLIC_INSTITUTION_LABELS):
                value_indices = _value_indices_after_label(words, line, label_end)
                if not value_indices:
                    continue
                start = min(words[index][0] for index in value_indices)
                end = max(words[index][1] for index in value_indices)
                value = text[start:end].strip()
                if not value or _compact(value) in _NON_VALUE_WORDS:
                    continue
                decision = score_public_institution_value(
                    strong_institution_pattern=_has_institution_suffix(_compact(value))
                    or any(_compact(value).startswith(_compact(prefix)) for prefix in COMPANY_NAME_PREFIXES),
                    explicit_institution_label=True,
                    exact_boundary=True,
                    independent_context=True,
                    quoted_context=_quoted_context(text, start, end),
                    auto_mask_threshold=auto_threshold,
                    review_threshold=review_threshold,
                )
                add(_candidate(
                    tag="INSTITUTION_VALUE",
                    category="institution_value",
                    source="public_institution_label",
                    start=start,
                    end=end,
                    text=text,
                    action="mask" if decision["action"] == "auto_mask" else "review",
                    score=float(decision["score"]),
                    reason_codes=tuple(str(code) for code in decision["reason_codes"]),
                ))
        for match in PUBLIC_INSTITUTION_INLINE_PAT.finditer(text):
            start, end = match.start("value"), match.end("value")
            if any(
                blocked_start < end and start < blocked_end
                for blocked_start, blocked_end, blocked_label in blocked_spans
                if blocked_label == "address"
            ):
                continue
            value = match.group("value")
            decision = score_public_institution_value(
                strong_institution_pattern=True,
                exact_boundary=True,
                quoted_context=_quoted_context(text, start, end),
                auto_mask_threshold=auto_threshold,
                review_threshold=review_threshold,
            )
            add(_candidate(
                tag="INSTITUTION_VALUE",
                category="institution_value",
                source="public_institution_suffix",
                start=start,
                end=end,
                text=text,
                action="mask" if decision["action"] == "auto_mask" else "review",
                score=float(decision["score"]),
                reason_codes=tuple(str(code) for code in decision["reason_codes"]),
            ))

    if bool(options.get("place", True)) or bool(options.get("region_context", True)):
        pattern = _region_pattern(_region_terms(region_data))
        if pattern is not None:
            for match in pattern.finditer(text):
                start, end = match.start("value"), match.end("value")
                if any(
                    blocked_start < end and start < blocked_end
                    for blocked_start, blocked_end, blocked_label in blocked_spans
                    if blocked_label == "address"
                ):
                    continue
                if any(
                    occupied_start < end and start < occupied_end
                    and occupied_category in {"institution_value", "institution_address"}
                    for occupied_start, occupied_end, occupied_category in occupied
                ):
                    continue
                line = _line_for_indices(lines, _span_indices(words, start, end))
                if line is not None and _line_has_label_before(
                    words, line, start, _PROFILE_VALUE_LABELS,
                ):
                    # Recipient/sender/dispatch rows are owned by the
                    # profile-layout path; a generic place candidate would
                    # duplicate or compete with that value.
                    continue
                explicit = line is not None and _line_has_label_before(
                    words, line, start, _REGION_CONTEXT_LABELS,
                )
                if not bool(options.get("place", True)) and not explicit:
                    continue
                value = match.group("value")
                compact = _compact(value)
                hierarchical = len([
                    term for term in _region_terms(region_data)
                    if term in compact and len(term) >= 2
                ]) >= 2
                decision = score_public_region_value(
                    explicit_region_label=explicit,
                    hierarchical=hierarchical,
                    exact_boundary=True,
                    quoted_context=_quoted_context(text, start, end),
                    auto_mask_threshold=auto_threshold,
                    review_threshold=review_threshold,
                )
                add(_candidate(
                    tag="PLACE",
                    category="region_name",
                    source="public_region_dictionary" if not explicit else "public_region_context",
                    start=start,
                    end=end,
                    text=text,
                    action="mask" if decision["action"] == "auto_mask" else "review",
                    score=float(decision["score"]),
                    reason_codes=tuple(str(code) for code in decision["reason_codes"]),
                ))

    if bool(options.get("email", True)):
        for match in EMAIL_PAT.finditer(text):
            start, end = match.start("value"), match.end("value")
            indices = _span_indices(words, start, end)
            line = _line_for_indices(lines, indices)
            # The fixed footer path owns contact values in a measured footer
            # band; letting the generic fallback claim the same rectangle first
            # would hide its footer_contact category from the profile linker.
            if line is not None and _footer_line(
                words, line, page_height, footer_contact_value_kind,
            ) and EMAIL_PAT.search(
                text[min(words[index][0] for index in line):max(words[index][1] for index in line)]
            ):
                continue
            add(_candidate(
                tag="EMAIL",
                category="email",
                source="public_email_regex",
                start=start,
                end=end,
                text=text,
                action="mask",
                reason_codes=("deterministic_email_format", "text_layer_geometry"),
            ))

    return candidates
