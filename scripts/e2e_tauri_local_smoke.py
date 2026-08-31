import argparse
import hashlib
import json
import math
import os
import platform
import plistlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Final


def load_tauri_config(repo_root: Path) -> dict[str, object]:
    config_path = repo_root / "src-tauri" / "tauri.conf.json"
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def default_app_path(repo_root: Path) -> Path:
    if platform.system() == "Darwin":
        bundle_dir = repo_root / "src-tauri" / "target" / "release" / "bundle" / "macos"
        candidates = sorted(bundle_dir.glob("*.app"))
        if candidates:
            return candidates[0]
    if platform.system() == "Windows":
        return repo_root / "src-tauri" / "target" / "release" / "Document-Masker-Tauri-windows-x64.exe"
    return repo_root / "src-tauri" / "target" / "release" / "tauri_frontend"


def macos_bundle_identifier(bundle_path: Path) -> str:
    info_plist = bundle_path / "Contents" / "Info.plist"
    if not info_plist.exists():
        return ""
    try:
        info = plistlib.loads(info_plist.read_bytes())
    except (plistlib.InvalidFileException, OSError, ValueError):
        return ""
    identifier = info.get("CFBundleIdentifier")
    return identifier.strip() if isinstance(identifier, str) else ""


def macos_bundle_id_report(search_roots: list[Path], bundle_id: str) -> dict[str, object]:
    active_apps: list[str] = []
    disabled_backups: list[str] = []
    for root in search_roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_dir():
                continue
            if candidate.suffix != ".app" and not candidate.name.endswith(".disabled-bundle"):
                continue
            if macos_bundle_identifier(candidate) != bundle_id:
                continue
            if candidate.suffix == ".app":
                active_apps.append(str(candidate))
            else:
                disabled_backups.append(str(candidate))

    active_apps = sorted(active_apps)
    disabled_backups = sorted(disabled_backups)
    ambiguous = len(active_apps) > 1
    if ambiguous:
        status = "ambiguous-active-apps"
    elif len(active_apps) == 1:
        status = "single-active-app"
    else:
        status = "no-active-app"
    return {
        "status": status,
        "bundle_id": bundle_id,
        "active_apps": active_apps,
        "disabled_backups": disabled_backups,
        "active_app_count": len(active_apps),
        "disabled_backup_count": len(disabled_backups),
        "ambiguous": ambiguous,
    }


def computer_use_attach_diagnosis(
    *,
    active_app_count: int,
    disabled_backup_count: int,
    cg_window_count: int,
    ax_window_count: int,
    computer_use_results: list[dict[str, str]],
) -> dict[str, object]:
    if active_app_count > 1:
        status = "ambiguous-bundle-id"
    elif cg_window_count > 0 and ax_window_count <= 0:
        status = "visible-cgwindow-without-accessibility-window"
    elif any(item.get("result") == "attached" for item in computer_use_results):
        status = "computer-use-attached"
    else:
        status = "computer-use-attach-failed"
    return {
        "status": status,
        "active_app_count": active_app_count,
        "disabled_backup_count": disabled_backup_count,
        "cg_window_count": cg_window_count,
        "ax_window_count": ax_window_count,
        "computer_use_result_count": len(computer_use_results),
    }


NATIVE_ACCEPTANCE_STEPS: tuple[str, ...] = (
    "computer_use_attached",
    "canvas_workspace_opened",
    "input_pdf_selected_via_os_picker",
    "fixture_pdf_loaded",
    "output_dir_selected_via_os_picker",
    "manual_mask_box_created",
    "manual_preview_applied",
    "final_save_completed",
)

NATIVE_ACCEPTANCE_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "canvas_workspace_opened": ("computer_use_attached",),
    "fixture_pdf_loaded": ("computer_use_attached", "canvas_workspace_opened"),
    "input_pdf_selected_via_os_picker": ("computer_use_attached", "canvas_workspace_opened"),
    "output_dir_selected_via_os_picker": ("computer_use_attached", "canvas_workspace_opened"),
    "manual_mask_box_created": ("computer_use_attached", "canvas_workspace_opened"),
    "manual_preview_applied": ("manual_mask_box_created",),
    "final_save_completed": ("manual_preview_applied",),
}

PUBLIC_DOCUMENT_STEPS: tuple[str, ...] = (
    "public_analyze_completed",
    "public_mixed_boundary_blocked",
    "public_ambiguous_common_only_blocked",
    "public_scan_manual_review_required",
    "public_repeated_occurrence_scoped",
    "public_review_cards_resolved",
    "public_manual_combined_resolved",
    "public_legal_advisory_isolated",
    "public_unresolved_review_blocked",
    "public_unresolved_review_confirmed",
    "public_stale_revision_blocked",
    "public_stale_manifest_hash_blocked",
    "public_tampered_manifest_blocked",
    "public_forged_resolution_blocked",
    "public_intrinsic_failure_blocked",
    "public_destination_bypass_blocked",
    "public_destination_authorized",
    "public_destination_token_issued",
    "public_threshold_hash_bound",
    "public_clean_document_verified",
    "public_atomic_promotion_failure_blocked",
    "public_finalize_promoted",
)
PUBLIC_DOCUMENT_PLUMBING_STEPS: tuple[str, ...] = (
    "public_analyze_completed",
    "public_unresolved_review_blocked",
    "public_unresolved_review_confirmed",
    "public_stale_revision_blocked",
    "public_stale_manifest_hash_blocked",
    "public_tampered_manifest_blocked",
    "public_forged_resolution_blocked",
    "public_destination_bypass_blocked",
    "public_destination_authorized",
    "public_destination_token_issued",
    "public_threshold_hash_bound",
    "public_atomic_promotion_failure_blocked",
    "public_finalize_promoted",
)
PUBLIC_NATIVE_RECEIPT_TIMEOUTS: dict[str, float] = {
    "public-document-plumbing": 5 * 60,
    "public-document-all": 10 * 60,
}
# Mirrors the fixture-semantic contract enforced by src-tauri/src/native_qa.rs.
OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT: Final = 19
OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT: Final = 10
MIXED_FIXTURE_OCCURRENCE_COUNT: Final = 19
MIXED_FIXTURE_PENDING_REVIEW_COUNT: Final = 10
AMBIGUOUS_FIXTURE_OCCURRENCE_COUNT: Final = 3
AMBIGUOUS_FIXTURE_PENDING_REVIEW_COUNT: Final = 1
CLEAN_FIXTURE_OCCURRENCE_COUNT: Final = 0
CLEAN_FIXTURE_PENDING_REVIEW_COUNT: Final = 0
MANUAL_OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT: Final = 19
MANUAL_OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT: Final = 10
PUBLIC_ACTION_SEMANTICS: dict[str, tuple[str, str | None]] = {
    "public_analyze_completed": ("pass", None),
    "public_mixed_boundary_blocked": ("blocked", "MIXED_BOUNDARY_REVIEW_REQUIRED"),
    "public_ambiguous_common_only_blocked": ("blocked", "AMBIGUOUS_COMMON_ONLY_REVIEW_REQUIRED"),
    "public_scan_manual_review_required": ("pass", None),
    "public_repeated_occurrence_scoped": ("pass", None),
    "public_review_cards_resolved": ("pass", None),
    "public_manual_combined_resolved": ("pass", None),
    "public_legal_advisory_isolated": ("pass", None),
    "public_unresolved_review_blocked": ("blocked", "UNRESOLVED_REVIEW"),
    "public_unresolved_review_confirmed": ("pass", None),
    "public_stale_revision_blocked": ("blocked", "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED"),
    "public_stale_manifest_hash_blocked": ("blocked", "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED"),
    "public_tampered_manifest_blocked": ("blocked", "PUBLIC_FINALIZE_REJECTED"),
    "public_forged_resolution_blocked": ("blocked", "REVIEW_RESOLUTION_REJECTED"),
    "public_intrinsic_failure_blocked": ("blocked", "INTRINSIC_VERIFICATION_FAILED"),
    "public_destination_bypass_blocked": ("blocked", "PUBLIC_FINALIZE_REJECTED"),
    "public_destination_authorized": ("pass", None),
    "public_destination_token_issued": ("pass", None),
    "public_threshold_hash_bound": ("pass", None),
    "public_clean_document_verified": ("pass", None),
    "public_atomic_promotion_failure_blocked": ("blocked", "ATOMIC_PROMOTION_FAILED"),
    "public_finalize_promoted": ("pass", None),
}


