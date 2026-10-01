import math


DEFAULT_NMS_IOU = 0.40
DEFAULT_SMALL_ZONE_NMS_IOU = 0.70
DEFAULT_MAXIMUM_SMALL_SIDE_FRACTION = 0.04


def box_iou(left, right):
    if len(left) != 4 or len(right) != 4:
        raise ValueError("bounding boxes must contain four coordinates")
    x1 = max(float(left[0]), float(right[0]))
    y1 = max(float(left[1]), float(right[1]))
    x2 = min(float(left[2]), float(right[2]))
    y2 = min(float(left[3]), float(right[3]))
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, float(left[2]) - float(left[0])) * max(
        0.0, float(left[3]) - float(left[1])
    )
    right_area = max(0.0, float(right[2]) - float(right[0])) * max(
        0.0, float(right[3]) - float(right[1])
    )
    union = left_area + right_area - intersection
    return intersection / union if union > 0.0 else 0.0


def validate_box(box, width=None, height=None):
    if len(box) != 4:
        raise ValueError("bounding boxes must contain four coordinates")
    points = tuple(float(value) for value in box)
    if not all(math.isfinite(value) for value in points):
        raise ValueError("bounding box coordinates must be finite")
    x1, y1, x2, y2 = points
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bounding boxes must have positive width and height")
    if width is not None and not (0.0 <= x1 < x2 <= float(width)):
        raise ValueError("bounding box exceeds the image width")
    if height is not None and not (0.0 <= y1 < y2 <= float(height)):
        raise ValueError("bounding box exceeds the image height")
    return points


def has_zero_iou(candidate, existing_boxes):
    candidate = validate_box(candidate)
    return all(box_iou(candidate, existing) == 0.0 for existing in existing_boxes)


def normalized_zone(zone):
    x1, y1, x2, y2 = validate_box(zone)
    if not (0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0):
        raise ValueError("player/bench zones must use normalized coordinates within [0, 1]")
    return x1, y1, x2, y2


def box_center_in_zones(box, width, height, zones):
    x1, y1, x2, y2 = validate_box(box, width, height)
    center_x = ((x1 + x2) / 2.0) / float(width)
    center_y = ((y1 + y2) / 2.0) / float(height)
    return any(
        zone_x1 <= center_x <= zone_x2 and zone_y1 <= center_y <= zone_y2
        for zone_x1, zone_y1, zone_x2, zone_y2 in map(normalized_zone, zones)
    )


def is_small_box(box, width, height, maximum_side_fraction=0.04):
    if not 0.0 < maximum_side_fraction <= 1.0:
        raise ValueError("maximum small-box side fraction must be within (0, 1]")
    x1, y1, x2, y2 = validate_box(box, width, height)
    return (
        (x2 - x1) / float(width) <= maximum_side_fraction
        and (y2 - y1) / float(height) <= maximum_side_fraction
    )


def zone_aware_nms(
    boxes,
    scores,
    width,
    height,
    zones=(),
    default_iou=DEFAULT_NMS_IOU,
    small_zone_iou=DEFAULT_SMALL_ZONE_NMS_IOU,
    maximum_small_side_fraction=DEFAULT_MAXIMUM_SMALL_SIDE_FRACTION,
):
    if len(boxes) != len(scores):
        raise ValueError("boxes and scores must have the same length")
    if not 0.0 <= default_iou <= small_zone_iou <= 1.0:
        raise ValueError("NMS thresholds must satisfy 0 <= default <= small-zone <= 1")
    normalized_zones = tuple(normalized_zone(zone) for zone in zones)
    validated = [validate_box(box, width, height) for box in boxes]
    ranked = sorted(range(len(validated)), key=lambda index: (-float(scores[index]), index))
    retained = []
    for index in ranked:
        candidate = validated[index]
        candidate_is_small_zone = is_small_box(
            candidate, width, height, maximum_small_side_fraction
        ) and box_center_in_zones(candidate, width, height, normalized_zones)
        suppressed = False
        for retained_index in retained:
            retained_box = validated[retained_index]
            retained_is_small_zone = is_small_box(
                retained_box, width, height, maximum_small_side_fraction
            ) and box_center_in_zones(retained_box, width, height, normalized_zones)
            threshold = (
                small_zone_iou
                if candidate_is_small_zone and retained_is_small_zone
                else default_iou
            )
            if box_iou(candidate, retained_box) > threshold:
                suppressed = True
                break
        if not suppressed:
            retained.append(index)
    return retained


def map_crop_box_to_frame(box, crop):
    x1, y1, x2, y2 = validate_box(box)
    crop_x1, crop_y1, crop_x2, crop_y2 = validate_box(crop)
    crop_width = crop_x2 - crop_x1
    crop_height = crop_y2 - crop_y1
    if x2 > crop_width or y2 > crop_height:
        raise ValueError("crop-relative bounding box exceeds the crop dimensions")
    return (
        x1 + crop_x1,
        y1 + crop_y1,
        x2 + crop_x1,
        y2 + crop_y1,
    )
