"""Shared AudioEngine/output lifecycle for Player and Setup modes."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Callable

from pnp_music_player.audio.cache import AudioCache
from pnp_music_player.audio.engine import AudioEngine
from pnp_music_player.audio.discord_voice import DiscordVoiceService
from pnp_music_player.audio.output_tap import DiscordPcmTap
from pnp_music_player.audio.sinks import LocalAudioSink, OutputSink

_LOG = logging.getLogger("ui.audio")


@dataclass(frozen=True, slots=True)
class OutputDevice:
    key: str
    label: str
    index: int | None


class AudioRuntime:
    """Own one engine and reopen only its physical sink when requested."""

    def __init__(
        self,
        *,
        sink_factory: Callable[[int | str | None], OutputSink] | None = None,
        device_lister: Callable[[], list[dict[str, object]]] | None = None,
        audio_cache: AudioCache | None = None,
    ) -> None:
        self._sink_factory = sink_factory or (lambda device: LocalAudioSink(device=device))
        self._device_lister = device_lister or LocalAudioSink.list_output_devices
        self._sink = self._sink_factory(None)
        self.engine = AudioEngine(self._sink)
        self.discord_tap = DiscordPcmTap()
        self.engine.set_output_tap(self.discord_tap)
        self.discord = DiscordVoiceService(self.discord_tap)
        self.discord.start()
        self.audio_cache = audio_cache or AudioCache.from_environment()
        self.started = False
        self.selected_output = "default"

    def ensure_started(self) -> str | None:
        if not self.started:
            try:
                self.engine.start_output()
                self.started = True
                _LOG.info("local audio output started device=%s", self.selected_output)
            except Exception as exc:
                if self.selected_output == "default":
                    raise
                _LOG.exception("selected audio output failed; falling back")
                fallback = self._sink_factory(None)
                self.engine.replace_output_sink(fallback, start=True)
                self._sink = fallback
                self.selected_output = "default"
                self.started = True
                _LOG.warning("local audio output fell back to system default")
                return f"Audio output unavailable; using system default: {exc}"
        return None

    def outputs(self) -> list[OutputDevice]:
        devices = [OutputDevice("default", "System Default", None)]
        for fallback_index, values in enumerate(self._device_lister()):
            index = int(values.get("index", fallback_index))
            name = str(values.get("name", f"Audio Output {index}"))
            hostapi = str(values.get("hostapi_name", values.get("hostapi", "")))
            devices.append(OutputDevice(f"{name}|{hostapi}", name, index))
        return devices

    def protected_audio_paths(self) -> tuple[str, ...]:
        snapshot = self.engine.snapshot()
        return tuple(
            path
            for path in (
                snapshot.track_filepath,
                snapshot.queued_track_filepath,
                snapshot.transition_target_filepath,
            )
            if path
        )

    def select_output(self, key: str) -> tuple[str, str | None]:
        devices = self.outputs()
        selected = next((device for device in devices if device.key == key), None)
        warning: str | None = None
        if selected is None:
            selected = devices[0]
            warning = "Saved audio output is missing; using system default."
        try:
            sink = self._sink_factory(selected.index)
            self.engine.replace_output_sink(sink, start=self.started)
            self._sink = sink
            self.selected_output = selected.key
            _LOG.info(
                "local audio output selected key=%s label=%s active=%s",
                selected.key,
                selected.label,
                self.started,
            )
        except Exception as exc:
            _LOG.exception("cannot switch audio output to %s", selected.label)
            fallback = self._sink_factory(None)
            self.engine.replace_output_sink(fallback, start=self.started)
            self._sink = fallback
            self.selected_output = "default"
            _LOG.warning("audio device selection fell back to system default")
            warning = f"Could not open audio output; using system default: {exc}"
        return self.selected_output, warning

    def close(self) -> None:
        _LOG.info("audio runtime shutdown requested")
        self.discord.close()
        self.engine.close()
        self.started = False
        _LOG.info("audio runtime stopped")
