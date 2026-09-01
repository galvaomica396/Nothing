import hashlib
import json
import tempfile
import os
import unittest
import zipfile
from pathlib import Path

from masking_evaluation import ProtocolValidationError, canonical_json_sha256, verify_protocol_receipt
from tools.annotate_public_document_gold import PublicGoldWorkflowError, initialize_batch
from tools.verify_gold_manifest import PublicGoldReviewError, lock_public_gold


class PublicGoldWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.issued = self.root / "issued"
        self.issued.mkdir()
        for index in range(15):
            (self.issued / f"issued-{index}.pdf").write_bytes(f"synthetic-issued-{index}".encode())
        self.review = self.root / "review.zip"
        with zipfile.ZipFile(self.review, "w") as archive:
            for index in range(10):
                archive.writestr(f"review-{index}.pdf", f"synthetic-review-{index}")
        self.receipt = self.root / "schema.json"
        schema_payload = self.root / "schema-payload.json"
        schema_payload.write_text(json.dumps({"schema": "IndependentGoldManifestV1"}), encoding="utf-8")
        receipt = {
            "schema": "ImmutableReceipt", "version": "V2",
            "protocol_version": "IndependentEvaluationProtocolV1",
            "stage": "synthetic_schema_lock", "protocolRunId": "test-run",
            "producer": {"role": "schema_owner"}, "command": "test-fixture",
            "timestamp": "2026-08-02T00:00:00+00:00", "immutable": True, "status": "locked",
            "inputs": [], "outputs": [{
                "path": schema_payload.name, "sha256": hashlib.sha256(schema_payload.read_bytes()).hexdigest(),
                "role": "output", "kind": "schema",
            }],
            "counts": {},
        }
        receipt["receipt_sha256"] = canonical_json_sha256(receipt)
        self.receipt.write_text(json.dumps(receipt), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _initialize(self, name="drafts"):
        drafts, locators = self.root / name, self.root / f"{name}-locators.json"
        result = initialize_batch(
            issued_dir=self.issued, review_zip=self.review, schema_receipt=self.receipt,
            sidecar_out=drafts, author_id="author", eval_root=self.root, locator_map_out=locators,
        )
        return drafts, locators, result
    @staticmethod
    def _canonical_sha256(value):
        return hashlib.sha256(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _tree_snapshot(root):
        return {
            path.relative_to(root).as_posix(): (
                "symlink", os.readlink(path)
            ) if path.is_symlink() else (
                "file", hashlib.sha256(path.read_bytes()).hexdigest()
            )
            for path in sorted(root.rglob("*"))
            if path.is_file() or path.is_symlink()
        }

    def _assert_initialize_rejected_without_mutation(self, *, issued, review, name):
        before_root = self._tree_snapshot(self.root)
        before_issued = (
            self._tree_snapshot(issued) if issued.is_dir() and not issued.is_symlink()
            else ("symlink", os.readlink(issued)) if issued.is_symlink()
            else hashlib.sha256(Path(issued).read_bytes()).hexdigest()
        )
        before_review = hashlib.sha256(Path(review).read_bytes()).hexdigest()
        drafts, locators = self.root / name, self.root / f"{name}-locators.json"
        with self.assertRaises(PublicGoldWorkflowError):
            initialize_batch(
                issued_dir=issued, review_zip=review, schema_receipt=self.receipt,
                sidecar_out=drafts, author_id="author", eval_root=self.root,
                locator_map_out=locators,
            )
        self.assertFalse(drafts.exists())
        self.assertFalse(locators.exists())
        self.assertEqual(before_root, self._tree_snapshot(self.root))
        self.assertEqual(before_issued, (
            self._tree_snapshot(issued) if issued.is_dir() and not issued.is_symlink()
            else ("symlink", os.readlink(issued)) if issued.is_symlink()
            else hashlib.sha256(Path(issued).read_bytes()).hexdigest()
        ))
        self.assertEqual(before_review, hashlib.sha256(Path(review).read_bytes()).hexdigest())


    def _complete_sidecars(self, directory):
        index = json.loads((directory / "public-gold-index.json").read_text())
        for entry in index["documents"]:
            path = directory / entry["sidecar"]
            sidecar = json.loads(path.read_text())
            sidecar.update({
                "pages": [{"page_index": 0, "width": 100, "height": 100}],
                "segments": [{"id": "s", "page_index": 0, "type": "body", "offsets": {"start": 0, "end": 1}}],
                "regions": [{"id": "r", "page_index": 0, "type": "body", "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}]}],
                "occurrences": [{"id": "o", "segment_id": "s", "region_id": "r", "page_index": 0,
                                 "category": "person_name", "offsets": {"start": 0, "end": 1},
                                 "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}], "ocr_confidence": None}],
                "annotation_completion": {
                    "pages": "completed", "segments": "completed", "regions": "completed",
                    "occurrences": "completed", "negatives": "none_confirmed",
                    "protected_neighbors": "none_confirmed",
                },
            })
            path.write_text(json.dumps(sidecar), encoding="utf-8")
        self._refresh_annotation_receipt(directory)

    def _refresh_annotation_receipt(self, directory):
        receipt_path = directory / "receipt.json"
        receipt = json.loads(receipt_path.read_text())
        for reference in receipt["inputs"]:
            if reference["kind"] == "annotation_sidecar":
                reference["sha256"] = hashlib.sha256(
                    (self.root / reference["path"]).read_bytes()
                ).hexdigest()
        receipt["receipt_sha256"] = canonical_json_sha256({
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        })
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")


    def _assert_lock_rejected_atomically(self, drafts, locators, name="locked"):
        before = self._tree_snapshot(self.root)
        locator_payload = json.loads(Path(locators).read_text(encoding="utf-8"))
        external_inputs = {}
        for value in locator_payload.get("locators", {}).values():
            path = Path(value)
            path = path if path.is_absolute() else self.root / path
            if path.exists() and not path.is_relative_to(self.root):
                external_inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        locked = self.root / name
        with self.assertRaises(PublicGoldReviewError):
            lock_public_gold(
                sidecar_dir=drafts, schema_receipt=self.receipt, reviewer_id="reviewer",
                lock_out=locked, eval_root=self.root, locator_map=locators,
            )
        self.assertFalse(locked.exists())
        self.assertEqual(before, self._tree_snapshot(self.root))
        self.assertEqual(external_inputs, {
            path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in external_inputs
        })

    @unittest.skipIf(
        os.name == "nt",
        "public-gold publication fixtures require POSIX directory-fsync and symlink semantics",
    )
    def test_exact_composition_and_input_sensitive_deterministic_identity(self):
        one_drafts, _, first = self._initialize("one")
        _, _, second = self._initialize("two")
        first_index = json.loads((one_drafts / "public-gold-index.json").read_text())
        identity = {
            key: first_index[key]
            for key in ("schema_version", "schema_receipt", "documents", "split_sha256")
        }
        self.assertEqual(
            hashlib.sha256(json.dumps(
                identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest(),
            first["gold_identity_sha256"],
        )
        split = json.loads((one_drafts / "public-folds.json").read_text())
        split_payload = {key: value for key, value in split.items() if key != "split_sha256"}
        self.assertEqual(
            hashlib.sha256(json.dumps(
                split_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")).hexdigest(),
            first["split_sha256"],
        )
        self.assertEqual((25, 15, 10), (first["document_count"], first["issued_count"], first["review_count"]))
        self.assertEqual(first["gold_identity_sha256"], second["gold_identity_sha256"])
        self.assertEqual(first["split_sha256"], second["split_sha256"])

        (self.issued / "issued-0.pdf").write_bytes(b"changed-issued-source")
        _, _, changed = self._initialize("source-changed")
        self.assertNotEqual(first["gold_identity_sha256"], changed["gold_identity_sha256"])
        self.assertNotEqual(first["split_sha256"], changed["split_sha256"])

    def test_missing_extra_duplicate_and_wrong_class_compositions_clean_up(self):
        cases = (
            ("missing", range(14), range(10), None, "public pilot must contain exactly 15 issued and 10 review PDFs"),
            ("extra", range(16), range(10), None, "public pilot must contain exactly 15 issued and 10 review PDFs"),
            ("duplicate", range(15), range(10), 0, "public pilot document hashes must be unique"),
            ("wrong-class", range(16), range(9), None, "public pilot must contain exactly 15 issued and 10 review PDFs"),
        )
        for name, issued_range, review_range, duplicate_issued_index, reason in cases:
            case_root = self.root / name
            case_root.mkdir()
            issued = case_root / "issued"
            issued.mkdir()
            for index in issued_range:
                (issued / f"{index}.pdf").write_bytes(f"issued-{index}".encode())
            review = case_root / "review.zip"
            with zipfile.ZipFile(review, "w") as archive:
                for index in review_range:
                    content = (
                        f"issued-{duplicate_issued_index}".encode()
                        if duplicate_issued_index is not None and index == 0
                        else f"review-{index}".encode()
                    )
                    archive.writestr(f"{index}.pdf", content)
            out, locators = case_root / "drafts", case_root / "locators.json"
            with self.assertRaisesRegex(PublicGoldWorkflowError, reason):
                initialize_batch(
                    issued_dir=issued, review_zip=review, schema_receipt=self.receipt,
                    sidecar_out=out, author_id="author", eval_root=self.root, locator_map_out=locators,
                )
            self.assertFalse(out.exists())
            self.assertFalse(locators.exists())
    @unittest.skipIf(
        os.name == "nt",
        "public-gold publication fixtures require POSIX directory-fsync and symlink semantics",
    )
    def test_initialization_rejects_external_symlink_and_hostile_zip_members_without_mutation(self):
        external = Path(self.temp.name).parent / f"{self.root.name}-external"
        external.mkdir()
        external_issued = external / "issued"
        external_issued.mkdir()
        for index in range(15):
            (external_issued / f"issued-{index}.pdf").write_bytes(f"issued-{index}".encode())
        self._assert_initialize_rejected_without_mutation(
            issued=external_issued, review=self.review, name="external-issued",
        )

        linked_issued = self.root / "linked-issued"
        linked_issued.symlink_to(external_issued, target_is_directory=True)
        self._assert_initialize_rejected_without_mutation(
            issued=linked_issued, review=self.review, name="symlink-issued",
        )

        for name, member in (
            ("parent", "../review-0.pdf"),
            ("absolute", "/review-0.pdf"),
            ("backslash", r"nested\review-0.pdf"),
        ):
            hostile = self.root / f"{name}.zip"
            with zipfile.ZipFile(hostile, "w") as archive:
                for index in range(9):
                    archive.writestr(f"review-{index}.pdf", f"review-{index}".encode())
                archive.writestr(member, b"hostile")
            self._assert_initialize_rejected_without_mutation(
                issued=self.issued, review=hostile, name=f"{name}-drafts",
            )

        symlink_zip = self.root / "symlink-member.zip"
        link = zipfile.ZipInfo("review-link.pdf")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        with zipfile.ZipFile(symlink_zip, "w") as archive:
            for index in range(9):
                archive.writestr(f"review-{index}.pdf", f"review-{index}".encode())
            archive.writestr(link, b"target.pdf")
        self._assert_initialize_rejected_without_mutation(
            issued=self.issued, review=symlink_zip, name="symlink-member-drafts",
        )

    def test_schema_receipt_requires_verified_stage_outputs(self):
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        receipt["outputs"] = []
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        self.receipt.write_text(json.dumps(receipt), encoding="utf-8")
        self._assert_initialize_rejected_without_mutation(
            issued=self.issued, review=self.review, name="missing-output",
        )
    @unittest.skipIf(
        os.name == "nt",
        "public-gold publication fixtures require POSIX directory-fsync and symlink semantics",
    )
    def test_source_index_sidecar_locator_and_hash_tampering_reject_lock_atomically(self):
        drafts, locators, _ = self._initialize()
        self._complete_sidecars(drafts)
        index_path = drafts / "public-gold-index.json"
        index = json.loads(index_path.read_text())
        index["documents"][0]["source_class"] = "review"
        index_path.write_text(json.dumps(index), encoding="utf-8")
        self._assert_lock_rejected_atomically(drafts, locators, "index-tampered")
        drafts, locators, _ = self._initialize("class-swap")
        self._complete_sidecars(drafts)
        index_path = drafts / "public-gold-index.json"
        index = json.loads(index_path.read_text())
        issued_entry = next(item for item in index["documents"] if item["source_class"] == "issued")
        review_entry = next(item for item in index["documents"] if item["source_class"] == "review")
        for field in ("source_class", "form", "profile"):
            issued_entry[field], review_entry[field] = review_entry[field], issued_entry[field]
        index_path.write_text(json.dumps(index), encoding="utf-8")
        self._assert_lock_rejected_atomically(drafts, locators, "count-preserving-class-swap")

        drafts, locators, _ = self._initialize("sidecar")
        self._complete_sidecars(drafts)
        index = json.loads((drafts / "public-gold-index.json").read_text())
        sidecar_path = drafts / index["documents"][0]["sidecar"]
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["document"]["input_sha256"] = "0" * 64
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        self._assert_lock_rejected_atomically(drafts, locators, "sidecar-tampered")

        drafts, locators, _ = self._initialize("non-first-sidecar")
        self._complete_sidecars(drafts)
        index = json.loads((drafts / "public-gold-index.json").read_text())
        sidecar_path = drafts / index["documents"][-1]["sidecar"]
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["provenance"]["detector_output_imported"] = True
        sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
        self._assert_lock_rejected_atomically(drafts, locators, "non-first-sidecar-tampered")

        drafts, locators, _ = self._initialize("locator")
        self._complete_sidecars(drafts)
        locator_payload = json.loads(locators.read_text())
        digest = next(iter(locator_payload["locators"]))
        wrong_source = next(path for path in self.issued.glob("*.pdf") if hashlib.sha256(path.read_bytes()).hexdigest() != digest)
        locator_payload["locators"][digest] = str(wrong_source.relative_to(self.root))
        locators.write_text(json.dumps(locator_payload), encoding="utf-8")
        self._assert_lock_rejected_atomically(drafts, locators, "locator-existing-wrong")

        drafts, locators, _ = self._initialize("locator-outside")
        self._complete_sidecars(drafts)
        locator_payload = json.loads(locators.read_text())
        digest = next(iter(locator_payload["locators"]))
        outside = Path(self.temp.name).parent / f"{self.root.name}-outside.pdf"
        outside.write_bytes(b"outside")
        locator_payload["locators"][digest] = str(outside)
        locators.write_text(json.dumps(locator_payload), encoding="utf-8")
        self._assert_lock_rejected_atomically(drafts, locators, "locator-outside-lock")

        drafts, locators, _ = self._initialize("locator-symlink")
        self._complete_sidecars(drafts)
        locator_payload = json.loads(locators.read_text())
        digest = next(iter(locator_payload["locators"]))
        link = self.root / "source-link.pdf"
        link.symlink_to(next(self.issued.glob("*.pdf")))
        locator_payload["locators"][digest] = str(link.relative_to(self.root))
        locators.write_text(json.dumps(locator_payload), encoding="utf-8")
        self._assert_lock_rejected_atomically(drafts, locators, "locator-symlink-lock")

        drafts, locators, _ = self._initialize("schema")
        self._complete_sidecars(drafts)
        schema_payload = self.root / "schema-payload.json"
        original_payload = schema_payload.read_bytes()
        schema_payload.write_text(json.dumps({"schema": "tampered"}), encoding="utf-8")
        with self.assertRaisesRegex(PublicGoldReviewError, "schema receipt"):
            lock_public_gold(
                sidecar_dir=drafts, schema_receipt=self.receipt, reviewer_id="reviewer",
                lock_out=self.root / "schema-tampered", eval_root=self.root, locator_map=locators,
            )
        self.assertFalse((self.root / "schema-tampered").exists())

        # Restore the independently verified schema payload before changing source bytes.
        schema_payload.write_bytes(original_payload)
        drafts, locators, _ = self._initialize("hash")
        self._complete_sidecars(drafts)
        source = next(self.issued.glob("*.pdf"))
        source.write_bytes(b"tampered-source")
        self._assert_lock_rejected_atomically(drafts, locators, "source-hash-tampered")

    @unittest.skipIf(
        os.name == "nt",
        "public-gold publication fixtures require POSIX directory-fsync and symlink semantics",
    )
    def test_lock_receipt_and_manifest_are_published_together_after_independent_review(self):
        drafts, locators, _ = self._initialize()
        self._complete_sidecars(drafts)
        with self.assertRaisesRegex(PublicGoldReviewError, "reviewer must differ"):
            lock_public_gold(
                sidecar_dir=drafts, schema_receipt=self.receipt, reviewer_id="author",
                lock_out=self.root / "same-author-review", eval_root=self.root, locator_map=locators,
            )
        self.assertFalse((self.root / "same-author-review").exists())

        input_snapshot = self._tree_snapshot(self.root)
        locked = self.root / "locked"
        result = lock_public_gold(
            sidecar_dir=drafts, schema_receipt=self.receipt, reviewer_id="reviewer",
            lock_out=locked, eval_root=self.root, locator_map=locators,
        )
        self.assertEqual("locked", result["status"])
        self.assertEqual(
            input_snapshot,
            {
                path: digest for path, digest in self._tree_snapshot(self.root).items()
                if not path.startswith("locked/")
            },
        )
        manifest_path, receipt_path = locked / "public-gold-manifest.json", locked / "receipt.json"
        manifest = json.loads(manifest_path.read_text())
        receipt = json.loads(receipt_path.read_text())
        self.assertEqual("locked", manifest["status"])
        self.assertEqual(result["manifest_sha256"], manifest["manifest_sha256"])
        self.assertEqual("locked", receipt["status"])
        self.assertEqual(25, receipt["counts"]["document_count"])
        for document in manifest["documents"]:
            with self.subTest(document=document["document"]["input_sha256"]):
                provenance = document["provenance"]
                self.assertEqual({"author", "reviewer"}, {
                    provenance["author"]["id"], provenance["reviewer"]["id"],
                })
                self.assertEqual("approved", provenance["reviewer"]["decision"])
                self.assertFalse(provenance["detector_output_imported"])
        self.assertEqual(hashlib.sha256(b"reviewer").hexdigest(), receipt["authorization"]["reviewer_digest"])
        manifest_digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        self.assertEqual(
            [{
                "path": "locked/public-gold-manifest.json", "sha256": manifest_digest,
                "role": "output", "kind": "locked_manifest",
            }],
            receipt["outputs"],
        )
        artifact_text = json.dumps(
            {"manifest": manifest, "receipt": receipt}, sort_keys=True, separators=(",", ":"),
        )
        self.assertNotIn(str(self.root), artifact_text)
        self.assertNotIn(str(self.root / "sensitive-source.pdf"), artifact_text)
        verified_receipt = verify_protocol_receipt(self.root, receipt_path, artifact_kind="public_gold_lock", required_status="locked")
        self.assertEqual(receipt, verified_receipt)
        self.assertEqual(
            receipt["receipt_sha256"],
            self._canonical_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"}),
        )
        original_bytes = {path: path.read_bytes() for path in (manifest_path, receipt_path)}
        original_digests = {
            path.name: hashlib.sha256(content).hexdigest() for path, content in original_bytes.items()
        }
        manifest_path.write_bytes(b'{"tampered":true}\n')
        with self.assertRaisesRegex(ProtocolValidationError, "referenced artifact hash mismatch"):
            verify_protocol_receipt(self.root, receipt_path, artifact_kind="public_gold_lock", required_status="locked")
        manifest_path.write_bytes(original_bytes[manifest_path])
        receipt["outputs"][0]["sha256"] = "0" * 64
        receipt["receipt_sha256"] = canonical_json_sha256({key: value for key, value in receipt.items() if key != "receipt_sha256"})
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "referenced artifact hash mismatch"):
            verify_protocol_receipt(self.root, receipt_path, artifact_kind="public_gold_lock", required_status="locked")
        receipt_link = locked / "manifest-link.json"
        receipt_link.symlink_to(manifest_path.name)
        for path in (
            f"../{self.root.name}/locked/public-gold-manifest.json",
            str(manifest_path),
            "locked/../locked/public-gold-manifest.json",
            "locked/manifest-link.json",
        ):
            with self.subTest(receipt_path=path):
                receipt["outputs"] = [{
                    "path": path, "sha256": manifest_digest,
                    "role": "output", "kind": "locked_manifest",
                }]
                receipt["receipt_sha256"] = self._canonical_sha256({
                    key: value for key, value in receipt.items() if key != "receipt_sha256"
                })
                receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                with self.assertRaises(ProtocolValidationError):
                    verify_protocol_receipt(
                        self.root, receipt_path, artifact_kind="public_gold_lock", required_status="locked",
                    )
        receipt_link.unlink()
        receipt["outputs"] = [{
            "path": "locked/receipt.json", "sha256": "0" * 64,
            "role": "output", "kind": "locked_manifest",
        }]
        receipt["receipt_sha256"] = canonical_json_sha256({
            key: value for key, value in receipt.items() if key != "receipt_sha256"
        })
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaisesRegex(ProtocolValidationError, "^RECEIPT_SELF_REFERENCE_REJECTED$"):
            verify_protocol_receipt(self.root, receipt_path, artifact_kind="public_gold_lock", required_status="locked")
        for path, content in original_bytes.items():
            path.write_bytes(content)
        with self.assertRaises(PublicGoldReviewError):
            lock_public_gold(
                sidecar_dir=drafts, schema_receipt=self.receipt, reviewer_id="reviewer",
                lock_out=locked, eval_root=self.root, locator_map=locators,
            )
        self.assertEqual(original_digests, {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (manifest_path, receipt_path)
        })


if __name__ == "__main__":
    unittest.main()
