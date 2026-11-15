"""Core package for underscore.pnp."""

from pnp_music_player._version import __version__
from pnp_music_player.models.track import TrackMetadata, TrackValidationError

__all__ = ["TrackMetadata", "TrackValidationError", "__version__"]
