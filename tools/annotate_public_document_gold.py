#!/usr/bin/env python3
"""PII-safe initialization of the versioned public-gold boundary."""
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
from masking_evaluation import (COORDINATE_SPACE, EVALUATION_PROTOCOL_VERSION,
    GEOMETRY_POLICY_VERSION, ManifestValidationError, ProtocolValidationError, SCHEMA_VERSION,
    canonical_json_bytes, canonical_json_sha256, create_split_lock, file_sha256,
    validate_sidecar_security, verify_protocol_receipt)

MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBERS = 256
MAX_ZIP_TOTAL_BYTES = 256 * 1024 * 1024
MAX_ZIP_RATIO = 100
SCHEMA_LOCK_STAGE = "synthetic_schema_lock"


class PublicGoldWorkflowError(ValueError):
    pass


_OPAQUE_SUBJECT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _subject_id(value: object) -> bool:
    return isinstance(value, str) and bool(_OPAQUE_SUBJECT.fullmatch(value))


def _sha_stream(stream, limit: int) -> str:
    digest, total = hashlib.sha256(), 0
    while block := stream.read(1024 * 1024):
        total += len(block)
        if total > limit:
            raise PublicGoldWorkflowError("PDF exceeds public-gold size limit")
        digest.update(block)
    return digest.hexdigest()


def _outside_repo(path: Path) -> Path:
    resolved, repo = path.expanduser().resolve(), Path(__file__).resolve().parents[1]
    if resolved == repo or repo in resolved.parents or ".gjc" in resolved.parts:
        raise PublicGoldWorkflowError("all public-gold artifacts must be outside repository and .gjc")
    return resolved


def _eval_root(path: str | Path) -> Path:
    root = _outside_repo(Path(path))
    if not root.is_dir():
        raise PublicGoldWorkflowError("explicit evaluation root is unavailable")
    return root


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError as error:
        raise PublicGoldWorkflowError("public-gold artifact must live below explicit evaluation root") from error


