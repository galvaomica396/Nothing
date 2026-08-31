import unittest

from masking_evaluation import geometry_faults


class RedactionGoldValidationTests(unittest.TestCase):
    def test_missing_geometry_is_not_proven(self):
        expected = [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}]
        applied = [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}]
        self.assertEqual(["missing_geometry"], geometry_faults([], []))
        self.assertEqual(["missing_geometry"], geometry_faults(expected, []))
        self.assertEqual(["missing_geometry"], geometry_faults([], applied))

    def test_multi_rectangle_gold_requires_each_rectangle_and_is_order_independent(self):
        expected = [
            {"x0": 10, "y0": 10, "x1": 20, "y1": 20},
            {"x0": 30, "y0": 30, "x1": 40, "y1": 40},
        ]
        self.assertEqual(
            [],
            geometry_faults(expected, list(reversed(expected)), epsilon=0.5),
        )
        self.assertEqual(
            ["incomplete_coverage"],
            geometry_faults(expected, [expected[0]], epsilon=0.5),
        )
        non_first_within_epsilon = [expected[0], dict(expected[1], x0=30.5)]
        self.assertEqual(
            [],
            geometry_faults(expected, non_first_within_epsilon, epsilon=0.5),
        )
        non_first_outside_epsilon = [expected[0], dict(expected[1], x0=30.5001)]
        self.assertEqual(
            ["incomplete_coverage"],
            geometry_faults(expected, non_first_outside_epsilon, epsilon=0.5),
        )

    def test_protected_neighbor_partial_overlap_is_detected_for_each_neighbor(self):
        expected = [{"x0": 10, "y0": 10, "x1": 20, "y1": 20}]
        protected = [
            {"x0": 30, "y0": 30, "x1": 40, "y1": 40},
            {"x0": 50, "y0": 50, "x1": 60, "y1": 60},
        ]
        self.assertEqual([], geometry_faults(expected, expected, protected))
        boundary_touching = [*expected, {"x0": 20, "y0": 30, "x1": 30, "y1": 40}]
        self.assertEqual([], geometry_faults(expected, boundary_touching, protected))
        first_neighbor_overlap = [*expected, {"x0": 29, "y0": 32, "x1": 35, "y1": 38}]
        self.assertEqual(
            ["protected_neighbor_intrusion"],
            geometry_faults(expected, first_neighbor_overlap, protected),
        )
        second_neighbor_overlap = [*expected, {"x0": 49, "y0": 52, "x1": 55, "y1": 58}]
        self.assertEqual(
            ["protected_neighbor_intrusion"],
            geometry_faults(expected, second_neighbor_overlap, protected),
        )


if __name__ == "__main__":
    unittest.main()
