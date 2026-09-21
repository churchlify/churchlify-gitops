from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from PIL import Image, UnidentifiedImageError


SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv"}
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CVAT_PROJECT_NAME = "Sportif Soccer Ball Annotation"
CVAT_TASK_PREFIX = "sportif-ball-"


@dataclass(frozen=True)
class Settings:
    endpoint_url: str
    raw_bucket: str
    frames_bucket: str
    provenance_bucket: str
    work_dir: Path
    frame_fps: float
    frame_phash_threshold: int

    @classmethod
    def from_environment(cls) -> "Settings":
        fps = float(os.environ.get("FRAME_FPS", "3"))
        if not math.isfinite(fps) or fps <= 0 or fps > 30:
            raise SystemExit("FRAME_FPS must be greater than 0 and no more than 30")
        try:
            threshold = int(os.environ.get("FRAME_PHASH_THRESHOLD", "8"))
        except ValueError as error:
            raise SystemExit("FRAME_PHASH_THRESHOLD must be an integer from 0 to 64") from error
        if threshold < 0 or threshold > 64:
            raise SystemExit("FRAME_PHASH_THRESHOLD must be an integer from 0 to 64")
        return cls(
            endpoint_url=_required_environment("AWS_ENDPOINT_URL"),
            raw_bucket=_required_environment("ML_RAW_BUCKET"),
            frames_bucket=_required_environment("ML_FRAMES_BUCKET"),
            provenance_bucket=_required_environment("ML_PROVENANCE_BUCKET"),
            work_dir=Path(os.environ.get("ML_WORK_DIR", "/work")),
            frame_fps=fps,
            frame_phash_threshold=threshold,
        )


def _required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"missing required environment variable: {name}")
    return value


def _safe_object_key(value: str, *, expected_suffixes: set[str] | None = None) -> str:
    path = PurePosixPath(value)
    if not value or value.startswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise SystemExit(f"invalid object key: {value!r}")
    if expected_suffixes and path.suffix.lower() not in expected_suffixes:
        raise SystemExit(f"unsupported object suffix for {value!r}")
    return value


def _s3_client(settings: Settings):
    import boto3

    return boto3.client("s3", endpoint_url=settings.endpoint_url)


