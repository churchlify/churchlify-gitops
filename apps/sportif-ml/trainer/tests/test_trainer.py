import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
from PIL import Image

from sportif_ml.evaluate import (
    average_precision,
    box_iou,
    evaluate_quality_gate,
    persist_evaluation_result,
)
from sportif_ml.model import SMALL_OBJECT_ANCHOR_SIZES, build_model, image_resize_policy
from sportif_ml.metrics import (
    calibrate_score_threshold,
    object_size_recall,
    per_source_metrics,
)
from sportif_ml.provenance import require_passing_evaluation
from sportif_ml.storage import REQUIRED_DATASET_FILES, _parse_checksums
from sportif_ml.train import (
    BalancedBatchSampler,
    YoloDetectionDataset,
    augment_detection,
    consume_early_stopping_patience,
    is_better_checkpoint,
    require_stable_loss,
    summarize_augmentation_scales,
    threshold_grid,
)


class TrainerTests(unittest.TestCase):
    class FixedRandom:
        def __init__(self, uniform_values, randint_values, random_values):
            self.uniform_values = iter(uniform_values)
            self.randint_values = iter(randint_values)
            self.random_values = iter(random_values)

        def uniform(self, _minimum, _maximum):
            return next(self.uniform_values)

        def randint(self, _minimum, _maximum):
            return next(self.randint_values)

        def random(self):
            return next(self.random_values)

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

    def test_source_aware_sampler_round_robins_sources(self):
        sampler = BalancedBatchSampler(
            [0, 2, 4, 6],
            [1, 3, 5, 7],
            batch_size=2,
            seed=42,
            source_by_index={0: "a", 1: "a", 2: "a", 3: "a", 4: "b", 5: "b", 6: "b", 7: "b"},
        )

        positive_order = sampler._ordered_indices([0, 2, 4, 6], __import__("random").Random(42))

        self.assertTrue(sampler.source_balancing_active)
        self.assertEqual(
            [sampler.source_by_index[index] for index in positive_order],
            ["a", "b", "a", "b"],
        )

    def test_source_balancing_is_inactive_for_one_source(self):
        sampler = BalancedBatchSampler(
            [0, 2], [1, 3], batch_size=2, seed=42,
            source_by_index={0: "only", 1: "only", 2: "only", 3: "only"},
        )

        self.assertFalse(sampler.source_balancing_active)
        self.assertEqual(sampler.sources, ["only"])

    def test_tiny_positive_replay_preserves_negative_exposure(self):
        sampler = BalancedBatchSampler(
            [0, 2, 4],
            [1, 3, 5, 7],
            batch_size=2,
            seed=42,
            replay_positive_indices=[0],
            replay_factor=4,
        )

        batches = list(sampler)
        positive_draws = [
            item
            for batch in batches
            for item in batch
            if item not in {1, 3, 5, 7}
        ]

        self.assertEqual(len(sampler.positive_indices), 6)
        replay_draws = sum(
            item == 0 or isinstance(item, tuple) and item[0] == 0
            for item in positive_draws
        )
        self.assertEqual(replay_draws, 4)
        self.assertTrue(all(len(set(batch) & {1, 3, 5, 7}) == 1 for batch in batches))

    def test_tiny_positive_replay_uses_distinct_deterministic_augmentation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_dir = root / "train" / "images" / "video-1"
            label_dir = root / "train" / "labels" / "video-1"
            image_dir.mkdir(parents=True)
            label_dir.mkdir(parents=True)
            Image.new("RGB", (100, 80), "white").save(image_dir / "frame.jpg")
            (label_dir / "frame.txt").write_text("0 0.5 0.5 0.1 0.1\n")
            dataset = YoloDetectionDataset(
                root,
                "train",
                augment=True,
                seed=42,
                augmentation={"scaleMin": 0.6, "scaleMax": 0.9},
            )

            base_image, base_target = dataset[0]
            replay_image, replay_target = dataset[(0, 1)]
            repeated_replay_image, repeated_replay_target = dataset[(0, 1)]

            self.assertFalse(
                torch.equal(base_image, replay_image)
                and torch.equal(base_target["boxes"], replay_target["boxes"])
            )
            torch.testing.assert_close(replay_image, repeated_replay_image)
            torch.testing.assert_close(replay_target["boxes"], repeated_replay_target["boxes"])

    def test_dataset_identifies_tiny_annotations_by_maximum_side(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_dir = root / "train" / "images" / "video-1"
            label_dir = root / "train" / "labels" / "video-1"
            image_dir.mkdir(parents=True)
            label_dir.mkdir(parents=True)
            Image.new("RGB", (100, 80), "white").save(image_dir / "tiny.jpg")
            Image.new("RGB", (100, 80), "white").save(image_dir / "small.jpg")
            (label_dir / "tiny.txt").write_text("0 0.5 0.5 0.1 0.1\n")
            (label_dir / "small.txt").write_text("0 0.5 0.5 0.2 0.1\n")
            dataset = YoloDetectionDataset(root, "train")

            self.assertFalse(dataset.has_tiny_annotations(0))
            self.assertTrue(dataset.has_tiny_annotations(1))

    def test_tiny_positive_replay_rejects_invalid_configuration(self):
        with self.assertRaisesRegex(ValueError, "at least one"):
            BalancedBatchSampler([0], [1], 2, 42, replay_factor=0)
        with self.assertRaisesRegex(ValueError, "positive frames"):
            BalancedBatchSampler([0], [1], 2, 42, replay_positive_indices=[2], replay_factor=2)

    def test_zoom_out_and_horizontal_flip_transform_boxes(self):
        image = Image.new("RGB", (100, 80), "white")
        boxes = torch.tensor([[10.0, 20.0, 30.0, 40.0]])
        randomizer = self.FixedRandom([0.5], [10, 5], [0.0])

        transformed_image, transformed_boxes = augment_detection(
            image,
            boxes,
            randomizer,
            {"scaleMin": 0.5, "scaleMax": 0.5, "horizontalFlipProbability": 1.0},
        )

        self.assertEqual(transformed_image.size, image.size)
        torch.testing.assert_close(
            transformed_boxes,
            torch.tensor([[75.0, 15.0, 85.0, 25.0]]),
        )

    def test_augmentation_preserves_empty_negative_boxes(self):
        image = Image.new("RGB", (100, 80), "white")
        randomizer = self.FixedRandom([0.5], [10, 5], [0.0])

        _, boxes = augment_detection(
            image,
            torch.empty((0, 4)),
            randomizer,
            {"scaleMin": 0.5, "scaleMax": 0.5, "horizontalFlipProbability": 1.0},
        )

        self.assertEqual(tuple(boxes.shape), (0, 4))

    def test_zoom_out_is_clamped_to_minimum_object_side(self):
        image = Image.new("RGB", (100, 80), "white")
        boxes = torch.tensor([[10.0, 20.0, 20.0, 30.0]])
        randomizer = self.FixedRandom([0.5], [10, 5], [1.0])

        _, transformed_boxes, metadata = augment_detection(
            image,
            boxes,
            randomizer,
            {
                "scaleMin": 0.5,
                "scaleMax": 0.5,
                "horizontalFlipProbability": 0.0,
                "minimumObjectSide": 8.0,
            },
            return_metadata=True,
        )

        self.assertAlmostEqual(metadata["requestedScale"], 0.5)
        self.assertAlmostEqual(metadata["appliedScale"], 0.8)
        self.assertTrue(metadata["scaleClamped"])
        self.assertAlmostEqual(float((transformed_boxes[:, 2:] - transformed_boxes[:, :2]).min()), 8.0)

    def test_negative_frame_keeps_full_zoom_out_range(self):
        image = Image.new("RGB", (100, 80), "white")
        randomizer = self.FixedRandom([0.5], [10, 5], [1.0])

        _, _, metadata = augment_detection(
            image,
            torch.empty((0, 4)),
            randomizer,
            {"scaleMin": 0.5, "scaleMax": 0.5, "minimumObjectSide": 8.0},
            return_metadata=True,
        )

        self.assertEqual(metadata["appliedScale"], 0.5)
        self.assertFalse(metadata["scaleClamped"])

    def test_augmentation_scale_summary_reports_clamping(self):
        summary = summarize_augmentation_scales([
            {"requestedScale": 0.5, "appliedScale": 0.8, "scaleClamped": True},
            {"requestedScale": 0.7, "appliedScale": 0.7, "scaleClamped": False},
        ])

        self.assertEqual(summary["samples"], 2)
        self.assertEqual(summary["clampedSamples"], 1)
        self.assertEqual(summary["minimumRequestedScale"], 0.5)
        self.assertEqual(summary["minimumAppliedScale"], 0.7)

    def test_dataset_augmentation_is_deterministic_per_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image_dir = root / "train" / "images" / "video-1"
            label_dir = root / "train" / "labels" / "video-1"
            image_dir.mkdir(parents=True)
            label_dir.mkdir(parents=True)
            Image.new("RGB", (32, 24), "white").save(image_dir / "frame.jpg")
            (label_dir / "frame.txt").write_text("0 0.5 0.5 0.25 0.25\n")
            dataset = YoloDetectionDataset(
                root,
                "train",
                augment=True,
                seed=42,
                augmentation={"scaleMin": 0.6, "scaleMax": 0.9, "horizontalFlipProbability": 0.5},
            )

            first_image, first_target = dataset[0]
            second_image, second_target = dataset[0]
            dataset.set_epoch(1)
            third_image, third_target = dataset[0]

            torch.testing.assert_close(first_image, second_image)
            torch.testing.assert_close(first_target["boxes"], second_target["boxes"])
            self.assertFalse(
                torch.equal(first_image, third_image)
                and torch.equal(first_target["boxes"], third_target["boxes"])
            )

    def test_per_source_metrics_partition_outputs(self):
        class Dataset:
            def source_id(self, index):
                return ["source-a", "source-b"][index]

        truth = [torch.tensor([[0.0, 0.0, 10.0, 10.0]]), torch.empty((0, 4))]
        outputs = [
            {"scores": torch.tensor([0.9]), "boxes": torch.tensor([[0.0, 0.0, 10.0, 10.0]])},
            {"scores": torch.tensor([0.8]), "boxes": torch.tensor([[0.0, 0.0, 5.0, 5.0]])},
        ]

        metrics = per_source_metrics(outputs, truth, Dataset(), 0.5)

        self.assertEqual(metrics["source-a"]["recall"], 1.0)
        self.assertEqual(metrics["source-b"]["falsePositives"], 1)

    def test_object_size_recall_reports_bands(self):
        truth = [torch.tensor([
            [0.0, 0.0, 8.0, 8.0],
            [20.0, 20.0, 36.0, 36.0],
            [50.0, 50.0, 80.0, 80.0],
        ])]
        outputs = [{
            "scores": torch.tensor([0.9, 0.9]),
            "boxes": torch.tensor([[0.0, 0.0, 8.0, 8.0], [50.0, 50.0, 80.0, 80.0]]),
        }]

        metrics = object_size_recall(outputs, truth, 0.5)

        self.assertEqual(metrics["tiny"]["recall"], 1.0)
        self.assertEqual(metrics["small"]["recall"], 0.0)
        self.assertEqual(metrics["larger"]["recall"], 1.0)

    def test_best_checkpoint_prefers_map50_then_map50_95(self):
        baseline = {
            "mAP50": 0.40,
            "mAP50-95": 0.20,
            "objectSizeRecall": {"tiny": {"recall": 0.30}},
        }

        improved_map = {"mAP50": 0.41, "mAP50-95": 0.10, "objectSizeRecall": {"tiny": {"recall": 0.20}}}
        improved_tiny = {"mAP50": 0.40, "mAP50-95": 0.10, "objectSizeRecall": {"tiny": {"recall": 0.40}}}
        unchanged = {"mAP50": 0.40, "mAP50-95": 0.20, "objectSizeRecall": {"tiny": {"recall": 0.30}}}

        self.assertTrue(is_better_checkpoint(improved_map, baseline, 0.001, 0.20))
        self.assertTrue(is_better_checkpoint(improved_tiny, baseline, 0.001, 0.20))
        self.assertFalse(is_better_checkpoint(unchanged, baseline, 0.001, 0.20))

    def test_checkpoint_rejects_tiny_recall_below_floor(self):
        metrics = {
            "mAP50": 0.50,
            "mAP50-95": 0.25,
            "objectSizeRecall": {"tiny": {"recall": 0.19}},
        }

        self.assertFalse(is_better_checkpoint(metrics, None, 0.001, 0.20))

    def test_early_stopping_patience_starts_after_first_eligible_checkpoint(self):
        self.assertFalse(consume_early_stopping_patience(None))
        self.assertTrue(consume_early_stopping_patience({"mAP50": 0.40}))

    def test_model_uses_small_object_anchors(self):
        with patch.dict("os.environ", {
            "TRAINING_MIN_IMAGE_SIZE": "720",
            "TRAINING_MAX_IMAGE_SIZE": "1280",
        }):
            model = build_model()

        self.assertEqual(model.rpn.anchor_generator.sizes, SMALL_OBJECT_ANCHOR_SIZES)
        self.assertEqual(model.rpn.anchor_generator.num_anchors_per_location(), [9] * 5)
        self.assertEqual(model.transform.min_size, (720,))
        self.assertEqual(model.transform.max_size, 1280)

    def test_image_resize_policy_validates_bounds(self):
        self.assertEqual(
            image_resize_policy({"TRAINING_MIN_IMAGE_SIZE": "720", "TRAINING_MAX_IMAGE_SIZE": "1280"}),
            (720, 1280),
        )
        with self.assertRaisesRegex(ValueError, "resize bounds"):
            image_resize_policy({"TRAINING_MIN_IMAGE_SIZE": "1280", "TRAINING_MAX_IMAGE_SIZE": "720"})

    def test_threshold_grid_includes_both_boundaries(self):
        self.assertEqual(
            threshold_grid(0.05, 0.20, 0.05),
            [0.05, 0.10, 0.15, 0.20],
        )

    def test_threshold_calibration_reduces_false_positives_with_recall_floor(self):
        truth = [torch.tensor([[0.0, 0.0, 10.0, 10.0]]), torch.empty((0, 4))]
        outputs = [
            {
                "scores": torch.tensor([0.90, 0.20]),
                "boxes": torch.tensor([[0.0, 0.0, 10.0, 10.0], [20.0, 20.0, 30.0, 30.0]]),
            },
            {
                "scores": torch.tensor([0.30]),
                "boxes": torch.tensor([[0.0, 0.0, 5.0, 5.0]]),
            },
        ]

        selected, sweep = calibrate_score_threshold(outputs, truth, [0.05, 0.50, 0.95], 0.50)

        self.assertEqual(selected["scoreThreshold"], 0.50)
        self.assertEqual(selected["precision"], 1.0)
        self.assertEqual(selected["recall"], 1.0)
        self.assertTrue(selected["minimumRecallSatisfied"])
        self.assertEqual(len(sweep), 3)

    def test_threshold_calibration_marks_unsatisfied_recall_floor(self):
        truth = [torch.tensor([[0.0, 0.0, 10.0, 10.0]])]
        outputs = [{"scores": torch.tensor([]), "boxes": torch.empty((0, 4))}]

        selected, _ = calibrate_score_threshold(outputs, truth, [0.05, 0.50], 0.50)

        self.assertFalse(selected["minimumRecallSatisfied"])
        self.assertEqual(selected["recall"], 0.0)

    def test_stable_loss_rejects_repeated_explosions(self):
        value, count = require_stable_loss(torch.tensor(51.0), 50.0, 0)
        self.assertEqual(value, 51.0)
        self.assertEqual(count, 1)

        with self.assertRaisesRegex(SystemExit, "repeated explosive losses"):
            require_stable_loss(torch.tensor(52.0), 50.0, count)

    def test_stable_loss_rejects_non_finite_values(self):
        with self.assertRaisesRegex(SystemExit, "non-finite loss"):
            require_stable_loss(torch.tensor(float("nan")), 50.0, 0)


if __name__ == "__main__":
    unittest.main()