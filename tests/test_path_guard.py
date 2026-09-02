"""Tests for the opt-in MASK_TOOL_ALLOWED_DIRS path allowlist (G-1)."""

from __future__ import annotations

import json
import hashlib
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
def directory_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[relative] = f"symlink:{path.readlink()}"
        elif path.is_dir():
            snapshot[relative] = "directory"
        else:
            snapshot[relative] = f"file:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    return snapshot


def source_snapshot() -> dict[str, str]:
    return {
        str(path.relative_to(REPO_ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (MANUAL_SCRIPT, ENGINE_ENTRY, PIPELINE_SCRIPT)
    }




def run(
    script_args: list[str],
    env_allowed: str | None,
    *,
    debug_trace: bool = False,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("MASK_TOOL_ALLOWED_DIRS", None)
    env.pop("MASK_TOOL_DEBUG_TRACE", None)
    if env_allowed is not None:
        env["MASK_TOOL_ALLOWED_DIRS"] = env_allowed
    if debug_trace:
        env["MASK_TOOL_DEBUG_TRACE"] = "1"
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

    def test_unset_allowlist_fails_closed(self) -> None:
        os.environ.pop("MASK_TOOL_ALLOWED_DIRS", None)

        self.assertEqual([], path_guard.resolve_allowed_roots())
        self.assertFalse(path_guard.is_path_allowed("/anywhere/at/all.pdf"))
        with self.assertRaisesRegex(PermissionError, "MASK_TOOL_ALLOWED_DIRS"):
            path_guard.require_allowed_path("/x/y.pdf")

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

    @unittest.skipUnless(os.name == "nt", "Windows path spelling coverage is unavailable on this platform")
    def test_verbatim_and_normal_windows_paths_compare_equal(self) -> None:
        self.assertTrue(
            path_guard.same_path(
                r"\\?\C:\Allowed\Document.pdf",
                r"c:\allowed\document.pdf",
            )
        )
        self.assertTrue(
            path_guard.same_path(
                r"\\?\UNC\server\share\Document.pdf",
                r"\\server\share\document.pdf",
            )
        )

    @unittest.skipUnless(os.name != "nt", "POSIX symlink coverage is unavailable on Windows")
    def test_ancestor_symlink_is_allowed_when_resolved_path_stays_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as allowed:
            root = Path(allowed)
            os.environ["MASK_TOOL_ALLOWED_DIRS"] = allowed
            physical = root / "physical"
            physical.mkdir()
            alias = root / "alias"
            alias.symlink_to(physical, target_is_directory=True)
            supplied = alias / "output.pdf"

            resolved = path_guard.require_allowed_path(supplied, label="output")

            self.assertEqual((physical / "output.pdf").resolve(), resolved)
            self.assertTrue(path_guard.is_path_allowed(supplied))

    @unittest.skipUnless(os.name != "nt", "POSIX symlink coverage is unavailable on Windows")
    def test_final_symlink_is_rejected_even_when_target_stays_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as allowed:
            root = Path(allowed)
            os.environ["MASK_TOOL_ALLOWED_DIRS"] = allowed
            target = root / "physical.pdf"
            target.touch()
            alias = root / "alias.pdf"
            alias.symlink_to(target)

            self.assertFalse(path_guard.is_path_allowed(alias))
            with self.assertRaises(PermissionError):
                path_guard.require_allowed_path(alias, label="input")


class PathGuardCliTests(unittest.TestCase):
    def test_all_entrypoints_reject_every_protected_path_role_without_artifacts(self) -> None:
        boxes = json.dumps([{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}])
        entrypoints = {
            "manual": lambda input_pdf, original_pdf, outdir: [
                str(MANUAL_SCRIPT), "--input", str(input_pdf), "--original", str(original_pdf),
                "--outdir", str(outdir), "--boxes", boxes,
            ],
            "engine": lambda input_pdf, original_pdf, outdir: [
                str(ENGINE_ENTRY), "--manual-boxes", "--input", str(input_pdf), "--original", str(original_pdf),
                "--outdir", str(outdir), "--boxes", boxes,
            ],
            "pipeline": lambda input_pdf, original_pdf, outdir: [
                str(PIPELINE_SCRIPT), "--repo-root", str(REPO_ROOT), "--mode", "finalize",
                "--input", str(input_pdf), "--original", str(original_pdf), "--outdir", str(outdir),
                "--opts", "{}",
            ],
        }
        for entrypoint, command in entrypoints.items():
            for role in ("input", "original", "outdir"):
                with self.subTest(entrypoint=entrypoint, role=role):
                    with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as elsewhere:
                        allowed_root = Path(allowed)
                        outside_root = Path(elsewhere)
                        inside_pdf = allowed_root / "inside.pdf"
                        outside_pdf = outside_root / "secret.pdf"
                        write_pdf(inside_pdf)
                        write_pdf(outside_pdf)
                        before_allowed = directory_snapshot(allowed_root)
                        before_outside = directory_snapshot(outside_root)
                        before_sources = source_snapshot()
                        paths = {"input": inside_pdf, "original": inside_pdf, "outdir": allowed_root}
                        paths[role] = outside_pdf if role != "outdir" else outside_root

                        proc = run(command(paths["input"], paths["original"], paths["outdir"]), env_allowed=allowed)
                        self.assertNotEqual(0, proc.returncode)
                        self.assertEqual("", proc.stdout.strip())
                        self.assertNotIn(str(outside_root), proc.stderr)
                        self.assertNotIn("secret.pdf", proc.stderr)
                        self.assertEqual(before_allowed, directory_snapshot(allowed_root))
                        self.assertEqual(before_outside, directory_snapshot(outside_root))
                        self.assertEqual(before_sources, source_snapshot())

                        if entrypoint == "manual":
                            self.assertEqual("MANUAL_APPLY_FAILED", proc.stderr.strip())
                        elif entrypoint == "engine":
                            self.assertEqual(
                                "PATH_ACCESS_REJECTED",
                                json.loads(proc.stderr)["error"]["code"],
                            )
                        else:
                            self.assertEqual(
                                "MASKING_PIPELINE_PATH_SYMLINK_REJECTED",
                                json.loads(proc.stderr)["error"]["code"],
                            )

    def test_all_entrypoints_reject_symlink_escape_for_each_protected_role_without_artifacts(self) -> None:
        boxes = json.dumps([{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}])
        entrypoints = {
            "manual": lambda input_pdf, original_pdf, outdir: [
                str(MANUAL_SCRIPT), "--input", str(input_pdf), "--original", str(original_pdf),
                "--outdir", str(outdir), "--boxes", boxes,
            ],
            "engine": lambda input_pdf, original_pdf, outdir: [
                str(ENGINE_ENTRY), "--manual-boxes", "--input", str(input_pdf), "--original", str(original_pdf),
                "--outdir", str(outdir), "--boxes", boxes,
            ],
            "pipeline": lambda input_pdf, original_pdf, outdir: [
                str(PIPELINE_SCRIPT), "--repo-root", str(REPO_ROOT), "--mode", "finalize",
                "--input", str(input_pdf), "--original", str(original_pdf), "--outdir", str(outdir),
                "--opts", "{}",
            ],
        }
        for entrypoint, command in entrypoints.items():
            for role in ("input", "original", "outdir"):
                with self.subTest(entrypoint=entrypoint, role=role):
                    with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as elsewhere:
                        allowed_root = Path(allowed)
                        outside_root = Path(elsewhere)
                        inside_pdf = allowed_root / "inside.pdf"
                        outside_pdf = outside_root / "secret.pdf"
                        write_pdf(inside_pdf)
                        write_pdf(outside_pdf)
                        escape = allowed_root / "escape"
                        escape.symlink_to(outside_root, target_is_directory=True)
                        before_allowed = directory_snapshot(allowed_root)
                        before_outside = directory_snapshot(outside_root)
                        before_sources = source_snapshot()
                        paths = {
                            "input": inside_pdf,
                            "original": inside_pdf,
                            "outdir": allowed_root,
                        }
                        paths[role] = escape / outside_pdf.name if role != "outdir" else escape

                        proc = run(command(paths["input"], paths["original"], paths["outdir"]), env_allowed=allowed)
                        self.assertNotEqual(0, proc.returncode)
                        self.assertEqual("", proc.stdout.strip())
                        self.assertNotIn(str(outside_root), proc.stderr)
                        self.assertNotIn("secret.pdf", proc.stderr)
                        self.assertEqual(before_allowed, directory_snapshot(allowed_root))
                        self.assertEqual(before_outside, directory_snapshot(outside_root))
                        self.assertEqual(before_sources, source_snapshot())

    @unittest.skipUnless(os.name == "nt", "Windows junction coverage is unavailable on this platform")
    def test_all_entrypoints_reject_windows_junction_escape_for_each_protected_role(self) -> None:
        boxes = json.dumps([{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}])
        entrypoints = {
            "manual": lambda input_pdf, original_pdf, outdir: [str(MANUAL_SCRIPT), "--input", str(input_pdf), "--original", str(original_pdf), "--outdir", str(outdir), "--boxes", boxes],
            "engine": lambda input_pdf, original_pdf, outdir: [str(ENGINE_ENTRY), "--manual-boxes", "--input", str(input_pdf), "--original", str(original_pdf), "--outdir", str(outdir), "--boxes", boxes],
            "pipeline": lambda input_pdf, original_pdf, outdir: [str(PIPELINE_SCRIPT), "--repo-root", str(REPO_ROOT), "--mode", "finalize", "--input", str(input_pdf), "--original", str(original_pdf), "--outdir", str(outdir), "--opts", "{}"],
        }
        for entrypoint, command in entrypoints.items():
            for role in ("input", "original", "outdir"):
                with self.subTest(entrypoint=entrypoint, role=role), tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as elsewhere:
                    allowed_root, outside_root = Path(allowed), Path(elsewhere)
                    inside_pdf, outside_pdf = allowed_root / "inside.pdf", outside_root / "secret.pdf"
                    write_pdf(inside_pdf)
                    write_pdf(outside_pdf)
                    junction = allowed_root / "escape"
                    junction_result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside_root)], capture_output=True, text=True)
                    self.assertEqual(0, junction_result.returncode, junction_result.stderr)
                    before_allowed = directory_snapshot(allowed_root)
                    before_outside = directory_snapshot(outside_root)
                    before_sources = source_snapshot()
                    paths = {"input": inside_pdf, "original": inside_pdf, "outdir": allowed_root}
                    paths[role] = junction / outside_pdf.name if role != "outdir" else junction
                    proc = run(command(paths["input"], paths["original"], paths["outdir"]), env_allowed=allowed)
                    self.assertNotEqual(0, proc.returncode)
                    self.assertEqual("", proc.stdout.strip())
                    self.assertNotIn(str(outside_root), proc.stderr)
                    self.assertNotIn("secret.pdf", proc.stderr)
                    self.assertEqual(before_allowed, directory_snapshot(allowed_root))
                    self.assertEqual(before_outside, directory_snapshot(outside_root))
                    self.assertEqual(before_sources, source_snapshot())
                    if entrypoint == "manual":
                        self.assertEqual("MANUAL_APPLY_FAILED", proc.stderr.strip())
                    elif entrypoint == "engine":
                        self.assertEqual("PATH_ACCESS_REJECTED", json.loads(proc.stderr)["error"]["code"])
                    else:
                        self.assertEqual(
                            "MASKING_PIPELINE_PATH_SYMLINK_REJECTED",
                            json.loads(proc.stderr)["error"]["code"],
                        )

    @unittest.skipUnless(os.name == "nt", "Windows junction coverage is unavailable on this platform")
    def test_all_entrypoints_allow_windows_junction_ancestor_inside_allowlist(self) -> None:
        boxes = json.dumps([{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}])
        entrypoints = {
            "manual": lambda input_pdf, original_pdf, outdir: [
                str(MANUAL_SCRIPT), "--input", str(input_pdf), "--original", str(original_pdf),
                "--outdir", str(outdir), "--boxes", boxes,
            ],
            "engine": lambda input_pdf, original_pdf, outdir: [
                str(ENGINE_ENTRY), "--manual-boxes", "--input", str(input_pdf),
                "--original", str(original_pdf), "--outdir", str(outdir), "--boxes", boxes,
            ],
            "pipeline": lambda input_pdf, original_pdf, outdir: [
                str(PIPELINE_SCRIPT), "--repo-root", str(REPO_ROOT), "--mode", "finalize",
                "--input", str(input_pdf), "--original", str(original_pdf),
                "--outdir", str(outdir), "--opts", "{}",
            ],
        }
        for entrypoint, command in entrypoints.items():
            for role in ("input", "original", "outdir"):
                with self.subTest(entrypoint=entrypoint, role=role), tempfile.TemporaryDirectory() as allowed:
                    allowed_root = Path(allowed)
                    physical_root = allowed_root / "physical"
                    physical_root.mkdir()
                    inside_pdf = physical_root / "inside.pdf"
                    write_pdf(inside_pdf)
                    junction = allowed_root / "redirected"
                    junction_result = subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(junction), str(physical_root)],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(0, junction_result.returncode, junction_result.stderr)
                    paths = {
                        "input": inside_pdf,
                        "original": inside_pdf,
                        "outdir": physical_root,
                    }
                    paths[role] = junction / inside_pdf.name if role != "outdir" else junction

                    proc = run(
                        command(paths["input"], paths["original"], paths["outdir"]),
                        env_allowed=allowed,
                        debug_trace=entrypoint == "pipeline",
                    )

                    self.assertEqual(0, proc.returncode, f"stderr:\n{proc.stderr}")

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


if __name__ == "__main__":
    unittest.main()
