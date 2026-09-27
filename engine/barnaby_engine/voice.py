"""Audio I/O, speech-to-text and text-to-speech.

Heavy dependencies (sounddevice, faster-whisper, kokoro) are imported lazily so
the engine starts instantly, tests run without audio hardware, and a missing
optional component degrades one feature instead of crashing the process.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Protocol

import numpy as np

log = logging.getLogger(__name__)

STT_RATE = 16_000
TTS_RATE = 24_000
MAX_RECORD_S = 120


class Recorder(Protocol):
    def start(self, on_level: Callable[[float], None]) -> None: ...
    def stop(self) -> np.ndarray: ...
    def abort(self) -> None: ...


class Transcriber(Protocol):
    def load(self) -> None: ...
    def transcribe(self, audio: np.ndarray, vocabulary_prompt: str) -> str: ...


class Speaker(Protocol):
    def say(self, text: str) -> None:
        """Blocking: synthesize and play. Must return promptly after stop()."""
    def stop(self) -> None: ...


def resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.float32, copy=False)
    # Box-filter anti-aliasing before linear interpolation: cheap, and plenty
    # for speech bandwidth going 48k -> 16k (integer factor 3 on most devices).
    factor = src_rate / dst_rate
    if factor > 1:
        k = int(round(factor))
        if k > 1:
            audio = np.convolve(audio, np.ones(k, dtype=np.float32) / k, mode="same")
    n_out = int(round(audio.size * dst_rate / src_rate))
    x_old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


# Capture endpoints that record what the speakers play, not a microphone. Sound
# cards (Creative "What U Hear", Realtek "Stereo Mix") often register one as the
# Windows default recording device. Listening to it hears silence, or worse, in
# hands-free mode it hears Barnaby's own voice and whatever else is playing.
_LOOPBACK_MARKERS = ("what u hear", "what you hear", "stereo mix", "wave out mix", "loopback")
# Aliases that forward to whatever the Windows default is, so they are the loopback too.
_DEFAULT_ALIASES = ("sound mapper", "primary sound capture")


def is_loopback(name: str) -> bool:
    lowered = name.lower()
    return any(m in lowered for m in _LOOPBACK_MARKERS)


def pick_input_device(devices: list[dict], default_index: int | None) -> int | None:
    """The input to use when the user hasn't chosen one: the system default, unless
    that's a speaker loopback. Then use the first real input on the same audio API
    (preferring names that say "mic"). ``None`` means let PortAudio decide."""
    def is_input(i: int) -> bool:
        return 0 <= i < len(devices) and devices[i].get("max_input_channels", 0) > 0

    if default_index is None or not is_input(default_index):
        return None
    if not is_loopback(str(devices[default_index].get("name", ""))):
        return default_index
    api = devices[default_index].get("hostapi")
    def usable(i: int) -> bool:
        name = str(devices[i].get("name", "")).lower()
        return is_input(i) and devices[i].get("hostapi") == api and not is_loopback(name) \
            and not any(a in name for a in _DEFAULT_ALIASES)

    real = [i for i in range(len(devices)) if usable(i)]
    mics = [i for i in real if "mic" in str(devices[i].get("name", "")).lower()]
    return (mics or real or [default_index])[0]


def _default_input_index(sd) -> int | None:  # noqa: ANN001
    idx = sd.default.device[0]
    return idx if isinstance(idx, int) and idx >= 0 else None


class MicRecorder:
    """Push-to-talk capture. Tries 16 kHz natively, else device rate + resample
    (WASAPI shared mode rejects non-native rates)."""

    def __init__(self, device: int | None = None) -> None:
        self.device = device
        self._chunks: list[np.ndarray] = []
        self._stream = None
        self._rate = STT_RATE
        self._lock = threading.Lock()
        self._hf_stream = None  # hands-free continuous stream (separate from push-to-talk)
        self._loopback_warned = False

    def _open(self, callback: Callable[..., None]) -> tuple[object, int]:
        """Open an input stream: 16 kHz natively if the device allows, else its own rate
        (WASAPI shared mode rejects non-native rates). Returns (stream, rate)."""
        import sounddevice as sd  # lazy

        device = self.device
        if device is None:
            default = _default_input_index(sd)
            device = pick_input_device(list(sd.query_devices()), default)
            if device != default and not self._loopback_warned:
                self._loopback_warned = True
                log.warning("System default input %r records speaker output; using %r instead",
                            sd.query_devices(default)["name"], sd.query_devices(device)["name"])
        last: Exception | None = None
        for rate in (STT_RATE, None):
            try:
                if rate is None:
                    info = sd.query_devices(device, "input")
                    rate = int(info["default_samplerate"])
                stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                                        device=device, blocksize=int(rate * 0.03),
                                        callback=callback)
                stream.start()
                return stream, rate
            except Exception as exc:  # noqa: BLE001 - PortAudio raises bare errors
                last = exc
        raise RuntimeError(f"Microphone unavailable: {last}")

    def start(self, on_level: Callable[[float], None]) -> None:
        self.abort()
        self._chunks = []
        max_frames = MAX_RECORD_S * 48_000

        def callback(indata, frames, _time, status):  # PortAudio thread
            if status:
                log.debug("input status: %s", status)
            mono = indata[:, 0].copy()
            with self._lock:
                if sum(c.size for c in self._chunks) < max_frames:
                    self._chunks.append(mono)
            on_level(float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0)

        self._stream, self._rate = self._open(callback)

    # ---- hands-free: an always-open stream delivering 30 ms blocks
    def start_stream(self, on_block: Callable[[np.ndarray], None]) -> int:
        """Open a continuous stream; ``on_block`` gets mono float32 blocks on the
        PortAudio thread. Returns the stream's sample rate."""
        self.stop_stream()

        def callback(indata, frames, _time, status):  # PortAudio thread
            if status:
                log.debug("input status: %s", status)
            on_block(indata[:, 0].copy())

        self._hf_stream, rate = self._open(callback)
        return rate

    def stop_stream(self) -> None:
        stream, self._hf_stream = self._hf_stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    def stop(self) -> np.ndarray:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
        return resample(audio, self._rate, STT_RATE)

    def abort(self) -> None:
        if self._stream is not None:
            self.stop()


