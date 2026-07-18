from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib import import_module
from typing import Final, Protocol

from privacy_spans import DetectionSpan


SUPPORTED_LABELS: Final = {
    "RRN": "rrn",
    "FRN": "foreign_id",
    "BUSINESS_REG": "business_number",
    "PHONE": "phone",
    "EMAIL": "email",
    "CARD": "card_number",
    "PASSPORT": "passport_number",
    "ACCOUNT": "bank_account",
    "PERSON": "person_name",
    "ADDRESS": "address",
}
ACTIVE_LABELS: Final = tuple(label for label in SUPPORTED_LABELS if label != "PERSON")
REVIEW_LABELS: Final = {"bank_account", "person_name", "address"}
EVIDENCE_PREFIX_CODES: Final = (
    ("checksum:invalid", "checksum_invalid"),
    ("checksum:", "checksum_valid"),
    ("keyword:bank(", "bank_context"),
    ("keyword:", "keyword_context"),
    ("pos:particle(", "particle_context"),
    ("pos:", "person_context"),
    ("intl:", "international_format"),
    ("obfuscated:", "obfuscated"),
    ("date_valid:", "date_valid"),
    ("pattern:", "pattern"),
)
EVIDENCE_KIND_CODES: Final = {
    "prefix": "prefix",
    "kind": "kind",
    "type": "type",
    "position": "position",
    "origin": "origin",
    "brand_hint": "brand_hint",
}


class KoPiiResult(Protocol):
    label: str
    text: str
    start: int
    end: int
    confidence: float
    evidence: list[str]


class DetectAll(Protocol):
    def __call__(
        self,
        text: str,
        *,
        include: tuple[str, ...],
    ) -> Sequence[KoPiiResult]: ...


def _evidence_code(value: str) -> str:
    for prefix, code in EVIDENCE_PREFIX_CODES:
        if value.startswith(prefix):
            return code
    kind, _separator, _detail = value.partition(":")
    return EVIDENCE_KIND_CODES.get(kind, "detector_evidence")


class KoPiiPrivacyDetector:
    name = "ko_pii"

    def __init__(self, detect_all: DetectAll) -> None:
        self._detect_all = detect_all

    def detect(self, text: str, context: dict[str, str] | None = None) -> list[DetectionSpan]:
        del context
        spans: list[DetectionSpan] = []
        results = self._detect_all(text, include=ACTIVE_LABELS)
        for index, result in enumerate(results, 1):
            if result.label == "PERSON":
                continue
            mapped_label = SUPPORTED_LABELS.get(result.label)
            if mapped_label is None:
                continue
            if not (0 <= result.start < result.end <= len(text)):
                continue
            if text[result.start : result.end] != result.text:
                continue
            evidence = tuple(dict.fromkeys(_evidence_code(item) for item in result.evidence))
            action = (
                "review"
                if mapped_label in REVIEW_LABELS or result.confidence < 0.9 or "checksum_invalid" in evidence
                else "mask"
            )
            spans.append(
                DetectionSpan(
                    id=f"ko_pii_{index:06d}",
                    label=mapped_label,
                    start=result.start,
                    end=result.end,
                    length=result.end - result.start,
                    source=self.name,
                    confidence=result.confidence,
                    action=action,
                    evidence=evidence,
                )
            )
        return spans


def build_ko_pii_detector(log: Callable[[str], None]) -> KoPiiPrivacyDetector | None:
    try:
        module = import_module("ko_pii")
    except ImportError:
        log("[ko-pii] 탐지기 미설치 - 기존 규칙 엔진 결과를 유지합니다")
        return None
    return KoPiiPrivacyDetector(module.detect_all)