def _download(s3, bucket: str, key: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        s3.download_file(bucket, key, str(destination))
    except Exception as error:
        raise SystemExit(f"failed to download s3://{bucket}/{key}: {error}") from error


def _upload_json(s3, bucket: str, key: str, document: Any) -> None:
    body = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")


def _download_json(s3, bucket: str, key: str) -> dict[str, Any]:
    try:
        document = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except Exception as error:
        raise SystemExit(f"failed to read JSON from s3://{bucket}/{key}: {error}") from error
    if not isinstance(document, dict):
        raise SystemExit(f"s3://{bucket}/{key} must contain one JSON object")
    return document


def _validate_selected_frames_manifest(
    document: dict[str, Any], video_id: str, source_sha256: str
) -> list[dict[str, Any]]:
    if document.get("schemaVersion") != 1:
        raise SystemExit("unsupported selected frames manifest schemaVersion")
    if document.get("videoId") != video_id:
        raise SystemExit("selected frames manifest videoId does not match provenance")
    if document.get("sourceVideoSha256") != source_sha256:
        raise SystemExit("selected frames manifest source SHA-256 does not match provenance")
    expected_source_key = f"videos/{video_id}/frames-manifest.json"
    if document.get("sourceManifestKey") != expected_source_key:
        raise SystemExit("selected frames manifest references an unexpected source manifest")
    selection = document.get("frameSelection")
    if not isinstance(selection, dict) or selection.get("algorithm") != "difference-hash-v1":
        raise SystemExit("selected frames manifest has an unsupported selection algorithm")
    frames = document.get("frames")
    if not isinstance(frames, list) or not frames:
        raise SystemExit("selected frames manifest must contain a non-empty frames list")
    if document.get("selectedFrameCount") != len(frames):
        raise SystemExit("selected frames manifest count does not match frames list")
    expected_prefix = f"videos/{video_id}/frames/"
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    previous_timestamp = -1.0
    for frame in frames:
        if not isinstance(frame, dict):
            raise SystemExit("selected frame entries must be JSON objects")
        frame_id = frame.get("frameId")
        object_key = frame.get("objectKey")
        timestamp = frame.get("timestampSeconds")
        if not isinstance(frame_id, str) or not re.fullmatch(r"frame-[0-9]{6}", frame_id):
            raise SystemExit("selected frame has an invalid frameId")
        if frame_id in seen_ids:
            raise SystemExit(f"selected frames manifest repeats frameId: {frame_id}")
        if frame.get("videoId") != video_id:
            raise SystemExit(f"selected frame belongs to another video: {frame_id}")
        if object_key != f"{expected_prefix}{frame_id}.jpg":
            raise SystemExit(f"selected frame has an invalid object key: {frame_id}")
        if object_key in seen_keys:
            raise SystemExit(f"selected frames manifest repeats object key: {object_key}")
        if not isinstance(frame.get("sha256"), str) or not SHA256_PATTERN.fullmatch(frame["sha256"]):
            raise SystemExit(f"selected frame has an invalid SHA-256: {frame_id}")
        if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
            raise SystemExit(f"selected frame has an invalid timestamp: {frame_id}")
        if timestamp < previous_timestamp:
            raise SystemExit("selected frames must be ordered chronologically")
        if not isinstance(frame.get("perceptualHash"), str) or not re.fullmatch(
            r"[0-9a-f]{16}", frame["perceptualHash"]
        ):
            raise SystemExit(f"selected frame has an invalid perceptual hash: {frame_id}")
        seen_ids.add(frame_id)
        seen_keys.add(object_key)
        previous_timestamp = float(timestamp)
    return frames


def _validate_frame_selection(
    document: dict[str, Any],
    video_id: str,
    source_sha256: str,
    selected_frames: list[dict[str, Any]],
) -> None:
    if document.get("schemaVersion") != 1 or document.get("status") != "PASS":
        raise SystemExit("frame selection provenance must have schemaVersion 1 and status PASS")
    if document.get("videoId") != video_id or document.get("sourceVideoSha256") != source_sha256:
        raise SystemExit("frame selection provenance identity does not match source provenance")
    if document.get("selectedManifestKey") != f"videos/{video_id}/selected-frames-manifest.json":
        raise SystemExit("frame selection provenance references an unexpected selected manifest")
    if document.get("selectedFrameCount") != len(selected_frames):
        raise SystemExit("frame selection provenance selected count does not match manifest")
    decisions = document.get("decisions")
    if not isinstance(decisions, list):
        raise SystemExit("frame selection provenance must contain decisions")
    selected_decisions = [
        item for item in decisions if isinstance(item, dict) and item.get("selected") is True
    ]
    if len(selected_decisions) != len(selected_frames):
        raise SystemExit("frame selection provenance decisions do not match selected count")
    expected = [
        (frame["frameId"], frame["perceptualHash"])
        for frame in selected_frames
    ]
    actual = [
        (decision.get("frameId"), decision.get("perceptualHash"))
        for decision in selected_decisions
    ]
    if actual != expected:
        raise SystemExit("frame selection provenance selected decisions do not match manifest")


def _encode_multipart(fields: list[tuple[str, Any]]) -> tuple[bytes, str]:
    boundary = f"sportif-ml-{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields:
        body.extend(f"--{boundary}\r\n".encode())
        if isinstance(value, tuple):
            filename, content, content_type = value
            body.extend(
                (
                    f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode()
            )
            body.extend(content)
        else:
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}'.encode())
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


class CvatClient:
    def __init__(self, base_url: str, token: str, *, opener=urlopen, sleep=time.sleep):
        if not base_url.startswith(("http://", "https://")):
            raise SystemExit("CVAT_API_URL must use http or https")
        if not token or any(character.isspace() for character in token):
            raise SystemExit("CVAT_API_TOKEN must be a non-empty token without whitespace")
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Token {token}", "Accept": "application/vnd.cvat+json"}
        self.opener = opener
        self.sleep = sleep

    def request(self, method: str, path: str, *, fields=None, body=None, headers=None) -> Any:
        request_headers = {**self.headers, **(headers or {})}
        if fields is not None:
            body, content_type = _encode_multipart(fields)
            request_headers["Content-Type"] = content_type
        request = Request(
            f"{self.base_url}{path}",
            data=body.encode() if isinstance(body, str) else body,
            headers=request_headers,
            method=method,
        )
        try:
            with self.opener(request, timeout=120) as response:
                status = response.status
                data = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:1000]
            raise SystemExit(f"CVAT API {method} {path} failed with HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise SystemExit(f"CVAT API {method} {path} connection failed: {error.reason}") from error
        if status < 200 or status >= 300:
            detail = data.decode("utf-8", errors="replace")[:1000]
            raise SystemExit(f"CVAT API {method} {path} failed with HTTP {status}: {detail}")
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError as error:
            raise SystemExit(f"CVAT API {method} {path} returned invalid JSON") from error

    def _exactly_one_or_none(self, resource: str, name: str) -> dict[str, Any] | None:
        document = self.request("GET", f"/api/{resource}?{urlencode({'name': name})}")
        results = document.get("results") if isinstance(document, dict) else None
        if not isinstance(results, list):
            raise SystemExit(f"CVAT {resource} lookup returned an invalid response")
        exact = [item for item in results if item.get("name") == name]
        count = document.get("count")
        if len(exact) > 1 or isinstance(count, int) and count > 1:
            raise SystemExit(f"CVAT contains duplicate {resource} named {name!r}")
        return exact[0] if exact else None

    def ensure_project(self) -> dict[str, Any]:
        project = self._exactly_one_or_none("projects", CVAT_PROJECT_NAME)
        if project is None:
            project = self.request(
                "POST",
                "/api/projects",
                body=json.dumps(
                    {"name": CVAT_PROJECT_NAME, "labels": [{"name": "ball", "type": "rectangle"}]}
                ),
                headers={"Content-Type": "application/json"},
            )
        labels = project.get("labels")
        if labels is None:
            project = self.request("GET", f"/api/projects/{project['id']}")
            labels = project.get("labels")
        if not isinstance(labels, list) or [(item.get("name"), item.get("type")) for item in labels] != [
            ("ball", "rectangle")
        ]:
            raise SystemExit("CVAT annotation project must contain only the rectangle label 'ball'")
        return project

    def ensure_task(self, video_id: str, project_id: int, frame_count: int) -> tuple[dict[str, Any], bool]:
        name = f"{CVAT_TASK_PREFIX}{video_id}"
        task = self._exactly_one_or_none("tasks", name)
        created = False
        if task is None:
            task = self.request(
                "POST",
                "/api/tasks",
                body=json.dumps({"name": name, "project_id": project_id, "segment_size": frame_count}),
                headers={"Content-Type": "application/json"},
            )
            created = True
        if task.get("project_id") != project_id:
            raise SystemExit("existing CVAT task belongs to an unexpected project")
        size = task.get("size", 0)
        if not isinstance(size, int) or size not in (0, frame_count):
            raise SystemExit("existing CVAT task has a frame count that does not match the selected manifest")
        return task, created

    def upload_images(self, task_id: int, paths: list[Path]) -> str:
        data_path = f"/api/tasks/{task_id}/data"
        self.request("POST", data_path, headers={"Upload-Start": ""})
        for offset in range(0, len(paths), 25):
            fields: list[tuple[str, Any]] = [("image_quality", "100")]
            for index, path in enumerate(paths[offset : offset + 25]):
                fields.append((f"client_files[{index}]", (path.name, path.read_bytes(), "image/jpeg")))
            self.request("POST", data_path, fields=fields, headers={"Upload-Multiple": ""})
        finish_fields: list[tuple[str, Any]] = [
            ("image_quality", "100"),
            ("sorting_method", "predefined"),
        ]
        finish_fields.extend(("upload_file_order", path.name) for path in paths)
        result = self.request(
            "POST", data_path, fields=finish_fields, headers={"Upload-Finish": ""}
        )
        request_id = result.get("rq_id") if isinstance(result, dict) else None
        if not request_id:
            raise SystemExit("CVAT data upload did not return a request ID")
        return request_id

    def wait_for_request(self, request_id: str, *, attempts: int = 180) -> dict[str, Any]:
        for _attempt in range(attempts):
            request = self.request("GET", f"/api/requests/{request_id}")
            status = request.get("status")
            if status == "finished":
                return request
            if status == "failed":
                raise SystemExit(f"CVAT request failed: {request.get('message', 'unknown error')}")
            if status not in {"queued", "started"}:
                raise SystemExit(f"CVAT request returned unexpected status: {status!r}")
            self.sleep(2)
        raise SystemExit("timed out waiting for CVAT data upload")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _difference_hash(path: Path) -> int:
    try:
        with Image.open(path) as image:
            grayscale = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = grayscale.tobytes()
    except (OSError, UnidentifiedImageError) as error:
        raise SystemExit(f"invalid frame image {path.name}: {error}") from error
    value = 0
    for row in range(8):
        offset = row * 9
        for column in range(8):
            value = (value << 1) | int(pixels[offset + column] > pixels[offset + column + 1])
    return value


def _hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def _select_frame_hashes(frame_hashes: list[tuple[str, int]], threshold: int) -> list[dict[str, Any]]:
    if not frame_hashes:
        raise SystemExit("frame manifest contains no frames")
    decisions = []
    previous_selected_id = ""
    previous_selected_hash = 0
    for index, (frame_id, frame_hash) in enumerate(frame_hashes):
        distance = None if index == 0 else _hamming_distance(previous_selected_hash, frame_hash)
        selected = index == 0 or distance is not None and distance > threshold
        decision = {
            "frameId": frame_id,
            "perceptualHash": f"{frame_hash:016x}",
            "selected": selected,
            "distanceFromPreviousSelected": distance,
        }
        if selected:
            previous_selected_id = frame_id
            previous_selected_hash = frame_hash
        else:
            decision["duplicateOfFrameId"] = previous_selected_id
        decisions.append(decision)
    return decisions


def _validate_frames_manifest(document: dict[str, Any], video_id: str, source_sha256: str) -> list[dict[str, Any]]:
    if document.get("schemaVersion") != 1:
        raise SystemExit("unsupported frames manifest schemaVersion")
    if document.get("videoId") != video_id:
        raise SystemExit("frames manifest videoId does not match provenance")
    if document.get("sourceVideoSha256") != source_sha256:
        raise SystemExit("frames manifest source SHA-256 does not match provenance")
    frames = document.get("frames")
    if not isinstance(frames, list) or not frames:
        raise SystemExit("frames manifest must contain a non-empty frames list")
    if document.get("frameCount") != len(frames):
        raise SystemExit("frames manifest frameCount does not match frames list")
    expected_prefix = f"videos/{video_id}/frames/"
    seen_ids: set[str] = set()
    seen_keys: set[str] = set()
    previous_timestamp = -1.0
    for frame in frames:
        if not isinstance(frame, dict):
            raise SystemExit("each frames manifest entry must be an object")
        frame_id = frame.get("frameId")
        object_key = frame.get("objectKey")
        timestamp = frame.get("timestampSeconds")
        if not isinstance(frame_id, str) or not re.fullmatch(r"frame-[0-9]{6}", frame_id):
            raise SystemExit("frames manifest contains an invalid frameId")
        if frame_id in seen_ids:
            raise SystemExit("frames manifest contains duplicate frameId values")
        if frame.get("videoId") != video_id:
            raise SystemExit("frames manifest entry videoId does not match provenance")
        if object_key != f"{expected_prefix}{frame_id}.jpg":
            raise SystemExit("frames manifest contains an unexpected frame object key")
        if object_key in seen_keys:
            raise SystemExit("frames manifest contains duplicate object keys")
        if not re.fullmatch(r"[0-9a-f]{64}", str(frame.get("sha256", ""))):
            raise SystemExit("frames manifest contains an invalid frame SHA-256")
        if not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
            raise SystemExit("frames manifest contains an invalid timestamp")
        if timestamp <= previous_timestamp:
            raise SystemExit("frames manifest timestamps must be strictly increasing")
        seen_ids.add(frame_id)
        seen_keys.add(object_key)
        previous_timestamp = float(timestamp)
    return frames


def _load_provenance(path: Path, video_key: str) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid provenance JSON: {error}") from error
    if not isinstance(document, dict):
        raise SystemExit("provenance must be one JSON object")
    required = {
        "schemaVersion",
        "videoId",
        "filename",
        "objectKey",
        "sha256",
        "source",
        "sourceRightsStatus",
        "aiTrainingPermission",
        "jurisdiction",
        "createdAtUtc",
    }
    missing = sorted(required - document.keys())
    if missing:
        raise SystemExit(f"provenance is missing fields: {', '.join(missing)}")
    if document["schemaVersion"] != 1:
        raise SystemExit("unsupported provenance schemaVersion")
    if document["objectKey"] != video_key:
        raise SystemExit("provenance objectKey does not match requested video")
    if document["filename"] != PurePosixPath(video_key).name:
        raise SystemExit("provenance filename does not match the video object key")
    if not VIDEO_ID_PATTERN.fullmatch(str(document["videoId"])):
        raise SystemExit("provenance videoId contains unsupported characters")
    if not re.fullmatch(r"[0-9a-f]{64}", str(document["sha256"])):
        raise SystemExit("provenance sha256 must be a lowercase SHA-256 hex digest")
    if document["sourceRightsStatus"] != "VERIFIED":
        raise SystemExit("sourceRightsStatus must be VERIFIED")
    if document["aiTrainingPermission"] is not True:
        raise SystemExit("aiTrainingPermission must be true")
    for field in ("source", "jurisdiction", "createdAtUtc"):
        if not isinstance(document[field], str) or not document[field].strip():
            raise SystemExit(f"provenance {field} must be a non-empty string")
    return document


def _probe_video(video: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate:format=duration",
        "-of",
        "json",
        str(video),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        duration = float(payload["format"]["duration"])
        fps = float(Fraction(stream["avg_frame_rate"]))
        width = int(stream["width"])
        height = int(stream["height"])
    except (subprocess.CalledProcessError, KeyError, IndexError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"ffprobe could not read a valid video stream: {error}") from error
    if duration <= 0 or fps <= 0 or width <= 0 or height <= 0:
        raise SystemExit("video metadata contains non-positive values")
    return {
        "durationSeconds": round(duration, 6),
        "width": width,
        "height": height,
        "fps": round(fps, 6),
    }


def _prepare_input(video_key: str, provenance_key: str, settings: Settings):
    video_key = _safe_object_key(video_key, expected_suffixes=SUPPORTED_VIDEO_SUFFIXES)
    provenance_key = _safe_object_key(provenance_key, expected_suffixes={".json"})
    run_dir = settings.work_dir / "input"
    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    video_path = run_dir / PurePosixPath(video_key).name
    provenance_path = run_dir / "source-provenance.json"
    s3 = _s3_client(settings)
    _download(s3, settings.provenance_bucket, provenance_key, provenance_path)
    provenance = _load_provenance(provenance_path, video_key)
    _download(s3, settings.raw_bucket, video_key, video_path)
    actual_sha256 = _sha256(video_path)
    if actual_sha256 != provenance["sha256"]:
        raise SystemExit("source video SHA-256 does not match provenance")
    media = _probe_video(video_path)
    return s3, video_path, provenance, media


def _validation_document(
    video_key: str,
    provenance_key: str,
    provenance: dict[str, Any],
    media: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "status": "PASS",
        "validatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "videoId": provenance["videoId"],
        "videoObject": {"bucket": os.environ["ML_RAW_BUCKET"], "key": video_key},
        "provenanceObject": {
            "bucket": os.environ["ML_PROVENANCE_BUCKET"],
            "key": provenance_key,
        },
        "sha256": provenance["sha256"],
        **media,
    }


def validate_input(video_key: str, provenance_key: str) -> None:
    settings = Settings.from_environment()
    s3, _, provenance, media = _prepare_input(video_key, provenance_key, settings)
    output_key = f"videos/{provenance['videoId']}/input-validation.json"
    document = _validation_document(video_key, provenance_key, provenance, media)
    _upload_json(s3, settings.provenance_bucket, output_key, document)
    print(json.dumps({"status": "PASS", "videoId": provenance["videoId"], "outputKey": output_key}))


def extract_frames(video_key: str, provenance_key: str) -> None:
    settings = Settings.from_environment()
    s3, video_path, provenance, media = _prepare_input(video_key, provenance_key, settings)
    video_id = provenance["videoId"]
    frames_dir = settings.work_dir / "frames" / video_id
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"fps={settings.frame_fps}",
            "-q:v",
            "2",
            str(frames_dir / "frame-%06d.jpg"),
        ],
        check=True,
    )
    frame_paths = sorted(frames_dir.glob("frame-*.jpg"))
    if not frame_paths:
        raise SystemExit("frame extraction produced no images")

    prefix = f"videos/{video_id}"
    frames = []
    for index, frame_path in enumerate(frame_paths):
        frame_key = f"{prefix}/frames/{frame_path.name}"
        s3.upload_file(
            str(frame_path),
            settings.frames_bucket,
            frame_key,
            ExtraArgs={"ContentType": "image/jpeg"},
        )
        frames.append(
            {
                "frameId": frame_path.stem,
                "videoId": video_id,
                "timestampSeconds": round(index / settings.frame_fps, 6),
                "sha256": _sha256(frame_path),
                "width": media["width"],
                "height": media["height"],
                "objectKey": frame_key,
            }
        )

    manifest_key = f"{prefix}/frames-manifest.json"
    manifest = {
        "schemaVersion": 1,
        "videoId": video_id,
        "sourceVideoSha256": provenance["sha256"],
        "frameExtraction": {
            "fps": settings.frame_fps,
            "imageFormat": "jpg",
            "ffmpegQuality": 2,
        },
        "frameCount": len(frames),
        "frames": frames,
    }
    _upload_json(s3, settings.frames_bucket, manifest_key, manifest)
    extraction_key = f"videos/{video_id}/frame-extraction.json"
    _upload_json(
        s3,
        settings.provenance_bucket,
        extraction_key,
        {
            "schemaVersion": 1,
            "status": "PASS",
            "completedAtUtc": datetime.now(timezone.utc).isoformat(),
            "videoId": video_id,
            "sourceVideoSha256": provenance["sha256"],
            "framesBucket": settings.frames_bucket,
            "framesPrefix": f"{prefix}/frames/",
            "manifestKey": manifest_key,
            "frameCount": len(frames),
            **media,
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "videoId": video_id,
                "frameCount": len(frames),
                "manifestKey": manifest_key,
            }
        )
    )


