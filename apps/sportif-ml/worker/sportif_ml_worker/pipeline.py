from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any


SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv"}
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class Settings:
    endpoint_url: str
    raw_bucket: str
    frames_bucket: str
    provenance_bucket: str
    work_dir: Path
    frame_fps: float

    @classmethod
    def from_environment(cls) -> "Settings":
        fps = float(os.environ.get("FRAME_FPS", "3"))
        if not math.isfinite(fps) or fps <= 0 or fps > 30:
            raise SystemExit("FRAME_FPS must be greater than 0 and no more than 30")
        return cls(
            endpoint_url=_required_environment("AWS_ENDPOINT_URL"),
            raw_bucket=_required_environment("ML_RAW_BUCKET"),
            frames_bucket=_required_environment("ML_FRAMES_BUCKET"),
            provenance_bucket=_required_environment("ML_PROVENANCE_BUCKET"),
            work_dir=Path(os.environ.get("ML_WORK_DIR", "/work")),
            frame_fps=fps,
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
