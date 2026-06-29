"""Shared audio constants and helpers for realtime streaming."""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import sounddevice as sd

SAMPLE_RATE: int = 48_000
CHANNELS: int = 1
FRAME_DURATION_MS: int = 20
SAMPLE_WIDTH_BYTES: int = 2  # 16-bit PCM
FRAME_SAMPLES: int = int(SAMPLE_RATE * FRAME_DURATION_MS / 1_000)
FRAME_BYTES: int = FRAME_SAMPLES * SAMPLE_WIDTH_BYTES


@dataclass(slots=True)
class AudioFrame:
    """Represents a single chunk of mono PCM audio."""

    sequence: int
    timestamp: float
    payload: bytes

    def to_numpy(self) -> np.ndarray:
        """Convert the raw PCM payload into an `np.int16` array."""
        return np.frombuffer(self.payload, dtype=np.int16)

    @classmethod
    def from_numpy(cls, sequence: int, samples: np.ndarray, *, timestamp: Optional[float] = None) -> "AudioFrame":
        if samples.dtype != np.int16:
            raise ValueError("AudioFrame requires int16 sample data")
        if samples.ndim != 1:
            raise ValueError("AudioFrame expects mono samples")
        if len(samples) != FRAME_SAMPLES:
            raise ValueError(f"Expected {FRAME_SAMPLES} samples, received {len(samples)}")
        return cls(sequence=sequence, timestamp=timestamp or time.time(), payload=samples.tobytes())


class AudioCapture:
    """Threaded audio capture helper used by the transmitter application."""

    def __init__(self, *, device: Optional[int] = None, callback: Optional[Callable[[AudioFrame], None]] = None):
        self._device = device
        self._callback = callback
        self._queue: "queue.Queue[AudioFrame]" = queue.Queue(maxsize=10)
        self._sequence = 0
        self._stream: Optional[sd.InputStream] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._stream = sd.InputStream(
            channels=CHANNELS,
            samplerate=SAMPLE_RATE,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=self._on_audio_chunk,
        )
        self._stream.start()
        self._thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream:
            self._stream.stop(ignore_errors=True)
            self._stream.close()
            self._stream = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)

    def _on_audio_chunk(self, indata: np.ndarray, _frames: int, _time, _status) -> None:  # pragma: no cover - sounddevice callback
        if not self._running.is_set():
            return
        frame = AudioFrame.from_numpy(self._sequence, indata.copy())
        self._sequence = (self._sequence + 1) % 2 ** 31
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            # Drop old frames to avoid unbounded latency
            _ = self._queue.get_nowait()
            self._queue.put_nowait(frame)

    def _dispatch_loop(self) -> None:
        while self._running.is_set():
            try:
                frame = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if self._callback:
                self._callback(frame)


class AudioPlayback:
    """Threaded audio playback helper used by the client application."""

    def __init__(self, *, device: Optional[int] = None):
        self._device = device
        self._queue: "queue.Queue[AudioFrame]" = queue.Queue(maxsize=50)
        self._stream: Optional[sd.OutputStream] = None
        self._running = threading.Event()

    def start(self) -> None:
        if self._running.is_set():
            return
        self._running.set()
        self._stream = sd.OutputStream(
            channels=CHANNELS,
            samplerate=SAMPLE_RATE,
            dtype="int16",
            blocksize=FRAME_SAMPLES,
            device=self._device,
            callback=self._on_output,
        )
        self._stream.start()

    def stop(self) -> None:
        self._running.clear()
        if self._stream:
            self._stream.stop(ignore_errors=True)
            self._stream.close()
            self._stream = None
        with self._queue.mutex:
            self._queue.queue.clear()

    def enqueue(self, frame: AudioFrame) -> None:
        try:
            self._queue.put_nowait(frame)
        except queue.Full:
            # Drop oldest frame for realtime playback
            _ = self._queue.get_nowait()
            self._queue.put_nowait(frame)

    def _on_output(self, outdata: np.ndarray, _frames: int, _time, _status) -> None:  # pragma: no cover - sounddevice callback
        if not self._running.is_set():
            outdata.fill(0)
            return
        try:
            frame = self._queue.get_nowait()
            outdata[:] = frame.to_numpy().reshape(outdata.shape)
        except queue.Empty:
            outdata.fill(0)
