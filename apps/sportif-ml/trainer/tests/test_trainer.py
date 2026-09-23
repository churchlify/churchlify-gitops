import tempfile
import unittest
from pathlib import Path

import torch
from PIL import Image

from sportif_ml.evaluate import (
    average_precision,
    box_iou,
    evaluate_quality_gate,
    persist_evaluation_result,
)
from sportif_ml.model import SMALL_OBJECT_ANCHOR_SIZES, build_model
from sportif_ml.provenance import require_passing_evaluation
from sportif_ml.storage import REQUIRED_DATASET_FILES, _parse_checksums
from sportif_ml.train import BalancedBatchSampler, YoloDetectionDataset, is_better_checkpoint


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

    def test_zero_quality_metrics_fail_closed(self):
        decision = evaluate_quality_gate(
            {"mAP50": 0.0, "mAP50-95": 0.0, "precision": 0.0, "recall": 0.0},
            {},
        )

        self.assertEqual(decision["qualityGateStatus"], "FAIL")
        self.assertEqual(len(decision["qualityGateFailures"]), 4)

    def test_quality_threshold_boundaries_pass(self):
        decision = evaluate_quality_gate(
            {"mAP50": 0.50, "mAP50-95": 0.20, "precision": 0.60, "recall": 0.60},
            {},
        )

        self.assertEqual(decision["qualityGateStatus"], "PASS")
        self.assertEqual(decision["qualityGateFailures"], [])

    def test_configured_quality_thresholds_are_applied(self):
        decision = evaluate_quality_gate(
            {"mAP50": 0.79, "mAP50-95": 0.39, "precision": 0.69, "recall": 0.59},
            {
                "EVALUATION_MIN_MAP50": "0.80",
                "EVALUATION_MIN_MAP50_95": "0.40",
                "EVALUATION_MIN_PRECISION": "0.70",
                "EVALUATION_MIN_RECALL": "0.60",
            },
        )

        self.assertEqual(decision["qualityGateStatus"], "FAIL")
        self.assertEqual(
            {item["metric"] for item in decision["qualityGateFailures"]},
            {"mAP50", "mAP50-95", "precision", "recall"},
        )

    def test_provenance_rejects_failed_quality_gate(self):
        with self.assertRaisesRegex(SystemExit, "model quality gate must pass"):
            require_passing_evaluation(
                {
                    "executionStatus": "PASS",
                    "qualityGateStatus": "FAIL",
                    "qualityGateFailures": [{"metric": "mAP50"}],
                }
            )

    def test_provenance_accepts_clean_passing_quality_gate(self):
        require_passing_evaluation(
            {
                "executionStatus": "PASS",
                "qualityGateStatus": "PASS",
                "qualityGateFailures": [],
            }
        )

    def test_failed_quality_result_is_persisted_before_exit(self):
        result = {
            "executionStatus": "PASS",
            "qualityGateStatus": "FAIL",
            "qualityGateFailures": [
                {"metric": "mAP50", "actual": 0.0, "minimum": 0.5}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metrics.json"

            with self.assertRaisesRegex(SystemExit, "model quality gate failed"):
                persist_evaluation_result(path, result)

            self.assertEqual(__import__("json").loads(path.read_text()), result)

    def test_balanced_sampler_contains_positive_and_negative_frames(self):
        sampler = BalancedBatchSampler([0, 2, 4], [1, 3, 5, 7], batch_size=2, seed=42)

        batches = list(sampler)

        self.assertTrue(batches)
        for batch in batches:
            self.assertEqual(len(batch), 2)
            self.assertEqual(len(set(batch) & {0, 2, 4}), 1)
            self.assertEqual(len(set(batch) & {1, 3, 5, 7}), 1)

    def test_balanced_sampler_is_deterministic_per_epoch(self):
        sampler = BalancedBatchSampler([0, 2, 4], [1, 3, 5], batch_size=2, seed=42)
        first = list(sampler)
        second = list(sampler)
        sampler.set_epoch(1)
        third = list(sampler)

        self.assertEqual(first, second)
        self.assertNotEqual(first, third)

    def test_best_checkpoint_prefers_map50_then_map50_95(self):
        baseline = {"mAP50": 0.40, "mAP50-95": 0.20}

        self.assertTrue(is_better_checkpoint({"mAP50": 0.41, "mAP50-95": 0.10}, baseline, 0.001))
        self.assertTrue(is_better_checkpoint({"mAP50": 0.40, "mAP50-95": 0.21}, baseline, 0.001))
        self.assertFalse(is_better_checkpoint({"mAP50": 0.40, "mAP50-95": 0.20}, baseline, 0.001))

    def test_model_uses_small_object_anchors(self):
        model = build_model()

        self.assertEqual(model.rpn.anchor_generator.sizes, SMALL_OBJECT_ANCHOR_SIZES)
        self.assertEqual(model.rpn.anchor_generator.num_anchors_per_location(), [9] * 5)


if __name__ == "__main__":
    unittest.main()