def deduplicate_frames(video_key: str, provenance_key: str) -> None:
    settings = Settings.from_environment()
    video_key = _safe_object_key(video_key, expected_suffixes=SUPPORTED_VIDEO_SUFFIXES)
    provenance_key = _safe_object_key(provenance_key, expected_suffixes={".json"})
    s3 = _s3_client(settings)
    input_dir = settings.work_dir / "selection-input"
    shutil.rmtree(input_dir, ignore_errors=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    provenance_path = input_dir / "source-provenance.json"
    _download(s3, settings.provenance_bucket, provenance_key, provenance_path)
    provenance = _load_provenance(provenance_path, video_key)
    video_id = provenance["videoId"]
    prefix = f"videos/{video_id}"
    source_manifest_key = f"{prefix}/frames-manifest.json"
    source_manifest = _download_json(s3, settings.frames_bucket, source_manifest_key)
    frames = _validate_frames_manifest(source_manifest, video_id, provenance["sha256"])

    frame_hashes = []
    for frame in frames:
        frame_path = input_dir / f"{frame['frameId']}.jpg"
        _download(s3, settings.frames_bucket, frame["objectKey"], frame_path)
        if _sha256(frame_path) != frame["sha256"]:
            raise SystemExit(f"frame SHA-256 does not match manifest: {frame['frameId']}")
        frame_hashes.append((frame["frameId"], _difference_hash(frame_path)))
        frame_path.unlink()

    decisions = _select_frame_hashes(frame_hashes, settings.frame_phash_threshold)
    decisions_by_id = {decision["frameId"]: decision for decision in decisions}
    selected_frames = [
        {**frame, "perceptualHash": decisions_by_id[frame["frameId"]]["perceptualHash"]}
        for frame in frames
        if decisions_by_id[frame["frameId"]]["selected"]
    ]
    selected_manifest_key = f"{prefix}/selected-frames-manifest.json"
    _upload_json(
        s3,
        settings.frames_bucket,
        selected_manifest_key,
        {
            "schemaVersion": 1,
            "videoId": video_id,
            "sourceVideoSha256": provenance["sha256"],
            "sourceManifestKey": source_manifest_key,
            "frameSelection": {
                "algorithm": "difference-hash-v1",
                "hashBits": 64,
                "comparison": "previous-selected-frame",
                "hammingDistanceThreshold": settings.frame_phash_threshold,
                "selectionRule": "select-first-or-distance-greater-than-threshold",
            },
            "originalFrameCount": len(frames),
            "selectedFrameCount": len(selected_frames),
            "frames": selected_frames,
        },
    )
    selection_key = f"{prefix}/frame-selection.json"
    _upload_json(
        s3,
        settings.provenance_bucket,
        selection_key,
        {
            "schemaVersion": 1,
            "status": "PASS",
            "completedAtUtc": datetime.now(timezone.utc).isoformat(),
            "videoId": video_id,
            "sourceVideoSha256": provenance["sha256"],
            "framesBucket": settings.frames_bucket,
            "sourceManifestKey": source_manifest_key,
            "selectedManifestKey": selected_manifest_key,
            "algorithm": "difference-hash-v1",
            "hashBits": 64,
            "comparison": "previous-selected-frame",
            "hammingDistanceThreshold": settings.frame_phash_threshold,
            "originalFrameCount": len(frames),
            "selectedFrameCount": len(selected_frames),
            "decisions": decisions,
        },
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "videoId": video_id,
                "originalFrameCount": len(frames),
                "selectedFrameCount": len(selected_frames),
                "selectedManifestKey": selected_manifest_key,
                "selectionKey": selection_key,
            }
        )
    )


