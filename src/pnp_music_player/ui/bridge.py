"""Qt-facing bridge for the setup controller."""

from __future__ import annotations

import os
import json
from pathlib import Path
from threading import Event
import logging
import sys

from PySide6.QtCore import QObject, Property, QThread, QTimer, QUrl, Signal, Slot

from pnp_music_player.analysis.pipeline import (
    ANALYSIS_VERSION,
    AnalysisCancelled,
    AnalysisConfig,
    ImportResult,
    analyze_track,
)
from pnp_music_player.analysis.waveform import load_waveform_cache
from pnp_music_player.audio.loader import INTERNAL_SAMPLE_RATE, load_audio
from pnp_music_player.audio.source_hash import calculate_source_sha256
from pnp_music_player.models.track import TrackMetadata, TrackValidationError
from pnp_music_player.ui.archive import ArchiveStore
from pnp_music_player.ui.audio_runtime import AudioRuntime
from pnp_music_player.ui.models import PreviewMode, format_time
from pnp_music_player.ui.metadata_exchange import (
    export_plan, inspect_import, write_exports,
)
from pnp_music_player.ui.persistence import (
    MetadataReadError,
    MetadataStore,
    UnsupportedMetadataVersion,
)
from pnp_music_player.ui.relink import MetadataRelinker, RelinkRequest
from pnp_music_player.ui.setup_controller import SetupController
from pnp_music_player.ui.setup_controller import copy_metadata
from pnp_music_player.ui.waveform_provider import WaveformProvider
from pnp_music_player.ui.worker_thread import WorkerThread

_LOG = logging.getLogger("ui.setup")


def _configure_analysis_thread(thread: QThread, platform_name: str) -> None:
    # macOS gives auxiliary threads a small default stack. Numba/LLVM can
    # exhaust it while compiling librosa's beat analysis (not while decoding).
    if platform_name == "darwin":
        thread.setStackSize(16 * 1024 * 1024)


class AnalysisWorker(QObject):
    progress = Signal(str, float)
    succeeded = Signal(object)
    failed = Signal(str)
    invalidMetadata = Signal(str)
    finished = Signal()

    def __init__(
        self,
        path: Path,
        existing: TrackMetadata | None,
        cache_directory: Path,
        source_sha256: str | None = None,
        force_reanalysis: bool = False,
    ) -> None:
        super().__init__()
        self.path = path
        self.existing = existing
        self.cache_directory = cache_directory
        self.source_sha256 = source_sha256
        self.force_reanalysis = force_reanalysis
        self.cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            if self.cancel_event.is_set():
                return
            if (
                not self.force_reanalysis
                and self.existing is not None
                and self.existing.analysis_version == ANALYSIS_VERSION
                and self.existing.waveform_cache_path
                and load_waveform_cache(
                    self.existing.waveform_cache_path,
                    self.path,
                    INTERNAL_SAMPLE_RATE,
                ) is not None
            ):
                self.progress.emit("decode", 0.0)
                audio = load_audio(self.path)
                self.existing.validate(audio.total_frames)
                self.progress.emit("decode", 1.0)
                for stage in ("silence", "loudness", "beats", "waveform", "save"):
                    self.progress.emit(stage, 1.0)
                if not self.cancel_event.is_set():
                    self.succeeded.emit(ImportResult(audio, self.existing, None))
                return
            result = analyze_track(
                self.path,
                existing_metadata=self.existing,
                config=AnalysisConfig(waveform_cache_directory=self.cache_directory),
                progress_callback=lambda stage, value: self.progress.emit(stage, value),
                cancel_event=self.cancel_event,
                source_sha256=self.source_sha256,
                force=self.force_reanalysis,
            )
            result.metadata.validate(result.audio.total_frames)
            if not self.cancel_event.is_set():
                self.succeeded.emit(result)
        except AnalysisCancelled:
            pass
        except TrackValidationError as exc:
            if not self.cancel_event.is_set():
                if self.existing is not None:
                    self.invalidMetadata.emit(str(exc))
                else:
                    self.failed.emit(str(exc))
        except Exception as exc:
            if not self.cancel_event.is_set():
                _LOG.exception("track import failed: %s", self.path)
                self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self.cancel_event.set()


class SourceHashWorker(QObject):
    progress = Signal(float)
    succeeded = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path
        self.cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            digest = calculate_source_sha256(
                self.path,
                progress_callback=self.progress.emit,
                cancel_event=self.cancel_event,
            )
            if not self.cancel_event.is_set():
                self.succeeded.emit(digest)
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.cancel_event.is_set():
                _LOG.exception("source hash calculation failed: %s", self.path)
                self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    @Slot()
    def cancel(self) -> None:
        self.cancel_event.set()


