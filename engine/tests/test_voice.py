"""WhisperTranscriber device selection: a GPU whose CUDA runtime is missing must
fall back to CPU at load time instead of failing (or hanging) on first use."""
import sys
import threading
import time
import types
import unittest
from unittest import mock

import numpy as np

from barnaby_engine import voice
from barnaby_engine.voice import WhisperTranscriber, run_with_timeout


def fake_modules(*, gpu_count: int, cuda_behaviour: str):
    """cuda_behaviour: 'ok' | 'raise' (missing cublas) | 'hang' (DLL lookup never returns)."""
    built: list[tuple[str, str]] = []

    class FakeModel:
        def __init__(self, name, device, compute_type):
            built.append((device, compute_type))
            self.device = device

        def transcribe(self, audio, **kw):
            def gen():
                if self.device == "cuda" and cuda_behaviour == "raise":
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                if self.device == "cuda" and cuda_behaviour == "hang":
                    time.sleep(5)
                yield types.SimpleNamespace(text=" hello ")
            return gen(), None

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = FakeModel
    ct2 = types.ModuleType("ctranslate2")
    ct2.get_cuda_device_count = lambda: gpu_count
    return {"faster_whisper": fw, "ctranslate2": ct2}, built


class DeviceSelectionTest(unittest.TestCase):
    def load(self, **kw):
        mods, built = fake_modules(**kw)
        with mock.patch.dict(sys.modules, mods):
            t = WhisperTranscriber("small.en")
            t.load()
            text = t.transcribe(np.zeros(16000, np.float32), "")
        return t, built, text

    def test_missing_cuda_runtime_falls_back_to_cpu(self):
        t, built, text = self.load(gpu_count=1, cuda_behaviour="raise")
        self.assertEqual(built, [("cuda", "float16"), ("cpu", "int8")])
        self.assertIn("CPU", t.status_detail)
        self.assertIn("CUDA libraries", t.status_detail)
        self.assertEqual(text, "hello")

    def test_hanging_cuda_probe_times_out_then_cpu(self):
        with mock.patch.object(voice, "PROBE_TIMEOUT_S", 0.3):
            t, built, _ = self.load(gpu_count=1, cuda_behaviour="hang")
        self.assertEqual([d for d, _ in built], ["cuda", "cpu"])
        self.assertTrue(t.status_detail.startswith("small.en on CPU"))

    def test_working_gpu_is_used(self):
        t, built, _ = self.load(gpu_count=1, cuda_behaviour="ok")
        self.assertEqual(built, [("cuda", "float16")])
        self.assertEqual(t.status_detail, "small.en on CUDA")

    def test_no_gpu_goes_straight_to_cpu(self):
        t, built, _ = self.load(gpu_count=0, cuda_behaviour="ok")
        self.assertEqual(built, [("cpu", "int8")])
        self.assertEqual(t.status_detail, "small.en on CPU")


class RunWithTimeoutTest(unittest.TestCase):
    def test_propagates_error_and_times_out(self):
        with self.assertRaises(ValueError):
            run_with_timeout(lambda: (_ for _ in ()).throw(ValueError("x")), 1, "t")
        gate = threading.Event()
        with self.assertRaises(TimeoutError):
            run_with_timeout(lambda: gate.wait(5), 0.1, "t")
        gate.set()


if __name__ == "__main__":
    unittest.main()
