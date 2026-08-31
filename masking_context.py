from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Iterable, Protocol, TypedDict


PAGE_MARKER_RE: Final = re.compile(r"(?im)^\s*=+\s*PAGE\s+(?P<page>\d+)(?:\s+[^=]+)?\s*=+\s*$")
SENTENCE_SPLIT_RE: Final = re.compile(r"(?<=[.!?。！？.])\s+|\n+")
SEARCH_TOKEN_RE: Final = re.compile(r"[0-9A-Za-z가-힣]{2,}")
MIN_CHUNK_SIZE: Final = 40
DEFAULT_CHUNK_SIZE: Final = 1200
DEFAULT_OVERLAP: Final = 120


class RedactionMatchLike(Protocol):
    tag: str
    text: str


class DocumentContextSummary(TypedDict):
    enabled: bool
    total_chunks: int
    page_count: int
    chunk_size: int
    overlap: int
    raw_text_saved: bool


class SafeReviewContext(TypedDict):
    context_id: str
    page: int | None
    chunk_index: int
    location_hint: str | None
    confidence: str
    raw_text_saved: bool


@dataclass(frozen=True, slots=True)
class DocumentChunk:
    context_id: str
    page: int | None
    chunk_index: int
    text: str
    start_offset: int
    end_offset: int
    location_hint: str | None


@dataclass(frozen=True, slots=True)
class DocumentContext:
    chunks: tuple[DocumentChunk, ...]
    summary: DocumentContextSummary


def _bounded_int(value: int, fallback: int, minimum: int) -> int:
    if value < minimum:
        return fallback
    return value


def _page_at_offset(markers: tuple[tuple[int, int], ...], offset: int) -> int | None:
    page: int | None = None
    for marker_offset, marker_page in markers:
        if marker_offset > offset:
            break
        page = marker_page
    return page


def _location_hint(page: int | None, chunk_index: int) -> str | None:
    if page is None:
        return f"청크 {chunk_index + 1}"
    return f"{page}페이지 / 청크 {chunk_index + 1}"


def _compact_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in normalized if ch.isalnum())


