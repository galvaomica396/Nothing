#!/usr/bin/env python3
"""Independent review and atomic immutable lock for PII-free public gold."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import re
import zipfile
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from masking_evaluation import (EVALUATION_PROTOCOL_VERSION, ManifestValidationError, ProtocolValidationError,
    canonical_json_bytes, canonical_json_sha256, create_split_lock, file_sha256, lock_manifest,
    validate_sidecar_security, validate_split_lock, verify_protocol_receipt)

MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ZIP_RATIO = 100
SCHEMA_LOCK_STAGE = "synthetic_schema_lock"
_INDEX_KEYS = {"schema_version", "schema_receipt", "documents", "split_sha256", "gold_identity_sha256", "status", "immutable", "index_sha256"}
_DOCUMENT_KEYS = {"document_sha256", "sidecar", "source_class", "form", "profile"}


_OPAQUE_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _subject_id(value: object) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_SUBJECT.fullmatch(value))


class PublicGoldReviewError(ValueError):
    pass


def _load(path: Path) -> dict:
    if path.is_symlink():
        raise PublicGoldReviewError("top-level verifier artifacts may not be symlinks")
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise PublicGoldReviewError("required JSON artifact is unreadable") from error
    if not isinstance(value, dict): raise PublicGoldReviewError("required JSON artifact must be an object")
    return value


def _outside_repo(path: Path) -> Path:
    resolved, repo = path.expanduser().resolve(), Path(__file__).resolve().parents[1]
    if resolved == repo or repo in resolved.parents or ".gjc" in resolved.parts:
        raise PublicGoldReviewError("public-gold artifacts must be outside repository and .gjc")
    return resolved


def _root(path: str | Path) -> Path:
    root = _outside_repo(Path(path))
    if not root.is_dir(): raise PublicGoldReviewError("explicit evaluation root is unavailable")
    return root


def _relative(root: Path, path: Path) -> str:
    try: return str(path.resolve().relative_to(root))
    except ValueError as error: raise PublicGoldReviewError("artifact must live below explicit evaluation root") from error


def _schema_receipt(root: Path, path: Path) -> dict:
    _relative(root, path)
    try: receipt = verify_protocol_receipt(root, path, artifact_kind=SCHEMA_LOCK_STAGE, required_status="locked")
    except ProtocolValidationError as error: raise PublicGoldReviewError("schema receipt must be a verified locked schema-lock receipt under evaluation root") from error
    outputs = receipt.get("outputs")
    if (not isinstance(outputs, list) or len(outputs) != 1
            or outputs[0].get("role") != "output" or outputs[0].get("kind") != "schema"):
        raise PublicGoldReviewError("schema receipt must bind exactly one schema output")
    return {"receipt_sha256": receipt["receipt_sha256"], "schema_output_sha256": outputs[0]["sha256"]}

def _annotation_receipt(root: Path, source: Path, index: dict) -> dict:
    receipt_path = source / "receipt.json"
    _relative(root, receipt_path)
    try:
        receipt = verify_protocol_receipt(
            root, receipt_path, artifact_kind="public_gold_annotation",
            required_status="draft_initialized",
        )
    except ProtocolValidationError as error:
        raise PublicGoldReviewError("annotation receipt is missing or invalid") from error
    authorization, outputs, counts, inputs = (
        receipt.get("authorization"), receipt.get("outputs"), receipt.get("counts"), receipt.get("inputs")
    )
    expected_inputs = [
        {"path": _relative(root, source / "public-folds.json"), "sha256": file_sha256(source / "public-folds.json"), "role": "input", "kind": "split_lock"},
    ]
    expected_inputs.extend(
        {"path": _relative(root, source / item["sidecar"]), "sha256": file_sha256(source / item["sidecar"]), "role": "input", "kind": "annotation_sidecar"}
        for item in index["documents"]
    )
    if (receipt.get("protocolRunId") != index.get("gold_identity_sha256")
            or not isinstance(authorization, dict)
            or not isinstance(authorization.get("author_digest"), str)
            or authorization.get("author_digest") != hashlib.sha256(
                next(
                    _load(source / item["sidecar"])["provenance"]["author"]["id"]
                    for item in index["documents"]
                ).encode("utf-8")
            ).hexdigest()
            or authorization.get("role") != "annotator"
            or authorization.get("decision") != "reviewed"
            or authorization.get("independent") is not False
            or authorization.get("content_sha256") != file_sha256(source / "public-gold-index.json")
            or not isinstance(outputs, list) or len(outputs) != 1
            or outputs[0].get("role") != "output" or outputs[0].get("kind") != "gold_index"
            or outputs[0].get("sha256") != file_sha256(source / "public-gold-index.json")
            or counts != {"document_count": 25, "issued_count": 15, "review_count": 10}
            or not isinstance(inputs, list) or len(inputs) != len(expected_inputs) + 1
            or any(item not in inputs for item in expected_inputs)
            or sum(
                item.get("role") == "input" and item.get("kind") == "schema_receipt"
                for item in inputs if isinstance(item, dict)
            ) != 1):
        raise PublicGoldReviewError("annotation receipt binding is invalid")
    return receipt


def _sha_stream(stream, limit: int) -> str:
    digest, total = hashlib.sha256(), 0
    while block := stream.read(1024 * 1024):
        total += len(block)
        if total > limit: raise PublicGoldReviewError("runtime PDF exceeds safe size limit")
        digest.update(block)
    return digest.hexdigest()


def _runtime_source_path(root: Path, locator: str) -> Path:
    path = Path(locator)
    if (not locator or path.is_absolute() or "\\" in locator
            or any(part in {"", ".", ".."} for part in path.parts)):
        raise ValueError("unsafe runtime locator")
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("symlink runtime locator")
    resolved = current.resolve(strict=True)
    resolved.relative_to(root)
    return resolved


def _locator_sha(root: Path, locator: str) -> str:
    if locator.startswith("zip:"):
        try:
            archive_name, member_name = locator[4:].split("!/", 1)
            archive_path = _runtime_source_path(root, archive_name)
            member = PurePosixPath(member_name)
            if (not member_name or member.is_absolute() or any(part in {".", ".."} for part in member.parts)
                    or "\\" in member_name):
                raise ValueError("unsafe member")
            with zipfile.ZipFile(archive_path) as archive:
                info = archive.getinfo(member_name)
                if (stat.S_ISLNK(info.external_attr >> 16) or info.file_size > MAX_PDF_BYTES
                        or info.compress_size == 0 or info.file_size / info.compress_size > MAX_ZIP_RATIO
                        or info.file_size > MAX_ZIP_TOTAL_BYTES):
                    raise PublicGoldReviewError("runtime ZIP member exceeds safe limits")
                with archive.open(info) as stream:
                    return _sha_stream(stream, MAX_PDF_BYTES)
        except (ValueError, OSError, zipfile.BadZipFile, KeyError) as error:
            raise PublicGoldReviewError("review source is unavailable for hash recheck") from error
    try:
        path = _runtime_source_path(root, locator)
    except (OSError, ValueError) as error:
        raise PublicGoldReviewError("issued source is unavailable for hash recheck") from error
    if not path.is_file():
        raise PublicGoldReviewError("issued source is unavailable for hash recheck")
    with path.open("rb") as stream:
        return _sha_stream(stream, MAX_PDF_BYTES)


def _validate_sidecar_security(sidecar: object) -> None:
    try:
        validate_sidecar_security(sidecar)
    except ManifestValidationError as error:
        raise PublicGoldReviewError(str(error)) from error


def _validate_index(index: dict) -> tuple[dict, list[dict]]:
    if set(index) != _INDEX_KEYS or index.get("schema_version") != "PublicGoldBatchV2" or index.get("status") != "draft_unreviewed" or index.get("immutable") is not True:
        raise PublicGoldReviewError("draft index schema is closed or altered")
    identity = {key: index[key] for key in ("schema_version", "schema_receipt", "documents", "split_sha256")}
    if index.get("index_sha256") != canonical_json_sha256(identity) or index.get("gold_identity_sha256") != canonical_json_sha256(identity):
        raise PublicGoldReviewError("draft index identity is altered")
    docs = index["documents"]
    if not isinstance(docs, list) or len(docs) != 25: raise PublicGoldReviewError("draft index document inventory is invalid")
    hashes = set()
    for item in docs:
        if not isinstance(item, dict) or set(item) != _DOCUMENT_KEYS or item.get("source_class") not in {"issued", "review"}:
            raise PublicGoldReviewError("index document fields are invalid")
        if item["form"] != item["source_class"] or item["profile"] != ("official_dispatch" if item["source_class"] == "issued" else "internal_review"):
            raise PublicGoldReviewError("index document classification mismatch")
        digest, sidecar = item.get("document_sha256"), item.get("sidecar")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest) or sidecar != f"{digest}.json" or digest in hashes:
            raise PublicGoldReviewError("index document identity mismatch")
        hashes.add(digest)
    if sum(item["source_class"] == "issued" for item in docs) != 15 or sum(item["source_class"] == "review" for item in docs) != 10:
        raise PublicGoldReviewError("canonical pilot composition mismatch")
    return identity, docs


def _validate_locator_map(path: Path, gold_identity: str, documents: list[dict]) -> dict:
    value = _load(path)
    payload = {key: value[key] for key in ("schema_version", "gold_identity_sha256", "locators")} if set(value) == {"schema_version", "gold_identity_sha256", "locators", "locator_map_sha256"} else None
    if payload is None or value.get("schema_version") != "PrivateRuntimeLocatorMapV1" or value.get("gold_identity_sha256") != gold_identity or value.get("locator_map_sha256") != canonical_json_sha256(payload):
        raise PublicGoldReviewError("private locator map is invalid")
    locators = value["locators"]
    expected = {item["document_sha256"] for item in documents}
    if not isinstance(locators, dict) or set(locators) != expected or not all(isinstance(item, str) and item for item in locators.values()):
        raise PublicGoldReviewError("private locator map coverage is invalid")
    return locators


def _validate_sidecar(sidecar: dict, entry: dict, reviewer_id: str, annotation_author_digest: str) -> dict:
    _validate_sidecar_security(sidecar)
    if sidecar.get("annotation_status") != "draft_unreviewed": raise PublicGoldReviewError("sidecar schema is closed or not a draft")
    if sidecar.get("source_class") != entry["source_class"] or sidecar.get("form") != entry["form"] or sidecar.get("profile") != entry["profile"]:
        raise PublicGoldReviewError("sidecar classification mismatch")
    document, provenance = sidecar.get("document"), sidecar.get("provenance")
    if not isinstance(document, dict) or document.get("document_id") != entry["document_sha256"] or document.get("input_sha256") != entry["document_sha256"]:
        raise PublicGoldReviewError("sidecar document identity mismatch")
    if not isinstance(provenance, dict) or provenance.get("detector_output_imported") is not False:
        raise PublicGoldReviewError("detector-generated gold is forbidden")
    author = provenance.get("author", {}).get("id") if isinstance(provenance.get("author"), dict) else None
    if (not isinstance(author, str) or not author or author == reviewer_id
            or hashlib.sha256(author.encode("utf-8")).hexdigest() != annotation_author_digest):
        raise PublicGoldReviewError("reviewer must differ from and annotation author must match receipt")
    completion = sidecar.get("annotation_completion")
    collections = ("pages", "segments", "regions", "occurrences", "negatives", "protected_neighbors")
    if not isinstance(completion, dict) or set(completion) != set(collections): raise PublicGoldReviewError("all annotation scopes require explicit completion attestations")
    for scope in collections:
        values, attestation = sidecar.get(scope), completion[scope]
        if not isinstance(values, list) or attestation not in {"completed", "none_confirmed"} or (values and attestation != "completed") or (not values and attestation != "none_confirmed"):
            raise PublicGoldReviewError("annotation completion attestation does not match scope")
    if not sidecar["pages"] or not sidecar["segments"]: raise PublicGoldReviewError("annotation requires explicit page and segment coverage")
    sidecar["provenance"]["reviewer"] = {"id": reviewer_id, "decision": "approved", "adjudication": "independent_review"}
    sidecar["annotation_status"] = "reviewed_approved"
    try: return lock_manifest(sidecar)
    except ManifestValidationError as error: raise PublicGoldReviewError("annotation is incomplete or invalid") from error


def _receipt(root: Path, final_output: Path, staged_manifest: Path, manifest: dict, schema_receipt: Path,
             annotation_receipt: Path, source: Path, documents: list[dict], reviewer_id: str) -> dict:
    inputs = [{"path": _relative(root, schema_receipt), "sha256": file_sha256(schema_receipt), "role": "input", "kind": "schema_receipt"},
              {"path": _relative(root, annotation_receipt), "sha256": file_sha256(annotation_receipt), "role": "input", "kind": "annotation_receipt"},
              {"path": _relative(root, source / "public-gold-index.json"), "sha256": file_sha256(source / "public-gold-index.json"), "role": "input", "kind": "gold_index"},
              {"path": _relative(root, source / "public-folds.json"), "sha256": file_sha256(source / "public-folds.json"), "role": "input", "kind": "split_lock"}]
    inputs.extend({"path": _relative(root, source / item["sidecar"]), "sha256": file_sha256(source / item["sidecar"]), "role": "input", "kind": "annotation_sidecar"} for item in documents)
    reviewer_digest = hashlib.sha256(reviewer_id.encode("utf-8")).hexdigest()
    value = {"schema": "ImmutableReceipt", "version": "V2", "protocol_version": EVALUATION_PROTOCOL_VERSION, "stage": "public_gold_lock", "protocolRunId": manifest["manifest_sha256"], "producer": {"role": "independent_reviewer"}, "command": "verify_gold_manifest", "timestamp": datetime.now(timezone.utc).isoformat(), "immutable": True, "status": "locked", "inputs": inputs, "outputs": [{"path": _relative(root, final_output / "public-gold-manifest.json"), "sha256": file_sha256(staged_manifest), "role": "output", "kind": "locked_manifest"}], "counts": {"document_count": 25, "issued_count": 15, "review_count": 10}, "authorization": {"reviewer_digest": reviewer_digest, "role": "independent_reviewer", "decision": "approved", "independent": True, "content_sha256": file_sha256(staged_manifest)}}
    value["receipt_sha256"] = canonical_json_sha256(value)
    return value


def lock_public_gold(*, sidecar_dir: str | Path, schema_receipt: str | Path, reviewer_id: str,
                     lock_out: str | Path, eval_root: str | Path, locator_map: str | Path) -> dict:
    raw_source, raw_output = Path(sidecar_dir).expanduser(), Path(lock_out).expanduser()
    if raw_source.is_symlink() or raw_output.is_symlink():
        raise PublicGoldReviewError("top-level verifier artifacts may not be symlinks")
    source, output, root = _outside_repo(raw_source), _outside_repo(raw_output), _root(eval_root)
    _relative(root, source); _relative(root, output)
    if output.exists() or not _subject_id(reviewer_id): raise PublicGoldReviewError("refusing overwrite and requiring reviewer_id")
    index_path, fold_path = source / "public-gold-index.json", source / "public-folds.json"
    _relative(root, index_path); _relative(root, fold_path)
    index = _load(index_path)
    _, documents = _validate_index(index)
    annotation = _annotation_receipt(root, source, index)
    schema = _schema_receipt(root, Path(schema_receipt))
    if index.get("schema_receipt") != schema: raise PublicGoldReviewError("schema receipt binding mismatch")
    split = _load(fold_path)
    try: validate_split_lock(split)
    except ManifestValidationError as error: raise PublicGoldReviewError("split lock is invalid") from error
    canonical_split = create_split_lock([item["document_sha256"] for item in documents], fold_count=5)
    if split != canonical_split or split.get("split_sha256") != index["split_sha256"]:
        raise PublicGoldReviewError("split lock document coverage mismatch")
    raw_locator_path = Path(locator_map).expanduser()
    if raw_locator_path.is_symlink():
        raise PublicGoldReviewError("runtime locator map must not be a symlink")
    locator_path = _outside_repo(raw_locator_path)
    if source == locator_path or source in locator_path.parents or output == locator_path or output in locator_path.parents:
        raise PublicGoldReviewError("runtime locator map must remain outside public artifacts")
    locators = _validate_locator_map(locator_path, index["gold_identity_sha256"], documents)
    locked = []
    for entry in documents:
        sidecar_path = source / entry["sidecar"]
        _relative(root, sidecar_path)
        if _locator_sha(root, locators[entry["document_sha256"]]) != entry["document_sha256"]: raise PublicGoldReviewError("document source hash mismatch")
        locked.append(_validate_sidecar(
            _load(sidecar_path), entry, reviewer_id, annotation["authorization"]["author_digest"],
        ))
    manifest = {"schema_version": "LockedPublicGoldManifestV2", "gold_identity_sha256": index["gold_identity_sha256"], "split_sha256": split["split_sha256"], "schema_receipt": schema, "documents": locked, "status": "locked", "immutable": True}
    manifest["manifest_sha256"] = canonical_json_sha256(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        staged_manifest = staging / "public-gold-manifest.json"
        staged_manifest.write_bytes(canonical_json_bytes(manifest) + b"\n")
        receipt = _receipt(root, output, staged_manifest, manifest, Path(schema_receipt),
                           source / "receipt.json", source, documents, reviewer_id)
        (staging / "receipt.json").write_bytes(canonical_json_bytes(receipt) + b"\n")
        try:
            os.mkdir(output)
        except FileExistsError as error:
            raise PublicGoldReviewError("refusing to overwrite immutable lock publication") from error
        try:
            for name in ("public-gold-manifest.json", "receipt.json"):
                os.link(staging / name, output / name)
            directory_fd = os.open(output, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            shutil.rmtree(output, ignore_errors=True)
            raise
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"status": "locked", "document_count": 25, "manifest_sha256": manifest["manifest_sha256"], "split_sha256": split["split_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently review and lock public gold sidecars.")
    parser.add_argument("--sidecar-dir", required=True); parser.add_argument("--schema-receipt", required=True)
    parser.add_argument("--reviewer-id", required=True); parser.add_argument("--lock-out", required=True)
    parser.add_argument("--eval-root", required=True); parser.add_argument("--locator-map", required=True)
    args = parser.parse_args()
    try: result = lock_public_gold(**vars(args))
    except (PublicGoldReviewError, OSError):
        print(json.dumps({"status": "invalid", "code": "PUBLIC_GOLD_REVIEW_REJECTED"}, sort_keys=True), file=sys.stderr); return 2
    print(json.dumps(result, sort_keys=True)); return 0

if __name__ == "__main__": raise SystemExit(main())
