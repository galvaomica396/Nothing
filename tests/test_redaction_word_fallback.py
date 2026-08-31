"""Regression tests for C-5 (word-bbox fallback + scanned-PDF failure, E2-1/E2-2).

redact_pdf_native() detects PII in extracted OCR/markdown text but must find
matching rects by re-searching the *original* PDF text layer. When the PDF
text layer diverges from the detected string (자간/개행 삽입 등), plain
page.search_for() misses the match and the name/value survives un-redacted
in the output PDF (a critical under-masking bug). These tests cover:

  (a) 자간 삽입 이름 ("홍 길 동" in the PDF vs detected "홍길동") is recovered by
      the word-bbox fallback and passes post-verification.
  (b) A phone number split across two lines by a newline is recovered the
      same way, producing one rect per line (matching search_for's own
      multi-line phrase semantics).
  (c) Plain, non-split text keeps using the existing search_for path and
      registers *zero* fallback rects (no regression on the common case).
  (d) An image-only (scanned) page with no text layer fails with a clear,
      dedicated error/reason code instead of a generic "no annotations"
      exception.
  (e) A value that is absent from the PDF altogether (fallback also fails)
      still surfaces as `missing_pdf_rect`, and the fallback counter stays 0
      for that term.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import fitz

import document_masker_ocr_gui as masker
import masking_redaction
from masking_redaction import (
    _RESIDUAL_FUZZY_MIN_NORMALIZED_LEN,
    _verify_redaction_output,
    ScannedPdfRedactionError,
)
from masking_reporting import evaluate_quality_gate
from pdf_redaction_rendering import korean_pdf_font_file


def pdf_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()
def redact_legal_fallback(source: Path, output: Path, matches: list[masker.RedactionMatch], display_mode: str = "black") -> dict[str, object]:
    """Exercise legacy text-layer matching only through its legal compatibility seam."""
    return masker.redact_pdf_native(
        str(source), str(output), matches, display_mode=display_mode, profile="legal", legal_compatibility=True
    )




def _insert_korean(page: "fitz.Page", pos: tuple[float, float], text: str, fontsize: float = 14) -> None:
    font_file = korean_pdf_font_file()
    if font_file:
        page.insert_text(pos, text, fontsize=fontsize, fontname="testko", fontfile=font_file)
    else:  # pragma: no cover - only exercised on machines without a CJK font
        page.insert_text(pos, text, fontsize=fontsize)


class WordBboxFallbackTests(unittest.TestCase):
    def test_public_call_cannot_use_legacy_match_strings_as_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "public.pdf"
            output = root / "out.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((32, 52), "anchor phone 010-9999-8888")
            doc.save(source)
            doc.close()

            for profile, legal_compatibility in (("mixed", False), ("mixed", True), ("legal", False)):
                with self.subTest(profile=profile, legal_compatibility=legal_compatibility):
                    output.unlink(missing_ok=True)
                    with self.assertRaisesRegex(ValueError, "^PUBLIC_OCCURRENCE_INPUTS_REQUIRED$"):
                        masker.redact_pdf_native(
                            str(source),
                            str(output),
                            [masker.RedactionMatch("PHONE", "010-9999-8888")],
                            profile=profile,
                            legal_compatibility=legal_compatibility,
                        )
                    self.assertFalse(output.exists())

    def test_legal_compatibility_search_redacts_global_exact_occurrences_not_near_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "legal-source.pdf", root / "legal-output.pdf"
            doc = fitz.open()
            first = doc.new_page(width=320, height=180)
            first.insert_text((32, 52), "exact TOKEN-42")
            first.insert_text((32, 92), "near TOKEN-420 remains")
            second = doc.new_page(width=320, height=180)
            second.insert_text((32, 52), "second exact TOKEN-42")
            second.insert_text((32, 92), "anchor control")
            doc.save(source)
            doc.close()

            result = redact_legal_fallback(source, output, [masker.RedactionMatch("ID", "TOKEN-42")])

            self.assertEqual("applied", result["status"])
            self.assertEqual(2, result["annotations_added"])
            self.assertTrue(result["verification"]["verified"])
            rendered = pdf_text(output)
            self.assertIn("TOKEN-420 remains", rendered)
            self.assertIn("anchor control", rendered)

    def test_word_index_is_built_once_per_page_in_each_document_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "cache_source.pdf"
            output = root / "cache_output.pdf"

            doc = fitz.open()
            for page_num in range(2):
                page = doc.new_page(width=320, height=180)
                page.insert_text((32, 52), f"page {page_num + 1} name Alice Example")
                page.insert_text((32, 92), f"page {page_num + 1} phone 010-1111-2222")
                page.insert_text((32, 132), "anchor cache control")
            doc.save(source)
            doc.close()

            matches = [
                masker.RedactionMatch("NAME", "Alice Example"),
                masker.RedactionMatch("PHONE", "010-1111-2222"),
            ]
            original_builder = masking_redaction._build_page_word_index
            with mock.patch.object(
                masking_redaction,
                "_build_page_word_index",
                wraps=original_builder,
            ) as build_index:
                result = redact_legal_fallback(source, output, matches)

            self.assertEqual("applied", result["status"])
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(4, build_index.call_count)
            rendered = pdf_text(output)
            self.assertIn("anchor cache control", rendered)
            self.assertNotIn("Alice Example", rendered)
            self.assertNotIn("010-1111-2222", rendered)

    def test_kerned_korean_name_recovered_by_word_fallback(self) -> None:
        if korean_pdf_font_file() is None:
            self.skipTest("no Korean-capable font available on this machine")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "kerned_name.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=240, height=180)
            # PDF text layer stores the name with inter-character spacing
            # (자간 삽입) — a common OCR/legacy-export artifact.
            _insert_korean(page, (32, 52), "홍 길 동")
            _insert_korean(page, (32, 92), "anchor control")
            doc.save(source)
            doc.close()

            # Detection ran against OCR/markdown text where the name appears
            # compact, with no spacing.
            matches = [masker.RedactionMatch("NAME", "홍길동")]

            # Plain search_for must fail for the compact form (proves the
            # scenario is real, not a trivially-passing test).
            probe = fitz.open(source)
            try:
                self.assertEqual([], probe[0].search_for("홍길동"))
            finally:
                probe.close()

            result = redact_legal_fallback(source, output, matches)

            self.assertEqual("applied", result["status"])
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(0, result["verification"]["residual_hits"])
            self.assertEqual(1, result["targets_hit"])
            self.assertEqual(0, result["missing_targets_count"])
            self.assertGreaterEqual(result["rects_from_word_fallback"], 1)
            self.assertTrue(output.exists())

            rendered = pdf_text(output)
            self.assertNotIn("홍", rendered)
            self.assertNotIn("길", rendered)
            self.assertNotIn("동", rendered)
            self.assertIn("anchor control", rendered.replace("\xa0", " "))

            fallback_items = [
                item for item in result["review_items"] if item.get("match_source") == "word_bbox_fallback"
            ]
            self.assertGreaterEqual(len(fallback_items), 1)

    def test_same_page_second_occurrence_in_different_form_is_not_left_unredacted(self) -> None:
        # Regression for a same-page multi-occurrence under-cover bug: the
        # word-bbox fallback used to be gated on "search_for found nothing
        # on this page at all" (page_hits == 0). If the first occurrence of
        # a value was found in plain form by search_for, a *second*
        # occurrence on the *same page* in a differently-kerned form (a
        # realistic body-text-vs-signature-block artifact) was silently
        # skipped -- and post-verification (which also only uses
        # search_for) did not catch the still-present residual either, so
        # `verified` came back True while real PII remained visible.
        if korean_pdf_font_file() is None:
            self.skipTest("no Korean-capable font available on this machine")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "same_page_dup.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=420, height=260)
            # First occurrence: plain form, directly findable by search_for.
            _insert_korean(page, (36, 60), "성명: 홍길동")
            # Second occurrence, same page, kerned form only the word-bbox
            # fallback can recover (e.g. a signature block further down).
            _insert_korean(page, (36, 160), "서 명 란: 홍 길 동")
            _insert_korean(page, (36, 210), "anchor control")
            doc.save(source)
            doc.close()

            matches = [masker.RedactionMatch("NAME", "홍길동")]
            result = redact_legal_fallback(source, output, matches)

            self.assertEqual("applied", result["status"])
            self.assertEqual(2, result["annotations_added"], "both occurrences must be redacted")
            self.assertGreaterEqual(result["rects_from_word_fallback"], 1)
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(0, result["verification"]["residual_hits"])

            rendered = pdf_text(output)
            self.assertNotIn("홍", rendered)
            self.assertNotIn("길", rendered)
            self.assertNotIn("동", rendered)
            self.assertIn("anchor control", rendered.replace("\xa0", " "))

    def test_newline_split_phone_number_recovered_by_word_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "split_phone.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=240, height=180)
            # The value is split across two separate lines (개행 분리),
            # exactly the string "010-1234-" + "5678" with no separating
            # whitespace so the concatenated normalized form matches.
            page.insert_text((32, 52), "010-1234-")
            page.insert_text((32, 84), "5678")
            page.insert_text((32, 124), "anchor control")
            doc.save(source)
            doc.close()

            matches = [masker.RedactionMatch("PHONE", "010-1234-5678")]

            probe = fitz.open(source)
            try:
                self.assertEqual([], probe[0].search_for("010-1234-5678"))
            finally:
                probe.close()

            result = redact_legal_fallback(source, output, matches)

            self.assertEqual("applied", result["status"])
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(1, result["targets_hit"])
            self.assertEqual(0, result["missing_targets_count"])
            self.assertGreaterEqual(result["rects_from_word_fallback"], 2, "expects one rect per line")

            rendered = pdf_text(output)
            self.assertNotIn("010-1234-", rendered)
            self.assertNotIn("5678", rendered)
            self.assertIn("anchor control", rendered)

    def test_plain_text_regression_uses_search_for_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "plain.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=240, height=180)
            page.insert_text((32, 52), "phone 010-9999-8888")
            page.insert_text((32, 92), "anchor control")
            doc.save(source)
            doc.close()

            matches = [masker.RedactionMatch("PHONE", "010-9999-8888")]
            result = redact_legal_fallback(source, output, matches)

            self.assertEqual("applied", result["status"])
            self.assertTrue(result["verification"]["verified"])
            self.assertEqual(1, result["targets_hit"])
            self.assertEqual(0, result["rects_from_word_fallback"])
            self.assertNotIn("010-9999-8888", pdf_text(output))
            self.assertIn("anchor control", pdf_text(output))

    def test_fallback_miss_still_reports_missing_pdf_rect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "partial_miss.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=240, height=180)
            page.insert_text((32, 52), "phone 010-9999-8888")
            page.insert_text((32, 92), "anchor control")
            doc.save(source)
            doc.close()

            matches = [
                masker.RedactionMatch("PHONE", "010-9999-8888"),
                # This value is absent from the PDF entirely -- both
                # search_for and the word fallback must fail for it.
                masker.RedactionMatch("NAME", "완전히다른이름"),
            ]
            result = redact_legal_fallback(source, output, matches)

            self.assertEqual(1, result["targets_hit"])
            self.assertEqual(1, result["missing_targets_count"])
            self.assertEqual(0, result["rects_from_word_fallback"])
            missing_items = [item for item in result["review_items"] if item.get("status") == "missing_pdf_rect"]
            self.assertEqual(1, len(missing_items))
            self.assertEqual("NAME", missing_items[0]["tag"])
            self.assertFalse(result["verification"]["verified"], "unresolved missing target must fail the gate")
            self.assertFalse(output.exists(), "failed fallback output must be cleaned")


class PostVerificationFuzzyResidualTests(unittest.TestCase):
    """R1: 사후검증(출력 PDF 잔존 검사)이 exact/compact search_for 만 쓰던 갭을,
    레닥션 폴백과 동일한 정규화/워드 시퀀스 매칭으로 닫는지 검증한다.

    검증 단계만 독립적으로 확인하기 위해, 잔존 변형을 인위로 남긴 "출력 PDF"를
    직접 만들어 내부 함수 ``_verify_redaction_output`` 를 호출한다(레닥션 폴백과
    사후검증은 같은 함수를 쓰므로, 정상 파이프라인에서는 둘이 대칭이다 — 여기서는
    검증기가 실제로 exact 가 놓친 변형 잔존을 잡아내는지를 격리해서 본다).
    """

    def test_fuzzy_residual_variant_caught_when_exact_search_misses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "residual.pdf"
            # 잔존 전화번호가 개행으로 쪼개져 있어("010-1234-" + "5678"),
            # exact/compact search_for("010-1234-5678"/"01012345678") 는 모두
            # 놓치지만 정규화 워드 연결("010-1234-5678")은 매칭된다.
            doc = fitz.open()
            page = doc.new_page(width=240, height=180)
            page.insert_text((32, 52), "010-1234-")
            page.insert_text((32, 84), "5678")
            doc.save(output)
            doc.close()

            probe = fitz.open(output)
            try:
                self.assertEqual([], probe[0].search_for("010-1234-5678"))
                self.assertEqual([], probe[0].search_for("01012345678"))
            finally:
                probe.close()

            terms = [masker.RedactionMatch("PHONE", "010-1234-5678")]
            residual_hits, residual_fuzzy_hits, residual_terms, review_items = _verify_redaction_output(
                fitz, str(output), terms, "black"
            )

            self.assertEqual(0, residual_hits, "exact/compact search_for must miss the split form")
            self.assertGreaterEqual(residual_fuzzy_hits, 1, "fuzzy verifier must catch the residual variant")
            self.assertEqual([terms[0]], residual_terms)
            fuzzy_items = [it for it in review_items if it.get("match_source") == "word_bbox_fallback"]
            self.assertGreaterEqual(len(fuzzy_items), 1)
            self.assertTrue(all(it["status"] == "residual_found" for it in fuzzy_items))

    def test_verifier_catches_variant_survivor_of_real_redaction(self) -> None:
        # End-to-end shape of the C-5 residual gap: reproduce "레닥션이 한 형태만
        # 잡고 다른 형태가 남는" by disabling the word-bbox fallback *while
        # redacting* (mock returns no fallback groups), so redact_pdf_native
        # only removes the plain first occurrence via search_for and the split
        # second occurrence survives in the output. Then run the real verifier
        # on that output and confirm its fuzzy pass catches the survivor that
        # exact/compact search_for cannot.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=320, height=220)
            page.insert_text((32, 52), "tel 010-4444-5555")  # plain, redacted
            page.insert_text((32, 190), "anchor control")
            page.insert_text((32, 120), "010-4444-")  # split survivor line 1
            page.insert_text((32, 150), "5555")  # split survivor line 2
            doc.save(source)
            doc.close()

            matches = [masker.RedactionMatch("PHONE", "010-4444-5555")]

            with mock.patch.object(
                masking_redaction,
                "_page_word_fallback_rect_groups",
                return_value=[],
            ):
                redact_legal_fallback(source, output, matches)

            # Real verifier (fallback no longer mocked) on the produced output.
            residual_hits, residual_fuzzy_hits, _terms, _items = _verify_redaction_output(
                fitz, str(output), matches, "black"
            )
            self.assertEqual(0, residual_hits, "exact/compact must miss the split residual")
            self.assertGreaterEqual(residual_fuzzy_hits, 1, "real fuzzy verifier must catch the survivor")
            self.assertIn("anchor control", pdf_text(output))

    def test_clean_output_has_no_fuzzy_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "clean.pdf"
            doc = fitz.open()
            page = doc.new_page(width=320, height=200)
            # Legitimate, non-target body text only -- no target value present
            # in any form.
            page.insert_text((32, 52), "This document contains no sensitive values.")
            page.insert_text((32, 90), "reference code AB-9090-0000 is public")
            doc.save(output)
            doc.close()

            terms = [
                masker.RedactionMatch("PHONE", "010-1234-5678"),
                masker.RedactionMatch("ACCOUNT", "110-222-333444"),
            ]
            residual_hits, residual_fuzzy_hits, residual_terms, review_items = _verify_redaction_output(
                fitz, str(output), terms, "black"
            )
            self.assertEqual(0, residual_hits)
            self.assertEqual(0, residual_fuzzy_hits)
            self.assertEqual([], residual_terms)
            self.assertEqual([], review_items)

    def test_short_value_guard_skips_fuzzy_to_avoid_false_positives(self) -> None:
        # A short (2-3 char) normalized target is *not* fuzzy-checked: its
        # exact form is already covered by the exact/compact pass, and a short
        # normalized substring is too collision-prone against legitimate
        # remaining body text. Here a spaced-out short name evades exact
        # search_for; the fuzzy guard intentionally does not flag it.
        if korean_pdf_font_file() is None:
            self.skipTest("no Korean-capable font available on this machine")
        self.assertGreater(_RESIDUAL_FUZZY_MIN_NORMALIZED_LEN, 3)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "short.pdf"
            doc = fitz.open()
            page = doc.new_page(width=240, height=160)
            _insert_korean(page, (32, 52), "홍 길 동")  # normalized len 3
            doc.save(output)
            doc.close()

            probe = fitz.open(output)
            try:
                self.assertEqual([], probe[0].search_for("홍길동"))
            finally:
                probe.close()

            terms = [masker.RedactionMatch("NAME", "홍길동")]
            residual_hits, residual_fuzzy_hits, _terms, _items = _verify_redaction_output(
                fitz, str(output), terms, "black"
            )
            self.assertEqual(0, residual_hits)
            self.assertEqual(0, residual_fuzzy_hits, "short values are excluded from the fuzzy net")

    def test_partial_fragment_of_removed_value_is_not_flagged(self) -> None:
        # partial 정책: 완전 제거 대상은 전체 값(예 RRN)뿐이다. 의도적으로 남긴
        # 앞자리 조각("800101")은 검증 대상(search_terms)이 아니며, 전체 값의
        # 정규화 문자열이 출력에 존재하지 않으므로 잔존으로 오판되면 안 된다.
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "partial.pdf"
            doc = fitz.open()
            page = doc.new_page(width=320, height=180)
            # The full RRN was fully redacted; only a partial masked display
            # remains (front 6 digits kept, tail masked).
            page.insert_text((32, 52), "800101-*******")
            doc.save(output)
            doc.close()

            terms = [masker.RedactionMatch("RRN", "800101-1234567")]
            residual_hits, residual_fuzzy_hits, residual_terms, review_items = _verify_redaction_output(
                fitz, str(output), terms, "black"
            )
            self.assertEqual(0, residual_hits)
            self.assertEqual(0, residual_fuzzy_hits)
            self.assertEqual([], residual_terms)
            self.assertEqual([], review_items)

    def test_normal_document_reports_zero_fuzzy_and_passes_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "plain.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=260, height=180)
            page.insert_text((32, 52), "phone 010-9999-8888")
            page.insert_text((32, 92), "anchor control")
            doc.save(source)
            doc.close()

            matches = [masker.RedactionMatch("PHONE", "010-9999-8888")]
            result = redact_legal_fallback(source, output, matches)

            self.assertTrue(result["verification"]["verified"])
            self.assertIn("residual_fuzzy_hits", result["verification"])
            self.assertEqual(0, result["verification"]["residual_fuzzy_hits"])
            self.assertTrue(evaluate_quality_gate(result))
            rendered = pdf_text(output)
            self.assertIn("anchor control", rendered)
            self.assertNotIn("010-9999-8888", rendered)


class ScannedPdfFailureTests(unittest.TestCase):
    def test_image_only_page_fails_with_dedicated_scanned_pdf_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "scanned.pdf"
            output = root / "out.pdf"

            doc = fitz.open()
            page = doc.new_page(width=240, height=180)
            # No insert_text at all -- only an embedded raster image, like a
            # scanned document with no text layer.
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40))
            pix.set_rect(pix.irect, (255, 255, 255))
            page.insert_image(fitz.Rect(20, 20, 220, 160), pixmap=pix)
            doc.save(source)
            doc.close()

            matches = [masker.RedactionMatch("NAME", "홍길동")]

            with self.assertRaises(ScannedPdfRedactionError) as ctx:
                redact_legal_fallback(source, output, matches)

            self.assertIn("스캔", str(ctx.exception))
            self.assertIn("수동 마스킹", str(ctx.exception))
            self.assertEqual("scanned_pdf_no_text_layer", ctx.exception.reason_code)

    def test_process_file_surfaces_scanned_pdf_reason_code_in_safe_report(self) -> None:
        # Simulates the realistic C-5 scenario end-to-end at the process_file
        # level: OCR/markdown extraction (marker/paddle) succeeds and finds a
        # PII target, but the *original* PDF has no text layer at all, so the
        # native-redaction step (which re-opens the original PDF) fails as a
        # scanned document. redact_pdf_native's own scanned-PDF detection is
        # covered directly above; here we verify the reason_code this raises
        # actually threads through process_file's except-block into the safe
        # report and product_checks (E2-2's "품질 게이트/safe report에 사유
        # 코드가 실리게" requirement) without touching Rust/TS.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "scanned_doc.pdf"

            doc = fitz.open()
            page = doc.new_page(width=240, height=180)
            page.insert_text((32, 52), "phone 010-1111-2222")
            doc.save(source)
            doc.close()

            with mock.patch.object(
                masker,
                "redact_pdf_native",
                side_effect=ScannedPdfRedactionError(
                    "스캔 PDF는 텍스트 레이어가 없어 자동 마스킹을 적용할 수 없습니다 — 수동 마스킹 캔버스를 사용하세요."
                ),
            ):
                _extracted, _masked, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(root),
                    opts={
                        "profile": "legal",
                        "extract_engine": "pypdf",
                        "output_artifacts": "pdf+report",
                        "display_mode": "black",
                        "pdf_redaction": True,
                    },
                )

            self.assertEqual("failed", report["pdf_redaction"]["status"])
            self.assertEqual("scanned_pdf_no_text_layer", report["pdf_redaction"].get("reason_code"))
            self.assertEqual(
                "scanned_pdf_no_text_layer",
                report["product_checks"]["native_redaction_reason_code"],
            )
            self.assertFalse(report["product_checks"]["quality_gate_passed"])
            self.assertEqual(
                "scanned_pdf_no_text_layer",
                report["pdf_redaction"]["verification"]["reason_code"],
            )
            self.assertNotIn("reason", report["pdf_redaction"]["verification"])


if __name__ == "__main__":
    unittest.main()
