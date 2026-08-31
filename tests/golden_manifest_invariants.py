from __future__ import annotations

from copy import deepcopy
from typing import Any


RUST_BOUNDARY_SEGMENT_KINDS = frozenset({
    "internal_review", "official_dispatch", "attachment", "legal",
})
GOLDEN_POLICY_VERSION = "masking-policy-v1"
GOLDEN_THRESHOLD_VERSION = "thresholds-v2"
FINAL_APPLIED_MASK_STATES = frozenset({"confirmed", "user_confirmed"})
SEGMENT_STATES = frozenset({"confirmed", "review_required", "user_confirmed"})
REVIEW_TARGET_KINDS = {
    "name": "occurrences",
    "institution": "occurrences",
    "region_geometry": "regions",
    "acknowledge": "segments",
    "boundary": "segments",
    "ocr": "segments",
}
RELEVANT_COVERAGE = {
    "internal_review": frozenset({"approval", "header_meta", "labeled_staff"}),
    "official_dispatch": frozenset({
        "recipient_reference", "sender_institution", "approval_staff",
        "dispatch_metadata", "footer_contact",
    }),
    "mixed": frozenset({
        "approval", "header_meta", "labeled_staff", "recipient_reference",
        "sender_institution", "approval_staff", "dispatch_metadata", "footer_contact",
    }),
}


class GoldenInvariantError(AssertionError):
    pass


def canonical_mask_count(manifest: dict[str, Any]) -> int:
    manually_replaced = {
        action["linkedOccurrenceId"]
        for action in manifest["manualActions"]
        if action["linkedOccurrenceId"] is not None
    }
    finalized = {
        item["occurrenceId"]
        for item in manifest["occurrences"]
        if item["proposedAction"] == "mask"
        and item["state"] in FINAL_APPLIED_MASK_STATES
        and item["occurrenceId"] not in manually_replaced
    }
    return len(finalized) + len(manifest["manualActions"])


def _target_ids(manifest: dict[str, Any], collection: str) -> set[str]:
    key = {
        "occurrences": "occurrenceId",
        "regions": "regionId",
        "segments": "segmentId",
    }[collection]
    return {item[key] for item in manifest[collection]}


def assert_public_review_targets_non_null(manifest: dict[str, Any]) -> None:
    for review in manifest["reviewItems"]:
        target_id = review["targetId"]
        if not isinstance(target_id, str) or not target_id:
            raise GoldenInvariantError("review target_id must be non-null")


def assert_review_target_bijection(manifest: dict[str, Any]) -> None:
    assert_public_review_targets_non_null(manifest)
    seen: set[tuple[str, str]] = set()
    pending_targets: set[tuple[str, str]] = set()
    for review in manifest["reviewItems"]:
        target_id = review["targetId"]
        target_collection = REVIEW_TARGET_KINDS.get(review["kind"])
        if target_collection is None or target_id not in _target_ids(manifest, target_collection):
            raise GoldenInvariantError(f"review {review['reviewId']} has no addressable target")
        target = (target_collection, target_id)
        if target in seen:
            raise GoldenInvariantError(f"review target is not bijective: duplicate {target}")
        seen.add(target)
        if review["status"] == "pending":
            pending_targets.add(target)
    required_targets = {
        ("segments", segment["segmentId"])
        for segment in manifest["segments"]
        if segment["state"] == "review_required"
    } | {
        ("regions", region["regionId"])
        for region in manifest["regions"]
        if region["state"] not in {"confirmed", "user_confirmed"}
    } | {
        ("occurrences", occurrence["occurrenceId"])
        for occurrence in manifest["occurrences"]
        if occurrence["state"] == "review_required"
    }
    if pending_targets != required_targets:
        missing = sorted(required_targets - pending_targets)
        unexpected = sorted(pending_targets - required_targets)
        raise GoldenInvariantError(
            f"review target is not bijective: missing={missing}, unexpected={unexpected}"
        )


def assert_indeterminate_coverage_has_pending_review(manifest: dict[str, Any]) -> None:
    relevant = RELEVANT_COVERAGE.get(manifest["profile"])
    if relevant is None:
        raise GoldenInvariantError(f"unsupported review profile: {manifest['profile']}")
    indeterminate = {
        item["kind"]
        for item in manifest["requiredRegionCoverage"]["kinds"]
        if item["kind"] in relevant and item["state"] == "indeterminate"
    }
    if manifest["approvalCoverage"]["state"] == "indeterminate":
        indeterminate.add("approval")
    geometry_reviewable = {
        region["kind"]
        for region in manifest["regions"]
        if region["kind"] in indeterminate
        and any(
            review["status"] == "pending"
            and review["kind"] == "region_geometry"
            and review["targetId"] == region["regionId"]
            for review in manifest["reviewItems"]
        )
    }
    segment_reviewable = any(
        segment["state"] == "review_required"
        and review["status"] == "pending"
        and review["kind"] in {"acknowledge", "boundary", "ocr"}
        and review["targetId"] == segment["segmentId"]
        for segment in manifest["segments"]
        for review in manifest["reviewItems"]
    )
    missing = indeterminate - geometry_reviewable
    if segment_reviewable:
        missing.clear()
    if missing:
        raise GoldenInvariantError(f"indeterminate coverage has no pending review: {sorted(missing)}")


