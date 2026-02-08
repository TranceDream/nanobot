"""OneBot v11 channel implementation via WebSocket."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import OneBotConfig


class OneBotChannel(BaseChannel):
    """OneBot v11 channel using reverse WebSocket client mode."""

    name = "onebot"

    def __init__(self, config: OneBotConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: OneBotConfig = config
        self._ws: Any = None
        self._connected = False

    async def _connect_ws(self) -> Any:
        """Open a websocket connection with optional authorization header."""
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
            # Compatibility fallback for older websockets versions.
            return await websockets.connect(
                self.config.ws_url,
                extra_headers=headers,
            )

    async def start(self) -> None:
        """Start the OneBot connection and consume upstream events."""
        logger.info(f"Connecting to OneBot gateway at {self.config.ws_url}...")
        self._running = True

        while self._running:
            try:
                ws = await self._connect_ws()
                self._ws = ws
                self._connected = True
                logger.info("Connected to OneBot gateway")

                async for message in ws:
                    try:
                        payload = json.loads(message)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON from OneBot: {message[:100]}")
                        continue

                    await self._handle_payload(payload)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"OneBot connection error: {e}")
            finally:
                self._connected = False
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                self._ws = None

            if self._running:
                logger.info("Reconnecting to OneBot in 3 seconds...")
                await asyncio.sleep(3)

    async def stop(self) -> None:
        """Stop the OneBot channel."""
        self._running = False
        self._connected = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send an outbound message through OneBot action API."""
        if not self._ws or not self._connected:
            logger.warning("OneBot websocket not connected")
            return

        action, params = self._build_send_action(msg.chat_id, msg.content)
        if not action:
            logger.warning(f"Unsupported OneBot chat_id format: {msg.chat_id}")
            return

        payload = {
            "action": action,
            "params": params,
            "echo": f"nanobot:{int(time.time() * 1000)}",
        }

        try:
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Error sending OneBot message: {e}")

    @staticmethod
    def _build_send_action(chat_id: str, content: str) -> tuple[str | None, dict[str, Any]]:
        """Map internal chat_id to OneBot action + params."""
        if chat_id.startswith("private:"):
            uid = chat_id.split(":", 1)[1]
            if uid:
                try:
                    return "send_private_msg", {"user_id": int(uid), "message": content}
                except ValueError:
                    return None, {}
        if chat_id.startswith("group:"):
            gid = chat_id.split(":", 1)[1]
            if gid:
                try:
                    return "send_group_msg", {"group_id": int(gid), "message": content}
                except ValueError:
                    return None, {}
        return None, {}

    @staticmethod
    def _extract_content(data: dict[str, Any]) -> str:
        """Extract plain text content from OneBot payload."""
        raw_message = data.get("raw_message")
        if isinstance(raw_message, str) and raw_message.strip():
            return raw_message

        message = data.get("message")
        if not isinstance(message, list):
            return "[empty message]"

        parts: list[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            seg_data = seg.get("data", {}) if isinstance(seg.get("data"), dict) else {}
            if seg_type == "text":
                text = seg_data.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
            else:
                parts.append(f"[{seg_type}]")

        merged = "".join(parts).strip()
        return merged or "[empty message]"

    async def _handle_payload(self, data: dict[str, Any]) -> None:
        """Handle OneBot upstream payloads and forward message events."""
        if data.get("post_type") != "message":
            return

        user_id = data.get("user_id")
        self_id = data.get("self_id")
        if user_id is None:
            return

        # Ignore bot's own messages to avoid loops.
        if self_id is not None and str(user_id) == str(self_id):
            return

        message_type = str(data.get("message_type", "private"))
        if message_type == "group":
            group_id = data.get("group_id")
            if group_id is None:
                return
            chat_id = f"group:{group_id}"
        else:
            chat_id = f"private:{user_id}"

        await self._handle_message(
            sender_id=str(user_id),
            chat_id=chat_id,
            content=self._extract_content(data),
            metadata={
                "message_id": data.get("message_id"),
                "post_type": data.get("post_type"),
                "message_type": message_type,
                "sub_type": data.get("sub_type"),
                "raw_event": data,
            },
        )
