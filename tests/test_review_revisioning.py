import hashlib
import json
import unittest

from document_routing import BoundaryCorrection, PageEvidence, apply_boundary_correction, route_logical_documents


def correction_authority(result, correction):
    fingerprint = hashlib.sha256(
        json.dumps(
            {"page_start": correction.page_start, "page_end": correction.page_end, "kind": correction.kind},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "document_sha256": result.document_hash,
        "prior_analysis_revision": result.analysis_revision,
        "profile": result.profile,
        "decision_code": "boundary_correction_confirmed",
        "correction_sha256": fingerprint,
    }

class ReviewRevisioningTests(unittest.TestCase):
    def test_boundary_correction_creates_next_revision_and_precise_range(self):
        result = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({"internal"})),
             PageEvidence(1, frozenset({"dispatch"}), boundary_confidence=.4),
             PageEvidence(2)],
            document_hash="a" * 64,
            analysis_revision=8,
        )
        original_segments = tuple(
            (segment.page_start, segment.page_end, segment.kind, segment.state)
            for segment in result.segments
        )
        original_reviews = tuple(item.safe_dict() for item in result.review_items)
        correction = BoundaryCorrection(1, 1, "official_dispatch")
        update = apply_boundary_correction(result, correction, correction_authority=correction_authority(result, correction))
        self.assertEqual(9, update.analysis_revision)
        self.assertEqual((1, 1), update.affected_page_range)
        self.assertFalse(update.carried_review_ids)  # acknowledgement is always revision scoped
        corrected_segments = {
            page: (segment.kind, segment.state)
            for segment in update.routing_result.segments
            for page in range(segment.page_start, segment.page_end + 1)
        }
        self.assertEqual(("official_dispatch", "user_confirmed"), corrected_segments[1])
        self.assertEqual("internal_review", corrected_segments[0][0])
        self.assertEqual("official_dispatch", corrected_segments[2][0])
        self.assertEqual(
            original_segments,
            tuple((segment.page_start, segment.page_end, segment.kind, segment.state) for segment in result.segments),
        )
        self.assertEqual(original_reviews, tuple(item.safe_dict() for item in result.review_items))
    def test_correction_authority_is_bound_to_exact_correction_and_revision(self):
        result = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({"internal"})),
             PageEvidence(1, frozenset({"dispatch"}), boundary_confidence=.4),
             PageEvidence(2)],
            document_hash="d" * 64,
            analysis_revision=3,
        )
        correction = BoundaryCorrection(1, 1, "official_dispatch")
        other_correction = BoundaryCorrection(1, 1, "internal_review")
        with self.assertRaisesRegex(ValueError, "authority"):
            apply_boundary_correction(
                result,
                correction,
                correction_authority=correction_authority(result, other_correction),
            )
        authority = correction_authority(result, correction)
        authority["correction_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "authority"):
            apply_boundary_correction(result, correction, correction_authority=authority)
        update = apply_boundary_correction(
            result, correction, correction_authority=correction_authority(result, correction),
        )
        with self.assertRaisesRegex(ValueError, "authority"):
            apply_boundary_correction(
                update.routing_result,
                correction,
                correction_authority=correction_authority(result, correction),
            )

    def test_corrected_fragments_retain_only_range_local_boundary_evidence(self):
        result = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({"internal"})),
             PageEvidence(1, frozenset({"dispatch"}), boundary_confidence=.4),
             PageEvidence(2)],
            document_hash="e" * 64,
        )
        update = apply_boundary_correction(
            result,
            BoundaryCorrection(1, 1, "official_dispatch"),
            correction_authority=correction_authority(result, BoundaryCorrection(1, 1, "official_dispatch")),
        )
        right_fragment = next(
            segment for segment in update.routing_result.segments
            if segment.page_start == segment.page_end == 2
        )
        self.assertEqual((), right_fragment.boundary_evidence)
        for segment in update.routing_result.segments:
            self.assertTrue(
                all(segment.page_start <= item.page_index <= segment.page_end for item in segment.boundary_evidence)
            )
    def test_stale_acknowledgments_and_invalid_corrections_fail_without_mutating_source(self):
        result = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({"internal"})), PageEvidence(1, frozenset({"dispatch"}), boundary_confidence=.4)],
            document_hash="b" * 64,
        )
        review = result.review_items[0]
        original_state = result.safe_dict()
        correction = BoundaryCorrection(1, 1, "official_dispatch")
        update = apply_boundary_correction(result, correction, correction_authority=correction_authority(result, correction))
        self.assertEqual((), update.carried_review_ids)
        self.assertTrue(update.routing_result.review_items)
        self.assertTrue(
            all(item.analysis_revision == update.analysis_revision for item in update.routing_result.review_items),
            "The correction consumer must replace every prior revision review item.",
        )
        self.assertNotIn(
            review.review_id,
            {item.review_id for item in update.routing_result.review_items},
            "The final routing result must not carry a stale acknowledgement into the corrected revision.",
        )
        for page_start, page_end, document_hash, message in (
            (-1, 0, "b" * 64, "invalid inclusive page range"),
            (1, 0, "b" * 64, "invalid inclusive page range"),
            (2, 2, "b" * 64, "exceeds routed pages"),
            (1, 1, "c" * 64, "authority"),
        ):
            with self.subTest(page_start=page_start, page_end=page_end, document_hash=document_hash):
                with self.assertRaisesRegex(ValueError, message):
                    invalid_correction = BoundaryCorrection(page_start, page_end, "official_dispatch")
                    authority = correction_authority(result, invalid_correction)
                    authority["document_sha256"] = document_hash
                    apply_boundary_correction(result, invalid_correction, correction_authority=authority)
                self.assertEqual(original_state, result.safe_dict())
        alternate = BoundaryCorrection(0, 0, "internal_review")
        replayed_hash = hashlib.sha256(
            json.dumps(
                {
                    "page_start": alternate.page_start,
                    "page_end": alternate.page_end,
                    "kind": alternate.kind,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        for name, correction_hash in (
            ("replayed-correction-hash", replayed_hash),
            ("forged-correction-hash", "f" * 64),
        ):
            with self.subTest(name=name):
                authority = correction_authority(result, correction)
                authority["correction_sha256"] = correction_hash
                with self.assertRaisesRegex(ValueError, "correction authority is invalid or stale"):
                    apply_boundary_correction(result, correction, correction_authority=authority)
                self.assertEqual(
                    original_state,
                    result.safe_dict(),
                    "Rejected correction hashes must preserve the original routing result.",
                )


if __name__ == "__main__":
    unittest.main()