def handoff_to_cvat(video_key: str, provenance_key: str) -> None:
    settings = Settings.from_environment()
    video_key = _safe_object_key(video_key, expected_suffixes=SUPPORTED_VIDEO_SUFFIXES)
    provenance_key = _safe_object_key(provenance_key, expected_suffixes={".json"})
    s3 = _s3_client(settings)
    input_dir = settings.work_dir / "cvat-handoff-input"
    shutil.rmtree(input_dir, ignore_errors=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    provenance_path = input_dir / "source-provenance.json"
    _download(s3, settings.provenance_bucket, provenance_key, provenance_path)
    provenance = _load_provenance(provenance_path, video_key)
    video_id = provenance["videoId"]
    prefix = f"videos/{video_id}"
    selected_manifest_key = f"{prefix}/selected-frames-manifest.json"
    selected_manifest = _download_json(s3, settings.frames_bucket, selected_manifest_key)
    frames = _validate_selected_frames_manifest(selected_manifest, video_id, provenance["sha256"])
    selection_key = f"{prefix}/frame-selection.json"
    selection = _download_json(s3, settings.provenance_bucket, selection_key)
    _validate_frame_selection(selection, video_id, provenance["sha256"], frames)

    paths = []
    for frame in frames:
        path = input_dir / f"{frame['frameId']}.jpg"
        _download(s3, settings.frames_bucket, frame["objectKey"], path)
        if _sha256(path) != frame["sha256"]:
            raise SystemExit(f"selected frame SHA-256 does not match manifest: {frame['frameId']}")
        paths.append(path)

    client = CvatClient(_required_environment("CVAT_API_URL"), _required_environment("CVAT_API_TOKEN"))
    project = client.ensure_project()
    task, created = client.ensure_task(video_id, project["id"], len(paths))
    if task.get("size", 0) == 0:
        request_id = client.upload_images(task["id"], paths)
        client.wait_for_request(request_id)
        task = client.request("GET", f"/api/tasks/{task['id']}")
    if task.get("size") != len(paths):
        raise SystemExit("CVAT task frame count does not match selected manifest after upload")

    handoff_key = f"{prefix}/cvat-handoff.json"
    _upload_json(
        s3,
        settings.provenance_bucket,
        handoff_key,
        {
            "schemaVersion": 1,
            "status": "PASS",
            "completedAtUtc": datetime.now(timezone.utc).isoformat(),
            "videoId": video_id,
            "sourceVideoSha256": provenance["sha256"],
            "selectedManifestKey": selected_manifest_key,
            "frameSelectionKey": selection_key,
            "selectedFrameCount": len(frames),
            "cvat": {
                "apiUrl": client.base_url,
                "projectId": project["id"],
                "projectName": CVAT_PROJECT_NAME,
                "taskId": task["id"],
                "taskName": task["name"],
                "taskCreatedByThisRun": created,
                "labelSchema": [{"name": "ball", "type": "rectangle"}],
                "assignee": task.get("assignee"),
                "status": task.get("status"),
            },
            "annotationPolicy": {
                "assignmentRequired": True,
                "humanReviewRequired": True,
                "exportRequired": True,
                "datasetApprovalGranted": False,
            },
        },
    )
    print(
        json.dumps(
            {"status": "PASS", "videoId": video_id, "taskId": task["id"], "handoffKey": handoff_key}
        )
    )
