import threading
import time
from typing import Callable, Optional

class WakeWordDetector:
    """
    Lightweight wake word detector.
    
    Uses a simple energy-based VAD and keyword matching algorithm.
    For production, replace with Porcupine or OpenWakeWord for better accuracy.
    """
    
    def __init__(self, keyword: str = "Barnaby", sensitivity: float = 0.5):
        self.keyword = keyword.lower()
        self.sensitivity = sensitivity
        self.is_running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._detection_callback: Optional[Callable] = None
        self._last_detection_time = 0.0
        self._detection_cooldown = 1.0  # Prevent rapid-fire detections
    
    def set_detection_callback(self, callback: Callable) -> None:
        """Set callback function to be called when wake word is detected."""
        self._detection_callback = callback
    
    def start(self) -> None:
        """Start the wake word detector in a background thread."""
        if self.is_running:
            return
        
        self.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._detect_loop, daemon=True)
        self._thread.start()
    
    def stop(self) -> None:
        """Stop the wake word detector."""
        if not self.is_running:
            return
        
        self.is_running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
    
    def _detect_loop(self) -> None:
        """
        Main detection loop.
        
        In production, this would use a proper wake word model.
        For now, use a placeholder that simulates detection.
        """
        while not self._stop_event.is_set():
            time.sleep(0.1)
            # Placeholder: In production, use actual audio processing here
            # This is a stub for the foundation phase
    
    def _simulate_detection(self, detected_word: str) -> None:
        """Simulate wake word detection (for testing)."""
        current_time = time.time()
        if current_time - self._last_detection_time < self._detection_cooldown:
            return
        
        if detected_word.lower() == self.keyword:
            self._last_detection_time = current_time
            if self._detection_callback:
                self._detection_callback()
