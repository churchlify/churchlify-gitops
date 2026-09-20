import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sportif_ml_worker.pipeline import Settings, _load_provenance, _safe_object_key


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


if __name__ == "__main__":
    unittest.main()
