import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


ARCHIVE_STATUS = "FAILED_QUALITY_GATES"


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_run(run_id, evidence_directory, output_directory):
    evidence_directory = Path(evidence_directory).resolve()
    output_directory = Path(output_directory).resolve()
    if not evidence_directory.is_dir():
        raise SystemExit(f"run evidence directory is missing: {evidence_directory}")
    if output_directory.exists():
        raise SystemExit(f"refusing to overwrite run archive: {output_directory}")
    output_directory.mkdir(parents=True)
    evidence_output = output_directory / "evidence"
    evidence_output.mkdir()
    copied = []
    for source in sorted(path for path in evidence_directory.rglob("*") if path.is_file()):
        relative = source.relative_to(evidence_directory)
        destination = evidence_output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(destination)
    if not copied:
        raise SystemExit("run archive requires at least one evidence file")
    inventory = [
        {
            "path": path.relative_to(output_directory).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in copied
    ]
    manifest = {
        "schemaVersion": 1,
        "runId": run_id,
        "status": ARCHIVE_STATUS,
        "publishable": False,
        "promotionEligible": False,
        "artifactState": "NO_GATE_ELIGIBLE_CANDIDATE",
        "archivedAtUtc": datetime.now(timezone.utc).isoformat(),
        "evidenceSource": str(evidence_directory),
        "evidenceFiles": inventory,
    }
    manifest_path = output_directory / "run-archive.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    checksum_paths = [*copied, manifest_path]
    (output_directory / "SHA256SUMS").write_text("".join(
        f"{sha256_file(path)}  {path.relative_to(output_directory).as_posix()}\n"
        for path in sorted(checksum_paths)
    ))
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: python -m sportif_ml.archive_run RUN_ID EVIDENCE_DIRECTORY OUTPUT_DIRECTORY"
        )
    print(json.dumps(archive_run(sys.argv[1], sys.argv[2], sys.argv[3])))