from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.koishi import KoishiChannel
from nanobot.channels.manager import ChannelManager
from nanobot.channels.onebot import OneBotChannel
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import Config, OneBotConfig
from nanobot.config.schema import KoishiConfig


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True


async def test_onebot_private_message_published_to_bus() -> None:
    bus = MessageBus()
    channel = OneBotChannel(OneBotConfig(enabled=True), bus)

    await channel._handle_payload(
        {
            "post_type": "message",
            "message_type": "private",
            "message_id": 1001,
            "self_id": 999999,
            "user_id": 123456,
            "raw_message": "hello from onebot",
        }
    )

    msg = await bus.consume_inbound()
    assert msg.channel == "onebot"
    assert msg.sender_id == "123456"
    assert msg.chat_id == "private:123456"
    assert msg.content == "hello from onebot"
    assert msg.metadata["message_type"] == "private"


async def test_onebot_group_message_segment_fallback() -> None:
    bus = MessageBus()
    channel = OneBotChannel(OneBotConfig(enabled=True), bus)

    await channel._handle_payload(
        {
            "post_type": "message",
            "message_type": "group",
            "message_id": 1002,
            "self_id": 999999,
            "user_id": 345678,
            "group_id": 24680,
            "raw_message": "",
            "message": [
                {"type": "text", "data": {"text": "hi"}},
                {"type": "image", "data": {"file": "abc.jpg"}},
            ],
        }
    )

    msg = await bus.consume_inbound()
    assert msg.chat_id == "group:24680"
    assert msg.sender_id == "345678"
    assert msg.content == "hi[image]"


async def test_onebot_ignores_non_message_event() -> None:
    bus = MessageBus()
    channel = OneBotChannel(OneBotConfig(enabled=True), bus)

    await channel._handle_payload(
        {
            "post_type": "notice",
            "notice_type": "group_upload",
        }
    )

    assert bus.inbound_size == 0


async def test_onebot_ignores_self_message() -> None:
    bus = MessageBus()
    channel = OneBotChannel(OneBotConfig(enabled=True), bus)

    await channel._handle_payload(
        {
            "post_type": "message",
            "message_type": "private",
            "self_id": 1234,
            "user_id": 1234,
            "raw_message": "should ignore",
        }
    )

    assert bus.inbound_size == 0


async def test_onebot_respects_allow_list() -> None:
    bus = MessageBus()
    channel = OneBotChannel(
        OneBotConfig(enabled=True, allow_from=["42"]),
        bus,
    )

    await channel._handle_payload(
        {
            "post_type": "message",
            "message_type": "private",
            "self_id": 9999,
            "user_id": 41,
            "raw_message": "not allowed",
        }
    )

    assert bus.inbound_size == 0


async def test_onebot_send_private_and_group_actions() -> None:
    import json

    bus = MessageBus()
    channel = OneBotChannel(OneBotConfig(enabled=True), bus)
    ws = FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(channel="onebot", chat_id="private:10001", content="hello private")
    )
    await channel.send(
        OutboundMessage(channel="onebot", chat_id="group:20002", content="hello group")
    )

    assert len(ws.sent) == 2

    first = json.loads(ws.sent[0])
    assert first["action"] == "send_private_msg"
    assert first["params"]["user_id"] == 10001
    assert first["params"]["message"] == "hello private"

    second = json.loads(ws.sent[1])
    assert second["action"] == "send_group_msg"
    assert second["params"]["group_id"] == 20002
    assert second["params"]["message"] == "hello group"


async def test_onebot_send_invalid_chat_id_noop() -> None:
    bus = MessageBus()
    channel = OneBotChannel(OneBotConfig(enabled=True), bus)
    ws = FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(channel="onebot", chat_id="bad-format", content="ignored")
    )
    await channel.send(
        OutboundMessage(channel="onebot", chat_id="private:not-a-number", content="ignored")
    )

    assert ws.sent == []


def test_channel_manager_registers_onebot_when_enabled() -> None:
    config = Config()
    config.channels.onebot.enabled = True
    manager = ChannelManager(config=config, bus=MessageBus())
    assert "onebot" in manager.enabled_channels


async def test_koishi_private_message_published_to_bus() -> None:
    bus = MessageBus()
    channel = KoishiChannel(KoishiConfig(enabled=True), bus)

    await channel._handle_payload(
        {
            "type": "message",
            "platform": "qq",
            "userId": "12345",
            "content": "hello koishi",
            "isDirect": True,
            "messageId": "m1",
        }
    )

    msg = await bus.consume_inbound()
    assert msg.channel == "koishi"
    assert msg.chat_id == "private:qq:12345"
    assert msg.sender_id == "qq:12345|12345"
    assert msg.content == "hello koishi"


