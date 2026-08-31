from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from scripts.generate_t35_mixed_text_scan_fixture import assert_fixture, write_fixture


class T35MixedTextScanFixtureTests(unittest.TestCase):
    def test_fixture_is_self_checked_and_byte_deterministic(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            first = Path(directory) / "first.pdf"
            second = Path(directory) / "second.pdf"

            write_fixture(first)
            write_fixture(second)

            first_result = assert_fixture(first)
            second_result = assert_fixture(second)
            expected_hash = hashlib.sha256(first.read_bytes()).hexdigest()
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual({
                "fixture_path": str(first.resolve()),
                "pages": 3,
                "sha256": expected_hash,
            }, first_result)
            self.assertEqual(3, second_result["pages"])
            self.assertEqual(expected_hash, second_result["sha256"])
