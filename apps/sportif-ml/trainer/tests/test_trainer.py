import tempfile
import unittest
from pathlib import Path

import torch
from PIL import Image

from sportif_ml.evaluate import average_precision, box_iou
from sportif_ml.storage import REQUIRED_DATASET_FILES, _parse_checksums
from sportif_ml.train import YoloDetectionDataset


class TrainerTests(unittest.TestCase):
    def test_negative_frame_has_empty_n_by_four_boxes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_dir = root / "train" / "images" / "video-1"
            label_dir = root / "train" / "labels" / "video-1"
            image_dir.mkdir(parents=True)
            label_dir.mkdir(parents=True)
            Image.new("RGB", (16, 12)).save(image_dir / "frame.jpg")
            (label_dir / "frame.txt").write_text("")

            _, target = YoloDetectionDataset(root, "train")[0]

            self.assertEqual(tuple(target["boxes"].shape), (0, 4))
            self.assertEqual(tuple(target["labels"].shape), (0,))

    def test_iou_and_average_precision_match_one_detection(self):
        truth = torch.tensor([[0.0, 0.0, 10.0, 10.0]])
        overlap = box_iou(truth, truth)
        self.assertEqual(overlap.item(), 1.0)

        result = average_precision([(0.9, 0, truth[0])], [truth], 0.5)
        self.assertEqual(result, (1.0, 1, 0, 0))

    def test_false_positive_on_negative_frame(self):
        prediction = torch.tensor([0.0, 0.0, 4.0, 4.0])
        result = average_precision([(0.8, 0, prediction)], [torch.empty((0, 4))], 0.5)
        self.assertEqual(result, (0.0, 0, 1, 0))

    def test_dataset_inventory_uses_path_objects_for_required_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SHA256SUMS").write_text(
                "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  "
                "train/labels/video/frame.txt\n"
            )
            checksums = _parse_checksums(root / "SHA256SUMS")
            expected = set(checksums) | {Path(name) for name in REQUIRED_DATASET_FILES}

            self.assertIn(Path("dataset-manifest.json"), expected)
            self.assertNotIn("dataset-manifest.json", expected)


if __name__ == "__main__":
    unittest.main()