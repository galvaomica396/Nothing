import subprocess
import sys
import unittest
from pathlib import Path

from scripts import check_contract_boundaries


REPO_ROOT = Path(__file__).resolve().parents[1]


class ContractCodegenTests(unittest.TestCase):
    def test_generated_contract_artifacts_are_current(self) -> None:
        result = subprocess.run(
            ["python3", "scripts/generate_contracts.py", "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

        rust = (REPO_ROOT / "src-tauri/src/contracts_generated.rs").read_text(encoding="utf-8")
        self.assertIn("pub schema_version: u64,", rust)

    def test_rust_review_resolution_preserves_json_discriminator(self) -> None:
        rust = (REPO_ROOT / "src-tauri/src/contracts_generated.rs").read_text(encoding="utf-8")

        self.assertIn(
            '#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]',
            rust,
        )
        self.assertIn("Institution {", rust)
        self.assertNotIn("#[serde(untagged)]\npub enum ReviewResolution", rust)

    def test_generated_contracts_respect_dto_boundaries(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/check_contract_boundaries.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def test_boundary_lint_rejects_generated_runtime_import(self) -> None:
        violations = check_contract_boundaries.boundary_violations(
            typescript='import type { Session } from "../../state/maskingSession";\n',
            rust="use serde::{Deserialize, Serialize};\n",
            security_sources={},
        )

        self.assertTrue(violations)

    def test_boundary_lint_rejects_security_owned_generated_module(self) -> None:
        violations = check_contract_boundaries.boundary_violations(
            typescript="export type Dto = string;\n",
            rust="use serde::{Deserialize, Serialize};\n",
            security_sources={"src-tauri/src/path_security.rs": "mod contracts_generated;\n"},
        )

        self.assertTrue(violations)

    def test_codegen_dependency_is_installed_by_ci(self) -> None:
        requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

        self.assertTrue(any(line.startswith("pydantic>=2") for line in requirements))
