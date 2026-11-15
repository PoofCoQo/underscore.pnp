"""Explicit callback-owned state machine for normal track playback."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class PlaybackState(Enum):
    """Observable phase of normal, non-transitioned playback."""

    STOPPED = auto()
    PLAYING_INTRO = auto()
    LOOPING = auto()
    EXITING_LOOP = auto()
    PLAYING_OUTRO = auto()
    FADING_OUT = auto()
    FADING_IN = auto()
    CROSSFADING = auto()
    PAUSED = auto()


@dataclass(slots=True)
class PlaybackStateMachine:
    """Validate the small set of legal phase-2 playback transitions.

    The audio callback is the sole owner of this object. ``PAUSED`` retains the
    phase to which playback returns, including a queued loop exit.
    """

    state: PlaybackState = PlaybackState.STOPPED
    _resume_state: PlaybackState = field(
        default=PlaybackState.STOPPED, init=False, repr=False
    )

    @property
    def playback_phase(self) -> PlaybackState:
        """Audible phase, retained while the transport is paused."""
        return self._resume_state if self.state is PlaybackState.PAUSED else self.state

    def start_track(self, *, has_intro: bool) -> None:
        self.state = (
            PlaybackState.PLAYING_INTRO if has_intro else PlaybackState.LOOPING
        )
        self._resume_state = PlaybackState.STOPPED

    def queue_requested(self) -> None:
        if self.state is PlaybackState.LOOPING:
            self.state = PlaybackState.EXITING_LOOP
        elif (
            self.state is PlaybackState.PAUSED
            and self._resume_state is PlaybackState.LOOPING
        ):
            self._resume_state = PlaybackState.EXITING_LOOP

    def can_cancel_queue(self) -> bool:
        if self.state in (
            PlaybackState.PLAYING_INTRO,
            PlaybackState.LOOPING,
            PlaybackState.EXITING_LOOP,
        ):
            return True
        return self.state is PlaybackState.PAUSED and self._resume_state in (
            PlaybackState.PLAYING_INTRO,
            PlaybackState.LOOPING,
            PlaybackState.EXITING_LOOP,
        )

    def queue_cancelled(self) -> bool:
        """Cancel a pending loop exit while the source is still loopable."""
        if not self.can_cancel_queue():
            return False
        if self.state is PlaybackState.EXITING_LOOP:
            self.state = PlaybackState.LOOPING
            return True
        if self.state in (PlaybackState.PLAYING_INTRO, PlaybackState.LOOPING):
            return True
        if self.state is PlaybackState.PAUSED and self._resume_state in (
            PlaybackState.PLAYING_INTRO,
            PlaybackState.LOOPING,
            PlaybackState.EXITING_LOOP,
        ):
            if self._resume_state is PlaybackState.EXITING_LOOP:
                self._resume_state = PlaybackState.LOOPING
            return True
        return True

    def reached_loop_start(self, *, has_queued_track: bool) -> None:
        if self.state is not PlaybackState.PLAYING_INTRO:
            raise RuntimeError(f"cannot finish intro from state {self.state.name}")
        self.state = (
            PlaybackState.EXITING_LOOP
            if has_queued_track
            else PlaybackState.LOOPING
        )

    def reached_loop_end(self, *, has_queued_track: bool) -> bool:
        if self.state not in (PlaybackState.LOOPING, PlaybackState.EXITING_LOOP):
            raise RuntimeError(f"cannot finish loop from state {self.state.name}")
        if has_queued_track:
            self.state = PlaybackState.PLAYING_OUTRO
            return True
        self.state = PlaybackState.LOOPING
        return False

    def pause(self) -> None:
        if self.state not in (PlaybackState.STOPPED, PlaybackState.PAUSED):
            self._resume_state = self.state
            self.state = PlaybackState.PAUSED

    def begin_fade(self) -> None:
        if self.state is PlaybackState.STOPPED:
            raise RuntimeError("cannot fade from STOPPED")
        self.state = PlaybackState.FADING_OUT

    def begin_fade_in(self) -> None:
        if self.state is not PlaybackState.FADING_OUT:
            raise RuntimeError(f"cannot fade in from state {self.state.name}")
        self.state = PlaybackState.FADING_IN

    def begin_crossfade(self) -> None:
        if self.state is PlaybackState.STOPPED:
            raise RuntimeError("cannot crossfade from STOPPED")
        self.state = PlaybackState.CROSSFADING

    def resume(self) -> None:
        if self.state is PlaybackState.PAUSED:
            self.state = self._resume_state
            self._resume_state = PlaybackState.STOPPED

    def stop(self) -> None:
        self.state = PlaybackState.STOPPED
        self._resume_state = PlaybackState.STOPPED
