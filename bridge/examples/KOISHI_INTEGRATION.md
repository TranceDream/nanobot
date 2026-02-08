# Koishi Integration Guide (Bridge Mode)

This guide wires your existing Koishi plugin to nanobot through WebSocket.

## 1) Enable Koishi channel in nanobot

Edit `~/.nanobot/config.json`:

```json
{
  "channels": {
    "koishi": {
      "enabled": true,
      "wsUrl": "ws://127.0.0.1:3002/ws",
      "accessToken": "",
      "allowFrom": [],
      "allowChannelIds": [],
      "allowGuildIds": []
    }
  }
}
```

Then run:

```bash
nanobot gateway
```

## 2) Add bridge in your Koishi plugin

Copy `bridge/examples/koishi-nanobot-bridge.ts` into your Koishi plugin project,
import `NanobotBridge`, and start it in plugin `apply(ctx)`.

Example:

```ts
import { Context } from 'koishi'
import { NanobotBridge } from './koishi-nanobot-bridge'

export function apply(ctx: Context) {
  const bridge = new NanobotBridge(ctx, {
    wsUrl: 'ws://127.0.0.1:3002/ws',
    token: '',
    allowUserIds: [],      // 可选：私聊白名单
    allowChannelIds: [],   // 可选：群/频道白名单
    allowGuildIds: [],     // 可选：服务器白名单
  })

  bridge.start()
  ctx.on('dispose', () => bridge.stop())
}
```

## 3) Chat ID mapping in nanobot

- Private: `private:{platform}:{userId}`
- Channel: `channel:{platform}:{channelId}`

The bridge handles this mapping for you.

## 4) Security suggestions

- Set `token` on both sides (`accessToken` in nanobot + bridge token in Koishi).
- Bind bridge to `127.0.0.1` unless remote access is required.
- Use `allowFrom` / `allowChannelIds` / `allowGuildIds` in nanobot to limit scope.
