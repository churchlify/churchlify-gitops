import json
import os
import shutil
import sys
from pathlib import Path, PurePosixPath

import boto3
from botocore.exceptions import ClientError

from .dataset import dataset_root, sha256_file


REQUIRED_DATASET_FILES = {
    "SHA256SUMS",
    "dataset-manifest.json",
    "dataset-validation.json",
    "provenance.json",
    "source-videos.json",
}
PUBLISHED_ARTIFACTS = (
    "model.pt",
    "metrics.json",
    "model.onnx",
    "onnx-validation.json",
    "training-metadata.json",
    "model-manifest.json",
    "SHA256SUMS",
)


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ["AWS_ENDPOINT_URL"],
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
    )


def _read_json(path):
    return json.loads(path.read_text())


def _object_keys(client, bucket, prefix):
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            yield item["Key"]


def _safe_relative(key, prefix):
    relative = PurePosixPath(key.removeprefix(prefix))
    if key == prefix or not relative.parts or ".." in relative.parts:
        raise SystemExit(f"unsafe or empty dataset object key: {key}")
    return Path(*relative.parts)


def _parse_checksums(path):
    checksums = {}
    for line_number, line in enumerate(path.read_text().splitlines(), 1):
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as error:
            raise SystemExit(f"malformed SHA256SUMS line {line_number}") from error
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise SystemExit(f"invalid SHA-256 at SHA256SUMS line {line_number}")
        path_value = Path(*PurePosixPath(relative).parts)
        if path_value.is_absolute() or ".." in path_value.parts or path_value in checksums:
            raise SystemExit(f"unsafe or duplicate checksum path: {relative}")
        checksums[path_value] = digest
    return checksums


