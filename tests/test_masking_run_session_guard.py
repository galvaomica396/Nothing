from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MASKING_RUN_CONTROLLER = REPO_ROOT / "src" / "features" / "masking-run" / "maskingRunController.ts"


class MaskingRunSessionGuardTests(unittest.TestCase):
    def test_session_owner_is_captured_after_derived_reset(self) -> None:
        source = MASKING_RUN_CONTROLLER.read_text(encoding="utf-8")

        reset_index = source.index("deps.resetDerivedArtifacts();")
        capture_index = source.index("let runProvenance = state.documentProvenance;")
        invoke_index = source.index("const result = await deps.runMaskingPipeline({")
        guard_index = source.index("if (!sessionIsCurrent()) return null;")

        self.assertLess(reset_index, capture_index)
        self.assertLess(capture_index, invoke_index)
        self.assertLess(invoke_index, guard_index)
        self.assertIn(
            "const sessionIsCurrent = () => state.documentProvenance === runProvenance;",
            source,
        )
        self.assertEqual(
            2,
            sum(
                line.strip() == "runProvenance = state.documentProvenance;"
                for line in source.splitlines()
            ),
        )


if __name__ == "__main__":
    unittest.main()