async def test_koishi_channel_message_published_to_bus() -> None:
    bus = MessageBus()
    channel = KoishiChannel(KoishiConfig(enabled=True), bus)

    await channel._handle_payload(
        {
            "type": "message",
            "platform": "discord",
            "userId": "u-1",
            "channelId": "c-1",
            "guildId": "g-1",
            "content": "channel msg",
        }
    )

    msg = await bus.consume_inbound()
    assert msg.chat_id == "channel:discord:c-1"
    assert msg.metadata["guild_id"] == "g-1"
    assert msg.metadata["platform"] == "discord"


async def test_koishi_ignores_non_message_payload() -> None:
    bus = MessageBus()
    channel = KoishiChannel(KoishiConfig(enabled=True), bus)
    await channel._handle_payload({"type": "status", "value": "ok"})
    assert bus.inbound_size == 0


async def test_koishi_respects_allow_list() -> None:
    bus = MessageBus()
    channel = KoishiChannel(KoishiConfig(enabled=True, allow_from=["42"]), bus)

    await channel._handle_payload(
        {
            "type": "message",
            "platform": "qq",
            "userId": "41",
            "content": "blocked",
            "isDirect": True,
        }
    )
    assert bus.inbound_size == 0


async def test_koishi_send_private_and_channel_actions() -> None:
    import json

    bus = MessageBus()
    channel = KoishiChannel(KoishiConfig(enabled=True), bus)
    ws = FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(channel="koishi", chat_id="private:qq:10001", content="hello private")
    )
    await channel.send(
        OutboundMessage(channel="koishi", chat_id="channel:discord:20002", content="hello channel")
    )

    assert len(ws.sent) == 2
    p1 = json.loads(ws.sent[0])
    p2 = json.loads(ws.sent[1])
    assert p1["type"] == "send"
    assert p1["targetType"] == "private"
    assert p1["platform"] == "qq"
    assert p1["userId"] == "10001"
    assert p2["targetType"] == "channel"
    assert p2["platform"] == "discord"
    assert p2["channelId"] == "20002"


async def test_koishi_send_invalid_chat_id_noop() -> None:
    bus = MessageBus()
    channel = KoishiChannel(KoishiConfig(enabled=True), bus)
    ws = FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(OutboundMessage(channel="koishi", chat_id="private:qq", content="x"))
    await channel.send(OutboundMessage(channel="koishi", chat_id="bad", content="y"))
    assert ws.sent == []


async def test_koishi_allow_channel_ids_blocks_unlisted_channel() -> None:
    bus = MessageBus()
    channel = KoishiChannel(
        KoishiConfig(enabled=True, allow_channel_ids=["allowed-channel"]),
        bus,
    )
    await channel._handle_payload(
        {
            "type": "message",
            "platform": "qq",
            "userId": "100",
            "channelId": "blocked-channel",
            "content": "blocked",
        }
    )
    assert bus.inbound_size == 0


async def test_koishi_allow_channel_ids_allows_listed_channel() -> None:
    bus = MessageBus()
    channel = KoishiChannel(
        KoishiConfig(enabled=True, allow_channel_ids=["allowed-channel"]),
        bus,
    )
    await channel._handle_payload(
        {
            "type": "message",
            "platform": "qq",
            "userId": "100",
            "channelId": "allowed-channel",
            "content": "ok",
        }
    )
    msg = await bus.consume_inbound()
    assert msg.chat_id == "channel:qq:allowed-channel"


async def test_koishi_allow_guild_ids_blocks_unlisted_guild() -> None:
    bus = MessageBus()
    channel = KoishiChannel(
        KoishiConfig(enabled=True, allow_guild_ids=["allowed-guild"]),
        bus,
    )
    await channel._handle_payload(
        {
            "type": "message",
            "platform": "discord",
            "userId": "100",
            "channelId": "c1",
            "guildId": "blocked-guild",
            "content": "blocked",
        }
    )
    assert bus.inbound_size == 0


def test_channel_manager_registers_koishi_when_enabled() -> None:
    config = Config()
    config.channels.koishi.enabled = True
    manager = ChannelManager(config=config, bus=MessageBus())
    assert "koishi" in manager.enabled_channels
