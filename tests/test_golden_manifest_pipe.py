"""Golden fixture regression template: register every incident invariant before fixing it."""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.models import AnalysisManifestV1, ManualActionV1, ResolveMaskingReviewRequest
from document_routing import BoundaryCorrection
from golden_manifest_invariants import GoldenInvariantError, assert_golden_invariants
from scripts.anonymize_manifest_fixture import (
    AnonymizationContractError,
    anonymize_values,
    assert_anonymization_contract,
)
from test_frontend_state_helpers import run_node_helper


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "golden-manifests"
RUST_ROUND_TRIP = ["cargo", "run", "--quiet", "--bin", "golden_manifest_roundtrip", "--"]


def fixture_paths() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def rust_round_trip(value: dict[str, Any], mode: str = "manifest") -> dict[str, Any]:
    result = subprocess.run(
        [*RUST_ROUND_TRIP, mode],
        cwd=REPO_ROOT / "src-tauri",
        input=json.dumps(value),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def ts_validate_manifest(manifest: dict[str, Any]) -> None:
    result = run_node_helper(
        "src/state/maskingSession.ts",
        f"m.parseAnalysisManifestV1({json.dumps(manifest)})",
    )
    assert result["ok"], result


def assert_json_semantically_equal(actual: Any, expected: Any) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == expected.keys()
        for key, value in expected.items():
            assert_json_semantically_equal(actual[key], value)
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            assert_json_semantically_equal(actual_item, expected_item)
        return
    if isinstance(expected, float):
        assert actual == pytest.approx(expected, rel=0, abs=1e-12)
        return
    assert actual == expected


def test_fixture_inventory_contains_all_fourteen_real_corpus_seeds() -> None:
    expected = [f"manifest-{index:02d}.json" for index in range(1, 15)]
    assert [path.name for path in fixture_paths()] == expected


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda path: path.stem)
def test_golden_fixture_round_trips_python_rust_and_typescript(path: Path) -> None:
    fixture = load_fixture(path)
    parsed = AnalysisManifestV1.model_validate(fixture["manifest"])
    public_manifest = parsed.model_dump(by_alias=True, mode="json")
    rust_manifest = rust_round_trip(public_manifest)
    assert_json_semantically_equal(rust_manifest, public_manifest)
    ts_validate_manifest(rust_manifest)
    for resolution in fixture["boundary_resolutions"]:
        parsed_resolution = ResolveMaskingReviewRequest.model_validate(resolution)
        assert rust_round_trip(parsed_resolution.model_dump(by_alias=True, mode="json"), "resolution") == resolution


@pytest.mark.parametrize("segment_kind", ("internal_review", "official_dispatch", "attachment", "legal"))
def test_boundary_resolution_allowed_kinds_round_trip_python_and_rust(segment_kind: str) -> None:
    resolution = {
        "runId": "run-1",
        "analysisRevision": 2,
        "manifestHash": "a" * 64,
        "reviewId": "review-1",
        "resolution": {"kind": "boundary", "pageStart": 0, "pageEnd": 0, "segmentKind": segment_kind},
    }
    parsed = ResolveMaskingReviewRequest.model_validate(resolution)
    assert rust_round_trip(parsed.model_dump(by_alias=True, mode="json"), "resolution") == resolution
    assert BoundaryCorrection(0, 0, segment_kind).kind == segment_kind


def test_t62_public_manual_action_requires_occurrence_bound_text_restore_authorization() -> None:
    action = {
        "actionId": "manual_aaaaaaaaaaaaaaaaaaaaaaaa",
        "analysisRevision": 2,
        "page": 0,
        "rects": [{"x0": 10.0, "y0": 20.0, "x1": 40.0, "y1": 60.0}],
        "protectedNeighborRefs": [],
        "mode": "mask",
        "sourceKind": "scan",
        "linkedOccurrenceId": None,
        "expectedTextHash": None,
        "restoreAuthorizationHash": None,
    }

    parsed = ManualActionV1.model_validate(action)

    assert parsed.mode == "mask"
    assert parsed.protected_neighbor_refs == ()
    restored = ManualActionV1.model_validate({
        **action,
        "mode": "restore",
        "sourceKind": "text_pdf",
        "linkedOccurrenceId": "occ_bbbbbbbbbbbbbbbbbbbbbbbb",
        "expectedTextHash": "b" * 64,
        "restoreAuthorizationHash": "c" * 64,
    })
    assert restored.mode == "restore"
    assert restored.source_kind == "text_pdf"
    assert restored.linked_occurrence_id == "occ_bbbbbbbbbbbbbbbbbbbbbbbb"
    assert restored.expected_text_hash == "b" * 64
    assert restored.restore_authorization_hash == "c" * 64