PROBE_TIMEOUT_S = 90.0


def run_with_timeout(fn: Callable[[], object], timeout: float, what: str) -> None:
    """Run fn on a daemon thread; raise its error, or TimeoutError if it doesn't
    return in time. A hung native call (e.g. a CUDA DLL that never resolves) is
    abandoned on its daemon thread instead of wedging the caller forever."""
    box: dict[str, BaseException | bool] = {}

    def target() -> None:
        try:
            fn()
            box["ok"] = True
        except BaseException as exc:  # noqa: BLE001
            box["err"] = exc

    t = threading.Thread(target=target, name=f"probe-{what}", daemon=True)
    t.start()
    t.join(timeout)
    if box.get("ok"):
        return
    err = box.get("err")
    if isinstance(err, BaseException):
        raise err
    raise TimeoutError(f"{what} did not finish within {timeout:.0f}s")


class WhisperTranscriber:
    def __init__(self, model: str = "small.en", device: str = "auto") -> None:
        self.model_name = model
        self.device = device
        self.status_detail: str | None = None
        self._model = None
        self._load_lock = threading.Lock()
        # CTranslate2 models are not safe to drive from two executor threads at
        # once; serialize so a second push-to-talk queues instead of racing.
        self._run_lock = threading.Lock()

    @staticmethod
    def _warm_up(model: object) -> None:
        # Forces a real encode. Model construction succeeds even when the CUDA
        # runtime (cuBLAS/cuDNN) is missing; only the first encode fails, so
        # this is the only reliable way to know the device actually works.
        segments, _ = model.transcribe(np.zeros(STT_RATE, np.float32), beam_size=1,  # type: ignore[attr-defined]
                                       vad_filter=False, without_timestamps=True)
        for _ in segments:
            pass

    def load(self) -> None:
        with self._load_lock:
            if self._model is not None:
                return
            from faster_whisper import WhisperModel  # lazy

            candidates: list[str]
            if self.device == "cpu":
                candidates = ["cpu"]
            else:
                has_gpu = False
                try:
                    import ctranslate2
                    has_gpu = ctranslate2.get_cuda_device_count() > 0
                except Exception as exc:  # noqa: BLE001
                    log.info("CUDA device query failed, using CPU: %s", exc)
                candidates = ["cuda", "cpu"] if (has_gpu or self.device == "cuda") else ["cpu"]

            last_exc: BaseException | None = None
            for device in candidates:
                compute = "float16" if device == "cuda" else "int8"
                try:
                    model = WhisperModel(self.model_name, device=device, compute_type=compute)
                    run_with_timeout(lambda m=model: self._warm_up(m), PROBE_TIMEOUT_S, f"whisper-{device}")
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    log.warning("Whisper on %s unusable, trying next device: %s", device, exc)
                    continue
                self._model = model
                note = ""
                if device == "cpu" and "cuda" in candidates:
                    note = " (GPU found, but its CUDA libraries aren't installed)"
                self.status_detail = f"{self.model_name} on {device.upper()}{note}"
                log.info("Whisper ready: %s", self.status_detail)
                return
            raise RuntimeError(f"Speech recognition could not start: {last_exc}") from last_exc

    def transcribe(self, audio: np.ndarray, vocabulary_prompt: str) -> str:
        self.load()
        assert self._model is not None
        with self._run_lock:
            return self._transcribe(audio, vocabulary_prompt)

    def _transcribe(self, audio: np.ndarray, vocabulary_prompt: str) -> str:
        assert self._model is not None
        segments, _info = self._model.transcribe(
            audio,
            language="en" if self.model_name.endswith(".en") else None,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 400},
            initial_prompt=vocabulary_prompt or None,
            condition_on_previous_text=False,  # stops hallucinated repetition loops
        )
        return " ".join(s.text.strip() for s in segments).strip()


