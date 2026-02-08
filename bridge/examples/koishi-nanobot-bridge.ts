/*
  Koishi <-> nanobot bridge example (for existing Koishi plugin integration)

  Usage in your plugin:
    const bridge = new NanobotBridge(ctx, {
      wsUrl: 'ws://127.0.0.1:3002/ws',
      token: '',
    })
    bridge.start()
*/

import { Context, Session } from 'koishi'
import { WebSocketServer, WebSocket } from 'ws'

type BridgeOptions = {
  wsUrl: string
  token?: string
  host?: string
  port?: number
  path?: string
}

type InboundFromNanobot = {
  type: 'send'
  targetType: 'private' | 'channel'
  platform: string
  userId?: string
  channelId?: string
  content: string
  replyTo?: string
  echo?: string
}

type OutboundToNanobot = {
  type: 'message'
  platform: string
  userId: string
  channelId?: string
  guildId?: string
  messageId?: string
  content: string
  isDirect?: boolean
}

export class NanobotBridge {
  private wss: WebSocketServer | null = null
  private clients = new Set<WebSocket>()

  constructor(private ctx: Context, private options: BridgeOptions) {}

  start() {
    const host = this.options.host || '127.0.0.1'
    const port = this.options.port || 3002
    const path = this.options.path || '/ws'

    this.wss = new WebSocketServer({ host, port, path })
    this.ctx.logger('nanobot-bridge').info(`listening on ws://${host}:${port}${path}`)

    this.wss.on('connection', (ws, req) => {
      const expected = this.options.token || ''
      const got = (req.headers['authorization'] || '').toString().replace(/^Bearer\s+/i, '')
      if (expected && expected !== got) {
        ws.close(1008, 'unauthorized')
        return
      }

      this.clients.add(ws)
      ws.on('close', () => this.clients.delete(ws))

      ws.on('message', async (raw) => {
        try {
          const payload = JSON.parse(raw.toString()) as InboundFromNanobot
          if (payload.type !== 'send') return
          await this.deliverToKoishi(payload)
        } catch (e) {
          this.ctx.logger('nanobot-bridge').warn(`invalid payload: ${String(e)}`)
        }
      })
    })

    this.ctx.on('message', async (session) => {
      if (this.isBotSelf(session)) return
      const payload = this.sessionToPayload(session)
      this.broadcast(payload)
    })
  }

  stop() {
    for (const ws of this.clients) ws.close()
    this.clients.clear()
    this.wss?.close()
    this.wss = null
  }

  private async deliverToKoishi(p: InboundFromNanobot) {
    if (p.targetType === 'private' && p.userId) {
      await this.ctx.bots[p.platform]?.sendPrivateMessage(p.userId, p.content)
      return
    }

    if (p.targetType === 'channel' && p.channelId) {
      await this.ctx.bots[p.platform]?.sendMessage(p.channelId, p.content)
    }
  }

  private sessionToPayload(session: Session): OutboundToNanobot {
    const isDirect = session.isDirect ?? !session.guildId
    return {
      type: 'message',
      platform: session.platform,
      userId: session.userId,
      channelId: session.channelId,
      guildId: session.guildId,
      messageId: session.messageId,
      content: session.content || '',
      isDirect,
    }
  }

  private broadcast(payload: OutboundToNanobot) {
    const encoded = JSON.stringify(payload)
    for (const ws of this.clients) {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(encoded)
      }
    }
  }

  private isBotSelf(session: Session): boolean {
    return !!session.user?.isBot
  }
}
