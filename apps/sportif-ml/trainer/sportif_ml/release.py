import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath

import yaml

from .provenance import require_passing_evaluation
from .storage import PUBLISHED_ARTIFACTS, _object_keys, _put_immutable, _safe_relative, s3_client


PROVENANCE_ARTIFACTS = (
    "dataset-manifest.json",
    "dataset-validation.json",
    "dataset-approval.json",
    "materialization.json",
    "training-metadata.json",
    "metrics.json",
    "onnx-validation.json",
    "model-manifest.json",
    "SHA256SUMS",
)
REQUIRED_APPROVAL = {
    "status": "APPROVED",
    "decision": "APPROVE",
    "dependencyLicensesVerified": True,
    "organizationalCommercialApprovalGranted": True,
    "productionPromotionAuthorized": True,
}


def sha256_path(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path):
    return json.loads(Path(path).read_text())


def _valid_identifier(value, label):
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in value):
        raise SystemExit(f"{label} must contain only lowercase letters, digits, and hyphens")


def _download_prefix(client, bucket, prefix, destination):
    keys = sorted(_object_keys(client, bucket, prefix))
    if not keys:
        raise SystemExit(f"candidate prefix is empty: s3://{bucket}/{prefix}")
    for key in keys:
        path = destination / _safe_relative(key, prefix)
        path.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(bucket, key, str(path))
    return keys


def _parse_candidate_checksums(path):
    checksums = {}
    for line_number, line in enumerate(Path(path).read_text().splitlines(), 1):
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as error:
            raise SystemExit(f"malformed candidate SHA256SUMS line {line_number}") from error
        relative_path = Path(*PurePosixPath(relative).parts)
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative_path in checksums
        ):
            raise SystemExit(f"invalid candidate checksum entry: {line}")
        checksums[relative_path] = digest
    return checksums


