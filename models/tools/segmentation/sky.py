"""
SkySegmentationModel — sky / non-sky segmentation for scene reconstruction.

Reference: https://github.com/xiongzhu666/Sky-Segmentation-and-Post-processing
Weights:   https://huggingface.co/JianyuanWang/skyseg (`skyseg.onnx`)

Conforms to `BaseToolModel`. Given an RGB PIL image, `predict()` returns a
HxW float32 array in [0, 1] holding the per-pixel probability that the pixel is
sky, at the input image's resolution.

Why this exists
---------------
Monocular geometry models place sky at a plausible mid-scene depth rather than
at infinity, because "infinitely far" is not something a depth head can express.
Nothing in the prediction marks those pixels out — their depth and confidence
sit comfortably inside the range spanned by real surfaces — so sky survives
every threshold and gets meshed into a stretched curtain draped over the scene.
It has to be segmented from the image instead.

Difference from upstream
------------------------
HunyuanWorldMirror's `segment_sky` min-max rescales each frame's output to
0-255 before thresholding. The network already emits a calibrated probability,
so rescaling only matters when the spread is small — which is exactly the
sky-free frame, where it stretches noise up to full range and invents a sky.
Measured on a sky-free crop the raw output peaks at 0.011; rescaled it reaches
1.0. This wrapper thresholds the probability directly and leaves it alone.
"""

from typing import Optional, Tuple

import numpy as np
from PIL import Image

from models.tools.base import BaseToolModel

#: HuggingFace repo and filename the ONNX weights are pulled from when no local
#: path is given.
HF_REPO = "JianyuanWang/skyseg"
HF_FILENAME = "skyseg.onnx"

#: Resolution the network expects, (width, height).
INPUT_SIZE = (320, 320)

#: ImageNet statistics the network was trained with.
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

#: Probability above which a pixel counts as sky. The output is calibrated, so
#: 0.5 is the natural cut; lower it to be more aggressive about shaving the
#: soft boundary where sky meets a treeline or roofline.
DEFAULT_THRESHOLD = 0.5


class SkySegmentationModel(BaseToolModel):
    """Sky segmentation via the `skyseg` ONNX network."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        device: str = "cpu",
        input_size: Tuple[int, int] = INPUT_SIZE,
        lazy: bool = False,
    ):
        """
        Args:
            model_path: Path to `skyseg.onnx`. Downloaded from the Hub when None.
            device: "cuda" selects the ONNX Runtime CUDA provider and silently
                falls back to CPU if it is unavailable. The network is small
                enough that CPU is usually fine.
            input_size: (width, height) the image is resized to for inference.
            lazy: Defer loading until the first call.
        """
        self.input_size = input_size
        super().__init__(model_path=model_path, device=device, lazy=lazy)

    def _load(self) -> None:
        import onnxruntime

        path = self.model_path
        # Empty is treated as absent: these paths usually arrive from an
        # environment variable, and an unset one reads as "" often enough that
        # the alternative is an unhelpful failure from deep inside onnxruntime.
        if not path:
            from huggingface_hub import hf_hub_download

            path = hf_hub_download(HF_REPO, HF_FILENAME)
            self.model_path = path

        providers = ["CPUExecutionProvider"]
        if self.device.startswith("cuda"):
            available = onnxruntime.get_available_providers()
            if "CUDAExecutionProvider" in available:
                providers.insert(0, "CUDAExecutionProvider")

        self.model = onnxruntime.InferenceSession(path, providers=providers)
        self._input_name = self.model.get_inputs()[0].name
        # The network is U2Net-shaped and emits one map per decoder stage; the
        # first is the fused prediction and the rest are deep-supervision heads.
        self._output_name = self.model.get_outputs()[0].name

    def predict(self, image: Image.Image, **kwargs) -> np.ndarray:
        """
        Estimate the per-pixel probability of sky.

        Args:
            image: RGB PIL image.

        Returns:
            HxW float32 array in [0, 1], sky probability at the input's size.
        """
        self._ensure_loaded()
        rgb = image.convert("RGB")
        width, height = rgb.size

        small = np.asarray(rgb.resize(self.input_size, Image.BILINEAR), np.float32)
        x = ((small / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[None]

        out = self.model.run([self._output_name], {self._input_name: x})
        probability = np.asarray(out).squeeze().astype(np.float32)

        return np.asarray(
            Image.fromarray(probability).resize((width, height), Image.BILINEAR),
            dtype=np.float32,
        )

    def sky_mask(
        self, image: Image.Image, threshold: float = DEFAULT_THRESHOLD
    ) -> np.ndarray:
        """HxW bool array, True where the pixel is sky."""
        return self.predict(image) >= threshold

    def keep_mask(
        self, image: Image.Image, threshold: float = DEFAULT_THRESHOLD
    ) -> np.ndarray:
        """
        HxW bool array, True where the pixel is *not* sky.

        This is the polarity `scene_mask` and upstream's `sky_mask` both expect.
        """
        return ~self.sky_mask(image, threshold)
