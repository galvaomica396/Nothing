import hashlib
import json
import unittest

from document_routing import (
    BoundaryCorrection,
    COMMON_DOCUMENT_HEADER,
    CONTINUATION_NO_START_SIGNAL,
    CONTINUATION_PAGE_NUMBER_SEQUENCE,
    PageEvidence,
    acknowledgment_is_current,
    apply_boundary_correction,
    carry_semantically_identical_decisions,
    ReviewDecision,
    normalize_profile,
    route_logical_documents,
)

def assert_no_canary(value: object, canary: str) -> None:
    if isinstance(value, str):
        assert canary not in value
    elif isinstance(value, dict):
        for key, nested in value.items():
            assert_no_canary(key, canary)
            assert_no_canary(nested, canary)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            assert_no_canary(nested, canary)



class DocumentRoutingTests(unittest.TestCase):
    def test_public_profiles_require_current_profile_authority_and_legal_is_compatible(self):
        pages = [PageEvidence(0), PageEvidence(1)]
        document_hash = "a" * 64

        def authority(profile, revision=1, bound_document_hash=document_hash, decision_code="profile_confirmed"):
            return {
                "document_sha256": bound_document_hash,
                "analysis_revision": revision,
                "profile": profile,
                "decision_code": decision_code,
            }

        for profile in ("internal_review", "official_dispatch"):
            result = route_logical_documents(
                profile,
                pages,
                document_hash=document_hash,
                profile_authority=authority(profile),
            )
            self.assertEqual([(profile, 0, 1)], [(s.kind, s.page_start, s.page_end) for s in result.segments])
            self.assertEqual("confirmed", result.segments[0].state)
            self.assertFalse(result.review_items)

        for name, invalid_authority in (
            ("missing", None),
            ("stale", authority("internal_review", decision_code="profile_superseded")),
            ("cross-document", authority("internal_review", bound_document_hash="b" * 64)),
            ("cross-revision", authority("internal_review", revision=2)),
            ("cross-profile", authority("official_dispatch")),
        ):
            with self.subTest(name=name):
                result = route_logical_documents(
                    "internal_review",
                    pages,
                    document_hash=document_hash,
                    profile_authority=invalid_authority,
                )
                self.assertEqual(("review_required", True), (result.segments[0].state, result.segments[0].common_only))
                self.assertEqual(("profile_authority_missing",), result.review_items[0].reason_codes)

        legal = route_logical_documents("legal", pages, document_hash=document_hash)
        self.assertEqual(("legal", "confirmed", False), (legal.segments[0].kind, legal.segments[0].state, legal.segments[0].common_only))
        self.assertFalse(legal.review_items)
        self.assertEqual("mixed", normalize_profile("official"))
        with self.assertRaises(ValueError):
            normalize_profile("unsupported-profile")


    def test_mixed_continues_confirmed_segment_when_page_number_sequence_has_no_start_signal(self):
        pages = [
            PageEvidence(0, frozenset({"dispatch"}), boundary_confidence=0.9),
            PageEvidence(1, continuity_signals=frozenset({
                CONTINUATION_PAGE_NUMBER_SEQUENCE,
                CONTINUATION_NO_START_SIGNAL,
            })),
        ]
        result = route_logical_documents("mixed", pages, document_hash="b" * 64)
        self.assertEqual(1, len(result.segments))
        self.assertEqual((0, 1), result.segments[0].page_range)
        self.assertEqual("official_dispatch", result.segments[0].kind)
        self.assertEqual(("confirmed", False), (result.segments[0].state, result.segments[0].common_only))

    def test_mixed_continues_confirmed_segment_when_running_title_matches(self):
        title = "서울특별시동작구입양가정지원에관한조례일부개정조례안검토보고"
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", "confirmed", (0, 1), False)],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )

    def test_mixed_repeated_same_running_title_continues_without_new_segment(self):
        title = "2026년도행정사무감사처리결과보고"
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_short_title_contained_in_unrelated_header_does_not_continue(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=("2026년도행정사무감사결과",),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    routing_titles=("2026년도행정사무감사결과및후속조치추진계획",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("internal_review", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_does_not_bridge_generic_running_title_alone(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=("검토보고서",),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    routing_titles=("검토보고서",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("internal_review", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_start_signal_page_ignores_different_routing_title_boundary(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=("위원회검토보고",),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    frozenset({"dispatch"}),
                    boundary_confidence=0.9,
                    routing_titles=("다른위원회검토보고",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("official_dispatch", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_splits_when_follow_on_running_title_differs(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=("서울특별시동작구입양가정지원에관한조례검토보고",),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    routing_titles=("첨부자료다른조례안검토보고",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("internal_review", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_different_running_title_wins_over_legacy_continuation_signal(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=("서울특별시동작구입양가정지원에관한조례검토보고",),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    continuity_signals=frozenset({
                        CONTINUATION_NO_START_SIGNAL,
                        CONTINUATION_PAGE_NUMBER_SEQUENCE,
                    }),
                    routing_titles=("서울특별시강남구다른조례검토보고",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [(0, 0), (1, 1)],
            [segment.page_range for segment in result.segments],
        )

    def test_mixed_bridges_one_titleless_gap_between_matching_running_titles(self):
        title = "서울특별시동작구입양가정지원에관한조례일부개정조례안검토보고"
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(1),
                PageEvidence(
                    2,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", "confirmed", (0, 2), False)],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )

    def test_mixed_bridges_one_titleless_gap_to_legacy_continuation_evidence(self):
        title = "서울특별시동작구입양가정지원에관한조례일부개정조례안검토보고"
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(1),
                PageEvidence(
                    2,
                    continuity_signals=frozenset({
                        CONTINUATION_NO_START_SIGNAL,
                        CONTINUATION_PAGE_NUMBER_SEQUENCE,
                    }),
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", "confirmed", (0, 2), False)],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )

    def test_mixed_bridges_multiple_titleless_gaps_to_title_specific_running_title(self):
        title = "서울특별시동작구입양가정지원에관한조례일부개정조례안검토보고"
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(1),
                PageEvidence(2),
                PageEvidence(
                    3,
                    routing_titles=("서울특별시동작구입양가정지원에관한조례일부개정조례안",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", "confirmed", (0, 3), False)],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )

    def test_mixed_rejects_multi_gap_bridge_with_contrary_start_signal(self):
        title = "서울특별시동작구입양가정지원에관한조례일부개정조례안검토보고"
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(1),
                PageEvidence(2, frozenset({"dispatch"}), boundary_confidence=0.9),
                PageEvidence(
                    3,
                    routing_titles=("서울특별시동작구입양가정지원에관한조례일부개정조례안",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("unknown", (1, 1)), ("official_dispatch", (2, 2)), ("internal_review", (3, 3))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_attachment_with_own_title_still_starts_a_new_segment(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=("서울특별시동작구입양가정지원에관한조례검토보고",),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(
                    1,
                    frozenset({"attachment"}),
                    boundary_confidence=0.9,
                    routing_titles=("붙임자료",),
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("attachment", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_does_not_bridge_two_titleless_pages_before_running_title(self):
        title = "서울특별시동작구입양가정지원에관한조례일부개정조례안검토보고"
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=(title,),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(1),
                PageEvidence(2),
                PageEvidence(
                    3,
                    routing_titles=("서울특별시동작구입양가정지원에관한조례",),
                    routing_title_kind="internal_review",
                ),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("unknown", (1, 3))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_mixed_does_not_bridge_trailing_titleless_page(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(
                    0,
                    frozenset({"internal"}),
                    boundary_confidence=0.9,
                    routing_titles=("서울특별시동작구입양가정지원에관한조례검토보고",),
                    routing_title_kind="internal_review",
                ),
                PageEvidence(1),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("unknown", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_common_document_header_without_kind_discriminator_requires_review(self):
        result = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({COMMON_DOCUMENT_HEADER}), boundary_confidence=1.0)],
            document_hash="a" * 64,
        )

        self.assertEqual(
            [("unknown", "review_required", True)],
            [(segment.kind, segment.state, segment.common_only) for segment in result.segments],
        )
        self.assertEqual(
            ("ambiguous_boundary", "common_document_header_ambiguous"),
            result.review_items[0].reason_codes,
        )

    def test_mixed_does_not_extend_confirmed_segment_when_only_no_start_signal_is_present(self):
        pages = [
            PageEvidence(0, frozenset({"internal"}), boundary_confidence=0.9),
            PageEvidence(1, continuity_signals=frozenset({CONTINUATION_NO_START_SIGNAL})),
        ]

        result = route_logical_documents("mixed", pages, document_hash="b" * 64)

        self.assertEqual(
            [
                ("internal_review", "confirmed", (0, 0), False),
                ("unknown", "review_required", (1, 1), True),
            ],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )
        self.assertEqual(("ambiguous_boundary", "continuation_evidence_missing"), result.review_items[0].reason_codes)

    def test_mixed_does_not_extend_confirmed_segment_without_continuation_evidence(self):
        pages = [
            PageEvidence(0, frozenset({"internal"}), boundary_confidence=0.9),
            PageEvidence(1),
        ]

        result = route_logical_documents("mixed", pages, document_hash="b" * 64)

        self.assertEqual(
            [
                ("internal_review", "confirmed", (0, 0), False),
                ("unknown", "review_required", (1, 1), True),
            ],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )
        self.assertEqual(("ambiguous_boundary", "continuation_evidence_missing"), result.review_items[0].reason_codes)

    def test_mixed_keeps_consecutive_unknown_pages_in_one_auditable_common_only_segment(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(0, frozenset({"dispatch"}), boundary_confidence=0.9),
                PageEvidence(1),
                PageEvidence(2),
            ],
            document_hash="b" * 64,
        )

        self.assertEqual(
            [
                ("official_dispatch", "confirmed", (0, 0), False),
                ("unknown", "review_required", (1, 2), True),
            ],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )
        self.assertEqual(
            (("boundary", (1, 2), ("ambiguous_boundary", "continuation_evidence_missing")),),
            tuple((review.kind, (review.page_start, review.page_end), review.reason_codes) for review in result.review_items),
        )

    def test_mixed_conflicting_strong_start_signal_starts_new_segment(self):
        pages = [
            PageEvidence(0, frozenset({"dispatch"}), boundary_confidence=0.9),
            PageEvidence(1, frozenset({"internal"}), boundary_confidence=0.9),
        ]

        result = route_logical_documents("mixed", pages, document_hash="b" * 64)

        self.assertEqual(
            [("official_dispatch", (0, 0)), ("internal_review", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_high_confidence_mixed_transition_creates_confirmed_segments(self):
        pages = [
            PageEvidence(0, frozenset({"internal"}), boundary_confidence=0.9),
            PageEvidence(1, frozenset({"dispatch"}), boundary_confidence=0.9),
        ]

        result = route_logical_documents("mixed", pages, document_hash="c" * 64)

        self.assertEqual(
            [
                ("internal_review", "confirmed", (0, 0), False),
                ("official_dispatch", "confirmed", (1, 1), False),
            ],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )
        self.assertEqual((), result.review_items)

    def test_ambiguous_boundary_is_common_only_and_revision_scoped(self):
        pages = [
            PageEvidence(0, frozenset({"internal"})),
            PageEvidence(1, frozenset({"dispatch"}), frozenset({"footer"}), boundary_confidence=0.5),
        ]
        result = route_logical_documents("mixed", pages, document_hash="c" * 64, analysis_revision=4)
        self.assertEqual(
            [
                ("internal_review", "review_required", (0, 0), True),
                ("official_dispatch", "review_required", (1, 1), True),
            ],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )
        self.assertEqual(2, len(result.review_items))
        self.assertEqual(
            [
                ("boundary", (0, 0), ("ambiguous_boundary", "internal")),
                ("boundary", (1, 1), ("ambiguous_boundary", "dispatch")),
            ],
            [(review.kind, (review.page_start, review.page_end), review.reason_codes) for review in result.review_items],
        )
        self.assertEqual(
            (1, ("dispatch",), 0.5, True),
            (
                result.segments[1].boundary_evidence[0].page_index,
                result.segments[1].boundary_evidence[0].reason_codes,
                result.segments[1].boundary_evidence[0].confidence,
                result.segments[1].boundary_evidence[0].ambiguous,
            ),
        )
        review = result.review_items[1]
        self.assertTrue(review.requires_acknowledgment)
        self.assertTrue(acknowledgment_is_current(review, 4))
        self.assertFalse(acknowledgment_is_current(review, 5))
        self.assertNotIn("text", result.safe_dict())
    def test_unrecognized_and_conflicting_start_signals_require_common_only_review(self):
        unrecognized = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({"unrecognized"}))],
            document_hash="d" * 64,
        )
        conflicting = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({"internal", "dispatch"}))],
            document_hash="e" * 64,
        )
        for result, reason in (
            (unrecognized, "unrecognized_start_signals"),
            (conflicting, "conflicting_start_signals"),
        ):
            self.assertEqual(("unknown", "review_required", True), (
                result.segments[0].kind,
                result.segments[0].state,
                result.segments[0].common_only,
            ))
            self.assertIn(reason, result.review_items[0].reason_codes)

    def test_safe_projection_recursively_excludes_source_canaries(self):
        canary = "PII-CANARY-홍길동-010-1234-5678"
        result = route_logical_documents(
            "mixed",
            [PageEvidence(0, frozenset({canary}), frozenset({canary}))],
            document_hash="f" * 64,
        )

        payload = result.safe_dict()
        assert_no_canary(payload, canary)
        self.assertEqual("unknown", payload["segments"][0]["kind"])
        self.assertEqual(["ambiguous_boundary", "unrecognized_start_signals"], payload["reviews"][0]["reason_codes"][:2])

    def test_rejects_nonconsecutive_duplicate_negative_pages_and_invalid_revisions(self):
        for pages in (
            [PageEvidence(1)],
            [PageEvidence(0), PageEvidence(2)],
            [PageEvidence(0), PageEvidence(0)],
        ):
            with self.assertRaisesRegex(ValueError, "consecutive and 0-based"):
                route_logical_documents("mixed", pages, document_hash="a" * 64)
        with self.assertRaisesRegex(ValueError, "analysis_revision must be a positive integer"):
            route_logical_documents("mixed", [PageEvidence(0)], document_hash="a" * 64, analysis_revision=0)
        with self.assertRaisesRegex(ValueError, "unsupported routing profile"):
            route_logical_documents("unsupported-profile", [PageEvidence(0)], document_hash="a" * 64)

    def test_page_evidence_rejects_negative_pages_and_unsupported_schema_contracts(self):
        with self.assertRaisesRegex(ValueError, "0-based"):
            PageEvidence(-1)
        with self.assertRaisesRegex(ValueError, "unsupported page evidence schema"):
            PageEvidence(0, schema_version="page-evidence-v0")
        with self.assertRaisesRegex(ValueError, "unsupported coordinate space"):
            PageEvidence(0, coordinate_space="pixels")
    def test_semantic_decision_carry_only_returns_unchanged_outside_replacements(self):
        def decision(review_id, page_start, page_end, fingerprint, action="approve", revision=3):
            return ReviewDecision(
                review_id, page_start, page_end, fingerprint, "policy-v1", action, revision,
            )

        prior = (
            decision("outside", 0, 0, "same"),
            decision("changed", 4, 4, "before"),
            decision("affected", 1, 2, "same"),
            decision("ack", 5, 5, "same", "boundary_acknowledgment"),
        )
        current = (
            decision("outside", 0, 0, "same", revision=4),
            decision("changed", 4, 4, "after", revision=4),
            decision("affected", 1, 2, "same", revision=4),
            decision("ack", 5, 5, "same", "boundary_acknowledgment", revision=4),
        )

        carried = carry_semantically_identical_decisions(prior, current, (1, 2))

        self.assertEqual(("outside",), tuple(item.review_id for item in carried))
        self.assertEqual((4,), tuple(item.analysis_revision for item in carried))

    def test_boundary_correction_rebuilds_corrected_segment_and_invalidates_decisions(self):
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(0, frozenset({"internal"})),
                PageEvidence(1, frozenset({"internal"}), boundary_confidence=.9),
                PageEvidence(2, frozenset({"dispatch"}), boundary_confidence=.4),
            ],
            document_hash="f" * 64,
            analysis_revision=3,
        )
        self.assertTrue(result.review_items)
        stale_acknowledgments = tuple(
            review for review in result.review_items if review.requires_acknowledgment
        )
        self.assertTrue(stale_acknowledgments)
        prior_decisions = (
            ReviewDecision("affected", 1, 2, "same", "policy-v1", "approve", 3),
            ReviewDecision("unaffected", 0, 0, "same", "policy-v1", "approve", 3),
            ReviewDecision("changed-fingerprint", 0, 0, "before", "policy-v1", "approve", 3),
            ReviewDecision("stale-ack", 2, 2, "same", "policy-v1", "boundary_acknowledgment", 3),
        )
        current_decisions = (
            ReviewDecision("affected", 1, 2, "same", "policy-v1", "approve", 4),
            ReviewDecision("unaffected", 0, 0, "same", "policy-v1", "approve", 4),
            ReviewDecision("changed-fingerprint", 0, 0, "after", "policy-v1", "approve", 4),
            ReviewDecision("stale-ack", 2, 2, "same", "policy-v1", "boundary_acknowledgment", 4),
        )
        correction = BoundaryCorrection(1, 2, "official_dispatch")

        def correction_authority(
            *,
            bound_document_hash=result.document_hash,
            prior_analysis_revision=result.analysis_revision,
            profile=result.profile,
            decision_code="boundary_correction_confirmed",
        ):
            correction_sha256 = hashlib.sha256(json.dumps(
                {"page_start": correction.page_start, "page_end": correction.page_end, "kind": correction.kind},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()).hexdigest()
            return {
                "document_sha256": bound_document_hash,
                "prior_analysis_revision": prior_analysis_revision,
                "profile": profile,
                "decision_code": decision_code,
                "correction_sha256": correction_sha256,
            }
        original_state = result.safe_dict()
        alternate_correction = BoundaryCorrection(0, 0, "internal_review")
        replayed_hash = hashlib.sha256(json.dumps(
            {
                "page_start": alternate_correction.page_start,
                "page_end": alternate_correction.page_end,
                "kind": alternate_correction.kind,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()).hexdigest()
        forged_hash = "0" * 64

        for name, invalid_authority in (
            ("missing", None),
            ("stale", correction_authority(decision_code="boundary_correction_superseded")),
            ("cross-document", correction_authority(bound_document_hash="a" * 64)),
            ("cross-revision", correction_authority(prior_analysis_revision=2)),
            ("cross-profile", correction_authority(profile="official_dispatch")),
            ("replayed-correction-hash", dict(correction_authority(), correction_sha256=replayed_hash)),
            ("forged-correction-hash", dict(correction_authority(), correction_sha256=forged_hash)),
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, "correction authority is invalid or stale"):
                    apply_boundary_correction(
                        result,
                        correction,
                        correction_authority=invalid_authority,
                    )
                self.assertEqual(
                    original_state,
                    result.safe_dict(),
                    "Rejected authority must not alter the source routing result.",
                )

        update = apply_boundary_correction(
            result,
            correction,
            correction_authority=correction_authority(),
        )

        self.assertEqual(4, update.analysis_revision)
        self.assertFalse(update.carried_review_ids)
        carried = carry_semantically_identical_decisions(prior_decisions, current_decisions, (1, 2))
        self.assertEqual(("unaffected",), tuple(item.review_id for item in carried))
        self.assertEqual((4,), tuple(item.analysis_revision for item in carried))
        self.assertTrue(all(
            not acknowledgment_is_current(item, update.analysis_revision)
            for item in stale_acknowledgments
        ))
        stale_review_ids = {item.review_id for item in stale_acknowledgments}
        self.assertFalse(stale_review_ids & {item.review_id for item in update.routing_result.review_items})
        self.assertEqual(
            [("internal_review", "review_required", (0, 0)), ("official_dispatch", "user_confirmed", (1, 2))],
            [(segment.kind, segment.state, segment.page_range) for segment in update.routing_result.segments],
        )
        self.assertTrue(all(segment.analysis_revision == 4 for segment in update.routing_result.segments))

    def test_boundary_correction_kind_allowlist_matches_the_public_contract(self):
        for kind in ("internal_review", "official_dispatch", "attachment", "legal"):
            with self.subTest(kind=kind):
                self.assertEqual(kind, BoundaryCorrection(0, 0, kind).kind)
        for kind in ("mixed", "common", "unknown", "arbitrary"):
            with self.subTest(kind=kind):
                with self.assertRaisesRegex(ValueError, "unsupported correction segment kind"):
                    BoundaryCorrection(0, 0, kind)


if __name__ == "__main__":
    unittest.main()
