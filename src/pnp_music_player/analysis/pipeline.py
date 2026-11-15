"""Fault-tolerant orchestration for non-destructive track imports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import logging
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, Callable

import soundfile as sf

from pnp_music_player.analysis.beats import (
    BeatAnalysisConfig,
    BeatAnalysisResult,
    analyze_beats,
)
from pnp_music_player.analysis.loudness import (
    LoudnessConfig,
    LoudnessResult,
    analyze_loudness,
    calculate_sample_peak_dbfs,
)
from pnp_music_player.analysis.silence import (
    SilenceDetectionConfig,
    SilenceDetectionResult,
    detect_silence_boundaries,
)
from pnp_music_player.analysis.waveform import (
    DEFAULT_BIN_SIZES,
    WaveformCacheData,
    get_or_create_waveform_cache,
)
from pnp_music_player.audio.loader import DecodedAudio, load_audio
from pnp_music_player.models.track import TrackMetadata

ANALYSIS_VERSION = 1
ProgressCallback = Callable[[str, float], None]
_LOG = logging.getLogger("analysis.pipeline")


class AnalysisCancelled(RuntimeError):
    """Raised between analysis stages when cancellation was requested."""


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    silence: SilenceDetectionConfig = SilenceDetectionConfig()
    loudness: LoudnessConfig = LoudnessConfig()
    beats: BeatAnalysisConfig = BeatAnalysisConfig()
    waveform_cache_directory: Path = Path(".cache/waveform")
    waveform_bin_sizes: tuple[int, ...] = DEFAULT_BIN_SIZES


@dataclass(slots=True)
class TrackAnalysisResult:
    source_path: Path
    source_format: str
    source_subtype: str
    source_sample_rate: int
    source_channels: int
    internal_sample_rate: int
    total_frames: int
    detected_play_start: int
    detected_play_end: int
    integrated_lufs: float | None
    sample_peak_dbfs: float
    requested_normalization_gain_db: float | None
    normalization_gain_db: float
    target_lufs: float
    sample_peak_ceiling_dbfs: float
    bpm: float | None
    beat_positions: list[int]
    beat_analysis_confidence: float | None
    tempo_is_variable: bool | None
    waveform_cache_path: Path | None
    waveform_cache_reused: bool
    analysis_version: int = ANALYSIS_VERSION
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.total_frames / self.internal_sample_rate

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["source_path"] = str(self.source_path)
        values["waveform_cache_path"] = (
            str(self.waveform_cache_path)
            if self.waveform_cache_path is not None
            else None
        )
        values["duration_seconds"] = self.duration_seconds
        return values


@dataclass(slots=True)
class ImportResult:
    audio: DecodedAudio
    metadata: TrackMetadata
    analysis: TrackAnalysisResult | None


def analyze_track(
    path: str | Path,
    *,
    existing_metadata: TrackMetadata | None = None,
    config: AnalysisConfig | None = None,
    progress_callback: ProgressCallback | None = None,
    cancel_event: Event | None = None,
    force: bool = False,
    source_sha256: str | None = None,
) -> ImportResult:
    """Decode once, run independent analysis stages and return suggestions."""
    config = config or AnalysisConfig()
    source_path = Path(path).expanduser().resolve()
    warnings: list[str] = []
    started_at = monotonic()
    _LOG.info("track analysis started file=%s force=%s", source_path.name, force)

    _progress(progress_callback, "decode", 0.0)
    _check_cancelled(cancel_event)
    source_info = sf.info(source_path)
    audio = load_audio(source_path)
    if existing_metadata is not None:
        existing_metadata.validate(audio.total_frames)
    _progress(progress_callback, "decode", 1.0)

    _check_cancelled(cancel_event)
    _progress(progress_callback, "silence", 0.0)
    try:
        silence = detect_silence_boundaries(
            audio.samples, audio.sample_rate, config.silence
        )
        warnings.extend(f"silence: {warning}" for warning in silence.warnings)
    except Exception as exc:
        silence = SilenceDetectionResult(0, audio.total_frames)
        warnings.append(f"silence analysis failed: {exc}")
    _progress(progress_callback, "silence", 1.0)

    _check_cancelled(cancel_event)
    _progress(progress_callback, "loudness", 0.0)
    try:
        loudness = analyze_loudness(
            audio.samples,
            audio.sample_rate,
            silence.play_start,
            silence.play_end,
            config.loudness,
        )
        warnings.extend(f"loudness: {warning}" for warning in loudness.warnings)
    except Exception as exc:
        region = audio.samples[silence.play_start : silence.play_end]
        loudness = LoudnessResult(
            None,
            calculate_sample_peak_dbfs(region),
            None,
            0.0,
            [str(exc)],
        )
        warnings.append(f"loudness analysis failed: {exc}")
    _progress(progress_callback, "loudness", 1.0)

    _check_cancelled(cancel_event)
    _progress(progress_callback, "beats", 0.0)
    try:
        beats = analyze_beats(audio.samples, audio.sample_rate, config.beats)
        warnings.extend(f"beats: {warning}" for warning in beats.warnings)
    except Exception as exc:
        beats = BeatAnalysisResult(None, [], warnings=[str(exc)])
        warnings.append(f"beat analysis failed: {exc}")
    _progress(progress_callback, "beats", 1.0)

    _check_cancelled(cancel_event)
    _progress(progress_callback, "waveform", 0.0)
    waveform: WaveformCacheData | None = None
    try:
        waveform = get_or_create_waveform_cache(
            source_path,
            audio.samples,
            audio.sample_rate,
            config.waveform_cache_directory,
            bin_sizes=config.waveform_bin_sizes,
            force=force,
            cancel_event=cancel_event,
        )
    except InterruptedError as exc:
        raise AnalysisCancelled(str(exc)) from exc
    except Exception as exc:
        warnings.append(f"waveform cache failed: {exc}")
    _progress(progress_callback, "waveform", 1.0)

    _check_cancelled(cancel_event)
    _progress(progress_callback, "save", 0.0)
    analysis = TrackAnalysisResult(
        source_path=source_path,
        source_format=source_info.format,
        source_subtype=source_info.subtype,
        source_sample_rate=int(source_info.samplerate),
        source_channels=int(source_info.channels),
        internal_sample_rate=audio.sample_rate,
        total_frames=audio.total_frames,
        detected_play_start=silence.play_start,
        detected_play_end=silence.play_end,
        integrated_lufs=loudness.integrated_lufs,
        sample_peak_dbfs=loudness.sample_peak_dbfs,
        requested_normalization_gain_db=loudness.requested_normalization_gain_db,
        normalization_gain_db=loudness.normalization_gain_db,
        target_lufs=config.loudness.target_lufs,
        sample_peak_ceiling_dbfs=config.loudness.sample_peak_ceiling_dbfs,
        bpm=beats.bpm,
        beat_positions=beats.beat_positions,
        beat_analysis_confidence=beats.confidence,
        tempo_is_variable=beats.tempo_is_variable,
        waveform_cache_path=waveform.path if waveform is not None else None,
        waveform_cache_reused=waveform.reused if waveform is not None else False,
        warnings=warnings,
    )
    metadata = _merge_metadata(source_path, audio, analysis, existing_metadata)
    if metadata.source_sha256 is None:
        metadata.source_sha256 = source_sha256
    _progress(progress_callback, "save", 1.0)
    result = ImportResult(audio, metadata, analysis)
    _LOG.info(
        "track analysis completed file=%s duration_seconds=%.3f frames=%d "
        "waveform_reused=%s warnings=%d",
        source_path.name,
        monotonic() - started_at,
        audio.total_frames,
        analysis.waveform_cache_reused,
        len(warnings),
    )
    return result


def _merge_metadata(
    source_path: Path,
    audio: DecodedAudio,
    analysis: TrackAnalysisResult,
    existing: TrackMetadata | None,
) -> TrackMetadata:
    if existing is None:
        metadata = TrackMetadata(
            id=source_path.stem or "track",
            title=source_path.stem or source_path.name,
            filepath=str(source_path),
            source_sample_rate=audio.source_sample_rate,
            internal_sample_rate=audio.sample_rate,
            play_start=analysis.detected_play_start,
            loop_start=analysis.detected_play_start,
            loop_end=analysis.detected_play_end,
            play_end=analysis.detected_play_end,
        )
    else:
        metadata = replace(
            existing,
            tags=list(existing.tags),
            main_loop_exit_points=list(existing.main_loop_exit_points),
            beat_positions=list(existing.beat_positions),
            detected_beat_positions=list(existing.detected_beat_positions),
            alternate_loops=[
                replace(region, exit_points=list(region.exit_points))
                for region in existing.alternate_loops
            ],
        )
    metadata.detected_play_start = analysis.detected_play_start
    metadata.detected_play_end = analysis.detected_play_end
    metadata.integrated_lufs = analysis.integrated_lufs
    metadata.target_lufs = analysis.target_lufs
    metadata.requested_normalization_gain_db = (
        analysis.requested_normalization_gain_db
    )
    metadata.normalization_gain_db = analysis.normalization_gain_db
    metadata.peak_dbfs = analysis.sample_peak_dbfs
    metadata.sample_peak_dbfs = analysis.sample_peak_dbfs
    metadata.detected_bpm = analysis.bpm
    metadata.detected_beat_positions = list(analysis.beat_positions)
    metadata.beat_analysis_confidence = analysis.beat_analysis_confidence
    metadata.detected_tempo_is_variable = analysis.tempo_is_variable
    metadata.analysis_version = analysis.analysis_version
    metadata.waveform_cache_path = (
        str(analysis.waveform_cache_path)
        if analysis.waveform_cache_path is not None
        else None
    )
    return metadata


def _progress(callback: ProgressCallback | None, stage: str, progress: float) -> None:
    if callback is not None:
        callback(stage, progress)


def _check_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise AnalysisCancelled("track analysis cancelled")
