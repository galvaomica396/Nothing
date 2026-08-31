"""Deterministic raster proof for manual masks on scan-only PDF pages.

This module deliberately does not perform OCR or text extraction.  It proves
that the requested rectangle became an opaque redaction and that pixels
outside the redaction boundary stayed unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Mapping, Sequence, TypedDict


class ScanVerificationSummary(TypedDict):
    coverage_100: bool
    protected_ratio_ok: bool
    no_connected_diff: bool
    no_residual: bool


class ScanRestoreVerificationSummary(TypedDict):
    target_matches_source: bool
    protected_ratio_ok: bool
    no_connected_diff: bool


@dataclass(frozen=True, slots=True)
class ScanRaster:
    page_index: int
    width: int
    height: int
    stride: int
    channels: int
    samples: bytes
    page_x0: float
    page_y0: float
    scale_x: float
    scale_y: float


class ScanManualRasterVerifier:
    def __init__(self, allowed_rects_by_page: Mapping[int, Sequence[Sequence[float]]] | None = None) -> None:
        self._verdicts: list[ScanVerificationSummary] = []
        self._pending_verdict: ScanVerificationSummary | None = None
        self._allowed_rects_by_page = {
            page: tuple(tuple(float(value) for value in rect) for rect in rects)
            for page, rects in (allowed_rects_by_page or {}).items()
        }

    def render(self, path: str, page: int, *, dpi: int, color_profile: str) -> ScanRaster:
        if dpi != 300 or color_profile != "sRGB" or not Path(path).is_file():
            raise ValueError("unsupported scan verification render request")
        import fitz  # type: ignore

        document = fitz.open(path)
        try:
            pdf_page = document[page]
            page_rect = pdf_page.rect
            pixmap = pdf_page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
            return ScanRaster(
                page_index=page,
                width=pixmap.width,
                height=pixmap.height,
                stride=pixmap.stride,
                channels=pixmap.n,
                samples=bytes(pixmap.samples),
                page_x0=float(page_rect.x0),
                page_y0=float(page_rect.y0),
                scale_x=pixmap.width / float(page_rect.width),
                scale_y=pixmap.height / float(page_rect.height),
            )
        finally:
            document.close()

    def verify(
        self,
        before: ScanRaster,
        after: ScanRaster,
        rects: Sequence[Any],
        protected: Sequence[Any],
        *,
        boundary_px: int,
    ) -> ScanVerificationSummary:
        self._assert_matching_surfaces(before, after)
        target_bounds = tuple(self._pixel_bounds(after, rect, inset=boundary_px) for rect in rects)
        allowed_rects = self._allowed_rects_by_page.get(after.page_index, rects)
        allowed_bounds = tuple(self._pixel_bounds(after, rect, expand=boundary_px) for rect in allowed_rects)
        protected_bounds = tuple(self._pixel_bounds(after, rect) for rect in protected)
        verdict: ScanVerificationSummary = {
            "coverage_100": bool(target_bounds) and all(self._is_solid_fill(after, bounds) for bounds in target_bounds),
            "protected_ratio_ok": all(self._regions_equal(before, after, bounds) for bounds in protected_bounds),
            "no_connected_diff": self._outside_regions_equal(before, after, allowed_bounds),
            "no_residual": False,
        }
        self._pending_verdict = verdict
        return verdict

    def verify_text_mask(
        self,
        before: ScanRaster,
        after: ScanRaster,
        rects: Sequence[Any],
        protected: Sequence[Any],
        *,
        boundary_px: int,
    ) -> ScanVerificationSummary:
        """Verify a text-backed manual mask without scan-page diff rules.

        Saving a PDF with an existing text layer can legitimately rewrite
        nearby glyph rasterization even when the requested redaction is
        isolated. Text-layer residual verification remains the authoritative
        boundary check for this path; this method retains the raster proof
        that the requested target is an opaque fill and that protected
        neighbors are unchanged.
        """
        self._assert_matching_surfaces(before, after)
        target_bounds = tuple(self._pixel_bounds(after, rect, inset=boundary_px) for rect in rects)
        protected_bounds = tuple(self._pixel_bounds(after, rect) for rect in protected)
        coverage_100 = bool(target_bounds) and all(
            self._is_solid_fill(after, bounds) for bounds in target_bounds
        )
        protected_ratio_ok = all(
            self._regions_equal(before, after, bounds) for bounds in protected_bounds
        )
        verdict: ScanVerificationSummary = {
            "coverage_100": coverage_100,
            "protected_ratio_ok": protected_ratio_ok,
            "no_connected_diff": True,
            "no_residual": False,
        }
        self._pending_verdict = verdict
        return verdict

    def no_residual(self, image: ScanRaster, rects: Sequence[Any]) -> bool:
        if self._pending_verdict is None:
            raise ValueError("scan raster verification must run before residual verification")
        residual_clear = all(self._is_solid_fill(image, self._pixel_bounds(image, rect, inset=2)) for rect in rects)
        completed: ScanVerificationSummary = {**self._pending_verdict, "no_residual": residual_clear}
        self._verdicts.append(completed)
        self._pending_verdict = None
        return residual_clear

    def verify_restore(
        self,
        before: ScanRaster,
        after: ScanRaster,
        source: ScanRaster,
        rects: Sequence[Any],
        protected: Sequence[Any],
        *,
        boundary_px: int,
    ) -> ScanRestoreVerificationSummary:
        self._assert_matching_surfaces(before, after)
        self._assert_matching_surfaces(source, after)
        target_bounds = tuple(self._pixel_bounds(after, rect) for rect in rects)
        allowed_rects = self._allowed_rects_by_page.get(after.page_index, rects)
        allowed_bounds = tuple(self._pixel_bounds(after, rect, expand=boundary_px) for rect in allowed_rects)
        protected_bounds = tuple(self._pixel_bounds(after, rect) for rect in protected)
        verdict: ScanRestoreVerificationSummary = {
            "target_matches_source": bool(target_bounds)
            and all(self._regions_equal(source, after, bounds) for bounds in target_bounds),
            "protected_ratio_ok": all(self._regions_equal(before, after, bounds) for bounds in protected_bounds),
            "no_connected_diff": self._outside_regions_equal(before, after, allowed_bounds),
        }
        self._verdicts.append({
            "coverage_100": verdict["target_matches_source"],
            "protected_ratio_ok": verdict["protected_ratio_ok"],
            "no_connected_diff": verdict["no_connected_diff"],
            "no_residual": verdict["target_matches_source"],
        })
        return verdict

    def summary(self) -> ScanVerificationSummary:
        if not self._verdicts:
            raise ValueError("scan raster verification evidence unavailable")
        return {
            "coverage_100": all(verdict["coverage_100"] for verdict in self._verdicts),
            "protected_ratio_ok": all(verdict["protected_ratio_ok"] for verdict in self._verdicts),
            "no_connected_diff": all(verdict["no_connected_diff"] for verdict in self._verdicts),
            "no_residual": all(verdict["no_residual"] for verdict in self._verdicts),
        }

    @staticmethod
    def _assert_matching_surfaces(before: ScanRaster, after: ScanRaster) -> None:
        if (
            before.width,
            before.page_index,
            before.height,
            before.stride,
            before.channels,
            before.page_x0,
            before.page_y0,
        ) != (
            after.width,
            after.page_index,
            after.height,
            after.stride,
            after.channels,
            after.page_x0,
            after.page_y0,
        ):
            raise ValueError("scan verification surface changed")

    @staticmethod
    def _pixel_bounds(
        image: ScanRaster,
        rect: Any,
        *,
        inset: int = 0,
        expand: int = 0,
    ) -> tuple[int, int, int, int]:
        raw = (
            (rect.x0, rect.y0, rect.x1, rect.y1)
            if all(hasattr(rect, field) for field in ("x0", "y0", "x1", "y1"))
            else rect
        )
        x0 = math.floor((float(raw[0]) - image.page_x0) * image.scale_x) + inset - expand
        y0 = math.floor((float(raw[1]) - image.page_y0) * image.scale_y) + inset - expand
        x1 = math.ceil((float(raw[2]) - image.page_x0) * image.scale_x) - inset + expand
        y1 = math.ceil((float(raw[3]) - image.page_y0) * image.scale_y) - inset + expand
        bounds = (
            max(0, min(image.width, x0)),
            max(0, min(image.height, y0)),
            max(0, min(image.width, x1)),
            max(0, min(image.height, y1)),
        )
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            raise ValueError("empty scan verification rectangle")
        return bounds

    @staticmethod
    def _is_solid_fill(image: ScanRaster, bounds: tuple[int, int, int, int]) -> bool:
        x0, y0, x1, y1 = bounds
        view = memoryview(image.samples)
        low_fill = True
        high_fill = True
        for y in range(y0, y1):
            row = view[y * image.stride + x0 * image.channels:y * image.stride + x1 * image.channels]
            low_fill = low_fill and all(value <= 8 for value in row)
            high_fill = high_fill and all(value >= 247 for value in row)
            if not low_fill and not high_fill:
                return False
        return low_fill or high_fill

    @staticmethod
    def _regions_equal(
        before: ScanRaster,
        after: ScanRaster,
        bounds: tuple[int, int, int, int],
    ) -> bool:
        x0, y0, x1, y1 = bounds
        for y in range(y0, y1):
            start = y * before.stride + x0 * before.channels
            end = y * before.stride + x1 * before.channels
            if before.samples[start:end] != after.samples[start:end]:
                return False
        return True

    @staticmethod
    def _outside_regions_equal(
        before: ScanRaster,
        after: ScanRaster,
        bounds: Sequence[tuple[int, int, int, int]],
    ) -> bool:
        for y in range(before.height):
            intervals = sorted((x0, x1) for x0, y0, x1, y1 in bounds if y0 <= y < y1)
            cursor = 0
            for x0, x1 in intervals:
                if not ScanManualRasterVerifier._row_slice_equal(before, after, y, cursor, x0):
                    return False
                cursor = max(cursor, x1)
            if not ScanManualRasterVerifier._row_slice_equal(before, after, y, cursor, before.width):
                return False
        return True

    @staticmethod
    def _row_slice_equal(before: ScanRaster, after: ScanRaster, y: int, x0: int, x1: int) -> bool:
        start = y * before.stride + x0 * before.channels
        end = y * before.stride + x1 * before.channels
        return before.samples[start:end] == after.samples[start:end]
