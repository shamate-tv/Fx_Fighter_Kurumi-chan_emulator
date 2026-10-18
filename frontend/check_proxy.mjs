// 联调验证：全部走 Vite 代理（5173），不直连 8000。
const BASE = 'http://127.0.0.1:5173'
const WS = 'ws://127.0.0.1:5173/ws'

const results = []
const ok = (name, detail) => {
  results.push(`  PASS  ${name}${detail ? ` — ${detail}` : ''}`)
}
const bad = (name, detail) => {
  results.push(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`)
}

// 1. 页面
const html = await fetch(`${BASE}/`).then((r) => r.text())
html.includes('<div id="root">') && html.includes('/src/main.tsx')
  ? ok('index.html 由 dev server 提供', `${html.length} bytes`)
  : bad('index.html', 'root 或入口脚本缺失')

// 2. HTTP 代理
const health = await fetch(`${BASE}/api/health`).then((r) => r.json())
health.market_data === 'mock'
  ? ok('HTTP 代理 /api -> 8000', `market_data=${health.market_data} sources=${JSON.stringify(health.data_sources)}`)
  : bad('HTTP 代理', JSON.stringify(health).slice(0, 200))

const markets = await fetch(`${BASE}/api/markets`).then((r) => r.json())
markets.markets?.length === 3
  ? ok('规则矩阵可达', `${markets.markets.map((m) => `${m.key}:${m.symbols.length}`).join(' ')}`)
  : bad('规则矩阵', '市场数不是 3')

const hist = await fetch(`${BASE}/api/history/EURUSD?interval=1m&limit=20`).then((r) => r.json())
Array.isArray(hist) && hist.length === 20
  ? ok('K 线可达', `${hist.length} 根，最后一根 close=${hist.at(-1).close}`)
  : bad('K 线', JSON.stringify(hist).slice(0, 160))

// 3. WebSocket 代理
const ws = new WebSocket(WS)
const inbox = []
await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error('WS 连接超时')), 10_000)
  ws.onopen = () => {
    clearTimeout(timer)
    resolve()
  }
  ws.onerror = () => {
    clearTimeout(timer)
    reject(new Error('WS 连接失败'))
  }
})
ws.onmessage = (event) => {
  try {
    inbox.push(JSON.parse(event.data))
  } catch {
    /* ignore */
  }
}

// 4. 订阅
ws.send(
  JSON.stringify({
    type: 'subscribe',
    symbols: ['00700.HK', 'EURUSD'],
    channels: ['tick', 'account', 'order', 'trade', 'settlement'],
  }),
)
await new Promise((r) => setTimeout(r, 2500))

inbox.some((m) => m.type === 'hello') ? ok('WS 代理握手 hello') : bad('WS hello', '未收到 hello')
const ticks = inbox.filter((m) => m.type === 'tick')
ticks.length > 0
  ? ok('实时 tick 推送', `${ticks.length} 条，例：${ticks[0].symbol} mid=${ticks[0].mid} stale=${ticks[0].stale}`)
  : bad('tick 推送', '2.5s 内没有收到 tick')
inbox.some((m) => m.type === 'account') ? ok('账户快照推送') : bad('account 推送', '未收到')

// 5. 下单 → 撮合 → 推送到 WS
const before = inbox.length
const orderResp = await fetch(`${BASE}/api/orders`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ symbol: '00700.HK', side: 'BUY', type: 'MARKET', qty: '100' }),
}).then((r) => r.json())

orderResp.status === 'FILLED'
  ? ok('下单即成交', `${orderResp.symbol} ${orderResp.qty}股 @ ${orderResp.avg_fill_price}，费用 ${orderResp.fee}`)
  : bad('下单', JSON.stringify(orderResp).slice(0, 200))

await new Promise((r) => setTimeout(r, 900))
const after = inbox.slice(before)
const gotOrder = after.some((m) => m.type === 'order' && m.order.id === orderResp.id)
const gotTrade = after.some((m) => m.type === 'trade' && m.trade.order_id === orderResp.id)
const gotAccount = after.some((m) => m.type === 'account')
gotOrder && gotTrade && gotAccount
  ? ok('成交事件回流前端', 'order + trade + account 均到达')
  : bad('事件回流', `order=${gotOrder} trade=${gotTrade} account=${gotAccount}`)

// 6. 港股 lot_size 拦截
const lotErr = await fetch(`${BASE}/api/orders`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ symbol: '00700.HK', side: 'BUY', type: 'MARKET', qty: '150' }),
})
const lotBody = await lotErr.json()
lotErr.status === 400 && lotBody.error?.code === 'LOT_SIZE_VIOLATION'
  ? ok('lot_size 校验', lotBody.error.message)
  : bad('lot_size 校验', JSON.stringify(lotBody).slice(0, 160))

// 7. 卖出 → 在途资金
const sellResp = await fetch(`${BASE}/api/orders`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ symbol: '00700.HK', side: 'SELL', type: 'MARKET', qty: '100' }),
}).then((r) => r.json())
const acc = await fetch(`${BASE}/api/account`).then((r) => r.json())
const hkd = acc.balances.HKD
Number(hkd.pending) > 0
  ? ok('T+2 在途资金', `available=${hkd.available} pending=${hkd.pending} 卖出=${sellResp.status}`)
  : bad('在途资金', JSON.stringify(hkd))

ws.close()
console.log(results.join('\n'))
const failed = results.filter((r) => r.includes('FAIL')).length
console.log(failed === 0 ? '\nALL CHECKS PASSED' : `\n${failed} CHECK(S) FAILED`)
process.exit(failed === 0 ? 0 : 1)