def verify_candidate(candidate, provenance, model_id, candidate_run_id):
    expected = {Path(name) for name in PUBLISHED_ARTIFACTS}
    actual = {path.relative_to(candidate) for path in candidate.rglob("*") if path.is_file()}
    if actual != expected:
        raise SystemExit(
            "candidate object inventory is incomplete or unexpected: "
            f"missing={sorted(str(path) for path in expected - actual)}, "
            f"unexpected={sorted(str(path) for path in actual - expected)}"
        )
    checksums = _parse_candidate_checksums(candidate / "SHA256SUMS")
    checksum_expected = expected - {Path("SHA256SUMS")}
    if set(checksums) != checksum_expected:
        raise SystemExit("candidate SHA256SUMS inventory does not match the artifact set")
    failures = [
        str(relative)
        for relative, expected_digest in checksums.items()
        if sha256_path(candidate / relative) != expected_digest
    ]
    if failures:
        raise SystemExit(f"candidate artifact checksum verification failed: {failures}")

    manifest = _read_json(candidate / "model-manifest.json")
    metrics = _read_json(candidate / "metrics.json")
    onnx_validation = _read_json(candidate / "onnx-validation.json")
    training = _read_json(candidate / "training-metadata.json")
    if manifest.get("modelId") != model_id or manifest.get("status") != "CANDIDATE":
        raise SystemExit("candidate model identity or status is invalid")
    manifest_artifacts = manifest.get("artifacts", {})
    manifest_required = {
        "model.pt",
        "model.onnx",
        "metrics.json",
        "onnx-validation.json",
        "training-metadata.json",
    }
    if set(manifest_artifacts) != manifest_required:
        raise SystemExit("candidate manifest artifact inventory is invalid")
    for name in manifest_required:
        recorded = manifest_artifacts[name]
        if (
            recorded.get("sha256") != sha256_path(candidate / name)
            or recorded.get("bytes") != (candidate / name).stat().st_size
        ):
            raise SystemExit(f"candidate manifest artifact metadata does not match: {name}")
    require_passing_evaluation(metrics)
    if manifest.get("evaluation") != metrics:
        raise SystemExit("candidate manifest evaluation does not match metrics.json")
    if onnx_validation.get("runtimeValidation") != "PASS" or onnx_validation.get("onnxValid") is not True:
        raise SystemExit("candidate ONNX runtime validation must pass")
    if training.get("initialization") != "random" or training.get("pretrainedWeightsUsed") is not False:
        raise SystemExit("candidate must use random initialization without pretrained weights")
    provenance_actual = {
        path.relative_to(provenance) for path in provenance.rglob("*") if path.is_file()
    }
    provenance_expected = {Path(name) for name in PROVENANCE_ARTIFACTS}
    if provenance_actual != provenance_expected:
        raise SystemExit("candidate provenance inventory is incomplete or unexpected")
    for name in ("training-metadata.json", "metrics.json", "onnx-validation.json", "model-manifest.json", "SHA256SUMS"):
        if sha256_path(candidate / name) != sha256_path(provenance / name):
            raise SystemExit(f"candidate and provenance copies differ: {name}")
    dataset_manifest = _read_json(provenance / "dataset-manifest.json")
    dataset_validation = _read_json(provenance / "dataset-validation.json")
    dataset_approval = _read_json(provenance / "dataset-approval.json")
    materialization = _read_json(provenance / "materialization.json")
    dataset_id = training.get("datasetId")
    content_digest = manifest.get("datasetContentSha256")
    if any(document.get("datasetId") != dataset_id for document in (
        dataset_manifest, dataset_validation, dataset_approval, materialization
    )):
        raise SystemExit("candidate dataset provenance identities do not agree")
    if (
        dataset_manifest.get("contentSha256") != content_digest
        or dataset_validation.get("contentSha256") != content_digest
        or dataset_approval.get("datasetContentSha256") != content_digest
        or materialization.get("datasetContentSha256") != content_digest
    ):
        raise SystemExit("candidate dataset provenance hashes do not agree")
    if dataset_validation.get("status") != "PASS" or dataset_validation.get("errors"):
        raise SystemExit("candidate dataset validation is not a clean PASS")
    if (
        dataset_approval.get("datasetApprovalGranted") is not True
        or dataset_approval.get("rightsVerified") is not True
        or dataset_approval.get("aiTrainingPermissionVerified") is not True
    ):
        raise SystemExit("candidate dataset approval and rights gates are incomplete")
    gate = manifest.get("commercialGate", {})
    required_candidate_gate = {
        "datasetRightsVerified": True,
        "datasetApprovalGranted": True,
        "trainingCodeLicenseVerified": True,
        "pretrainedWeightsUsed": False,
        "provenanceComplete": True,
    }
    if any(gate.get(key) != expected_value for key, expected_value in required_candidate_gate.items()):
        raise SystemExit("candidate engineering provenance gate is incomplete")
    return {
        "manifest": manifest,
        "metrics": metrics,
        "onnxValidation": onnx_validation,
        "training": training,
        "manifestSha256": sha256_path(candidate / "model-manifest.json"),
        "checksumSetSha256": sha256_path(candidate / "SHA256SUMS"),
        "candidateRunId": candidate_run_id,
        "datasetProvenance": {
            name: sha256_path(provenance / name)
            for name in ("dataset-manifest.json", "dataset-validation.json", "dataset-approval.json", "materialization.json")
        },
    }


def python_dependency_inventory():
    result = []
    for distribution in importlib.metadata.distributions():
        metadata = distribution.metadata
        classifiers = [
            value.removeprefix("License :: OSI Approved :: ")
            for value in metadata.get_all("Classifier", [])
            if value.startswith("License ::")
        ]
        project_urls = metadata.get_all("Project-URL", [])
        source = next(
            (
                value.split(", ", 1)[1]
                for value in project_urls
                if value.lower().startswith(("source,", "repository,"))
            ),
            metadata.get("Home-page"),
        )
        license_value = metadata.get("License-Expression") or (classifiers[0] if classifiers else None)
        if not license_value:
            license_lines = (metadata.get("License") or "").splitlines()
            license_value = license_lines[0][:120] if license_lines else None
        result.append({
            "name": metadata.get("Name"),
            "version": distribution.version,
            "declaredLicense": license_value,
            "source": source,
        })
    return sorted(result, key=lambda item: ((item["name"] or "").lower(), item["version"]))


