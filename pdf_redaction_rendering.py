from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, Protocol, assert_never


DisplayMode = Literal["black", "label_en", "label_ko", "pseudonym"]
MANUAL_REDACTION_TAG: Final = "MANUAL"

MASK_TOKEN_LABELS: Final[Mapping[str, str]] = {
    "RRN": "주민등록번호",
    "PHONE": "전화번호",
    "FOREIGN_REG": "외국인등록번호",
    "BUSINESS_REG_NO": "사업자등록번호",
    "CARD": "카드번호",
    "PASSPORT": "여권번호",
    "ACCOUNT": "계좌번호",
    "NAME": "이름",
    "ADDRESS": "주소",
    "PLACE": "지명",
    "ADDR_DETAIL": "상세주소",
    "LOT_NO": "지번",
    "LEGAL_PARTY": "당사자",
    "COMPANY": "회사명",
    "COURT": "법원명",
    "CASE_TITLE": "사건명",
    "CASE_NUMBER": "사건번호",
    "LAW_FIRM": "법무법인",
    "ATTORNEY": "변호사",
    "APPROVAL_LINE": "결재자",
    "APPROVAL_FLOW": "결재구분",
    "REGION": "지역",
    "DOC_META": "문서메타",
    "EMAIL": "이메일",
    "KEYWORD": "키워드",
    MANUAL_REDACTION_TAG: "마스킹",
}

KOREAN_PDF_FONT_CANDIDATES: Final = (
    Path(os.environ["DOCMASK_KO_FONT"]) if os.environ.get("DOCMASK_KO_FONT") else None,
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/Library/Fonts/AppleGothic.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
)


class PdfRectMetrics(Protocol):
    width: float
    height: float


class PdfTextPage(Protocol):
    def insert_textbox(
        self,
        rect: PdfRectMetrics,
        buffer: str,
        *,
        fontsize: float,
        color: tuple[int, int, int],
        align: int,
        fontname: str = ...,
        fontfile: str = ...,
    ) -> int:
        ...


class PdfRedactionPage(Protocol):
    def add_redact_annot(self, quad: PdfRectMetrics, *, fill: tuple[int, int, int]) -> bool:
        ...


@dataclass(frozen=True, slots=True)
class RedactionVisual:
    fill: tuple[int, int, int]
    label: str | None


@dataclass(frozen=True, slots=True)
class PseudonymTextLayout:
    text: str
    fontsize: float


def normalize_display_mode(mode: str) -> DisplayMode:
    match mode:
        case "black" | "label_en" | "label_ko" | "pseudonym":
            return mode
        case _:
            return "black"


def normalize_redaction_tag(tag: str | None) -> str:
    if not tag:
        return MANUAL_REDACTION_TAG
    normalized = tag.strip().upper()
    return normalized if normalized in MASK_TOKEN_LABELS else MANUAL_REDACTION_TAG


def display_token(tag: str, mode: str = "label_en") -> str:
    display_mode = normalize_display_mode(mode)
    normalized_tag = normalize_redaction_tag(tag)
    match display_mode:
        case "black" | "label_en" | "pseudonym":
            if normalized_tag == MANUAL_REDACTION_TAG:
                return "[MASK]"
            return f"[{normalized_tag}]"
        case "label_ko":
            return f"[{MASK_TOKEN_LABELS.get(normalized_tag, normalized_tag)}]"
        case unreachable:
            assert_never(unreachable)


def redaction_visual(tag: str, mode: str) -> RedactionVisual:
    display_mode = normalize_display_mode(mode)
    match display_mode:
        case "black":
            return RedactionVisual(fill=(0, 0, 0), label=None)
        case "label_en" | "label_ko":
            return RedactionVisual(fill=(1, 1, 1), label=display_token(tag, display_mode))
        case "pseudonym":
            return RedactionVisual(fill=(0, 0, 0), label=None)
        case unreachable:
            assert_never(unreachable)


def add_redaction_annotation(page: PdfRedactionPage, rect: PdfRectMetrics, tag: str, mode: str) -> str | None:
    visual = redaction_visual(tag, mode)
    page.add_redact_annot(rect, fill=visual.fill)
    return visual.label


@lru_cache(maxsize=1)
def korean_pdf_font_file() -> str | None:
    for candidate in KOREAN_PDF_FONT_CANDIDATES:
        if candidate and candidate.exists():
            return str(candidate)
    return None


def insert_pdf_label(page: PdfTextPage, rect: PdfRectMetrics, label: str) -> None:
    fontsize = max(5.0, min(9.0, rect.height * 0.55, rect.width / max(len(label) * 0.62, 1)))
    kwargs: dict[str, str | float | int | tuple[int, int, int]] = {
        "fontsize": fontsize,
        "color": (0, 0, 0),
        "align": 1,
    }
    if any(ord(ch) > 127 for ch in label):
        fontfile = korean_pdf_font_file()
        if fontfile:
            kwargs["fontname"] = "docmaskko"
            kwargs["fontfile"] = fontfile
    page.insert_textbox(rect, label, **kwargs)


def _pseudonym_text_units(value: str) -> float:
    return sum(1.0 if ord(character) > 127 else 0.62 for character in value)


def pseudonym_text_layout(rect: PdfRectMetrics, label: str) -> PseudonymTextLayout | None:
    available_width = max(rect.width - 2.0, 0.0)
    max_fontsize = min(9.0, rect.height * 0.55)
    if available_width < 4.0 or max_fontsize < 4.0 or not label:
        return None

    units = max(_pseudonym_text_units(label), 1.0)
    fitted_fontsize = min(max_fontsize, available_width / units)
    if fitted_fontsize >= 4.0:
        return PseudonymTextLayout(text=label, fontsize=fitted_fontsize)

    max_units = available_width / 4.0
    ellipsis_units = _pseudonym_text_units("…")
    kept: list[str] = []
    used_units = 0.0
    for character in label:
        character_units = _pseudonym_text_units(character)
        if used_units + character_units + ellipsis_units > max_units:
            break
        kept.append(character)
        used_units += character_units
    if not kept:
        return None
    return PseudonymTextLayout(text="".join(kept) + "…", fontsize=4.0)


def insert_pdf_pseudonym_label(page: PdfTextPage, rect: PdfRectMetrics, label: str) -> bool:
    layout = pseudonym_text_layout(rect, label)
    if layout is None:
        return False
    kwargs: dict[str, str | float | int | tuple[int, int, int]] = {
        "fontsize": layout.fontsize,
        "color": (1, 1, 1),
        "align": 1,
    }
    if any(ord(character) > 127 for character in layout.text):
        fontfile = korean_pdf_font_file()
        if fontfile is None:
            return False
        kwargs["fontname"] = "docmaskko"
        kwargs["fontfile"] = fontfile
    return page.insert_textbox(rect, layout.text, **kwargs) >= 0