def _independent_save_gate(manifest: dict[str, Any]) -> bool:
    if any(segment["state"] == "review_required" for segment in manifest["segments"]):
        return False
    if any(review["status"] == "pending" for review in manifest["reviewItems"]):
        return False
    try:
        assert_indeterminate_coverage_has_pending_review(manifest)
    except GoldenInvariantError:
        return False
    relevant = RELEVANT_COVERAGE.get(manifest["profile"])
    if relevant is None:
        raise GoldenInvariantError(f"unsupported review profile: {manifest['profile']}")
    coverage = {item["kind"]: item["state"] for item in manifest["requiredRegionCoverage"]["kinds"]}
    return manifest["approvalCoverage"]["state"] != "indeterminate" and not any(
        coverage.get(kind) == "indeterminate" for kind in relevant
    )


def assert_segment_save_gate_reachability(manifest: dict[str, Any]) -> None:
    for segment in manifest["segments"]:
        state = segment["state"]
        if state not in SEGMENT_STATES:
            raise GoldenInvariantError(f"unknown segment state: {state}")
        compatible = [
            review for review in manifest["reviewItems"]
            if review["status"] == "pending"
            and review["targetId"] == segment["segmentId"]
            and review["kind"] in {"acknowledge", "boundary", "ocr"}
        ]
        if state == "review_required" and not compatible:
            raise GoldenInvariantError("review_required segment has no pending resolution")
        candidate = deepcopy(manifest)
        for item in candidate["segments"]:
            item["state"] = "confirmed"
            if item["segmentId"] == segment["segmentId"] and state == "review_required":
                item["state"] = "user_confirmed"
        for review in candidate["reviewItems"]:
            review["status"] = "resolved"
        candidate["approvalCoverage"]["state"] = "absent"
        for coverage in candidate["requiredRegionCoverage"]["kinds"]:
            if coverage["state"] == "indeterminate":
                coverage["state"] = "absent"
        if not _independent_save_gate(candidate):
            raise GoldenInvariantError(f"segment state cannot reach save gate: {state}")


def assert_boundary_resolution_kinds(boundary_resolutions: list[dict[str, Any]]) -> None:
    invalid = {
        entry["resolution"]["segmentKind"]
        for entry in boundary_resolutions
        if entry["resolution"]["segmentKind"] not in RUST_BOUNDARY_SEGMENT_KINDS
    }
    if invalid:
        raise GoldenInvariantError(f"TS-only boundary segment kind: {sorted(invalid)}")


def assert_four_core_golden_invariants(manifest: dict[str, Any], expected_count: int) -> None:
    if expected_count != canonical_mask_count(manifest):
        raise GoldenInvariantError("expected occurrenceCount differs from canonical mask count")
    assert_indeterminate_coverage_has_pending_review(manifest)
    assert_segment_save_gate_reachability(manifest)
    assert_review_target_bijection(manifest)


def assert_golden_invariants(fixture: dict[str, Any]) -> None:
    manifest = fixture["manifest"]
    if fixture["fixture_version"] != 1:
        raise GoldenInvariantError("unsupported golden fixture version")
    if fixture["policy_version"] != GOLDEN_POLICY_VERSION:
        raise GoldenInvariantError("fixture does not use the pinned policy version")
    if fixture["threshold_version"] != GOLDEN_THRESHOLD_VERSION:
        raise GoldenInvariantError("fixture does not use the pinned threshold version")
    if fixture["policy_version"] != manifest["policyVersion"]:
        raise GoldenInvariantError("fixture policy version is not pinned to the manifest")
    if fixture["threshold_version"] != manifest["thresholdVersion"]:
        raise GoldenInvariantError("fixture threshold version is not pinned to the manifest")
    if manifest["thresholdArtifact"]["version"] != GOLDEN_THRESHOLD_VERSION:
        raise GoldenInvariantError("threshold artifact does not use the pinned threshold version")
    assert_four_core_golden_invariants(manifest, fixture["expected_occurrence_count"])
    assert_boundary_resolution_kinds(fixture["boundary_resolutions"])
