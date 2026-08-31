import copy
import unittest

from masking_evaluation import ProtocolValidationError, one_to_one_match


class ThresholdCalibrationContractTests(unittest.TestCase):
    @staticmethod
    def _item(identifier, x0, x1):
        return {
            "id": identifier, "page_index": 0, "category": "person_name",
            "rects": [{"x0": x0, "y0": 0, "x1": x1, "y1": 10}],
        }

    def test_threshold_cutoff_is_inclusive_and_discriminates_below_and_above(self):
        gold = [self._item("gold", 0, 10)]
        half_overlap = [self._item("candidate", 0, 5)]
        before_gold, before_candidates = copy.deepcopy(gold), copy.deepcopy(half_overlap)
        self.assertEqual([], one_to_one_match(gold, half_overlap, threshold=.51))
        self.assertEqual([(0, 0)], one_to_one_match(gold, half_overlap, threshold=.5))
        self.assertEqual([(0, 0)], one_to_one_match(gold, half_overlap, threshold=.49))
        self.assertEqual(before_gold, gold)
        self.assertEqual(before_candidates, half_overlap)

    def test_tie_selection_is_by_candidate_identity_not_input_order(self):
        gold = [self._item("gold", 0, 10)]
        candidates = [self._item("z", 0, 10), self._item("a", 0, 10)]
        reversed_candidates = list(reversed(candidates))
        first = one_to_one_match(gold, candidates, threshold=.5)
        second = one_to_one_match(gold, reversed_candidates, threshold=.5)
        self.assertEqual("a", candidates[first[0][1]]["id"])
        self.assertEqual("a", reversed_candidates[second[0][1]]["id"])

    def test_non_numeric_threshold_has_metric_type_error_not_protocol_error(self):
        gold = [self._item("gold", 0, 10)]
        with self.assertRaises(TypeError) as raised:
            one_to_one_match(gold, [self._item("candidate", 0, 5)], threshold="invalid")
        self.assertNotIsInstance(raised.exception, ProtocolValidationError)
    def test_nonfinite_boolean_and_out_of_range_thresholds_are_rejected(self):
        gold = [self._item("gold", 0, 10)]
        candidate = [self._item("candidate", 0, 5)]
        for name, threshold in (
            ("true", True), ("false", False), ("nan", float("nan")),
            ("positive-infinity", float("inf")), ("negative-infinity", float("-inf")),
            ("below-zero", -0.01), ("above-one", 1.01),
        ):
            with self.subTest(threshold=name):
                before_gold, before_candidate = copy.deepcopy(gold), copy.deepcopy(candidate)
                with self.assertRaises((TypeError, ValueError)):
                    one_to_one_match(gold, candidate, threshold=threshold)
                self.assertEqual(before_gold, gold)
                self.assertEqual(before_candidate, candidate)

    def test_no_candidate_and_cross_page_candidates_do_not_match(self):
        gold = [self._item("gold", 0, 1)]
        self.assertEqual(one_to_one_match(gold, [], threshold=.5), [])
        self.assertEqual(one_to_one_match(gold, [
            {**self._item("other", 0, 1), "page_index": 1}
        ], threshold=.5), [])


if __name__ == "__main__":
    unittest.main()
