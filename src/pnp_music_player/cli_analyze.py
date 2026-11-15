"""Command-line interface for the offline phase-4 analysis pipeline."""

from __future__ import annotations

import argparse
import json
from math import isfinite
from pathlib import Path
import signal
import sys
from threading import Event
from typing import Any

from pnp_music_player.analysis.loudness import LoudnessConfig
from pnp_music_player.analysis.pipeline import (
    AnalysisCancelled,
    AnalysisConfig,
    TrackAnalysisResult,
    analyze_track,
)
from pnp_music_player.logging_config import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze one local track without modifying the audio file."
    )
    parser.add_argument("audio_file", type=Path)
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--force", action="store_true", help="rebuild waveform cache")
    parser.add_argument(
        "--cache-directory", type=Path, default=Path(".cache/waveform")
    )
    parser.add_argument("--target-lufs", type=float, default=-18.0)
    parser.add_argument("--max-positive-gain", type=float, default=6.0)
    parser.add_argument("--peak-ceiling", type=float, default=-1.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    log_runtime = configure_logging(install_qt_handler=False)
    try:
        return _run(argv)
    finally:
        log_runtime.close()


def _run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cancelled = Event()

    def request_cancel(_signum: int, _frame: object) -> None:
        cancelled.set()

    previous_int = signal.signal(signal.SIGINT, request_cancel)
    previous_term = signal.signal(signal.SIGTERM, request_cancel)
    config = AnalysisConfig(
        loudness=LoudnessConfig(
            target_lufs=args.target_lufs,
            max_positive_normalization_gain_db=args.max_positive_gain,
            sample_peak_ceiling_dbfs=args.peak_ceiling,
        ),
        waveform_cache_directory=args.cache_directory,
    )
    try:
        result = analyze_track(
            args.audio_file,
            config=config,
            cancel_event=cancelled,
            force=args.force,
        )
    except AnalysisCancelled as exc:
        print(str(exc), file=sys.stderr)
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"analysis failed: {exc}", file=sys.stderr)
        return 2
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)

    if args.json:
        print(json.dumps(_json_safe(result.analysis.to_dict()), indent=2, allow_nan=False))
    else:
        print(_human_report(result.analysis))
    return 0


def _human_report(result: TrackAnalysisResult) -> str:
    lines = [
        f"File: {result.source_path}",
        (
            f"Source format: {result.source_format}/{result.source_subtype}, "
            f"{result.source_sample_rate} Hz, {result.source_channels} channel(s)"
        ),
        f"Internal format: {result.internal_sample_rate} Hz / stereo / float32",
        f"Frames: {result.total_frames:,}",
        f"Duration: {result.duration_seconds:.2f} s",
        "",
        f"Detected play start: {result.detected_play_start:,} frames",
        f"Detected play end: {result.detected_play_end:,} frames",
        "",
        f"Integrated loudness: {_format_number(result.integrated_lufs, ' LUFS')}",
        f"Sample peak: {_format_number(result.sample_peak_dbfs, ' dBFS')}",
        (
            "Requested normalization: "
            f"{_format_signed(result.requested_normalization_gain_db, ' dB')}"
        ),
        f"Applied normalization: {_format_signed(result.normalization_gain_db, ' dB')}",
        f"Target loudness: {result.target_lufs:g} LUFS",
        f"Peak ceiling: {result.sample_peak_ceiling_dbfs:g} dBFS",
        "",
        f"Estimated BPM: {_format_number(result.bpm)}",
        f"Detected beats: {len(result.beat_positions)}",
        f"Tempo variable hint: {_format_bool(result.tempo_is_variable)}",
        "",
        f"Waveform cache: {result.waveform_cache_path or 'unavailable'}",
        f"Waveform cache reused: {str(result.waveform_cache_reused).lower()}",
    ]
    if result.warnings:
        lines.extend(("", "Warnings:"))
        lines.extend(f"- {warning}" for warning in result.warnings)
    return "\n".join(lines)


def _format_number(value: float | None, suffix: str = "") -> str:
    if value is None:
        return "unknown"
    if value == float("-inf"):
        return f"-inf{suffix}"
    return f"{value:.2f}{suffix}"


def _format_signed(value: float | None, suffix: str) -> str:
    return "unknown" if value is None else f"{value:+.2f}{suffix}"


def _format_bool(value: bool | None) -> str:
    return "unknown" if value is None else str(value).lower()


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