class SetupBridge(QObject):
    changed = Signal()
    playheadChanged = Signal()
    viewportChanged = Signal()
    waveformChanged = Signal()
    analysisProgress = Signal(str, float)
    archiveChanged = Signal()
    sourceHashMismatch = Signal()
    metadataProblem = Signal()
    relinkCompleted = Signal()
    relinkFailed = Signal()

    def __init__(
        self,
        metadata_directory: Path,
        cache_directory: Path,
        music_directory: Path,
        archive_metadata_directory: Path,
        archive_cache_directory: Path,
        runtime: AudioRuntime | None = None,
        export_directory: Path | None = None,
    ) -> None:
        super().__init__()
        self._owns_runtime = runtime is None
        self.runtime = runtime or AudioRuntime()
        self._engine = self.runtime.engine
        active_store = MetadataStore(
            metadata_directory,
            music_directory=music_directory,
            waveform_cache_directory=cache_directory,
        )
        archived_store = MetadataStore(
            archive_metadata_directory,
            music_directory=music_directory,
            waveform_cache_directory=archive_cache_directory,
        )
        self.archive_store = ArchiveStore(
            active_store,
            archived_store,
            music_directory=music_directory,
            active_waveform_directory=cache_directory,
            archive_waveform_directory=archive_cache_directory,
        )
        self.relinker = MetadataRelinker(active_store, archived_store)
        self._relink_request: RelinkRequest | None = None
        self._relink_temporary = False
        self.controller = SetupController(
            active_store,
            engine=self._engine,
            cache_directory=cache_directory,
        )
        self._waveform: WaveformProvider | None = None
        self._music_directory_url = QUrl.fromLocalFile(
            str(music_directory.resolve())
        ).toString()
        self._export_directory = (
            Path(export_directory).expanduser().resolve()
            if export_directory is not None
            else metadata_directory.resolve().parent.parent / "exports"
        )
        self._visible_start = 0
        self._visible_end = 1
        self._busy = False
        self._closing = False
        self._progress = 0.0
        self._stage = ""
        self._error = ""
        self._thread: QThread | None = None
        self._worker: AnalysisWorker | SourceHashWorker | None = None
        self._playhead = 0
        self._reanalyzing = False
        self._analysis_is_reanalysis = False
        self._analysis_saved_metadata: TrackMetadata | None = None
        self._analysis_source: Path | None = None
        self._recovery_source: Path | None = None
        self._recovery_document: Path | None = None
        self._recovery_reason = ""
        self._recovery_archive = False
        self._recovery_unsupported = False
        self._hash_path: Path | None = None
        self._hash_existing: TrackMetadata | None = None
        self._hash_result = ""
        self._hash_failed = False
        self._hash_expected = ""
        self._hash_actual = ""
        self._setup_audio_path: str | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._poll_playhead)
        self._timer.start()

    @property
    def worker_running(self) -> bool:
        # The finished signal must be handled on the UI thread before QML
        # teardown; isRunning() can turn false before that queued slot runs.
        return self._thread is not None

    def begin_shutdown(self) -> None:
        self._closing = True
        self._timer.stop()
        if self._worker is not None:
            self._worker.cancel()

    def close(self) -> None:
        self.begin_shutdown()
        if self._thread is not None:
            # Normal window closing waits asynchronously. This is the safety
            # net for app.quit() or teardown without a running UI event loop.
            self._thread.wait()
        if self._setup_audio_path is not None:
            self.runtime.audio_cache.unpin(self._setup_audio_path)
            self._setup_audio_path = None
            self.runtime.audio_cache.trim(
                protected_paths=self.runtime.protected_audio_paths()
            )
        if self._owns_runtime:
            try:
                self.runtime.close()
            except Exception:
                pass

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, constant=True)
    def musicDirectoryUrl(self) -> str:
        return self._music_directory_url

    @Property(str, constant=True)
    def exportDirectoryUrl(self) -> str:
        return QUrl.fromLocalFile(str(self._export_directory)).toString()

    @Slot(str, result=str)
    def localPathText(self, value: str) -> str:
        return str(self._path_from_value(value))

    @Slot(result=bool)
    def ensureExportDirectory(self) -> bool:
        try:
            self._export_directory.mkdir(parents=True, exist_ok=True)
            self._error = ""
            self.changed.emit()
            return True
        except OSError as exc:
            self._error = f"Default export folder is not writable: {exc}. Choose another folder."
            self.changed.emit()
            return False

    @Slot(result="QVariantList")
    def exportCandidates(self) -> list[dict[str, object]]:
        tracks, _problems = self.controller.store.scan_all()
        return [
            {
                "source": item.filepath,
                "artist": item.artist,
                "title": item.title,
                "fileName": Path(item.filepath).name,
                "hasHash": bool(item.source_sha256),
            }
            for item in sorted(tracks, key=lambda track: (
                track.artist.casefold(), track.title.casefold(), track.filepath.casefold()
            ))
        ]

    def _selected_export_metadata(self, sources_json: str) -> list[TrackMetadata]:
        sources = json.loads(sources_json)
        if not isinstance(sources, list) or not sources or not all(
            isinstance(value, str) for value in sources
        ):
            raise ValueError("Select at least one track to export")
        tracks, _problems = self.controller.store.scan_all()
        by_source = {track.filepath: track for track in tracks}
        if len(set(sources)) != len(sources) or any(source not in by_source for source in sources):
            raise ValueError("The export selection is no longer available; refresh the list")
        return [by_source[source] for source in sources]

    @Slot(str, str, result="QVariantList")
    def previewExport(self, sources_json: str, directory_url: str) -> list[dict[str, object]]:
        try:
            selected = self._selected_export_metadata(sources_json)
            directory = self._path_from_value(directory_url)
            if directory.exists() and not directory.is_dir():
                raise ValueError("The export destination is not a directory")
            plan = export_plan(selected, directory)
            self._error = ""
            self.changed.emit()
            return plan
        except Exception as exc:
            self._error = f"Could not prepare export: {exc}"
            self.changed.emit()
            return []

    @Slot(str, str, str, result=str)
    def exportSelected(self, sources_json: str, directory_url: str, decisions_json: str) -> str:
        try:
            selected = self._selected_export_metadata(sources_json)
            decisions = json.loads(decisions_json)
            if not isinstance(decisions, dict) or any(
                value not in ("skip", "replace") for value in decisions.values()
            ):
                raise ValueError("Invalid overwrite decisions")
            directory = self._path_from_value(directory_url)
            written, skipped = write_exports(selected, directory, decisions)
            self._error = ""
            self.changed.emit()
            return f"Exported {written} JSON file(s) to {directory}. Skipped {skipped}."
        except Exception as exc:
            self._error = f"Could not export metadata: {exc}"
            self.changed.emit()
            return ""

    @Slot(str, result="QVariantList")
    def previewImports(self, documents_json: str) -> list[dict[str, object]]:
        try:
            values = json.loads(documents_json)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) for value in values
            ):
                raise ValueError("Select one or more metadata JSON files")
            music = self._path_from_value(self._music_directory_url)
            result = [inspect_import(self._path_from_value(value), music) for value in values]
            for item in result:
                suggested = str(item["suggestedAudio"])
                item["targetExists"] = bool(
                    suggested and self.controller.store.path_for(suggested).exists()
                )
            self._error = ""
            self.changed.emit()
            return result
        except Exception as exc:
            self._error = f"Could not inspect metadata: {exc}"
            self.changed.emit()
            return []

    @Slot(str, result=bool)
    def hasActiveMetadata(self, audio_url: str) -> bool:
        return self.controller.store.path_for(self._path_from_value(audio_url)).exists()

    @Property(float, notify=changed)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=changed)
    def progressStage(self) -> str:
        labels = {
            "decode": "Decoding",
            "silence": "Silence Detection",
            "loudness": "Loudness Analysis",
            "beats": "Beat Analysis",
            "waveform": "Waveform",
            "save": "Saving",
            "hash": "Verifying File",
        }
        return labels.get(self._stage, self._stage)

    @Property(str, notify=changed)
    def errorMessage(self) -> str:
        return self._error

    @Property(str, notify=changed)
    def sourceHashMismatchFileName(self) -> str:
        return self._hash_path.name if self._hash_path is not None else ""

    @Property(str, notify=changed)
    def sourceHashExpected(self) -> str:
        return self._hash_expected

    @Property(str, notify=changed)
    def sourceHashActual(self) -> str:
        return self._hash_actual

    @Property(str, notify=changed)
    def metadataProblemPath(self) -> str:
        return str(self._recovery_document or "")

    @Property(str, notify=changed)
    def metadataProblemReason(self) -> str:
        return self._recovery_reason

    @Property(bool, notify=changed)
    def metadataProblemIsUnsupported(self) -> bool:
        return self._recovery_unsupported

    @Property(bool, notify=changed)
    def hasTrack(self) -> bool:
        return self.controller.metadata is not None

    @Property(str, notify=changed)
    def currentSource(self) -> str:
        return self.controller.metadata.filepath if self.controller.metadata else ""

    @Property(bool, notify=changed)
    def temporaryRelink(self) -> bool:
        return self._relink_temporary

    @Property(bool, notify=changed)
    def relinking(self) -> bool:
        return self._relink_request is not None

    @Property(bool, notify=changed)
    def dirty(self) -> bool:
        return self.controller.dirty

    @Property("QVariantList", notify=archiveChanged)
    def archivedTracks(self) -> list[dict[str, object]]:
        return [
            {
                "title": item.metadata.title,
                "artist": item.metadata.artist,
                "album": item.metadata.album,
                "fileName": Path(item.metadata.filepath).name,
                "relativePath": item.relative_media_path,
                "sourceUrl": QUrl.fromLocalFile(item.metadata.filepath).toString(),
                "documentUrl": QUrl.fromLocalFile(
                    str(self.archive_store.archive_store.path_for(item.metadata.filepath))
                ).toString(),
                "mediaAvailable": item.media_available,
            }
            for item in self.archive_store.all_tracks()
        ]

    @Property(int, notify=changed)
    def totalFrames(self) -> int:
        return self.controller.total_frames

    @Property(int, notify=viewportChanged)
    def visibleStart(self) -> int:
        return self._visible_start

    @Property(int, notify=viewportChanged)
    def visibleEnd(self) -> int:
        return self._visible_end

    @Property(int, notify=playheadChanged)
    def playheadFrame(self) -> int:
        return self._playhead

    @Property(int, notify=changed)
    def cursorFrame(self) -> int:
        return self.controller.cursor_frame

    @Property(str, notify=changed)
    def previewMode(self) -> str:
        return self.controller.preview_mode.value

    @Property(bool, notify=changed)
    def previewAvailable(self) -> bool:
        snapshot = self._engine.snapshot()
        return not snapshot.playing or self.controller.preview_mode.value != "stopped"

    def _metadata_value(self, name: str, default: object = "") -> object:
        metadata = self.controller.metadata
        return getattr(metadata, name) if metadata is not None else default

    title = Property(str, lambda self: str(self._metadata_value("title")), notify=changed)
    displayName = Property(
        str, lambda self: str(self._metadata_value("title")), notify=changed
    )
    fileName = Property(
        str,
        lambda self: Path(str(self._metadata_value("filepath"))).name
        if self.hasTrack else "",
        notify=changed,
    )
    artist = Property(str, lambda self: str(self._metadata_value("artist")), notify=changed)
    album = Property(str, lambda self: str(self._metadata_value("album")), notify=changed)
    attribution = Property(str, lambda self: str(self._metadata_value("attribution")), notify=changed)
    category = Property(str, lambda self: str(self._metadata_value("category")), notify=changed)
    tags = Property(str, lambda self: ", ".join(self._metadata_value("tags", [])), notify=changed)
    playStart = Property(int, lambda self: int(self._metadata_value("play_start", 0)), notify=changed)
    introStart = Property(int, lambda self: int(self.controller.metadata.effective_intro_start if self.controller.metadata else 0), notify=changed)
    introEnd = Property(int, lambda self: int(self.controller.metadata.effective_intro_end if self.controller.metadata else 0), notify=changed)
    loopStart = Property(int, lambda self: int(self._metadata_value("loop_start", 0)), notify=changed)
    loopEnd = Property(int, lambda self: int(self._metadata_value("loop_end", 0)), notify=changed)
    outroStart = Property(int, lambda self: int(self.controller.metadata.effective_outro_start if self.controller.metadata else 0), notify=changed)
    outroEnd = Property(int, lambda self: int(self.controller.metadata.effective_outro_end if self.controller.metadata else 0), notify=changed)
    playEnd = Property(int, lambda self: int(self._metadata_value("play_end", 0)), notify=changed)
    detectedPlayStart = Property(int, lambda self: int(self._metadata_value("detected_play_start", 0) or 0), notify=changed)
    detectedPlayEnd = Property(int, lambda self: int(self._metadata_value("detected_play_end", 0) or 0), notify=changed)
    bpm = Property(float, lambda self: float(self._metadata_value("bpm", 0.0) or self._metadata_value("detected_bpm", 0.0) or 0.0), notify=changed)
    detectedBpm = Property(float, lambda self: float(self._metadata_value("detected_bpm", 0.0) or 0.0), notify=changed)
    beatsPerBar = Property(int, lambda self: int(self._metadata_value("beats_per_bar", 4)), notify=changed)
    beatUnit = Property(int, lambda self: int(self._metadata_value("beat_unit", 4)), notify=changed)
    downbeatOffset = Property(int, lambda self: int(self._metadata_value("downbeat_offset", 0)), notify=changed)
    beatGridOffset = Property(int, lambda self: int(self._metadata_value("beat_grid_offset_frames", 0)), notify=changed)
    integratedLufs = Property(float, lambda self: float(self._metadata_value("integrated_lufs", 0.0) or 0.0), notify=changed)
    samplePeak = Property(float, lambda self: float(self._metadata_value("sample_peak_dbfs", 0.0) or 0.0), notify=changed)
    targetLufs = Property(float, lambda self: float(self._metadata_value("target_lufs", -18.0)), notify=changed)
    normalizationGain = Property(float, lambda self: float(self._metadata_value("normalization_gain_db", 0.0)), notify=changed)
    loudnessOffset = Property(float, lambda self: float(self._metadata_value("loudness_offset_db", 0.0)), notify=changed)
    normalizationEnabled = Property(bool, lambda self: bool(self._metadata_value("normalization_enabled", True)), notify=changed)
    snapMode = Property(str, lambda self: self.controller.snap_mode.value, notify=changed)
    hasBeatGrid = Property(bool, lambda self: bool(self.controller.metadata and self.controller.effective_beats()), notify=changed)

    @Property("QVariantList", notify=changed)
    def loopRegions(self) -> list[dict[str, object]]:
        if not self.hasTrack:
            return []
        return [
            {
                "id": region.id,
                "name": region.name,
                "start": region.start,
                "end": region.end,
                "main": region.id == "main",
            }
            for region in self.controller.loop_regions()
        ]

    @Property(str, notify=changed)
    def selectedLoopId(self) -> str:
        return self.controller.selected_loop_id

    @Property(int, notify=changed)
    def selectedLoopStart(self) -> int:
        if not self.hasTrack:
            return 0
        return self.controller._loop_region(self.controller.selected_loop_id).start

    @Property(int, notify=changed)
    def selectedLoopEnd(self) -> int:
        if not self.hasTrack:
            return 0
        return self.controller._loop_region(self.controller.selected_loop_id).end

    @Property(str, notify=changed)
    def selectedLoopName(self) -> str:
        if not self.hasTrack:
            return ""
        return self.controller._loop_region(self.controller.selected_loop_id).name

    @Property("QVariantList", notify=changed)
    def selectedLoopExitPoints(self) -> list[int]:
        if not self.hasTrack:
            return []
        return self.controller.selected_loop_exit_points()

    @Slot(str)
    def openFile(self, value: str) -> None:
        if self._closing or self._busy:
            return
        self._relink_temporary = False
        url = QUrl(value)
        path = Path(url.toLocalFile() if url.isLocalFile() else value).expanduser().resolve()
        self._error = ""
        self._recovery_source = None
        self._recovery_document = None
        self._recovery_reason = ""
        self._recovery_archive = False
        self._recovery_unsupported = False
        try:
            existing = self.controller.existing_metadata(path)
        except MetadataReadError as exc:
            self._recovery_source = path
            self._recovery_document = exc.path
            self._recovery_reason = exc.reason
            self._recovery_unsupported = isinstance(exc, UnsupportedMetadataVersion)
            _LOG.warning("metadata cannot be opened path=%s reason=%s", exc.path, exc.reason)
            self.metadataProblem.emit()
            self.changed.emit()
            return
        except Exception as exc:
            self._error = str(exc)
            self.changed.emit()
            return
        self._reanalyzing = False
        self._start_hash_check(path, existing)

    @Slot(str, str, result=bool)
    def beginRelink(self, document_url: str, audio_url: str) -> bool:
        return self._begin_relink(document_url, audio_url, replace_existing=False)

    @Slot(str, str, bool, result=bool)
    def beginImportedConfig(
        self, document_url: str, audio_url: str, replace_existing: bool
    ) -> bool:
        return self._begin_relink(document_url, audio_url, replace_existing=replace_existing)

    def _begin_relink(
        self, document_url: str, audio_url: str, *, replace_existing: bool
    ) -> bool:
        if self._closing or self._busy:
            return False
        try:
            request = self.relinker.prepare(
                self._path_from_value(document_url),
                self._path_from_value(audio_url),
                replace_existing=replace_existing,
            )
        except Exception as exc:
            self._error = f"Could not relink track: {exc}"
            self.changed.emit()
            self.relinkFailed.emit()
            return False
        self._relink_request = request
        self._relink_temporary = False
        self._error = ""
        self._reanalyzing = False
        self._start_hash_check(request.source, request.metadata)
        return True

    @Slot(str, result=bool)
    def resolveMetadataProblem(self, decision: str) -> bool:
        path = self._recovery_source
        if path is None:
            return False
        if decision == "dont_open":
            self._recovery_source = None
            self._recovery_document = None
            self._recovery_reason = ""
            self._recovery_archive = False
            self._recovery_unsupported = False
            self.changed.emit()
            return True
        if decision == "start_fresh":
            self._reanalyzing = False
            self._start_hash_check(path, None)
            return True
        return False

    @Slot(str, result=bool)
    def hasArchivedConfiguration(self, value: str) -> bool:
        path = self._path_from_value(value)
        if path != self._recovery_source:
            self._recovery_source = None
            self._recovery_document = None
            self._recovery_reason = ""
            self._recovery_archive = False
            self._recovery_unsupported = False
        try:
            return (
                self.controller.existing_metadata(path) is None
                and self.archive_store.contains(path)
            )
        except MetadataReadError as exc:
            self._recovery_source = path
            self._recovery_document = exc.path
            self._recovery_reason = exc.reason
            self._recovery_archive = (
                exc.path.parent == self.archive_store.archive_store.directory
            )
            self._recovery_unsupported = isinstance(exc, UnsupportedMetadataVersion)
            _LOG.warning("metadata cannot be opened path=%s reason=%s", exc.path, exc.reason)
            self.metadataProblem.emit()
            self.changed.emit()
            return False
        except Exception as exc:
            self._error = str(exc)
            self.changed.emit()
            return False

    @Slot(result=bool)
    def archiveCurrent(self) -> bool:
        metadata = self.controller.metadata
        if self._busy or metadata is None:
            return False
        if self.controller.dirty:
            self._error = "Save changes before archiving this track."
            self.changed.emit()
            return False
        source = Path(metadata.filepath).expanduser().resolve()
        setup_preview_active = (
            self.controller.preview_mode is not PreviewMode.STOPPED
        )
        if not setup_preview_active:
            protected = {
                Path(path).expanduser().resolve()
                for path in self.runtime.protected_audio_paths()
            }
            if source in protected:
                self._error = (
                    "Stop playback and remove the track from NEXT before archiving it."
                )
                self.changed.emit()
                return False
        try:
            if setup_preview_active:
                self.controller.stop()
            self.archive_store.archive(copy_metadata(metadata))
            self._release_setup_track(source)
            self._error = ""
            _LOG.info("track archived file=%s", source.name)
        except Exception as exc:
            _LOG.exception("could not archive track file=%s", source.name)
            self._error = str(exc)
            self.changed.emit()
            return False
        self.archiveChanged.emit()
        self.viewportChanged.emit()
        self.waveformChanged.emit()
        self.playheadChanged.emit()
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def restoreArchived(self, value: str) -> bool:
        path = self._path_from_value(value)
        try:
            self.archive_store.restore(path)
            self._error = ""
            _LOG.info("track restored from archive file=%s", path.name)
        except Exception as exc:
            _LOG.exception("could not restore archived track file=%s", path.name)
            self._error = str(exc)
            self.changed.emit()
            return False
        self.archiveChanged.emit()
        self.changed.emit()
        return True

    @Slot()
    def refreshArchive(self) -> None:
        self.archiveChanged.emit()

    def _release_setup_track(self, source: Path) -> None:
        if self._setup_audio_path is not None:
            self.runtime.audio_cache.unpin(self._setup_audio_path)
        self.runtime.audio_cache.remove(source)
        self._setup_audio_path = None
        self.controller.clear()
        self._waveform = None
        self._visible_start = 0
        self._visible_end = 1
        self._playhead = 0

    @staticmethod
    def _path_from_value(value: str) -> Path:
        url = QUrl(value)
        raw = url.toLocalFile() if url.isLocalFile() else value
        return Path(raw).expanduser().resolve()

    def _start_hash_check(
        self, path: Path, existing: TrackMetadata | None
    ) -> None:
        if self._closing:
            return
        self._busy = True
        self._progress = 0.0
        self._stage = "hash"
        self._hash_path = path
        self._hash_existing = existing
        self._hash_result = ""
        self._hash_failed = False
        self._hash_expected = ""
        self._hash_actual = ""
        worker = SourceHashWorker(path)
        thread = WorkerThread(worker, self)
        worker.progress.connect(self._on_hash_progress)
        worker.succeeded.connect(self._on_hash_result)
        worker.failed.connect(self._on_hash_error)
        thread.finished.connect(self._hash_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()
        self.changed.emit()

    @Slot(float)
    def _on_hash_progress(self, value: float) -> None:
        if self._closing:
            return
        self._progress = min(1.0, max(0.0, float(value)))
        self.changed.emit()

    @Slot(str)
    def _on_hash_result(self, digest: str) -> None:
        if self._closing:
            return
        self._hash_result = digest

    @Slot(str)
    def _on_hash_error(self, message: str) -> None:
        if self._closing:
            return
        self._hash_failed = True
        was_relinking = self._relink_request is not None
        self._relink_request = None
        self._error = f"Could not verify audio file: {message}"
        self.changed.emit()
        if was_relinking:
            self.relinkFailed.emit()

    @Slot()
    def _hash_finished(self) -> None:
        self._busy = False
        self._thread = None
        self._worker = None
        if self._closing:
            self._clear_hash_check()
            return
        if not self._hash_failed and self._hash_result and self._hash_path is not None:
            self._complete_hash_check(
                self._hash_path, self._hash_existing, self._hash_result
            )
            return
        self._clear_hash_check()
        self.changed.emit()

    def _complete_hash_check(
        self,
        path: Path,
        existing: TrackMetadata | None,
        actual_hash: str,
    ) -> None:
        expected_hash = (existing.source_sha256 or "") if existing else ""
        if existing is not None and not expected_hash:
            if self._relink_request is not None:
                existing.source_sha256 = actual_hash
                self._open_after_hash_check(path, existing, actual_hash)
                return
            try:
                existing.source_sha256 = actual_hash
                self.controller.store.save(existing)
                _LOG.info("source hash added to metadata file=%s", path.name)
            except Exception as exc:
                _LOG.exception("could not add source hash file=%s", path.name)
                self._error = f"Could not save source hash: {exc}"
                self._clear_hash_check()
                self.changed.emit()
                return
        elif existing is not None and expected_hash != actual_hash:
            self._hash_path = path
            self._hash_existing = existing
            self._hash_expected = expected_hash
            self._hash_actual = actual_hash
            self._hash_result = actual_hash
            _LOG.warning("source hash mismatch file=%s", path.name)
            self.sourceHashMismatch.emit()
            self.changed.emit()
            return
        self._open_after_hash_check(path, existing, actual_hash)

    @Slot(str, result=bool)
    def resolveSourceHashMismatch(self, decision: str) -> bool:
        path = self._hash_path
        existing = self._hash_existing
        actual_hash = self._hash_actual
        if path is None or existing is None or not actual_hash:
            return False
        if decision == "dont_open":
            _LOG.info("source hash mismatch rejected file=%s", path.name)
            was_relinking = self._relink_request is not None
            self._relink_request = None
            self._relink_temporary = False
            self._clear_hash_check()
            self.changed.emit()
            if was_relinking:
                self.relinkFailed.emit()
            return True
        if decision == "open_once":
            _LOG.info("source hash mismatch accepted once file=%s", path.name)
            if self._relink_request is not None:
                self._relink_temporary = True
            self._open_after_hash_check(
                path, existing, actual_hash, force_reanalysis=True
            )
            return True
        if decision == "overwrite":
            if self._relink_request is not None:
                existing.source_sha256 = actual_hash
                self._open_after_hash_check(
                    path, existing, actual_hash, force_reanalysis=True
                )
                return True
            try:
                existing.source_sha256 = actual_hash
                self.controller.store.save(existing)
                _LOG.info("source hash overwritten file=%s", path.name)
            except Exception as exc:
                _LOG.exception("could not overwrite source hash file=%s", path.name)
                self._error = f"Could not overwrite source hash: {exc}"
                self.changed.emit()
                return False
            self._open_after_hash_check(
                path, existing, actual_hash, force_reanalysis=True
            )
            return True
        self._error = f"Unknown source hash decision: {decision}"
        self.changed.emit()
        return False

    def _open_after_hash_check(
        self,
        path: Path,
        existing: TrackMetadata | None,
        actual_hash: str,
        *,
        force_reanalysis: bool = False,
    ) -> None:
        self._clear_hash_check()
        self._start_analysis(
            path,
            existing,
            source_sha256=actual_hash,
            force_reanalysis=force_reanalysis,
        )

    def _clear_hash_check(self) -> None:
        self._hash_path = None
        self._hash_existing = None
        self._hash_result = ""
        self._hash_failed = False
        self._hash_expected = ""
        self._hash_actual = ""

    def _start_analysis(
        self,
        path: Path,
        existing: TrackMetadata | None,
        *,
        source_sha256: str | None = None,
        force_reanalysis: bool = False,
    ) -> None:
        if self._closing:
            return
        self._analysis_source = path
        self._analysis_is_reanalysis = self._reanalyzing
        if not self._reanalyzing:
            self._analysis_saved_metadata = (
                copy_metadata(existing) if existing is not None else None
            )
        self._busy = True
        self._progress = 0.0
        self._stage = "decode"
        worker = AnalysisWorker(
            path,
            existing,
            self.controller.cache_directory,
            source_sha256,
            force_reanalysis,
        )
        thread = WorkerThread(worker, self)
        _configure_analysis_thread(thread, sys.platform)
        worker.progress.connect(self._on_progress)
        worker.succeeded.connect(self._on_import)
        worker.invalidMetadata.connect(self._on_invalid_metadata)
        worker.failed.connect(self._on_error)
        thread.finished.connect(self._analysis_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()
        self.changed.emit()

    @Slot()
    def reanalyze(self) -> None:
        if self._busy or self.controller.metadata is None:
            return
        self._reanalyzing = True
        existing = copy_metadata(self.controller.metadata)
        self._start_analysis(Path(existing.filepath), existing)

    @Slot(str, float)
    def _on_progress(self, stage: str, value: float) -> None:
        if self._closing:
            return
        stages = {"decode": 0, "silence": 1, "loudness": 2, "beats": 3, "waveform": 4, "save": 5}
        self._stage = stage
        self._progress = (stages.get(stage, 0) + value) / 6.0
        self.analysisProgress.emit(stage, value)
        self.changed.emit()

    @Slot(object)
    def _on_import(self, result: ImportResult) -> None:
        if self._closing:
            return
        relink = self._relink_request
        if relink is not None and not self._relink_temporary:
            try:
                self.relinker.commit(relink, result.metadata)
            except Exception as exc:
                self._error = f"Could not relink track: {exc}"
                self._relink_request = None
                self._relink_temporary = False
                self.changed.emit()
                self.relinkFailed.emit()
                return
        self.controller.accept_import(
            result,
            saved_metadata=(result.metadata if relink is not None
                            else self._analysis_saved_metadata),
            preserve_saved=self._analysis_is_reanalysis,
        )
        if relink is not None:
            self._relink_request = None
            if not self._relink_temporary:
                self.archiveChanged.emit()
                self.relinkCompleted.emit()
            else:
                self.relinkFailed.emit()
        if self._setup_audio_path is not None:
            self.runtime.audio_cache.unpin(self._setup_audio_path)
        source_path = result.metadata.filepath
        self.runtime.audio_cache.put(
            source_path,
            result.audio,
            protected_paths=(
                *self.runtime.protected_audio_paths(),
                source_path,
            ),
        )
        self.runtime.audio_cache.pin(source_path)
        self._setup_audio_path = source_path
        self._visible_start = 0
        self._visible_end = result.audio.total_frames
        cache_path = result.metadata.waveform_cache_path
        self._waveform = (
            WaveformProvider.open(cache_path, result.metadata.filepath, result.audio.sample_rate)
            if cache_path else None
        )
        self._playhead = result.metadata.play_start
        self.viewportChanged.emit()
        self.waveformChanged.emit()
        self.playheadChanged.emit()
        self.changed.emit()

    @Slot(str)
    def _on_error(self, message: str) -> None:
        if self._closing:
            return
        was_relinking = self._relink_request is not None
        self._relink_request = None
        self._relink_temporary = False
        self._error = message
        self.changed.emit()
        if was_relinking:
            self.relinkFailed.emit()

    @Slot(str)
    def _on_invalid_metadata(self, message: str) -> None:
        if self._closing:
            return
        if self._relink_request is not None:
            self._relink_request = None
            self._relink_temporary = False
            self._error = "Selected audio does not fit the saved markers: " + message
            self.changed.emit()
            self.relinkFailed.emit()
            return
        if self._analysis_is_reanalysis or self._analysis_saved_metadata is None or self._analysis_source is None:
            self._on_error(message)
            return
        self._recovery_source = self._analysis_source
        self._recovery_document = self.controller.store.path_for(self._analysis_source)
        self._recovery_reason = "Metadata does not fit the decoded audio: " + message
        self._recovery_unsupported = False
        _LOG.warning(
            "metadata does not fit audio path=%s reason=%s",
            self._recovery_document, message,
        )
        self.metadataProblem.emit()
        self.changed.emit()

    @Slot()
    def _analysis_finished(self) -> None:
        self._busy = False
        self._thread = None
        self._worker = None
        self._reanalyzing = False
        if self._closing:
            return
        self._progress = 1.0 if self.hasTrack and not self._error else self._progress
        self.changed.emit()

    @Slot(str, int)
    def setMarker(self, name: str, frame: int) -> None:
        self._guard(lambda: self.controller.set_marker(name, frame))

    @Slot(str)
    def selectLoop(self, loop_id: str) -> None:
        self._guard(lambda: self.controller.select_loop(loop_id))

    @Slot()
    def addLoop(self) -> None:
        self._guard(self.controller.add_loop)

    @Slot()
    def removeSelectedLoop(self) -> None:
        self._guard(self.controller.remove_selected_loop)

    @Slot(str)
    def renameSelectedLoop(self, name: str) -> None:
        self._guard(lambda: self.controller.rename_selected_loop(name))

    @Slot(str, int)
    def setSelectedLoopBoundary(self, boundary: str, frame: int) -> None:
        self._guard(
            lambda: self.controller.set_selected_loop_boundary(boundary, frame)
        )

    @Slot(int)
    def addSelectedLoopExitPoint(self, frame: int) -> None:
        self._guard(
            lambda: self.controller.add_selected_loop_exit_point(frame)
        )

    @Slot(int)
    def removeSelectedLoopExitPoint(self, frame: int) -> None:
        self._guard(
            lambda: self.controller.remove_selected_loop_exit_point(frame)
        )

    @Slot(int, int)
    def moveSelectedLoopExitPoint(self, old_frame: int, new_frame: int) -> None:
        self._guard(
            lambda: self.controller.move_selected_loop_exit_point(
                old_frame, new_frame
            )
        )

    @Slot(int, result=int)
    def snapSelectedLoopFrame(self, frame: int) -> int:
        try:
            return self.controller.snap_selected_loop_frame(frame)
        except Exception as exc:
            self._error = str(exc)
            self.changed.emit()
            return int(frame)

    @Slot(str, str)
    def setText(self, name: str, value: str) -> None:
        self._guard(lambda: self.controller.set_text(name, value))

    @Slot(str)
    def setTags(self, value: str) -> None:
        self._guard(lambda: self.controller.set_tags(value))

    @Slot(str)
    def setSnapMode(self, value: str) -> None:
        self._guard(lambda: self.controller.set_snap_mode(value))

    @Slot(float)
    def setBpm(self, value: float) -> None:
        self._guard(lambda: self.controller.set_bpm(value), waveform=True)

    @Slot(float)
    def adjustBpm(self, delta: float) -> None:
        self._guard(lambda: self.controller.adjust_bpm(delta), waveform=True)

    @Slot()
    def resetBpm(self) -> None:
        self._guard(self.controller.reset_bpm, waveform=True)

    @Slot(int, int)
    def setTimeSignature(self, numerator: int, denominator: int) -> None:
        self._guard(
            lambda: self.controller.set_time_signature(numerator, denominator),
            waveform=True,
        )

    @Slot(int)
    def setDownbeatOffset(self, value: int) -> None:
        self._guard(lambda: self.controller.set_downbeat_offset(value), waveform=True)

    @Slot(int)
    def shiftBeatGrid(self, frames: int) -> None:
        self._guard(lambda: self.controller.shift_beat_grid(frames), waveform=True)

    @Slot(float)
    def setLoudnessOffset(self, value: float) -> None:
        self._guard(lambda: self.controller.set_loudness_offset(value))

    @Slot(bool)
    def setNormalizationEnabled(self, enabled: bool) -> None:
        self._guard(lambda: self.controller.set_normalization_enabled(enabled))

    @Slot(int)
    def setCursor(self, frame: int) -> None:
        self._guard(lambda: self.controller.set_cursor(frame))

    @Slot()
    def fit(self) -> None:
        self._visible_start, self._visible_end = 0, max(1, self.totalFrames)
        self.viewportChanged.emit()
        self.waveformChanged.emit()

    @Slot(float, int)
    def zoom(self, factor: float, anchor: int) -> None:
        if self.totalFrames <= 0 or factor <= 0:
            return
        old = self._visible_end - self._visible_start
        length = min(self.totalFrames, max(128, round(old / factor)))
        ratio = (anchor - self._visible_start) / old if old else 0.5
        start = round(anchor - ratio * length)
        start = min(self.totalFrames - length, max(0, start))
        self._visible_start, self._visible_end = start, start + length
        self.viewportChanged.emit()
        self.waveformChanged.emit()

    @Slot(int)
    def pan(self, frames: int) -> None:
        length = self._visible_end - self._visible_start
        start = min(max(0, self.totalFrames - length), max(0, self._visible_start + int(frames)))
        self._visible_start, self._visible_end = start, start + length
        self.viewportChanged.emit()
        self.waveformChanged.emit()

    @Slot(float, float, result=int)
    def xToFrame(self, x: float, width: float) -> int:
        from pnp_music_player.ui.models import x_to_frame
        return x_to_frame(x, self._visible_start, self._visible_end, width)

    @Slot(int, float, result=float)
    def frameToX(self, frame: int, width: float) -> float:
        from pnp_music_player.ui.models import frame_to_x
        return frame_to_x(frame, self._visible_start, self._visible_end, width)

    @Slot(int, result=str)
    def timeText(self, frame: int) -> str:
        return format_time(frame)

    @Slot(result="QVariantList")
    def visibleBeats(self) -> list[int]:
        if not self.hasTrack:
            return []
        return [v for v in self.controller.effective_beats() if self._visible_start <= v <= self._visible_end]

    @Slot(result="QVariantList")
    def visibleBars(self) -> list[int]:
        if not self.hasTrack:
            return []
        return [v for v in self.controller.bars() if self._visible_start <= v <= self._visible_end]

    def waveform_slice(self, width: int):
        return self._waveform.visible(self._visible_start, self._visible_end, width) if self._waveform else None

    @Slot(result=bool)
    def save(self) -> bool:
        try:
            if self._relink_temporary:
                raise ValueError("This file is linked for this session only. Relink it permanently to save changes.")
            metadata = self.controller.metadata
            backup_existing = bool(
                metadata is not None
                and self._recovery_source == Path(metadata.filepath).resolve()
                and self._recovery_document == self.controller.store.path_for(metadata.filepath)
            )
            if self._recovery_archive and metadata is not None:
                metadata.validate(self.controller.total_frames)
                archive_document = self.archive_store.archive_store.path_for(metadata.filepath)
                if archive_document != self._recovery_document:
                    raise ValueError("Archived metadata changed during recovery")
                backup = self.archive_store.archive_store.quarantine(metadata.filepath)
                try:
                    self.controller.save()
                except Exception:
                    os.replace(backup, archive_document)
                    raise
                self.archiveChanged.emit()
            elif backup_existing:
                self.controller.save(backup_existing=True)
            else:
                self.controller.save()
            self._recovery_source = None
            self._recovery_document = None
            self._recovery_reason = ""
            self._recovery_archive = False
            self._recovery_unsupported = False
            self._error = ""
            succeeded = True
        except Exception as exc:
            self._error = str(exc)
            succeeded = False
        self.changed.emit()
        return succeeded

    @Slot()
    def discard(self) -> None:
        if self.controller.metadata is not None and not self.controller.has_saved_version:
            try:
                if self.controller.preview_mode is not PreviewMode.STOPPED:
                    self.controller.stop()
                self._release_setup_track(Path(self.controller.metadata.filepath).resolve())
                self._recovery_source = None
                self._recovery_document = None
                self._recovery_reason = ""
                self._recovery_archive = False
                self._recovery_unsupported = False
                self._error = ""
            except Exception as exc:
                self._error = str(exc)
            self.viewportChanged.emit()
            self.waveformChanged.emit()
            self.playheadChanged.emit()
            self.changed.emit()
        else:
            self._guard(self.controller.discard, waveform=True)

    @Slot(str)
    def preview(self, mode: str) -> None:
        actions = {
            "normal": self.controller.preview_normal,
            "loop": self.controller.preview_loop,
            "boundary": self.controller.preview_boundary,
            "intro": self.controller.preview_intro,
            "outro": self.controller.preview_outro,
        }
        if mode not in actions:
            self._error = f"Unknown preview mode: {mode}"
            self.changed.emit()
            return
        if not self.previewAvailable:
            self._error = "Player playback is active. Stop it before using Setup preview."
            self.changed.emit()
            return
        if not self._ensure_output():
            return
        self._guard(actions[mode])

    @Slot(str, int, int)
    def previewRange(self, mode: str, start: int, end: int) -> None:
        actions = {
            "cursor": lambda: self.controller.preview_range_once(
                start, end, from_cursor=True
            ),
            "once": lambda: self.controller.preview_range_once(start, end),
            "loop": lambda: self.controller.preview_range_loop(start, end),
            "boundary": lambda: self.controller.preview_range_boundary(start, end),
        }
        if mode not in actions:
            self._error = f"Unknown section preview mode: {mode}"
            self.changed.emit()
            return
        if not self.previewAvailable:
            self._error = "Player playback is active. Stop it before using Setup preview."
            self.changed.emit()
            return
        if not self._ensure_output():
            return
        self._guard(actions[mode])

    @Slot()
    def pauseResume(self) -> None:
        self._guard(self.controller.pause_or_resume)

    @Slot()
    def stop(self) -> None:
        self._guard(self.controller.stop)

    @Slot()
    def stopPreview(self) -> None:
        """Stop setup-owned audio without interrupting unrelated player audio."""
        if self.controller.preview_mode is not PreviewMode.STOPPED:
            self._guard(self.controller.stop)

    def _ensure_output(self) -> bool:
        try:
            self.runtime.ensure_started()
            return True
        except Exception as exc:
            self._error = f"Audio output unavailable: {exc}"
            self.changed.emit()
            return False

    def _guard(self, action, *, waveform: bool = False) -> None:
        try:
            action()
            self._error = ""
        except Exception as exc:
            self._error = str(exc)
        if waveform:
            self.waveformChanged.emit()
        self._sync_playhead()
        self.changed.emit()

    def _sync_playhead(self) -> None:
        value = self.controller.preview_playhead_frame
        if value != self._playhead:
            self._playhead = value
            self.playheadChanged.emit()

    @Slot()
    def _poll_playhead(self) -> None:
        if not self.hasTrack:
            return
        previous_mode = self.controller.preview_mode
        self.controller.preview_playhead()
        self._sync_playhead()
        if self.controller.preview_mode is not previous_mode:
            self.changed.emit()
