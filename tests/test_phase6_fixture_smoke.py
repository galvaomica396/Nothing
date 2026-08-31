from __future__ import annotations

import json
import subprocess
import sys
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import fitz


REPO_ROOT = Path(__file__).resolve().parents[1]
ENSURE_FIXTURE_SCRIPT = REPO_ROOT / "scripts" / "ensure_phase6_fixture.py"
FIXTURE_SMOKE_SCRIPT = REPO_ROOT / "scripts" / "e2e_fixture_smoke.py"


def run_json_command(args: list[str]) -> dict[str, object]:
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise AssertionError("fixture command timed out without publishing an artifact") from None
    if completed.stderr or len(completed.stdout.splitlines()) != 1:
        raise AssertionError("fixture command must emit exactly one stdout JSON event")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise AssertionError("fixture command emitted an unstructured event") from error
    if not isinstance(payload, dict):
        raise AssertionError("fixture command event must be an object")
    return payload
def run_engine_command(args: list[str], *, allowed_dir: Path) -> dict[str, object]:
    try:
        completed = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "MASK_TOOL_ALLOWED_DIRS": str(allowed_dir.resolve())},
        )
    except subprocess.TimeoutExpired:
        raise AssertionError("engine command timed out without publishing an artifact") from None
    self_output = completed.stdout.splitlines()
    if len(self_output) != 1:
        raise AssertionError("engine stdout must contain exactly one structured event")
    event = json.loads(self_output[0])
    expected = {"event", "schemaVersion", "result"}
    if set(event) != expected or event["event"] != "engine_result" or event["schemaVersion"] != 1:
        raise AssertionError("engine result event schema is invalid")
    result = event["result"]
    if not isinstance(result, dict) or set(result) != {
        "status", "runtimeManifest", "rawTextReturned", "enginePackaged",
    }:
        raise AssertionError("engine result payload schema is invalid")
    if result["status"] != "ok" or result["rawTextReturned"] is not False:
        raise AssertionError("engine result safety contract failed")
    if not isinstance(result["runtimeManifest"], dict) or set(result["runtimeManifest"]) != {"outputs"}:
        raise AssertionError("engine runtime manifest schema is invalid")
    if not isinstance(result["runtimeManifest"]["outputs"], dict) or not all(
        isinstance(value, bool) for value in result["runtimeManifest"]["outputs"].values()
    ):
        raise AssertionError("engine output manifest must be boolean-only")
    if completed.stderr:
        for line in completed.stderr.splitlines():
            diagnostic = json.loads(line)
            if (
                set(diagnostic) != {"event", "schemaVersion", "diagnostics"}
                or diagnostic["event"] != "engine_diagnostics"
                or diagnostic["schemaVersion"] != 1
            ):
                raise AssertionError("engine diagnostic event schema is invalid")
    return result

