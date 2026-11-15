"""Discord voice lifecycle isolated from Qt and the realtime audio callback."""

from __future__ import annotations

import asyncio
from concurrent.futures import CancelledError as FutureCancelledError
import ctypes.util
from dataclasses import dataclass
from enum import Enum
import logging
import sys
from threading import Event, Lock, Thread
from typing import Awaitable, Callable

from pnp_music_player.audio.output_tap import DiscordPcmTap
from pnp_music_player.config_env import load_discord_token

_LOG = logging.getLogger("audio.discord")


def _ensure_discord_opus(
    discord_module: object,
    *,
    platform: str | None = None,
    find_library: Callable[[str], str | None] = ctypes.util.find_library,
) -> None:
    """Load and validate the Opus encoder through the platform's native path.

    discord.py ships matching Opus DLLs in its Windows wheel and lazily loads
    them when an Encoder is constructed. POSIX installations instead use the
    system library discovered by ctypes.
    """
    opus = getattr(discord_module, "opus")
    if opus.is_loaded():
        return
    target_platform = sys.platform if platform is None else platform
    if target_platform.startswith("win"):
        # Constructing one short-lived encoder invokes discord.py's supported
        # lazy Windows loader and also proves that the bundled DLL architecture
        # matches the running Python interpreter.
        opus.Encoder()
    else:
        opus_library = find_library("opus")
        if not opus_library:
            raise RuntimeError("libopus was not found")
        opus.load_opus(opus_library)
    if not opus.is_loaded():
        raise RuntimeError("the Opus encoder could not be loaded")


class DiscordState(str, Enum):
    NOT_CONFIGURED = "not_configured"
    OFFLINE = "offline"
    CONNECTING = "connecting"
    READY = "ready"
    JOINING_CHANNEL = "joining_channel"
    IN_CHANNEL = "in_channel"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DiscordChoice:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class DiscordSnapshot:
    state: DiscordState
    detail: str
    guilds: tuple[DiscordChoice, ...]
    channels: tuple[DiscordChoice, ...]
    selected_guild_id: str
    channel_id: str
    invite_url: str
    gateway_connected: bool = False


