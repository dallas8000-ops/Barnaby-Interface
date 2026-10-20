import unittest
import time
from barnaby_engine.continuous import ContinuousMode

class TestContinuousMode(unittest.TestCase):
    def test_initialization(self):
        mode = ContinuousMode(timeout=2.0)
        self.assertFalse(mode.is_enabled)
        self.assertEqual(mode.timeout, 2.0)

    def test_enable_disable(self):
        mode = ContinuousMode(timeout=2.0)
        mode.enable()
        self.assertTrue(mode.is_enabled)
        mode.disable()
        self.assertFalse(mode.is_enabled)

    def test_silence_detection(self):
        mode = ContinuousMode(timeout=0.5)  # Short timeout for testing
        mode.enable()
        mode.record_activity()
        self.assertFalse(mode.should_submit_transcript())
        time.sleep(0.6)
        self.assertTrue(mode.should_submit_transcript())
        mode.disable()

    def test_activity_resets_timer(self):
        mode = ContinuousMode(timeout=0.5)
        mode.enable()
        mode.record_activity()
        time.sleep(0.3)
        mode.record_activity()  # Reset timer
        time.sleep(0.3)
        self.assertFalse(mode.should_submit_transcript())  # Should not submit yet
        time.sleep(0.3)
        self.assertTrue(mode.should_submit_transcript())
        mode.disable()

    def test_activity_threshold(self):
        mode = ContinuousMode(timeout=0.5)
        mode.enable()

        # Low audio level (below threshold)
        mode.record_activity(audio_level=0.05)
        time.sleep(0.6)
        self.assertTrue(mode.should_submit_transcript())

        mode.disable()
        mode.enable()  # Re-enable for second test
        mode.record_activity(audio_level=0.2)  # Above threshold
        time.sleep(0.3)
        self.assertFalse(mode.should_submit_transcript())

        mode.disable()
