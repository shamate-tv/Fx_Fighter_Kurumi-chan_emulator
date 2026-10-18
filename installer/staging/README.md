# 多市场 T+0 模拟交易原型

外汇 / 美股 / 港股三市场的**本地模拟交易**：真实市场规则（T+0 交易、T+1/T+2 交收、港股 lot_size 与四项费用），
所有成交只在本地记账，**不接任何实盘下单接口**。

> 明确不做：A 股、A 股 T+1 股票卖出限制。

架构说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 环境

| 项 | 要求 |
|---|---|
| Python | **3.11+**。本机请用 `py -3.14`（裸 `python` 是 32 位 3.8，装不了新 FastAPI） |
| Node | 仅前端需要（Phase 5） |

## 启动后端

```powershell
cd backend
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

默认使用 `MockProvider`（无网络、无 key），并启用**可快进的模拟时钟**。打开 <http://127.0.0.1:8000/docs> 即可下单。

## 跑测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest
```

零网络、零 key；交收用 `SimClock` 精确验证，不 sleep。

## 数据源（实时行情）

默认 `MARKET_DATA=mock`：三个市场全部走本地随机游走，不需要网络也不需要任何 key。

要接真实行情，复制 `.env.example` 为 `.env`，填入 Alpaca 的 Paper Key，并把 `MARKET_DATA` 改成 `live`：

```powershell
cd backend
Copy-Item .env.example .env
# 编辑 .env：填 ALPACA_API_KEY / ALPACA_SECRET_KEY，设 MARKET_DATA=live
```

| 市场 | 实时源 | 说明 |
|---|---|---|
| 外汇 | **biquote**（无需 key） | SignalR `hubs/tick` 推流；价格只取 `mid`（`last`/`volume` 恒为 0）；连不上自动转 REST 轮询 |
| 美股 | **Alpaca IEX**（`wss://stream.data.alpaca.markets/v2/iex`） | 只订阅行情，绝不下单；Key 只从 `.env` 读 |
| 港股 | 仍为 Mock | 真实源留到 Phase 4 |

关键安全语义：**闭市/陈旧的报价会被标记 `stale`，只用于画图，永远不参与撮合**。
biquote 在周末会反复推送周五的旧 tick，这条判定挡住了“用两天前的价格成交”。

任一数据源缺 key 或连不上都会自动降级到 Mock，`GET /api/health` 的 `provider_status` 会显示每个市场的真实状态：

```json
{"FX":{"name":"biquote","enabled":true,"mode":"signalr","last_error":null},
 "US":{"name":"alpaca","enabled":true,"feed":"iex","mode":"stream","last_error":null},
 "HK":{"name":"mock","enabled":true,"mode":"static"}}
```

## 配置（环境变量 / `.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `MARKET_DATA` | `mock` | `mock` 全本地；`live` 按市场接真实行情 |
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | 空 | Alpaca Paper Key；缺省则美股回退 Mock |
| `ALPACA_FEED` | `iex` | 行情源：`iex`（免费实时）/ `sip`（需订阅） |
| `BIQUOTE_ENABLED` | `true` | 外汇是否尝试 biquote |
| `QUOTE_STALE_AFTER` | `180` | 报价超过多少秒算陈旧（秒） |
| `INITIAL_USD` | `100000` | 美元初始资金 |
| `INITIAL_HKD` | `800000` | 港币初始资金 |
| `USE_SIM_CLOCK` | `1` | 模拟时钟（可 `POST /api/admin/advance-time` 快进） |
| `USDHKD_RATE` | `7.80` | 换汇基准价 |
| `SETTLEMENT_SCAN_INTERVAL` | `5` | 交收扫描间隔（秒） |

> `.env` 已在 `.gitignore` 里，真实密钥不会进仓库；`.env.example` 才是要提交的模板。

## 主要端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/markets` | 规则矩阵 + 标的清单（前端据此渲染，不硬编码） |
| GET | `/api/quote/{symbol}` · `/api/history/{symbol}` | 快照 / K 线 |
| GET | `/api/account` | 现金（available / pending / frozen）、购买力、持仓 |
| POST | `/api/orders` · DELETE `/api/orders/{id}` | 下单 / 撤单 |
| GET | `/api/orders` · `/api/trades` | 订单 / 成交（含费用明细） |
| POST | `/api/cash/convert` | USD↔HKD 换汇（跨市场再投资） |
| POST | `/api/admin/reset` · `/api/admin/advance-time` | 重置账户 / 快进时钟 |
| WS | `/ws` | tick / account / order / trade / settlement |

## 三市场规则速查

| 规则 | 外汇 | 美股 | 港股 |
|---|---|---|---|
| 交易 | T+0 | T+0 | T+0 |
| 交收 | 即时 | T+1 | T+2 |
| 卖出资金提现 | 即时 | T+1 | T+2 |
| 同市场再投资 | 即时 | 即时 | 即时 |
| 跨市场再投资 | 即时 | 交收后 | 交收后 |
| 最小单位 | 0.01 手 | 1 股 | lot_size（100/500/1000…） |
| 成本 | 点差 | 佣金（默认 0） | 印花税 0.1% + 征费 0.0027% + 交易费 0.00565% + 结算费 0.0042% |

现金语义：`available` 可提现/可跨市场，`pending` 仅本市场可再买，`buying_power = available + 本市场 pending`。
