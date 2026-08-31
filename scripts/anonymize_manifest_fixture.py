#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["pydantic>=2"]
# ///

# ─── How to run ───
# 1. Install uv (if not installed):
#      curl -LsSf https://astral.sh/uv/install.sh | sh
# 2. Run directly:
#      uv run scripts/anonymize_manifest_fixture.py --input-dir /path/to/analysis-runs --output-dir tests/fixtures/golden-manifests
# ──────────────────
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from contracts.models import AnalysisManifestV1, ResolveMaskingReviewRequest
from tests.golden_manifest_invariants import (
    GOLDEN_POLICY_VERSION,
    GOLDEN_THRESHOLD_VERSION,
    RUST_BOUNDARY_SEGMENT_KINDS,
    assert_four_core_golden_invariants,
    assert_golden_invariants,
    canonical_mask_count,
)


PRESERVED_VALUE_KEYS = frozenset({
    "schema_version", "profile", "coordinate_space", "policy_version", "options_version",
    "threshold_version", "version", "kind", "state", "proposed_action", "status",
    "confirmation_source", "source", "policy", "provenance", "tag", "category",
    "reason_codes", "mode", "source_kind",
})


class AnonymizationContractError(AssertionError):
    pass


def _digest(value: str) -> str:
    return hashlib.sha256(f"golden-fixture-v1:{value}".encode()).hexdigest()


def _anonymized_string(value: str, key: str, mapping: dict[str, str]) -> str:
    if key in PRESERVED_VALUE_KEYS:
        return value
    cache_key = value
    if cache_key in mapping:
        return mapping[cache_key]
    digest = _digest(cache_key)
    if value.startswith("occ_") and len(value) == 28:
        result = f"occ_{digest[:24]}"
    elif value.startswith(("seg_", "region_", "review_")):
        prefix, suffix = value.split("_", 1)
        result = f"{prefix}_{digest[:len(suffix)]}"
    elif len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower()):
        result = digest
    else:
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        result = "".join(
            character if not character.isalnum() else alphabet[int(digest[index % len(digest)], 16) % len(alphabet)]
            for index, character in enumerate(value)
        )
    if len(result) != len(value) or result in mapping.values():
        raise ValueError("anonymization collision or length drift")
    mapping[cache_key] = result
    return result


def _anonymize_values(value: Any, key: str, substitutions: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            item_key: _anonymize_values(item_value, item_key, substitutions)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_anonymize_values(item, key, substitutions) for item in value]
    if isinstance(value, str):
        return _anonymized_string(value, key, substitutions)
    return value


def assert_anonymization_contract(source: Any, anonymized: Any) -> None:
    forward: dict[str, str] = {}
    reverse: dict[str, str] = {}

    def visit(source_value: Any, anonymized_value: Any, key: str, path: str) -> None:
        if isinstance(source_value, dict) and isinstance(anonymized_value, dict):
            if source_value.keys() != anonymized_value.keys():
                raise AnonymizationContractError(f"structure drift at {path}")
            for item_key, item_value in source_value.items():
                visit(item_value, anonymized_value[item_key], item_key, f"{path}.{item_key}")
            return
        if isinstance(source_value, list) and isinstance(anonymized_value, list):
            if len(source_value) != len(anonymized_value):
                raise AnonymizationContractError(f"structure drift at {path}")
            for index, item_value in enumerate(source_value):
                visit(item_value, anonymized_value[index], key, f"{path}[{index}]")
            return
        if isinstance(source_value, str) and isinstance(anonymized_value, str):
            if len(source_value) != len(anonymized_value):
                raise AnonymizationContractError(f"length drift at {path}")
            if key in PRESERVED_VALUE_KEYS and source_value != anonymized_value:
                raise AnonymizationContractError(f"contract token drift at {path}")
            prior_anonymized = forward.setdefault(source_value, anonymized_value)
            prior_source = reverse.setdefault(anonymized_value, source_value)
            if prior_anonymized != anonymized_value or prior_source != source_value:
                raise AnonymizationContractError(f"relation drift at {path}")
            return
        if type(source_value) is not type(anonymized_value) or source_value != anonymized_value:
            raise AnonymizationContractError(f"value or structure drift at {path}")

    visit(source, anonymized, "", "$")


def anonymize_values(value: Any) -> Any:
    anonymized = _anonymize_values(value, "", {})
    assert_anonymization_contract(value, anonymized)
    return anonymized


def public_manifest(manifest: dict[str, Any], index: int) -> dict[str, Any]:
    schema_version = manifest["schema_version"]
    public = {key: value for key, value in manifest.items() if key != "schema_version"}
    public["manifest_version"] = schema_version
    public["run_id"] = f"golden-run-{index:02d}"
    public["manifest_hash"] = _digest(f"manifest:{index}")
    parsed = AnalysisManifestV1.model_validate(public)
    return parsed.model_dump(by_alias=True, mode="json")


def assert_seed_versions(manifest: dict[str, Any]) -> None:
    if manifest["policy_version"] != GOLDEN_POLICY_VERSION:
        raise AnonymizationContractError(
            f"seed policy_version is not pinned: {manifest['policy_version']}"
        )
    if (
        manifest["threshold_version"] != GOLDEN_THRESHOLD_VERSION
        or manifest["threshold_artifact"]["version"] != GOLDEN_THRESHOLD_VERSION
    ):
        raise AnonymizationContractError(
            "seed threshold version is not pinned to the golden contract"
        )


def boundary_resolutions(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    vectors = []
    for index, segment in enumerate(manifest["segments"]):
        if segment["kind"] not in RUST_BOUNDARY_SEGMENT_KINDS:
            continue
        vector = {
            "runId": manifest["runId"], "analysisRevision": manifest["analysisRevision"],
            "manifestHash": manifest["manifestHash"], "reviewId": f"fixture-boundary-{index}",
            "resolution": {
                "kind": "boundary", "pageStart": segment["pageStart"],
                "pageEnd": segment["pageEnd"], "segmentKind": segment["kind"],
            },
        }
        ResolveMaskingReviewRequest.model_validate(vector)
        vectors.append(vector)
    return vectors


def fixture_from_corpus(source: Path, index: int) -> dict[str, Any]:
    raw = json.loads(source.read_text(encoding="utf-8"))["analysis_manifest"]
    anonymized = anonymize_values(raw)
    assert_seed_versions(anonymized)
    manifest = public_manifest(anonymized, index)
    expected_occurrence_count = canonical_mask_count(manifest)
    assert_four_core_golden_invariants(manifest, expected_occurrence_count)
    fixture = {
        "fixture_version": 1,
        "policy_version": manifest["policyVersion"],
        "threshold_version": manifest["thresholdVersion"],
        "expected_occurrence_count": expected_occurrence_count,
        "manifest": manifest,
        "boundary_resolutions": boundary_resolutions(manifest),
    }
    assert_golden_invariants(fixture)
    return fixture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    sources = sorted(arguments.input_dir.glob("*.json"))
    if len(sources) != 14:
        raise ValueError(f"expected 14 corpus manifests, found {len(sources)}")
    fixtures = [
        fixture_from_corpus(source, index)
        for index, source in enumerate(sources, start=1)
    ]
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    for index, fixture in enumerate(fixtures, start=1):
        output = arguments.output_dir / f"manifest-{index:02d}.json"
        output.write_text(json.dumps(fixture, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
