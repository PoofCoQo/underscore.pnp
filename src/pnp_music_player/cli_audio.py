"""Command-line smoke test for looping and normal queued transitions."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import signal
import sys
from threading import Event, Thread, Timer

from pnp_music_player.audio.engine import AudioEngine
from pnp_music_player.audio.loader import load_audio
from pnp_music_player.audio.sinks import LocalAudioSink
from pnp_music_player.audio.transitions import TransitionMode
from pnp_music_player.logging_config import configure_logging
from pnp_music_player.models.track import TrackMetadata


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Loop one local track and choose up to two queue candidates without "
            "reopening the PortAudio stream."
        )
    )
    parser.add_argument("audio_file", type=Path)
    parser.add_argument("next_audio_file", type=Path, nargs="?")
    parser.add_argument("third_audio_file", type=Path, nargs="?")
    parser.add_argument("--track-1-play-start", type=int, default=0, dest="play_start")
    parser.add_argument("--track-1-loop-start", type=int, dest="loop_start")
    parser.add_argument("--track-1-loop-end", type=int, dest="loop_end")
    parser.add_argument("--track-1-play-end", type=int, dest="play_end")
    parser.add_argument(
        "--track-2-play-start", type=int, default=0, dest="next_play_start"
    )
    parser.add_argument("--track-2-loop-start", type=int, dest="next_loop_start")
    parser.add_argument("--track-2-loop-end", type=int, dest="next_loop_end")
    parser.add_argument("--track-2-play-end", type=int, dest="next_play_end")
    parser.add_argument("--track-3-play-start", type=int, default=0)
    parser.add_argument("--track-3-loop-start", type=int)
    parser.add_argument("--track-3-loop-end", type=int)
    parser.add_argument("--track-3-play-end", type=int)
    parser.add_argument(
        "--queue-after",
        type=float,
        dest="queue_after",
        metavar="SECONDS",
        help="apply --transition to --queue-track after this delay",
    )
    parser.add_argument(
        "--queue-track",
        type=int,
        choices=(2, 3),
        default=2,
        help="track selected by --queue-after (default: 2)",
    )
    parser.add_argument(
        "--transition",
        type=TransitionMode,
        choices=tuple(TransitionMode),
        default=TransitionMode.NORMAL,
        metavar="{normal,hard_cut,fade,crossfade}",
        help="timed transition mode (default: normal)",
    )
    parser.add_argument(
        "--transition-duration",
        type=float,
        default=3.0,
        metavar="SECONDS",
        help="total fade/crossfade duration (default: 3.0)",
    )
    parser.add_argument("--master-db", type=float, default=0.0)
    parser.add_argument("--blocksize", type=int, default=1024)
    parser.add_argument("--device", help="PortAudio output device name or numeric index")
    return parser


def main(argv: list[str] | None = None) -> int:
    log_runtime = configure_logging(install_qt_handler=False)
    try:
        return _run(argv)
    finally:
        log_runtime.close()


def _run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    available_paths = {
        2: args.next_audio_file,
        3: args.third_audio_file,
    }
    if args.queue_after is not None and available_paths[args.queue_track] is None:
        parser.error(f"--queue-track {args.queue_track} has no matching audio file")
    if args.queue_after is not None and args.queue_after < 0:
        parser.error("--queue-after must be >= 0")
    if args.transition_duration < 0:
        parser.error("--transition-duration must be >= 0")
    audio = load_audio(args.audio_file)
    metadata = _metadata_for(
        args.audio_file,
        audio.source_sample_rate,
        audio.sample_rate,
        audio.total_frames,
        play_start=args.play_start,
        loop_start=args.loop_start,
        loop_end=args.loop_end,
        play_end=args.play_end,
    )
    next_audio = load_audio(args.next_audio_file) if args.next_audio_file else None
    next_metadata = (
        _metadata_for(
            args.next_audio_file,
            next_audio.source_sample_rate,
            next_audio.sample_rate,
            next_audio.total_frames,
            play_start=args.next_play_start,
            loop_start=(
                args.loop_start
                if args.next_loop_start is None
                else args.next_loop_start
            ),
            loop_end=args.loop_end if args.next_loop_end is None else args.next_loop_end,
            play_end=args.next_play_end,
        )
        if args.next_audio_file is not None and next_audio is not None
        else None
    )
    third_audio = load_audio(args.third_audio_file) if args.third_audio_file else None
    third_metadata = (
        _metadata_for(
            args.third_audio_file,
            third_audio.source_sample_rate,
            third_audio.sample_rate,
            third_audio.total_frames,
            play_start=args.track_3_play_start,
            loop_start=(
                args.loop_start
                if args.track_3_loop_start is None
                else args.track_3_loop_start
            ),
            loop_end=(
                args.loop_end
                if args.track_3_loop_end is None
                else args.track_3_loop_end
            ),
            play_end=args.track_3_play_end,
        )
        if args.third_audio_file is not None and third_audio is not None
        else None
    )

    device: int | str | None = args.device
    if isinstance(device, str) and device.isdecimal():
        device = int(device)
    sink = LocalAudioSink(blocksize=args.blocksize, device=device)
    engine = AudioEngine(sink)
    engine.set_master_volume_db(args.master_db)
    engine.play(audio, metadata)
    stopped = Event()
    queue_timer: Timer | None = None
    logger = logging.getLogger("audio.engine")
    queue_candidates = {
        number: candidate
        for number, candidate in (
            (2, (next_audio, next_metadata)),
            (3, (third_audio, third_metadata)),
        )
        if candidate[0] is not None and candidate[1] is not None
    }

    def transition_track(
        number: int, mode: TransitionMode = TransitionMode.NORMAL
    ) -> None:
        if stopped.is_set() or number not in queue_candidates:
            return
        candidate_audio, candidate_metadata = queue_candidates[number]
        assert candidate_audio is not None and candidate_metadata is not None
        engine.transition_to(
            candidate_audio,
            candidate_metadata,
            mode=mode,
            duration_seconds=args.transition_duration,
        )
        logger.info(
            "requested %s to track %d: %s",
            mode.value,
            number,
            candidate_metadata.title,
        )

    def wait_for_queue_command() -> None:
        choices = "/".join(str(number) for number in queue_candidates)
        logger.info(
            "commands: 2/3=normal, c2/c3=cut, f2/f3=fade, "
            "x2/x3=crossfade, Enter=normal track 2, q=stop; tracks=%s",
            choices,
        )
        while not stopped.is_set():
            line = sys.stdin.readline()
            if not line:
                return
            command = line.strip().lower()
            if command == "q":
                stopped.set()
                return
            if command == "" and 2 in queue_candidates:
                transition_track(2)
            elif command.isdecimal() and int(command) in queue_candidates:
                transition_track(int(command))
            elif (
                len(command) == 2
                and command[0] in "cfx"
                and command[1].isdecimal()
                and int(command[1]) in queue_candidates
            ):
                mode = {
                    "c": TransitionMode.HARD_CUT,
                    "f": TransitionMode.FADE,
                    "x": TransitionMode.CROSSFADE,
                }[command[0]]
                transition_track(int(command[1]), mode)
            else:
                logger.warning("unknown transition command: %s", command or "<Enter>")

    def request_stop(_signum: int, _frame: object) -> None:
        stopped.set()

    previous_int = signal.signal(signal.SIGINT, request_stop)
    previous_term = signal.signal(signal.SIGTERM, request_stop)
    try:
        engine.start_output()
        logger.info(
            "playing %s (%d frames), loop [%d, %d); press Ctrl+C to stop",
            args.audio_file,
            audio.total_frames,
            metadata.loop_start,
            metadata.loop_end,
        )
        if queue_candidates:
            if args.queue_after is not None:
                queue_timer = Timer(
                    args.queue_after,
                    transition_track,
                    args=(args.queue_track, args.transition),
                )
                queue_timer.daemon = True
                queue_timer.start()
                logger.info(
                    "%s to track %d (%s) starts after %.3f seconds",
                    args.transition.value,
                    args.queue_track,
                    available_paths[args.queue_track],
                    args.queue_after,
                )
            else:
                Thread(
                    target=wait_for_queue_command,
                    name="cli-queue-command",
                    daemon=True,
                ).start()
        stopped.wait()
    finally:
        stopped.set()
        if queue_timer is not None:
            queue_timer.cancel()
        try:
            engine.close()
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)
    return 0


def _metadata_for(
    path: Path,
    source_sample_rate: int,
    internal_sample_rate: int,
    total_frames: int,
    *,
    play_start: int,
    loop_start: int | None,
    loop_end: int | None,
    play_end: int | None,
) -> TrackMetadata:
    resolved_play_end = total_frames if play_end is None else play_end
    resolved_loop_start = play_start if loop_start is None else loop_start
    resolved_loop_end = resolved_play_end if loop_end is None else loop_end
    metadata = TrackMetadata(
        id=path.stem,
        title=path.stem,
        filepath=str(path.resolve()),
        source_sample_rate=source_sample_rate,
        internal_sample_rate=internal_sample_rate,
        play_start=play_start,
        loop_start=resolved_loop_start,
        loop_end=resolved_loop_end,
        play_end=resolved_play_end,
    )
    metadata.validate(total_frames)
    return metadata


if __name__ == "__main__":
    raise SystemExit(main())
