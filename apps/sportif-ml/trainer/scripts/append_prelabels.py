import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sportif_ml.cvat_prelabel import (
    appendable_items,
    cvat_rectangle,
    require_expected_annotation_digest,
)


CURATED = Path(os.environ.get(
    "PRELABEL_CURATED",
    "/work/prelabel-revision/epoch-0015-curated.json",
))
RECEIPT = Path(os.environ.get(
    "PRELABEL_RECEIPT",
    "/work/prelabel-revision/epoch-0015-import-receipt.json",
))
TASK_TO_JOB = json.loads(os.environ.get("PRELABEL_TASK_TO_JOB", "{}"))
LABEL_ID = int(os.environ.get("PRELABEL_LABEL_ID", "1"))
EXPECTED_CHECKPOINT_SHA256 = os.environ.get(
    "PRELABEL_EXPECTED_CHECKPOINT_SHA256",
    "a175e107417efdb3de83ebe1acb5960fb96490bf68d71d28f7c0e3c8d242ac3a",
)


def request(path, *, method="GET", body=None):
    base = os.environ["CVAT_API_URL"].rstrip("/")
    headers = {
        "Authorization": f"Token {os.environ['CVAT_API_TOKEN']}",
        "Accept": "application/vnd.cvat+json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = Request(base + path, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=120) as response:
            payload = response.read()
            status = response.status
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:4000]
        raise RuntimeError(f"{method} {path} failed with HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"{method} {path} failed: {error.reason}") from error
    return status, json.loads(payload) if payload else None


def digest(document):
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def main():
    curated = json.loads(CURATED.read_text())
    task_to_job = TASK_TO_JOB or {task_id: int(task_id) for task_id in curated["tasks"]}
    if set(curated["tasks"]) != set(task_to_job):
        raise RuntimeError("curated task set does not match the guarded import task set")
    if curated["checkpointSha256"] != EXPECTED_CHECKPOINT_SHA256:
        raise RuntimeError("unexpected checkpoint digest")
    expected_annotation_digests = curated.get("expectedAnnotationSha256", {})

    receipt = {
        "schemaVersion": 1,
        "curatedManifestSha256": hashlib.sha256(CURATED.read_bytes()).hexdigest(),
        "checkpointSha256": curated["checkpointSha256"],
        "taskToJob": task_to_job,
        "tasks": {},
        "status": "started",
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")

    for task_id, items in curated["tasks"].items():
        job_id = task_to_job[task_id]
        _, before = request(f"/api/jobs/{job_id}/annotations")
        before_digest = digest(before)
        require_expected_annotation_digest(
            task_id, items, before_digest, expected_annotation_digests
        )
        _, metadata = request(f"/api/tasks/{task_id}/data/meta")
        frame_dimensions = {
            frame: (item["width"], item["height"])
            for frame, item in enumerate(metadata["frames"])
        }
        accepted, rejected = appendable_items(
            items, before, frame_dimensions, label_id=LABEL_ID
        )

        shapes = [cvat_rectangle(item, LABEL_ID) for item in accepted]
        if not shapes:
            receipt["tasks"][task_id] = {
                "jobId": job_id,
                "createdCount": 0,
                "rejectedCount": len(rejected),
                "rejected": rejected,
                "beforeAnnotationSha256": before_digest,
                "afterAnnotationSha256": before_digest,
            }
            RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
            continue

        payload = {"version": int(before.get("version", 0)), "tags": [], "shapes": shapes, "tracks": []}
        status, response = request(
            f"/api/jobs/{job_id}/annotations?action=create",
            method="PATCH",
            body=payload,
        )
        _, after = request(f"/api/jobs/{job_id}/annotations")
        before_ids = {shape.get("id") for shape in before.get("shapes", [])}
        created = [
            shape for shape in after.get("shapes", [])
            if shape.get("id") not in before_ids
        ]
        if len(created) != len(shapes):
            raise RuntimeError(
                f"task {task_id} created {len(created)} shapes, expected {len(shapes)}"
            )
        expected = {
            (shape["frame"], tuple(round(value, 5) for value in shape["points"]))
            for shape in shapes
        }
        actual = {
            (shape["frame"], tuple(round(float(value), 5) for value in shape["points"]))
            for shape in created
        }
        if actual != expected:
            raise RuntimeError(f"task {task_id} created shape coordinates do not match")
        if any(shape.get("label_id") != LABEL_ID or shape.get("source") != "auto" for shape in created):
            raise RuntimeError(f"task {task_id} created shapes have invalid label/source")

        receipt["tasks"][task_id] = {
            "jobId": job_id,
            "httpStatus": status,
            "beforeShapeCount": len(before.get("shapes", [])),
            "afterShapeCount": len(after.get("shapes", [])),
            "createdCount": len(created),
            "createdShapeIds": [shape["id"] for shape in created],
            "createdFrames": [shape["frame"] for shape in created],
            "rejectedCount": len(rejected),
            "rejected": rejected,
            "beforeAnnotationSha256": before_digest,
            "afterAnnotationSha256": digest(after),
            "responseVersion": response.get("version") if isinstance(response, dict) else None,
        }
        RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")

    receipt["status"] = "complete"
    receipt["createdTotal"] = sum(item["createdCount"] for item in receipt["tasks"].values())
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