class Phase6FixtureSmokeTests(unittest.TestCase):
    def test_command_waits_are_bounded_and_timeout_diagnostics_do_not_echo_arguments(self) -> None:
        secret = "TIMEOUT_PII_CANARY_010-1234-5678"
        args = [sys.executable, "-c", "import time; time.sleep(60)", secret]
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(args, 30)):
            with self.assertRaisesRegex(AssertionError, "^fixture command timed out without publishing an artifact$") as error:
                run_json_command(args)
        self.assertNotIn(secret, str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertTrue(error.exception.__suppress_context__)
    def test_fixture_generator_creates_non_sensitive_pdf_with_expected_dummy_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "phase6_fixture.pdf"

            result = run_json_command(
                [
                    sys.executable,
                    str(ENSURE_FIXTURE_SCRIPT),
                    "--output",
                    str(output_path),
                    "--force",
                ]
            )

            self.assertEqual(str(output_path.resolve()), result["fixture_path"])
            self.assertTrue(output_path.exists())
            self.assertGreater(int(result["bytes"]), 0)
            self.assertEqual(64, len(str(result["sha256"])))

            doc = fitz.open(output_path)
            try:
                page_count = len(doc)
                text = "\n".join(page.get_text() for page in doc)
            finally:
                doc.close()
            normalized = text.replace("\xa0", " ")
            self.assertIn("010-0000-0000", normalized)
            self.assertIn("4000-0000-0000-0000", normalized)
            self.assertIn("M00000000", normalized)
            self.assertIn("부산광역시 해운대구 우동 테스트로 0", normalized)
            self.assertIn("MAKIIING V2 PHASE 6 NON-SENSITIVE FIXTURE", normalized)
            self.assertIn("법률문서 더미 케이스", normalized)
            self.assertEqual(2, page_count)

    def test_fixture_smoke_masks_fixture_without_returning_raw_text(self) -> None:
        raw_values = (
            "010-0000-0000",
            "4000-0000-0000-0000",
            "M00000000",
            "부산광역시 해운대구 우동 테스트로 0",
        )
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            fixture_path = work / "phase6_fixture.pdf"
            outdir = work / "out"
            outdir.mkdir()
            run_json_command([
                sys.executable,
                str(ENSURE_FIXTURE_SCRIPT),
                "--output",
                str(fixture_path),
                "--force",
            ])
            result = run_engine_command([
                sys.executable,
                str(REPO_ROOT / "scripts" / "masking_engine_entry.py"),
                "--repo-root",
                str(REPO_ROOT),
                "--input",
                str(fixture_path),
                "--outdir",
                str(outdir),
                "--opts",
                json.dumps({
                    "output_artifacts": "pdf_safe_report",
                    "profile": "legal",
                    "region_scope": "national",
                    "display_mode": "black",
                    "pdf_redaction": True,
                    "return_text_preview": False,
                }, separators=(",", ":")),
            ], allowed_dir=work)

            runtime_outputs = result["runtimeManifest"]["outputs"]
            self.assertTrue(runtime_outputs["masked_pdf_file"])
            self.assertTrue(runtime_outputs["report_path"])
            self.assertNotIn(str(fixture_path), json.dumps(result))
            self.assertNotIn("010-0000-0000", json.dumps(result))
            self.assertFalse(result["rawTextReturned"])

            masked_pdfs = list(outdir.glob("*.final_masked_black.*.pdf"))
            self.assertEqual(1, len(masked_pdfs))
            self.assertGreater(masked_pdfs[0].stat().st_size, 0)
            document = fitz.open(masked_pdfs[0])
            try:
                self.assertEqual(2, len(document))
                masked_text = "\n".join(page.get_text() for page in document).replace("\xa0", " ")
            finally:
                document.close()
            self.assertIn("MAKIIING V2 PHASE 6 NON-SENSITIVE FIXTURE", masked_text)
            self.assertIn("법률문서 더미 케이스", masked_text)
            for raw_value in raw_values:
                self.assertNotIn(raw_value, masked_text)
            public_channels = {
                "engine_result": json.dumps(result, ensure_ascii=False).encode("utf-8"),
            }
            for artifact in outdir.rglob("*"):
                if artifact.is_file():
                    public_channels[str(artifact.relative_to(outdir))] = artifact.read_bytes()
            for channel, value in public_channels.items():
                with self.subTest(channel=channel):
                    text = value.decode("utf-8", errors="replace")
                    for raw_value in raw_values:
                        self.assertNotIn(raw_value, text)
                    self.assertNotIn(str(fixture_path), text)

            failure_canaries = (
                "FAILURE_PII_CANARY_010-1234-5678",
                "/private/FAILURE_PATH_CANARY.pdf",
                "FAILURE_ERROR_CANARY",
            )
            failed = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "masking_engine_entry.py"),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--input",
                    failure_canaries[1],
                    "--outdir",
                    str(outdir / failure_canaries[2]),
                    "--opts",
                    json.dumps({"profile": failure_canaries[0]}),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            failure_output = failed.stdout + failed.stderr
            self.assertNotEqual(0, failed.returncode)
            for canary in failure_canaries:
                self.assertNotIn(canary, failure_output)


if __name__ == "__main__":
    unittest.main()
