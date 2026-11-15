"""Callback-owned playback engine with sample-accurate transitions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from copy import copy
from enum import Enum, auto
import logging
from math import isfinite, isinf, log10, pow
from queue import Empty, SimpleQueue

import numpy as np

from pnp_music_player.audio.loader import DecodedAudio, INTERNAL_SAMPLE_RATE
from pnp_music_player.audio.sinks import OutputSink
from pnp_music_player.audio.transitions import (
    TransitionMode,
    fill_equal_power_gains,
    fill_fade_gains,
)
from pnp_music_player.models.track import TrackMetadata
from pnp_music_player.playback.state_machine import PlaybackState, PlaybackStateMachine

_LOG = logging.getLogger("audio.playback")
_SCRATCH_FRAMES = 8_192


class _CommandType(Enum):
    PLAY = auto()
    PLAY_ONCE = auto()
    QUEUE = auto()
    CANCEL_QUEUE = auto()
    SELECT_QUEUED_ENTRY = auto()
    SELECT_LOOP = auto()
    TRANSITION = auto()
    PAUSE = auto()
    RESUME = auto()
    STOP = auto()
    MASTER_DB = auto()
    MUTE = auto()


@dataclass(frozen=True, slots=True)
class PlaybackSnapshot:
    playing: bool
    paused: bool
    position: int
    track_id: str | None
    queued_track_id: str | None
    transition_target_id: str | None
    track_filepath: str | None
    queued_track_filepath: str | None
    queued_entry_id: str | None
    transition_target_filepath: str | None
    transition_target_entry_id: str | None
    transition_frame: int
    transition_total_frames: int
    state: PlaybackState
    master_volume_db: float
    muted: bool
    loop_count: int
    next_jump_frames: int
    next_exit_frames: int | None
    output_peak_left_dbfs: float
    output_peak_right_dbfs: float
    can_cancel_queued_track: bool
    active_loop_id: str
    pending_loop_id: str | None
    play_command_generation: int


@dataclass(frozen=True, slots=True)
class _PreparedTrack:
    audio: DecodedAudio
    metadata: TrackMetadata


@dataclass(frozen=True, slots=True)
class _TrackStart:
    entry_id: str
    position: int
    state_machine: PlaybackStateMachine
    active_loop_id: str
    loop_start: int
    loop_end: int


@dataclass(slots=True)
class TransitionContext:
    """All callback-owned progress for one non-nested forced transition."""

    mode: TransitionMode
    total_frames: int
    current_frame: int
    source_track: _PreparedTrack
    source_position: int
    source_state_machine: PlaybackStateMachine
    source_loop_start: int
    source_loop_end: int
    target_track: _PreparedTrack
    target_position: int
    target_state_machine: PlaybackStateMachine
    target_entry_id: str
    target_active_loop_id: str
    target_loop_start: int
    target_loop_end: int
    fade_out_frames: int = 0
    fade_in_frames: int = 0
    target_loop_count: int = 0


class AudioEngine:
    """Render in-memory tracks through one persistent output stream.

    Public methods enqueue commands. The audio callback exclusively owns the
    immediate playback, queue and transition state. Decoding, resampling and
    logging stay outside the callback.
    """

    def __init__(
        self, sink: OutputSink | None = None, *, sample_rate: int = INTERNAL_SAMPLE_RATE
    ) -> None:
        if sample_rate != INTERNAL_SAMPLE_RATE:
            raise ValueError(f"internal engine rate must be {INTERNAL_SAMPLE_RATE} Hz")
        if sink is not None and sink.sample_rate != sample_rate:
            raise ValueError("OutputSink sample rate does not match the engine")
        self.sample_rate = sample_rate
        self._sink = sink
        self._commands: SimpleQueue[tuple[_CommandType, object | None]] = SimpleQueue()
        self._current_track: _PreparedTrack | None = None
        self._queued_track: _PreparedTrack | None = None
        self._queued_entry_id: str | None = None
        self._transition: TransitionContext | None = None
        self._position = 0
        self._state_machine = PlaybackStateMachine()
        self._play_command_generation = 0
        self._master_volume_db = 0.0
        self._master_gain = 1.0
        self._muted = False
        self._loop_count = 0
        self._one_shot_end: int | None = None
        self._active_loop_id = "main"
        self._pending_loop_id: str | None = None
        self._output_peak_left_dbfs = -80.0
        self._output_peak_right_dbfs = -80.0
        self._output_tap: object | None = None
        self._output_tap_failures = 0

        # Fixed-size callback scratch avoids arrays proportional to a requested
        # transition duration. Larger callback blocks are processed in chunks.
        self._indices = np.arange(_SCRATCH_FRAMES, dtype=np.float64)
        self._gain_a = np.empty(_SCRATCH_FRAMES, dtype=np.float64)
        self._gain_b = np.empty(_SCRATCH_FRAMES, dtype=np.float64)
        self._mix_scratch = np.empty((_SCRATCH_FRAMES, 2), dtype=np.float32)

    def start_output(self) -> None:
        if self._sink is None:
            raise RuntimeError("AudioEngine has no OutputSink")
        self._sink.start(self.render_into)

    def replace_output_sink(self, sink: OutputSink, *, start: bool = True) -> None:
        """Replace the physical output while retaining callback-owned playback."""
        if sink.sample_rate != self.sample_rate:
            raise ValueError("OutputSink sample rate does not match the engine")
        previous = self._sink
        if previous is sink:
            if start:
                sink.start(self.render_into)
            return
        if previous is not None:
            previous.stop()
        self._sink = sink
        if start:
            sink.start(self.render_into)

    def close(self) -> None:
        self.stop()
        if self._sink is not None:
            self._sink.stop()

    def set_output_tap(self, tap: object | None) -> None:
        """Attach one non-blocking consumer of the shared program bus."""
        if tap is not None and not callable(getattr(tap, "consume", None)):
            raise TypeError("output tap must provide consume(samples)")
        self._output_tap = tap

    def play(
        self,
        audio: DecodedAudio,
        metadata: TrackMetadata,
        *,
        start_frame: int | None = None,
    ) -> None:
        """Schedule an initial track; normal changes use ``queue_track``."""
        prepared = self._prepare_track(audio, metadata)
        position = (
            prepared.metadata.effective_intro_start
            if start_frame is None else start_frame
        )
        if position < prepared.metadata.play_start or position >= prepared.metadata.loop_end:
            raise ValueError("start_frame must lie in [play_start, loop_end)")
        if (
            prepared.metadata.effective_intro_end <= position
            < prepared.metadata.loop_start
        ):
            position = prepared.metadata.loop_start
        self._commands.put((_CommandType.PLAY, (prepared, position)))

    def queue_track(
        self,
        audio: DecodedAudio,
        metadata: TrackMetadata,
        *,
        entry_id: str = "intro",
    ) -> None:
        """Queue or replace NEXT for the normal loop/outro transition."""
        prepared = self._prepare_track(audio, metadata)
        _track_start(prepared, entry_id)
        self._commands.put((_CommandType.QUEUE, (prepared, entry_id)))

    def select_queued_entry(self, entry_id: str) -> None:
        """Change where the current NEXT track will begin."""
        if not entry_id.strip():
            raise ValueError("entry_id must not be empty")
        self._commands.put((_CommandType.SELECT_QUEUED_ENTRY, entry_id))

    def play_once(
        self,
        audio: DecodedAudio,
        metadata: TrackMetadata,
        *,
        start_frame: int,
        end_frame: int,
    ) -> None:
        """Play exactly ``[start_frame, end_frame)`` and then stop."""
        prepared = self._prepare_track(audio, metadata)
        if not 0 <= start_frame < end_frame <= audio.total_frames:
            raise ValueError("one-shot range must lie inside the audio")
        self._commands.put(
            (_CommandType.PLAY_ONCE, (prepared, int(start_frame), int(end_frame)))
        )

    def cancel_queued_track(self) -> None:
        """Cancel NEXT only while the current track can remain in its loop."""
        self._commands.put((_CommandType.CANCEL_QUEUE, None))

    def select_loop(self, loop_id: str) -> None:
        """Activate a loop at the next end of the currently playing loop."""
        if not loop_id.strip():
            raise ValueError("loop_id must not be empty")
        self._commands.put((_CommandType.SELECT_LOOP, loop_id))

    def transition_to(
        self,
        audio: DecodedAudio,
        metadata: TrackMetadata,
        *,
        mode: TransitionMode = TransitionMode.NORMAL,
        duration_seconds: float = 0.0,
        entry_id: str = "intro",
    ) -> None:
        """Request a normal queue, hard cut, sequential fade or crossfade."""
        if not isfinite(duration_seconds) or duration_seconds < 0:
            raise ValueError("transition duration must be finite and >= 0 seconds")
        if not isinstance(mode, TransitionMode):
            mode = TransitionMode(mode)
        if mode is TransitionMode.NORMAL:
            self.queue_track(audio, metadata, entry_id=entry_id)
            return
        prepared = self._prepare_track(audio, metadata)
        _track_start(prepared, entry_id)
        transition_frames = round(duration_seconds * self.sample_rate)
        _LOG.info(
            "forced transition requested: mode=%s target=%s duration=%.6fs frames=%d",
            mode.value,
            prepared.metadata.id,
            duration_seconds,
            transition_frames,
        )
        self._commands.put(
            (_CommandType.TRANSITION, (prepared, mode, transition_frames, entry_id))
        )

    def pause(self) -> None:
        self._commands.put((_CommandType.PAUSE, None))

    def resume(self) -> None:
        self._commands.put((_CommandType.RESUME, None))

    def stop(self) -> None:
        self._commands.put((_CommandType.STOP, None))

    def set_master_volume_db(self, value: float) -> None:
        if (isinf(value) and value > 0) or (
            not isinf(value) and not -80.0 <= value <= 0.0
        ):
            raise ValueError("master volume must be -inf or between -80 and 0 dB")
        self._commands.put((_CommandType.MASTER_DB, float(value)))

    def set_muted(self, muted: bool) -> None:
        self._commands.put((_CommandType.MUTE, bool(muted)))

    def render(self, frames: int) -> np.ndarray:
        """Allocate and render a block for tests and non-realtime consumers."""
        if frames < 0:
            raise ValueError("frames must be >= 0")
        output = np.empty((frames, 2), dtype=np.float32)
        self.render_into(output)
        return output

    def render_into(self, output: np.ndarray) -> None:
        """Fill preallocated stereo float32 PCM without file I/O or logging."""
        if output.dtype != np.float32 or output.ndim != 2 or output.shape[1] != 2:
            raise ValueError("output must be a float32 array shaped (frames, 2)")
        self._apply_commands()
        output.fill(0.0)
        if (
            self._current_track is None
            or self._state_machine.state is PlaybackState.PAUSED
            or output.shape[0] == 0
        ):
            self._output_peak_left_dbfs = -80.0
            self._output_peak_right_dbfs = -80.0
            self._publish_output(output)
            return

        write_at = 0
        while write_at < output.shape[0] and self._current_track is not None:
            if self._transition is not None:
                write_at = self._render_transition(output, write_at)
            else:
                write_at = self._render_current_segment(output, write_at)

        # Secondary outputs branch before the local gain stage. Every sink
        # applies its own volume, mute and final clipping independently.
        self._publish_output(output)
        master_gain = 0.0 if self._muted else self._master_gain
        if master_gain != 1.0:
            np.multiply(output, master_gain, out=output)
        # Float32 mixing retains internal headroom. This final sample-peak guard
        # is the last resort after transition mixing and master attenuation.
        np.clip(output, -1.0, 1.0, out=output)
        self._output_peak_left_dbfs = _channel_peak_dbfs(output[:, 0])
        self._output_peak_right_dbfs = _channel_peak_dbfs(output[:, 1])

    def _publish_output(self, output: np.ndarray) -> None:
        tap = self._output_tap
        if tap is None:
            return
        try:
            tap.consume(output)
        except Exception:
            # A secondary output must never abort the realtime local stream.
            self._output_tap_failures += 1

    def snapshot(self) -> PlaybackSnapshot:
        current = self._current_track
        queued = self._queued_track
        transition = self._transition
        return PlaybackSnapshot(
            playing=current is not None,
            paused=self._state_machine.state is PlaybackState.PAUSED,
            position=self._position,
            track_id=current.metadata.id if current is not None else None,
            queued_track_id=queued.metadata.id if queued is not None else None,
            transition_target_id=(
                transition.target_track.metadata.id if transition is not None else None
            ),
            track_filepath=(
                current.metadata.filepath if current is not None else None
            ),
            queued_track_filepath=(
                queued.metadata.filepath if queued is not None else None
            ),
            queued_entry_id=self._queued_entry_id if queued is not None else None,
            transition_target_filepath=(
                transition.target_track.metadata.filepath
                if transition is not None else None
            ),
            transition_target_entry_id=(
                transition.target_entry_id if transition is not None else None
            ),
            transition_frame=transition.current_frame if transition is not None else 0,
            transition_total_frames=(
                transition.total_frames if transition is not None else 0
            ),
            state=self._state_machine.state,
            master_volume_db=self._master_volume_db,
            muted=self._muted,
            loop_count=self._loop_count,
            next_jump_frames=self._next_jump_frames(),
            next_exit_frames=self._next_exit_frames(),
            output_peak_left_dbfs=self._output_peak_left_dbfs,
            output_peak_right_dbfs=self._output_peak_right_dbfs,
            can_cancel_queued_track=(
                queued is not None
                and transition is None
                and self._state_machine.can_cancel_queue()
            ),
            active_loop_id=self._active_loop_id,
            pending_loop_id=self._pending_loop_id,
            play_command_generation=self._play_command_generation,
        )

    @property
    def current_track(self) -> TrackMetadata | None:
        track = self._current_track
        return _copy_metadata(track.metadata) if track is not None else None

    @property
    def queued_track(self) -> TrackMetadata | None:
        track = self._queued_track
        return _copy_metadata(track.metadata) if track is not None else None

    def _render_current_segment(self, output: np.ndarray, write_at: int) -> int:
        track = self._current_track
        if track is None:
            return write_at
        metadata = track.metadata
        loop_start, loop_end = metadata.loop_bounds(self._active_loop_id)
        state = self._state_machine.state
        if state is PlaybackState.PLAYING_INTRO:
            boundary = metadata.effective_intro_end
        elif state in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
            boundary = self._next_active_loop_boundary(
                metadata, self._position, loop_end
            )
        elif state is PlaybackState.PLAYING_OUTRO:
            boundary = (
                self._one_shot_end
                if self._one_shot_end is not None
                else metadata.effective_outro_end
            )
        else:
            raise RuntimeError(f"cannot render normal playback in state {state.name}")

        available = boundary - self._position
        if available < 0:
            raise RuntimeError(
                f"position {self._position} passed boundary {boundary} in {state.name}"
            )
        count = min(available, output.shape[0] - write_at)
        if count:
            destination = output[write_at : write_at + count]
            destination[:] = track.audio.samples[self._position : self._position + count]
            _apply_track_gain(destination, metadata)
            self._position += count
            write_at += count

        if self._position == boundary:
            if state is PlaybackState.PLAYING_INTRO:
                self._state_machine.reached_loop_start(
                    has_queued_track=self._queued_track is not None
                )
                self._position = loop_start
            elif state in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
                exit_points = metadata.loop_exit_points(self._active_loop_id)
                change_requested = (
                    self._queued_track is not None
                    or self._pending_loop_id is not None
                )
                at_exit = (
                    change_requested
                    and (boundary == loop_end or boundary in exit_points)
                )
                if boundary == loop_end:
                    self._loop_count += 1
                if at_exit:
                    self._take_requested_loop_exit(metadata)
                else:
                    if not change_requested:
                        self._state_machine.reached_loop_end(
                            has_queued_track=False
                        )
                    self._position = loop_start
            else:
                self._start_queued_track_or_stop()
        return write_at

    def _next_jump_frames(self) -> int:
        """Frames until the next callback-owned phase or position jump."""
        track = self._current_track
        if track is None:
            return 0
        phase = self._state_machine.playback_phase
        transition = self._transition
        if transition is not None:
            if phase is PlaybackState.FADING_OUT:
                return max(0, transition.fade_out_frames - transition.current_frame)
            if phase in (PlaybackState.FADING_IN, PlaybackState.CROSSFADING):
                return max(0, transition.total_frames - transition.current_frame)
        metadata = track.metadata
        if phase is PlaybackState.PLAYING_INTRO:
            boundary = metadata.effective_intro_end
        elif phase in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
            loop_end = metadata.loop_bounds(self._active_loop_id)[1]
            boundary = self._next_active_loop_boundary(
                metadata, self._position, loop_end
            )
        elif phase is PlaybackState.PLAYING_OUTRO:
            boundary = (
                self._one_shot_end
                if self._one_shot_end is not None
                else metadata.effective_outro_end
            )
        else:
            return 0
        return max(0, boundary - self._position)

    def _next_exit_frames(self) -> int | None:
        """Audible frames until the next explicit or implicit loop exit."""
        track = self._current_track
        if track is None or self._transition is not None:
            return None
        metadata = track.metadata
        points = metadata.loop_exit_points(self._active_loop_id)
        phase = self._state_machine.playback_phase
        loop_start, loop_end = metadata.loop_bounds(self._active_loop_id)
        first_point = points[0] if points else loop_end
        if phase is PlaybackState.PLAYING_INTRO:
            return max(0, metadata.effective_intro_end - self._position) + (
                first_point - loop_start
            )
        if phase not in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
            return None
        next_point = next(
            (point for point in points if point >= self._position), loop_end
        )
        return max(0, next_point - self._position)

    def _next_active_loop_boundary(
        self, metadata: TrackMetadata, position: int, loop_end: int
    ) -> int:
        if self._queued_track is None and self._pending_loop_id is None:
            return loop_end
        points = metadata.loop_exit_points(self._active_loop_id)
        return next((point for point in points if point >= position), loop_end)

    def _take_requested_loop_exit(self, metadata: TrackMetadata) -> None:
        if self._queued_track is not None:
            self._state_machine.reached_loop_end(has_queued_track=True)
            self._position = metadata.effective_outro_start
            return
        if self._pending_loop_id is None:
            raise RuntimeError("loop exit reached without a pending destination")
        self._state_machine.reached_loop_end(has_queued_track=False)
        self._active_loop_id = self._pending_loop_id
        self._pending_loop_id = None
        self._position = metadata.loop_bounds(self._active_loop_id)[0]

    def _render_transition(self, output: np.ndarray, write_at: int) -> int:
        context = self._transition
        if context is None:
            return write_at
        if context.mode is TransitionMode.FADE:
            return self._render_fade(output, write_at, context)
        if context.mode is TransitionMode.CROSSFADE:
            return self._render_crossfade(output, write_at, context)
        raise RuntimeError(f"unexpected active transition {context.mode.value}")

    def _render_fade(
        self, output: np.ndarray, write_at: int, context: TransitionContext
    ) -> int:
        if self._state_machine.state is PlaybackState.FADING_OUT:
            phase_frame = context.current_frame
            remaining = context.fade_out_frames - phase_frame
            count = min(remaining, output.shape[0] - write_at, _SCRATCH_FRAMES)
            destination = output[write_at : write_at + count]
            context.source_position = self._render_transition_source_into(
                destination, context.source_track, context.source_position,
                context.source_state_machine,
                context.source_loop_start, context.source_loop_end,
            )
            fill_fade_gains(
                phase_frame,
                count,
                context.fade_out_frames,
                self._indices,
                self._gain_a,
                fade_in=False,
            )
            np.multiply(destination, self._gain_a[:count, None], out=destination)
            context.current_frame += count
            write_at += count
            self._position = context.source_position
            if context.current_frame == context.fade_out_frames:
                self._current_track = context.target_track
                self._position = context.target_position
                self._active_loop_id = context.target_active_loop_id
                self._state_machine.begin_fade_in()
            return write_at

        if self._state_machine.state is not PlaybackState.FADING_IN:
            raise RuntimeError("fade context exists outside a fade state")
        phase_frame = context.current_frame - context.fade_out_frames
        remaining = context.fade_in_frames - phase_frame
        count = min(remaining, output.shape[0] - write_at, _SCRATCH_FRAMES)
        destination = output[write_at : write_at + count]
        context.target_position, loops = self._render_normal_track_into(
            destination,
            context.target_track,
            context.target_position,
            context.target_state_machine,
            context.target_loop_start,
            context.target_loop_end,
        )
        context.target_loop_count += loops
        self._loop_count = context.target_loop_count
        fill_fade_gains(
            phase_frame,
            count,
            context.fade_in_frames,
            self._indices,
            self._gain_b,
            fade_in=True,
        )
        np.multiply(destination, self._gain_b[:count, None], out=destination)
        context.current_frame += count
        write_at += count
        self._position = context.target_position
        if context.current_frame == context.total_frames:
            if self._queued_track is not None:
                context.target_state_machine.queue_requested()
            self._state_machine = context.target_state_machine
            self._active_loop_id = context.target_active_loop_id
            self._transition = None
        return write_at

    def _render_crossfade(
        self, output: np.ndarray, write_at: int, context: TransitionContext
    ) -> int:
        remaining = context.total_frames - context.current_frame
        count = min(remaining, output.shape[0] - write_at, _SCRATCH_FRAMES)
        destination = output[write_at : write_at + count]
        context.source_position = self._render_transition_source_into(
            destination, context.source_track, context.source_position,
            context.source_state_machine,
            context.source_loop_start, context.source_loop_end,
        )

        target_pcm = self._mix_scratch[:count]
        context.target_position, loops = self._render_normal_track_into(
            target_pcm,
            context.target_track,
            context.target_position,
            context.target_state_machine,
            context.target_loop_start,
            context.target_loop_end,
        )
        context.target_loop_count += loops
        fill_equal_power_gains(
            context.current_frame,
            count,
            context.total_frames,
            self._indices,
            self._gain_a,
            self._gain_b,
        )
        np.multiply(destination, self._gain_a[:count, None], out=destination)
        np.multiply(target_pcm, self._gain_b[:count, None], out=target_pcm)
        np.add(destination, target_pcm, out=destination)

        context.current_frame += count
        write_at += count
        self._position = context.source_position
        if context.current_frame == context.total_frames:
            self._current_track = context.target_track
            self._position = context.target_position
            if self._queued_track is not None:
                context.target_state_machine.queue_requested()
            self._state_machine = context.target_state_machine
            self._active_loop_id = context.target_active_loop_id
            self._loop_count = context.target_loop_count
            self._transition = None
        return write_at

    def _render_normal_track_into(
        self,
        output: np.ndarray,
        track: _PreparedTrack,
        position: int,
        state_machine: PlaybackStateMachine,
        loop_start: int,
        loop_end: int,
    ) -> tuple[int, int]:
        """Render one independent intro/loop cursor, used by transition targets."""
        write_at = 0
        metadata = track.metadata
        loop_count = 0
        while write_at < output.shape[0]:
            state = state_machine.state
            if state is PlaybackState.PLAYING_INTRO:
                boundary = metadata.effective_intro_end
            elif state is PlaybackState.LOOPING:
                boundary = loop_end
            elif state is PlaybackState.PLAYING_OUTRO:
                boundary = metadata.effective_outro_end
            else:
                raise RuntimeError(f"invalid target playback state {state.name}")
            count = min(boundary - position, output.shape[0] - write_at)
            destination = output[write_at : write_at + count]
            destination[:] = track.audio.samples[position : position + count]
            position += count
            write_at += count
            if position == boundary:
                if state is PlaybackState.PLAYING_INTRO:
                    state_machine.reached_loop_start(has_queued_track=False)
                    position = loop_start
                elif state is PlaybackState.LOOPING:
                    state_machine.reached_loop_end(has_queued_track=False)
                    position = loop_start
                    loop_count += 1
                elif write_at < output.shape[0]:
                    raise RuntimeError("transition target exceeded outro")
        _apply_track_gain(output, metadata)
        return position, loop_count

    def _render_transition_source_into(
        self,
        output: np.ndarray,
        track: _PreparedTrack,
        position: int,
        state_machine: PlaybackStateMachine,
        loop_start: int,
        loop_end: int,
    ) -> int:
        """Render source once through intro, loop remainder and outro."""
        write_at = 0
        metadata = track.metadata
        while write_at < output.shape[0]:
            state = state_machine.state
            if state is PlaybackState.PLAYING_INTRO:
                boundary = metadata.effective_intro_end
            elif state in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
                boundary = loop_end
            elif state is PlaybackState.PLAYING_OUTRO:
                boundary = metadata.effective_outro_end
            else:
                raise RuntimeError(f"invalid transition source state {state.name}")
            count = min(boundary - position, output.shape[0] - write_at)
            output[write_at : write_at + count] = track.audio.samples[
                position : position + count
            ]
            position += count
            write_at += count
            if position == boundary:
                if state is PlaybackState.PLAYING_INTRO:
                    state_machine.reached_loop_start(has_queued_track=True)
                    position = loop_start
                elif state in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
                    state_machine.reached_loop_end(has_queued_track=True)
                    position = metadata.effective_outro_start
                elif write_at < output.shape[0]:
                    raise RuntimeError("transition source exceeded outro")
        _apply_track_gain(output, metadata)
        return position

    def _apply_commands(self) -> None:
        for _ in range(64):
            try:
                command, payload = self._commands.get_nowait()
            except Empty:
                break
            if command is _CommandType.PLAY:
                prepared, position = payload  # type: ignore[misc]
                self._current_track = prepared
                self._queued_track = None
                self._queued_entry_id = None
                self._transition = None
                self._position = position
                self._loop_count = 0
                self._one_shot_end = None
                self._active_loop_id = "main"
                self._pending_loop_id = None
                self._state_machine.start_track(
                    has_intro=position < prepared.metadata.effective_intro_end
                )
                self._play_command_generation += 1
            elif command is _CommandType.PLAY_ONCE:
                prepared, start, end = payload  # type: ignore[misc]
                self._current_track = prepared
                self._queued_track = None
                self._queued_entry_id = None
                self._transition = None
                self._position = start
                self._loop_count = 0
                self._one_shot_end = end
                self._active_loop_id = "main"
                self._pending_loop_id = None
                self._state_machine = PlaybackStateMachine(
                    state=PlaybackState.PLAYING_OUTRO
                )
                self._play_command_generation += 1
            elif command is _CommandType.QUEUE:
                prepared, entry_id = payload  # type: ignore[misc]
                if self._current_track is None:
                    self._hard_cut_to(prepared, entry_id)
                elif self._transition is None:
                    self._queued_track = prepared
                    self._queued_entry_id = entry_id
                    self._state_machine.queue_requested()
                else:
                    # Normal queue requests during a forced transition replace
                    # NEXT and are considered only after the transition target.
                    self._queued_track = prepared
                    self._queued_entry_id = entry_id
            elif command is _CommandType.CANCEL_QUEUE:
                if self._queued_track is not None and self._transition is None:
                    if self._state_machine.queue_cancelled():
                        self._queued_track = None
                        self._queued_entry_id = None
            elif command is _CommandType.SELECT_QUEUED_ENTRY:
                if self._queued_track is not None and self._transition is None:
                    entry_id = str(payload)
                    _track_start(self._queued_track, entry_id)
                    self._queued_entry_id = entry_id
            elif command is _CommandType.SELECT_LOOP:
                loop_id = str(payload)
                # During a forced transition current_track still refers to the
                # source. Carrying one of its loop ids over to the target can
                # select the wrong loop or fail in the following callback.
                if self._current_track is not None and self._transition is None:
                    self._current_track.metadata.loop_bounds(loop_id)
                    if (
                        loop_id == self._active_loop_id
                        or loop_id == self._pending_loop_id
                    ):
                        self._pending_loop_id = None
                    else:
                        self._pending_loop_id = loop_id
            elif command is _CommandType.TRANSITION:
                prepared, mode, frames, entry_id = payload  # type: ignore[misc]
                self._begin_forced_transition(prepared, mode, frames, entry_id)
            elif command is _CommandType.PAUSE:
                self._state_machine.pause()
            elif command is _CommandType.RESUME:
                self._state_machine.resume()
            elif command is _CommandType.STOP:
                self._current_track = None
                self._queued_track = None
                self._queued_entry_id = None
                self._transition = None
                self._position = 0
                self._loop_count = 0
                self._one_shot_end = None
                self._active_loop_id = "main"
                self._pending_loop_id = None
                self._output_peak_left_dbfs = -80.0
                self._output_peak_right_dbfs = -80.0
                self._state_machine.stop()
            elif command is _CommandType.MASTER_DB:
                self._master_volume_db = float(payload)
                self._master_gain = _db_to_gain(self._master_volume_db)
            elif command is _CommandType.MUTE:
                self._muted = bool(payload)

    def _begin_forced_transition(
        self,
        target: _PreparedTrack,
        mode: TransitionMode,
        requested_frames: int,
        entry_id: str,
    ) -> None:
        # A track may deliberately be queued behind itself. In that case
        # GO NOW means restart it through the selected transition. Preserve
        # that intent before consuming NEXT; an unqueued transition request
        # to the already active track remains the established no-op.
        target_was_queued = (
            self._queued_track is not None
            and self._queued_track.metadata.id == target.metadata.id
            and self._queued_track.metadata.filepath == target.metadata.filepath
        )
        self._queued_track = None
        self._queued_entry_id = None
        self._one_shot_end = None
        if self._transition is not None:
            # Latest wins without nesting: the audible transition target becomes
            # the sole source cursor for the replacement command.
            active = self._transition
            self._current_track = active.target_track
            self._position = active.target_position
            self._state_machine = active.target_state_machine
            self._loop_count = active.target_loop_count
            self._active_loop_id = active.target_active_loop_id
            self._transition = None

        source = self._current_track
        if source is None:
            self._hard_cut_to(target, entry_id)
            return
        if (
            source.metadata.id == target.metadata.id
            and source.metadata.filepath == target.metadata.filepath
            and not target_was_queued
        ):
            return
        if mode is TransitionMode.HARD_CUT:
            self._hard_cut_to(target, entry_id)
            return

        target_start = _track_start(target, entry_id)

        source_machine = copy(self._state_machine)
        if source_machine.state is PlaybackState.PAUSED:
            # A forced GO NOW request is explicit transport intent. Resume the
            # captured source phase before constructing the transition.
            source_machine.resume()
        source_loop_start, source_loop_end = source.metadata.loop_bounds(
            self._active_loop_id
        )
        self._pending_loop_id = None
        source_remaining = self._source_program_remaining(
            source.metadata, self._position, source_machine.state,
            source_loop_start, source_loop_end,
        )
        if mode is TransitionMode.CROSSFADE:
            effective_frames = min(
                requested_frames,
                source_remaining,
                _target_program_remaining(target, target_start),
            )
            if effective_frames < 2:
                self._hard_cut_to(target, entry_id)
                return
            self._transition = TransitionContext(
                mode=mode,
                total_frames=effective_frames,
                current_frame=0,
                source_track=source,
                source_position=self._position,
                source_state_machine=source_machine,
                source_loop_start=source_loop_start,
                source_loop_end=source_loop_end,
                target_track=target,
                target_position=target_start.position,
                target_state_machine=target_start.state_machine,
                target_entry_id=entry_id,
                target_active_loop_id=target_start.active_loop_id,
                target_loop_start=target_start.loop_start,
                target_loop_end=target_start.loop_end,
            )
            self._state_machine.begin_crossfade()
            return

        # FADE duration is total time, split floor/ceil between its two phases.
        fade_out_frames = requested_frames // 2
        fade_in_frames = requested_frames - fade_out_frames
        fade_out_frames = min(fade_out_frames, source_remaining)
        fade_in_frames = min(
            fade_in_frames, _target_program_remaining(target, target_start)
        )
        if fade_out_frames < 2 or fade_in_frames < 2:
            self._hard_cut_to(target, entry_id)
            return
        self._transition = TransitionContext(
            mode=mode,
            total_frames=fade_out_frames + fade_in_frames,
            current_frame=0,
            source_track=source,
            source_position=self._position,
            source_state_machine=source_machine,
            source_loop_start=source_loop_start,
            source_loop_end=source_loop_end,
            target_track=target,
            target_position=target_start.position,
            target_state_machine=target_start.state_machine,
            target_entry_id=entry_id,
            target_active_loop_id=target_start.active_loop_id,
            target_loop_start=target_start.loop_start,
            target_loop_end=target_start.loop_end,
            fade_out_frames=fade_out_frames,
            fade_in_frames=fade_in_frames,
        )
        self._state_machine.begin_fade()

    def _hard_cut_to(self, target: _PreparedTrack, entry_id: str = "intro") -> None:
        start = _track_start(target, entry_id)
        self._current_track = target
        self._queued_track = None
        self._queued_entry_id = None
        self._transition = None
        self._position = start.position
        self._loop_count = 0
        self._one_shot_end = None
        self._active_loop_id = start.active_loop_id
        self._pending_loop_id = None
        self._state_machine = start.state_machine

    def _source_program_remaining(
        self, metadata: TrackMetadata, position: int, state: PlaybackState,
        loop_start: int, loop_end: int,
    ) -> int:
        if state is PlaybackState.PLAYING_INTRO:
            return (
                metadata.effective_intro_end - position
                + loop_end - loop_start
                + metadata.effective_outro_end - metadata.effective_outro_start
            )
        if state in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
            return (
                loop_end - position
                + metadata.effective_outro_end - metadata.effective_outro_start
            )
        return metadata.effective_outro_end - position

    def _prepare_track(
        self, audio: DecodedAudio, metadata: TrackMetadata
    ) -> _PreparedTrack:
        if audio.sample_rate != self.sample_rate:
            raise ValueError(
                f"audio is {audio.sample_rate} Hz, engine is {self.sample_rate} Hz"
            )
        if metadata.internal_sample_rate != self.sample_rate:
            raise ValueError("metadata internal_sample_rate does not match the engine")
        playback_metadata = _copy_metadata(metadata)
        playback_metadata.validate(audio.total_frames)
        return _PreparedTrack(audio, playback_metadata)

    def _start_queued_track_or_stop(self) -> None:
        next_track, self._queued_track = self._queued_track, None
        entry_id, self._queued_entry_id = self._queued_entry_id or "intro", None
        if next_track is None:
            self._current_track = None
            self._position = 0
            self._one_shot_end = None
            self._active_loop_id = "main"
            self._pending_loop_id = None
            self._state_machine.stop()
            return
        self._hard_cut_to(next_track, entry_id)


def _track_start(track: _PreparedTrack, entry_id: str) -> _TrackStart:
    metadata = track.metadata
    if entry_id == "intro":
        has_intro = metadata.effective_intro_start < metadata.effective_intro_end
        machine = PlaybackStateMachine()
        machine.start_track(has_intro=has_intro)
        return _TrackStart(
            entry_id,
            metadata.effective_intro_start if has_intro else metadata.loop_start,
            machine,
            "main",
            metadata.loop_start,
            metadata.loop_end,
        )
    if entry_id == "outro":
        return _TrackStart(
            entry_id,
            metadata.effective_outro_start,
            PlaybackStateMachine(state=PlaybackState.PLAYING_OUTRO),
            "main",
            metadata.loop_start,
            metadata.loop_end,
        )
    if entry_id.startswith("loop:"):
        loop_id = entry_id.removeprefix("loop:")
        if not loop_id:
            raise ValueError("loop entry must contain a loop id")
        loop_start, loop_end = metadata.loop_bounds(loop_id)
        return _TrackStart(
            entry_id,
            loop_start,
            PlaybackStateMachine(state=PlaybackState.LOOPING),
            loop_id,
            loop_start,
            loop_end,
        )
    raise ValueError(f"unknown track entry: {entry_id}")


def _target_program_remaining(track: _PreparedTrack, start: _TrackStart) -> int:
    if start.state_machine.state is PlaybackState.PLAYING_OUTRO:
        return track.metadata.effective_outro_end - start.position
    return 2**63 - 1


def _apply_track_gain(output: np.ndarray, metadata: TrackMetadata) -> None:
    gain = _db_to_gain(metadata.playback_gain_db)
    if gain != 1.0:
        np.multiply(output, gain, out=output)


def _db_to_gain(decibels: float) -> float:
    return pow(10.0, decibels / 20.0)


def _channel_peak_dbfs(channel: np.ndarray) -> float:
    if channel.size == 0:
        return -80.0
    peak = max(float(np.max(channel)), -float(np.min(channel)))
    if peak <= 0.0001:
        return -80.0
    return max(-80.0, 20.0 * log10(peak))


def _copy_metadata(metadata: TrackMetadata) -> TrackMetadata:
    return replace(
        metadata,
        tags=list(metadata.tags),
        main_loop_exit_points=list(metadata.main_loop_exit_points),
        beat_positions=list(metadata.beat_positions),
        detected_beat_positions=list(metadata.detected_beat_positions),
        alternate_loops=[
            replace(region, exit_points=list(region.exit_points))
            for region in metadata.alternate_loops
        ],
    )
