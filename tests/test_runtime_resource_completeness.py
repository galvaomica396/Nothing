from __future__ import annotations

import json
import subprocess
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
def runtime_resource_completeness() -> list[str]:
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import { checkRuntimeResourceCompleteness } from './scripts/runtime_resource_completeness.mjs'; console.log(JSON.stringify(checkRuntimeResourceCompleteness(process.cwd())));",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


class RuntimeResourceCompletenessTests(unittest.TestCase):
    def test_tauri_resources_cover_the_masking_runtime_import_and_data_closure(self) -> None:
        # Given: the shared resource-map enumerator used by the runtime package gates.
        missing = runtime_resource_completeness()

        # When: the T13 completeness gate evaluates the runtime import and data closure.
        # Then: package builds cannot omit an import or region data dependency.
        self.assertEqual([], missing, f"Tauri bundle resources are missing: {', '.join(missing)}")


if __name__ == "__main__":
    unittest.main()
