import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from masking_evaluation import (
    ProtocolValidationError, _fsync_directory, _public_gold_sources, _public_oof_report_projection, canonical_json_sha256,
    create_split_lock, create_synthetic_split_lock, lock_final_threshold, lock_manifest, make_protocol_receipt,
    public_oof_once, report_from_oof, synthetic_holdout_once, verify_oof, verify_pilot_report,
    verify_protocol_receipt, write_core_gate_receipt, write_threshold_e2e_receipt,
)


class EvaluationProtocolTests(unittest.TestCase):
    run_id = "evaluation-run-1"

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.hashes = [f"{index:064x}" for index in range(25)]
        self.gold = self.root / "public-gold.json"
        documents = []
        for index, value in enumerate(self.hashes):
            source_class = "issued" if index < 15 else "review"
            documents.append(lock_manifest({
                "schema_version": "IndependentGoldManifestV1", "geometry_policy_version": "GeometryPolicyV1",
                "coordinate_space": "pdf_points_top_left",
                "profile": "official_dispatch" if source_class == "issued" else "internal_review",
                "policy_version": "test-policy", "source_class": source_class, "form": source_class,
                "document": {"document_id": value, "input_sha256": value},
                "provenance": {"author": {"id": "author"}, "reviewer": {
                    "id": "reviewer", "decision": "approved", "adjudication": "independent_review",
                }, "detector_output_imported": False},
                "pages": [{"page_index": 0, "width": 1, "height": 1}],
                "segments": [{"id": "s", "page_index": 0, "type": "body", "offsets": {"start": 0, "end": 1}}],
                "regions": [{"id": "r", "page_index": 0, "type": "body", "rects": [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]}],
                "occurrences": [{"id": "o", "segment_id": "s", "region_id": "r", "page_index": 0,
                    "category": "name", "offsets": {"start": 0, "end": 1},
                    "ocr_confidence": None,
                    "rects": [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}]}],
                "negatives": [], "protected_neighbors": [], "annotation_status": "reviewed_approved",
                "annotation_completion": {
                    "pages": "completed", "segments": "completed", "regions": "completed",
                    "occurrences": "completed", "negatives": "none_confirmed",
                    "protected_neighbors": "none_confirmed",
                },
            }))
        gold = {"schema_version": "LockedPublicGoldManifestV2", "documents": documents, "status": "locked", "immutable": True}
        gold["manifest_sha256"] = canonical_json_sha256(gold)
        self.gold.write_text(json.dumps(gold), encoding="utf-8")
        self.split = self.root / "folds.json"
        self.split.write_text(json.dumps(create_split_lock(self.hashes)), encoding="utf-8")
        self.gate_payload = self.root / "core-evidence.json"
        self.gate_payload.write_text(json.dumps({"schema": "CoreGateOutputV1", "verified": True}), encoding="utf-8")
        self.gate = self.root / "core.json"
        write_core_gate_receipt(
            eval_root=self.root, output_path=self.gate, producer_role="core", inputs=(),
            public_content_read_count=0, output_payload=self.gate_payload, protocol_run_id=self.run_id,
        )
        self.calibration_dir = self.root / "calibration"
        self.calibration_dir.mkdir()


    def tearDown(self):
        self.tempdir.cleanup()

    def _predictions(self):
        assignments = json.loads(self.split.read_text()) ["assignments"]
        return {
            fold: [{
                "document_sha256": item["document_sha256"],
                "profile": "mixed",
                "status": "evaluated",
                "counts": self._oof_counts(),
            } for item in assignments if item["fold"] == fold]
            for fold in range(5)
        }

    @staticmethod
    def _oof_counts(**overrides):
        counts = {
            "detection_tp": 1, "detection_fp": 0, "detection_fn": 0,
            "automatic_tp": 1, "automatic_fp": 0, "automatic_fn": 0,
            "automatic_name_tp": 1, "automatic_name_fp": 0, "automatic_name_fn": 0,
            "protected_neighbor_overlap_count": 0, "blocked_document_count": 0,
            "body_occurrence_tp": 1, "body_occurrence_fp": 0, "body_occurrence_fn": 0,
            "region_tp": 0, "region_fp": 0, "region_fn": 0,
            "fixed_region_omission_count": 0, "unscoped_fixed_region_omission_count": 0,
        }
        counts.update(overrides)
        return counts

    @staticmethod
    def _candidate(**overrides):
        candidate = {
            "auto_threshold": .6, "review_threshold": .6,
            "detected_count": 100, "target_count": 100,
            "automatic_name_fp_count": 1, "automatic_name_count": 100,
        }
        candidate.update(overrides)
        return candidate
    def _calibration(self, candidates):
        payload = {
            "schema_version": "ImmutableThresholdCalibrationV2",
            "protocol_version": "IndependentEvaluationProtocolV1",
            "producer_role": "calibration",
            "immutable": True,
            "threshold_candidates": candidates,
        }
        payload["calibration_sha256"] = canonical_json_sha256(payload)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        path = self.calibration_dir / f"{hashlib.sha256(encoded.encode()).hexdigest()}.json"
        if not path.exists():
            path.write_text(encoded, encoding="utf-8")
        return path
    def _fold_threshold_evidence(self, candidates, split_path):
        assignments = json.loads(Path(split_path).read_text(encoding="utf-8"))["assignments"]
        return {
            fold: {
                "training_document_sha256s": sorted(
                    item["document_sha256"]
                    for item in assignments
                    if item["fold"] != fold
                ),
                "threshold_candidates": [dict(item) for item in candidates],
            }
            for fold in range(5)
        }
    def _holdout_payload(self, name, document_sha256s):
        path = self.root / f"{name}-holdout-payload.json"
        path.write_text(json.dumps({
            "schema_version": "ImmutableUntouchedHoldoutV1",
            "immutable": True,
            "document_sha256s": document_sha256s,
        }), encoding="utf-8")
        return path

    def _consumption_markers(self):
        return sorted(self.root.glob(".synthetic-holdout-*.consumed.json"))
    def _locked_holdout_inputs(self, name):
        protocol_run_id = f"{self.run_id}-{name}"
        gate_payload = self.root / f"{name}-core-evidence.json"
        gate_payload.write_text(
            json.dumps({"schema": "CoreGateOutputV1", "verified": True}),
            encoding="utf-8",
        )
        gate = self.root / f"{name}-core.json"
        write_core_gate_receipt(
            eval_root=self.root,
            output_path=gate,
            producer_role="core",
            inputs=(),
            public_content_read_count=0,
            output_payload=gate_payload,
            protocol_run_id=protocol_run_id,
        )
        oof = self.root / name / "oof.json"
        self._oof(oof, run_id=protocol_run_id, gate=gate)
        synthetic = self.root / name / "synthetic.json"
        create_synthetic_split_lock(
            eval_root=self.root, output_path=synthetic, calibration_sha256s=[f"{len(name):064x}"],
            holdout_sha256s=["b" * 64], producer_role="owner",
        )
        threshold = self.root / name / "threshold.json"
        lock_final_threshold(
            eval_root=self.root, output_path=threshold, oof_path=oof, synthetic_lock_path=synthetic,
            producer_role="owner", candidates=[self._candidate()],
        )
        return oof, synthetic, threshold
    @staticmethod
    def _input_snapshot(*paths):
        return {str(path): hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths}

    @staticmethod
    def _canonical_sha256(value):
        return hashlib.sha256(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def test_fsync_directory_is_noop_on_windows(self):
        with mock.patch("masking_evaluation.os.name", "nt"), mock.patch(
            "masking_evaluation.os.open", side_effect=PermissionError(13, "directory open denied")
        ) as open_mock:
            _fsync_directory(self.root)
        open_mock.assert_not_called()




    def _oof(
        self, output, *, predictions=None, candidates=None, calibration_candidates=None, run_id=None, gate=None,
        evaluator=None, split=None, fold_threshold_evidence=None,
    ):
        candidates = [self._candidate()] if candidates is None else candidates
        calibration_candidates = candidates if calibration_candidates is None else calibration_candidates
        active_split = self.split if split is None else split
        return public_oof_once(
            eval_root=self.root, output_path=output, split_lock_path=active_split,
            public_gold_path=self.gold,
            core_gate_receipt=self.gate if gate is None else gate, producer_role="runner",
            predictions_by_fold=self._predictions() if predictions is None else predictions,
            evaluator=evaluator, synthetic_calibration_path=self._calibration(calibration_candidates),
            threshold_candidates=candidates,
            fold_threshold_evidence=(
                self._fold_threshold_evidence(calibration_candidates, active_split)
                if fold_threshold_evidence is None
                else fold_threshold_evidence
            ),
            protocol_run_id=self.run_id if run_id is None else run_id,
        )
    def _assert_pre_consumption_oof_cleanup(self, output):
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".lease.json").exists())
        self.assertFalse(output.with_suffix(".failure.json").exists())
        self.assertFalse(any(self.root.glob(".public-oof-*.lease.json")))
        self.assertFalse(any(self.root.glob("fold-*/threshold.json")))
        self.assertFalse(any(self.root.glob("fold-*/predictions.jsonl")))
        self.assertFalse(any(self.root.glob("*.tmp")))

    def test_oof_requires_complete_exact_held_out_coverage_and_one_shot_identity(self):
        output = self.root / "oof.json"
        self._oof(output)
        verified = verify_oof(self.root, output)
        self.assertEqual(25, verified["document_count"])
        self.assertEqual(self.run_id, verified["protocol_run_id"])
        self.assertEqual({item["document_sha256"] for item in json.loads(self.split.read_text())["assignments"]}, {
            value for fold in verified["folds"] for value in fold["held_out_document_sha256s"]
        })
        original_digest = hashlib.sha256(output.read_bytes()).hexdigest()
        evaluator_calls = []
        with self.assertRaisesRegex(ProtocolValidationError, "OOF input tuple has already been leased"):
            self._oof(self.root / "alternate-oof.json", evaluator=lambda **_: evaluator_calls.append("called"))
        self.assertEqual([], evaluator_calls)
        self.assertFalse((self.root / "alternate-oof.json").exists())
        self.assertEqual(original_digest, hashlib.sha256(output.read_bytes()).hexdigest())
    def test_oof_evaluator_receives_only_fold_boundary_metadata_and_cannot_reuse_held_out_actor(self):
        observed = []

        def evaluator(**kwargs):
            observed.append(copy.deepcopy(kwargs))
            self.assertEqual(
                {
                    "fold", "held_out_document_sha256s", "training_document_sha256s", "thresholds",
                    "actor", "capabilities",
                },
                set(kwargs),
            )
            self.assertNotEqual(kwargs["actor"], "runner")
            self.assertEqual({"evaluate_held_out_fold"}, set(kwargs["capabilities"]))
            self.assertEqual(set(), set(kwargs["held_out_document_sha256s"]) & set(kwargs["training_document_sha256s"]))
            self.assertEqual({"auto_threshold": .6, "review_threshold": .6}, kwargs["thresholds"])
            return [
                {
                    "document_sha256": document,
                    "profile": "mixed",
                    "status": "evaluated",
                    "counts": self._oof_counts(),
                }
                for document in kwargs["held_out_document_sha256s"]
            ]

        output = self.root / "boundary-oof.json"
        self._oof(output, evaluator=evaluator)
        verified = verify_oof(self.root, output)
        self.assertEqual(5, len(observed))
        self.assertEqual(
            {item["document_sha256"] for item in json.loads(self.split.read_text())["assignments"]},
            {document for call in observed for document in call["held_out_document_sha256s"]},
        )
        for fold in verified["folds"]:
            threshold = json.loads((self.root / fold["threshold"]["path"]).read_text())
            inputs = threshold["selection_inputs"]
            self.assertEqual(fold["training_document_sha256s"], inputs["public_training_document_sha256s"])
            self.assertEqual(fold["held_out_document_sha256s"], inputs["held_out_document_sha256s"])
            self.assertFalse(set(inputs["public_training_document_sha256s"]) & set(inputs["held_out_document_sha256s"]))
            self.assertEqual(self._canonical_sha256({
                key: value for key, value in inputs.items() if key != "input_sha256"
            }), inputs["input_sha256"])
    def test_fold_thresholds_use_only_non_held_out_public_training_evidence(self):
        candidates = [
            self._candidate(auto_threshold=.8, review_threshold=.8),
            self._candidate(
                auto_threshold=.6,
                review_threshold=.6,
                detected_count=98,
                automatic_name_fp_count=0,
            ),
        ]
        fold_evidence = self._fold_threshold_evidence(candidates, self.split)
        fold_evidence[0]["threshold_candidates"] = [
            self._candidate(
                auto_threshold=.8,
                review_threshold=.8,
                detected_count=0,
                automatic_name_fp_count=100,
            ),
            self._candidate(
                auto_threshold=.6,
                review_threshold=.6,
                detected_count=100,
                automatic_name_fp_count=0,
            ),
        ]
        output = self.root / "fold-specific-threshold-oof.json"
        self._oof(
            output,
            candidates=candidates,
            fold_threshold_evidence=fold_evidence,
        )
        verified = verify_oof(self.root, output)
        selected = {}
        for fold in verified["folds"]:
            threshold_path = self.root / fold["threshold"]["path"]
            threshold = json.loads(threshold_path.read_text(encoding="utf-8"))
            selected[fold["fold"]] = (
                threshold["auto_threshold"],
                threshold["review_threshold"],
            )
            self.assertEqual(
                fold["training_document_sha256s"],
                threshold["public_training_evidence"]["training_document_sha256s"],
            )
            self.assertFalse(
                set(fold["held_out_document_sha256s"])
                & set(threshold["public_training_evidence"]["training_document_sha256s"])
            )
        self.assertEqual((.6, .6), selected[0])
        self.assertEqual({(.8, .8)}, {selected[fold] for fold in range(1, 5)})

        first_threshold = self.root / verified["folds"][0]["threshold"]["path"]
        tampered = json.loads(first_threshold.read_text(encoding="utf-8"))
        tampered["public_training_evidence"]["evidence_sha256"] = "0" * 64
        first_threshold.chmod(0o600)
        first_threshold.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(ProtocolValidationError):
            verify_oof(self.root, output)

    def test_fold_threshold_evidence_allows_no_automatic_names_when_public_training_failed_closed(self):
        candidates = [self._candidate()]
        fold_evidence = self._fold_threshold_evidence(candidates, self.split)
        for evidence in fold_evidence.values():
            evidence["threshold_candidates"] = [
                self._candidate(
                    automatic_name_count=0,
                    automatic_name_fp_count=0,
                    detected_count=0,
                )
            ]

        output = self.root / "failed-closed-training-oof.json"
        self._oof(
            output,
            candidates=candidates,
            fold_threshold_evidence=fold_evidence,
        )

        verified = verify_oof(self.root, output)
        for fold in verified["folds"]:
            threshold = json.loads(
                (self.root / fold["threshold"]["path"]).read_text(encoding="utf-8")
            )
            public_candidate = threshold["public_training_evidence"]["threshold_candidates"][0]
            self.assertEqual(0, public_candidate["automatic_name_count"])
            self.assertEqual(0, public_candidate["automatic_name_fp_count"])


    def test_oof_rejects_each_isolated_coverage_defect_before_writing_fold_artifacts(self):
        baseline = self._predictions()
        expected = json.loads(self.split.read_text())["assignments"]
        cases = {}
        missing = {fold: list(items) for fold, items in baseline.items()}
        missing[0].pop()
        cases["missing"] = missing
        extra = {fold: list(items) for fold, items in baseline.items()}
        extra[0].append({"document_sha256": "f" * 64})
        cases["extra"] = extra
        duplicate = {fold: list(items) for fold, items in baseline.items()}
        duplicate[1].append(dict(duplicate[0][0]))
        cases["duplicate"] = duplicate
        wrong_fold = {fold: list(items) for fold, items in baseline.items()}
        moved = next(item for item in expected if item["fold"] == 0)
        wrong_fold[1].append({"document_sha256": moved["document_sha256"]})
        wrong_fold[0] = [item for item in wrong_fold[0] if item["document_sha256"] != moved["document_sha256"]]
        cases["wrong-fold"] = wrong_fold
        for name, predictions in cases.items():
            with self.subTest(defect=name):
                output = self.root / f"{name}.json"
                with self.assertRaisesRegex(ProtocolValidationError, "OOF_PREDICTION_REJECTED"):
                    self._oof(output, predictions=predictions, evaluator=None)
                self._assert_pre_consumption_oof_cleanup(output)

    def test_oof_rejects_unassigned_cross_run_and_public_reading_core_receipts(self):
        unassigned = self.root / "unassigned-core.json"
        receipt = json.loads(self.gate.read_text())
        receipt["protocolRunId"] = "unassigned"
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        unassigned.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "OOF_CORE_GATE_BINDING_INVALID"):
            self._oof(self.root / "unassigned-oof.json", gate=unassigned)
        cross_run = self.root / "cross-run-core.json"
        receipt["protocolRunId"] = "different-run"
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        cross_run.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "OOF_CORE_GATE_BINDING_INVALID"):
            self._oof(self.root / "cross-run-oof.json", gate=cross_run)
        public_read = self.root / "public-read-core.json"
        receipt["protocolRunId"] = self.run_id
        receipt["counts"] = {"public_content_read_count": 1}
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        public_read.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "public_content_read_count=0"):
            self._oof(self.root / "public-read-oof.json", gate=public_read)
        swapped_role = self.root / "swapped-role-core.json"
        receipt["counts"] = {"public_content_read_count": 0}
        receipt["outputs"][0]["role"] = "input"
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        swapped_role.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "receipt artifact metadata"):
            self._oof(self.root / "swapped-role-oof.json", gate=swapped_role)
        empty_kind = self.root / "empty-kind-core.json"
        receipt["outputs"][0]["role"] = "output"
        receipt["outputs"][0]["kind"] = ""
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        empty_kind.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "artifact metadata"):
            self._oof(self.root / "empty-kind-oof.json", gate=empty_kind)
    def test_oof_rejects_invalid_count_bound_candidate_evidence(self):
        fields = ("detected_count", "target_count", "automatic_name_fp_count", "automatic_name_count")
        invalid_values = (
            ("negative", -1),
            ("fractional", 1.5),
            ("boolean", True),
            ("wrong-type", "1"),
        )
        for field in fields:
            for name, invalid in invalid_values:
                with self.subTest(field=field, defect=name):
                    output = self.root / f"{field}-{name}.json"
                    calls = []
                    with self.assertRaisesRegex(ProtocolValidationError, "THRESHOLD_CANDIDATE_REJECTED"):
                        self._oof(
                            output, candidates=[self._candidate(**{field: invalid})],
                            evaluator=lambda **_: calls.append("called"),
                        )
                    self.assertEqual([], calls)
                    self._assert_pre_consumption_oof_cleanup(output)
            with self.subTest(field=field, defect="missing"):
                candidate = self._candidate()
                candidate.pop(field)
                output = self.root / f"{field}-missing.json"
                calls = []
                with self.assertRaisesRegex(ProtocolValidationError, "THRESHOLD_CANDIDATE_REJECTED"):
                    self._oof(
                        output, candidates=[candidate],
                        evaluator=lambda **_: calls.append("called"),
                    )
                self.assertEqual([], calls)
                self._assert_pre_consumption_oof_cleanup(output)
        for name, candidate in (
            ("zero-target", self._candidate(target_count=0)),
            ("detected-overflow", self._candidate(detected_count=101)),
            ("false-positive-overflow", self._candidate(automatic_name_fp_count=101)),
            ("legacy-rate", {**self._candidate(), "detection": 1.0}),
        ):
            with self.subTest(candidate=name):
                output = self.root / f"{name}.json"
                calls = []
                with self.assertRaisesRegex(ProtocolValidationError, "THRESHOLD_CANDIDATE_REJECTED"):
                    self._oof(output, candidates=[candidate], evaluator=lambda **_: calls.append("called"))
                self.assertEqual([], calls)
                self._assert_pre_consumption_oof_cleanup(output)
        finite_threshold_cases = (
            ("auto-boolean", self._candidate(auto_threshold=True)),
            ("review-boolean", self._candidate(review_threshold=False)),
            ("auto-below-range", self._candidate(auto_threshold=-0.01)),
            ("review-above-range", self._candidate(review_threshold=1.01)),
            ("inverted-order", self._candidate(auto_threshold=.4, review_threshold=.5)),
        )
        for name, candidate in finite_threshold_cases:
            with self.subTest(candidate=name):
                output = self.root / f"{name}.json"
                calls = []
                with self.assertRaisesRegex(ProtocolValidationError, "THRESHOLD_CANDIDATE_REJECTED"):
                    self._oof(output, candidates=[candidate], evaluator=lambda **_: calls.append("called"))
                self.assertEqual([], calls)
                self._assert_pre_consumption_oof_cleanup(output)
        for name, candidate in (
            ("auto-nan", self._candidate(auto_threshold=float("nan"))),
            ("review-infinity", self._candidate(review_threshold=float("inf"))),
        ):
            with self.subTest(candidate=name):
                output = self.root / f"{name}.json"
                calls = []
                with self.assertRaisesRegex(ProtocolValidationError, "THRESHOLD_CANDIDATE_REJECTED"):
                    self._oof(
                        output, candidates=[candidate], calibration_candidates=[self._candidate()],
                        evaluator=lambda **_: calls.append("called"),
                    )
                self.assertEqual([], calls)
                self._assert_pre_consumption_oof_cleanup(output)
    def test_post_lease_evaluator_failures_are_durable_atomic_and_nonreplayable(self):
        cases = (
            ("exception", lambda **_: (_ for _ in ()).throw(RuntimeError("boom"))),
            ("malformed", lambda **_: [{}]),
        )
        for name, evaluator in cases:
            with self.subTest(failure=name):
                protocol_run_id = f"{self.run_id}-{name}"
                gate_payload = self.root / f"{name}-core-evidence.json"
                gate_payload.write_text(
                    json.dumps({"schema": "CoreGateOutputV1", "verified": True}),
                    encoding="utf-8",
                )
                gate = self.root / f"{name}-core.json"
                write_core_gate_receipt(
                    eval_root=self.root,
                    output_path=gate,
                    producer_role="core",
                    inputs=(),
                    public_content_read_count=0,
                    output_payload=gate_payload,
                    protocol_run_id=protocol_run_id,
                )
                run_root = self.root / name
                split = run_root / "folds.json"
                split.parent.mkdir(parents=True, exist_ok=True)
                split.write_text(json.dumps(create_split_lock(
                    self.hashes, split_seed=f"post-lease-{name}",
                )), encoding="utf-8")
                output = run_root / "oof.json"
                calls = []

                def tracked_evaluator(**kwargs):
                    calls.append(kwargs["fold"])
                    return evaluator(**kwargs)

                with self.assertRaisesRegex(ProtocolValidationError, "OOF_"):
                    self._oof(
                        output,
                        split=split,
                        evaluator=tracked_evaluator,
                        run_id=protocol_run_id,
                        gate=gate,
                    )
                failure = output.with_suffix(".failure.json")
                self.assertEqual(1 if name == "exception" else 5, len(calls))
                self.assertFalse(output.exists())
                self.assertTrue(failure.exists())
                self.assertTrue(any(self.root.glob(".public-oof-*.lease.json")))
                self.assertFalse(any(run_root.glob("fold-*/threshold.json")))
                self.assertFalse(any(run_root.glob("fold-*/predictions.jsonl")))
                replay_calls = []
                with self.assertRaisesRegex(ProtocolValidationError, "already been leased"):
                    self._oof(
                        run_root / "alternate-oof.json",
                        split=split,
                        evaluator=lambda **_: replay_calls.append("called"),
                        run_id=protocol_run_id,
                        gate=gate,
                    )
                self.assertEqual([], replay_calls)

    def test_oof_requires_complete_count_schema_and_failed_closed_denominator_coverage(self):
        partial = self._predictions()
        partial[0][0]["counts"].pop("automatic_fn")
        with self.assertRaisesRegex(ProtocolValidationError, "OOF_PREDICTION_REJECTED"):
            self._oof(self.root / "partial-counts.json", predictions=partial)
        self._assert_pre_consumption_oof_cleanup(self.root / "partial-counts.json")
        missing_status = self._predictions()
        missing_status[0][0].pop("status")
        with self.assertRaisesRegex(ProtocolValidationError, "OOF_PREDICTION_REJECTED"):
            self._oof(self.root / "missing-status.json", predictions=missing_status)
        self._assert_pre_consumption_oof_cleanup(self.root / "missing-status.json")

        extra_count = self._predictions()
        extra_count[0][0]["counts"]["detected_count"] = 1
        with self.assertRaisesRegex(ProtocolValidationError, "OOF_PREDICTION_REJECTED"):
            self._oof(self.root / "extra-count.json", predictions=extra_count)
        self._assert_pre_consumption_oof_cleanup(self.root / "extra-count.json")

        failed_closed = self._predictions()
        failed_closed[0][0].update({
            "status": "failed_closed",
            "counts": self._oof_counts(
                detection_tp=0, detection_fn=1,
                automatic_tp=0, automatic_fn=1,
                automatic_name_tp=0, automatic_name_fn=1,
                body_occurrence_tp=0, body_occurrence_fn=1,
                blocked_document_count=1,
            ),
        })
        oof = self.root / "failed-closed-oof.json"
        self._oof(oof, predictions=failed_closed)
        metrics, counts, _ = _public_oof_report_projection(
            self.root.resolve(),
            verify_oof(self.root, oof),
            json.loads(self.gold.read_text(encoding="utf-8")),
            _public_gold_sources(json.loads(self.gold.read_text(encoding="utf-8"))),
        )
        self.assertEqual(1, counts["failed_document_count"])
        self.assertEqual(1, counts["detection_fn"])
        self.assertEqual(24 / 25, metrics["occurrence_detection_recall"])

    def test_oof_report_rejects_prediction_aggregate_disagreement_with_locked_gold(self):
        predictions = self._predictions()
        predictions[0][0]["counts"] = self._oof_counts(
            detection_tp=0,
            detection_fn=2,
            automatic_tp=0,
            automatic_fn=2,
            automatic_name_tp=0,
            automatic_name_fn=1,
        )
        oof = self.root / "aggregate-mismatch-oof.json"
        self._oof(oof, predictions=predictions)
        gold = json.loads(self.gold.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(ProtocolValidationError, "disagrees with locked public-gold denominators"):
            _public_oof_report_projection(
                self.root.resolve(), verify_oof(self.root, oof), gold, _public_gold_sources(gold),
            )
    def test_count_bound_candidate_evidence_and_successful_holdout_e2e_report_chain(self):
        oof = self.root / "oof.json"
        candidates = [self._candidate(auto_threshold=.8, review_threshold=.8), self._candidate()]
        candidates_before = copy.deepcopy(candidates)
        self._oof(oof, candidates=candidates)
        self.assertEqual(candidates_before, candidates)
        synthetic = self.root / "synthetic.json"
        create_synthetic_split_lock(
            eval_root=self.root, output_path=synthetic, calibration_sha256s=["a" * 64],
            holdout_sha256s=["b" * 64], producer_role="synthetic-owner",
        )
        threshold = self.root / "threshold.json"
        before_lock = self._input_snapshot(oof, synthetic, self.gold, self.split)
        lock = lock_final_threshold(
            eval_root=self.root, output_path=threshold, oof_path=oof, synthetic_lock_path=synthetic,
            producer_role="threshold-locker", candidates=candidates,
        )
        self.assertEqual(candidates_before, candidates)
        self.assertEqual(before_lock, self._input_snapshot(oof, synthetic, self.gold, self.split))
        self.assertEqual((.6, .6), (lock["auto_threshold"], lock["review_threshold"]))
        self.assertEqual(candidates_before, lock["selection_evidence"])
        self.assertEqual(self._canonical_sha256(candidates_before), lock["selection_evidence_sha256"])
        for name, uncommitted in (
            ("changed", [self._candidate(auto_threshold=.8, review_threshold=.8, detected_count=99), self._candidate()]),
            ("substituted", [self._candidate(auto_threshold=.8, review_threshold=.8), self._candidate(auto_threshold=.7, review_threshold=.7)]),
            ("reordered", list(reversed(candidates))),
        ):
            with self.subTest(candidate_evidence=name):
                rejected_lock = self.root / f"{name}-threshold.json"
                with self.assertRaisesRegex(ProtocolValidationError, "threshold candidates must be exactly the verified OOF evidence"):
                    lock_final_threshold(
                        eval_root=self.root, output_path=rejected_lock, oof_path=oof, synthetic_lock_path=synthetic,
                        producer_role="owner", candidates=uncommitted,
                    )
                self.assertFalse(rejected_lock.exists())

        counts = {
            "detected_count": 99, "target_count": 100,
            "automatic_name_fp_count": 5, "automatic_name_count": 100,
        }
        calls = []
        def evaluator(*, threshold, holdout):
            calls.append((threshold, holdout))
            self.assertEqual(self.run_id, threshold["protocol_run_id"])
            self.assertEqual({
                "schema_version": "ImmutableUntouchedHoldoutV1",
                "immutable": True,
                "document_sha256s": ["b" * 64],
            }, holdout)
            return {
                "counts": counts, "measured_counters": counts,
                "metrics": {"detection": .99, "automatic_name_fp": .05},
            }
        holdout_payload = self._holdout_payload("success", ["b" * 64])
        holdout = self.root / "holdout.json"
        result = synthetic_holdout_once(
            eval_root=self.root, output_path=holdout, threshold_lock_path=threshold, synthetic_lock_path=synthetic,
            producer_role="owner", evaluator=evaluator, untouched_holdout_payload=holdout_payload,
        )
        self.assertEqual("success", result["status"])
        self.assertEqual(
            {"detection": 99 / 100, "automatic_name_fp": 5 / 100},
            result["metrics"],
        )
        self.assertEqual(
            {
                "detected_count": 99, "target_count": 100,
                "automatic_name_fp_count": 5, "automatic_name_count": 100,
            },
            result["counts"],
        )
        self.assertEqual(["b" * 64], json.loads(holdout_payload.read_text())["document_sha256s"])
        self.assertEqual(holdout_payload.name, result["holdout_payload"]["path"])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(self._consumption_markers()))
        alternate_holdout = self.root / "alternate-holdout.json"
        protected_artifacts = (oof, synthetic, threshold, holdout, holdout_payload, *self._consumption_markers())
        protected_digests = self._input_snapshot(*protected_artifacts)
        caller_counts = copy.deepcopy(counts)
        with self.assertRaisesRegex(ProtocolValidationError, "untouched holdout was already consumed"):
            synthetic_holdout_once(
                eval_root=self.root, output_path=alternate_holdout, threshold_lock_path=threshold, synthetic_lock_path=synthetic,
                producer_role="holdout-runner", evaluator=evaluator, untouched_holdout_payload=holdout_payload,
            )
        self.assertEqual(caller_counts, counts)
        self.assertEqual(1, len(calls))
        self.assertFalse(alternate_holdout.exists())
        self.assertFalse(alternate_holdout.with_suffix(".consumed.json").exists())
        self.assertEqual(protected_digests, self._input_snapshot(*protected_artifacts))
        payload = self.root / "e2e-payload.json"
        payload.write_text(json.dumps({"schema": "ThresholdE2EOutputV1", "verified": True}), encoding="utf-8")
        e2e = self.root / "e2e.json"
        write_threshold_e2e_receipt(
            eval_root=self.root, output_path=e2e, threshold_lock_path=threshold, holdout_path=holdout,
            producer_role="owner", output_payload=payload,
            measured_counters={
                "public_content_read_count": 0, "oof_read_count": 0,
                "final_lock_read_count": 0, "holdout_read_count": 0,
            },
            protocol_run_id=self.run_id,
        )
        for name, counters in (
            ("missing", {}),
            ("nonzero", {
                "public_content_read_count": 1, "oof_read_count": 0,
                "final_lock_read_count": 0, "holdout_read_count": 0,
            }),
        ):
            with self.subTest(measured_counters=name):
                with self.assertRaisesRegex(ProtocolValidationError, "THRESHOLD_E2E_COUNTERS_REJECTED"):
                    write_threshold_e2e_receipt(
                        eval_root=self.root, output_path=self.root / f"{name}-e2e.json",
                        threshold_lock_path=threshold, holdout_path=holdout, producer_role="owner",
                        output_payload=payload, measured_counters=counters, protocol_run_id=self.run_id,
                    )
        report = self.root / "report.json"
        report = self.root / "report.json"
        report_from_oof(
            eval_root=self.root, output_path=report, oof_path=oof, threshold_e2e_receipt=e2e,
            producer_role="reporter",
        )
        verified_report = verify_pilot_report(self.root, report)
        self.assertEqual("reported", verified_report["status"])
        report_payload = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(25, report_payload["counts"]["document_denominator"])
        self.assertEqual(25, report_payload["counts"]["document_count"])
        self.assertEqual(15, report_payload["counts"]["issued_documents"])
        self.assertEqual(10, report_payload["counts"]["review_documents"])
        self.assertEqual(25, report_payload["counts"]["occurrence_denominator"])
        self.assertEqual(25, report_payload["counts"]["detection_tp"])
        self.assertNotIn("detected_count", report_payload["counts"])
        self.assertEqual(1.0, report_payload["metrics"]["occurrence_detection_recall"])
        self.assertEqual(
            {"tp": 0, "fp": 0, "fn": 0},
            report_payload["metrics"]["region_tp_fp_fn"],
        )
        self.assertEqual(
            {
                "documents": 25,
                "pages": 25,
                "gold_pii": 25,
                "non_pii_lookalikes": 0,
            },
            report_payload["accuracy_denominators"],
        )
        self.assertEqual(
            {"value": 0, "target": 0, "comparison": "equal", "met": True, "denominator": 0},
            report_payload["accuracy_targets"]["fixed_region_omission_count"],
        )
        self.assertEqual(
            {"value": 1.0, "target": 0.99, "comparison": "greater_than_or_equal", "met": True, "denominator": 25},
            report_payload["accuracy_targets"]["body_occurrence_recall"],
        )
        self.assertEqual(
            {"value": None, "target": 0.05, "comparison": "less_than_or_equal", "met": None, "denominator": 0},
            report_payload["accuracy_targets"]["false_positive_rate"],
        )
        self.assertNotIn("body_occurrence_recall", report_payload["unavailable_metrics"])
        self.assertNotIn("region_tp_fp_fn", report_payload["unavailable_metrics"])
        self.assertNotIn("fixed_region_omission_count", report_payload["unavailable_metrics"])
        expected_oof_counts = {
            key: sum(
                prediction["counts"][key]
                for fold_predictions in self._predictions().values()
                for prediction in fold_predictions
            )
            for key in self._oof_counts()
        }
        for key, expected in expected_oof_counts.items():
            with self.subTest(count=key):
                self.assertEqual(expected, report_payload["counts"][key])
        self.assertEqual(
            {"status": "success", "metrics": {"detection": .99, "automatic_name_fp": .05}},
            report_payload["synthetic_holdout"],
        )
        with self.assertRaisesRegex(ProtocolValidationError, "producer_role"):
            report_from_oof(
                eval_root=self.root, output_path=self.root / "empty-producer-report.json",
                oof_path=oof, threshold_e2e_receipt=e2e, producer_role="",
            )
        original_report = hashlib.sha256(report.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProtocolValidationError, "already consumed"):
            report_from_oof(
                eval_root=self.root, output_path=self.root / "alternate-report.json", oof_path=oof,
                threshold_e2e_receipt=e2e, producer_role="owner",
            )
        self.assertFalse((self.root / "alternate-report.json").exists())
        self.assertEqual(original_report, hashlib.sha256(report.read_bytes()).hexdigest())
        oof_index = json.loads(oof.read_text(encoding="utf-8"))
        prediction_path = self.root / oof_index["folds"][0]["predictions"]["path"]
        original_prediction = prediction_path.read_bytes()
        prediction_path.chmod(0o600)
        prediction_path.write_bytes(b'{}\n')
        with self.assertRaises(ProtocolValidationError):
            verify_oof(self.root, oof)
        with self.assertRaises(ProtocolValidationError):
            verify_pilot_report(self.root, report)
        prediction_path.write_bytes(original_prediction)
        prediction_path.chmod(0o400)
        self.assertEqual("complete", verify_oof(self.root, oof)["status"])
        self.assertEqual("reported", verify_pilot_report(self.root, report)["status"])
        report_marker = next(self.root.glob(".pilot-report-*.lease.json"))
        original_marker = report_marker.read_bytes()
        report_marker.chmod(0o600)
        report_marker.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "lease is invalid"):
            verify_pilot_report(self.root, report)
        report_marker.write_bytes(original_marker)
        report_marker.chmod(0o400)
        self.assertEqual("reported", verify_pilot_report(self.root, report)["status"])

    def test_final_threshold_lock_replay_preserves_all_input_artifacts(self):
        oof = self.root / "replay-oof.json"
        candidates = [self._candidate()]
        self._oof(oof, candidates=candidates)
        synthetic = self.root / "replay-synthetic.json"
        create_synthetic_split_lock(
            eval_root=self.root, output_path=synthetic, calibration_sha256s=["a" * 64],
            holdout_sha256s=["b" * 64], producer_role="owner",
        )
        threshold = self.root / "replay-threshold.json"
        lock_final_threshold(
            eval_root=self.root, output_path=threshold, oof_path=oof, synthetic_lock_path=synthetic,
            producer_role="owner", candidates=candidates,
        )
        before = self._input_snapshot(oof, synthetic, threshold, self.split, self.gold)
        caller_candidates = copy.deepcopy(candidates)
        alternate_threshold = self.root / "alternate-replay-threshold.json"
        with self.assertRaises(ProtocolValidationError):
            lock_final_threshold(
                eval_root=self.root, output_path=alternate_threshold, oof_path=oof, synthetic_lock_path=synthetic,
                producer_role="owner", candidates=candidates,
            )
        self.assertFalse(alternate_threshold.exists())
        self.assertEqual(caller_candidates, candidates)
        self.assertEqual(before, self._input_snapshot(oof, synthetic, threshold, self.split, self.gold))
    def test_holdout_rejections_are_atomic_and_metrics_are_independently_derived(self):
        oof = self.root / "oof.json"
        self._oof(oof)
        synthetic = self.root / "synthetic.json"
        create_synthetic_split_lock(
            eval_root=self.root, output_path=synthetic, calibration_sha256s=["a" * 64],
            holdout_sha256s=["b" * 64], producer_role="owner",
        )
        threshold = self.root / "threshold.json"
        lock_final_threshold(
            eval_root=self.root, output_path=threshold, oof_path=oof, synthetic_lock_path=synthetic,
            producer_role="owner", candidates=[self._candidate()],
        )
        baseline_digests = {
            path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (oof, synthetic, threshold)
        }
        markers_before = set(self._consumption_markers())
        for name, document_sha256s in (
            ("overlap", ["a" * 64, "b" * 64]),
            ("wrong", ["c" * 64]),
            ("missing", []),
            ("extra", ["b" * 64, "c" * 64]),
        ):
            with self.subTest(membership=name):
                payload = self._holdout_payload(name, document_sha256s)
                output = self.root / f"{name}-holdout.json"
                calls = []
                with self.assertRaisesRegex(ProtocolValidationError, "holdout payload does not exactly bind the untouched holdout"):
                    synthetic_holdout_once(
                        eval_root=self.root, output_path=output, threshold_lock_path=threshold,
                        synthetic_lock_path=synthetic, producer_role="owner",
                        evaluator=lambda **_: calls.append("called"), untouched_holdout_payload=payload,
                    )
                self.assertEqual([], calls)
                self.assertFalse(output.exists())
                self.assertEqual(markers_before, set(self._consumption_markers()))
                self.assertEqual(baseline_digests, {
                    path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (oof, synthetic, threshold)
                })
        counts = {
            "detected_count": 1, "target_count": 2,
            "automatic_name_fp_count": 0, "automatic_name_count": 1,
        }
        for name, metrics in (
            ("detection", {"detection": 1.0, "automatic_name_fp": 0.0}),
            ("automatic-name-fp", {"detection": 0.5, "automatic_name_fp": 1.0}),
        ):
            with self.subTest(metric=name):
                _metric_oof, metric_synthetic, metric_threshold = self._locked_holdout_inputs(name)
                payload = self._holdout_payload(name, ["b" * 64])
                output = self.root / f"{name}-mismatch-holdout.json"
                calls = []

                def evaluator(**_):
                    calls.append("called")
                    return {"counts": counts, "measured_counters": counts, "metrics": metrics}

                markers_before = set(self._consumption_markers())
                with self.assertRaisesRegex(ProtocolValidationError, "holdout metrics do not match counts"):
                    synthetic_holdout_once(
                        eval_root=self.root, output_path=output, threshold_lock_path=metric_threshold,
                        synthetic_lock_path=metric_synthetic, producer_role="owner", evaluator=evaluator,
                        untouched_holdout_payload=payload,
                    )
                markers = set(self._consumption_markers()) - markers_before
                self.assertEqual(["called"], calls)
                self.assertFalse(output.exists())
                self.assertEqual(1, len(markers))
                marker = markers.pop()
                protected_artifacts = (oof, metric_synthetic, metric_threshold, payload, marker)
                protected_digests = {
                    path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_artifacts
                }
                alternate_output = self.root / f"{name}-alternate-holdout.json"
                with self.assertRaisesRegex(ProtocolValidationError, "untouched holdout was already consumed"):
                    synthetic_holdout_once(
                        eval_root=self.root, output_path=alternate_output, threshold_lock_path=metric_threshold,
                        synthetic_lock_path=metric_synthetic, producer_role="owner", evaluator=evaluator,
                        untouched_holdout_payload=payload,
                    )
                self.assertEqual(["called"], calls)
                self.assertFalse(alternate_output.exists())
                self.assertEqual(protected_digests, {
                    path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_artifacts
                })
        missing_counter_output = self.root / "missing-measured-counters.json"
        missing_payload = self._holdout_payload("missing-counters", ["b" * 64])
        markers_before = set(self._consumption_markers())
        with self.assertRaisesRegex(ProtocolValidationError, "measured_counters"):
            synthetic_holdout_once(
                eval_root=self.root, output_path=missing_counter_output, threshold_lock_path=threshold,
                synthetic_lock_path=synthetic, producer_role="owner",
                evaluator=lambda **_: {
                    "counts": counts, "metrics": {"detection": .5, "automatic_name_fp": 0.0},
                },
                untouched_holdout_payload=missing_payload,
            )
        self.assertFalse(missing_counter_output.exists())
        self.assertEqual(1, len(set(self._consumption_markers()) - markers_before))

    def test_receipts_require_explicit_identity_and_oof_rejects_raw_prediction_fields(self):
        with self.assertRaises(ProtocolValidationError):
            make_protocol_receipt(eval_root=self.root, output_path=self.root / "missing.json", artifact_kind="stage", producer_role="runner", status="passed", protocol_run_id="unassigned")
        predictions = self._predictions()
        predictions[0][0]["raw_text"] = "canary"
        output = self.root / "raw-oof.json"
        with self.assertRaises(ProtocolValidationError):
            self._oof(output, predictions=predictions)
        self._assert_pre_consumption_oof_cleanup(output)
    def test_receipt_self_reference_is_rejected_before_hash_verification(self):
        receipt_path = self.root / "self-referential-receipt.json"
        receipt = json.loads(self.gate.read_text())
        receipt["outputs"] = [{
            "path": receipt_path.name,
            "sha256": "0" * 64,
            "role": "output",
            "kind": "core_gate_output",
        }]
        receipt["receipt_sha256"] = "0" * 64
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "RECEIPT_SELF_REFERENCE_REJECTED"):
            verify_protocol_receipt(self.root, receipt_path)


if __name__ == "__main__":
    unittest.main()
