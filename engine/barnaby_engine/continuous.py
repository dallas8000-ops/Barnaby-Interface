import time
from typing import Optional

class ContinuousMode:
    """
    Continuous dictation mode with voice activity detection.
    
    Tracks audio activity levels and determines when a turn is complete
    based on silence duration.
    """
    
    def __init__(self, timeout: float = 2.0):
        self.timeout = timeout
        self.is_enabled = False
        self._last_activity_time: Optional[float] = None
        self._activity_threshold = 0.1  # RMS level threshold for "activity"
    
    def enable(self) -> None:
        """Enable continuous mode."""
        self.is_enabled = True
        self._last_activity_time = time.time()
    
    def disable(self) -> None:
        """Disable continuous mode."""
        self.is_enabled = False
        self._last_activity_time = None
    
    def record_activity(self, audio_level: float = 0.5) -> None:
        """
        Record audio activity.
        
        Args:
            audio_level: RMS audio level (0.0 to 1.0)
        """
        if not self.is_enabled:
            return
        
        if audio_level > self._activity_threshold:
            self._last_activity_time = time.time()
    
    def should_submit_transcript(self) -> bool:
        """
        Check if enough silence has passed to submit the transcript.
        
        Returns:
            True if silence duration exceeds timeout, False otherwise
        """
        if not self.is_enabled or self._last_activity_time is None:
            return False
        
        silence_duration = time.time() - self._last_activity_time
        return silence_duration >= self.timeout
    
    def reset(self) -> None:
        """Reset the silence timer."""
        if self.is_enabled:
            self._last_activity_time = time.time()
