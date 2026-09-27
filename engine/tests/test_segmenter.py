"""Hands-free segmentation, driven by synthetic audio (no microphone)."""
import unittest

import numpy as np

from barnaby_engine.continuous import VoiceSegmenter

R, B = 16_000, 480  # 30 ms blocks, as the mic stream delivers


class Audio:
    def __init__(self, seed=0):
        self.rng = np.random.default_rng(seed)

    def noise(self, s, a=0.002):
        return (self.rng.standard_normal(int(s * R)) * a).astype(np.float32)

    def speech(self, s, a=0.1, floor=0.002):
        """Syllables: 220 ms voiced, then a 90 ms near-silent gap, like real speech."""
        out, t = [], 0.0
        while t < s:
            v = np.arange(int(0.22 * R)) / R
            out.append((np.sin(2 * np.pi * (180 + 40 * self.rng.random()) * v) * a * np.hanning(v.size)).astype(np.float32))
            out.append(self.noise(0.09, floor))
            t += 0.31
        return np.concatenate(out)[: int(s * R)]


def run(sig, seg=None):
    seg = seg or VoiceSegmenter()
    outs = []
    for i in range(0, len(sig), B):
        outs += seg.feed(sig[i:i + B])
    return [len(o) / R for o in outs]


class SegmenterTest(unittest.TestCase):
    def setUp(self):
        self.a = Audio()

    def test_one_utterance_keeps_preroll_and_trims_tail(self):
        (d,) = run(np.concatenate([self.a.noise(1), self.a.speech(1.5), self.a.noise(2)]))
        # 1.5 s speech + up to 0.3 s pre-roll + ~0.2 s tail; the 2 s of trailing silence is trimmed
        self.assertTrue(1.6 <= d <= 2.1, d)

    def test_short_pause_does_not_split(self):
        (d,) = run(np.concatenate([self.a.noise(1), self.a.speech(1), self.a.noise(0.6), self.a.speech(1), self.a.noise(2)]))
        self.assertGreater(d, 2.5)

    def test_long_pause_splits(self):
        self.assertEqual(len(run(np.concatenate([self.a.noise(1), self.a.speech(1), self.a.noise(2),
                                                self.a.speech(1), self.a.noise(2)]))), 2)

    def test_pause_length_is_configurable(self):
        sig = np.concatenate([self.a.noise(1), self.a.speech(1), self.a.noise(1.0), self.a.speech(1), self.a.noise(3)])
        self.assertEqual(len(run(sig, VoiceSegmenter(silence_s=0.6))), 2)
        self.assertEqual(len(run(sig, VoiceSegmenter(silence_s=2.0))), 1)

    def test_cough_and_click_ignored(self):
        self.assertEqual(run(np.concatenate([self.a.noise(1), self.a.speech(0.1), self.a.noise(2)])), [])

    def test_long_speech_not_cut_off(self):
        (d,) = run(np.concatenate([self.a.noise(1), self.a.speech(12), self.a.noise(2)]))
        self.assertGreater(d, 12)

    def test_fan_switching_on_is_learned_not_transcribed(self):
        self.assertEqual(run(np.concatenate([self.a.noise(2), self.a.noise(20, 0.02)])), [])

    def test_speech_over_a_fan_still_detected(self):
        fan = 0.02
        sig = np.concatenate([self.a.noise(2), self.a.noise(8, fan),
                              self.a.speech(2, 0.12, floor=fan), self.a.noise(3, fan)])
        (d,) = run(sig)
        self.assertGreater(d, 1.8)

    def test_paused_hears_nothing_and_resume_starts_clean(self):
        seg = VoiceSegmenter()
        seg.pause()
        self.assertEqual(run(np.concatenate([self.a.noise(1), self.a.speech(2), self.a.noise(2)]), seg), [])
        seg.resume()
        self.assertEqual(len(run(np.concatenate([self.a.noise(1), self.a.speech(1), self.a.noise(2)]), seg)), 1)

    def test_max_length_forces_a_cut(self):
        seg = VoiceSegmenter(max_utterance_s=5)
        durations = run(np.concatenate([self.a.noise(1), self.a.speech(12), self.a.noise(2)]), seg)
        self.assertGreaterEqual(len(durations), 2)
        self.assertTrue(all(d <= 5.1 for d in durations[:-1]))


if __name__ == "__main__":
    unittest.main()
