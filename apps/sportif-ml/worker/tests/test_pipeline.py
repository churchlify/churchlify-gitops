import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from sportif_ml_worker.pipeline import (
    Settings,
    _difference_hash,
    _load_provenance,
    _safe_object_key,
    _select_frame_hashes,
    _validate_frames_manifest,
)


class WorkerValidationTests(unittest.TestCase):
    def test_rejects_parent_traversal(self):
        with self.assertRaises(SystemExit):
            _safe_object_key("videos/../secret.mp4", expected_suffixes={".mp4"})

    def test_rejects_unapproved_rights(self):
        document = {
            "schemaVersion": 1,
            "videoId": "VID-000001",
            "filename": "input.mp4",
            "objectKey": "videos/input.mp4",
            "sha256": "a" * 64,
            "source": "Sportif-owned test footage",
            "sourceRightsStatus": "PENDING",
            "aiTrainingPermission": True,
            "jurisdiction": "Canada",
            "createdAtUtc": "2026-09-20T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provenance.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(SystemExit, "sourceRightsStatus"):
                _load_provenance(path, "videos/input.mp4")

    def test_accepts_complete_approved_provenance(self):
        document = {
            "schemaVersion": 1,
            "videoId": "VID-000001",
            "filename": "input.mp4",
            "objectKey": "videos/input.mp4",
            "sha256": "a" * 64,
            "source": "Sportif-owned test footage",
            "sourceRightsStatus": "VERIFIED",
            "aiTrainingPermission": True,
            "jurisdiction": "Canada",
            "createdAtUtc": "2026-09-20T00:00:00Z",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "provenance.json"
            path.write_text(json.dumps(document))
            self.assertEqual(
                _load_provenance(path, "videos/input.mp4")["videoId"],
                "VID-000001",
            )

    def test_settings_reject_excessive_frame_rate(self):
        environment = {
            "AWS_ENDPOINT_URL": "http://minio:9000",
            "ML_RAW_BUCKET": "raw",
            "ML_FRAMES_BUCKET": "frames",
            "ML_PROVENANCE_BUCKET": "provenance",
            "FRAME_FPS": "60",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(SystemExit, "FRAME_FPS"):
                Settings.from_environment()

    def test_settings_rejects_invalid_hash_threshold(self):
        environment = {
            "AWS_ENDPOINT_URL": "http://minio:9000",
            "ML_RAW_BUCKET": "raw",
            "ML_FRAMES_BUCKET": "frames",
            "ML_PROVENANCE_BUCKET": "provenance",
            "FRAME_FPS": "3",
            "FRAME_PHASH_THRESHOLD": "65",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(SystemExit, "FRAME_PHASH_THRESHOLD"):
                Settings.from_environment()

    def test_difference_hash_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "frame.jpg"
            image = Image.new("L", (9, 8))
            image.putdata([column * 20 for _row in range(8) for column in range(9)])
            image.save(path, quality=100, subsampling=0)
            self.assertEqual(_difference_hash(path), 0)
            self.assertEqual(_difference_hash(path), _difference_hash(path))

    def test_selects_first_and_frames_above_threshold(self):
        decisions = _select_frame_hashes(
            [
                ("frame-000001", 0),
                ("frame-000002", 1),
                ("frame-000003", (1 << 10) - 1),
            ],
            threshold=8,
        )
        self.assertEqual([item["selected"] for item in decisions], [True, False, True])
        self.assertEqual(decisions[1]["duplicateOfFrameId"], "frame-000001")
        self.assertEqual(decisions[2]["distanceFromPreviousSelected"], 10)

    def test_manifest_rejects_cross_video_frame_key(self):
        document = {
            "schemaVersion": 1,
            "videoId": "VID-000001",
            "sourceVideoSha256": "a" * 64,
            "frameCount": 1,
            "frames": [
                {
                    "frameId": "frame-000001",
                    "videoId": "VID-000001",
                    "timestampSeconds": 0,
                    "sha256": "b" * 64,
                    "width": 1920,
                    "height": 1080,
                    "objectKey": "videos/VID-OTHER/frames/frame-000001.jpg",
                }
            ],
        }
        with self.assertRaisesRegex(SystemExit, "object key"):
            _validate_frames_manifest(document, "VID-000001", "a" * 64)

    def test_manifest_accepts_ordered_verified_frames(self):
        frames = [
            {
                "frameId": f"frame-{index:06d}",
                "videoId": "VID-000001",
                "timestampSeconds": index - 1,
                "sha256": f"{index:064x}",
                "width": 1920,
                "height": 1080,
                "objectKey": f"videos/VID-000001/frames/frame-{index:06d}.jpg",
            }
            for index in (1, 2)
        ]
        document = {
            "schemaVersion": 1,
            "videoId": "VID-000001",
            "sourceVideoSha256": "a" * 64,
            "frameCount": len(frames),
            "frames": frames,
        }
        self.assertEqual(
            _validate_frames_manifest(document, "VID-000001", "a" * 64),
            frames,
        )


if __name__ == "__main__":
    unittest.main()