def materialize(dataset_id):
    if dataset_id != os.environ.get("DATASET_ID"):
        raise SystemExit("workflow dataset ID does not match the approved configured dataset")
    if os.environ.get("DATASET_APPROVED", "").lower() != "true":
        raise SystemExit("dataset approval ConfigMap gate is not enabled")

    client = s3_client()
    dataset_bucket = os.environ["ML_DATASETS_BUCKET"]
    provenance_bucket = os.environ["ML_PROVENANCE_BUCKET"]
    dataset_prefix = f"{dataset_id}/"
    approval_key = f"datasets/{dataset_id}/dataset-approval.json"
    root = dataset_root(dataset_id)
    temporary = root.with_name(f".{root.name}.materializing")
    shutil.rmtree(temporary, ignore_errors=True)
    temporary.mkdir(parents=True)

    keys = sorted(_object_keys(client, dataset_bucket, dataset_prefix))
    if not keys:
        raise SystemExit(f"approved dataset prefix is empty: s3://{dataset_bucket}/{dataset_prefix}")
    for key in keys:
        destination = temporary / _safe_relative(key, dataset_prefix)
        destination.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(dataset_bucket, key, str(destination))

    present = {path.name for path in temporary.iterdir() if path.is_file()}
    missing = REQUIRED_DATASET_FILES - present
    if missing:
        raise SystemExit(f"approved dataset is missing required files: {sorted(missing)}")

    checksums = _parse_checksums(temporary / "SHA256SUMS")
    expected_paths = set(checksums) | {Path(name) for name in REQUIRED_DATASET_FILES}
    actual_paths = {path.relative_to(temporary) for path in temporary.rglob("*") if path.is_file()}
    if actual_paths != expected_paths:
        raise SystemExit(
            "dataset object inventory differs from SHA256SUMS: "
            f"missing={sorted(str(path) for path in expected_paths - actual_paths)}, "
            f"unexpected={sorted(str(path) for path in actual_paths - expected_paths)}"
        )
    checksum_errors = [
        str(relative)
        for relative, expected in checksums.items()
        if sha256_file(temporary / relative) != expected
    ]
    if checksum_errors:
        raise SystemExit(f"dataset checksum verification failed: {checksum_errors[:10]}")

    approval_path = temporary / "dataset-approval.json"
    client.download_file(provenance_bucket, approval_key, str(approval_path))
    manifest = _read_json(temporary / "dataset-manifest.json")
    validation = _read_json(temporary / "dataset-validation.json")
    approval = _read_json(approval_path)
    content_digest = manifest.get("contentSha256")
    required_approval = {
        "datasetId": dataset_id,
        "datasetContentSha256": content_digest,
        "status": "APPROVED",
        "decision": "APPROVE",
        "datasetApprovalGranted": True,
        "humanAnnotationReviewComplete": True,
        "rightsVerified": True,
        "aiTrainingPermissionVerified": True,
    }
    mismatches = {
        key: {"expected": expected, "actual": approval.get(key)}
        for key, expected in required_approval.items()
        if approval.get(key) != expected
    }
    if mismatches:
        raise SystemExit(f"immutable dataset approval gate failed: {mismatches}")
    if validation.get("status") != "PASS" or validation.get("errors"):
        raise SystemExit("stored dataset validation is not a clean PASS")
    for document in (manifest, validation):
        if document.get("datasetId") != dataset_id or document.get("contentSha256") != content_digest:
            raise SystemExit("dataset manifest, validation, and approval identities do not agree")
    if len(checksums) != manifest.get("objectCount") + 4:
        raise SystemExit("checksum inventory count does not match the approved dataset manifest")

    shutil.rmtree(root, ignore_errors=True)
    temporary.rename(root)
    result = {
        "status": "PASS",
        "datasetId": dataset_id,
        "datasetContentSha256": content_digest,
        "datasetObjectsVerified": manifest["objectCount"],
        "checksumsVerified": len(checksums),
        "approvalKey": approval_key,
        "approvedBy": approval["approvedBy"],
        "approvedAtUtc": approval["approvedAtUtc"],
    }
    (root / "materialization.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


def _put_immutable(client, bucket, key, path):
    digest = sha256_file(path)
    try:
        existing = client.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in {"404", "NoSuchKey", "NotFound"}:
            raise
    else:
        if existing.get("Metadata", {}).get("sha256") == digest:
            return
        raise SystemExit(f"refusing to overwrite existing artifact: s3://{bucket}/{key}")
    client.upload_file(str(path), bucket, key, ExtraArgs={"Metadata": {"sha256": digest}})


def publish(dataset_id, run_id):
    if not run_id or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in run_id):
        raise SystemExit("run ID must contain only lowercase letters, digits, and hyphens")
    root = dataset_root(dataset_id)
    artifacts = root / "artifacts"
    missing = [name for name in PUBLISHED_ARTIFACTS if not (artifacts / name).is_file()]
    if missing:
        raise SystemExit(f"candidate artifact set is incomplete: {missing}")

    client = s3_client()
    model_bucket = os.environ["ML_MODELS_BUCKET"]
    provenance_bucket = os.environ["ML_PROVENANCE_BUCKET"]
    model_id = os.environ["MODEL_ID"]
    model_prefix = f"candidates/{model_id}/{run_id}/"
    provenance_prefix = f"models/{model_id}/{run_id}/"

    for name in PUBLISHED_ARTIFACTS:
        _put_immutable(client, model_bucket, model_prefix + name, artifacts / name)
    for name in ("dataset-manifest.json", "dataset-validation.json", "dataset-approval.json", "materialization.json"):
        _put_immutable(client, provenance_bucket, provenance_prefix + name, root / name)
    for name in ("training-metadata.json", "metrics.json", "onnx-validation.json", "model-manifest.json", "SHA256SUMS"):
        _put_immutable(client, provenance_bucket, provenance_prefix + name, artifacts / name)

    print(json.dumps({
        "status": "PUBLISHED_CANDIDATE",
        "modelId": model_id,
        "datasetId": dataset_id,
        "runId": run_id,
        "modelPrefix": f"s3://{model_bucket}/{model_prefix}",
        "provenancePrefix": f"s3://{provenance_bucket}/{provenance_prefix}",
    }))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "materialize":
        materialize(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "publish":
        publish(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("usage: python -m sportif_ml.storage materialize DATASET_ID | publish DATASET_ID RUN_ID")