@pytest.mark.parametrize("segment_kind", ("mixed", "common", "unknown", "arbitrary"))
def test_boundary_resolution_rejects_noncanonical_kinds_at_python_boundaries(segment_kind: str) -> None:
    resolution = {
        "runId": "run-1",
        "analysisRevision": 2,
        "manifestHash": "a" * 64,
        "reviewId": "review-1",
        "resolution": {"kind": "boundary", "pageStart": 0, "pageEnd": 0, "segmentKind": segment_kind},
    }
    with pytest.raises(ValidationError):
        ResolveMaskingReviewRequest.model_validate(resolution)
    with pytest.raises(ValueError, match="unsupported correction segment kind"):
        BoundaryCorrection(0, 0, segment_kind)


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda path: path.stem)
def test_golden_fixture_independent_invariants(path: Path) -> None:
    assert_golden_invariants(load_fixture(path))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.__setitem__("expected_occurrence_count", value["expected_occurrence_count"] + 1), "occurrenceCount"),
        (lambda value: (
            value["manifest"]["requiredRegionCoverage"]["kinds"][0].update(state="indeterminate"),
            [review.update(status="resolved") for review in value["manifest"]["reviewItems"]],
        ), "indeterminate coverage"),
        (lambda value: value["manifest"]["reviewItems"].append(copy.deepcopy(value["manifest"]["reviewItems"][0])), "bijective"),
        (lambda value: value["manifest"]["segments"][0].update(state="review_required"), "no pending resolution"),
        (lambda value: value["manifest"]["reviewItems"][0].update(targetId=None), "target_id"),
        (lambda value: value["boundary_resolutions"][0]["resolution"].update(segmentKind="common"), "TS-only"),
    ],
)
def test_each_golden_invariant_rejects_a_mutated_fixture_copy(mutation: Any, message: str) -> None:
    fixture = load_fixture(fixture_paths()[0])
    mutation(fixture)
    with pytest.raises(GoldenInvariantError, match=message):
        assert_golden_invariants(fixture)


def test_known_unmatched_routing_review_fixture_is_rejected() -> None:
    fixture = load_fixture(fixture_paths()[0])
    fixture["manifest"]["reviewItems"][0]["targetId"] = None
    with pytest.raises(GoldenInvariantError, match="target_id"):
        assert_golden_invariants(fixture)
    with pytest.raises(ValidationError):
        AnalysisManifestV1.model_validate(fixture["manifest"])


def test_review_target_bijection_rejects_a_missing_required_review() -> None:
    fixture = load_fixture(fixture_paths()[0])
    fixture["manifest"]["reviewItems"].pop(0)

    with pytest.raises(GoldenInvariantError, match="bijective"):
        assert_golden_invariants(fixture)


def test_anonymizer_preserves_value_lengths_and_identifier_relationships() -> None:
    source = {
        "segments": [{"segment_id": "seg_aaaaaaaaaaaaaaaaaaaaaaaa"}],
        "review_items": [{"target_id": "seg_aaaaaaaaaaaaaaaaaaaaaaaa"}],
        "value_hash": "a" * 64,
        "label": "홍길동-2026",
    }
    anonymized = anonymize_values(source)
    assert anonymized.keys() == source.keys()
    assert anonymized["segments"][0]["segment_id"] == anonymized["review_items"][0]["target_id"]
    assert anonymized["segments"][0]["segment_id"] != source["segments"][0]["segment_id"]
    assert len(anonymized["value_hash"]) == len(source["value_hash"])
    assert len(anonymized["label"]) == len(source["label"])


def test_anonymization_contract_rejects_reference_relation_drift() -> None:
    source = {
        "segments": [{"segment_id": "seg_aaaaaaaaaaaaaaaaaaaaaaaa"}],
        "review_items": [{"target_id": "seg_aaaaaaaaaaaaaaaaaaaaaaaa"}],
    }
    anonymized = anonymize_values(source)
    anonymized["review_items"][0]["target_id"] = "seg_bbbbbbbbbbbbbbbbbbbbbbbb"

    with pytest.raises(AnonymizationContractError, match="relation drift"):
        assert_anonymization_contract(source, anonymized)


