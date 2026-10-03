import base64
import io
import json
import pathlib
import sys
import types
import unittest

from PIL import Image


SOURCE = pathlib.Path(__file__).with_name("main.py")


def load_handler_module():
    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    fake_torch.inference_mode = lambda: __import__("contextlib").nullcontext()
    fake_model_module = types.ModuleType("sportif_ml.model")
    fake_model_module.build_model = lambda: None
    fake_package = types.ModuleType("sportif_ml")
    fake_package.model = fake_model_module
    previous = {
        name: sys.modules.get(name)
        for name in ("torch", "sportif_ml", "sportif_ml.model")
    }
    sys.modules.update({
        "torch": fake_torch,
        "sportif_ml": fake_package,
        "sportif_ml.model": fake_model_module,
    })
    module = types.ModuleType("sportif_ball_nuclio_main")
    try:
        exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), module.__dict__)
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value
    return module


class FakeResponse:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class FakeModel:
    def __init__(self, prediction):
        self.prediction = prediction

    def __call__(self, images):
        assert len(images) == 1
        return [self.prediction]


class FakeTensor:
    def __init__(self, values):
        self.values = values

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.values


class HandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_handler_module()
        cls.module._tensor = lambda image, device: (image.width, image.height, device)

    def context(self, prediction):
        return types.SimpleNamespace(
            user_data=types.SimpleNamespace(
                device="cpu", model=FakeModel(prediction)
            ),
            Response=FakeResponse,
        )

    def event(self, body):
        return types.SimpleNamespace(body=body)

    def encoded_image(self):
        output = io.BytesIO()
        Image.new("RGB", (20, 10), color="white").save(output, format="JPEG")
        return base64.b64encode(output.getvalue()).decode()

    def prediction(self):
        return {
            "boxes": FakeTensor([
                [-1.0, 1.0, 21.0, 9.0],
                [1.0, 1.0, 2.0, 2.0],
                [3.0, 3.0, 4.0, 4.0],
            ]),
            "labels": FakeTensor([1, 1, 2]),
            "scores": FakeTensor([0.9, 0.1, 0.99]),
        }

    def test_returns_clipped_ball_rectangles_above_threshold(self):
        response = self.module.handler(
            self.context(self.prediction()),
            self.event({"image": self.encoded_image(), "threshold": 0.5}),
        )
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.body)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "ball")
        self.assertEqual(result[0]["type"], "rectangle")
        self.assertEqual(result[0]["points"], [0.0, 1.0, 20.0, 9.0])

    def test_accepts_json_bytes(self):
        body = json.dumps({"image": self.encoded_image(), "threshold": 0.95}).encode()
        response = self.module.handler(self.context(self.prediction()), self.event(body))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), [])

    def test_rejects_invalid_threshold(self):
        response = self.module.handler(
            self.context(self.prediction()),
            self.event({"image": self.encoded_image(), "threshold": 2}),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("threshold", json.loads(response.body)["error"])

    def test_rejects_invalid_image(self):
        response = self.module.handler(
            self.context(self.prediction()), self.event({"image": "not-base64"})
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", json.loads(response.body)["error"])


if __name__ == "__main__":
    unittest.main()