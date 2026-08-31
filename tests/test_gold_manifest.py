import copy
import hashlib
import json
import unittest

from masking_evaluation import (ManifestValidationError, create_split_lock, lock_manifest,
                                validate_manifest, validate_split_lock)


HASH = "a" * 64


def manifest():
    return {
        "schema_version": "IndependentGoldManifestV1",
        "geometry_policy_version": "GeometryPolicyV1",
        "coordinate_space": "pdf_points_top_left",
        "profile": "mixed",
        "policy_version": "synthetic-policy-v1",
        "source_class": "synthetic",
        "form": "synthetic",
        "document": {"document_id": "synthetic-1", "input_sha256": HASH, "output_sha256": "b" * 64},
        "provenance": {"author": {"id": "annotator"}, "reviewer": {
            "id": "reviewer", "decision": "approved", "adjudication": "independent_review",
        }, "detector_output_imported": False},
        "pages": [{"page_index": 0, "width": 100, "height": 100}],
        "segments": [{"id": "segment-1", "page_index": 0, "type": "body", "offsets": {"start": 0, "end": 6}}],
        "regions": [{"id": "region-1", "page_index": 0, "type": "body", "rects": [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}]}],
        "occurrences": [{"id": "occurrence-1", "segment_id": "segment-1", "region_id": "region-1", "page_index": 0,
                         "category": "person_name", "offsets": {"start": 0, "end": 3}, "text_hash": "c" * 64,
                         "ocr_confidence": None,
                         "rects": [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}]}],
        "annotation_status": "reviewed_approved",
        "annotation_completion": {
            "pages": "completed", "segments": "completed", "regions": "completed",
            "occurrences": "completed", "negatives": "completed", "protected_neighbors": "completed",
        },
        "negatives": [{"id": "negative-1", "page_index": 0, "kind": "name", "category": "person_name", "rects": [{"x0": 30, "y0": 10, "x1": 40, "y1": 20}]}],
        "protected_neighbors": [{"id": "neighbor-1", "page_index": 0, "rects": [{"x0": 50, "y0": 10, "x1": 60, "y1": 20}]}],
    }