def test_version_pins_reject_a_silent_policy_and_threshold_upgrade() -> None:
    fixture = load_fixture(fixture_paths()[0])
    fixture["policy_version"] = fixture["manifest"]["policyVersion"] = "masking-policy-v2"
    fixture["threshold_version"] = fixture["manifest"]["thresholdVersion"] = "thresholds-v3"
    fixture["manifest"]["thresholdArtifact"]["version"] = "thresholds-v3"

    with pytest.raises(GoldenInvariantError, match="pinned policy version"):
        assert_golden_invariants(fixture)


def test_version_pins_reject_a_threshold_artifact_upgrade() -> None:
    fixture = load_fixture(fixture_paths()[0])
    fixture["manifest"]["thresholdArtifact"]["version"] = "thresholds-v3"

    with pytest.raises(GoldenInvariantError, match="threshold artifact"):
        assert_golden_invariants(fixture)


def test_golden_legal_save_controls_remain_react_owned_and_dispatch_to_finalization() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "components" / "CanvasWorkspace.tsx").read_text(encoding="utf-8")
    controller_source = (Path(__file__).resolve().parents[1] / "src" / "features" / "finalization" / "finalizationController.ts").read_text(encoding="utf-8")
    application_controller_source = (Path(__file__).resolve().parents[1] / "src" / "app" / "applicationController.ts").read_text(encoding="utf-8")
    final_save_start = source.index('id="final-save-dialog"')
    final_save_end = source.index('id="masking-progress-dialog"', final_save_start)
    final_save_modal = source[final_save_start:final_save_end]

    assert 'id="btn-save"' in source
    assert 'onClick={() => withWorkspace((controller) => void controller.saveFinalOutput())}' in source
    assert 'owner="react"' in final_save_modal
    assert 'hidden={!workspace.finalSaveDialog.visible}' in final_save_modal
    assert 'onClose={() => withWorkspace((controller) => controller.closeFinalSaveDialog())}' in final_save_modal
    assert 'id="btn-dialog-cancel-save" className="dm-btn dm-btn--ghost" type="button" onClick={() => withWorkspace((controller) => controller.closeFinalSaveDialog())}' in final_save_modal
    assert 'disabled={!workspace.finalSaveDialog.confirmEnabled}' in final_save_modal
    assert '{workspace.finalSaveDialog.confirmLabel}' in final_save_modal
    assert 'workspace.finalSaveDialog.warnings.map((warning)' in final_save_modal
    assert 'publishWorkspaceFinalSaveDialog({' in controller_source
    assert 'setWorkspaceFinalSaveDialogVisible(true)' in controller_source
    assert 'setWorkspaceFinalSaveDialogVisible(false)' in controller_source
    assert 'setModalVisible(deps.finalSaveDialogEl' not in controller_source
    assert 'setWorkspaceFinalSaveDialogVisible(false)' in application_controller_source


def test_t59_native_hex_save_token_may_start_with_a_digit() -> None:
    result = run_node_helper(
        "src/state/maskingSession.ts",
        "({ digit: m.isMaskingToken('0'.repeat(32)), letter: m.isMaskingToken('a'.repeat(32)), blank: m.isMaskingToken('') })",
    )

    assert result == {"digit": True, "letter": True, "blank": False}


def test_success_dialog_remains_react_owned_with_store_rendered_content() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "src" / "components" / "CanvasWorkspace.tsx").read_text(encoding="utf-8")
    store_source = (root / "src" / "state" / "workspaceStore.ts").read_text(encoding="utf-8")
    application_controller_source = (root / "src" / "app" / "applicationController.ts").read_text(encoding="utf-8")
    success_start = source.index('id="finalization-success-dialog"')
    success_modal = source[success_start:]

    assert 'owner="react"' in success_modal
    assert 'hidden={!workspace.finalizationSuccessDialog.visible}' in success_modal
    assert '{workspace.finalizationSuccessDialog.fileName}' in success_modal
    assert '{workspace.finalizationSuccessDialog.meta}' in success_modal
    assert 'export function publishWorkspaceFinalizationSuccessDialog' in store_source
    assert 'export function setWorkspaceFinalizationSuccessDialogVisible' in store_source
    assert 'publishWorkspaceFinalizationSuccessDialog({' in application_controller_source
    assert 'finalizationSuccessModalEl.querySelector' not in application_controller_source
    assert 'setModalVisible(finalizationSuccessModalEl' not in application_controller_source
