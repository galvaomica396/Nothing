from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_SCRIPT = REPO_ROOT / "scripts" / "run_masking_pipeline.py"


class TrustedFinalizeWrapperTests(unittest.TestCase):
    def test_trusted_finalize_emits_aligned_counts(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            root = Path(temporary)
            original = root / "original.pdf"
            manifest = root / "immutable-manifest.json"
            staging_output = root / "finalized.pdf"
            original.write_bytes(b"source")
            manifest.write_text("{}", encoding="utf-8")
            (root / "document_masker_ocr_gui.py").write_text(
                "def normalize_opts(options):\n"
                "    return options\n\n"
                "def trusted_finalize_manifest(original, manifest, options, staging_output):\n"
                "    with open(staging_output, \"wb\") as output:\n"
                "        output.write(b\"finalized\")\n"
                "    return {\n"
                "        \"status\": \"applied\",\n"
                "        \"verification\": {\"verified\": True},\n"
                "        \"staging_hash\": \"a\" * 64,\n"
                "        \"occurrence_count\": 2,\n"
                "        \"applied_mask_count\": 2,\n"
                "    }\n",
                encoding="utf-8",
            )
            request = {
                "input": str(original),
                "original": str(original),
                "manifest": str(manifest),
                "staging_output": str(staging_output),
                "options": {"display_mode": "black", "profile": "mixed"},
            }
            environment = os.environ.copy()
            environment["MASK_TOOL_ALLOWED_DIRS"] = str(root)
            environment.pop("MASK_TOOL_DEBUG_TRACE", None)
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(REPO_ROOT), environment.get("PYTHONPATH", "")))
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE_SCRIPT),
                    "--repo-root",
                    str(root),
                    "--mode",
                    "trusted-finalize",
                    "--request-stdin",
                ],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env=environment,
                check=False,
            )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(
            {
                "status": "applied",
                "verification": {"verified": True},
                "staging_hash": "a" * 64,
                "occurrenceCount": 2,
                "appliedMaskCount": 2,
            },
            json.loads(completed.stdout),
        )

    def test_trusted_finalize_debug_trace_is_opt_in_and_redacts_message(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary:
            root = Path(temporary)
            original = root / "private-original.pdf"
            manifest = root / "immutable-manifest.json"
            staging_output = root / "finalized.pdf"
            original.write_bytes(b"source")
            manifest.write_text("{}", encoding="utf-8")
            (root / "document_masker_ocr_gui.py").write_text(
                "def normalize_opts(options):\n"
                "    return options\n\n"
                "def trusted_finalize_manifest(original, manifest, options, staging_output):\n"
                "    raise RuntimeError('RAW_DOCUMENT_TEXT')\n",
                encoding="utf-8",
            )
            request = {
                "input": str(original),
                "original": str(original),
                "manifest": str(manifest),
                "staging_output": str(staging_output),
                "options": {"display_mode": "black", "profile": "mixed"},
            }
            environment = os.environ.copy()
            environment["MASK_TOOL_ALLOWED_DIRS"] = str(root)
            environment["MASK_TOOL_DEBUG_TRACE"] = "1"
            environment["PYTHONPATH"] = os.pathsep.join(
                filter(None, (str(REPO_ROOT), environment.get("PYTHONPATH", "")))
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE_SCRIPT),
                    "--repo-root",
                    str(root),
                    "--mode",
                    "trusted-finalize",
                    "--request-stdin",
                ],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env=environment,
                check=False,
            )

        self.assertNotEqual(0, completed.returncode)
        self.assertEqual("", completed.stdout)
        payload = json.loads(completed.stderr)
        self.assertEqual("MASKING_PIPELINE_INTERNAL_FAILURE", payload["error"]["code"])
        self.assertEqual("RuntimeError", payload["error"]["debug"]["exceptionType"])
        self.assertEqual("exception_message_suppressed", payload["error"]["debug"]["message"])
        self.assertNotIn("RAW_DOCUMENT_TEXT", completed.stderr)
        self.assertNotIn(str(root), completed.stderr)
