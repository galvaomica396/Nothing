from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import fitz

from document_masker_ocr_gui import (
    TrustedFinalizeManualResultError,
    TrustedFinalizeOccurrenceIntrinsicError,
    _trusted_occurrence_intrinsic_diagnostics,
    trusted_finalize_manifest,
)
from masking_redaction import _rect_text_hash, _review_evidence
def _threshold_artifact() -> dict[str, object]:
    auto_threshold = 0.85
    review_threshold = 0.5
    material = {
        "auto_threshold": auto_threshold,
        "policy_version": "masking-policy-v1",
        "review_threshold": review_threshold,
    }
    content_hash = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "version": "thresholds-v2",
        "content_hash": content_hash,
        "auto_mask_threshold": auto_threshold,
        "review_threshold": review_threshold,
    }




class TrustedFinalizeLifecycleContracts(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.original = self.root / "original.pdf"
        document = fitz.open()
        document.new_page().insert_text((40, 50), "original document")
        document.save(self.original)
        document.close()
        self.original_bytes = self.original.read_bytes()
        self.staging_root = self.root / "private-staging"
        self.staging_root.mkdir()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _manifest(self) -> dict[str, object]:
        return {
            "runId": "run-1",
            "originalDocumentHash": hashlib.sha256(self.original.read_bytes()).hexdigest(),
            "analysisRevision": 3,
            "profile": "internal_review",
            "policyVersion": "masking-policy-v1",
            "optionsHash": "b" * 64,
            "thresholdVersion": _threshold_artifact()["version"],
            "thresholdHash": _threshold_artifact()["content_hash"],
            "thresholdArtifact": {
                "version": _threshold_artifact()["version"],
                "contentHash": _threshold_artifact()["content_hash"],
                "autoMaskThreshold": _threshold_artifact()["auto_mask_threshold"],
                "reviewThreshold": _threshold_artifact()["review_threshold"],
            },
            "coordinateSpace": "pdf_points_top_left",
            "segments": [{"segmentId": "segment-1", "analysisRevision": 3, "pageStart": 0, "pageEnd": 0}],
            "regions": [{
                "regionId": "region-1", "segmentId": "segment-1", "analysisRevision": 3,
                "page": 0, "rects": [], "kind": "approval", "state": "unconfirmed",
            }],
            "occurrences": [],
            "manualActions": [],
            "reviewItems": [],
        }
    @staticmethod
    def _finalize_options() -> dict[str, object]:
        return {
            "run_id": "run-1",
            "analysis_revision": 3,
            "profile": "internal_review",
            "options_hash": "b" * 64,
            "threshold_version": _threshold_artifact()["version"],
            "threshold_hash": _threshold_artifact()["content_hash"],
            "threshold_artifact": _threshold_artifact(),
            "auto_mask_threshold": _threshold_artifact()["auto_mask_threshold"],
            "review_threshold": _threshold_artifact()["review_threshold"],
            "warnings_confirmed": False,
        }

    def _unverified_output(self, _original: str, staging: str, *_args, **_kwargs) -> dict[str, object]:
        Path(staging).write_bytes(b"unverified output")
        return {
            "status": "applied",
            "output_file": staging,
            "verification": {"verified": False},
        }
    def test_verified_nonempty_occurrence_and_manual_finalize_promotes_staging_without_mutating_source(self) -> None:
        manifest = self._manifest()
        manifest["occurrences"] = [{
            "occurrenceId": "occ_000000000000000000000001", "analysisRevision": 3, "page": 0,
            "rects": [{"x0": 40, "y0": 35, "x1": 140, "y1": 65}], "proposedAction": "mask",
            "state": "confirmed", "provenance": "trusted-analysis", "expectedTextHash": "a" * 64,
        }]
        manifest["manualActions"] = [{
            "actionId": "manual-1", "analysisRevision": 3, "page": 0,
            "rects": [{"x0": 40, "y0": 35, "x1": 140, "y1": 65}], "mode": "mask",
            "sourceKind": "manual", "expectedTextHash": "a" * 64,
            "protectedNeighborRefs": [{"x0": 150, "y0": 35, "x1": 165, "y1": 65}],
        }]
        staging = self.staging_root / "verified-nonempty.pdf"

        def produce(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"occurrence-masked")
            return {
                "status": "applied", "output_file": destination,
                "occurrences_applied": 1, "verification": {"verified": True},
            }

        def apply(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"manual-masked")
            return {
                "status": "applied", "output_file": destination,
                "actions_applied": 1, "verification": {"verified": True},
            }

        with (
            patch("document_masker_ocr_gui.redact_pdf_native", side_effect=produce) as producer,
            patch("document_masker_ocr_gui.apply_manual_actions_v1", side_effect=apply) as manual_producer,
        ):
            result = trusted_finalize_manifest(str(self.original), manifest, self._finalize_options(), str(staging))
        self.assertEqual("applied", result["status"])
        self.assertIs(result["verification"]["verified"], True)
        self.assertEqual(2, result["occurrence_count"])
        self.assertEqual(2, result["applied_mask_count"])
        self.assertEqual(b"manual-masked", staging.read_bytes())
        self.assertEqual(hashlib.sha256(staging.read_bytes()).hexdigest(), result["staging_hash"])
        self.assertEqual(str(staging.resolve()), manual_producer.call_args.args[0])
        self.assertEqual(
            str(Path(f"{staging}.manual.pdf").resolve()),
            manual_producer.call_args.args[1],
        )
        self.assertEqual(self.original_bytes, self.original.read_bytes())
        producer.assert_called_once()
        manual_producer.assert_called_once()

    def test_scan_manual_action_reaches_existing_raster_verified_finalize_path(self) -> None:
        document = fitz.open()
        page = document.new_page(width=200, height=200)
        page.draw_rect(page.rect, color=None, fill=(0.85, 0.85, 0.85))
        page.draw_circle((90, 90), 28, color=(0.2, 0.2, 0.2), fill=(0.4, 0.4, 0.4))
        document.save(self.original)
        document.close()
        self.original_bytes = self.original.read_bytes()
        manifest = self._manifest()
        manifest["segments"] = [{
            "analysisRevision": 3, "pageStart": 0, "pageEnd": 0,
            "kind": "unknown", "state": "review_required", "commonOnly": False,
            "source": "scanned_geometry_unavailable",
        }]
        manifest["manualActions"] = [
            {
                "actionId": "manual-scan-1", "analysisRevision": 3, "page": 0,
                "rects": [{"x0": 55, "y0": 55, "x1": 125, "y1": 125}], "mode": "mask",
                "sourceKind": "scan", "linkedOccurrenceId": None, "expectedTextHash": None,
                "protectedNeighborRefs": [],
            },
            {
                "actionId": "manual-scan-2", "analysisRevision": 3, "page": 0,
                "rects": [{"x0": 140, "y0": 140, "x1": 180, "y1": 180}], "mode": "mask",
                "sourceKind": "scan", "linkedOccurrenceId": None, "expectedTextHash": None,
                "protectedNeighborRefs": [],
            },
        ]
        staging = self.staging_root / "scan-manual.pdf"

        result = trusted_finalize_manifest(
            str(self.original), manifest, self._finalize_options(), str(staging)
        )

        self.assertEqual("applied", result["status"])
        self.assertEqual(2, result["occurrence_count"])
        self.assertEqual(2, result["applied_mask_count"])
        self.assertTrue(staging.exists())
        self.assertEqual(
            {
                "coverage_100": True,
                "protected_ratio_ok": True,
                "no_connected_diff": True,
                "no_residual": True,
            },
            result["verification"]["scan_manual"],
        )
        verified = fitz.open(staging)
        try:
            pixmap = verified[0].get_pixmap(clip=fitz.Rect(60, 60, 120, 120), alpha=False)
            self.assertTrue(all(value == 0 for value in pixmap.samples))
        finally:
            verified.close()

    def test_manual_link_and_protected_occurrence_are_excluded_before_overlap_validation(self) -> None:
        manifest = self._manifest()
        target_rect = {"x0": 40, "y0": 35, "x1": 140, "y1": 65}
        neighbor_rect = {"x0": 150, "y0": 35, "x1": 165, "y1": 65}
        target_id = "occ_000000000000000000000001"
        neighbor_id = "occ_000000000000000000000002"
        manifest["occurrences"] = [
            {
                "occurrenceId": target_id, "analysisRevision": 3, "page": 0,
                "rects": [target_rect], "proposedAction": "mask",
                "state": "confirmed", "provenance": "trusted-analysis", "expectedTextHash": "a" * 64,
            },
            {
                "occurrenceId": neighbor_id, "analysisRevision": 3, "page": 0,
                "rects": [neighbor_rect], "proposedAction": "mask",
                "state": "confirmed", "provenance": "trusted-analysis", "expectedTextHash": "b" * 64,
            },
        ]
        manifest["manualActions"] = [{
            "actionId": "manual-1", "analysisRevision": 3, "page": 0,
            "rects": [target_rect], "mode": "mask", "sourceKind": "text_pdf",
            "linkedOccurrenceId": target_id, "expectedTextHash": "a" * 64,
            "protectedNeighborRefs": [neighbor_rect],
        }]
        staging = self.staging_root / "manual-exclusions.pdf"

        def produce(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"occurrence-stage")
            return {
                "status": "applied", "occurrences_applied": 0,
                "verification": {"verified": True},
            }

        def apply(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"manual-stage")
            return {
                "status": "applied", "actions_applied": 1,
                "verification": {"verified": True},
            }

        with (
            patch("document_masker_ocr_gui.redact_pdf_native", side_effect=produce) as producer,
            patch("document_masker_ocr_gui.apply_manual_actions_v1", side_effect=apply),
        ):
            result = trusted_finalize_manifest(
                str(self.original), manifest, self._finalize_options(), str(staging)
            )

        self.assertEqual(1, result["occurrence_count"])
        self.assertEqual(1, result["applied_mask_count"])

        rendered_occurrences = producer.call_args.kwargs["occurrence_inputs"]
        self.assertEqual(
            [(target_id, "exclude"), (neighbor_id, "exclude")],
            [(item.occurrence_id, item.action) for item in rendered_occurrences],
        )

    def test_custom_keyword_at_protected_neighbor_remains_a_mask(self) -> None:
        manifest = self._manifest()
        target_rect = {"x0": 40, "y0": 35, "x1": 140, "y1": 65}
        neighbor_rect = {"x0": 150, "y0": 35, "x1": 165, "y1": 65}
        manifest["occurrences"] = [
            {
                "occurrenceId": "occ_aaaaaaaaaaaaaaaaaaaaaaaa",
                "analysisRevision": 3,
                "page": 0,
                "rects": [target_rect],
                "proposedAction": "mask",
                "state": "confirmed",
                "provenance": "trusted-analysis",
                "expectedTextHash": "a" * 64,
            },
            {
                "occurrenceId": "occ_bbbbbbbbbbbbbbbbbbbbbbbb",
                "analysisRevision": 3,
                "page": 0,
                "rects": [neighbor_rect],
                "proposedAction": "mask",
                "state": "confirmed",
                "provenance": "trusted-analysis",
                "expectedTextHash": "b" * 64,
            },
            {
                "occurrenceId": "occ_cccccccccccccccccccccccc",
                "analysisRevision": 3,
                "page": 0,
                "rects": [neighbor_rect],
                "proposedAction": "mask",
                "state": "confirmed",
                "category": "custom_keyword",
                "provenance": "custom_keyword",
                "expectedTextHash": "c" * 64,
            },
        ]
        manifest["manualActions"] = [{
            "actionId": "manual-1",
            "analysisRevision": 3,
            "page": 0,
            "rects": [target_rect],
            "mode": "mask",
            "sourceKind": "text_pdf",
            "linkedOccurrenceId": "occ_aaaaaaaaaaaaaaaaaaaaaaaa",
            "expectedTextHash": "a" * 64,
            "protectedNeighborRefs": [neighbor_rect],
        }]
        staging = self.staging_root / "keyword-protected-neighbor.pdf"

        def produce(_source: str, destination: str, *_args, **kwargs) -> dict[str, object]:
            actions = kwargs["occurrence_inputs"]
            Path(destination).write_bytes(b"occurrence-stage")
            return {
                "status": "applied",
                "occurrences_applied": sum(item.action == "mask" for item in actions),
                "verification": {"verified": True},
            }

        def apply(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"manual-stage")
            return {
                "status": "applied",
                "actions_applied": 1,
                "verification": {"verified": True},
            }

        with (
            patch("document_masker_ocr_gui.redact_pdf_native", side_effect=produce) as producer,
            patch("document_masker_ocr_gui.apply_manual_actions_v1", side_effect=apply),
        ):
            result = trusted_finalize_manifest(
                str(self.original), manifest, self._finalize_options(), str(staging)
            )

        self.assertEqual(2, result["occurrence_count"])
        rendered_occurrences = producer.call_args.kwargs["occurrence_inputs"]
        self.assertEqual(
            {
                "occ_aaaaaaaaaaaaaaaaaaaaaaaa": "exclude",
                "occ_bbbbbbbbbbbbbbbbbbbbbbbb": "exclude",
                "occ_cccccccccccccccccccccccc": "mask",
            },
            {item.occurrence_id: item.action for item in rendered_occurrences},
        )

    def test_run_and_options_authority_mismatches_block_before_producer_work(self) -> None:
        cases = (
            ("missing-manifest-run", lambda manifest, options: manifest.pop("runId"), "STALE_ANALYSIS"),
            ("non-string-manifest-run", lambda manifest, options: manifest.__setitem__("runId", 7), "STALE_ANALYSIS"),
            ("mismatched-run", lambda manifest, options: options.__setitem__("run_id", "other-run"), "STALE_ANALYSIS"),
            ("missing-manifest-options", lambda manifest, options: manifest.pop("optionsHash"), "STALE_ANALYSIS"),
            ("non-string-manifest-options", lambda manifest, options: manifest.__setitem__("optionsHash", 7), "STALE_ANALYSIS"),
            ("mismatched-options", lambda manifest, options: options.__setitem__("options_hash", "c" * 64), "STALE_ANALYSIS"),
            ("mismatched-profile", lambda manifest, options: options.__setitem__("profile", "mixed"), "STALE_ANALYSIS"),
            ("mismatched-policy", lambda manifest, options: manifest.__setitem__("policyVersion", "masking-policy-v2"), "STALE_ANALYSIS"),
            ("mismatched-threshold-version", lambda manifest, options: options.__setitem__("threshold_version", "thresholds-v3"), "STALE_ANALYSIS"),
            ("mismatched-threshold-hash", lambda manifest, options: options.__setitem__("threshold_hash", "c" * 64), "STALE_ANALYSIS"),
            ("mismatched-analysis-revision", lambda manifest, options: options.__setitem__("analysis_revision", 4), "STALE_ANALYSIS"),
            ("missing-caller-run", lambda manifest, options: options.pop("run_id"), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("empty-caller-run", lambda manifest, options: options.__setitem__("run_id", ""), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("non-string-caller-run", lambda manifest, options: options.__setitem__("run_id", 7), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("missing-caller-analysis-revision", lambda manifest, options: options.pop("analysis_revision"), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("invalid-caller-analysis-revision", lambda manifest, options: options.__setitem__("analysis_revision", 0), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("non-integer-caller-analysis-revision", lambda manifest, options: options.__setitem__("analysis_revision", "3"), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("missing-caller-options", lambda manifest, options: options.pop("options_hash"), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("empty-caller-options", lambda manifest, options: options.__setitem__("options_hash", ""), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
            ("non-string-caller-options", lambda manifest, options: options.__setitem__("options_hash", 7), "TRUSTED_FINALIZE_AUTHORITY_MISSING"),
        )
        for name, mutate, expected in cases:
            with self.subTest(case=name):
                manifest = self._manifest()
                options = self._finalize_options()
                mutate(manifest, options)
                staging = self.staging_root / f"{name}.pdf"
                with patch("document_masker_ocr_gui.redact_pdf_native") as producer:
                    with self.assertRaisesRegex(ValueError, f"^{expected}$"):
                        trusted_finalize_manifest(str(self.original), manifest, options, str(staging))
                producer.assert_not_called()
                self.assertEqual(self.original_bytes, self.original.read_bytes())
                self.assertFalse(staging.exists())


    def _assert_blocked_before_producer_work(
        self,
        manifest: dict[str, object],
        *,
        expected: str,
        warnings_confirmed: bool = False,
    ) -> None:
        staging = self.staging_root / f"{expected.lower()}.pdf"
        forbidden_fragments = (str(self.original), self.original.name, str(staging), staging.name)
        options = {**self._finalize_options(), "warnings_confirmed": warnings_confirmed}
        with patch("document_masker_ocr_gui.redact_pdf_native") as producer:
            with self.assertRaises(ValueError) as raised:
                trusted_finalize_manifest(str(self.original), manifest, options, str(staging))
        self.assertEqual(expected, str(raised.exception))
        for fragment in forbidden_fragments:
            self.assertNotIn(fragment, str(raised.exception))
        producer.assert_not_called()
        self.assertEqual(self.original_bytes, self.original.read_bytes())
        self.assertFalse(staging.exists())

    def test_original_drift_cannot_mutate_source_or_leave_owned_staging(self) -> None:
        manifest = self._manifest()
        manifest["originalDocumentHash"] = "0" * 64
        self._assert_blocked_before_producer_work(manifest, expected="ORIGINAL_CHANGED")

    def test_warning_confirmation_cannot_bypass_integrity_failures(self) -> None:
        original_changed = self._manifest()
        original_changed["originalDocumentHash"] = "0" * 64
        self._assert_blocked_before_producer_work(
            original_changed,
            expected="ORIGINAL_CHANGED",
            warnings_confirmed=True,
        )
        stale_analysis = self._manifest()
        stale_analysis["analysisRevision"] = 2
        self._assert_blocked_before_producer_work(
            stale_analysis,
            expected="STALE_ANALYSIS",
            warnings_confirmed=True,
        )

    def test_stale_revision_and_unconfirmed_review_cannot_mutate_source_or_leave_owned_staging(self) -> None:
        stale = self._manifest()
        stale["occurrences"] = [{
            "occurrenceId": "occ_000000000000000000000001", "analysisRevision": 2, "page": 0,
            "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}], "proposedAction": "mask",
            "provenance": "test", "expectedTextHash": "a" * 64,
        }]
        self._assert_blocked_before_producer_work(stale, expected="STALE_ANALYSIS")
        noncanonical = self._manifest()
        noncanonical["occurrences"] = [{
            "occurrenceId": "occ_ABCDEFABCDEFABCDEFABCDEF", "analysisRevision": 3, "page": 0,
            "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}], "proposedAction": "mask",
            "provenance": "test", "expectedTextHash": "a" * 64,
        }]
        self._assert_blocked_before_producer_work(noncanonical, expected="STALE_ANALYSIS")

        unresolved = self._manifest()
        unresolved["reviewItems"] = [{
            "reviewId": "review-1", "analysisRevision": 3, "status": "pending",
            "kind": "acknowledge", "targetId": "segment-1", "pageStart": 0, "pageEnd": 0,
            "reasonCodes": ["requires_review"], "requiresAcknowledgment": True,
            "commonOnly": False, "provenance": "test",
        }]
        self._assert_blocked_before_producer_work(unresolved, expected="UNRESOLVED_REVIEW")

    def test_confirmed_review_finalization_records_confirmation_summary(self) -> None:
        manifest = self._manifest()
        manifest["reviewItems"] = [{
            "reviewId": "review-1", "analysisRevision": 3, "status": "pending",
            "kind": "region_geometry", "targetId": "region-1", "pageStart": 0, "pageEnd": 0,
            "reasonCodes": ["geometry_review"], "requiresAcknowledgment": True,
            "commonOnly": False, "provenance": "test",
        }]
        options = {**self._finalize_options(), "warnings_confirmed": True}
        staging = self.staging_root / "confirmed-review.pdf"

        def produce(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"confirmed-review")
            return {"status": "applied", "occurrences_applied": 0, "verification": {"verified": True}}

        with patch("document_masker_ocr_gui.redact_pdf_native", side_effect=produce):
            result = trusted_finalize_manifest(str(self.original), manifest, options, str(staging))
        self.assertEqual("user_confirmed", result["save_confirmation"]["status"])
        self.assertEqual(
            {
                "kind": "region_geometry",
                "target_id": "region-1",
                "category": "approval",
                "page_start": 0,
                "page_end": 0,
                "reason_codes": ["geometry_review"],
            },
            result["save_confirmation"]["unresolved_reviews"][0],
        )

    def test_confirmed_occurrence_review_is_saved_without_masking_the_review_target(self) -> None:
        document = fitz.open(self.original)
        try:
            rect = document[0].search_for("original document")[0]
            expected_text_hash = _rect_text_hash(document[0], [rect])
        finally:
            document.close()
        self.assertIsNotNone(expected_text_hash)
        manifest = self._manifest()
        manifest["occurrences"] = [{
            "occurrenceId": "occ_000000000000000000000001",
            "analysisRevision": 3,
            "page": 0,
            "rects": [{"x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1}],
            "tag": "NAME",
            "category": "name",
            "valueHash": "a" * 64,
            "expectedTextHash": expected_text_hash,
            "source": "text_pdf",
            "policy": "masking-policy-v1",
            "proposedAction": "review",
            "state": "review_required",
            "provenance": "trusted_analysis",
        }]
        manifest["reviewItems"] = [{
            "reviewId": "review-1",
            "analysisRevision": 3,
            "status": "pending",
            "kind": "name",
            "targetId": "occ_000000000000000000000001",
            "pageStart": 0,
            "pageEnd": 0,
            "reasonCodes": ["requires_review"],
            "requiresAcknowledgment": False,
            "commonOnly": False,
            "provenance": "trusted_analysis",
        }]
        options = {**self._finalize_options(), "warnings_confirmed": True}
        staging = self.staging_root / "confirmed-occurrence-review.pdf"

        result = trusted_finalize_manifest(
            str(self.original), manifest, options, str(staging)
        )

        self.assertEqual("user_confirmed", result["save_confirmation"]["status"])
        self.assertEqual(
            [{
                "kind": "name",
                "target_id": "occ_000000000000000000000001",
                "category": "name",
                "page_start": 0,
                "page_end": 0,
                "reason_codes": ["requires_review"],
            }],
            result["save_confirmation"]["unresolved_reviews"],
        )
        self.assertTrue(result["verification"]["verified"])
        output_document = fitz.open(staging)
        try:
            self.assertIn("original document", output_document[0].get_text())
        finally:
            output_document.close()

    def test_authorized_text_restore_keeps_target_visible_and_excludes_only_that_mask(self) -> None:
        document = fitz.open(self.original)
        try:
            target = document[0].search_for("original document")[0]
            expected_text_hash = _rect_text_hash(document[0], [target])
        finally:
            document.close()
        self.assertIsNotNone(expected_text_hash)
        manifest = self._manifest()
        target_id = "occ_000000000000000000000001"
        target_rect = {
            "x0": target.x0,
            "y0": target.y0,
            "x1": target.x1,
            "y1": target.y1,
        }
        manifest["occurrences"] = [{
            "occurrenceId": target_id,
            "analysisRevision": 3,
            "page": 0,
            "rects": [target_rect],
            "proposedAction": "mask",
            "state": "confirmed",
            "provenance": "trusted_analysis",
            "expectedTextHash": expected_text_hash,
        }]
        manifest["manualActions"] = [{
            "actionId": "restore-1",
            "analysisRevision": 3,
            "page": 0,
            "rects": [target_rect],
            "mode": "restore",
            "sourceKind": "text_pdf",
            "linkedOccurrenceId": target_id,
            "expectedTextHash": expected_text_hash,
            "protectedNeighborRefs": [],
            "restoreAuthorizationHash": "a" * 64,
        }]
        staging = self.staging_root / "authorized-restore.pdf"

        result = trusted_finalize_manifest(
            str(self.original), manifest, self._finalize_options(), str(staging)
        )

        self.assertEqual("applied", result["status"])
        self.assertEqual(0, result["occurrence_count"])
        self.assertEqual(0, result["applied_mask_count"])
        self.assertEqual(1, result["restore_count"])
        output_document = fitz.open(staging)
        try:
            self.assertEqual("original document", output_document[0].get_textbox(target).strip())
            pixmap = output_document[0].get_pixmap(clip=target, alpha=False)
            self.assertTrue(any(value > 8 for value in pixmap.samples))
        finally:
            output_document.close()

    def test_clean_finalization_does_not_claim_warning_confirmation(self) -> None:
        manifest = self._manifest()
        options = {**self._finalize_options(), "warnings_confirmed": True}
        staging = self.staging_root / "clean-review.pdf"

        def produce(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"clean-review")
            return {"status": "applied", "occurrences_applied": 0, "verification": {"verified": True}}

        with patch("document_masker_ocr_gui.redact_pdf_native", side_effect=produce):
            result = trusted_finalize_manifest(str(self.original), manifest, options, str(staging))
        self.assertEqual("not_required", result["save_confirmation"]["status"])
        self.assertEqual([], result["save_confirmation"]["unresolved_reviews"])

    def test_confirmed_indeterminate_coverage_is_recorded_without_review_cards(self) -> None:
        manifest = self._manifest()
        manifest["approvalCoverage"] = {
            "schemaVersion": 1,
            "state": "indeterminate",
            "signerCount": 0,
            "protectedNeighborCount": 0,
        }
        options = {**self._finalize_options(), "warnings_confirmed": True}
        staging = self.staging_root / "coverage-review.pdf"

        def produce(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"coverage-review")
            return {"status": "applied", "occurrences_applied": 0, "verification": {"verified": True}}

        with patch("document_masker_ocr_gui.redact_pdf_native", side_effect=produce):
            result = trusted_finalize_manifest(str(self.original), manifest, options, str(staging))
        self.assertEqual("user_confirmed", result["save_confirmation"]["status"])
        self.assertEqual(
            [{
                "kind": "coverage",
                "target_id": "approval",
                "category": "approval",
                "page_start": 0,
                "page_end": 0,
                "reason_codes": ["indeterminate_coverage"],
            }],
            result["save_confirmation"]["unresolved_reviews"],
        )

    def test_unverified_redaction_output_cannot_mutate_source_or_leave_owned_staging(self) -> None:
        manifest = self._manifest()
        manifest["occurrences"] = [{
            "occurrenceId": "occ_000000000000000000000001", "analysisRevision": 3, "page": 0,
            "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}], "proposedAction": "mask",
            "provenance": "test", "expectedTextHash": "a" * 64,
        }]
        staging = self.staging_root / "unverified.pdf"
        with (
            patch("document_masker_ocr_gui.redact_pdf_native", side_effect=self._unverified_output) as producer,
            self.assertRaisesRegex(ValueError, "^TRUSTED_FINALIZE_BLOCKED$"),
        ):
            trusted_finalize_manifest(str(self.original), manifest, self._finalize_options(), str(staging))
        producer.assert_called_once()
        self.assertFalse(staging.exists())
        self.assertEqual(self.original_bytes, self.original.read_bytes())

    def test_occurrence_intrinsic_failure_keeps_hard_block_and_records_safe_diagnostics(self) -> None:
        manifest = self._manifest()
        manifest["occurrences"] = [{
            "occurrenceId": "occ_000000000000000000000001", "analysisRevision": 3, "page": 0,
            "rects": [{"x0": 40, "y0": 35, "x1": 140, "y1": 65}], "proposedAction": "mask",
            "state": "confirmed", "provenance": "trusted_analysis", "expectedTextHash": "0" * 64,
        }]
        staging = self.staging_root / "intrinsic-failure.pdf"

        with self.assertRaises(TrustedFinalizeOccurrenceIntrinsicError) as raised:
            trusted_finalize_manifest(
                str(self.original),
                manifest,
                {**self._finalize_options(), "warnings_confirmed": True},
                str(staging),
            )

        self.assertEqual("TRUSTED_FINALIZE_OCCURRENCE_INTRINSIC_FAILED", str(raised.exception))
        self.assertEqual(
            [
                {"kind": "occurrence_failure", "reason_code": "expected_text_hash_mismatch", "count": 1},
                {"kind": "pii_non_exposure", "reason_code": "final_output_not_published", "count": 1},
            ],
            raised.exception.diagnostics,
        )
        self.assertFalse(staging.exists())

    def test_occurrence_intrinsic_diagnostics_keep_hash_only_context(self) -> None:
        item = _review_evidence(
            "occ_" + "a" * 24,
            "expected_text_hash_mismatch",
            page_index=1,
            category="dispatch_metadata",
            rects=((10.0, 20.0, 30.0, 40.0),),
            expected_text_hash="b" * 64,
            observed_text_hash="c" * 64,
        )

        diagnostics = _trusted_occurrence_intrinsic_diagnostics({"review_items": [item]})

        self.assertEqual(
            {
                "kind": "occurrence_failure",
                "reason_code": "expected_text_hash_mismatch",
                "count": 1,
                "occurrence_id": "occ_" + "a" * 24,
                "category": "dispatch_metadata",
                "page": 1,
                "rect_fingerprint": item["rect_fingerprint"],
                "expected_text_hash": "b" * 64,
                "observed_text_hash": "c" * 64,
            },
            diagnostics[0],
        )
        self.assertNotIn("raw_value_saved", diagnostics[0])
        self.assertEqual("pii_non_exposure", diagnostics[-1]["kind"])

    def test_manual_result_failure_keeps_hard_block_and_records_safe_diagnostics(self) -> None:
        manifest = self._manifest()
        manifest["manualActions"] = [{
            "actionId": "manual-failure",
            "analysisRevision": 3,
            "page": 0,
            "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}],
            "mode": "mask",
            "sourceKind": "manual",
            "expectedTextHash": "a" * 64,
            "protectedNeighborRefs": [{"x0": 3, "y0": 1, "x1": 4, "y1": 2}],
        }]
        staging = self.staging_root / "manual-failure.pdf"
        failed_result = {
            "status": "failed",
            "verification": {
                "verified": False,
                "reason_code": "manual_residual_verification_failed",
            },
            "review_items": [{
                "manual_action_id": "manual-failure",
                "page": 0,
                "status": "review_required",
                "reason_code": "scan_connected_diff_into_protected_glyph",
                "count": 2,
                "raw_value_saved": False,
            }],
        }

        with (
            patch("document_masker_ocr_gui.apply_manual_actions_v1", return_value=failed_result),
            self.assertRaises(TrustedFinalizeManualResultError) as raised,
        ):
            trusted_finalize_manifest(
                str(self.original),
                manifest,
                self._finalize_options(),
                str(staging),
            )

        self.assertEqual("TRUSTED_FINALIZE_MANUAL_RESULT_FAILED", str(raised.exception))
        self.assertEqual(
            [
                {
                    "kind": "manual_failure",
                    "reason_code": "scan_connected_diff_into_protected_glyph",
                    "count": 2,
                },
                {
                    "kind": "pii_non_exposure",
                    "reason_code": "final_output_not_published",
                    "count": 1,
                },
            ],
            raised.exception.diagnostics,
        )
        self.assertFalse(staging.exists())

    def test_manual_promotion_failure_cannot_mutate_source_or_leave_owned_staging(self) -> None:
        manifest = self._manifest()
        manifest["manualActions"] = [{
            "actionId": "action-1", "analysisRevision": 3, "page": 0,
            "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}], "mode": "mask",
            "sourceKind": "manual", "expectedTextHash": "a" * 64,
            "protectedNeighborRefs": [{"x0": 3, "y0": 1, "x1": 4, "y1": 2}],
        }]
        staging = self.staging_root / "promotion-failure.pdf"
        manual_staging = Path(f"{staging}.manual.pdf")
        hostile_error = "promotion failed: Kim /private/patient.pdf"
        def produce_manual_output(_source: str, destination: str, *_args, **_kwargs) -> dict[str, object]:
            Path(destination).write_bytes(b"owned manual staging")
            return {"status": "applied", "verification": {"verified": True}}

        with (
            patch("document_masker_ocr_gui.apply_manual_actions_v1", side_effect=produce_manual_output),
            patch("document_masker_ocr_gui.os.replace", side_effect=OSError(hostile_error)),
            self.assertRaises(ValueError) as raised,
        ):
            trusted_finalize_manifest(str(self.original), manifest, self._finalize_options(), str(staging))

        self.assertEqual("TRUSTED_FINALIZE_PROMOTION_FAILED", str(raised.exception))
        for fragment in (
            hostile_error, "promotion failed", "Kim", "/private/patient.pdf", "/private", "patient.pdf",
            str(self.original), self.original.name, str(staging), staging.name, str(manual_staging), manual_staging.name,
        ):
            self.assertNotIn(fragment, str(raised.exception))
        self.assertEqual(self.original_bytes, self.original.read_bytes())
        self.assertFalse(staging.exists())
        self.assertFalse(manual_staging.exists())


if __name__ == "__main__":
    unittest.main()
