import { useEffect, useRef } from 'react'
import { isDemo, startDemoStream } from '../demo/engine'
import { useStore } from '../store/useStore'
import type { WsMessage } from '../types'

/**
 * 单条 WebSocket 长连接：
 *  - 通过 Vite 代理连到后端 /ws（同源，无 CORS）
 *  - 断线指数退避重连
 *  - 切换市场时先退订旧标的、再订阅新标的
 *  - 20s 心跳
 */
export function useStream(): void {
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(0)
  const timerRef = useRef<number | null>(null)
  const subscribedRef = useRef<string[]>([])
  const disposedRef = useRef(false)

  const wsStatus = useStore((s) => s.wsStatus)
  const market = useStore((s) => s.market)
  const markets = useStore((s) => s.markets)

  useEffect(() => {
    disposedRef.current = false

    const handle = (msg: WsMessage) => {
      const st = useStore.getState()
      switch (msg.type) {
        case 'hello':
          st.setServerTime(msg.server_time)
          break
        case 'tick':
          st.applyTick(msg)
          break
        case 'account':
          st.setAccount(msg.account)
          break
        case 'life':
          st.setLife(msg.life)
          break
        case 'event':
          st.setActiveEvent(msg.event)
          break
        case 'session_resumed':
          st.setResumeNotice(msg)
          break
        case 'game_over':
          st.setGameOver(msg)
          break
        case 'order':
          st.upsertOrder(msg.order)
          break
        case 'trade':
          st.prependTrade(msg.trade)
          break
        case 'settlement':
          st.notify('info', `交收到账 ${msg.item.amount} ${msg.item.currency}`)
          break
        default:
          break
      }
    }

    const connect = () => {
      if (disposedRef.current) return
      const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
      const socket = new WebSocket(`${proto}://${window.location.host}/ws`)
      wsRef.current = socket
      useStore.getState().setWsStatus('connecting')

      socket.onopen = () => {
        retryRef.current = 0
        subscribedRef.current = []
        useStore.getState().setWsStatus('open')
      }

      socket.onmessage = (event) => {
        try {
          handle(JSON.parse(event.data) as WsMessage)
        } catch {
          /* 忽略无法解析的帧 */
        }
      }

      socket.onerror = () => socket.close()

      socket.onclose = () => {
        wsRef.current = null
        if (disposedRef.current) return
        useStore.getState().setWsStatus('closed')
        const delay = Math.min(10_000, 500 * 2 ** retryRef.current)
        retryRef.current += 1
        timerRef.current = window.setTimeout(connect, delay)
      }
    }

    if (isDemo) {
      // 单机演示：不建 WebSocket，用本地引擎生成 tick / account / life / event
      useStore.getState().setWsStatus('open')
      const stop = startDemoStream((msg) => handle(msg as WsMessage))
      return () => {
        disposedRef.current = true
        stop()
      }
    }

    // StrictMode 会执行 mount → cleanup → mount：立即连接会让第一次连接
    // 先抢到 session_resumed 又被 cleanup 关掉，弹窗永远丢失。
    // 延迟一个宏任务，第一次的建连定时器会被 cleanup 取消，只保留真正的连接。
    const connectTimer = window.setTimeout(connect, 0)

    return () => {
      disposedRef.current = true
      window.clearTimeout(connectTimer)
      if (timerRef.current !== null) window.clearTimeout(timerRef.current)
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [])

  // 订阅当前市场的标的
  useEffect(() => {
    if (wsStatus !== 'open') return
    const symbols = markets.find((g) => g.key === market)?.symbols.map((s) => s.symbol) ?? []
    const socket = wsRef.current
    if (!socket || socket.readyState !== WebSocket.OPEN || symbols.length === 0) return

    const previous = subscribedRef.current
    if (previous.length > 0) {
      socket.send(JSON.stringify({ type: 'unsubscribe', symbols: previous }))
    }
    socket.send(
      JSON.stringify({
        type: 'subscribe',
        symbols,
        channels: ['tick', 'account', 'order', 'trade', 'settlement', 'life', 'event'],
      }),
    )
    subscribedRef.current = symbols
  }, [wsStatus, market, markets])

  // 心跳
  useEffect(() => {
    const id = window.setInterval(() => {
      const socket = wsRef.current
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'ping' }))
      }
    }, 20_000)
    return () => window.clearInterval(id)
  }, [])
}