def pii_safe(value: object) -> bool:
    import re

    forbidden_keys = {
        "inputFile", "outputPath", "destination", "finalPath", "path", "locator",
        "runtime_channel", "stderr", "stdout", "error", "details", "app_path", "executable",
    }
    opaque_fields = {"evidence"}
    opaque_token = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
    sha256_token = re.compile(r"^[0-9a-fA-F]{64}$")
    forbidden_text = re.compile(
        r"(?:\b(?:\d{6}-?\d{7}|01\d-?\d{3,4}-?\d{4}|[^\s@]+@[^\s@]+\.[^\s@]+\b)"
        r"|(?:^|[\s\"'])/(?:[^\s\"']+)|(?:[A-Za-z]:[\\/]))"
    )

    def valid(item: object, key: str | None = None) -> bool:
        if isinstance(item, dict):
            return all(
                isinstance(name, str) and name not in forbidden_keys and valid(child, name)
                for name, child in item.items()
            )
        if isinstance(item, list):
            return all(valid(child) for child in item)
        if isinstance(item, str):
            if sha256_token.fullmatch(item):
                return True
            return not forbidden_text.search(item) and (
                key not in opaque_fields or bool(opaque_token.fullmatch(item))
            )
        return item is None or isinstance(item, (bool, int, float))

    return valid(value)