class GoldManifestTests(unittest.TestCase):
    @staticmethod
    def _independent_manifest_digest(value):
        payload = {key: item for key, item in value.items() if key != "manifest_sha256"}
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
    @staticmethod
    def _independent_split_digest(value):
        payload = {key: item for key, item in value.items() if key != "split_sha256"}
        return hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()

    def test_valid_manifest_locks_with_canonical_hash(self):
        locked = lock_manifest(manifest())
        self.assertEqual(self._independent_manifest_digest(locked), locked["manifest_sha256"])
        validate_manifest(locked, require_locked=True)
    def test_lock_manifest_is_non_mutating_and_hashes_an_independent_canonical_oracle(self):
        source = manifest()
        source_before = copy.deepcopy(source)
        locked = lock_manifest(source)
        self.assertEqual(source_before, source)
        self.assertNotIn("manifest_sha256", source)
        self.assertIsNot(source, locked)
        self.assertEqual(self._independent_manifest_digest(locked), locked["manifest_sha256"])

        replay = copy.deepcopy(locked)
        validate_manifest(replay, require_locked=True)
        self.assertEqual(locked, replay)
    def test_locked_manifest_requires_review_status_classification_and_completion(self):
        cases = (
            ("review", lambda value: value["provenance"]["reviewer"].update(adjudication="pending"), "reviewer"),
            ("status", lambda value: value.update(annotation_status="draft_unreviewed"), "reviewed_approved"),
            ("source-form", lambda value: value.update(form="review"), "source/form"),
            ("completion", lambda value: value["annotation_completion"].update(negatives="none_confirmed"), "completion"),
        )
        for name, mutate, diagnostic in cases:
            with self.subTest(requirement=name):
                value = manifest()
                mutate(value)
                with self.assertRaisesRegex(ManifestValidationError, diagnostic):
                    validate_manifest(value, require_locked=True)
    def test_fixed_schema_geometry_coordinate_and_profile_contracts_are_enforced(self):
        cases = (
            ("schema_version", "IndependentGoldManifestV0", "schema_version must be IndependentGoldManifestV1"),
            ("geometry_policy_version", "GeometryPolicyV0", "geometry_policy_version must be GeometryPolicyV1"),
            ("coordinate_space", "pixel_top_left", "coordinate_space must be pdf_points_top_left"),
            ("profile", "official", "manifest.profile must be a canonical document profile"),
        )
        for field, invalid_value, expected in cases:
            with self.subTest(field=field):
                value = manifest()
                value[field] = invalid_value
                with self.assertRaises(ManifestValidationError) as raised:
                    validate_manifest(value)
                self.assertEqual(expected, str(raised.exception))

    def test_duplicate_occurrence_is_rejected(self):
        value = manifest()
        value["occurrences"].append(copy.deepcopy(value["occurrences"][0]))
        with self.assertRaisesRegex(ManifestValidationError, "duplicate occurrence id"):
            validate_manifest(value)
    def test_duplicate_ids_are_rejected_for_every_manifest_entity_class(self):
        cases = (
            ("segments", "duplicate segment id"),
            ("regions", "duplicate region id"),
            ("occurrences", "duplicate occurrence id"),
            ("negatives", "duplicate negative id"),
            ("protected_neighbors", "duplicate protected neighbor id"),
        )
        for collection, diagnostic in cases:
            with self.subTest(collection=collection):
                value = manifest()
                value[collection].append(copy.deepcopy(value[collection][0]))
                with self.assertRaisesRegex(ManifestValidationError, diagnostic):
                    validate_manifest(value)

    def test_unknown_raw_nested_fields_are_rejected_without_echoing_values(self):
        cases = (
            ("document", "raw_path"),
            ("provenance", "raw_author_name"),
            ("pages", "extracted_text"),
            ("segments", "raw_text"),
            ("regions", "source_path"),
            ("occurrences", "matched_text"),
            ("negatives", "candidate_name"),
            ("protected_neighbors", "source_path"),
        )
        canary = "Kim /private/case.pdf"
        forbidden_fragments = (canary, "Kim", "/private/case.pdf", "/private", "case.pdf")
        for collection, field in cases:
            with self.subTest(collection=collection, field=field):
                value = manifest()
                target = value[collection][0] if isinstance(value[collection], list) else value[collection]
                target[field] = canary
                with self.assertRaises(ManifestValidationError) as raised:
                    validate_manifest(value)
                for fragment in forbidden_fragments:
                    self.assertNotIn(fragment, str(raised.exception))

    def test_shared_segment_and_region_references_are_allowed(self):
        value = manifest()
        duplicate = copy.deepcopy(value["occurrences"][0])
        duplicate["id"] = "occurrence-2"
        duplicate["offsets"] = {"start": 3, "end": 6}
        duplicate["rects"] = [{"x0": 20, "y0": 10, "x1": 30, "y1": 20}]
        value["occurrences"].append(duplicate)
        validate_manifest(value)

    def test_body_occurrence_may_omit_region(self):
        value = manifest()
        value["occurrences"][0].pop("region_id")
        validate_manifest(value)

    def test_reviewer_conflict_and_detector_import_are_rejected(self):
        value = manifest()
        value["provenance"]["reviewer"]["id"] = "annotator"
        with self.assertRaisesRegex(ManifestValidationError, "different"):
            validate_manifest(value)
        value = manifest()
        value["provenance"]["detector_output_imported"] = True
        with self.assertRaisesRegex(ManifestValidationError, "detector_output_imported"):
            validate_manifest(value)

    def test_locked_hash_binds_every_security_relevant_manifest_section(self):
        mutations = (
            ("schema_version", lambda value: value.update(schema_version="IndependentGoldManifestV0")),
            ("geometry_policy_version", lambda value: value.update(geometry_policy_version="GeometryPolicyV0")),
            ("coordinate_space", lambda value: value.update(coordinate_space="pixel_top_left")),
            ("profile", lambda value: value.update(profile="official")),
            ("policy_version", lambda value: value.update(policy_version="synthetic-policy-v2")),
            ("source_class", lambda value: value.update(source_class="production")),
            ("form", lambda value: value.update(form="review")),
            ("document", lambda value: value["document"].update(document_id="synthetic-2")),
            ("provenance", lambda value: value["provenance"]["author"].update(id="different-annotator")),
            ("pages", lambda value: value["pages"][0].update(width=101)),
            ("segments", lambda value: value["segments"][0]["offsets"].update(end=5)),
            ("regions", lambda value: value["regions"][0]["rects"][0].update(x1=21)),
            ("occurrences", lambda value: value["occurrences"][0]["offsets"].update(end=2)),
            ("annotation_status", lambda value: value.update(annotation_status="draft")),
            ("annotation_completion", lambda value: value["annotation_completion"].update(negatives="none_confirmed")),
            ("negatives", lambda value: value["negatives"][0]["rects"][0].update(x1=41)),
            ("protected_neighbors", lambda value: value["protected_neighbors"][0]["rects"][0].update(x1=61)),
        )
        for section, mutate in mutations:
            with self.subTest(section=section):
                value = lock_manifest(manifest())
                locked_digest = value["manifest_sha256"]
                mutate(value)
                self.assertNotEqual(locked_digest, self._independent_manifest_digest(value))
                with self.assertRaises(ManifestValidationError) as raised:
                    validate_manifest(value, require_locked=True)
                self.assertEqual("manifest hash mismatch", str(raised.exception))
    def test_fold_assignment_is_deterministic_complete_and_locked(self):
        document_hashes = [f"{index:064x}" for index in range(25)]
        first = create_split_lock(document_hashes)
        second = create_split_lock(reversed(document_hashes))
        self.assertEqual(first, second)
        self.assertEqual(self._independent_split_digest(first), first["split_sha256"])
        self.assertEqual(5, first["fold_count"])
        assignments = first["assignments"]
        self.assertEqual(document_hashes, sorted(entry["document_sha256"] for entry in assignments))
        self.assertEqual([5, 5, 5, 5, 5], [
            sum(entry["fold"] == fold for entry in assignments) for fold in range(5)
        ])
        self.assertEqual(25, len({entry["document_sha256"] for entry in assignments}))
        before_validation = copy.deepcopy(first)
        validate_split_lock(first)
        self.assertEqual(before_validation, first)

    def test_split_lock_rejects_each_membership_defect_without_altering_baseline(self):
        baseline = create_split_lock([f"{index:064x}" for index in range(25)])
        cases = []
        dropped = copy.deepcopy(baseline)
        dropped["assignments"].pop()
        cases.append(("dropped", dropped))
        extra = copy.deepcopy(baseline)
        extra["assignments"].append({"document_sha256": "f" * 64, "fold": 0})
        cases.append(("extra", extra))
        duplicate = copy.deepcopy(baseline)
        duplicate["assignments"][-1]["document_sha256"] = duplicate["assignments"][0]["document_sha256"]
        cases.append(("duplicate", duplicate))
        unknown = copy.deepcopy(baseline)
        unknown["assignments"][0]["document_sha256"] = "f" * 64
        cases.append(("unknown", unknown))
        reassigned = copy.deepcopy(baseline)
        reassigned["assignments"].append(copy.deepcopy(reassigned["assignments"][0]))
        cases.append(("multiply-assigned", reassigned))
        wrong_fold = copy.deepcopy(baseline)
        wrong_fold["assignments"][0]["fold"] = (wrong_fold["assignments"][0]["fold"] + 1) % 5
        cases.append(("wrong-fold", wrong_fold))
        for name, tampered in cases:
            with self.subTest(defect=name):
                before_validation = copy.deepcopy(tampered)
                with self.assertRaisesRegex(ManifestValidationError, "assignments"):
                    validate_split_lock(tampered)
                self.assertEqual(before_validation, tampered)


if __name__ == "__main__":
    unittest.main()
