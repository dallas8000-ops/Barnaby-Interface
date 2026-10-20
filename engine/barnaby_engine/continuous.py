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


# ------------------------------------------------------------------ segmentation
from collections import deque as _deque  # noqa: E402
from dataclasses import dataclass as _dataclass, field as _field  # noqa: E402

import numpy as _np  # noqa: E402


@_dataclass
class VoiceSegmenter:
    """Turns a continuous microphone stream into utterances (hands-free mode).

    Deterministic and clock-free: time is counted in samples fed, so tests can
    drive it with synthetic audio. Energy-based with an adaptive noise floor:
    speech starts when block RMS stays above ``max(min_threshold,
    floor * start_factor)`` for ``start_blocks`` blocks, and ends after
    ``silence_s`` of quiet. The last ``preroll_s`` before the start is kept,
    so the first syllable isn't clipped. Whisper's own VAD trims the rest.

    Stationary noise (a fan or AC switching on) is handled by minimum
    tracking: while capturing, the noise estimate is the quietest block in the
    last ``floor_window_s``. Speech always dips near the room level between
    words, and a fan never does, so a fan reads as silence and ends the
    capture. The floor is then raised to the fan's level, so it doesn't
    re-trigger.
    """

    rate: int = 16_000
    silence_s: float = 1.2        # pause that ends an utterance (user-tunable)
    min_speech_s: float = 0.35    # shorter bursts (a cough, a click) are dropped
    preroll_s: float = 0.3
    max_utterance_s: float = 60.0
    start_factor: float = 3.0
    min_threshold: float = 0.008
    start_blocks: int = 2
    floor_window_s: float = 1.5
    floor: float = 0.003          # running noise-floor estimate (RMS)
    _pre: _deque = _field(default_factory=_deque, repr=False)
    _pre_samples: int = 0
    _buf: list = _field(default_factory=list, repr=False)
    _speech_samples: int = 0
    _silence_samples: int = 0
    _loud_run: int = 0
    _pending: list = _field(default_factory=list, repr=False)
    _recent: _deque = _field(default_factory=_deque, repr=False)  # (rms, samples) for minimum tracking
    _recent_samples: int = 0
    _preroll_blocks: int = 0      # leading blocks of _buf that are pre-roll, not capture
    capturing: bool = False
    paused: bool = False

    def _threshold(self) -> float:
        return max(self.min_threshold, self.floor * self.start_factor)

    def reset(self) -> None:
        self._pre.clear()
        self._pre_samples = 0
        self._buf = []
        self._pending = []
        self._speech_samples = self._silence_samples = self._loud_run = 0
        self._recent.clear()
        self._recent_samples = 0
        self.capturing = False

    def pause(self) -> None:
        """Stop listening (e.g. while Barnaby speaks, so it doesn't hear itself)."""
        self.paused = True
        self.reset()

    def resume(self) -> None:
        self.paused = False
        self.reset()

    def feed(self, block: _np.ndarray) -> list[_np.ndarray]:
        """Feed one mono float32 block; returns any utterances completed by it."""
        if self.paused or block.size == 0:
            return []
        rms = float(_np.sqrt(_np.mean(block * block)))
        self._recent.append((rms, block.size))
        self._recent_samples += block.size
        while self._recent and self._recent_samples - self._recent[0][1] >= self.floor_window_s * self.rate:
            self._recent_samples -= self._recent.popleft()[1]
        out: list[_np.ndarray] = []
        if not self.capturing:
            if rms > self._threshold():
                self._loud_run += 1
                self._pending.append(block)
                if self._loud_run >= self.start_blocks:
                    self.capturing = True
                    self._preroll_blocks = len(self._pre)
                    self._buf = list(self._pre) + self._pending
                    self._speech_samples = sum(b.size for b in self._pending)
                    self._silence_samples = 0
                    self._pre.clear()
                    self._pre_samples = 0
                    self._pending = []
            else:
                # Quiet: adapt the floor (slowly) and keep a rolling pre-roll.
                self.floor = 0.95 * self.floor + 0.05 * rms
                for b in self._pending:  # a lone loud block was noise; fold it back
                    self._push_pre(b)
                self._pending = []
                self._loud_run = 0
                self._push_pre(block)
            return out

        self._buf.append(block)
        local_floor = min(r for r, _ in self._recent)
        if rms > max(self.min_threshold * 0.7, max(self.floor, local_floor) * 2.0):
            self._speech_samples += block.size + self._silence_samples
            self._silence_samples = 0
        else:
            self._silence_samples += block.size
        total = sum(b.size for b in self._buf)
        if self._silence_samples >= self.silence_s * self.rate or total >= self.max_utterance_s * self.rate:
            if self._speech_samples >= self.min_speech_s * self.rate and self._has_speech_dips():
                audio = _np.concatenate(self._buf)
                keep_tail = int(0.2 * self.rate)  # trim most of the trailing silence
                cut = max(0, self._silence_samples - keep_tail)
                out.append(audio[: audio.size - cut] if cut else audio)
            # Learn the level we ended at as the noise floor (a fan that just switched on).
            self.floor = max(self.floor, min(r for r, _ in self._recent))
            self.capturing = False
            self._buf = []
            self._speech_samples = self._silence_samples = self._loud_run = 0
        return out

    def _has_speech_dips(self) -> bool:
        """Speech dips close to the room level between syllables; steady noise (a fan
        that just switched on) doesn't. Compare the quiet 10th percentile of block
        energy to the loud 90th, over the captured sound only: excluding the pre-roll
        before it and the trailing silence after it, which would otherwise dominate."""
        blocks = self._buf[self._preroll_blocks:]
        trailing, n = self._silence_samples, len(blocks)
        while n and trailing > 0:
            n -= 1
            trailing -= blocks[n].size
        levels = _np.array([float(_np.sqrt(_np.mean(b * b))) for b in blocks[:n] if b.size])
        if levels.size < 4:
            return True
        # Quiet end vs loud end. (Median fails when over half the span is pauses.)
        loud = float(_np.percentile(levels, 90))
        return loud <= 0 or float(_np.percentile(levels, 10)) < 0.5 * loud

    def _push_pre(self, block: _np.ndarray) -> None:
        self._pre.append(block)
        self._pre_samples += block.size
        limit = int(self.preroll_s * self.rate)
        while self._pre and self._pre_samples - self._pre[0].size >= limit:
            self._pre_samples -= self._pre.popleft().size
