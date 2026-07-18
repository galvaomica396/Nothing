from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, Sequence

from privacy_spans import (
    action_for_tag,
    clean_match_text as _clean_match_text,
    confidence_for_tag,
    label_for_tag,
    match_occurrence_id,
    match_offsets,
    match_source,
)
from privacy_spans import _find_offset  # re-exported for backwards compatibility


DetectionDecision = Literal["auto_mask", "review", "preserve"]


class RedactionMatchLike(Protocol):
    tag: str
    text: str


@dataclass(frozen=True, slots=True)
class DetectionCandidate:
    id: str
    tag: str
    label: str
    start: int
    end: int
    length: int
    recognizer_name: str
    score: float
    decision: DetectionDecision
    reason: str
    raw_text_stored: bool
    _match: RedactionMatchLike

    def to_redaction_match(self) -> RedactionMatchLike:
        return self._match

    def to_safe_report_dict(self) -> dict[str, str | int | float | bool | None]:
        return {
            "id": self.id,
            "tag": self.tag,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "recognizer_name": self.recognizer_name,
            "score": self.score,
            "decision": self.decision,
            "reason": self.reason,
            "raw_text_stored": self.raw_text_stored,
            "page": None,
        }


def detection_candidates_from_matches(
    text: str,
    matches: Sequence[RedactionMatchLike],
) -> list[DetectionCandidate]:
    cursors: dict[str, int] = {}
    candidates: list[DetectionCandidate] = []
    for index, match in enumerate(matches, 1):
        value = _clean_match_text(match.text)
        start, end, authoritative = match_offsets(text, match, cursors.get(value, 0))
        if start >= 0:
            if not authoritative:
                cursors[value] = end
            length = end - start
        else:
            end = -1
            length = 0
        candidates.append(
            DetectionCandidate(
                id=match_occurrence_id(match, f"candidate_{index:06d}"),
                tag=_report_tag(match.tag),
                label=label_for_tag(match.tag),
                start=start,
                end=end,
                length=length,
                recognizer_name=match_source(match),
                score=confidence_for_tag(match.tag),
                decision=_decision_for_tag(match.tag),
                reason=_reason_for_tag(match.tag),
                raw_text_stored=False,
                _match=match,
            )
        )
    return candidates


def safe_detection_candidate_reports(
    text: str,
    matches: Sequence[RedactionMatchLike],
) -> list[dict[str, str | int | float | bool | None]]:
    return [candidate.to_safe_report_dict() for candidate in detection_candidates_from_matches(text, matches)]


def _report_tag(tag: str) -> str:
    return "PLACE" if tag == "WEAK_PLACE" else tag


def _decision_for_tag(tag: str) -> DetectionDecision:
    return "review" if action_for_tag(tag) == "review" else "auto_mask"


def _reason_for_tag(tag: str) -> str:
    if _decision_for_tag(tag) == "review":
        return "manual review required by confidence policy"
    return "high confidence recognizer can be auto masked"