class KokoroSpeaker:
    def __init__(self, voice: str = "am_michael", speed: float = 1.05) -> None:
        self.voice = voice
        self.speed = speed
        self._pipelines: dict[str, object] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def _pipeline(self):
        from kokoro import KPipeline  # lazy

        lang = self.voice[:1] if self.voice[:1] in "abefhijpz" else "a"
        if lang not in self._pipelines:
            self._pipelines[lang] = KPipeline(lang_code=lang)
        return self._pipelines[lang]

    def load(self) -> None:
        with self._lock:
            self._pipeline()

    def say(self, text: str) -> None:
        import sounddevice as sd  # lazy

        self._stop.clear()
        with self._lock:
            pipeline = self._pipeline()
        for _gs, _ps, audio in pipeline(text, voice=self.voice, speed=self.speed):
            if self._stop.is_set():
                return
            chunk = audio.numpy() if hasattr(audio, "numpy") else np.asarray(audio)
            sd.play(chunk, TTS_RATE)
            # poll instead of sd.wait() so stop() interrupts within ~50 ms
            while sd.get_stream().active:
                if self._stop.wait(0.05):
                    sd.stop()
                    return

    def stop(self) -> None:
        self._stop.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:  # noqa: BLE001
            pass


def list_input_devices() -> list[dict[str, object]]:
    try:
        import sounddevice as sd
        devices = list(sd.query_devices())
        default_in = _default_input_index(sd)
        auto = pick_input_device(devices, default_in)
        return [
            {"index": i, "name": d["name"], "default": i == default_in,
             "auto": i == auto, "loopback": is_loopback(d["name"])}
            for i, d in enumerate(devices) if d["max_input_channels"] > 0
        ]
    except Exception:  # noqa: BLE001
        return []
