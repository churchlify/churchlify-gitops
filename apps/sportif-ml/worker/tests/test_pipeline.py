import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from sportif_ml_worker.pipeline import (
    CVAT_PROJECT_NAME,
    CvatClient,
    Settings,
    _difference_hash,
    _encode_multipart,
    _load_provenance,
    _safe_object_key,
    _select_frame_hashes,
    _validate_frame_selection,
    _validate_frames_manifest,
    _validate_selected_frames_manifest,
)


class WorkerValidationTests(unittest.TestCase):
    @staticmethod
    def selected_manifest(video_id="VID-000001"):
        return {
            "schemaVersion": 1,
            "videoId": video_id,
            "sourceVideoSha256": "a" * 64,
            "sourceManifestKey": f"videos/{video_id}/frames-manifest.json",
            "frameSelection": {"algorithm": "difference-hash-v1"},
            "originalFrameCount": 2,
            "selectedFrameCount": 1,
            "frames": [
                {
                    "frameId": "frame-000001",
                    "videoId": video_id,
                    "timestampSeconds": 0,
                    "sha256": "b" * 64,
                    "width": 1920,
                    "height": 1080,
                    "objectKey": f"videos/{video_id}/frames/frame-000001.jpg",
                    "perceptualHash": "0123456789abcdef",
                }
            ],
        }

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

    def test_selected_manifest_rejects_cross_video_object(self):
        document = self.selected_manifest()
        document["frames"][0]["objectKey"] = "videos/VID-OTHER/frames/frame-000001.jpg"
        with self.assertRaisesRegex(SystemExit, "object key"):
            _validate_selected_frames_manifest(document, "VID-000001", "a" * 64)

    def test_frame_selection_requires_matching_selected_count(self):
        document = {
            "schemaVersion": 1,
            "status": "PASS",
            "videoId": "VID-000001",
            "sourceVideoSha256": "a" * 64,
            "selectedManifestKey": "videos/VID-000001/selected-frames-manifest.json",
            "selectedFrameCount": 2,
            "decisions": [{"selected": True}, {"selected": True}],
        }
        with self.assertRaisesRegex(SystemExit, "selected count"):
            _validate_frame_selection(
                document, "VID-000001", "a" * 64, self.selected_manifest()["frames"]
            )

    def test_frame_selection_requires_matching_selected_decisions(self):
        document = {
            "schemaVersion": 1,
            "status": "PASS",
            "videoId": "VID-000001",
            "sourceVideoSha256": "a" * 64,
            "selectedManifestKey": "videos/VID-000001/selected-frames-manifest.json",
            "selectedFrameCount": 1,
            "decisions": [
                {
                    "frameId": "frame-000002",
                    "perceptualHash": "fedcba9876543210",
                    "selected": True,
                }
            ],
        }
        with self.assertRaisesRegex(SystemExit, "do not match manifest"):
            _validate_frame_selection(
                document, "VID-000001", "a" * 64, self.selected_manifest()["frames"]
            )

    def test_cvat_client_rejects_duplicate_named_tasks(self):
        client = CvatClient("http://cvat", "token")
        with patch.object(
            client,
            "request",
            return_value={
                "results": [
                    {"id": 1, "name": "sportif-ball-VID-000001"},
                    {"id": 2, "name": "sportif-ball-VID-000001"},
                ]
            },
        ):
            with self.assertRaisesRegex(SystemExit, "duplicate tasks"):
                client.ensure_task("VID-000001", 10, 1)

    def test_cvat_client_rejects_existing_task_with_wrong_size(self):
        client = CvatClient("http://cvat", "token")
        with patch.object(
            client,
            "request",
            return_value={
                "results": [
                    {
                        "id": 1,
                        "name": "sportif-ball-VID-000001",
                        "project_id": 10,
                        "size": 2,
                    }
                ]
            },
        ):
            with self.assertRaisesRegex(SystemExit, "frame count"):
                client.ensure_task("VID-000001", 10, 1)

    def test_cvat_project_requires_only_ball_rectangle_label(self):
        client = CvatClient("http://cvat", "token")
        with patch.object(
            client,
            "request",
            return_value={
                "results": [
                    {
                        "id": 10,
                        "name": CVAT_PROJECT_NAME,
                        "labels": [{"name": "player", "type": "rectangle"}],
                    }
                ]
            },
        ):
            with self.assertRaisesRegex(SystemExit, "only the rectangle label 'ball'"):
                client.ensure_project()

    def test_cvat_request_uses_token_authorization(self):
        class Response:
            status = 200

            def read(self):
                return b'{"results": []}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return Response()

        client = CvatClient("http://cvat", "secret-token", opener=opener)
        client.request("GET", "/api/tasks")
        self.assertEqual(requests[0][0].get_header("Authorization"), "Token secret-token")
        self.assertEqual(requests[0][1], 120)

    def test_multipart_encoder_preserves_repeated_file_order_fields(self):
        body, content_type = _encode_multipart(
            [
                ("upload_file_order", "frame-000001.jpg"),
                ("upload_file_order", "frame-000002.jpg"),
            ]
        )
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        text = body.decode()
        self.assertLess(text.index("frame-000001.jpg"), text.index("frame-000002.jpg"))


if __name__ == "__main__":
    unittest.main()
