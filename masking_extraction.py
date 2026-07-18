#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PDF/text extraction engine extracted from document_masker_ocr_gui.

Behavior-preserving move of the marker/paddle/pymupdf4llm/pypdf extraction
engines plus their cleanup wrappers and the plain-text IO helpers.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


def read_text_file(path: str) -> str:
    encodings = ["utf-8", "cp949", "euc-kr"]
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except Exception:
            continue
    raise RuntimeError(f"파일 인코딩을 읽을 수 없습니다: {path}")


def write_text_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


@dataclass
class ExtractResult:
    text: str
    engine_used: str
    duration_sec: float
    notes: list[str]


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
    finally 에서 디렉터리를 제거한다. 정리 실패는 무시하되 로그로 남긴다.
    """
    work = tempfile.mkdtemp(prefix="marker_")
    try:
        return _extract_pdf_with_marker(pdf_path, work)
    finally:
        try:
            shutil.rmtree(work, ignore_errors=False)
        except Exception:  # noqa: BLE001 - 정리 실패는 치명적이지 않음
            sys.stderr.write("[marker] TEMP_RESOURCE_CLEANUP_FAILED\n")


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

    md_text = md_candidates[0].read_text(encoding="utf-8", errors="ignore")
    return ExtractResult(
        text=md_text,
        engine_used="marker-pdf",
        duration_sec=time.time() - start,
        notes=["표/레이아웃 유지 목적 추출(markdown)"]
    )


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
    return ExtractResult(
        text=md,
        engine_used="pymupdf4llm",
        duration_sec=time.time() - start,
        notes=["텍스트 기반 PDF에 유리", "OCR 미포함"]
    )


def _extract_pdf_with_paddle(pdf_path: str) -> ExtractResult:
    """
    PaddleOCR 추출.
    - paddleocr가 PDF 직접 입력을 처리할 수 있는 버전에서는 pdf_path를 그대로 사용
    - 실패 시 명확한 에러 메시지 제공
    """
    start = time.time()
    try:
        from paddleocr import PaddleOCR  # type: ignore
    except Exception:
        raise RuntimeError("EXTRACTION_PADDLE_UNAVAILABLE") from None

    try:
        # paddleocr 3.x: use_textline_orientation 권장
        ocr = PaddleOCR(lang="korean", use_textline_orientation=True)
    except TypeError:
        # 구버전 호환
        ocr = PaddleOCR(lang="korean", use_angle_cls=True)
    except Exception:
        raise RuntimeError("EXTRACTION_PADDLE_INIT_FAILED") from None

    try:
        result = ocr.ocr(pdf_path, cls=True)
    except Exception:
        raise RuntimeError("EXTRACTION_PADDLE_FAILED") from None

    lines: list[str] = []
    # result 형식: 페이지별 [[box, (text, score)], ...]
    for page_idx, page_items in enumerate(result or [], 1):
        lines.append(f"\n\n===== PAGE {page_idx} (paddle) =====")
        if not page_items:
            continue
        for item in page_items:
            try:
                text = item[1][0]
            except Exception:
                text = ""
            if text:
                lines.append(text)

    text = "\n".join(lines).strip()
    if not text:
        raise RuntimeError("EXTRACTION_PADDLE_EMPTY")

    return ExtractResult(
        text=text,
        engine_used="paddleocr",
        duration_sec=time.time() - start,
        notes=["한글 OCR 중심", "표/레이아웃 보존은 marker 대비 제한적일 수 있음"],
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
        for i, page in enumerate(reader.pages, 1):
            t = page.extract_text() or ""
            texts.append(f"\n\n===== PAGE {i} =====\n{t}")
    except Exception:
        raise RuntimeError("EXTRACTION_PYPDF_FAILED") from None

    return ExtractResult(
        text="".join(texts),
        engine_used="pypdf",
        duration_sec=time.time() - start,
        notes=["빠른 텍스트 추출", "표/레이아웃 보존 약함"]
    )


def extract_document(path: str, engine: str = "auto") -> ExtractResult:
    ext = Path(path).suffix.lower()
    if ext in {".txt", ".md", ".csv", ".log"}:
        t0 = time.time()
        return ExtractResult(
            text=read_text_file(path),
            engine_used="plain-text",
            duration_sec=time.time() - t0,
            notes=[]
        )

    if ext != ".pdf":
        raise RuntimeError("지원 형식은 현재 txt/md/csv/log/pdf 입니다.")

    if engine not in {"auto", "marker", "paddle", "pymupdf", "pypdf"}:
        raise RuntimeError("EXTRACTION_ENGINE_UNSUPPORTED")

    if engine == "marker":
        return _extract_pdf_with_marker_cleanup(path)
    if engine == "paddle":
        return _extract_pdf_with_paddle(path)
    if engine == "pymupdf":
        return _extract_pdf_with_pymupdf4llm(path)
    if engine == "pypdf":
        return _extract_pdf_with_pypdf(path)

    # auto
    try:
        return _extract_pdf_with_marker_cleanup(path)
    except Exception:
        pass

    try:
        return _extract_pdf_with_paddle(path)
    except Exception:
        pass

    try:
        return _extract_pdf_with_pymupdf4llm(path)
    except Exception:
        pass

    try:
        return _extract_pdf_with_pypdf(path)
    except Exception:
        pass

    raise RuntimeError("EXTRACTION_ALL_ENGINES_FAILED")
