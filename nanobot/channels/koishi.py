"""Koishi bridge channel implementation via WebSocket."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import KoishiConfig


class KoishiChannel(BaseChannel):
    """
    Koishi channel via a lightweight websocket bridge.

    Inbound payload:
      {"type":"message","platform":"qq","userId":"...","channelId":"...","content":"..."}

    Outbound payload:
      {"type":"send","targetType":"private|channel","platform":"qq","userId|channelId":"...","content":"..."}
    """

    name = "koishi"

    def __init__(self, config: KoishiConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: KoishiConfig = config
        self._ws: Any = None
        self._connected = False

    async def _connect_ws(self) -> Any:
        import websockets

        headers = None
        if self.config.access_token:
            headers = {"Authorization": f"Bearer {self.config.access_token}"}

        try:
            return await websockets.connect(
                self.config.ws_url,
                additional_headers=headers,
            )
        except TypeError:
            return await websockets.connect(
                self.config.ws_url,
                extra_headers=headers,
            )

    async def start(self) -> None:
        """Connect to Koishi bridge and consume inbound events."""
        logger.info(f"Connecting to Koishi bridge at {self.config.ws_url}...")
        self._running = True

        while self._running:
            try:
                ws = await self._connect_ws()
                self._ws = ws
                self._connected = True
                logger.info("Connected to Koishi bridge")

                async for message in ws:
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from Koishi bridge: {message[:100]}")
                        continue
                    await self._handle_payload(payload)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Koishi bridge connection error: {e}")
            finally:
                self._connected = False
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                self._ws = None

            if self._running:
                logger.info("Reconnecting to Koishi bridge in 3 seconds...")
                await asyncio.sleep(3)

    async def stop(self) -> None:
        """Stop channel."""
        self._running = False
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send message through Koishi bridge."""
        if not self._ws or not self._connected:
            logger.warning("Koishi bridge websocket not connected")
            return

        payload = self._build_send_payload(msg.chat_id, msg.content, msg.reply_to)
        if payload is None:
            logger.warning(f"Unsupported Koishi chat_id format: {msg.chat_id}")
            return

        payload["echo"] = f"nanobot:{int(time.time() * 1000)}"
        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Error sending Koishi message: {e}")

    @staticmethod
    def _build_send_payload(chat_id: str, content: str, reply_to: str | None) -> dict[str, Any] | None:
        private_prefix = "private:"
        channel_prefix = "channel:"

        if chat_id.startswith(private_prefix):
            # private:{platform}:{userId}
            parts = chat_id.split(":", 2)
            if len(parts) != 3 or not parts[1] or not parts[2]:
                return None
            payload = {
                "type": "send",
                "targetType": "private",
                "platform": parts[1],
                "userId": parts[2],
                "content": content,
            }
            if reply_to:
                payload["replyTo"] = reply_to
            return payload

        if chat_id.startswith(channel_prefix):
            # channel:{platform}:{channelId}
            parts = chat_id.split(":", 2)
            if len(parts) != 3 or not parts[1] or not parts[2]:
                return None
            payload = {
                "type": "send",
                "targetType": "channel",
                "platform": parts[1],
                "channelId": parts[2],
                "content": content,
            }
            if reply_to:
                payload["replyTo"] = reply_to
            return payload

        return None

    async def _handle_payload(self, data: dict[str, Any]) -> None:
        """Parse bridge payload and publish inbound messages."""
        if data.get("type") != "message":
            return

        platform = str(data.get("platform") or "unknown")
        user_id = data.get("userId")
        content = str(data.get("content") or "").strip()
        if user_id is None:
            return

        if not content:
            content = "[empty message]"

        is_direct = bool(data.get("isDirect"))
        channel_id = data.get("channelId")
        guild_id = data.get("guildId")

        # Optional allowlist checks for channel/guild scope.
        allow_channel_ids = set(getattr(self.config, "allow_channel_ids", []))
        allow_guild_ids = set(getattr(self.config, "allow_guild_ids", []))
        if not is_direct:
            if allow_channel_ids and (channel_id is None or str(channel_id) not in allow_channel_ids):
                return
            if allow_guild_ids and (guild_id is None or str(guild_id) not in allow_guild_ids):
                return

        if is_direct or not channel_id:
            chat_id = f"private:{platform}:{user_id}"
        else:
            chat_id = f"channel:{platform}:{channel_id}"

        sender_id = f"{platform}:{user_id}|{user_id}"
        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            metadata={
                "message_id": data.get("messageId"),
                "platform": platform,
                "guild_id": guild_id,
                "channel_id": channel_id,
                "is_direct": is_direct,
                "raw_event": data,
            },
        )