class DiscordVoiceService:
    """Run discord.py on a private asyncio loop and expose small commands."""

    def __init__(
        self,
        tap: DiscordPcmTap,
        *,
        token: str | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._tap = tap
        self._token = (token if token is not None else load_discord_token()).strip()
        self._on_change = on_change
        self._lock = Lock()
        self._closing = Event()
        self._shutdown_step_timeout = 1.5
        self._snapshot = DiscordSnapshot(
            state=(DiscordState.OFFLINE if self._token else DiscordState.NOT_CONFIGURED),
            detail=("Offline" if self._token else "Bot token not configured"),
            guilds=(), channels=(), selected_guild_id="", channel_id="",
            invite_url="",
        )
        self._thread: Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: object | None = None
        self._voice_client: object | None = None
        self._discord: object | None = None

    def set_on_change(self, callback: Callable[[], None] | None) -> None:
        self._on_change = callback

    def snapshot(self) -> DiscordSnapshot:
        with self._lock:
            return self._snapshot

    def start(self) -> None:
        if not self._token or self._thread is not None:
            return
        self._closing.clear()
        self._set_snapshot(
            state=DiscordState.CONNECTING,
            detail="Connecting bot…",
            gateway_connected=False,
        )
        self._thread = Thread(target=self._thread_main, name="discord-voice", daemon=True)
        self._thread.start()

    def reconfigure_token(self, token: str) -> None:
        """Disconnect, replace credentials and start a fresh Discord client."""
        self.close()
        self._token = str(token).strip()
        self._discord = None
        self._closing.clear()
        self._set_snapshot(
            state=(DiscordState.OFFLINE if self._token else DiscordState.NOT_CONFIGURED),
            detail=("Offline" if self._token else "Bot token not configured"),
            guilds=(),
            channels=(),
            selected_guild_id="",
            channel_id="",
            invite_url="",
            gateway_connected=False,
        )
        self.start()

    def select_guild(self, guild_id: str) -> None:
        self._submit(self._select_guild(guild_id))

    def join(self, guild_id: str, channel_id: str) -> None:
        self._submit(self._join(guild_id, channel_id))

    def leave(self) -> None:
        self._submit(self._leave())

    def close(self) -> None:
        self._closing.set()
        self._tap.set_enabled(False)
        loop = self._loop
        client = self._client
        if loop is not None and client is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            try:
                future.result(timeout=4.0)
            except TimeoutError:
                future.cancel()
                _LOG.warning("Discord shutdown timed out; stopping its event loop")
                loop.call_soon_threadsafe(loop.stop)
            except FutureCancelledError:
                # discord.py closes the client from inside this coroutine.
                # That lets client.start() return and its loop teardown may
                # cancel this outer Future after cleanup already succeeded.
                pass
            except Exception as exc:
                _LOG.warning("Discord shutdown was forced: %s", exc)
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=4.0)
            if thread.is_alive() and loop is not None and loop.is_running():
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=1.0)

    def _thread_main(self) -> None:
        try:
            import discord
        except (ImportError, OSError) as exc:
            self._set_snapshot(
                state=DiscordState.ERROR,
                detail=f"Discord voice support unavailable: {exc}",
                gateway_connected=False,
            )
            return
        try:
            _ensure_discord_opus(discord)
        except Exception as exc:
            self._set_snapshot(
                state=DiscordState.ERROR,
                detail=f"Discord voice codec unavailable: {exc}",
                gateway_connected=False,
            )
            return
        self._discord = discord
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(self._asyncio_exception)
        self._loop = loop
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        service = self

        class Client(discord.Client):
            async def on_ready(self) -> None:
                service._client_ready()

            async def on_guild_join(self, _guild: object) -> None:
                service._client_ready()

            async def on_guild_remove(self, _guild: object) -> None:
                service._client_ready()

            async def on_disconnect(self) -> None:
                service._mark_gateway_disconnected()

            async def on_resumed(self) -> None:
                service._client_ready()

        client = Client(intents=intents)
        self._client = client
        try:
            loop.run_until_complete(client.start(self._token, reconnect=True))
        except discord.LoginFailure:
            self._tap.set_enabled(False)
            if not self._closing.is_set():
                _LOG.warning("Discord login rejected: invalid bot token")
                self._set_snapshot(
                    state=DiscordState.ERROR,
                    detail="Discord login failed: invalid bot token",
                    gateway_connected=False,
                )
        except Exception as exc:
            self._tap.set_enabled(False)
            if not self._closing.is_set():
                _LOG.exception("Discord client failed")
                self._set_snapshot(
                    state=DiscordState.ERROR,
                    detail=f"Discord error: {exc}",
                    gateway_connected=False,
                )
        finally:
            try:
                if not client.is_closed():
                    loop.run_until_complete(
                        self._await_bounded(
                            client.close(), operation="Discord client cleanup"
                        )
                    )
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                self._voice_client = None
                self._client = None
                self._loop = None
                loop.close()

    @staticmethod
    def _asyncio_exception(
        _loop: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> None:
        exception = context.get("exception")
        message = str(context.get("message", "unhandled Discord asyncio error"))
        if isinstance(exception, BaseException):
            _LOG.error(
                "%s", message,
                exc_info=(type(exception), exception, exception.__traceback__),
            )
        else:
            _LOG.error("%s", message)

    def _client_ready(self) -> None:
        client = self._client
        discord = self._discord
        if client is None or discord is None:
            return
        guilds = tuple(
            DiscordChoice(str(guild.id), guild.name)
            for guild in sorted(client.guilds, key=lambda item: item.name.casefold())
        )
        current = self.snapshot()
        selected = current.selected_guild_id
        if not any(item.id == selected for item in guilds):
            selected = guilds[0].id if guilds else ""
        permissions = discord.Permissions(view_channel=True, connect=True, speak=True)
        invite_url = discord.utils.oauth_url(
            client.user.id, permissions=permissions, scopes=("bot",)
        )
        self._set_snapshot(
            state=(DiscordState.IN_CHANNEL if self._voice_connected() else DiscordState.READY),
            detail=(current.detail if self._voice_connected() else "Bot ready"),
            guilds=guilds,
            selected_guild_id=selected,
            invite_url=invite_url,
            gateway_connected=True,
        )
        if selected:
            self._submit(self._select_guild(selected))

    def _mark_gateway_disconnected(self) -> None:
        if self._closing.is_set():
            return
        current = self.snapshot()
        if current.state is DiscordState.OFFLINE:
            return
        self._set_snapshot(
            state=DiscordState.RECONNECTING,
            detail="Discord connection interrupted; reconnecting…",
            gateway_connected=False,
        )

    async def _select_guild(self, guild_id: str) -> None:
        client = self._client
        if client is None:
            return
        try:
            numeric_id = int(guild_id)
        except ValueError:
            numeric_id = -1
        guild = client.get_guild(numeric_id)
        channels: tuple[DiscordChoice, ...] = ()
        if guild is not None:
            member = guild.me
            channels = tuple(
                DiscordChoice(str(channel.id), channel.name)
                for channel in sorted(guild.voice_channels, key=lambda item: item.position)
                if member is not None
                and channel.permissions_for(member).view_channel
                and channel.permissions_for(member).connect
                and channel.permissions_for(member).speak
            )
        self._set_snapshot(
            selected_guild_id=guild_id if guild is not None else "",
            channels=channels,
        )

    async def _join(self, guild_id: str, channel_id: str) -> None:
        client = self._client
        if client is None:
            self._set_snapshot(state=DiscordState.ERROR, detail="Discord bot is offline")
            return
        self._set_snapshot(state=DiscordState.JOINING_CHANNEL, detail="Joining channel…")
        try:
            guild = client.get_guild(int(guild_id))
            channel = guild.get_channel(int(channel_id)) if guild is not None else None
            if guild is None or channel is None or channel not in guild.voice_channels:
                raise ValueError("Selected voice channel is no longer available")
            voice = self._voice_client
            if voice is not None and voice.is_connected():
                await voice.move_to(channel)
            else:
                voice = await channel.connect(reconnect=True)
                self._voice_client = voice

            service = self
            discord = self._discord

            class LivePcmSource(discord.AudioSource):
                def read(self) -> bytes:
                    return service._tap.read_packet()

                def is_opus(self) -> bool:
                    return False

            if voice.is_playing():
                voice.stop()
            self._tap.set_enabled(True)

            def playback_finished(error: Exception | None) -> None:
                if error is not None:
                    self._tap.set_enabled(False)
                    self._set_snapshot(
                        state=DiscordState.ERROR,
                        detail=f"Discord audio stopped: {error}",
                    )

            voice.play(
                LivePcmSource(), after=playback_finished, signal_type="music"
            )
            self._set_snapshot(
                state=DiscordState.IN_CHANNEL,
                detail=f"Connected to {guild.name} / {channel.name}",
                selected_guild_id=str(guild.id),
                channel_id=str(channel.id),
            )
        except Exception as exc:
            self._tap.set_enabled(False)
            _LOG.exception("cannot join Discord voice channel")
            self._set_snapshot(state=DiscordState.ERROR, detail=f"Could not join: {exc}")

    async def _leave(self) -> None:
        self._tap.set_enabled(False)
        voice, self._voice_client = self._voice_client, None
        try:
            if voice is not None:
                if voice.is_playing():
                    voice.stop()
                disconnected = await self._await_bounded(
                    voice.disconnect(force=True), operation="voice disconnect"
                )
                if not disconnected:
                    voice.cleanup()
            self._set_snapshot(
                state=DiscordState.READY,
                detail="Bot ready",
                channel_id="",
            )
        except Exception as exc:
            _LOG.exception("cannot leave Discord voice channel")
            self._set_snapshot(state=DiscordState.ERROR, detail=f"Could not leave: {exc}")

    async def _shutdown(self) -> None:
        self._tap.set_enabled(False)
        voice, self._voice_client = self._voice_client, None
        if voice is not None:
            if voice.is_playing():
                voice.stop()
            disconnected = await self._await_bounded(
                voice.disconnect(force=True), operation="voice shutdown"
            )
            if not disconnected:
                voice.cleanup()
        client = self._client
        if client is not None:
            await self._await_bounded(
                client.close(), operation="Discord client shutdown"
            )

    async def _await_bounded(
        self, awaitable: Awaitable[object], *, operation: str
    ) -> bool:
        """Wait for Discord cleanup without allowing it to stall app exit."""
        task = asyncio.ensure_future(awaitable)
        done, _pending = await asyncio.wait(
            {task}, timeout=self._shutdown_step_timeout
        )
        if not done:
            task.cancel()
            _LOG.warning("%s exceeded %.1f seconds", operation, self._shutdown_step_timeout)
            # Give cooperative coroutines one loop turn to observe cancellation,
            # without waiting indefinitely for cancellation-resistant cleanup.
            await asyncio.sleep(0)
            return False
        try:
            task.result()
            return True
        except asyncio.CancelledError:
            return False
        except Exception as exc:
            _LOG.warning("%s failed during cleanup: %s", operation, exc)
            return False

    def _submit(self, coroutine: object) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            if hasattr(coroutine, "close"):
                coroutine.close()
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)

        def report_failure(completed: object) -> None:
            try:
                completed.result()
            except Exception:
                _LOG.exception("Discord command failed")

        future.add_done_callback(report_failure)

    def _voice_connected(self) -> bool:
        voice = self._voice_client
        return bool(voice is not None and voice.is_connected())

    def _set_snapshot(self, **changes: object) -> None:
        with self._lock:
            previous_state = self._snapshot.state
            values = {
                "state": self._snapshot.state,
                "detail": self._snapshot.detail,
                "guilds": self._snapshot.guilds,
                "channels": self._snapshot.channels,
                "selected_guild_id": self._snapshot.selected_guild_id,
                "channel_id": self._snapshot.channel_id,
                "invite_url": self._snapshot.invite_url,
                "gateway_connected": self._snapshot.gateway_connected,
            }
            values.update(changes)
            self._snapshot = DiscordSnapshot(**values)
            current_state = self._snapshot.state
            current_detail = self._snapshot.detail
        if current_state is not previous_state:
            _LOG.info(
                "Discord state changed from=%s to=%s detail=%s",
                previous_state.value,
                current_state.value,
                current_detail,
            )
        callback = self._on_change
        if callback is not None:
            callback()
