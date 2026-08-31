#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Final


REPO_ROOT: Final = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from document_masker_ocr_gui import trusted_analysis_manifest
from masking_rules import EMAIL_PAT
from scripts.real_corpus import RealCorpusError, resolve_real_corpus


SESSION_HASH_KEY: Final = bytes(range(32))
MIXED_OPTIONS: Final = {
    "profile": "mixed",
    "auto_threshold": 0.85,
    "review_threshold": 0.5,
}

def _valid_segments(segments: list[dict[str, object]]) -> list[dict[str, object]]:
    valid: list[dict[str, object]] = []
    for segment in segments:
        kind = segment.get("kind")
        page_start = segment.get("page_start")
        page_end = segment.get("page_end")
        if isinstance(kind, str) and type(page_start) is int and type(page_end) is int and page_end >= page_start:
            valid.append(segment)
    return valid


def _is_ambiguous(segment: dict[str, object]) -> bool:
    return segment.get("common_only") is True


def dominant_segment_kind(segments: list[dict[str, object]]) -> str | None:
    page_counts: Counter[str] = Counter()
    for segment in segments:
        kind = segment.get("kind")
        if not isinstance(kind, str):
            continue
        page_counts[kind] += int(segment["page_end"]) - int(segment["page_start"]) + 1
    if not page_counts:
        return None
    highest = max(page_counts.values())
    winners = sorted(kind for kind, count in page_counts.items() if count == highest)
    return winners[0] if len(winners) == 1 else None


def verdict_for(gold: str, segments: list[dict[str, object]]) -> tuple[str, str | None]:
    confirmed = [segment for segment in _valid_segments(segments) if not _is_ambiguous(segment)]
    ambiguous = [segment for segment in _valid_segments(segments) if _is_ambiguous(segment)]
    predicted = dominant_segment_kind(confirmed)
    if not confirmed:
        return "WRONG", None
    if all(segment.get("kind") == gold for segment in confirmed):
        return ("CONFIRMED" if not ambiguous else "CONSERVATIVE"), predicted
    return "WRONG", predicted


def _email_inventory(
    path: Path,
    occurrences: list[dict[str, object]],
) -> tuple[int | None, int]:
    """Return text-layer email count and geometry-backed detected count.

    ``None`` is deliberate: a page without a usable text layer is unknown,
    not proof that no email exists.  The emitted evaluator line contains only
    counts and the hash-manifest alias; no extracted value or filename.
    """
    try:
        import fitz  # type: ignore

        with fitz.open(path) as document:
            text_count = sum(
                len(EMAIL_PAT.findall(page.get_text("text")))
                for page in document
            )
            detected = 0
            for occurrence in occurrences:
                if occurrence.get("category") not in {"email", "footer_contact"}:
                    continue
                page_index = occurrence.get("page")
                rects = occurrence.get("rects")
                if type(page_index) is not int or not isinstance(rects, list) or not rects:
                    continue
                if not 0 <= page_index < document.page_count:
                    continue
                visible = "\n".join(
                    document[page_index].get_textbox(
                        fitz.Rect(rect["x0"], rect["y0"], rect["x1"], rect["y1"])
                    )
                    for rect in rects
                    if isinstance(rect, dict)
                    and all(key in rect for key in ("x0", "y0", "x1", "y1"))
                )
                if EMAIL_PAT.search(visible):
                    detected += 1
            return text_count, detected
    except (OSError, RuntimeError, ValueError, TypeError):
        return None, 0


def _detection_inventory(path: Path, manifest: dict[str, object]) -> dict[str, object]:
    raw_occurrences = manifest.get("occurrences")
    occurrences = raw_occurrences if isinstance(raw_occurrences, list) else []
    typed_occurrences = [
        item for item in occurrences
        if isinstance(item, dict)
    ]
    email_text_count, email_detected_count = _email_inventory(path, typed_occurrences)
    category_counts = Counter(
        str(item["category"])
        for item in typed_occurrences
        if isinstance(item.get("category"), str)
        and str(item["category"]) in {
            "dispatch_metadata", "email", "footer_contact",
            "institution_address", "institution_value", "region_name",
        }
    )
    return {
        "email_text_layer": email_text_count,
        "email_detected": email_detected_count,
        "categories": dict(sorted(category_counts.items())),
    }


def evaluate(corpus_dir: Path | None = None, *, alias: str | None = None) -> int:
    try:
        documents = resolve_real_corpus(corpus_dir, alias=alias)
    except RealCorpusError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 2
    if not documents:
        print("No manifest documents selected.")
        return 2
    counts: Counter[str] = Counter()
    for document in documents:
        path = Path(document["path"])
        alias = str(document["alias"])
        digest = str(document["sha256"])
        gold = str(document["category"])
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                manifest = trusted_analysis_manifest(
                    str(path), dict(MIXED_OPTIONS), session_hash_key=SESSION_HASH_KEY,
                )
            raw_segments = manifest.get("segments")
            segments = raw_segments if isinstance(raw_segments, list) else []
            verdict, predicted = verdict_for(gold, segments)
            inventory = _detection_inventory(path, manifest)
        except (OSError, RuntimeError, ValueError) as error:
            verdict, predicted = "WRONG", None
            inventory = {"email_text_layer": None, "email_detected": 0, "categories": {}}
            print(f"note: {alias} analysis failed: {type(error).__name__}", file=sys.stderr)
        counts[verdict] += 1
        prediction = predicted if predicted is not None else "ambiguous"
        print(f"{verdict}\talias={alias}\tsha256={digest}\tgold={gold}\tpredicted={prediction}")
        email_layer = inventory["email_text_layer"]
        email_status = (
            "unknown" if email_layer is None
            else "present" if email_layer > 0
            else "absent"
        )
        print(
            "DETECTION"
            f"\talias={alias}\tsha256={digest}"
            f"\temails={email_status}:{email_layer if email_layer is not None else 'unknown'}"
            f"\temail_detected={inventory['email_detected']}"
            f"\tcategories={inventory['categories']}"
        )
    total = len(documents)
    print(f"Categories: CONFIRMED={counts['CONFIRMED']} CONSERVATIVE={counts['CONSERVATIVE']} WRONG={counts['WRONG']}")
    wrong = counts["WRONG"]
    print(f"{'FAIL' if wrong else 'PASS'}\tWRONG={wrong}/{total}")
    return 1 if wrong else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate mixed-profile routing against the hash-only real corpus.")
    parser.add_argument("corpus_dir", type=Path, nargs="?")
    parser.add_argument("--alias")
    args = parser.parse_args()
    corpus_dir = args.corpus_dir.expanduser().resolve() if args.corpus_dir else None
    if corpus_dir is not None and not corpus_dir.is_dir():
        print("Corpus directory is unavailable.", file=sys.stderr)
        return 2
    return evaluate(corpus_dir, alias=args.alias)


if __name__ == "__main__":
    raise SystemExit(main())
