"""Output abstraction and the PortAudio-backed local sink."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from queue import Empty, SimpleQueue
import re
import sys
from threading import Event, Lock, Thread
from time import monotonic
from typing import Callable

import numpy as np

RenderCallback = Callable[[np.ndarray], None]
_LOG = logging.getLogger("audio.output")


class AudioOutputError(RuntimeError):
    """The local output stream could not be opened or maintained."""


class OutputSink(ABC):
    """Consumes the final 48-kHz stereo float32 master bus."""

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Required input sample rate for this sink."""

    @abstractmethod
    def start(self, renderer: RenderCallback) -> None:
        """Start requesting output blocks from ``renderer``."""

    @abstractmethod
    def stop(self) -> None:
        """Stop and release output resources. Calls must be idempotent."""

    @property
    @abstractmethod
    def active(self) -> bool:
        """Whether the sink currently owns a running output stream."""


class LocalAudioSink(OutputSink):
    """One persistent ``sounddevice.OutputStream`` for local playback.

    PortAudio status messages are passed to a small logger worker so logging
    never blocks the realtime callback.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 48_000,
        blocksize: int = 1_024,
        device: int | str | None = None,
        latency: str | float = "low",
    ) -> None:
        self._sample_rate = sample_rate
        self.blocksize = blocksize
        self.device = device
        self.latency = latency
        self._stream: object | None = None
        self._events: SimpleQueue[tuple[str, object] | None] = SimpleQueue()
        self._logger_stop = Event()
        self._logger_thread: Thread | None = None
        self._lifecycle_lock = Lock()

    @property
    def active(self) -> bool:
        stream = self._stream
        return bool(stream is not None and getattr(stream, "active", False))

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    def start(self, renderer: RenderCallback) -> None:
        with self._lifecycle_lock:
            if self._stream is not None:
                return
            try:
                import sounddevice as sd
            except (ImportError, OSError) as exc:
                raise AudioOutputError(f"sounddevice/PortAudio is unavailable: {exc}") from exc

            self._logger_stop.clear()
            self._logger_thread = Thread(
                target=self._log_events, name="audio-output-events", daemon=True
            )
            self._logger_thread.start()

            def callback(outdata: np.ndarray, frames: int, _time: object, status: object) -> None:
                if status:
                    self._events.put(("status", str(status)))
                if frames != outdata.shape[0]:
                    outdata.fill(0.0)
                    self._events.put(("error", "PortAudio frame-count mismatch"))
                    raise sd.CallbackAbort
                try:
                    renderer(outdata)
                except Exception as exc:
                    outdata.fill(0.0)
                    self._events.put(("exception", exc))
                    raise sd.CallbackAbort from exc

            try:
                stream = sd.OutputStream(
                    samplerate=self._sample_rate,
                    channels=2,
                    dtype="float32",
                    blocksize=self.blocksize,
                    device=self.device,
                    latency=self.latency,
                    callback=callback,
                )
                stream.start()
                self._stream = stream
                _LOG.info(
                    "PortAudio stream opened device=%s rate=%d blocksize=%d latency=%s",
                    self.device if self.device is not None else "system-default",
                    self._sample_rate,
                    self.blocksize,
                    self.latency,
                )
            except Exception as exc:
                self._stop_logger()
                raise AudioOutputError(f"cannot open local audio output: {exc}") from exc

    def stop(self) -> None:
        with self._lifecycle_lock:
            stream, self._stream = self._stream, None
            failures: list[Exception] = []
            try:
                if stream is not None:
                    try:
                        stream.stop()
                    except Exception as exc:
                        failures.append(exc)
                    try:
                        stream.close()
                    except Exception as exc:
                        failures.append(exc)
            finally:
                self._stop_logger()
            if failures:
                raise AudioOutputError(
                    f"cannot close local audio output cleanly: {failures[0]}"
                ) from failures[0]
            if stream is not None:
                _LOG.info("PortAudio stream closed")

    def _stop_logger(self) -> None:
        self._logger_stop.set()
        self._events.put(None)
        thread, self._logger_thread = self._logger_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _log_events(self) -> None:
        status_seen = False
        suppressed_statuses = 0
        last_status_log = 0.0
        while not self._logger_stop.is_set():
            try:
                event = self._events.get(timeout=0.5)
            except Empty:
                continue
            if event is None:
                break
            kind, detail = event
            if kind == "status":
                now = monotonic()
                if not status_seen:
                    status_seen = True
                    last_status_log = now
                    _LOG.warning(
                        "PortAudio callback status (possible underrun): %s", detail
                    )
                elif now - last_status_log >= 5.0:
                    _LOG.warning(
                        "PortAudio callback statuses repeated count=%d latest=%s",
                        suppressed_statuses + 1,
                        detail,
                    )
                    suppressed_statuses = 0
                    last_status_log = now
                else:
                    suppressed_statuses += 1
            elif kind == "exception":
                error = detail
                _LOG.error(
                    "audio callback aborted",
                    exc_info=(type(error), error, error.__traceback__),
                )
            else:
                _LOG.error("audio callback aborted: %s", detail)
        if suppressed_statuses:
            _LOG.warning(
                "PortAudio callback statuses suppressed during shutdown count=%d",
                suppressed_statuses,
            )

    @staticmethod
    def list_output_devices() -> list[dict[str, object]]:
        """Return currently usable, user-facing PortAudio output endpoints."""
        try:
            import sounddevice as sd
            hostapis = sd.query_hostapis()
            candidates: list[dict[str, object]] = []
            pipewire_alsa_proxy = False
            for index, device in enumerate(sd.query_devices()):
                values = dict(device)
                values["index"] = index
                values["hostapi_name"] = str(
                    hostapis[int(values["hostapi"])]["name"]
                )
                pipewire_alsa_proxy = (
                    pipewire_alsa_proxy
                    or _is_pipewire_alsa_proxy(values)
                )
                if int(values.get("max_output_channels", 0)) < 2:
                    continue
                if _is_virtual_output(values):
                    continue
                try:
                    sd.check_output_settings(
                        device=index,
                        channels=2,
                        dtype="float32",
                        samplerate=48_000,
                    )
                except Exception:
                    continue
                candidates.append(values)
            return _filter_user_output_devices(
                candidates,
                platform=sys.platform,
                pipewire_alsa_proxy=pipewire_alsa_proxy,
            )
        except Exception as exc:
            raise AudioOutputError(f"cannot enumerate audio outputs: {exc}") from exc


_VIRTUAL_OUTPUT_NAMES = {
    "default",
    "sysdefault",
    "pulse",
    "pipewire",
    "jack",
    "null",
    "oss",
}
_VIRTUAL_OUTPUT_PREFIXES = {
    "default",
    "sysdefault",
    "front",
    "surround21",
    "surround40",
    "surround41",
    "surround50",
    "surround51",
    "surround71",
    "iec958",
    "spdif",
    "hdmi",
    "dmix",
    "dsnoop",
    "pulse",
    "pipewire",
    "speex",
    "upmix",
    "vdownmix",
}


def _filter_user_output_devices(
    devices: list[dict[str, object]],
    *,
    platform: str,
    pipewire_alsa_proxy: bool = False,
) -> list[dict[str, object]]:
    """Remove PortAudio aliases and duplicate host-API representations.

    No concrete hardware names are used. PortAudio supplies endpoint names on
    the target system; only well-known software aliases are hidden because the
    UI already represents them through ``System Default``.
    """
    physical = [device for device in devices if not _is_virtual_output(device)]
    preferred = _preferred_host_api_subset(
        physical,
        platform=platform,
        pipewire_alsa_proxy=pipewire_alsa_proxy,
    )
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for device in preferred:
        identity = _device_identity(str(device.get("name", "")))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(device)
    return result


def _is_virtual_output(device: dict[str, object]) -> bool:
    name = str(device.get("name", "")).strip().casefold()
    if name in _VIRTUAL_OUTPUT_NAMES:
        return True
    if ":" not in name:
        return False
    return name.split(":", 1)[0] in _VIRTUAL_OUTPUT_PREFIXES


def _is_pipewire_alsa_proxy(device: dict[str, object]) -> bool:
    """Identify PipeWire's ALSA compatibility endpoint.

    Some PortAudio builds expose the generic ``pipewire`` PCM through ALSA,
    while the actual WirePlumber sinks appear through PortAudio's JACK API.
    """
    name = str(device.get("name", "")).strip().casefold()
    hostapi = str(device.get("hostapi_name", "")).casefold()
    return name == "pipewire" and "alsa" in hostapi


def _preferred_host_api_subset(
    devices: list[dict[str, object]],
    *,
    platform: str,
    pipewire_alsa_proxy: bool = False,
) -> list[dict[str, object]]:
    if platform.startswith("win"):
        preferences = (("wasapi",), ("directsound",), ("wdm-ks",), ("mme",))
    elif platform == "darwin":
        preferences = (("core audio", "coreaudio"),)
    else:
        native_session_api = ("pipewire", "pulseaudio", "pulse audio")
        preferences = (
            (native_session_api, ("jack",), ("alsa",))
            if pipewire_alsa_proxy
            else (native_session_api, ("alsa",), ("jack",))
        )
    for names in preferences:
        matches = [
            device
            for device in devices
            if any(
                name in str(device.get("hostapi_name", "")).casefold()
                for name in names
            )
        ]
        if matches:
            return matches
    return devices


def _device_identity(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()
