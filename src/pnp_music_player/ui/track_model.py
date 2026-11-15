"""Filtered QAbstractListModel for touch-sized player track cards."""

from __future__ import annotations


from pnp_music_player.ui.metadata_paths import metadata_filename

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal

from pnp_music_player.ui.player_controller import (
    ALL_CATEGORY,
    ALL_ALBUMS,
    FAVORITES_CATEGORY,
    LibraryTrack,
    NO_ALBUM,
    NO_CATEGORY,
)


class TrackListModel(QAbstractListModel):
    countChanged = Signal()

    TrackKeyRole = Qt.ItemDataRole.UserRole + 1
    TrackIdRole = TrackKeyRole + 1
    TitleRole = TrackKeyRole + 2
    ArtistRole = TrackKeyRole + 3
    AlbumRole = TrackKeyRole + 4
    CategoryRole = TrackKeyRole + 5
    TagsRole = TrackKeyRole + 6
    PlayingRole = TrackKeyRole + 7
    QueuedRole = TrackKeyRole + 8
    ValidRole = TrackKeyRole + 9
    StatusRole = TrackKeyRole + 10
    FavoriteRole = TrackKeyRole + 11
    DisplayNameRole = TrackKeyRole + 12
    FileNameRole = TrackKeyRole + 13
    MissingAudioRole = TrackKeyRole + 14

    def __init__(self) -> None:
        super().__init__()
        self._all: list[LibraryTrack] = []
        self._visible: list[LibraryTrack] = []
        self._category = ALL_CATEGORY
        self._album = ALL_ALBUMS
        self._current_key: str | None = None
        self._queued_key: str | None = None

    def roleNames(self) -> dict[int, bytes]:
        return {
            self.TrackKeyRole: b"trackKey", self.TrackIdRole: b"trackId",
            self.TitleRole: b"title", self.ArtistRole: b"artist",
            self.AlbumRole: b"album", self.CategoryRole: b"category",
            self.TagsRole: b"tags", self.PlayingRole: b"isPlaying",
            self.QueuedRole: b"isQueued", self.ValidRole: b"isValid",
            self.StatusRole: b"statusLabel",
            self.FavoriteRole: b"isFavorite",
            self.DisplayNameRole: b"displayName",
            self.FileNameRole: b"fileName",
            self.MissingAudioRole: b"isMissingAudio",
        }

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._visible)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._visible):
            return None
        track = self._visible[index.row()]
        metadata = track.metadata
        values = {
            self.TrackKeyRole: track.key,
            self.TrackIdRole: metadata.id,
            self.TitleRole: metadata.title,
            self.ArtistRole: metadata.artist,
            self.AlbumRole: metadata.album,
            self.CategoryRole: metadata.category or NO_CATEGORY,
            self.TagsRole: " · ".join(metadata.tags[:4]),
            self.PlayingRole: track.key == self._current_key,
            self.QueuedRole: track.key == self._queued_key,
            self.ValidRole: track.valid,
            self.MissingAudioRole: track.error == "Audio file is missing",
            self.StatusRole: (
                "▶ PLAYING" if track.key == self._current_key else
                "NEXT" if track.key == self._queued_key else
                ("AUDIO MISSING" if track.error == "Audio file is missing"
                 else "SETUP REQUIRED") if not track.valid else ""
            ),
            self.FavoriteRole: metadata.favorite,
            self.DisplayNameRole: metadata.title,
            self.FileNameRole: metadata_filename(metadata.filepath),
        }
        return values.get(role)

    def set_tracks(self, tracks: list[LibraryTrack]) -> None:
        self.beginResetModel()
        self._all = list(tracks)
        self._rebuild_visible()
        self.endResetModel()
        self.countChanged.emit()

    def set_category(self, category: str) -> None:
        if category == self._category:
            return
        self.beginResetModel()
        self._category = category
        self._rebuild_visible()
        self.endResetModel()
        self.countChanged.emit()

    def set_album(self, album: str) -> None:
        if album == self._album:
            return
        self.beginResetModel()
        self._album = album
        self._rebuild_visible()
        self.endResetModel()
        self.countChanged.emit()

    def set_playback(self, current_key: str | None, queued_key: str | None) -> None:
        if (current_key, queued_key) == (self._current_key, self._queued_key):
            return
        self._current_key, self._queued_key = current_key, queued_key
        if self._visible:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._visible) - 1),
                [self.PlayingRole, self.QueuedRole, self.StatusRole],
            )

    def _rebuild_visible(self) -> None:
        self._visible = [
            track for track in self._all
            if (
                self._category == ALL_CATEGORY
                or (
                    self._category == FAVORITES_CATEGORY
                    and track.metadata.favorite
                )
                or (
                    track.metadata.category.strip() or NO_CATEGORY
                ) == self._category
            )
            and (
                self._album == ALL_ALBUMS
                or (track.metadata.album.strip() or NO_ALBUM) == self._album
            )
        ]
