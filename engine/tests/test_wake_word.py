import unittest
from unittest.mock import Mock
from barnaby_engine.wake_word import WakeWordDetector

class TestWakeWordDetector(unittest.TestCase):
    def test_initialization(self):
        detector = WakeWordDetector(keyword="Barnaby", sensitivity=0.5)
        self.assertEqual(detector.keyword, "barnaby")  # keyword is lowercased
        self.assertEqual(detector.sensitivity, 0.5)
        self.assertFalse(detector.is_running)
    
    def test_start_stop(self):
        detector = WakeWordDetector(keyword="Barnaby", sensitivity=0.5)
        detector.start()
        self.assertTrue(detector.is_running)
        detector.stop()
        self.assertFalse(detector.is_running)
    
    def test_detection_callback(self):
        detector = WakeWordDetector(keyword="Barnaby", sensitivity=0.5)
        callback = Mock()
        detector.set_detection_callback(callback)
        detector.start()
        # Simulate detection
        detector._simulate_detection("Barnaby")
        callback.assert_called_once()
        detector.stop()
    
    def test_detection_cooldown(self):
        detector = WakeWordDetector(keyword="Barnaby", sensitivity=0.5)
        callback = Mock()
        detector.set_detection_callback(callback)
        detector.start()
        
        # First detection
        detector._simulate_detection("Barnaby")
        self.assertEqual(callback.call_count, 1)
        
        # Immediate second detection (should be ignored due to cooldown)
        detector._simulate_detection("Barnaby")
        self.assertEqual(callback.call_count, 1)  # Still 1
        
        detector.stop()
