"""Tests for the opt-in MASK_TOOL_ALLOWED_DIRS path allowlist (G-1)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

import path_guard

REPO_ROOT = Path(__file__).resolve().parents[1]
MANUAL_SCRIPT = REPO_ROOT / "scripts" / "apply_manual_boxes.py"
ENGINE_ENTRY = REPO_ROOT / "scripts" / "masking_engine_entry.py"
PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "run_masking_pipeline.py"


def write_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((32, 52), "name Hong Gil Dong")
    doc.save(path)
    doc.close()


def run(script_args: list[str], env_allowed: str | None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("MASK_TOOL_ALLOWED_DIRS", None)
    if env_allowed is not None:
        env["MASK_TOOL_ALLOWED_DIRS"] = env_allowed
    return subprocess.run(
        [sys.executable, *script_args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
    )


class PathGuardUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._prior = os.environ.get("MASK_TOOL_ALLOWED_DIRS")

    def tearDown(self) -> None:
        if self._prior is None:
            os.environ.pop("MASK_TOOL_ALLOWED_DIRS", None)
        else:
            os.environ["MASK_TOOL_ALLOWED_DIRS"] = self._prior

    def test_unrestricted_when_unset_and_no_defaults(self) -> None:
        os.environ.pop("MASK_TOOL_ALLOWED_DIRS", None)
        self.assertIsNone(path_guard.resolve_allowed_roots())
        self.assertTrue(path_guard.is_path_allowed("/anywhere/at/all.pdf"))
        # require_allowed_path is a passthrough when unconfigured.
        self.assertEqual("/x/y.pdf", path_guard.require_allowed_path("/x/y.pdf"))

    def test_env_restricts_to_inside_paths(self) -> None:
        with tempfile.TemporaryDirectory() as allowed:
            os.environ["MASK_TOOL_ALLOWED_DIRS"] = allowed
            inside = str(Path(allowed) / "in.pdf")
            outside = str(Path(allowed).parent / "out.pdf")
            self.assertTrue(path_guard.is_path_allowed(inside))
            self.assertFalse(path_guard.is_path_allowed(outside))
            with self.assertRaisesRegex(PermissionError, "MASK_TOOL_ALLOWED_DIRS"):
                path_guard.require_allowed_path(outside, label="input")

    def test_default_roots_used_only_when_env_unset(self) -> None:
        os.environ.pop("MASK_TOOL_ALLOWED_DIRS", None)
        with tempfile.TemporaryDirectory() as root:
            inside = str(Path(root) / "a.pdf")
            self.assertTrue(path_guard.is_path_allowed(inside, default_roots=[root]))
            self.assertFalse(path_guard.is_path_allowed("/somewhere/else.pdf", default_roots=[root]))
        # An explicit env wins over supplied defaults.
        with tempfile.TemporaryDirectory() as env_dir, tempfile.TemporaryDirectory() as default_dir:
            os.environ["MASK_TOOL_ALLOWED_DIRS"] = env_dir
            self.assertFalse(
                path_guard.is_path_allowed(str(Path(default_dir) / "x.pdf"), default_roots=[default_dir])
            )
            self.assertTrue(path_guard.is_path_allowed(str(Path(env_dir) / "x.pdf"), default_roots=[default_dir]))


class PathGuardCliTests(unittest.TestCase):
    def test_manual_boxes_rejects_input_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as elsewhere:
            outside_pdf = Path(elsewhere) / "secret.pdf"
            write_pdf(outside_pdf)
            boxes = json.dumps([{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}])
            proc = run(
                [
                    str(MANUAL_SCRIPT),
                    "--input", str(outside_pdf),
                    "--original", str(outside_pdf),
                    "--outdir", allowed,
                    "--boxes", boxes,
                ],
                env_allowed=allowed,
            )
            self.assertNotEqual(0, proc.returncode)
            self.assertEqual("MANUAL_APPLY_FAILED", proc.stderr.strip())
            self.assertNotIn("secret.pdf", proc.stderr)
            self.assertEqual("", proc.stdout.strip())

    def test_manual_boxes_allows_input_inside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as allowed:
            inside_pdf = Path(allowed) / "doc.pdf"
            write_pdf(inside_pdf)
            boxes = json.dumps([{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}])
            proc = run(
                [
                    str(MANUAL_SCRIPT),
                    "--input", str(inside_pdf),
                    "--original", str(inside_pdf),
                    "--outdir", allowed,
                    "--boxes", boxes,
                ],
                env_allowed=allowed,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual("applied", payload["status"])

    def test_engine_entry_manual_boxes_rejects_output_dir_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as elsewhere:
            inside_pdf = Path(allowed) / "doc.pdf"
            write_pdf(inside_pdf)
            boxes = json.dumps([{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}])
            proc = run(
                [
                    str(ENGINE_ENTRY),
                    "--manual-boxes",
                    "--input", str(inside_pdf),
                    "--original", str(inside_pdf),
                    "--outdir", elsewhere,  # outside the allowlist
                    "--boxes", boxes,
                ],
                env_allowed=allowed,
            )
            self.assertNotEqual(0, proc.returncode)
            self.assertEqual("MASKING_ENGINE_FAILED", proc.stderr.strip())
            self.assertNotIn(str(elsewhere), proc.stderr)


if __name__ == "__main__":
    unittest.main()
