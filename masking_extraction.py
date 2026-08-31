#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF/text extraction engine extracted from document_masker_ocr_gui.

Behavior-preserving move of the marker/paddle/pymupdf4llm/pypdf extraction
engines plus their cleanup wrappers and the plain-text IO helpers.
"""

from __future__ import annotations

import shutil
import contextlib
import io
import math
import re
import warnings
import subprocess
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Sequence

def read_text_file(path: str) -> str:
    encodings = ["utf-8", "cp949", "euc-kr"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
        except OSError:
            raise RuntimeError("TEXT_SOURCE_UNAVAILABLE") from None
    raise RuntimeError("TEXT_ENCODING_UNSUPPORTED")


def write_text_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


_EXTRACTION_FAILURE_CODES = {
    "marker-pdf": (
        "EXTRACTION_MARKER_UNAVAILABLE",
        "EXTRACTION_MARKER_FAILED",
        "EXTRACTION_MARKER_EMPTY",
        "EXTRACTION_MARKER_INVALID_UTF8",
        "EXTRACTION_MARKER_CLEANUP_FAILED",
    ),
    "paddleocr": (
        "EXTRACTION_PADDLE_UNAVAILABLE",
        "EXTRACTION_PADDLE_VERSION_UNSUPPORTED",
        "EXTRACTION_PADDLE_INIT_FAILED",
        "EXTRACTION_PADDLE_FAILED",
        "EXTRACTION_PADDLE_MALFORMED_RESULT",
        "EXTRACTION_PADDLE_MALFORMED_ENTRY",
        "EXTRACTION_PADDLE_EMPTY",
    ),
    "pymupdf4llm": (
        "EXTRACTION_PYMUPDF_UNAVAILABLE",
        "EXTRACTION_PYMUPDF_FAILED",
    ),
    "pypdf": (
        "EXTRACTION_PYPDF_UNAVAILABLE",
        "EXTRACTION_PYPDF_FAILED",
        "EXTRACTION_PYPDF_EMPTY",
    ),
}
_EVIDENCE_ADAPTER_FAILURE_CODES = (
    "PAGE_EVIDENCE_ADAPTER_UNAVAILABLE",
    "PAGE_EVIDENCE_ADAPTER_FAILED",
)

EXTRACTION_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ExtractedWord:
    """A word located in page-local PDF points measured from the top-left."""

    text: str
    bbox: tuple[float, float, float, float]
    confidence: float | None = None
    start: int | None = None
    end: int | None = None
    page_start: int | None = None
    page_end: int | None = None
    source: str = ""
    coordinate_space: str = "pdf_points_top_left"

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bbox": list(self.bbox),
            "confidence": self.confidence,
            "start": self.start,
            "end": self.end,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "source": self.source,
            "coordinate_space": self.coordinate_space,
        }


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    """One 0-based PDF page and the evidence extracted from it."""

    page_index: int
    width: float | None = None
    height: float | None = None
    text: str = ""
    words: tuple[ExtractedWord, ...] = ()
    drawings: tuple[tuple[float, float, float, float], ...] = ()
    start: int | None = None
    end: int | None = None
    source: str = ""
    coordinate_space: str = "pdf_points_top_left"
    raster_transform: dict[str, float] | None = None
    evidence_status: str = "available"
    evidence_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_index": self.page_index,
            "width": self.width,
            "height": self.height,
            "text": self.text,
            "words": [word.to_dict() for word in self.words],
            "drawings": [list(rect) for rect in self.drawings],
            "start": self.start,
            "end": self.end,
            "source": self.source,
            "engine": self.source,
            "coordinate_space": self.coordinate_space,
            "raster_transform": self.raster_transform,
            "evidence_status": self.evidence_status,
            "evidence_reason": self.evidence_reason,
        }


@dataclass
class ExtractResult:
    text: str
    engine_used: str
    duration_sec: float
    notes: list[str] = field(default_factory=list)
    pages: tuple[ExtractedPage, ...] = ()
    engine_chain: tuple[str, ...] = ()
    fallback_chain: tuple[str, ...] = ()
    schema_version: int = EXTRACTION_SCHEMA_VERSION
    evidence_adapter: str | None = None
    evidence_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "text": self.text,
            "content_engine": self.engine_used,
            "engine_used": self.engine_used,
            "duration_sec": self.duration_sec,
            "notes": list(self.notes),
            "pages": [page.to_dict() for page in self.pages],
            "engine_chain": list(self.engine_chain),
            "fallback_chain": list(self.fallback_chain),
            "evidence_adapter": self.evidence_adapter,
            "evidence_reason": self.evidence_reason,
        }


def _with_engine_chain(result: ExtractResult, attempted: Sequence[str], failures: Sequence[str] = ()) -> ExtractResult:
    result.engine_chain = tuple(attempted)
    result.fallback_chain = tuple(failures)
    return result
PageEvidenceAdapter = Callable[[str], tuple[ExtractedPage, ...]]

_MIN_DRAWING_SPAN = 12.0
_DRAWING_AXIS_TOLERANCE = 1.5
_MIN_RULED_ROW_HEIGHT = 4.0
_MAX_RULED_ROW_HEIGHT = 100.0


def _merge_collinear_rules(
    rules: Sequence[tuple[float, float, float, float]], *, horizontal: bool,
) -> tuple[tuple[float, float, float, float], ...]:
    ordered = sorted(
        rules,
        key=(
            (lambda rect: ((rect[1] + rect[3]) / 2, rect[0]))
            if horizontal
            else (lambda rect: ((rect[0] + rect[2]) / 2, rect[1]))
        ),
    )
    merged: list[tuple[float, float, float, float]] = []
    for rect in ordered:
        if not merged:
            merged.append(rect)
            continue
        previous = merged[-1]
        same_axis = (
            abs((previous[1] + previous[3] - rect[1] - rect[3]) / 2)
            <= _DRAWING_AXIS_TOLERANCE
            if horizontal
            else abs((previous[0] + previous[2] - rect[0] - rect[2]) / 2)
            <= _DRAWING_AXIS_TOLERANCE
        )
        touches = (
            rect[0] <= previous[2] + _DRAWING_AXIS_TOLERANCE
            if horizontal
            else rect[1] <= previous[3] + _DRAWING_AXIS_TOLERANCE
        )
        if not (same_axis and touches):
            merged.append(rect)
            continue
        merged[-1] = (
            min(previous[0], rect[0]),
            min(previous[1], rect[1]),
            max(previous[2], rect[2]),
            max(previous[3], rect[3]),
        )
    return tuple(merged)


def _ruled_rows(
    horizontal_rules: Sequence[tuple[float, float, float, float]],
    vertical_rules: Sequence[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], ...]:
    ordered = sorted(horizontal_rules, key=lambda rect: (rect[1] + rect[3]) / 2)
    rows: list[tuple[float, float, float, float]] = []
    for index, upper in enumerate(ordered):
        upper_y = (upper[1] + upper[3]) / 2
        for lower in ordered[index + 1:]:
            lower_y = (lower[1] + lower[3]) / 2
            height = lower_y - upper_y
            if height < _MIN_RULED_ROW_HEIGHT:
                continue
            if height > _MAX_RULED_ROW_HEIGHT:
                break
            left = max(upper[0], lower[0])
            right = min(upper[2], lower[2])
            has_left_boundary = any(
                abs((vertical[0] + vertical[2]) / 2 - left) <= _DRAWING_AXIS_TOLERANCE
                and vertical[1] <= upper_y + _DRAWING_AXIS_TOLERANCE
                and vertical[3] >= lower_y - _DRAWING_AXIS_TOLERANCE
                for vertical in vertical_rules
            )
            has_right_boundary = any(
                abs((vertical[0] + vertical[2]) / 2 - right) <= _DRAWING_AXIS_TOLERANCE
                and vertical[1] <= upper_y + _DRAWING_AXIS_TOLERANCE
                and vertical[3] >= lower_y - _DRAWING_AXIS_TOLERANCE
                for vertical in vertical_rules
            )
            if right - left >= _MIN_DRAWING_SPAN and has_left_boundary and has_right_boundary:
                rows.append((left, upper_y, right, lower_y))
                break
    return tuple(rows)


def _drawing_rules(
    drawings: Sequence[tuple[float, float, float, float]],
) -> tuple[
    tuple[tuple[float, float, float, float], ...],
    tuple[tuple[float, float, float, float], ...],
]:
    unique = tuple(dict.fromkeys(
        rect for rect in drawings
        if len(rect) == 4 and all(math.isfinite(value) for value in rect)
    ))
    horizontal = _merge_collinear_rules(
        tuple(
            rect for rect in unique
            if rect[3] - rect[1] <= _DRAWING_AXIS_TOLERANCE
            and rect[2] - rect[0] >= _MIN_DRAWING_SPAN
        ),
        horizontal=True,
    )
    vertical = _merge_collinear_rules(
        tuple(
            rect for rect in unique
            if rect[2] - rect[0] <= _DRAWING_AXIS_TOLERANCE
            and rect[3] - rect[1] >= _MIN_DRAWING_SPAN
        ),
        horizontal=False,
    )
    return horizontal, vertical


def ruled_table_rows(
    drawings: Sequence[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], ...]:
    """Return stroke-bounded row regions from horizontal and vertical vector rules."""
    horizontal, vertical = _drawing_rules(drawings)
    return _ruled_rows(horizontal, vertical)


def ruled_table_cells(
    drawings: Sequence[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], ...]:
    """Return cells bounded on all four sides by horizontal and vertical vector rules."""
    horizontal, vertical = _drawing_rules(drawings)
    cells: list[tuple[float, float, float, float]] = []
    for index, upper in enumerate(horizontal):
        upper_y = (upper[1] + upper[3]) / 2
        for lower in horizontal[index + 1:]:
            lower_y = (lower[1] + lower[3]) / 2
            height = lower_y - upper_y
            if height < _MIN_RULED_ROW_HEIGHT:
                continue
            if height > _MAX_RULED_ROW_HEIGHT:
                break
            boundaries = sorted({
                (rule[0] + rule[2]) / 2
                for rule in vertical
                if rule[1] <= upper_y + _DRAWING_AXIS_TOLERANCE
                and rule[3] >= lower_y - _DRAWING_AXIS_TOLERANCE
                and upper[0] - _DRAWING_AXIS_TOLERANCE <= (rule[0] + rule[2]) / 2 <= upper[2] + _DRAWING_AXIS_TOLERANCE
                and lower[0] - _DRAWING_AXIS_TOLERANCE <= (rule[0] + rule[2]) / 2 <= lower[2] + _DRAWING_AXIS_TOLERANCE
            })
            row_cells = tuple(
                (left, upper_y, right, lower_y)
                for left, right in zip(boundaries, boundaries[1:])
                if right - left >= _MIN_DRAWING_SPAN
                and upper[0] <= left <= right <= upper[2]
                and lower[0] <= left <= right <= lower[2]
            )
            if row_cells:
                cells.extend(row_cells)
                break
    return tuple(dict.fromkeys(cells))


RawCharacter = tuple[str, tuple[float, float, float, float]]


def _rawdict_characters(rawdict: Any) -> tuple[RawCharacter, ...]:
    if not isinstance(rawdict, dict):
        return ()
    characters: list[RawCharacter] = []
    for block in rawdict.get("blocks", ()):
        if not isinstance(block, dict):
            continue
        for line in block.get("lines", ()):
            if not isinstance(line, dict):
                continue
            for span in line.get("spans", ()):
                if not isinstance(span, dict):
                    continue
                for character in span.get("chars", ()):
                    if not isinstance(character, dict):
                        continue
                    text = character.get("c")
                    bbox = character.get("bbox")
                    if not isinstance(text, str) or not text or not isinstance(bbox, (list, tuple)):
                        continue
                    try:
                        x0, y0, x1, y1 = (float(value) for value in bbox)
                    except (TypeError, ValueError):
                        continue
                    if not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or x1 <= x0 or y1 <= y0:
                        continue
                    characters.append((text, (x0, y0, x1, y1)))
    return tuple(characters)


def _center(rect: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)


def _word_table_cells(
    word: ExtractedWord,
    cells: Sequence[tuple[float, float, float, float]],
) -> tuple[tuple[float, float, float, float], ...]:
    _, word_y = _center(word.bbox)
    return tuple(
        cell for cell in cells
        if cell[1] <= word_y <= cell[3]
        and word.bbox[0] < cell[2] - _DRAWING_AXIS_TOLERANCE
        and word.bbox[2] > cell[0] + _DRAWING_AXIS_TOLERANCE
    )


def _compact(value: str) -> str:
    return "".join(character for character in value if not character.isspace())


def reconstruct_table_cell_words(
    words: Sequence[ExtractedWord],
    drawings: Sequence[tuple[float, float, float, float]],
    raw_characters: Sequence[RawCharacter],
) -> tuple[ExtractedWord, ...]:
    """Split text-layer words only when their raw characters span ruled table cells."""
    cells = ruled_table_cells(drawings)
    if not cells or not raw_characters:
        return tuple(words)
    affected_cells: list[tuple[float, float, float, float]] = []
    for word in words:
        spanning_cells = _word_table_cells(word, cells)
        if len(spanning_cells) >= 2:
            affected_cells.extend(spanning_cells)
    affected_cells = list(dict.fromkeys(affected_cells))
    if not affected_cells:
        return tuple(words)
    affected_indices = tuple(
        index for index, word in enumerate(words)
        if any(cell in affected_cells for cell in _word_table_cells(word, cells))
    )
    if not affected_indices or affected_indices != tuple(range(affected_indices[0], affected_indices[-1] + 1)):
        return tuple(words)
    affected_words = tuple(words[index] for index in affected_indices)
    cell_characters = tuple(
        tuple(
            character for character in raw_characters
            if cell[0] <= _center(character[1])[0] <= cell[2]
            and cell[1] <= _center(character[1])[1] <= cell[3]
        )
        for cell in affected_cells
    )
    if (
        not affected_words
        or _compact("".join(character[0] for characters in cell_characters for character in characters))
        != _compact("".join(word.text for word in affected_words))
    ):
        return tuple(words)
    fragments = tuple(
        replace(
            affected_words[0],
            text="".join(character[0] for character in characters),
            bbox=(
                min(character[1][0] for character in characters),
                min(character[1][1] for character in characters),
                max(character[1][2] for character in characters),
                max(character[1][3] for character in characters),
            ),
            source="pymupdf_table_cell",
        )
        for characters in cell_characters
        if characters
    )
    if len(fragments) < 2:
        return tuple(words)
    affected_ids = {id(word) for word in affected_words}
    reconstructed: list[ExtractedWord] = []
    fragments_inserted = False
    for word in words:
        if id(word) not in affected_ids:
            reconstructed.append(word)
            continue
        if not fragments_inserted:
            reconstructed.extend(fragments)
            fragments_inserted = True
    return tuple(reconstructed)


def _page_drawings(page: Any) -> tuple[tuple[float, float, float, float], ...]:
    rects: list[tuple[float, float, float, float]] = []
    for drawing in page.get_drawings():
        raw_rect = drawing.get("rect") if hasattr(drawing, "get") else None
        if raw_rect is None:
            continue
        try:
            x0, y0, x1, y1 = (float(value) for value in raw_rect)
        except (TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            continue
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if max(right - left, bottom - top) < _MIN_DRAWING_SPAN:
            continue
        rects.append((left, top, right, bottom))
    unique = tuple(dict.fromkeys(rects))
    horizontal = _merge_collinear_rules(
        tuple(
            rect for rect in unique
            if rect[3] - rect[1] <= _DRAWING_AXIS_TOLERANCE
            and rect[2] - rect[0] >= _MIN_DRAWING_SPAN
        ),
        horizontal=True,
    )
    vertical = _merge_collinear_rules(
        tuple(
            rect for rect in unique
            if rect[2] - rect[0] <= _DRAWING_AXIS_TOLERANCE
            and rect[3] - rect[1] >= _MIN_DRAWING_SPAN
        ),
        horizontal=False,
    )
    area_rects = tuple(
        rect for rect in unique
        if rect[2] - rect[0] >= _MIN_DRAWING_SPAN
        and rect[3] - rect[1] >= _MIN_RULED_ROW_HEIGHT
    )
    return tuple(dict.fromkeys((*area_rects, *horizontal, *vertical, *ruled_table_rows(unique))))


def _extract_pdf_page_evidence(pdf_path: str) -> tuple[ExtractedPage, ...]:
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        warnings.simplefilter("ignore", DeprecationWarning)
        return _extract_pdf_page_evidence_impl(pdf_path)

def _extract_pdf_page_evidence_impl(pdf_path: str) -> tuple[ExtractedPage, ...]:
    """Read local text-layer geometry without changing the selected extractor."""
    try:
        with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            warnings.simplefilter("ignore", DeprecationWarning)
            import fitz  # type: ignore
    except Exception as error:
        raise RuntimeError("PAGE_EVIDENCE_ADAPTER_UNAVAILABLE") from error

    try:
        document = fitz.open(pdf_path)
        try:
            pages: list[ExtractedPage] = []
            for page_index, page in enumerate(document):
                page_text = page.get_text("text")
                words = tuple(
                    ExtractedWord(
                        text=str(word[4]),
                        bbox=(float(word[0]), float(word[1]), float(word[2]), float(word[3])),
                        confidence=None,
                        page_start=None,
                        page_end=None,
                        source="pymupdf_text_layer",
                    )
                    for word in page.get_text("words")
                    if len(word) >= 5 and str(word[4])
                )
                drawings = _page_drawings(page)
                words = reconstruct_table_cell_words(
                    words,
                    drawings,
                    _rawdict_characters(page.get_text("rawdict")),
                )
                pages.append(
                    ExtractedPage(
                        page_index=page_index,
                        width=float(page.rect.width),
                        height=float(page.rect.height),
                        text=page_text,
                        words=words,
                        drawings=drawings,
                        source="pymupdf_text_layer",
                        evidence_status="available" if page_text else "gap",
                        evidence_reason=None if page_text else "no_text_layer",
                    )
                )
            return tuple(pages)
        finally:
            document.close()
    except RuntimeError:
        raise
    except Exception as error:
        raise RuntimeError("PAGE_EVIDENCE_ADAPTER_FAILED") from error


def _enrich_pdf_result(
    result: ExtractResult,
    pdf_path: str,
    page_evidence_adapter: PageEvidenceAdapter | None = None,
) -> ExtractResult:
    adapter = page_evidence_adapter or _extract_pdf_page_evidence
    try:
        adapter_pages = adapter(pdf_path)
    except Exception as error:
        result.pages = _mark_evidence_adapter_gap(result.pages, _safe_evidence_reason(error))
        result.evidence_adapter = "pymupdf_text_layer"
        result.evidence_reason = _safe_evidence_reason(error)
        return result
    if not adapter_pages:
        result.pages = _mark_evidence_adapter_gap(result.pages, "PAGE_EVIDENCE_ADAPTER_FAILED")
        result.evidence_adapter = "pymupdf_text_layer"
        result.evidence_reason = "PAGE_EVIDENCE_ADAPTER_FAILED"
        return result

    if result.pages:
        # OCR evidence is authoritative for scan content.  A blank or divergent
        # text layer cannot replace it or be promoted to PDF-point geometry.
        adapter_by_index = {page.page_index: page for page in adapter_pages}
        trusted_adapter_pages = tuple(
            adapter_by_index.get(page.page_index, page)
            if (
                adapter_by_index.get(page.page_index) is not None
                and adapter_by_index[page.page_index].text
                and adapter_by_index[page.page_index].words
                and (
                    not page.text
                    or page.source != "paddleocr"
                    or _normalize_evidence_text(adapter_by_index[page.page_index].text)
                    == _normalize_evidence_text(page.text)
                )
            )
            else page
            for page in result.pages
        )
        result.pages = _align_page_evidence(result.text, trusted_adapter_pages)
    else:
        result.pages = _align_page_evidence(result.text, adapter_pages)
    result.evidence_adapter = "pymupdf_text_layer"
    result.evidence_reason = None
    return result


def _normalize_evidence_text(value: str) -> str:
    return "".join(value.split())


def _safe_evidence_reason(error: Exception) -> str:
    diagnostic = str(error)
    for code in _EVIDENCE_ADAPTER_FAILURE_CODES:
        if diagnostic.startswith(code):
            return code
    return "PAGE_EVIDENCE_ADAPTER_FAILED"


def _safe_extraction_failure(engine_name: str, error: Exception) -> str:
    diagnostic = str(error)
    for code in _EXTRACTION_FAILURE_CODES[engine_name]:
        if diagnostic.startswith(code):
            return code
    return _EXTRACTION_FAILURE_CODES[engine_name][1]
def _mark_evidence_adapter_gap(
    pages: Sequence[ExtractedPage],
    reason: str,
) -> tuple[ExtractedPage, ...]:
    return tuple(
        replace(
            page,
            evidence_status="gap",
            evidence_reason=reason,
        )
        for page in pages
    )



def _normalized_alignment_text(value: str) -> tuple[str, tuple[int, ...]]:
    normalized: list[str] = []
    source_offsets: list[int] = []
    in_whitespace = False
    for index, character in enumerate(value):
        if character.isspace():
            if normalized and not in_whitespace:
                normalized.append(" ")
                source_offsets.append(index)
            in_whitespace = True
            continue
        normalized.append(character)
        source_offsets.append(index)
        in_whitespace = False
    if normalized and normalized[-1] == " ":
        normalized.pop()
        source_offsets.pop()
    return "".join(normalized), tuple(source_offsets)


def _word_offset(
    text: str,
    word: ExtractedWord,
    start: int,
    end: int | None = None,
) -> tuple[int | None, int | None]:
    if word.source == "pymupdf_table_cell":
        match = re.search(r"\s*".join(re.escape(character) for character in word.text), text[start:end])
        if match is not None:
            return start + match.start(), start + match.end()
    word_start = text.find(word.text, start, end)
    return (word_start, word_start + len(word.text)) if word_start >= 0 else (None, None)


def _align_page_evidence(text: str, pages: Sequence[ExtractedPage]) -> tuple[ExtractedPage, ...]:
    normalized_text, text_offsets = _normalized_alignment_text(text)
    normalized_cursor = 0
    aligned: list[ExtractedPage] = []
    for page in pages:
        normalized_page, _ = _normalized_alignment_text(page.text)
        normalized_start = (
            normalized_text.find(normalized_page, normalized_cursor)
            if normalized_page
            else -1
        )
        normalized_end = (
            normalized_start + len(normalized_page)
            if normalized_start >= 0
            else -1
        )
        page_start = text_offsets[normalized_start] if normalized_start >= 0 else None
        page_end = (
            text_offsets[normalized_end - 1] + 1
            if normalized_end > normalized_start >= 0
            else None
        )
        if page_start is None or page_end is None:
            aligned.append(
                ExtractedPage(
                    page_index=page.page_index,
                    width=page.width,
                    height=page.height,
                    text=page.text,
                    words=tuple(
                        ExtractedWord(
                            text=word.text,
                            bbox=word.bbox,
                            confidence=word.confidence,
                            start=word.start,
                            end=word.end,
                            page_start=word.page_start,
                            page_end=word.page_end,
                            source=word.source or page.source,
                            coordinate_space=word.coordinate_space,
                        )
                        for word in page.words
                    ),
                    drawings=page.drawings,
                    source=page.source,
                    coordinate_space=page.coordinate_space,
                    raster_transform=page.raster_transform,
                    evidence_status="gap" if not page.text else "unaligned",
                    evidence_reason=page.evidence_reason or (
                        "no_text_layer" if not page.text else "canonical_text_unaligned"
                    ),
                )
            )
            continue
        normalized_cursor = normalized_end
        global_word_cursor = page_start
        local_word_cursor = 0
        words: list[ExtractedWord] = []
        for word in page.words:
            word_start, word_end = _word_offset(text, word, global_word_cursor, page_end)
            local_start, local_end = _word_offset(page.text, word, local_word_cursor)
            if word_start is not None:
                global_word_cursor = word_end
            if local_start is not None:
                local_word_cursor = local_end
            words.append(
                ExtractedWord(
                    text=word.text,
                    bbox=word.bbox,
                    confidence=word.confidence,
                    start=word_start,
                    end=word_end,
                    page_start=local_start,
                    page_end=local_end,
                    source=word.source or page.source,
                    coordinate_space=word.coordinate_space,
                )
            )
        aligned.append(
            ExtractedPage(
                page_index=page.page_index,
                width=page.width,
                height=page.height,
                text=page.text,
                words=tuple(words),
                drawings=page.drawings,
                start=page_start,
                end=page_end,
                source=page.source,
                coordinate_space=page.coordinate_space,
                raster_transform=page.raster_transform,
                evidence_status=page.evidence_status,
                evidence_reason=page.evidence_reason,
            )
        )
    return tuple(aligned)

def _paddle_word(item: object, start: int) -> ExtractedWord:
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_ENTRY")
    box, recognition = item
    if (
        not isinstance(box, (list, tuple))
        or len(box) != 4
        or not isinstance(recognition, (list, tuple))
        or len(recognition) != 2
    ):
        raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_ENTRY")
    text, confidence = recognition
    if (
        not isinstance(text, str)
        or not text
        or isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
    ):
        raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_ENTRY")
    try:
        points = tuple(
            (float(point[0]), float(point[1]))
            for point in box
            if isinstance(point, (list, tuple)) and len(point) == 2
        )
        score = float(confidence)
    except (TypeError, ValueError):
        raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_ENTRY") from None
    if len(points) != 4 or not all(
        value == value and value not in {float("inf"), float("-inf")}
        for point in points for value in point
    ) or score != score or score in {float("inf"), float("-inf")}:
        raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_ENTRY")
    bbox = (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_ENTRY")
    return ExtractedWord(
        text=text,
        bbox=bbox,
        confidence=score,
        start=start,
        end=start + len(text),
        coordinate_space="raster_pixels",
    )


def _run_cmd(cmd: list[str], timeout: int = 600) -> tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    return p.returncode, p.stdout, p.stderr


def _extract_pdf_with_marker_cleanup(pdf_path: str) -> ExtractResult:
    """marker 추출을 시스템 임시 디렉터리에서 수행하고 종료 시 원문(PII 포함)을 정리(H-4).

    기존에는 입력 폴더 옆 `{입력}_tmp/marker_out/*.md`(마스킹 전 원문)가 영구 잔존했다.
    tempfile.mkdtemp 로 시스템 temp 를 사용하고, 반환 전에 md 를 메모리로 읽은 뒤
    finally 에서 디렉터리를 제거한다. 정리 실패는 추출 성공으로 취급하지 않는다.
    """
    work = tempfile.mkdtemp(prefix="marker_")
    try:
        return _extract_pdf_with_marker(pdf_path, work)
    finally:
        try:
            shutil.rmtree(work)
        except OSError:
            try:
                shutil.rmtree(work)
            except OSError as error:
                raise RuntimeError("EXTRACTION_MARKER_CLEANUP_FAILED") from error


def _extract_pdf_with_marker(pdf_path: str, work_dir: str) -> ExtractResult:
    start = time.time()
    marker_bin = shutil.which("marker_single")
    if not marker_bin:
        raise RuntimeError("EXTRACTION_MARKER_UNAVAILABLE")

    out_dir = Path(work_dir) / "marker_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        marker_bin,
        pdf_path,
        "--output_dir",
        str(out_dir),
        "--output_format",
        "markdown",
    ]
    code, _stdout, _stderr = _run_cmd(cmd, timeout=1800)
    if code != 0:
        raise RuntimeError("EXTRACTION_MARKER_FAILED")

    md_candidates = sorted(out_dir.rglob("*.md"))
    if not md_candidates:
        raise RuntimeError("EXTRACTION_MARKER_EMPTY")

    try:
        md_text = md_candidates[0].read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeError("EXTRACTION_MARKER_INVALID_UTF8") from None
    result = ExtractResult(
        text=md_text,
        engine_used="marker-pdf",
        duration_sec=time.time() - start,
        notes=["표/레이아웃 유지 목적 추출(markdown)"],
    )
    return result


def _extract_pdf_with_pymupdf4llm(pdf_path: str) -> ExtractResult:
    start = time.time()
    try:
        import pymupdf4llm  # type: ignore
    except Exception:
        raise RuntimeError("EXTRACTION_PYMUPDF_UNAVAILABLE") from None

    try:
        md = pymupdf4llm.to_markdown(pdf_path)
    except Exception:
        raise RuntimeError("EXTRACTION_PYMUPDF_FAILED") from None
    result = ExtractResult(
        text=md,
        engine_used="pymupdf4llm",
        duration_sec=time.time() - start,
        notes=["텍스트 기반 PDF에 유리", "OCR 미포함"],
    )
    return result


def _paddle_constructor_options(module: object) -> dict[str, object]:
    """Select the documented PaddleOCR constructor contract by package major version."""
    version = getattr(module, "__version__", None)
    if not isinstance(version, str):
        try:
            from importlib.metadata import version as package_version
            version = package_version("paddleocr")
        except Exception:
            raise RuntimeError("EXTRACTION_PADDLE_VERSION_UNSUPPORTED") from None
    major_text = version.split(".", 1)[0]
    if not major_text.isdigit():
        raise RuntimeError("EXTRACTION_PADDLE_VERSION_UNSUPPORTED")
    major = int(major_text)
    if major >= 3:
        return {"lang": "korean", "use_textline_orientation": True}
    if major == 2:
        return {"lang": "korean", "use_angle_cls": True}
    raise RuntimeError("EXTRACTION_PADDLE_VERSION_UNSUPPORTED")


def _extract_pdf_with_paddle(pdf_path: str) -> ExtractResult:
    """Extract OCR text only from a documented, versioned PaddleOCR result contract."""
    start = time.time()
    try:
        import paddleocr  # type: ignore
        PaddleOCR = paddleocr.PaddleOCR
    except Exception:
        raise RuntimeError("EXTRACTION_PADDLE_UNAVAILABLE") from None

    try:
        ocr = PaddleOCR(**_paddle_constructor_options(paddleocr))
    except RuntimeError:
        raise
    except TypeError:
        # A constructor signature mismatch means this PaddleOCR build does not
        # implement the documented options for its reported major version.
        raise RuntimeError("EXTRACTION_PADDLE_VERSION_UNSUPPORTED") from None
    except Exception:
        raise RuntimeError("EXTRACTION_PADDLE_INIT_FAILED") from None

    try:
        result = ocr.ocr(pdf_path, cls=True)
    except Exception:
        raise RuntimeError("EXTRACTION_PADDLE_FAILED") from None
    if result is not None and not isinstance(result, (list, tuple)):
        raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_RESULT")

    lines: list[str] = []
    pages: list[ExtractedPage] = []
    for page_index, page_items in enumerate(result or ()):
        if page_items is None:
            page_items = ()
        if not isinstance(page_items, (list, tuple)):
            raise RuntimeError("EXTRACTION_PADDLE_MALFORMED_RESULT")
        page_lines: list[str] = []
        words: list[ExtractedWord] = []
        for item in page_items:
            word = _paddle_word(item, len("\n".join(page_lines)) + (1 if page_lines else 0))
            page_lines.append(word.text)
            words.append(word)
        page_text = "\n".join(page_lines)
        pages.append(
            ExtractedPage(
                page_index=page_index,
                text=page_text,
                words=tuple(words),
                source="paddleocr",
                coordinate_space="raster_pixels",
                evidence_status="gap",
                evidence_reason="pdf_point_transform_unavailable",
            )
        )
        lines.append(f"\n\n===== PAGE {page_index + 1} (paddle) =====")
        lines.extend(page_lines)
    text = "\n".join(lines).strip()
    if not any(page.text.strip() for page in pages):
        raise RuntimeError("EXTRACTION_PADDLE_EMPTY")
    return ExtractResult(
        text=text,
        engine_used="paddleocr",
        duration_sec=time.time() - start,
        notes=["한글 OCR 중심", "표/레이아웃 보존은 marker 대비 제한적일 수 있음"],
        pages=tuple(pages),
    )


def _extract_pdf_with_pypdf(pdf_path: str) -> ExtractResult:
    start = time.time()
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        raise RuntimeError("EXTRACTION_PYPDF_UNAVAILABLE") from None

    try:
        reader = PdfReader(pdf_path)
        texts: list[str] = []
        pages: list[ExtractedPage] = []
        for page_index, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            media_box = page.mediabox
            pages.append(
                ExtractedPage(
                    page_index=page_index,
                    width=float(media_box.width),
                    height=float(media_box.height),
                    text=page_text,
                    source="pypdf_text_layer",
                    evidence_status="gap" if not page_text else "available",
                    evidence_reason="no_text_layer" if not page_text else None,
                )
            )
            texts.append(f"\n\n===== PAGE {page_index + 1} =====\n{page_text}")
    except Exception:
        raise RuntimeError("EXTRACTION_PYPDF_FAILED") from None
    if not any(page.text.strip() for page in pages):
        raise RuntimeError("EXTRACTION_PYPDF_EMPTY")
    return ExtractResult(
        text="".join(texts),
        engine_used="pypdf",
        duration_sec=time.time() - start,
        notes=["빠른 텍스트 추출", "표/레이아웃 보존 약함"],
        pages=tuple(pages),
    )


def _with_page_evidence(
    result: ExtractResult,
    path: str,
    page_evidence_adapter: PageEvidenceAdapter | None,
) -> ExtractResult:
    return _enrich_pdf_result(result, path, page_evidence_adapter)


def extract_document(
    path: str,
    engine: str = "auto",
    *,
    page_evidence_adapter: PageEvidenceAdapter | None = None,
) -> ExtractResult:
    ext = Path(path).suffix.lower()
    if ext in {".txt", ".md", ".csv", ".log"}:
        t0 = time.time()
        result = ExtractResult(
            text=read_text_file(path),
            engine_used="plain-text",
            duration_sec=time.time() - t0,
            notes=[],
        )
        return _with_engine_chain(result, ("plain-text",))

    if ext != ".pdf":
        raise RuntimeError("지원 형식은 현재 txt/md/csv/log/pdf 입니다.")

    if engine not in {"auto", "marker", "paddle", "pymupdf", "pypdf"}:
        raise RuntimeError("EXTRACTION_ENGINE_UNSUPPORTED")

    if engine == "marker":
        return _with_page_evidence(_with_engine_chain(_extract_pdf_with_marker_cleanup(path), ("marker-pdf",)), path, page_evidence_adapter)
    if engine == "paddle":
        return _with_page_evidence(_with_engine_chain(_extract_pdf_with_paddle(path), ("paddleocr",)), path, page_evidence_adapter)
    if engine == "pymupdf":
        return _with_page_evidence(_with_engine_chain(_extract_pdf_with_pymupdf4llm(path), ("pymupdf4llm",)), path, page_evidence_adapter)
    if engine == "pypdf":
        return _with_page_evidence(_with_engine_chain(_extract_pdf_with_pypdf(path), ("pypdf",)), path, page_evidence_adapter)

    attempts: list[str] = []
    failures: list[str] = []
    for engine_name, extractor in (
        ("marker-pdf", _extract_pdf_with_marker_cleanup),
        ("paddleocr", _extract_pdf_with_paddle),
        ("pymupdf4llm", _extract_pdf_with_pymupdf4llm),
        ("pypdf", _extract_pdf_with_pypdf),
    ):
        attempts.append(engine_name)
        try:
            return _with_page_evidence(_with_engine_chain(extractor(path), attempts, failures), path, page_evidence_adapter)
        except RuntimeError as error:
            failure = _safe_extraction_failure(engine_name, error)
            if failure == "EXTRACTION_MARKER_CLEANUP_FAILED":
                raise RuntimeError(failure) from error
            failures.append(failure)
    raise RuntimeError("EXTRACTION_ALL_ENGINES_FAILED:" + ",".join(failures))


def extract_document_for_public_analysis(
    path: str,
    engine: str = "auto",
    *,
    extractor: Callable[[str, str], ExtractResult] | None = None,
) -> ExtractResult:
    """Use one geometry-aligned PDF text layer for public manifest evidence."""
    result = (extractor or extract_document)(path, engine)
    if not result.pages:
        return result

    if all(
        page.source == "pymupdf_text_layer"
        and page.text.strip()
        and page.words
        and page.coordinate_space == "pdf_points_top_left"
        and page.evidence_status == "available"
        and page.start is not None
        and page.end is not None
        and all(word.page_start is not None and word.page_end is not None for word in page.words)
        for page in result.pages
    ):
        return result

    if all(
        page.source == "pymupdf_text_layer"
        and page.text.strip()
        and page.words
        and page.coordinate_space == "pdf_points_top_left"
        and page.evidence_status in {"available", "unaligned"}
        and (
            page.evidence_status == "available"
            or page.evidence_reason == "canonical_text_unaligned"
        )
        for page in result.pages
    ):
        canonical_text = "\n".join(page.text for page in result.pages)
        trusted_pages = tuple(
            replace(page, evidence_status="available", evidence_reason=None)
            for page in result.pages
        )
        aligned_pages = _align_page_evidence(canonical_text, trusted_pages)
        if all(
            page.evidence_status == "available"
            and page.start is not None
            and page.end is not None
            and all(word.page_start is not None and word.page_end is not None for word in page.words)
            for page in aligned_pages
        ):
            result.text = canonical_text
            result.pages = aligned_pages
            return result

    # Candidate generation and manifest construction must see the same words
    # and offsets. The selected extractor (notably pypdf) may produce useful
    # text while its page boundaries cannot be joined to PyMuPDF rectangles.
    # For text PDFs, replace that divergent evidence with a fresh, aligned
    # PyMuPDF text layer instead of silently converting every keyword to zero
    # occurrences.
    try:
        adapter_pages = _extract_pdf_page_evidence(path)
    except Exception:
        return result
    if not adapter_pages or not all(
        page.source == "pymupdf_text_layer"
        and page.text.strip()
        and page.words
        and page.coordinate_space == "pdf_points_top_left"
        and page.evidence_status == "available"
        for page in adapter_pages
    ):
        return result

    canonical_text = "\n".join(page.text for page in adapter_pages)
    aligned_pages = _align_page_evidence(canonical_text, adapter_pages)
    if not all(
        page.evidence_status == "available"
        and page.start is not None
        and page.end is not None
        and all(word.page_start is not None and word.page_end is not None for word in page.words)
        for page in aligned_pages
    ):
        return result
    result.text = canonical_text
    result.pages = aligned_pages
    result.engine_used = "pymupdf_text_layer"
    result.engine_chain = tuple(dict.fromkeys((*result.engine_chain, "pymupdf_text_layer")))
    result.evidence_adapter = "pymupdf_text_layer"
    result.evidence_reason = "public_geometry_rebased_to_pymupdf_text_layer"
    return result