def _source_path(root: Path, path: Path, *, label: str, directory: bool = False) -> Path:
    if path.is_symlink():
        raise PublicGoldWorkflowError(f"{label} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise PublicGoldWorkflowError(f"{label} must live below explicit evaluation root") from error
    if (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
        raise PublicGoldWorkflowError(f"{label} is unavailable")
    return resolved


def _pdfs(root: Path, directory: Path, source_class: str) -> list[dict]:
    directory = _source_path(root, directory, label="issued corpus directory", directory=True)
    entries = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_symlink():
            raise PublicGoldWorkflowError("issued corpus must not contain symlinks")
        if path.is_file() and path.suffix.lower() == ".pdf":
            with path.open("rb") as stream:
                digest = _sha_stream(stream, MAX_PDF_BYTES)
            entries.append({"document_sha256": digest, "source_class": source_class,
                            "form": source_class, "profile": "official_dispatch" if source_class == "issued" else "internal_review",
                            "locator": _relative(root, path)})
    return entries


def _zip_pdfs(root: Path, path: Path) -> list[dict]:
    path = _source_path(root, path, label="review zip")
    entries, total = [], 0
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_MEMBERS or len({info.filename for info in infos}) != len(infos):
                raise PublicGoldWorkflowError("review zip has invalid member inventory")
            for info in infos:
                member = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (member.is_absolute() or ".." in member.parts or "\\" in info.filename
                        or stat.S_ISLNK(mode)):
                    raise PublicGoldWorkflowError("review zip contains unsafe member")
                if info.is_dir() or not info.filename.lower().endswith(".pdf"):
                    continue
                if info.file_size > MAX_PDF_BYTES or info.compress_size == 0 or info.file_size / info.compress_size > MAX_ZIP_RATIO:
                    raise PublicGoldWorkflowError("review zip member exceeds safe limits")
                total += info.file_size
                if total > MAX_ZIP_TOTAL_BYTES:
                    raise PublicGoldWorkflowError("review zip aggregate exceeds safe limit")
                with archive.open(info, "r") as stream:
                    digest = _sha_stream(stream, MAX_PDF_BYTES)
                entries.append({"document_sha256": digest, "source_class": "review", "form": "review",
                                "profile": "internal_review", "locator": f"zip:{_relative(root, path)}!/{info.filename}"})
    except zipfile.BadZipFile as error:
        raise PublicGoldWorkflowError("review zip is invalid") from error
    return sorted(entries, key=lambda item: item["document_sha256"])


def _schema_receipt(root: Path, path: Path) -> dict:
    _relative(root, path)
    try:
        receipt = verify_protocol_receipt(root, path, artifact_kind=SCHEMA_LOCK_STAGE, required_status="locked")
    except ProtocolValidationError as error:
        raise PublicGoldWorkflowError("schema receipt must be a verified locked schema-lock receipt under evaluation root") from error
    outputs = receipt.get("outputs")
    if (not isinstance(outputs, list) or len(outputs) != 1
            or outputs[0].get("role") != "output" or outputs[0].get("kind") != "schema"):
        raise PublicGoldWorkflowError("schema receipt must bind exactly one schema output")
    return {"receipt_sha256": receipt["receipt_sha256"], "schema_output_sha256": outputs[0]["sha256"]}


def _validate_sidecar_security(sidecar: object) -> None:
    try:
        validate_sidecar_security(sidecar)
    except ManifestValidationError as error:
        raise PublicGoldWorkflowError(str(error)) from error

def _shell(entry: dict, author_id: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "geometry_policy_version": GEOMETRY_POLICY_VERSION,
            "coordinate_space": COORDINATE_SPACE, "profile": entry["profile"], "policy_version": "unassigned-draft-policy",
            "document": {"document_id": entry["document_sha256"], "input_sha256": entry["document_sha256"]},
            "source_class": entry["source_class"], "form": entry["form"],
            "provenance": {"author": {"id": author_id}, "reviewer": {"id": "pending", "decision": "pending"}, "detector_output_imported": False},
            "pages": [], "segments": [], "regions": [], "occurrences": [], "negatives": [], "protected_neighbors": [],
            "annotation_completion": {"pages": "pending", "segments": "pending", "regions": "pending", "occurrences": "pending", "negatives": "pending", "protected_neighbors": "pending"},
            "annotation_status": "draft_unreviewed"}


def _receipt(root: Path, final_dir: Path, staged_manifest: Path, manifest_name: str, *, stage: str, status: str, run_id: str, inputs: list[dict], counts: dict, authorization: dict) -> dict:
    value = {"schema": "ImmutableReceipt", "version": "V2", "protocol_version": EVALUATION_PROTOCOL_VERSION,
             "stage": stage, "protocolRunId": run_id, "producer": {"role": "annotator"}, "command": "annotate_public_document_gold",
             "timestamp": datetime.now(timezone.utc).isoformat(), "immutable": True, "status": status,
             "inputs": inputs, "outputs": [{"path": _relative(root, final_dir / manifest_name), "sha256": file_sha256(staged_manifest), "role": "output", "kind": "gold_index"}],
             "counts": counts, "authorization": authorization}
    value["receipt_sha256"] = canonical_json_sha256(value)
    return value


def initialize_batch(*, issued_dir: str | Path, review_zip: str | Path, schema_receipt: str | Path,
                     sidecar_out: str | Path, author_id: str, eval_root: str | Path,
                     import_completed_dir: str | Path | None = None, locator_map_out: str | Path | None = None) -> dict:
    out, root = _outside_repo(Path(sidecar_out)), _eval_root(eval_root)
    _relative(root, out)
    locator_out = _outside_repo(Path(locator_map_out)) if locator_map_out else out.parent / f".{out.name}.runtime-locators.json"
    _relative(root, locator_out)
    if not _subject_id(author_id) or out.exists() or locator_out.exists():
        raise PublicGoldWorkflowError("refusing overwrite and requiring author_id")
    schema = _schema_receipt(root, Path(schema_receipt))
    issued, review = _pdfs(root, Path(issued_dir), "issued"), _zip_pdfs(root, Path(review_zip))
    out.parent.mkdir(parents=True, exist_ok=True)
    locator_out.parent.mkdir(parents=True, exist_ok=True)
    if len(issued) != 15 or len(review) != 10:
        raise PublicGoldWorkflowError("public pilot must contain exactly 15 issued and 10 review PDFs")
    documents, hashes = issued + review, [item["document_sha256"] for item in issued + review]
    if len(set(hashes)) != 25:
        raise PublicGoldWorkflowError("public pilot document hashes must be unique")
    imported = _outside_repo(Path(import_completed_dir)) if import_completed_dir else None
    if imported is not None and not imported.is_dir():
        raise PublicGoldWorkflowError("completed annotation import directory is unavailable")
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}.", dir=out.parent))
    try:
        sidecars = []
        for entry in sorted(documents, key=lambda item: item["document_sha256"]):
            filename = f"{entry['document_sha256']}.json"
            shell = _shell(entry, author_id)
            if imported is not None and (imported / filename).is_file():
                try: shell = json.loads((imported / filename).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error: raise PublicGoldWorkflowError("completed annotation payload is unreadable") from error
                _validate_sidecar_security(shell)
                if (shell.get("document", {}).get("input_sha256") != entry["document_sha256"]
                        or shell.get("provenance", {}).get("detector_output_imported") is not False
                        or shell.get("provenance", {}).get("author", {}).get("id") != author_id):
                    raise PublicGoldWorkflowError("completed annotation payload author or provenance mismatch")
            (staging / filename).write_bytes(canonical_json_bytes(shell) + b"\n")
            sidecars.append({key: entry[key] for key in ("document_sha256", "source_class", "form", "profile")} | {"sidecar": filename})
        split = create_split_lock(hashes, fold_count=5)
        (staging / "public-folds.json").write_bytes(canonical_json_bytes(split) + b"\n")
        identity = {"schema_version": "PublicGoldBatchV2", "schema_receipt": schema, "documents": sidecars, "split_sha256": split["split_sha256"]}
        index = {**identity, "gold_identity_sha256": canonical_json_sha256(identity), "status": "draft_unreviewed", "immutable": True}
        index["index_sha256"] = canonical_json_sha256(identity)
        (staging / "public-gold-index.json").write_bytes(canonical_json_bytes(index) + b"\n")
        receipt_inputs = [
            {"path": _relative(root, Path(schema_receipt)), "sha256": file_sha256(Path(schema_receipt)), "role": "input", "kind": "schema_receipt"},
            {"path": _relative(root, out / "public-folds.json"), "sha256": file_sha256(staging / "public-folds.json"), "role": "input", "kind": "split_lock"},
        ]
        receipt_inputs.extend(
            {"path": _relative(root, out / item["sidecar"]), "sha256": file_sha256(staging / item["sidecar"]), "role": "input", "kind": "annotation_sidecar"}
            for item in sidecars
        )
        author_digest = hashlib.sha256(author_id.encode("utf-8")).hexdigest()
        receipt = _receipt(
            root, out, staging / "public-gold-index.json", "public-gold-index.json",
            stage="public_gold_annotation", status="draft_initialized",
            run_id=index["gold_identity_sha256"], inputs=receipt_inputs,
            counts={"document_count": 25, "issued_count": 15, "review_count": 10},
            authorization={
                "author_digest": author_digest,
                "role": "annotator",
                "decision": "reviewed",
                "independent": False,
                "content_sha256": file_sha256(staging / "public-gold-index.json"),
            },
        )
        (staging / "receipt.json").write_bytes(canonical_json_bytes(receipt) + b"\n")
        locator_payload = {"schema_version": "PrivateRuntimeLocatorMapV1", "gold_identity_sha256": index["gold_identity_sha256"], "locators": {entry["document_sha256"]: next(doc["locator"] for doc in documents if doc["document_sha256"] == entry["document_sha256"]) for entry in sidecars}}
        locator_payload["locator_map_sha256"] = canonical_json_sha256(locator_payload)
        locator_created = False
        try:
            descriptor = os.open(locator_out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as error:
            raise PublicGoldWorkflowError("refusing to overwrite private locator map") from error
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(canonical_json_bytes(locator_payload) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
            locator_created = True
        except Exception:
            try:
                locator_out.unlink()
            except OSError:
                pass
            raise
        try:
            os.mkdir(out)
        except FileExistsError as error:
            raise PublicGoldWorkflowError("refusing to overwrite public-gold publication") from error
        try:
            for staged in staging.iterdir():
                os.link(staged, out / staged.name)
            directory_fd = os.open(out, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            shutil.rmtree(out, ignore_errors=True)
            raise
        shutil.rmtree(staging, ignore_errors=True)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if "locator_created" in locals() and locator_created:
            locator_out.unlink(missing_ok=True)
        raise
    return {"status": "draft_initialized", "document_count": 25, "issued_count": 15, "review_count": 10, "split_sha256": split["split_sha256"], "gold_identity_sha256": index["gold_identity_sha256"], "private_locator_map_sha256": locator_payload["locator_map_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize PII-free public gold annotation shells.")
    parser.add_argument("--issued-dir", required=True); parser.add_argument("--review-zip", required=True)
    parser.add_argument("--schema-receipt", required=True); parser.add_argument("--sidecar-out", required=True)
    parser.add_argument("--author-id", required=True); parser.add_argument("--eval-root", required=True)
    parser.add_argument("--import-completed-dir"); parser.add_argument("--locator-map-out")
    args = parser.parse_args()
    try: result = initialize_batch(**vars(args))
    except (PublicGoldWorkflowError, OSError):
        print(json.dumps({"status": "invalid", "code": "PUBLIC_GOLD_INITIALIZATION_REJECTED"}, sort_keys=True), file=sys.stderr); return 2
    print(json.dumps(result, sort_keys=True)); return 0

if __name__ == "__main__":
    raise SystemExit(main())
