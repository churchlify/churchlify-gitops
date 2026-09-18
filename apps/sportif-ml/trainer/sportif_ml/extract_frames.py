import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def extract(video_key):
    root = Path(os.environ.get("ML_WORK_DIR", "/work"))
    video = root / "raw" / video_key
    if video.suffix.lower() not in {".mp4", ".mov", ".mkv"}:
        raise SystemExit("unsupported video format")
    output = root / "frames" / Path(video_key).stem
    output.mkdir(parents=True, exist_ok=True)
    fps = os.environ.get("FRAME_FPS", "3")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", str(video), "-vf", f"fps={fps}", "-q:v", "2", str(output / "frame-%06d.jpg")],
        check=True,
    )
    manifest = []
    for frame in sorted(output.glob("*.jpg")):
        manifest.append({"frameId": frame.stem, "videoId": output.name, "sha256": hashlib.sha256(frame.read_bytes()).hexdigest()})
    (output / "frames-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m sportif_ml.extract_frames OBJECT_KEY")
    extract(sys.argv[1])