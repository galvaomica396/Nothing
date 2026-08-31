from __future__ import annotations

import unittest

from scripts.evaluate_routing_corpus import dominant_segment_kind
from scripts.real_corpus import load_real_corpus_manifest


class RoutingCorpusEvaluationTests(unittest.TestCase):
    def test_hash_manifest_contains_fifteen_categorized_documents(self):
        documents = load_real_corpus_manifest()
        self.assertEqual(15, len(documents))
        self.assertEqual(15, len({document["sha256"] for document in documents}))
        self.assertTrue(all(document["category"] in {"internal_review", "official_dispatch"} for document in documents))
        self.assertEqual(
            "internal_review",
            next(document["category"] for document in documents if document["alias"] == "doc-14"),
        )

    def test_dominant_segment_kind_counts_covered_pages_and_rejects_ties(self):
        self.assertEqual(
            "internal_review",
            dominant_segment_kind([
                {"kind": "official_dispatch", "page_start": 0, "page_end": 0},
                {"kind": "internal_review", "page_start": 1, "page_end": 3},
            ]),
        )
        self.assertIsNone(dominant_segment_kind([
            {"kind": "official_dispatch", "page_start": 0, "page_end": 0},
            {"kind": "internal_review", "page_start": 1, "page_end": 1},
        ]))
