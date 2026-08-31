import re
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SENSITIVE_DOCUMENT_PATH = re.compile(
    r"(?:/Users/[^/\s`)<]+|/home/[^/\s`)<]+|/tmp/[^/\s`)<]+|/private/[^/\s`)<]+|/var/folders/[^/\s`)<]+|[A-Za-z]:\\\\Users\\\\[^\\\s`)<]+)"
)
EVIDENCE_ABSOLUTE_PATH = re.compile(
    r"(?:"
    r"(?<![:A-Za-z0-9._\]-])/(?!/)[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+"
    r"|[A-Za-z]:\\\\[^\\\s`)<]+(?:\\\\[^\\\s`)<]+)+"
    r")"
)


class RepositoryPrivacyTests(unittest.TestCase):
    def test_committed_documentation_and_evidence_do_not_embed_local_absolute_paths(self):
        documentation_files = [REPOSITORY_ROOT / "DESIGN.md"]
        documentation_files.extend((REPOSITORY_ROOT / "docs").rglob("*.md"))
        evidence_root = REPOSITORY_ROOT / ".omo" / "evidence"
        evidence_files = [
            path
            for path in evidence_root.rglob("*")
            if path.suffix in {".json", ".md"}
        ]

        leaks = []
        for path in documentation_files + evidence_files:
            if not path.is_file():
                continue
            pattern = (
                EVIDENCE_ABSOLUTE_PATH if path in evidence_files else SENSITIVE_DOCUMENT_PATH
            )
            matches = pattern.findall(path.read_text(encoding="utf-8"))
            if matches:
                leaks.append(f"{path.relative_to(REPOSITORY_ROOT)}: {matches[0]}")

        self.assertEqual(
            [],
            leaks,
            "documentation must not contain sensitive local paths and evidence must not contain raw local absolute paths",
        )
