from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROOF_SCRIPT = REPO_ROOT / "scripts" / "qa_package_freshness.mjs"


class PackageFreshnessTests(unittest.TestCase):
    def test_package_freshness_proof_covers_fresh_and_failure_cases(self) -> None:
        # Given: the repository resource map and a synthetic macOS bundle fixture.
        # When: the package-freshness proof exercises the verification CLI.
        completed = subprocess.run(
            ["node", str(PROOF_SCRIPT)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )

        # Then: fresh packages pass while stale, missing, and incomplete sidecars fail.
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("fresh bundle: PASS", completed.stdout)
        self.assertIn("stale file hash: FAIL", completed.stdout)
        self.assertIn("missing sidecar: FAIL", completed.stdout)
        self.assertIn("entry-count mismatch: FAIL", completed.stdout)
        self.assertIn("repackaged stale engine: FAIL", completed.stdout)


if __name__ == "__main__":
    unittest.main()
