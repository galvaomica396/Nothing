import json
import unittest
from pathlib import Path

from masking_evaluation import (
    evaluate_fixed_region_occurrences, evaluate_occurrences, evaluate_regions,
    geometry_faults, one_to_one_match, rect_coverage,
)


FIXTURES = Path(__file__).parent / "fixtures"


def item(identifier, x0=0, x1=10, *, action="mask", identity="shared"):
    return {"id": identifier, "page_index": 0, "category": "person_name", "action": action,
            "segment_id": f"segment-{identity}", "region_id": f"region-{identity}",
            "text_hash": f"text-{identity}",
            "rects": [{"x0": x0, "y0": 0, "x1": x1, "y1": 10}]}


class EvaluationMetricsTests(unittest.TestCase):
    def test_fixed_region_omission_counts_only_confirmed_or_user_confirmed_regions(self):
        fixture = json.loads((FIXTURES / "scoped-fixed-region-gold.json").read_text(encoding="utf-8"))

        result = evaluate_fixed_region_occurrences(
            fixture["occurrences"], [], fixture["regions"], fixture["segments"],
        )

        self.assertEqual({"tp": 0, "fp": 0, "fn": 1}, result["region_tp_fp_fn"])
        self.assertEqual(1, result["fixed_region_omission_count"])
        self.assertEqual(1, result["fixed_region_gold_pii_count"])
        self.assertEqual(1, result["unscoped_fixed_region_gold_pii_count"])
        self.assertEqual(1, result["unscoped_fixed_region_omission_count"])

    def test_fixed_region_alert_candidate_remains_an_omission(self):
        # Given: a gold PII occurrence in an automatically confirmed fixed region.
        fixture = json.loads((FIXTURES / "scoped-fixed-region-gold.json").read_text(encoding="utf-8"))
        candidate = dict(fixture["occurrences"][0], id="alert-only", action="alert")

        # When: the pipeline detected the value but did not mask it.
        result = evaluate_fixed_region_occurrences(
            fixture["occurrences"], [candidate], fixture["regions"], fixture["segments"],
        )

        # Then: the confirmed-region gold value is still a scoped omission.
        self.assertEqual({"tp": 0, "fp": 0, "fn": 1}, result["region_tp_fp_fn"])
        self.assertEqual(1, result["fixed_region_omission_count"])

    def test_user_confirmed_fixed_region_is_in_scope(self):
        # Given: a fixed region explicitly confirmed by the user.
        fixture = json.loads((FIXTURES / "scoped-fixed-region-gold.json").read_text(encoding="utf-8"))
        fixture["regions"][0].update(state="confirmed", confirmation_source="user")

        # When: its gold PII occurrence is not masked.
        result = evaluate_fixed_region_occurrences(
            fixture["occurrences"], [], fixture["regions"], fixture["segments"],
        )

        # Then: the omission is included in the zero-omission scope.
        self.assertEqual(1, result["fixed_region_omission_count"])

    def test_user_confirmed_review_required_fixed_region_is_in_scope(self):
        # Given: a user-confirmed fixed region whose state still requires review.
        fixture = json.loads((FIXTURES / "user-review-required-fixed-region-gold.json").read_text(encoding="utf-8"))

        # When: its gold PII occurrence is not masked.
        result = evaluate_fixed_region_occurrences(
            fixture["occurrences"], [], fixture["regions"], fixture["segments"],
        )

        # Then: user confirmation wins and its omission counts toward the zero target.
        self.assertEqual(1, result["fixed_region_omission_count"])

    def test_unlinked_mask_candidate_is_counted_and_remains_an_omission(self):
        # Given: a correctly masked fixed-region value with dropped region linkage.
        fixture = json.loads((FIXTURES / "scoped-fixed-region-gold.json").read_text(encoding="utf-8"))
        candidate = dict(fixture["occurrences"][0], id="unlinked-mask")
        candidate.pop("region_id")

        # When: fixed-region metrics are evaluated.
        result = evaluate_fixed_region_occurrences(
            fixture["occurrences"], [candidate], fixture["regions"], fixture["segments"],
        )

        # Then: the mask remains conservatively excluded but the linkage loss is observable.
        self.assertEqual(1, result["fixed_region_omission_count"])
        self.assertEqual(1, result["unlinked_mask_candidate_count"])

    def test_occurrence_false_positive_rate_uses_non_pii_lookalike_denominator(self):
        negatives = [
            {"id": "lookalike", "page_index": 0, "category": "person_name", "kind": "name",
             "rects": item("x", 20, 30)["rects"]},
        ]

        result = evaluate_occurrences([], [item("false-positive", 20, 30)], negatives)

        self.assertEqual(
            {"numerator": 1, "denominator": 1, "status": "ok", "value": 1.0},
            result["false_positive_rate"],
        )

    def test_occurrence_metrics_are_one_to_one_and_count_complete(self):
        gold = [item("g1"), item("g2", 20, 30)]
        candidates = [
            item("matched", 0, 10),
            item("automatic-name-fp", 40, 50),
            item("alert-name-fp", 60, 70, action="alert"),
            item("category-distractor", 80, 90, action="alert") | {"category": "address"},
        ]
        negatives = [
            {"id": "automatic-name", "page_index": 0, "category": "person_name", "kind": "name",
             "rects": item("x", 40, 50)["rects"]},
            {"id": "alert-name", "page_index": 0, "category": "person_name", "kind": "name",
             "rects": item("x", 60, 70)["rects"]},
            {"id": "non-name-distractor", "page_index": 0, "category": "person_name", "kind": "address",
             "rects": item("x", 80, 90)["rects"]},
            {"id": "other-kind-distractor", "page_index": 0, "category": "address", "kind": "address",
             "rects": item("x", 80, 90)["rects"]},
        ]
        result = evaluate_occurrences(gold, candidates, negatives)
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (1, 3, 1))
        self.assertEqual(
            result["recall"],
            {"numerator": 1, "denominator": 2, "status": "ok", "value": 0.5},
        )
        self.assertEqual(
            result["name_auto_false_positive_rate"],
            {"numerator": 1, "denominator": 2, "status": "ok", "value": 0.5},
        )
        self.assertEqual(
            result["name_false_alert_rate"],
            {"numerator": 2, "denominator": 2, "status": "ok", "value": 1.0},
        )

    def test_zero_denominators_are_explicitly_not_applicable_for_every_ratio(self):
        result = evaluate_occurrences([], [])
        for metric_name in ("recall", "name_auto_false_positive_rate", "name_false_alert_rate"):
            with self.subTest(metric=metric_name):
                self.assertEqual(
                    result[metric_name],
                    {"numerator": 0, "denominator": 0, "status": "not_applicable", "value": None},
                )

    def test_name_auto_false_positive_rate_ignores_disjoint_and_boundary_only_geometry(self):
        negatives = [
            {"id": "disjoint", "page_index": 0, "category": "person_name", "kind": "name",
             "rects": item("x", 0, 10)["rects"]},
            {"id": "boundary", "page_index": 0, "category": "person_name", "kind": "name",
             "rects": item("x", 20, 30)["rects"]},
        ]
        candidates = [item("between", 10, 20), item("far-away", 40, 50, action="alert")]
        result = evaluate_occurrences([], candidates, negatives)
        self.assertEqual(
            result["name_auto_false_positive_rate"],
            {"numerator": 0, "denominator": 2, "status": "ok", "value": 0.0},
        )
        self.assertEqual(
            result["name_false_alert_rate"],
            {"numerator": 0, "denominator": 2, "status": "ok", "value": 0.0},
        )

    def test_multiline_geometry_requires_each_rect_and_protects_neighbors(self):
        expected = [
            {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
            {"x0": 0, "y0": 20, "x1": 10, "y1": 30},
        ]
        self.assertLess(rect_coverage(expected[1], [expected[0]]), 1.0)
        self.assertEqual(["incomplete_coverage"], geometry_faults(expected, [expected[0]]))
        self.assertEqual(
            ["protected_neighbor_intrusion"],
            geometry_faults([expected[0]], [expected[0]], [expected[0]]),
        )
    def test_missing_geometry_is_not_a_successful_match(self):
        candidate = item("c1")
        candidate.pop("rects")
        result = evaluate_occurrences([item("g1")], [candidate])
        self.assertEqual((result["tp"], result["fp"], result["fn"]), (0, 1, 1))
        self.assertEqual(geometry_faults([], []), ["missing_geometry"])

    def test_matching_uses_an_asymmetric_augmenting_path_for_maximum_cardinality(self):
        # g1 can use c1/c2; g2 can only use c1.  Quality ordering greedily selects
        # g1->c1 first, so the second match requires reassignment through c2.
        gold = [item("g1", 0, 10), item("g2", 6, 16)]
        candidates = [item("c1", 5, 15), item("c2", 0, 5)]
        self.assertEqual(one_to_one_match(gold, candidates), [(0, 1), (1, 0)])
    def test_occurrence_matching_uses_iou_or_gold_coverage_but_region_matching_requires_both(self):
        gold = [item("gold")]

        occurrence_iou_only = evaluate_occurrences(gold, [item("iou-only", 0, 5)])
        occurrence_coverage_only = evaluate_occurrences(gold, [item("coverage-only", 2, 20)])
        self.assertEqual((occurrence_iou_only["tp"], occurrence_iou_only["fp"], occurrence_iou_only["fn"]), (1, 0, 0))
        self.assertEqual((occurrence_coverage_only["tp"], occurrence_coverage_only["fp"], occurrence_coverage_only["fn"]), (1, 0, 0))

        region_gold = [item("gold") | {"type": "body"}]
        region_iou_only = [item("iou-only", 0, 5) | {"type": "body"}]
        region_coverage_only = [item("coverage-only", 2, 20) | {"type": "body"}]
        exact_both = [item("both", 0, 10) | {"type": "body"}]
        self.assertEqual(evaluate_regions(region_gold, region_iou_only), {"tp": 0, "fp": 1, "fn": 1})
        self.assertEqual(evaluate_regions(region_gold, region_coverage_only), {"tp": 0, "fp": 1, "fn": 1})
        self.assertEqual(evaluate_regions(region_gold, exact_both), {"tp": 1, "fp": 0, "fn": 0})

    def test_page_and_category_mismatches_do_not_cross_match_at_exact_geometry_boundary(self):
        gold = [item("gold")]
        wrong_page = item("wrong-page", 0, 20) | {"page_index": 1}
        wrong_category = item("wrong-category", 0, 20) | {"category": "address"}

        result = evaluate_occurrences(gold, [wrong_page, wrong_category])

        self.assertEqual((result["tp"], result["fp"], result["fn"]), (0, 2, 1))


if __name__ == "__main__":
    unittest.main()