def _search_tokens(text: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens = [match.group(0) for match in SEARCH_TOKEN_RE.finditer(normalized)]
    compact = _compact_search_text(text)
    if len(compact) >= 4:
        tokens.append(compact)
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return tuple(unique)


def _safe_context_from_chunk(chunk: DocumentChunk, confidence: str) -> SafeReviewContext:
    return {
        "context_id": chunk.context_id,
        "page": chunk.page,
        "chunk_index": chunk.chunk_index,
        "location_hint": chunk.location_hint,
        "confidence": confidence,
        "raw_text_saved": False,
    }


def _search_score(
    match_text: str,
    chunk: DocumentChunk,
    compact_chunk: str,
    casefold_chunk: str,
) -> tuple[int, str] | None:
    if match_text in chunk.text:
        return 100, "exact"

    compact_query = _compact_search_text(match_text)
    if len(compact_query) >= 4 and compact_query in compact_chunk:
        return 90, "normalized"

    tokens = _search_tokens(match_text)
    if not tokens:
        return None
    hits = sum(1 for token in tokens if token in compact_chunk or token in casefold_chunk)
    if hits == 0:
        return None
    if len(tokens) > 1 and hits < min(2, len(tokens)):
        return None
    return 40 + hits * 10, "keyword"


def _best_context_for_match(
    match_text: str,
    indexed_chunks: tuple[tuple[DocumentChunk, str, str], ...],
) -> SafeReviewContext | None:
    best: tuple[int, int, int, str, DocumentChunk] | None = None
    for chunk, compact_chunk, casefold_chunk in indexed_chunks:
        scored = _search_score(match_text, chunk, compact_chunk, casefold_chunk)
        if scored is None:
            continue
        score, confidence = scored
        page_sort = chunk.page if chunk.page is not None else 1_000_000
        candidate = (score, -page_sort, -chunk.chunk_index, confidence, chunk)
        if best is None or candidate[:3] > best[:3]:
            best = candidate
    if best is None:
        return None
    return _safe_context_from_chunk(best[4], best[3])


def _context_for_offset(
    match_text: str,
    start: int,
    end: int,
    chunks: tuple[DocumentChunk, ...],
) -> SafeReviewContext | None:
    if start < 0 or end <= start:
        return None
    for chunk in chunks:
        if chunk.start_offset <= start and end <= chunk.end_offset:
            local_start = start - chunk.start_offset
            local_end = end - chunk.start_offset
            if _compact_search_text(chunk.text[local_start:local_end]) != _compact_search_text(match_text):
                return None
            return _safe_context_from_chunk(chunk, "authoritative_offset")
    return None


def _segments_with_offsets(text: str) -> list[tuple[str, int, int]]:
    segments: list[tuple[str, int, int]] = []
    cursor = 0
    for match in SENTENCE_SPLIT_RE.finditer(text):
        end = match.end()
        segment = text[cursor:end]
        if segment.strip():
            segments.append((segment, cursor, end))
        cursor = end
    tail = text[cursor:]
    if tail.strip():
        segments.append((tail, cursor, len(text)))
    return segments or [(text, 0, len(text))]


def _split_long_segment(segment: str, start_offset: int, chunk_size: int) -> list[tuple[str, int, int]]:
    parts: list[tuple[str, int, int]] = []
    for rel_start in range(0, len(segment), chunk_size):
        rel_end = min(rel_start + chunk_size, len(segment))
        text = segment[rel_start:rel_end]
        if text.strip():
            parts.append((text, start_offset + rel_start, start_offset + rel_end))
    return parts


def build_document_context(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> DocumentContext:
    effective_chunk_size = _bounded_int(chunk_size, DEFAULT_CHUNK_SIZE, MIN_CHUNK_SIZE)
    effective_overlap = max(0, min(overlap, effective_chunk_size // 3))
    markers = tuple(
        (match.start(), int(match.group("page")))
        for match in PAGE_MARKER_RE.finditer(text)
    )
    marker_offsets = {offset for offset, _page in markers}
    marker_pages = {page for _offset, page in markers}

    chunks: list[DocumentChunk] = []
    current_parts: list[str] = []
    current_start: int | None = None
    current_end = 0

    def flush(*, keep_overlap: bool = True) -> None:
        nonlocal current_parts, current_start, current_end
        if current_start is None or not current_parts:
            return
        chunk_text = "".join(current_parts)
        chunk_index = len(chunks)
        page = _page_at_offset(markers, current_start)
        chunks.append(
            DocumentChunk(
                context_id=f"ctx-{chunk_index + 1:04d}",
                page=page,
                chunk_index=chunk_index,
                text=chunk_text,
                start_offset=current_start,
                end_offset=current_end,
                location_hint=_location_hint(page, chunk_index),
            )
        )
        if effective_overlap <= 0 or not keep_overlap:
            current_parts = []
            current_start = None
            current_end = 0
            return
        overlap_text = chunk_text[-effective_overlap:]
        overlap_start = max(current_end - len(overlap_text), current_start)
        current_parts = [overlap_text] if overlap_text.strip() else []
        current_start = overlap_start if current_parts else None

    for segment, start, end in _segments_with_offsets(text):
        pieces = (
            _split_long_segment(segment, start, effective_chunk_size)
            if len(segment) > effective_chunk_size
            else [(segment, start, end)]
        )
        for piece, piece_start, piece_end in pieces:
            if piece_start in marker_offsets and current_parts:
                flush(keep_overlap=False)
            if current_start is not None and current_parts and current_end + len(piece) - current_start > effective_chunk_size:
                flush()
            if current_start is None:
                current_start = piece_start
            current_parts.append(piece)
            current_end = piece_end

    flush()

    summary: DocumentContextSummary = {
        "enabled": True,
        "total_chunks": len(chunks),
        "page_count": len(marker_pages),
        "chunk_size": effective_chunk_size,
        "overlap": effective_overlap,
        "raw_text_saved": False,
    }
    return DocumentContext(chunks=tuple(chunks), summary=summary)


def find_masking_context(
    matches: Iterable[RedactionMatchLike],
    document_context: DocumentContext,
) -> tuple[SafeReviewContext | None, ...]:
    contexts: list[SafeReviewContext | None] = []
    seen: set[tuple[str, str, int, int]] = set()
    indexed_chunks: tuple[tuple[DocumentChunk, str, str], ...] | None = None
    for match in matches:
        start = getattr(match, "start", -1)
        end = getattr(match, "end", -1)
        authoritative_start = start if isinstance(start, int) else -1
        authoritative_end = end if isinstance(end, int) else -1
        key = (match.tag, match.text, authoritative_start, authoritative_end)
        if key in seen or not match.text:
            contexts.append(None)
            continue
        seen.add(key)
        authoritative_context = _context_for_offset(
            match.text,
            authoritative_start,
            authoritative_end,
            document_context.chunks,
        )
        if authoritative_context is not None:
            contexts.append(authoritative_context)
            continue
        if authoritative_start != -1 or authoritative_end != -1:
            contexts.append(None)
            continue
        if indexed_chunks is None:
            indexed_chunks = tuple(
                (chunk, _compact_search_text(chunk.text), chunk.text.casefold())
                for chunk in document_context.chunks
            )
        contexts.append(_best_context_for_match(match.text, indexed_chunks))
    return tuple(contexts)


def safe_document_context_summary(document_context: DocumentContext | None) -> dict[str, DocumentContextSummary | bool]:
    if document_context is None:
        return {
            "enabled": False,
            "summary": {
                "enabled": False,
                "total_chunks": 0,
                "page_count": 0,
                "chunk_size": 0,
                "overlap": 0,
                "raw_text_saved": False,
            },
        }
    return {
        "enabled": True,
        "summary": document_context.summary,
    }
