from collections import defaultdict

from .prelabel import has_zero_iou, validate_box


def visible_rectangles_by_frame(annotations, label_id=1):
    rectangles = defaultdict(list)
    for shape in annotations.get("shapes", []):
        if (
            shape.get("label_id") == label_id
            and shape.get("type") == "rectangle"
            and not shape.get("outside", False)
        ):
            rectangles[int(shape["frame"])].append(
                tuple(float(value) for value in shape["points"])
            )
    for track in annotations.get("tracks", []):
        if track.get("label_id") != label_id:
            continue
        for shape in track.get("shapes", []):
            if shape.get("type") == "rectangle" and not shape.get("outside", False):
                rectangles[int(shape["frame"])].append(
                    tuple(float(value) for value in shape["points"])
                )
    return rectangles


def appendable_items(items, annotations, frame_dimensions, label_id=1):
    existing = visible_rectangles_by_frame(annotations, label_id)
    accepted = []
    rejected = []
    accepted_by_frame = defaultdict(list)
    for item in items:
        frame = int(item["frame"])
        if frame not in frame_dimensions:
            raise ValueError(f"frame {frame} is not present in the CVAT job")
        width, height = frame_dimensions[frame]
        points = validate_box(item["points"], width, height)
        blockers = [*existing.get(frame, ()), *accepted_by_frame.get(frame, ())]
        if not has_zero_iou(points, blockers):
            rejected.append({**item, "reason": "positive-iou-with-existing-or-batch-box"})
            continue
        normalized = {**item, "frame": frame, "points": list(points)}
        accepted.append(normalized)
        accepted_by_frame[frame].append(points)
    return accepted, rejected


def cvat_rectangle(item, label_id=1):
    return {
        "label_id": label_id,
        "type": "rectangle",
        "frame": int(item["frame"]),
        "group": 0,
        "source": "auto",
        "occluded": False,
        "outside": False,
        "z_order": 0,
        "rotation": 0.0,
        "points": [float(value) for value in item["points"]],
        "attributes": [],
        "elements": [],
    }


def require_expected_annotation_digest(task_id, items, actual_digest, expected_digests):
    if not items:
        return
    task_id = str(task_id)
    expected_digest = expected_digests.get(task_id)
    if not expected_digest:
        raise RuntimeError(
            f"task {task_id} has curated items but no expected annotation digest"
        )
    if actual_digest != expected_digest:
        raise RuntimeError(
            f"task {task_id} annotations changed after curation: "
            f"expected {expected_digest}, found {actual_digest}"
        )