def system_package_inventory():
    output = subprocess.run(
        ["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\n"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted([
        {"name": name, "version": version}
        for name, version in (line.split("\t", 1) for line in output.splitlines() if line)
    ], key=lambda item: (item["name"], item["version"]))


def environment_inventory():
    python_packages = python_dependency_inventory()
    system_packages = system_package_inventory()
    python_bytes = (json.dumps(python_packages, indent=2, sort_keys=True) + "\n").encode()
    system_bytes = (json.dumps(system_packages, indent=2, sort_keys=True) + "\n").encode()
    return {
        "pythonPackages": python_packages,
        "systemPackages": system_packages,
        "pythonInventorySha256": hashlib.sha256(python_bytes).hexdigest(),
        "systemInventorySha256": hashlib.sha256(system_bytes).hexdigest(),
    }


def write_inventory(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    inventory = environment_inventory()
    (output / "python-packages.json").write_text(
        json.dumps(inventory["pythonPackages"], indent=2, sort_keys=True) + "\n"
    )
    (output / "system-packages.json").write_text(
        json.dumps(inventory["systemPackages"], indent=2, sort_keys=True) + "\n"
    )
    result = {
        "pythonInventorySha256": inventory["pythonInventorySha256"],
        "systemInventorySha256": inventory["systemInventorySha256"],
        "pythonPackageCount": len(inventory["pythonPackages"]),
        "systemPackageCount": len(inventory["systemPackages"]),
    }
    (output / "inventory-hashes.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


def approval_template(model_id, candidate_data, inventory):
    return {
        "schemaVersion": 1,
        "modelId": model_id,
        "candidateRunId": candidate_data["candidateRunId"],
        "candidateManifestSha256": candidate_data["manifestSha256"],
        "candidateChecksumSetSha256": candidate_data["checksumSetSha256"],
        "pythonInventorySha256": inventory["pythonInventorySha256"],
        "systemInventorySha256": inventory["systemInventorySha256"],
        "status": "PENDING",
        "decision": "PENDING",
        "dependencyLicensesVerified": False,
        "organizationalCommercialApprovalGranted": False,
        "productionPromotionAuthorized": False,
        "approvedBy": "",
        "approverRole": "",
        "approvedAtUtc": "",
    }


def review(candidate_run_id, output_directory):
    _valid_identifier(candidate_run_id, "candidate run ID")
    model_id = os.environ["MODEL_ID"]
    client = s3_client()
    model_bucket = os.environ["ML_MODELS_BUCKET"]
    provenance_bucket = os.environ["ML_PROVENANCE_BUCKET"]
    candidate_prefix = f"candidates/{model_id}/{candidate_run_id}/"
    provenance_prefix = f"models/{model_id}/{candidate_run_id}/"
    output = Path(output_directory)
    if output.exists() and any(output.iterdir()):
        raise SystemExit("release review output directory must be empty")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        candidate = root / "candidate"
        provenance = root / "provenance"
        candidate.mkdir()
        provenance.mkdir()
        _download_prefix(client, model_bucket, candidate_prefix, candidate)
        _download_prefix(client, provenance_bucket, provenance_prefix, provenance)
        candidate_data = verify_candidate(candidate, provenance, model_id, candidate_run_id)
        inventory = environment_inventory()
        (output / "python-packages.json").write_text(
            json.dumps(inventory["pythonPackages"], indent=2, sort_keys=True) + "\n"
        )
        (output / "system-packages.json").write_text(
            json.dumps(inventory["systemPackages"], indent=2, sort_keys=True) + "\n"
        )
        summary = {
            "schemaVersion": 1,
            "status": "READY_FOR_ORGANIZATIONAL_REVIEW",
            "modelId": model_id,
            "candidateRunId": candidate_run_id,
            "candidateManifestSha256": candidate_data["manifestSha256"],
            "candidateChecksumSetSha256": candidate_data["checksumSetSha256"],
            "datasetProvenance": candidate_data["datasetProvenance"],
            "evaluation": candidate_data["metrics"],
            "onnxValidation": candidate_data["onnxValidation"],
            "pythonInventorySha256": inventory["pythonInventorySha256"],
            "systemInventorySha256": inventory["systemInventorySha256"],
            "pythonPackageCount": len(inventory["pythonPackages"]),
            "systemPackageCount": len(inventory["systemPackages"]),
            "packagesWithoutDeclaredLicense": [
                item for item in inventory["pythonPackages"] if not item.get("declaredLicense")
            ],
            "engineeringGateStatus": "PASS",
            "organizationalApprovalStatus": "PENDING",
        }
        (output / "release-review.json").write_text(json.dumps(summary, indent=2) + "\n")
        (output / "release-approval.template.json").write_text(
            json.dumps(approval_template(model_id, candidate_data, inventory), indent=2) + "\n"
        )
        print(json.dumps(summary))


def _training_config(candidate_data):
    training = candidate_data["training"]
    return {
        "model": {
            "id": candidate_data["manifest"]["modelId"],
            "architecture": candidate_data["manifest"]["architecture"],
        },
        "dataset": {
            "id": training["datasetId"],
            "contentSha256": candidate_data["manifest"]["datasetContentSha256"],
        },
        "training": {
            "seed": training["seed"],
            "epochsConfigured": training["epochs"],
            "epochsCompleted": training["epochsCompleted"],
            "batchSize": training["batchSize"],
            "baseLearningRate": training["baseLearningRate"],
            "selectedOperatingThreshold": training["selectedOperatingThreshold"],
            "bestValidationEpoch": training["bestValidationEpoch"],
            "bestValidationMetrics": training["bestValidationMetrics"],
            "initialization": training["initialization"],
            "pretrainedWeightsUsed": training["pretrainedWeightsUsed"],
        },
    }


def _model_card(candidate_data, approval):
    metrics = candidate_data["metrics"]
    return "\n".join([
        f"# {candidate_data['manifest']['modelId']}",
        "",
        "## Status",
        "",
        "Released through the Sportif engineering provenance gate. This record is not legal advice.",
        "",
        "## Intended use",
        "",
        "Single-class soccer-ball detection for Sportif-owned soccer video workflows.",
        "",
        "## Dataset and training",
        "",
        f"- Dataset: `{candidate_data['training']['datasetId']}`",
        f"- Dataset content SHA-256: `{candidate_data['manifest']['datasetContentSha256']}`",
        "- Initialization: random",
        "- Pretrained weights: no",
        "",
        "## Held-out test metrics",
        "",
        f"- mAP50: `{metrics['mAP50']}`",
        f"- mAP50-95: `{metrics['mAP50-95']}`",
        f"- Precision: `{metrics['precision']}`",
        f"- Recall: `{metrics['recall']}`",
        f"- Operating threshold selected from validation: `{metrics['scoreThreshold']}`",
        "",
        "## Approval",
        "",
        f"- Approved by: `{approval['approvedBy']}`",
        f"- Approval role: `{approval['approverRole']}`",
        f"- Approved at UTC: `{approval['approvedAtUtc']}`",
        "",
        "## Limitations",
        "",
        "Validated only against the immutable held-out test split recorded in the release manifest. "
        "Production monitoring, rollback readiness, and domain-shift review remain operational requirements.",
        "",
    ])


def validate_approval(approval, model_id, candidate_data, inventory):
    expected_identity = {
        "modelId": model_id,
        "candidateRunId": candidate_data["candidateRunId"],
        "candidateManifestSha256": candidate_data["manifestSha256"],
        "candidateChecksumSetSha256": candidate_data["checksumSetSha256"],
        "pythonInventorySha256": inventory["pythonInventorySha256"],
        "systemInventorySha256": inventory["systemInventorySha256"],
    }
    mismatches = {
        key: {"expected": expected, "actual": approval.get(key)}
        for key, expected in {**REQUIRED_APPROVAL, **expected_identity}.items()
        if approval.get(key) != expected
    }
    for required_text in ("approvedBy", "approverRole", "approvedAtUtc"):
        if not isinstance(approval.get(required_text), str) or not approval[required_text].strip():
            mismatches[required_text] = {"expected": "non-empty string", "actual": approval.get(required_text)}
    try:
        approved_at = datetime.fromisoformat(approval.get("approvedAtUtc", "").replace("Z", "+00:00"))
    except ValueError:
        mismatches["approvedAtUtc"] = {"expected": "ISO-8601 timestamp", "actual": approval.get("approvedAtUtc")}
    else:
        if approved_at.tzinfo is None:
            mismatches["approvedAtUtc"] = {"expected": "timezone-aware timestamp", "actual": approval.get("approvedAtUtc")}
    if mismatches:
        raise SystemExit(f"commercial release approval gate failed: {mismatches}")


def build_release_package(candidate, provenance, output, candidate_data, approval, release_id, inventory):
    output.mkdir(parents=True)
    for name in PUBLISHED_ARTIFACTS:
        if name != "SHA256SUMS":
            shutil.copyfile(candidate / name, output / name)

    (output / "dependency-lock.txt").write_text(
        "\n".join(f'{item["name"]}=={item["version"]}' for item in inventory["pythonPackages"]) + "\n"
    )
    (output / "system-package-lock.txt").write_text(
        "\n".join(f'{item["name"]}=={item["version"]}' for item in inventory["systemPackages"]) + "\n"
    )
    licenses = output / "licenses"
    notices = output / "notices"
    licenses.mkdir()
    notices.mkdir()
    (licenses / "dependency-licenses.json").write_text(json.dumps({
        "schemaVersion": 1,
        "reviewStatus": "VERIFIED_BY_RELEASE_APPROVAL",
        "reviewedBy": approval["approvedBy"],
        "reviewedAtUtc": approval["approvedAtUtc"],
        "pythonInventorySha256": inventory["pythonInventorySha256"],
        "systemInventorySha256": inventory["systemInventorySha256"],
        "pythonPackages": inventory["pythonPackages"],
        "systemPackages": inventory["systemPackages"],
    }, indent=2) + "\n")
    (notices / "README.md").write_text(
        "# Third-party notices\n\n"
        "See `../licenses/dependency-licenses.json` for the declared direct dependency "
        "licenses reviewed for this release. Package metadata is evidence, not legal advice.\n"
    )
    (output / "training-config.yaml").write_text(yaml.safe_dump(_training_config(candidate_data), sort_keys=False))
    (output / "model-card.md").write_text(_model_card(candidate_data, approval))
    (output / "release-approval.json").write_text(json.dumps(approval, indent=2) + "\n")
    for name in ("dataset-manifest.json", "dataset-validation.json", "dataset-approval.json", "materialization.json"):
        shutil.copyfile(provenance / name, output / name)

    released_manifest = dict(candidate_data["manifest"])
    released_manifest["status"] = "RELEASED"
    released_manifest["release"] = {
        "releaseId": release_id,
        "candidateRunId": candidate_data["candidateRunId"],
        "candidateManifestSha256": candidate_data["manifestSha256"],
        "candidateChecksumSetSha256": candidate_data["checksumSetSha256"],
        "approvedBy": approval["approvedBy"],
        "approverRole": approval["approverRole"],
        "approvedAtUtc": approval["approvedAtUtc"],
        "pythonInventorySha256": inventory["pythonInventorySha256"],
        "systemInventorySha256": inventory["systemInventorySha256"],
    }
    released_manifest["commercialGate"] = {
        **released_manifest["commercialGate"],
        "dependencyLicensesVerified": True,
        "releaseApproved": True,
        "productionPromotionAuthorized": True,
    }
    (output / "model-manifest.json").write_text(json.dumps(released_manifest, indent=2) + "\n")

    files = sorted(path.relative_to(output) for path in output.rglob("*") if path.is_file())
    checksum_lines = [f"{sha256_path(output / relative)}  {relative.as_posix()}" for relative in files]
    (output / "SHA256SUMS").write_text("\n".join(checksum_lines) + "\n")
    return released_manifest


def promote(candidate_run_id, release_id, approval_key):
    _valid_identifier(candidate_run_id, "candidate run ID")
    _valid_identifier(release_id, "release ID")
    model_id = os.environ["MODEL_ID"]
    client = s3_client()
    model_bucket = os.environ["ML_MODELS_BUCKET"]
    provenance_bucket = os.environ["ML_PROVENANCE_BUCKET"]
    candidate_prefix = f"candidates/{model_id}/{candidate_run_id}/"
    candidate_provenance_prefix = f"models/{model_id}/{candidate_run_id}/"
    expected_approval_key = (
        f"releases/{model_id}/{candidate_run_id}/release-approval.json"
    )
    if approval_key != expected_approval_key:
        raise SystemExit(
            "approval key must be the candidate-scoped immutable release approval key: "
            f"{expected_approval_key}"
        )
    release_prefix = f"releases/{model_id}/{release_id}/"
    release_provenance_prefix = f"releases/{model_id}/{release_id}/"

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        candidate = root / "candidate"
        candidate.mkdir()
        _download_prefix(client, model_bucket, candidate_prefix, candidate)
        provenance = root / "provenance"
        provenance.mkdir()
        _download_prefix(client, provenance_bucket, candidate_provenance_prefix, provenance)
        candidate_data = verify_candidate(candidate, provenance, model_id, candidate_run_id)
        inventory = environment_inventory()
        approval_path = root / "release-approval.json"
        client.download_file(provenance_bucket, approval_key, str(approval_path))
        approval = _read_json(approval_path)
        validate_approval(approval, model_id, candidate_data, inventory)
        release = root / "release"
        manifest = build_release_package(
            candidate, provenance, release, candidate_data, approval, release_id, inventory
        )
        for path in sorted(item for item in release.rglob("*") if item.is_file()):
            relative = path.relative_to(release).as_posix()
            _put_immutable(client, model_bucket, release_prefix + relative, path)
        for name in ("model-manifest.json", "release-approval.json", "SHA256SUMS"):
            _put_immutable(client, provenance_bucket, release_provenance_prefix + name, release / name)
        result = {
            "status": "RELEASED",
            "modelId": model_id,
            "candidateRunId": candidate_run_id,
            "releaseId": release_id,
            "releaseManifestSha256": sha256_path(release / "model-manifest.json"),
            "releaseChecksumSetSha256": sha256_path(release / "SHA256SUMS"),
            "modelPrefix": f"s3://{model_bucket}/{release_prefix}",
            "provenancePrefix": f"s3://{provenance_bucket}/{release_provenance_prefix}",
            "commercialGate": manifest["commercialGate"],
        }
        print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "promote":
        promote(sys.argv[2], sys.argv[3], sys.argv[4])
    elif len(sys.argv) == 3 and sys.argv[1] == "inventory":
        write_inventory(sys.argv[2])
    elif len(sys.argv) == 4 and sys.argv[1] == "review":
        review(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit(
            "usage: python -m sportif_ml.release inventory OUTPUT_DIR | "
            "review CANDIDATE_RUN_ID OUTPUT_DIR | "
            "promote CANDIDATE_RUN_ID RELEASE_ID APPROVAL_KEY"
        )