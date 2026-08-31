#!/usr/bin/env python3
"""Hash-only resolver for the fixed real-document corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Final

REAL_CORPUS_SIZE: Final = 15
REAL_CORPUS_MANIFEST_PATH: Final = Path(__file__).resolve().parents[1] / "contracts" / "real-corpus.json"
VALID_CATEGORIES: Final = frozenset({"internal_review", "official_dispatch"})
SHA256_LENGTH: Final = 64
FORBIDDEN_MANIFEST_KEYS: Final = frozenset({"filename", "fileName", "path", "absolutePath"})


class RealCorpusError(RuntimeError):
    code = "REAL_CORPUS_INCOMPLETE"


def load_real_corpus_manifest() -> tuple[dict[str, str], ...]:
    try:
        raw = json.loads(REAL_CORPUS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RealCorpusError(f"manifest could not be read ({type(error).__name__})") from error
    documents = raw.get("documents") if isinstance(raw, dict) else None
    if not isinstance(documents, list):
        raise RealCorpusError("manifest must contain a documents array")
    if len(documents) != REAL_CORPUS_SIZE:
        raise RealCorpusError(f"manifest contains {len(documents)} entries; expected {REAL_CORPUS_SIZE}")

    aliases: set[str] = set()
    hashes: set[str] = set()
    normalized: list[dict[str, str]] = []
    problems: list[str] = []
    for index, value in enumerate(documents):
        label = f"documents[{index}]"
        if not isinstance(value, dict):
            problems.append(f"{label} is not an object")
            continue
        forbidden = FORBIDDEN_MANIFEST_KEYS.intersection(value)
        if forbidden:
            problems.append(f"{label}.{sorted(forbidden)[0]} is forbidden")
            continue
        alias = value.get("alias") if isinstance(value.get("alias"), str) else f"doc-{index + 1:02d}"
        digest = value.get("sha256", "")
        category = value.get("category")
        if not isinstance(digest, str) or len(digest) != SHA256_LENGTH or any(
            character not in "0123456789abcdefABCDEF" for character in digest
        ):
            problems.append(f"{label}.sha256 is not a SHA-256 hash")
        if category not in VALID_CATEGORIES:
            problems.append(f"{label}.category is unsupported")
        if alias in aliases:
            problems.append(f"{label}.alias is duplicated")
        if digest in hashes:
            problems.append(f"{label}.sha256 is duplicated")
        aliases.add(alias)
        hashes.add(digest)
        normalized.append({"alias": alias, "sha256": digest.lower(), "category": category})
    if problems:
        raise RealCorpusError("; ".join(problems))
    return tuple(normalized)


def _corpus_root(directory: str | os.PathLike[str] | None) -> Path:
    value = directory if directory is not None else os.environ.get("NOTHING_REAL_CORPUS_DIR")
    return Path(value).expanduser().resolve() if value else (Path.home() / "Downloads").resolve()


def _pdf_hashes(root: Path) -> dict[str, list[Path]]:
    try:
        paths = (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")
        hashed: dict[str, list[Path]] = {}
        for path in paths:
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            hashed.setdefault(digest, []).append(path.resolve())
        return hashed
    except OSError as error:
        raise RealCorpusError(f"corpus directory cannot be scanned ({type(error).__name__})") from error


def resolve_real_corpus(
    directory: str | os.PathLike[str] | None = None,
    *,
    category: str | None = None,
    alias: str | None = None,
) -> tuple[dict[str, str | Path], ...]:
    root = _corpus_root(directory)
    if not root.is_dir():
        raise RealCorpusError("corpus root is not a directory")
    manifest = load_real_corpus_manifest()
    by_hash = _pdf_hashes(root)
    problems: list[str] = []
    resolved: list[dict[str, str | Path]] = []
    for entry in manifest:
        matches = by_hash.get(entry["sha256"], [])
        if not matches:
            problems.append(f'{entry["alias"]}: expected hash {entry["sha256"]} was not found')
        elif len(matches) != 1:
            problems.append(f'{entry["alias"]}: duplicate matching PDF')
        else:
            resolved.append({**entry, "path": matches[0]})
    if problems:
        raise RealCorpusError("; ".join(problems))
    if category is not None and category not in VALID_CATEGORIES:
        raise RealCorpusError(f"unsupported category filter {category}")
    if alias is not None and not any(entry["alias"] == alias for entry in resolved):
        raise RealCorpusError(f"manifest alias {alias} was not found")
    return tuple(
        entry
        for entry in resolved
        if (category is None or entry["category"] == category)
        and (alias is None or entry["alias"] == alias)
    )


def resolve_real_corpus_document(identifier: str, directory: str | os.PathLike[str] | None = None) -> dict[str, str | Path] | None:
    lowered = identifier.lower()
    return next(
        (
            entry
            for entry in resolve_real_corpus(directory)
            if entry["alias"] == identifier or entry["sha256"] == lowered
        ),
        None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve the fixed real corpus by SHA-256.")
    parser.add_argument("--category", choices=sorted(VALID_CATEGORIES))
    parser.add_argument("--alias")
    args = parser.parse_args()
    try:
        documents = resolve_real_corpus(category=args.category, alias=args.alias)
    except RealCorpusError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(
        [
            {"alias": entry["alias"], "sha256": entry["sha256"], "category": entry["category"]}
            for entry in documents
        ],
        ensure_ascii=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