def atomic_write_evidence(output_path: str, text: str) -> None:
    supplied = Path(output_path).expanduser()
    if supplied.name in {"", ".", ".."}:
        raise ValueError("EVIDENCE_DESTINATION_REJECTED")
    try:
        if os.path.islink(supplied):
            raise ValueError("EVIDENCE_DESTINATION_REJECTED")
    except OSError:
        raise ValueError("EVIDENCE_DESTINATION_REJECTED") from None
    try:
        parent = supplied.parent.resolve(strict=True)
        parent_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError:
        raise ValueError("EVIDENCE_DESTINATION_REJECTED") from None
    temporary_name = f".smoke-evidence-{os.getpid()}-{time.time_ns()}"
    try:
        descriptor = os.open(
            temporary_name,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as evidence:
                evidence.write(f"{text}\n")
                evidence.flush()
                os.fsync(evidence.fileno())
            os.replace(
                temporary_name, supplied.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd
            )
            os.fsync(parent_fd)
        except Exception:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
            raise
    except OSError:
        raise ValueError("EVIDENCE_DESTINATION_REJECTED") from None
    finally:
        os.close(parent_fd)
def validate_threshold_artifact(
    artifact_path: str, pinned_digest: str, receipt: dict[str, object]
) -> bool:
    if not artifact_path or len(pinned_digest) != 64:
        return False
    try:
        artifact_bytes = Path(artifact_path).read_bytes()
        artifact = json.loads(artifact_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(artifact, dict) or hashlib.sha256(artifact_bytes).hexdigest() != pinned_digest:
        return False
    return (
        artifact.get("schemaVersion") == 1
        and artifact.get("thresholdVersion") == receipt["thresholdVersion"]
        and artifact.get("thresholdHash") == receipt["thresholdHash"]
        and artifact.get("thresholdValueHash") == receipt["thresholdValueHash"]
    )
def load_threshold_binding(
    artifact_path: str, pinned_digest: str
) -> tuple[dict[str, object] | None, str]:
    """Load the exact threshold binding required by the native QA request."""
    if not artifact_path:
        return None, "THRESHOLD_ARTIFACT_UNAVAILABLE"
    if (
        not isinstance(pinned_digest, str)
        or len(pinned_digest) != 64
        or any(character not in "0123456789abcdef" for character in pinned_digest)
    ):
        return None, "THRESHOLD_DIGEST_UNPINNED"
    try:
        artifact_bytes = Path(artifact_path).read_bytes()
        artifact = json.loads(artifact_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, "THRESHOLD_ARTIFACT_INVALID"
    if hashlib.sha256(artifact_bytes).hexdigest() != pinned_digest:
        return None, "THRESHOLD_DIGEST_MISMATCH"
    if (
        not isinstance(artifact, dict)
        or not isinstance(artifact.get("schemaVersion"), int)
        or isinstance(artifact.get("schemaVersion"), bool)
        or artifact["schemaVersion"] != 1
    ):
        return None, "THRESHOLD_ARTIFACT_INVALID"

    required_fields = (
        "thresholdVersion",
        "thresholdHash",
        "thresholdValueHash",
        "autoMaskThreshold",
        "reviewThreshold",
    )
    if any(field not in artifact for field in required_fields):
        return None, "THRESHOLD_ARTIFACT_INVALID"
    threshold_version = artifact["thresholdVersion"]
    threshold_hash = artifact["thresholdHash"]
    threshold_value_hash = artifact["thresholdValueHash"]
    auto_mask_threshold = artifact["autoMaskThreshold"]
    review_threshold = artifact["reviewThreshold"]
    is_hash = lambda value: (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
    if (
        not isinstance(threshold_version, str)
        or not threshold_version
        or not is_hash(threshold_hash)
        or not is_hash(threshold_value_hash)
        or not isinstance(auto_mask_threshold, (int, float))
        or isinstance(auto_mask_threshold, bool)
        or not isinstance(review_threshold, (int, float))
        or isinstance(review_threshold, bool)
        or not math.isfinite(float(auto_mask_threshold))
        or not math.isfinite(float(review_threshold))
        or not 0.0 <= float(review_threshold) <= float(auto_mask_threshold) <= 1.0
    ):
        return None, "THRESHOLD_ARTIFACT_INVALID"
    return (
        {
            "thresholdVersion": threshold_version,
            "thresholdHash": threshold_hash,
            "thresholdValueHash": threshold_value_hash,
            "autoMaskThreshold": auto_mask_threshold,
            "reviewThreshold": review_threshold,
        },
        "THRESHOLD_BINDING_LOADED",
    )


def public_steps_for_scenario(scenario: str) -> tuple[str, ...]:
    return PUBLIC_DOCUMENT_STEPS if scenario == "public-document-all" else PUBLIC_DOCUMENT_PLUMBING_STEPS



class TrustedNativeActions(list[dict[str, str]]):
    """Actions admitted only after validating a runtime receipt."""


def parse_native_actions(raw_actions: list[str]) -> list[dict[str, object]]:
    # Command-line assertions are diagnostics, never native evidence.
    return [
        {
            "name": "native_action_rejected",
            "status": "invalid",
            "evidence": "CALLER_AUTHORED_NATIVE_EVIDENCE_REJECTED",
        }
        for _ in raw_actions
    ]


def canonical_json_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def receipt_action_hash(receipt: dict[str, object], action: dict[str, object], phase: str) -> str:
    return canonical_json_hash(
        {
            "phase": phase,
            "scenario": receipt["scenario"],
            "name": action["name"],
            "outcome": action["outcome"],
            "errorCode": action["errorCode"],
            "requestEvidence": action["requestEvidence"],
            "resultEvidence": action["resultEvidence"],
            "nonce": receipt["nonce"],
            "binaryHash": receipt["binaryHash"],
            "runId": receipt["runId"],
            "analysisRevision": receipt["analysisRevision"],
            "manifestHash": receipt["manifestHash"],
            "thresholdVersion": receipt["thresholdVersion"],
            "thresholdHash": receipt["thresholdHash"],
            "thresholdValueHash": receipt["thresholdValueHash"],
        }
    )


def receipt_auth(receipt: dict[str, object]) -> str:
    return canonical_json_hash(
        {
            "domain": "DocumentMaskerNativeQaReceiptAuthV1",
            "nonce": receipt["nonce"],
            "binaryHash": receipt["binaryHash"],
            "canonicalReceiptHash": receipt["canonicalReceiptHash"],
            "actions": [
                {
                    "requestHash": action["requestHash"],
                    "resultHash": action["resultHash"],
                    "requestEvidence": action["requestEvidence"],
                    "resultEvidence": action["resultEvidence"],
                }
                for action in receipt["actions"]
            ],
        }
    )


def valid_action_evidence(receipt: dict[str, object], action: dict[str, object]) -> bool:
    request = action["requestEvidence"]
    result = action["resultEvidence"]
    name = action["name"]
    expected_outcome, expected_error = PUBLIC_ACTION_SEMANTICS[name]
    expected_result = {
        "public_analyze_completed": "ANALYZE_COMPLETED",
        "public_mixed_boundary_blocked": "MIXED_BOUNDARY_OBSERVED",
        "public_ambiguous_common_only_blocked": "AMBIGUOUS_COMMON_ONLY_OBSERVED",
        "public_scan_manual_review_required": "SCANNED_GEOMETRY_REVIEW_OBSERVED",
        "public_repeated_occurrence_scoped": "REPEATED_OCCURRENCE_SCOPE_OBSERVED",
        "public_review_cards_resolved": "REVIEW_RESOLUTION_OBSERVED",
        "public_manual_combined_resolved": "MANUAL_AND_INTRINSIC_OBSERVED",
        "public_legal_advisory_isolated": "LEGAL_TAGS_ABSENT",
        "public_intrinsic_failure_blocked": "MASKING_SESSION_ORIGINAL_CHANGED",
        "public_unresolved_review_confirmed": "UNRESOLVED_REVIEW_CONFIRMED",
        "public_clean_document_verified": "CLEAN_DOCUMENT_HASH_MATCHED",
        "public_destination_authorized": "DESTINATION_AUTHORIZED",
        "public_destination_token_issued": "SAVE_TOKEN_ISSUED",
        "public_threshold_hash_bound": "THRESHOLD_HASH_BOUND",
        "public_finalize_promoted": "PROMOTED",
    }.get(name, expected_error or "OBSERVATION_CONFIRMED")
    is_hash = lambda value: (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
    def exact(value: object, keys: set[str]) -> bool:
        return isinstance(value, dict) and set(value) == keys

    if not (
        exact(request, {"operationCode", "fixtureHash", "actualRequest", "requestEvidenceHash"})
        and request["operationCode"] == name
        and is_hash(request["fixtureHash"])
        and isinstance(request["actualRequest"], dict)
        and request["requestEvidenceHash"] == canonical_json_hash(request["actualRequest"])
        and exact(result, {"resultCode", "observed", "count", "actualResult", "resultEvidenceHash"})
        and result["observed"] is True
        and result["resultCode"] == expected_result
        and isinstance(result["count"], int)
        and not isinstance(result["count"], bool)
        and result["count"] >= 0
        and isinstance(result["actualResult"], dict)
        and result["resultEvidenceHash"] == canonical_json_hash(result["actualResult"])
        and expected_outcome == action["outcome"]
    ):
        return False

    actual_request = request["actualRequest"]
    actual_result = result["actualResult"]
    hash_fields = {
        "inputHash", "profileHash", "optionsHash", "runIdHash", "requestedManifestHash",
        "saveTokenHash", "destinationHash", "reviewIdHash", "currentManifestHash",
        "manifestHash", "thresholdHash", "thresholdValueHash", "artifactHash", "finalHash", "observedIdentityHash",
        "pendingReviewIdHash", "duplicateValueHash", "distinctPageOrRectHash",
        "linkedOccurrenceHash", "promotedFinalHash", "checkedTagSetHash",
        "sourceBeforeHash", "sourceAfterHash", "resolvedManifestHash", "inputManifestHash",
        "sourceHash",
    }
    def closed(value: dict[str, object], keys: set[str]) -> bool:
        return exact(value, keys) and all(
            is_hash(value[key]) if key in hash_fields else True for key in value
        )

    finalize_actions = {
        "public_unresolved_review_blocked": (
            ("MASKING_SESSION_UNRESOLVED_REVIEW", "UNRESOLVED_REVIEW"), "unresolved_review",
        ),
        "public_stale_revision_blocked": (("MASKING_SESSION_STALE_ANALYSIS",), "stale_revision"),
        "public_stale_manifest_hash_blocked": (("MASKING_SESSION_STALE_ANALYSIS",), "stale_manifest"),
        "public_tampered_manifest_blocked": (("MASKING_SESSION_STALE_ANALYSIS",), "tampered_manifest"),
        "public_destination_bypass_blocked": (("MASKING_SESSION_DESTINATION_REJECTED",), "destination_rejected"),
        "public_atomic_promotion_failure_blocked": (
            ("MASKING_SESSION_PRECOMMIT_RETRYABLE;cause=MASKING_SESSION_PROMOTION_FAILED",),
            "promotion_failed",
        ),
    }
    if name == "public_unresolved_review_confirmed":
        return (
            closed(actual_request, {"operationKind", "runIdHash", "requestedRevision",
                                   "requestedManifestHash", "saveTokenHash", "destinationHash",
                                   "bindingCode", "warningsConfirmed"})
            and closed(actual_result, {"statusCode", "confirmationStatus", "unresolvedReviewCount",
                                       "categoryPageEvidence", "finalHash", "confirmedRunIdHash"})
            and actual_request["operationKind"] == "finalize"
            and actual_request["warningsConfirmed"] is True
            and is_hash(actual_request["runIdHash"])
            and isinstance(actual_result["unresolvedReviewCount"], int)
            and not isinstance(actual_result["unresolvedReviewCount"], bool)
            and actual_result["unresolvedReviewCount"] > 0
            and actual_result["statusCode"] == "unresolved_review_confirmed"
            and actual_result["confirmationStatus"] == "user_confirmed"
            and actual_result["categoryPageEvidence"] is True
            and actual_result["confirmedRunIdHash"] == actual_request["runIdHash"]
            and is_hash(actual_result["confirmedRunIdHash"])
            and result["count"] == 1
        )
    if name == "public_analyze_completed":
        return (
            closed(actual_request, {"inputHash", "profileHash", "optionsHash"})
            and closed(actual_result, {
                "runIdHash", "analysisRevision", "manifestHash", "reviewCount",
                "nameReviewPending", "institutionReviewPending",
                "nonPersonNameCandidateExcluded",
            })
            and actual_request["inputHash"] == request["fixtureHash"]
            and result["count"] == OFFICIAL_DISPATCH_FIXTURE_OCCURRENCE_COUNT
            and isinstance(actual_result["analysisRevision"], int)
            and not isinstance(actual_result["analysisRevision"], bool)
            and actual_result["reviewCount"] == OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT
            and actual_result["nameReviewPending"] is True
            and actual_result["institutionReviewPending"] is True
            and actual_result["nonPersonNameCandidateExcluded"] is True
        )
    if name in finalize_actions:
        error_codes, status_code = finalize_actions[name]
        return (
            closed(actual_request, {"operationKind", "runIdHash", "requestedRevision",
                                   "requestedManifestHash", "saveTokenHash", "destinationHash",
                                   "bindingCode"})
            and closed(actual_result, {"errorCode", "currentRevision", "currentManifestHash",
                                       "statusCode"})
            and actual_request["operationKind"] == "finalize"
            and actual_result["errorCode"] in error_codes
            and actual_request["runIdHash"] == hashlib.sha256(receipt["runId"].encode()).hexdigest()
            and isinstance(actual_result["currentRevision"], int)
            and not isinstance(actual_result["currentRevision"], bool)
            and actual_result["statusCode"] == status_code
            and result["count"] == 1
        )
    if name == "public_forged_resolution_blocked":
        return (
            closed(actual_request, {"operationKind", "runIdHash", "requestedRevision",
                                   "requestedManifestHash", "reviewIdHash", "resolutionKind"})
            and closed(actual_result, {"errorCode", "currentRevision", "currentManifestHash", "statusCode"})
            and actual_request["operationKind"] == "resolve"
            and actual_request["runIdHash"] == hashlib.sha256(receipt["runId"].encode()).hexdigest()
            and isinstance(actual_request["requestedRevision"], int)
            and not isinstance(actual_request["requestedRevision"], bool)
            and actual_request["resolutionKind"] == "acknowledge"
            and actual_result["errorCode"] == "MASKING_SESSION_UNKNOWN_REVIEW"
            and actual_result["statusCode"] == "unknown_review"
            and result["count"] == 1
        )
    if name == "public_mixed_boundary_blocked":
        return (
            closed(actual_request, {"fixtureHash", "manifestHash", "pendingBoundaryCount", "pendingReviewIdHash"})
            and closed(actual_result, {"boundaryBlocked", "pendingBoundaryCount", "pendingReviewCount"})
            and actual_request["fixtureHash"] == request["fixtureHash"]
            and actual_result["boundaryBlocked"] is True
            and isinstance(actual_request["pendingBoundaryCount"], int)
            and actual_request["pendingBoundaryCount"] > 0
            and actual_request["pendingBoundaryCount"] == actual_result["pendingBoundaryCount"] == result["count"]
            and actual_result["pendingReviewCount"] == MIXED_FIXTURE_PENDING_REVIEW_COUNT
        )
    if name == "public_ambiguous_common_only_blocked":
        return (
            closed(actual_request, {"fixtureHash", "manifestHash", "pendingCommonOnlyCount", "pendingReviewIdHash"})
            and closed(actual_result, {"commonOnlyBlocked", "pendingCommonOnlyCount", "pendingReviewCount"})
            and actual_request["fixtureHash"] == request["fixtureHash"]
            and actual_result["commonOnlyBlocked"] is True
            and isinstance(actual_request["pendingCommonOnlyCount"], int)
            and actual_request["pendingCommonOnlyCount"] > 0
            and actual_request["pendingCommonOnlyCount"] == actual_result["pendingCommonOnlyCount"] == result["count"]
            and actual_result["pendingReviewCount"] == AMBIGUOUS_FIXTURE_PENDING_REVIEW_COUNT
        )
    if name == "public_scan_manual_review_required":
        return (
            closed(actual_request, {"inputHash", "profileHash", "optionsHash"})
            and closed(actual_result, {"scanSegmentCount", "pendingScanReviewCount", "manifestHash"})
            and actual_request["inputHash"] == request["fixtureHash"]
            and isinstance(actual_result["scanSegmentCount"], int)
            and actual_result["scanSegmentCount"] > 0
            and isinstance(actual_result["pendingScanReviewCount"], int)
            and actual_result["pendingScanReviewCount"] > 0
            and actual_result["pendingScanReviewCount"] == result["count"]
            and isinstance(actual_result["manifestHash"], str)
            and len(actual_result["manifestHash"]) == 64
        )
    if name == "public_repeated_occurrence_scoped":
        return (
            closed(actual_request, {"inputHash", "duplicateValueHash", "distinctPageOrRectHash"})
            and closed(actual_result, {"duplicateOccurrenceCount", "occurrenceCount", "scoped", "manifestHash"})
            and actual_request["inputHash"] == request["fixtureHash"]
            and actual_result["scoped"] is True
            and isinstance(actual_result["duplicateOccurrenceCount"], int)
            and actual_result["duplicateOccurrenceCount"] > 1
            and actual_result["duplicateOccurrenceCount"] == result["count"]
            and isinstance(actual_result["occurrenceCount"], int)
            and actual_result["occurrenceCount"] >= result["count"]
        )
    if name == "public_review_cards_resolved":
        return (
            closed(actual_request, {"fixtureHash", "pendingBefore", "manifestHash"})
            and closed(actual_result, {"pendingAfter", "resolvedRevision", "resolvedManifestHash"})
            and actual_request["fixtureHash"] == request["fixtureHash"]
            and actual_request["pendingBefore"] == MIXED_FIXTURE_PENDING_REVIEW_COUNT
            and result["count"] == MIXED_FIXTURE_PENDING_REVIEW_COUNT
            and actual_result["pendingAfter"] == 0
            and isinstance(actual_result["resolvedRevision"], int)
            and actual_result["resolvedRevision"] > 0
        )
    if name == "public_manual_combined_resolved":
        return (
            closed(actual_request, {"fixtureHash", "linkedOccurrenceHash", "neighborRefCount", "pendingReviewCount"})
            and closed(actual_result, {"manualActionCount", "pendingReviewCount", "linkedOccurrenceHash", "neighborRefCount", "promotedFinalHash"})
            and actual_request["fixtureHash"] == request["fixtureHash"]
            and actual_request["neighborRefCount"] > 0
            and actual_request["pendingReviewCount"] == MANUAL_OFFICIAL_DISPATCH_FIXTURE_PENDING_REVIEW_COUNT
            and actual_result["manualActionCount"] == 1
            and actual_result["pendingReviewCount"] == 0
            and actual_result["linkedOccurrenceHash"] == actual_request["linkedOccurrenceHash"]
            and actual_result["neighborRefCount"] == actual_request["neighborRefCount"]
            and result["count"] == 1
        )
    if name == "public_legal_advisory_isolated":
        return (
            closed(actual_request, {"fixtureHash", "checkedTagSetHash", "manifestHash"})
            and closed(actual_result, {"matchedCount", "occurrenceCount"})
            and actual_request["fixtureHash"] == request["fixtureHash"]
            and actual_result["matchedCount"] == 0
            and actual_result["occurrenceCount"] == MIXED_FIXTURE_OCCURRENCE_COUNT
            and result["count"] == 0
        )
    if name == "public_intrinsic_failure_blocked":
        return (
            closed(actual_request, {"fixtureHash", "sourceBeforeHash", "sourceAfterHash"})
            and closed(actual_result, {"errorCode", "destinationAbsent"})
            and actual_request["fixtureHash"] == request["fixtureHash"]
            and actual_request["sourceBeforeHash"] != actual_request["sourceAfterHash"]
            and actual_result["errorCode"] == "MASKING_SESSION_ORIGINAL_CHANGED"
            and actual_result["destinationAbsent"] is True
            and result["count"] == 1
        )
    if name == "public_clean_document_verified":
        return (
            closed(actual_request, {"fixtureHash", "inputManifestHash"})
            and closed(actual_result, {"sourceHash", "finalHash", "occurrenceCount", "pendingReviewCount"})
            and actual_request["fixtureHash"] == request["fixtureHash"]
            and actual_result["sourceHash"] == actual_result["finalHash"] == request["fixtureHash"]
            and actual_result["occurrenceCount"] == CLEAN_FIXTURE_OCCURRENCE_COUNT
            and actual_result["pendingReviewCount"] == CLEAN_FIXTURE_PENDING_REVIEW_COUNT
            and result["count"] == CLEAN_FIXTURE_OCCURRENCE_COUNT
        )
    if name == "public_destination_authorized":
        return (
            closed(actual_request, {"destinationHash", "manifestHash", "bindingCode"})
            and closed(actual_result, {"saveTokenHash", "bindingCode"})
            and actual_request["manifestHash"] == receipt["manifestHash"]
            and actual_request["bindingCode"] == "public"
            and actual_result["bindingCode"] == "registered"
            and result["count"] == 1
        )
    if name == "public_destination_token_issued":
        return (
            closed(actual_request, {"manifestHash", "bindingCode"})
            and closed(actual_result, {"saveTokenHash", "nonempty"})
            and actual_request["manifestHash"] == receipt["manifestHash"]
            and actual_request["bindingCode"] == "registered"
            and actual_result["nonempty"] is True
            and result["count"] == 1
        )
    if name == "public_threshold_hash_bound":
        return (
            closed(actual_request, {
                "autoMaskThreshold", "reviewThreshold", "thresholdHash",
                "thresholdValueHash", "thresholdVersion",
            })
            and closed(actual_result, {
                "artifactHash", "autoMaskThreshold", "reviewThreshold", "thresholdValueHash",
            })
            and actual_request["thresholdHash"] == receipt["thresholdHash"]
            and actual_request["thresholdValueHash"] == receipt["thresholdValueHash"]
            and actual_request["thresholdVersion"] == receipt["thresholdVersion"]
            and actual_result["artifactHash"] == receipt["thresholdHash"]
            and actual_result["thresholdValueHash"] == receipt["thresholdValueHash"]
            and actual_request["autoMaskThreshold"] == actual_result["autoMaskThreshold"]
            and actual_request["reviewThreshold"] == actual_result["reviewThreshold"]
            and isinstance(actual_request["autoMaskThreshold"], (int, float))
            and not isinstance(actual_request["autoMaskThreshold"], bool)
            and isinstance(actual_request["reviewThreshold"], (int, float))
            and not isinstance(actual_request["reviewThreshold"], bool)
            and 0 <= actual_request["reviewThreshold"] <= actual_request["autoMaskThreshold"] <= 1
            and result["count"] == 1
        )
    if name == "public_finalize_promoted":
        return (
            closed(actual_request, {"destinationHash", "manifestHash"})
            and closed(actual_result, {"statusCode", "finalHash"})
            and actual_request["manifestHash"] == receipt["manifestHash"]
            and actual_result["statusCode"] == "promoted"
            and result["count"] == 1
        )
    return False


def public_receipt_from_native_stdout(
    channel: str, *, nonce: str, binary_hash: str, scenario: str
) -> dict[str, object] | None:
    try:
        lines = channel.splitlines()
        if len(lines) != 1 or not lines[0] or not channel.endswith("\n"):
            return None
        event = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(event, dict)
        or set(event) != {"event", "source", "receipt"}
        or event.get("event") != "public_action_receipt"
        or event.get("source") != "native"
        or not isinstance(event.get("receipt"), dict)
    ):
        return None
    receipt = event["receipt"]
    expected_keys = {
        "schema", "schemaVersion", "scenario", "nonce", "binaryHash", "runId",
        "analysisRevision", "manifestHash", "thresholdVersion", "thresholdHash",
        "thresholdValueHash", "scenarioSteps", "actions", "canonicalReceiptHash",
        "receiptAuth",
    }
    if (
        set(receipt) != expected_keys
        or receipt["schema"] != "PublicActionReceiptV1"
        or receipt["schemaVersion"] != 1
        or receipt["scenario"] != scenario
        or receipt["nonce"] != nonce
        or receipt["binaryHash"] != binary_hash
        or not isinstance(receipt["nonce"], str)
        or len(receipt["nonce"]) < 32
        or not isinstance(receipt["runId"], str)
        or not receipt["runId"]
        or not isinstance(receipt["analysisRevision"], int)
        or receipt["analysisRevision"] < 1
        or not all(
            isinstance(receipt[field], str) and len(receipt[field]) == 64
            and all(char in "0123456789abcdef" for char in receipt[field].lower())
            for field in ("binaryHash", "manifestHash", "thresholdHash", "thresholdValueHash",
                          "canonicalReceiptHash", "receiptAuth")
        )
        or not isinstance(receipt["thresholdVersion"], str)
        or not receipt["thresholdVersion"]
        or not pii_safe(receipt)
    ):
        return None
    steps = public_steps_for_scenario(scenario)
    if receipt["scenarioSteps"] != list(steps) or not isinstance(receipt["actions"], list):
        return None
    canonical_receipt = {
        key: value for key, value in receipt.items()
        if key not in {"canonicalReceiptHash", "receiptAuth"}
    }
    if receipt["canonicalReceiptHash"] != canonical_json_hash(canonical_receipt):
        return None
    if receipt["receiptAuth"] != receipt_auth(receipt):
        return None
    if len(receipt["actions"]) != len(steps):
        return None
    for expected_name, action in zip(steps, receipt["actions"], strict=True):
        if not isinstance(action, dict) or set(action) != {
            "name", "outcome", "errorCode", "requestEvidence", "resultEvidence",
            "requestHash", "resultHash"
        }:
            return None
        expected_outcome, expected_error = PUBLIC_ACTION_SEMANTICS[expected_name]
        if (
            action["name"] != expected_name
            or action["outcome"] != expected_outcome
            or action["errorCode"] != expected_error
            or not valid_action_evidence(receipt, action)
            or not all(
                isinstance(action[field], str)
                and len(action[field]) == 64
                and all(char in "0123456789abcdef" for char in action[field])
                for field in ("requestHash", "resultHash")
            )
            or action["requestHash"] == action["resultHash"]
            or action["requestHash"] != receipt_action_hash(receipt, action, "request")
            or action["resultHash"] != receipt_action_hash(receipt, action, "result")
        ):
            return None
    return receipt




def acceptance_report(
    actions: list[dict[str, str]],
    steps: tuple[str, ...],
    prerequisites: dict[str, tuple[str, ...]],
    scope: str,
) -> dict[str, object]:
    action_by_name = {
        str(action.get("name", "")).strip(): {
            "status": str(action.get("status", "")).strip(),
            "evidence": str(action.get("evidence", "")).strip(),
        }
        for action in actions
        if str(action.get("name", "")).strip()
    }
    proven: list[str] = []
    blocked: list[str] = []
    blockers: list[dict[str, str]] = []
    for step in steps:
        action = action_by_name.get(step)
        if not action:
            blocked.append(step)
            blockers.append(
                {
                    "step": step,
                    "reason": "MISSING_RUNTIME_RECEIPT",
                    "evidence": "NONE",
                }
            )
            continue
        raw_status = action["status"]
        evidence = action["evidence"]
        if raw_status == "pass":
            missing_prerequisites = [
                prerequisite for prerequisite in prerequisites.get(step, ()) if prerequisite not in proven
            ]
            if missing_prerequisites:
                blocked.append(step)
                blockers.append(
                    {
                        "step": step,
                        "reason": f"missing prerequisite evidence: {', '.join(missing_prerequisites)}",
                        "evidence": evidence,
                    }
                )
            else:
                proven.append(step)
        elif raw_status in {"blocked", "fail"}:
            blocked.append(step)
            blockers.append({"step": step, "reason": raw_status, "evidence": evidence})
        else:
            blocked.append(step)
            blockers.append({"step": step, "reason": f"unknown status: {raw_status}", "evidence": evidence})

    not_proven = [step for step in steps if step not in proven]
    return {
        "status": "pass" if not not_proven else ("partial" if proven else "fail"),
        "scope": scope,
        "proven": proven,
        "blocked": blocked,
        "not_proven": not_proven,
        "blockers": blockers,
        "actions": [{"name": action.get("name"), "status": action.get("status"), "evidence": action.get("evidence")}
                    for action in actions],
    }


def native_gui_acceptance_report(actions: list[dict[str, str]]) -> dict[str, object]:
    if not isinstance(actions, TrustedNativeActions):
        actions = TrustedNativeActions(
            {
                "name": "native_action_rejected",
                "status": "invalid",
                "evidence": "CALLER_AUTHORED_NATIVE_EVIDENCE_REJECTED",
            }
            for _ in actions
        )
    return acceptance_report(
        actions,
        NATIVE_ACCEPTANCE_STEPS,
        NATIVE_ACCEPTANCE_PREREQUISITES,
        "native packaged app GUI acceptance",
    )




def parse_computer_use_results(raw_results: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for raw_result in raw_results:
        app, separator, result = raw_result.partition("=")
        if not separator:
            parsed.append({"app": raw_result.strip(), "result": ""})
            continue
        parsed.append({"app": app.strip(), "result": result.strip()})
    return parsed


def executable_for_app(app_path: Path) -> Path:
    if app_path.suffix == ".app":
        macos_dir = app_path / "Contents" / "MacOS"
        executables = [path for path in macos_dir.iterdir() if path.is_file() and os.access(path, os.X_OK)]
        if not executables:
            raise RuntimeError(f"실행 파일을 찾을 수 없습니다: {macos_dir}")
        return executables[0]
    return app_path
def executable_sha256(executable: Path) -> str:
    if executable.is_symlink() or not executable.is_file():
        raise ValueError("RUNTIME_EXECUTABLE_UNAVAILABLE")
    return hashlib.sha256(executable.read_bytes()).hexdigest()


def launch_public_native_receipt(
    app_path: Path,
    *,
    scenario: str,
    nonce: str,
    timeout: float,
    threshold_binding: dict[str, object] | None = None,
) -> tuple[dict[str, object] | None, str]:
    if threshold_binding is None:
        return None, "NATIVE_RECEIPT_THRESHOLD_BINDING_UNAVAILABLE"
    required_binding_fields = (
        "thresholdVersion",
        "thresholdHash",
        "thresholdValueHash",
        "autoMaskThreshold",
        "reviewThreshold",
    )
    if (
        not isinstance(threshold_binding, dict)
        or any(field not in threshold_binding for field in required_binding_fields)
    ):
        return None, "NATIVE_RECEIPT_THRESHOLD_BINDING_INVALID"
    executable = executable_for_app(app_path)
    try:
        binary_hash_before = executable_sha256(executable)
        completed = subprocess.run(
            [str(executable), "--public-native-qa-stdin"],
            input=json.dumps(
                {
                    "schemaVersion": 1,
                    "scenario": scenario,
                    "nonce": nonce,
                    "thresholdVersion": threshold_binding["thresholdVersion"],
                    "thresholdHash": threshold_binding["thresholdHash"],
                    "thresholdValueHash": threshold_binding["thresholdValueHash"],
                    "autoMaskThreshold": threshold_binding["autoMaskThreshold"],
                    "reviewThreshold": threshold_binding["reviewThreshold"],
                },
                separators=(",", ":"),
            ),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        binary_hash_after = executable_sha256(executable)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None, "NATIVE_RECEIPT_LAUNCH_FAILED"
    if completed.returncode != 0:
        return None, "NATIVE_RECEIPT_NONZERO_EXIT"
    if binary_hash_before != binary_hash_after:
        return None, "NATIVE_EXECUTABLE_MUTATED"
    receipt = public_receipt_from_native_stdout(
        completed.stdout,
        nonce=nonce,
        binary_hash=binary_hash_before,
        scenario=scenario,
    )
    if receipt is None:
        return None, "NATIVE_RECEIPT_REJECTED"
    return receipt, "NATIVE_RECEIPT_CAPTURED"




def macos_cg_window_snapshot(labels: list[str]) -> tuple[int, str]:
    script = "\n".join(
        [
            "import CoreGraphics",
            "import Foundation",
            "let rawLabels = ProcessInfo.processInfo.environment[\"MAKIIING_WINDOW_LABELS\"] ?? \"[]\"",
            "let labelData = rawLabels.data(using: .utf8) ?? Data()",
            "let labels = (try? JSONSerialization.jsonObject(with: labelData)) as? [String] ?? []",
            "let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []",
            "var total = 0",
            "for window in windows {",
            "  let owner = window[kCGWindowOwnerName as String] as? String ?? \"\"",
            "  let name = window[kCGWindowName as String] as? String ?? \"\"",
            "  let layer = window[kCGWindowLayer as String] as? Int ?? -1",
            "  let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]",
            "  let width = (bounds[\"Width\"] as? NSNumber)?.doubleValue ?? 0",
            "  let height = (bounds[\"Height\"] as? NSNumber)?.doubleValue ?? 0",
            "  let alpha = (window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1",
            "  let matched = labels.contains { !$0.isEmpty && (owner == $0 || owner.contains($0) || name == $0 || name.contains($0)) }",
            "  if matched && layer == 0 && width >= 200 && height >= 120 && alpha > 0 {",
            "    total += 1",
            "  }",
            "}",
            "print(total)",
        ]
    )
    env = os.environ.copy()
    env["MAKIIING_WINDOW_LABELS"] = json.dumps(labels, ensure_ascii=False)
    try:
        result = subprocess.run(
            ["swift", "-e", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return 0, "COREGRAPHICS_SNAPSHOT_TIMEOUT"
    if result.returncode != 0:
        return 0, "COREGRAPHICS_SNAPSHOT_FAILED"
    try:
        count = int(result.stdout.strip())
    except ValueError:
        return 0, "COREGRAPHICS_SNAPSHOT_INVALID"
    return (count, "COREGRAPHICS_RENDERABLE_WINDOW_OBSERVED") if count > 0 else (0, "COREGRAPHICS_NO_MATCH")


def macos_largest_cg_window_id(labels: list[str]) -> tuple[str, str]:
    script = "\n".join(
        [
            "import CoreGraphics",
            "import Foundation",
            "let rawLabels = ProcessInfo.processInfo.environment[\"MAKIIING_WINDOW_LABELS\"] ?? \"[]\"",
            "let labelData = rawLabels.data(using: .utf8) ?? Data()",
            "let labels = (try? JSONSerialization.jsonObject(with: labelData)) as? [String] ?? []",
            "let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []",
            "var bestId = 0",
            "var bestArea = 0.0",
            "for window in windows {",
            "  let owner = window[kCGWindowOwnerName as String] as? String ?? \"\"",
            "  let name = window[kCGWindowName as String] as? String ?? \"\"",
            "  let windowId = window[kCGWindowNumber as String] as? Int ?? 0",
            "  let layer = window[kCGWindowLayer as String] as? Int ?? -1",
            "  let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]",
            "  let width = (bounds[\"Width\"] as? NSNumber)?.doubleValue ?? 0",
            "  let height = (bounds[\"Height\"] as? NSNumber)?.doubleValue ?? 0",
            "  let alpha = (window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1",
            "  let matched = labels.contains { !$0.isEmpty && (owner == $0 || owner.contains($0) || name == $0 || name.contains($0)) }",
            "  let area = width * height",
            "  if matched && layer == 0 && width >= 200 && height >= 120 && alpha > 0 && area > bestArea {",
            "    bestId = windowId",
            "    bestArea = area",
            "  }",
            "}",
            "print(bestId)",
        ]
    )
    env = os.environ.copy()
    env["MAKIIING_WINDOW_LABELS"] = json.dumps(labels, ensure_ascii=False)
    try:
        result = subprocess.run(
            ["swift", "-e", script],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return "", "COREGRAPHICS_WINDOW_LOOKUP_TIMEOUT"
    if result.returncode != 0:
        return "", "COREGRAPHICS_WINDOW_LOOKUP_FAILED"
    window_id = result.stdout.strip()
    if not window_id.isdigit() or window_id == "0":
        return "", "COREGRAPHICS_NO_MATCH"
    return window_id, "COREGRAPHICS_LARGEST_RENDERABLE_WINDOW_SELECTED"


def macos_cg_window_capture(labels: list[str], out_path: Path) -> dict[str, str]:
    if platform.system() != "Darwin":
        return {"status": "skipped", "code": "SCREEN_CAPTURE_PLATFORM_UNSUPPORTED"}
    window_id, lookup_code = macos_largest_cg_window_id(labels)
    if not window_id:
        return {"status": "fail", "code": lookup_code}
    try:
        parent = out_path.parent.resolve(strict=True)
        if out_path.is_symlink():
            return {"status": "fail", "code": "SCREEN_CAPTURE_DESTINATION_REJECTED"}
        temporary = parent / f".native-screenshot-{os.getpid()}-{time.time_ns()}.png"
        descriptor = os.open(
            temporary,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
    except OSError:
        return {"status": "fail", "code": "SCREEN_CAPTURE_DESTINATION_REJECTED"}
    try:
        try:
            result = subprocess.run(
                ["screencapture", "-x", "-l", window_id, str(temporary)],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=15,
            )
        except subprocess.TimeoutExpired:
            return {"status": "fail", "code": "SCREEN_CAPTURE_TIMEOUT"}
        if result.returncode != 0:
            return {"status": "fail", "code": "SCREEN_CAPTURE_FAILED"}
        screenshot = temporary.read_bytes()
        if len(screenshot) <= 8:
            return {"status": "fail", "code": "SCREEN_CAPTURE_OUTPUT_EMPTY"}
        if screenshot[:8] != b"\x89PNG\r\n\x1a\n":
            return {"status": "fail", "code": "SCREEN_CAPTURE_OUTPUT_INVALID"}
        os.replace(temporary, out_path)
    except OSError:
        return {"status": "fail", "code": "SCREEN_CAPTURE_OUTPUT_MISSING"}
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {"status": "pass", "artifact": "NATIVE_SCREENSHOT_CAPTURED"}


def macos_window_snapshot(labels: list[str]) -> tuple[int, str]:
    cg_count, cg_observation = macos_cg_window_snapshot(labels)
    if cg_count > 0:
        return cg_count, cg_observation
    if cg_observation != "COREGRAPHICS_NO_MATCH":
        return 0, cg_observation

    script = "\n".join(
        [
            "on run argv",
            "  tell application \"System Events\"",
            "    set totalWindows to 0",
            "    repeat with p in every process",
            "      set pname to name of p",
            "      set matched to false",
            "      repeat with labelText in argv",
            "        if labelText is not \"\" and (pname is labelText or pname contains labelText) then",
            "          set matched to true",
            "        end if",
            "      end repeat",
            "      if matched then",
            "        set totalWindows to totalWindows + (count of windows of p)",
            "      end if",
            "    end repeat",
            "    return totalWindows as text",
            "  end tell",
            "end run",
        ]
    )
    try:
        result = subprocess.run(
            ["osascript", "-"] + labels, input=script, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return 0, "COREGRAPHICS_NO_MATCH;ACCESSIBILITY_TIMEOUT"
    if result.returncode != 0:
        return 0, "COREGRAPHICS_NO_MATCH;ACCESSIBILITY_FAILED"
    try:
        return int(result.stdout.strip()), "COREGRAPHICS_NO_MATCH;ACCESSIBILITY_COMPATIBILITY_CHECKED"
    except ValueError:
        return 0, "COREGRAPHICS_NO_MATCH;ACCESSIBILITY_INVALID"


def macos_process_labels(repo_root: Path, app_path: Path, executable: Path) -> list[str]:
    config = load_tauri_config(repo_root)
    labels: list[str] = [app_path.stem, executable.name]
    product_name = str(config.get("productName", "")).strip()
    if product_name:
        labels.append(product_name)

    app_config = config.get("app")
    if isinstance(app_config, dict):
        windows = app_config.get("windows")
        if isinstance(windows, list):
            for window in windows:
                if not isinstance(window, dict):
                    continue
                title = str(window.get("title", "")).strip()
                if title:
                    labels.append(title)

    deduped: list[str] = []
    for label in labels:
        if label and label not in deduped:
            deduped.append(label)
    return deduped


def run_macos_app_smoke(
    repo_root: Path,
    app_path: Path,
    executable: Path,
    seconds: float,
) -> dict[str, object]:
    labels = macos_process_labels(repo_root, app_path, executable)
    launch = subprocess.Popen(
        [str(executable)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    window_count = 0
    try:
        deadline = time.time() + seconds
        while time.time() < deadline:
            window_count, _ = macos_window_snapshot(labels)
            if window_count > 0:
                break
            time.sleep(0.25)
        if launch.poll() is None:
            launch.terminate()
        try:
            stdout, stderr = launch.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            launch.kill()
            stdout, stderr = launch.communicate(timeout=5)
    finally:
        if launch.poll() is None:
            launch.kill()
            launch.communicate(timeout=5)
    diagnostics = ["RUNTIME_OUTPUT_SUPPRESSED"] if stdout or stderr else []
    if launch.returncode not in (0, -15) or window_count <= 0:
        return {
            "status": "fail",
            "exit_code": launch.returncode,
            "diagnostics": diagnostics + ["VISIBLE_WINDOW_NOT_OBSERVED"],
            "scope": "startup/render smoke with visible-window check",
        }
    return {
        "status": "pass",
        "diagnostics": diagnostics,
        "scope": "startup/render smoke with visible-window check",
        "not_proven": ["OS file picker", "drag masking", "final save"],
    }


def run_smoke(app_path: Path, seconds: float) -> dict[str, object]:
    executable = executable_for_app(app_path)
    if not executable.exists():
        raise ValueError("RUNTIME_EXECUTABLE_UNAVAILABLE")

    if platform.system() == "Darwin" and app_path.suffix == ".app":
        repo_root = Path(__file__).resolve().parents[1]
        return run_macos_app_smoke(repo_root, app_path, executable, seconds)

    process = subprocess.Popen(
        [str(executable)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        time.sleep(seconds)
        alive = process.poll() is None
        if alive:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    diagnostics = ["RUNTIME_OUTPUT_SUPPRESSED"] if stdout or stderr else []
    if not alive:
        return {
            "status": "fail",
            "exit_code": process.returncode,
            "diagnostics": diagnostics + ["RUNTIME_EXITED_EARLY"],
            "scope": "startup/render smoke only",
        }
    return {
        "status": "pass",
        "diagnostics": diagnostics,
        "scope": "startup/render smoke only",
        "not_proven": ["OS file picker", "drag masking", "final save"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--app-path", default="")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--out", default="")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--bundle-id", default="")
    parser.add_argument("--bundle-search-root", action="append", default=[])
    parser.add_argument("--computer-use-result", action="append", default=[])
    parser.add_argument("--cg-window-count", type=int, default=0)
    parser.add_argument("--ax-window-count", type=int, default=0)
    parser.add_argument("--native-action", action="append", default=[])
    parser.add_argument("--runtime-receipt-channel", default="")
    parser.add_argument("--receipt-nonce", default="")
    parser.add_argument("--threshold-artifact", default="")
    parser.add_argument("--threshold-digest", default="")
    parser.add_argument("--native-screenshot-out", default="")
    parser.add_argument(
        "--scenario",
        choices=("legal-advisory", "public-document-plumbing", "public-document-all"),
        default="legal-advisory",
    )
    args = parser.parse_args()
    if not 0 < args.seconds <= 60:
        parser.error("--seconds must be greater than zero and at most 60")
    repo_root = Path(args.repo_root).resolve()
    app_path = Path(args.app_path).resolve() if args.app_path else default_app_path(repo_root)
    output_path = args.out or args.evidence
    try:
        result = run_smoke(app_path, args.seconds)
    except Exception:
        result = {
            "status": "fail",
            "diagnostics": ["RUNTIME_LAUNCH_FAILED"],
            "scope": "startup/render smoke only",
        }
    runtime_status = str(result.get("status", "fail"))
    bundle_report: dict[str, object] | None = None
    if args.bundle_id:
        search_roots = [Path(path).resolve() for path in args.bundle_search_root]
        if not search_roots:
            search_roots = [
                repo_root / "src-tauri" / "target" / "release" / "bundle" / "macos",
                Path.home() / "Downloads",
                repo_root / ".omx" / "disabled-duplicate-apps",
            ]
        bundle_report = macos_bundle_id_report(search_roots, args.bundle_id)
        result["bundle_report"] = {
            key: bundle_report[key]
            for key in ("status", "active_app_count", "disabled_backup_count", "ambiguous")
        }

    computer_use_results = parse_computer_use_results(args.computer_use_result)
    if computer_use_results:
        result["attach_diagnosis"] = computer_use_attach_diagnosis(
            active_app_count=int(bundle_report["active_app_count"]) if bundle_report else 0,
            disabled_backup_count=int(bundle_report["disabled_backup_count"]) if bundle_report else 0,
            cg_window_count=args.cg_window_count,
            ax_window_count=args.ax_window_count,
            computer_use_results=computer_use_results,
        )
        if result["attach_diagnosis"]["status"] != "computer-use-attached":
            result["status"] = "fail"
    native_actions = parse_native_actions(args.native_action)
    if native_actions:
        result["native_gui_acceptance"] = native_gui_acceptance_report(native_actions)
        result["status"] = "fail"
    if args.scenario.startswith("public-document-"):
        steps = public_steps_for_scenario(args.scenario)
        receipt: dict[str, object] | None = None
        receipt_diagnostic = "NATIVE_RECEIPT_REQUEST_INVALID"
        if len(args.receipt_nonce) >= 32:
            channel_path = Path(args.runtime_receipt_channel).expanduser()
            if args.runtime_receipt_channel and (
                os.path.islink(channel_path) or channel_path.exists()
            ):
                receipt_diagnostic = "RUNTIME_RECEIPT_CHANNEL_PREEXISTS"
            else:
                threshold_binding, threshold_diagnostic = load_threshold_binding(
                    args.threshold_artifact, args.threshold_digest
                )
                if threshold_binding is not None:
                    receipt, receipt_diagnostic = launch_public_native_receipt(
                        app_path,
                        scenario=args.scenario,
                        nonce=args.receipt_nonce,
                        timeout=PUBLIC_NATIVE_RECEIPT_TIMEOUTS[args.scenario],
                        threshold_binding=threshold_binding,
                    )
                else:
                    receipt_diagnostic = threshold_diagnostic
        if receipt is None:
            result["public_document_lifecycle"] = acceptance_report(
                [], steps, {}, "direct packaged native receipt"
            )
            result["public_document_lifecycle"]["receipt_diagnostic"] = receipt_diagnostic
            result["status"] = "fail"
        else:
            # Receipt parsing above already requires each action's exact scenario-specific
            # outcome/error pair. An expected fail-closed outcome therefore proves its
            # contract instead of becoming a harness blocker.
            receipt_actions = TrustedNativeActions(
                {
                    "name": action["name"],
                    "status": "pass",
                    "evidence": "DIRECT_PACKAGED_NATIVE_RECEIPT",
                }
                for action in receipt["actions"]
            )
            result["public_document_lifecycle"] = acceptance_report(
                receipt_actions,
                steps,
                {},
                "direct packaged native Analyze→Resolve→Finalize receipt",
            )
            result["public_action_receipt"] = receipt
            result["harness_receipt_hash"] = canonical_json_hash(receipt)
            threshold_valid = validate_threshold_artifact(
                args.threshold_artifact, args.threshold_digest, receipt
            )
            if (
                result["public_document_lifecycle"]["status"] != "pass"
                or not threshold_valid
            ):
                result["status"] = "fail"
            elif args.runtime_receipt_channel:
                try:
                    atomic_write_evidence(
                        args.runtime_receipt_channel,
                        json.dumps(
                            {
                                "event": "public_action_receipt",
                                "source": "native",
                                "receipt": receipt,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    )
                except ValueError:
                    result["status"] = "fail"
                    result["public_document_lifecycle"][
                        "receipt_diagnostic"
                    ] = "RUNTIME_RECEIPT_CHANNEL_WRITE_REJECTED"
    if args.scenario.startswith("public-document-") and "public_document_lifecycle" not in result:
        steps = public_steps_for_scenario(args.scenario)
        public_report = {
            "status": "fail",
            "scope": "independent trusted public-document authority",
            "proven": [],
            "blocked": list(steps),
            "not_proven": list(steps),
            "blockers": [
                {
                    "step": step,
                    "reason": "INDEPENDENT_TRUSTED_EVIDENCE_UNAVAILABLE",
                    "evidence": "none",
                }
                for step in steps
            ],
            "actions": [],
        }
        result["public_document_lifecycle"] = public_report
        result["scenario"] = args.scenario
        result["status"] = "fail"
    if args.native_screenshot_out:
        try:
            executable = executable_for_app(app_path)
            result["native_screenshot"] = macos_cg_window_capture(
                macos_process_labels(repo_root, app_path, executable),
                Path(args.native_screenshot_out),
            )
        except Exception:
            result["native_screenshot"] = {"status": "fail", "code": "SCREEN_CAPTURE_UNAVAILABLE"}
    if args.scenario.startswith("public-document-"):
        public_projection = {
            "status": result["status"],
            "scenario": args.scenario,
            "runtime": {"status": runtime_status},
            "public_document_lifecycle": result["public_document_lifecycle"],
            "public_action_receipt": result.get("public_action_receipt"),
            "harness_receipt_hash": result.get("harness_receipt_hash"),
        }
        if not pii_safe(public_projection):
            public_projection["status"] = "fail"
            public_projection["runtime"] = {
                "status": runtime_status,
                "code": "PUBLIC_REPORT_PII_REJECTED",
            }
        public_projection["pii_safe"] = pii_safe(public_projection)
        result = public_projection
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output_path:
        atomic_write_evidence(output_path, text)
    print(text)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
