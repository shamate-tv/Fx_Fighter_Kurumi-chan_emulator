# 多市场 T+0 模拟交易原型 · 架构计划

> 状态：待确认（Phase 0）。确认后从 Phase 1 开始写代码。
> 范围：外汇 / 美股 / 港股三市场 T+0 模拟交易，本地记账，无实盘下单。
> 明确排除：A 股、A 股 T+1 股票卖出限制。

---

## 0. 环境核查结论（已实测，2026-10-16）

| 项 | 结论 |
|---|---|
| Python | `py -3.14` → 3.14.3（**不能**用裸 `python`，那是 32 位 3.8.10） |
| 依赖可用性 | `pip install --dry-run` 全绿：fastapi 0.143.3 / starlette 1.8.0 / pydantic 2.14.1 / uvicorn 0.54.0 / websockets 17.2 / httpx 0.28.1 / pytest 9.1.1，cp314 wheel 齐全 |
| Node | v24.21.0；`npm` / `pnpm` 的 `.ps1` 被执行策略拦截 → 一律用 `npm.cmd` |
| npm registry | `https://registry.npmjs.org/` 可达：react 19.3.1 / lightweight-charts 5.2.1 / vite 8.3.4 |
| biquote REST | 可达，无需 key。实测 `GET /api/EURUSD` 返回 bid/ask/mid/spread |
| biquote WS | SignalR hub `https://biquote.io/hubs/tick`，`Subscribe([symbols])` → `ReceiveTick` |

实测 tick 关键字段（决定实现细节）：

```json
{"symbol":"EURUSD","bid":1.12390,"ask":1.12396,"last":0,"volume":0,
 "mid":1.12393,"spread":0.00006,"stale":true,"marketState":"closed",
 "quoteAgeSeconds":102744,"timestamp":"2026-10-16T20:58:43Z"}
```

→ **`last`/`volume` 恒为 0，价格一律取 `mid`**；`stale` + `marketState` 必须参与撮合准入判断（周末/收盘的旧价不得成交）。

三个环境陷阱，实施时必须绕开：
1. 裸 `python` 是 3.8-32，与 FastAPI 新版不兼容 → 所有脚本写死 `py -3.14`。
2. `npm`/`pnpm` 走 `.cmd`，或先 `Set-ExecutionPolicy -Scope Process Bypass`。
3. 工作目录 ACL 缺 `WRITE_OWNER`（沙箱 provision 会失败）；本会话已切 `danger-full-access`，若换会话再遇到同样报错，先跑 `diagnose-windows-sandbox-acl`。

---

## 1. 目录结构

```
D:\Fx_Fighter_Kurumi-chan_emulator\
├─ backend\
│  ├─ pyproject.toml
│  ├─ app\
│  │  ├─ main.py                  # FastAPI 装配 + lifespan
│  │  ├─ settings.py              # 配置（env / .env / config.json）
│  │  ├─ core\
│  │  │  ├─ enums.py              # Market / Side / OrderType / OrderStatus
│  │  │  ├─ models.py             # Pydantic 领域模型
│  │  │  ├─ symbols.py            # SymbolSpec 注册表（内置标的 + lot_size）
│  │  │  ├─ market_rules.py       # MarketRule 矩阵（按 market 索引）
│  │  │  ├─ fees.py               # FeeEngine
│  │  │  ├─ calendar.py           # 交易日历 + 交收日计算
│  │  │  └─ clock.py              # Clock 抽象（真实 / 可快进）
│  │  ├─ engine\
│  │  │  ├─ account.py            # 现金簿 + 持仓
│  │  │  ├─ orders.py             # 订单生命周期
│  │  │  ├─ matcher.py            # 撮合
│  │  │  ├─ settlement.py         # 在途资金释放
│  │  │  ├─ trading_engine.py     # 串行化 actor（唯一写入口）
│  │  │  └─ events.py             # 领域事件
│  │  ├─ providers\
│  │  │  ├─ base.py               # MarketDataProvider 抽象
│  │  │  ├─ mock.py               # 默认，零网络零 key
│  │  │  ├─ biquote.py            # 外汇（SignalR + REST 兜底）
│  │  │  ├─ alpaca.py             # 美股 paper
│  │  │  ├─ hk_real.py            # 港股可选适配器
│  │  │  └─ router.py             # 按 market 路由 + 降级
│  │  ├─ hub\
│  │  │  ├─ market_hub.py         # 订阅聚合、tick 缓存、驱动撮合
│  │  │  └─ ws_manager.py         # 连接与频道广播
│  │  ├─ api\
│  │  │  ├─ deps.py
│  │  │  ├─ routes_market.py
│  │  │  ├─ routes_trading.py
│  │  │  ├─ routes_admin.py
│  │  │  └─ routes_ws.py
│  │  └─ storage\
│  │     └─ snapshot.py           # 状态快照（JSON 原子写）
│  └─ tests\
│     ├─ conftest.py
│     ├─ test_mock_provider.py
│     ├─ test_symbols_lot_size.py
│     ├─ test_fees.py
│     ├─ test_matching.py
│     ├─ test_settlement.py
│     └─ test_cross_market.py
├─ frontend\
│  ├─ package.json
│  ├─ vite.config.ts              # dev 代理 /api 与 /ws 到 127.0.0.1:8000
│  └─ src\
│     ├─ main.tsx / App.tsx
│     ├─ api\client.ts            # REST 封装
│     ├─ ws\useStream.ts          # WS 订阅 hook（断线重连）
│     ├─ state\reducer.ts         # 账户/订单/成交/持仓单一状态树
│     ├─ components\
│     │  ├─ MarketTabs.tsx
│     │  ├─ CandleChart.tsx       # lightweight-charts v5
│     │  ├─ OrderPanel.tsx        # 限价/市价 + 数量步进
│     │  ├─ CashPanel.tsx         # 可用 / 在途 / 购买力 分列
│     │  ├─ PositionsTable.tsx
│     │  ├─ OpenOrders.tsx
│     │  └─ TradesTable.tsx       # 含费用明细展开
│     └─ lib\bars.ts              # tick → bar 聚合
├─ docs\ARCHITECTURE.md
└─ README.md                      # 启动方式（py -3.14 / npm.cmd）
```

后端与前端**不共享进程**：前端只认 `127.0.0.1:8000`，所有 key 只在后端。

---

## 2. 模块图

```
                    ┌─────────────────────────── 浏览器 ───────────────────────────┐
                    │  MarketTabs · CandleChart · OrderPanel · CashPanel          │
                    │  Positions · OpenOrders · Trades        (React 19 + LWC v5) │
                    └───────────▲───────────────────────────┬────────────────────┘
                       WS /ws   │                           │ REST /api/*
                    ┌───────────┴───────────────────────────▼────────────────────┐
                    │                    FastAPI (uvicorn)                       │
                    │  ┌──────────────┐   ┌───────────────────────────────┐       │
                    │  │ WsManager    │   │ routes_*  (market/trading/    │       │
                    │  │ 频道: tick   │   │            admin)             │       │
                    │  │   account    │   └──────────────┬────────────────┘       │
                    │  │   order/trade│                  │                        │
                    │  └──────▲───────┘                  │                        │
                    │         │ events                   │ place/cancel           │
                    │  ┌──────┴──────────────────────────▼────────────────────┐  │
                    │  │            TradingEngine (单 asyncio.Lock actor)      │  │
                    │  │  ┌────────┐ ┌─────────┐ ┌──────────┐ ┌────────────┐   │  │
                    │  │  │Matcher │ │ Account │ │Settlement│ │ FeeEngine  │   │  │
                    │  │  └────────┘ └─────────┘ └──────────┘ └────────────┘   │  │
                    │  │        ▲ on_quote()  ▲ on_timer()                     │  │
                    │  └────────┼─────────────┼───────────────────────────────┘  │
                    │  ┌────────┴─────────────┴───────────────────────────────┐  │
                    │  │ MarketHub：符号订阅聚合 + 最近 tick 缓存              │  │
                    │  └────────▲─────────────────────────────▲────────────────┘  │
                    │  ┌────────┴──────────┐        ┌─────────┴──────────────┐    │
                    │  │ ProviderRouter    │        │ Clock (real/sim)       │    │
                    │  │ market→provider   │        │ Calendar (交易日历)    │    │
                    │  │ 失败→Mock 降级     │        └────────────────────────┘    │
                    │  └────────▲──────────┘                                      │
                    └───────────┼─────────────────────────────────────────────────┘
                                │ 统一 MarketDataProvider
        ┌───────────────┬───────┴────────┬──────────────────┬──────────────────┐
        │ MockProvider  │ BiquoteProvider│ AlpacaProvider   │ HkRealProvider   │
        │ 随机游走      │ SignalR+REST   │ REST+/v2 IE       │ 可选适配器        │
        │ 三市场全覆盖  │ 外汇           │ 美股 paper        │ 无 key→Mock      │
        └───────────────┴────────────────┴──────────────────┴──────────────────┘
```

铁律：`TradingEngine` 是账户状态的**唯一写入口**，所有变更走队列串行执行，杜绝行情回调与用户下单的竞态。

---

## 3. 核心类型与接口签名

### 3.1 枚举与标的

```python
class Market(str, Enum):  FX = "FX"; US = "US"; HK = "HK"
class Side(str, Enum):    BUY = "BUY"; SELL = "SELL"
class OrderType(str, Enum): MARKET = "MARKET"; LIMIT = "LIMIT"
class OrderStatus(str, Enum):
    PENDING = "PENDING"      # 已接受，未成交
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"

@dataclass(frozen=True)
class SymbolSpec:
    symbol: str            # "EURUSD" | "AAPL" | "00700.HK"
    market: Market
    description: str
    currency: str          # 计价币种：USD / HKD
    min_qty: Decimal       # 最小下单量
    qty_step: Decimal      # 下单步长
    price_precision: int
    lot_size: int | None   # 港股必填；美股恒 1；外汇 None
    contract_size: Decimal # 外汇：1 手 = 100000 单位；股票 1

    def validate_qty(self, qty: Decimal) -> None: ...   # 抛 LotSizeError
```

`symbols.py` 内置标的（约 20 个，全部带合法 lot_size）：

| 市场 | 标的 | lot / step | 币种 |
|---|---|---|---|
| FX | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, XAUUSD | 手数 step=0.01，min=0.01 | USD |
| US | AAPL, MSFT, NVDA, TSLA, AMZN, SPY | lot=1, step=1（允许 1 股） | USD |
| HK | 00700.HK(100), 09988.HK(100), 00941.HK(500), 00883.HK(1000), 02318.HK(500), 00016.HK(1000) | 从 {1,50,100,500,1000,2000,5000,10000} 取 | HKD |

> 注：港股 `lot_size` 只能取上述 7 个合法值之一（如汇丰 `00005.HK` 实为 400 股/手，不在合法集合内，故不收录）。

### 3.2 行情

```python
class Quote(BaseModel):
    symbol: str; market: Market
    bid: Decimal; ask: Decimal; mid: Decimal
    ts: datetime
    stale: bool = False
    source: str              # "mock" | "biquote" | "alpaca" | "hk"

class Bar(BaseModel):
    symbol: str; interval: str
    open_time: datetime
    open: float; high: float; low: float; close: float
    volume: float = 0.0
```

### 3.3 订单 / 成交 / 账户

```python
class OrderRequest(BaseModel):
    symbol: str
    side: Side
    type: OrderType
    qty: Decimal = Field(gt=0)
    limit_price: Decimal | None = None      # MARKET 时禁止
    client_order_id: str | None = None

class Order(BaseModel):
    id: str; symbol: str; market: Market
    side: Side; type: OrderType
    qty: Decimal; filled_qty: Decimal = 0
    limit_price: Decimal | None
    status: OrderStatus
    avg_fill_price: Decimal | None = None
    fee: Decimal = 0
    created_at: datetime; updated_at: datetime
    reject_reason: str | None = None

class FeeComponent(BaseModel):
    name: str        # "stamp_duty" | "transaction_levy" | "trading_fee" | "settlement_fee" | "commission"
    rate: Decimal    # 费率或单位金额
    amount: Decimal
    currency: str

class Trade(BaseModel):
    id: str; order_id: str; symbol: str; market: Market
    side: Side; qty: Decimal; price: Decimal
    gross: Decimal                 # qty * price * contract_size
    fees: list[FeeComponent]
    fee_total: Decimal
    currency: str
    trade_date: date               # 交收基准日
    settles_on: date | None        # FX 为 None（即时）
    realized_pnl: Decimal | None   # 平仓时按加权成本计算
    ts: datetime

class Position(BaseModel):
    symbol: str; market: Market
    qty: Decimal                   # 负数为空头（外汇允许双向，股票仅多头）
    avg_cost: Decimal
    currency: str
    last_price: Decimal | None = None
    unrealized_pnl: Decimal | None = None
```

### 3.4 现金（见 §6 详细语义）

```python
@dataclass
class CashBucket:
    currency: str
    market: Market
    available: Decimal = Decimal("0")
    pending: Decimal = Decimal("0")        # 本市场可再买，不可跨市场/提现
    settlements: list[SettlementItem] = []  # 每笔在途的明细

@dataclass
class SettlementItem:
    id: str
    amount: Decimal
    currency: str
    market: Market
    trade_id: str
    release_at: datetime
    created_at: datetime

class AccountSnapshot(BaseModel):
    balances: dict[str, BalancesOut]        # 按币种：USD / HKD
    buying_power: dict[Market, Decimal]     # 本市场购买力 = available + 本市场 pending
    withdrawable: dict[Market, Decimal]     # = available
    positions: list[Position]
    equity: dict[str, Decimal]              # 按币种估值（含外汇浮动）
```

### 3.5 Provider 抽象（唯一对外接口）

```python
class MarketDataProvider(ABC):
    name: str
    market: Market

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    async def get_history(self, symbol: str, interval: str, limit: int = 200) -> list[Bar]: ...

    @abstractmethod
    def subscribe(self, symbols: Sequence[str]) -> AsyncIterator[Quote]:
        """返回异步迭代器；内部后台任务 + asyncio.Queue，消费方取消即退订。"""

    async def close(self) -> None: ...
```

约定：`subscribe` 产出的每个 tick **必须**携带 `stale`；`stale=True` 的 tick 只用于画图，不进入撮合。

### 3.6 引擎

```python
class TradingEngine:
    async def start(self) -> None
    async def place_order(self, req: OrderRequest) -> Order
    async def cancel_order(self, order_id: str) -> Order
    async def on_quote(self, q: Quote) -> None          # 行情驱动撮合
    async def on_timer(self, now: datetime) -> None      # 交收释放（默认 30s 一次）
    def snapshot(self) -> AccountSnapshot
    async def reset(self, initial: dict[str, Decimal]) -> None
```

事件（经 `MarketHub` → `WsManager` 广播）：

```python
class DomainEvent: ts: datetime
class QuoteEvent(DomainEvent):     quote: Quote
class OrderEvent(DomainEvent):     order: Order
class TradeEvent(DomainEvent):     trade: Trade
class AccountEvent(DomainEvent):   account: AccountSnapshot
class SettlementEvent(DomainEvent): released: SettlementItem
```

---

## 4. 数据流

**链路 A：行情 → 撮合 → 账务 → 推送**
```
Provider.subscribe ─► MarketHub._on_tick
   ├─ 写 tick 缓存（图表快照）
   ├─ 广播 QuoteEvent ─────────────► WS: tick 频道
   └─ stale? 否 ─► TradingEngine.on_quote(q)
                      ├─ Matcher: 遍历该 symbol 挂单，bid/ask 触价判定
                      ├─ 命中 ─► FeeEngine.calculate() 
                      │          ├─ Account 扣/加现金
                      │          ├─ 卖出入 pending（或 FX 直接 available）
                      │          ├─ 更新持仓（加权成本 / 已实现盈亏）
                      │          └─ Trade + Order(FILLED) 落库
                      └─ 事件 ─► WS: order / trade / account
```

**链路 B：用户下单**
```
POST /api/orders ─► TradingEngine.place_order (排队)
   ├─ 校验：symbol 存在 / market 规则 / lot_size / qty 步长
   ├─ 校验：BUY 购买力 = buying_power[market]；SELL 持仓 ≥ qty（T+0，无锁）
   ├─ 市价：立即用最新 tick 的 ask/bid 成交（无 tick → 拒绝）
   ├─ 限价：入挂单簿，回 PENDING
   └─ 事件 ─► WS 即刻推送，前端乐观更新后以服务端为准
```

**链路 C：交收释放**
```
on_timer(30s) ─► Settlement.scan(now)
   选出 release_at <= now 的 SettlementItem
   pending -= amount；available += amount
   推送 AccountEvent + SettlementEvent
```

---

## 5. 市场规则矩阵的落地方式

### 5.1 结构

规则表**按 `Market` 索引**，`SymbolSpec.market` 是唯一入口；配置可覆盖但结构固定：

```python
class FeeKind(str, Enum): PERCENT = "PERCENT"; PER_SHARE = "PER_SHARE"; FLAT = "FLAT"
class LotPolicy(str, Enum): SHARES = "SHARES"; LOT_TABLE = "LOT_TABLE"; FX_UNITS = "FX_UNITS"
class Settlement(str, Enum): INSTANT = "INSTANT"; T_N = "T_N"

@dataclass(frozen=True)
class FeeRule:
    name: str
    kind: FeeKind
    value: Decimal
    currency: str
    side: Side | None = None          # None = 双边
    min_amount: Decimal = Decimal("0")
    round_up_to_whole: bool = False   # 港股印花税向上取整到整元

@dataclass(frozen=True)
class MarketRule:
    market: Market
    trade_mode: Literal["T0"]
    settlement: Settlement
    settlement_days: int              # 0 / 1 / 2（业务日）
    sell_proceeds_release: Literal["instant", "settle"]
    allow_same_market_reinvest: bool  # 恒 True：pending 可买本市场
    cross_market_reinvest: Literal["instant", "after_settlement"]
    lot_policy: LotPolicy
    allowed_lot_sizes: frozenset[int] | None
    price_limit: Literal["NONE", "MARKET_CIRCUIT", "VCM"]
    flags: dict[str, bool]            # us_circuit_breaker / hk_vcm（Phase 2 用）
    fees: tuple[FeeRule, ...]
```

### 5.2 矩阵 → 常量（`market_rules.py`）

| 规则项 | FX | US | HK |
|---|---|---|---|
| `trade_mode` | `T0` | `T0` | `T0` |
| `settlement` | `INSTANT` | `T_N` | `T_N` |
| `settlement_days` | 0 | 1 | 2 |
| `sell_proceeds_release` | `instant` | `settle` | `settle` |
| `allow_same_market_reinvest` | True | True | True |
| `cross_market_reinvest` | `instant` | `after_settlement` | `after_settlement` |
| `lot_policy` | `FX_UNITS`（step 0.01 手） | `SHARES`（1 股，允许零碎股） | `LOT_TABLE` |
| `allowed_lot_sizes` | None | None | `{1,50,100,500,1000,2000,5000,10000}` |
| `price_limit` | `NONE` | `NONE` + `flags{circuit_breaker}` | `NONE` + `flags{vcm}` |
| 费用 | 点差（含在 bid/ask） | 佣金可配 0 | 印花税 0.1% 双边、交易征费 0.0027%、交易费 0.00565%、结算费 0.0042%、佣金可选 |

`price_limit` 与 `flags` 在 Phase 1 只做**数据位**（返回给前端与规则展示），不改撮合行为；Phase 2 再实现熔断/VCM 状态机。

### 5.3 校验顺序（`TradingEngine.place_order`）

1. symbol ∈ 注册表 → 否则 `UNKNOWN_SYMBOL`
2. `qty` 满足 `SymbolSpec.validate_qty` → `LOT_SIZE_VIOLATION`（含港股合法 lot 集合、美股整数股、外汇 0.01 手步长）
3. `LIMIT` 必须有 `limit_price`，且 > 0、精度合规
4. 购买力 / 持仓（见 §6 三量）
5. 行情可用性：市价单无可用非 stale tick → `NO_MARKET_DATA`
6. 通过 → 落单

---

## 6. 现金模型：T+0 与 T+1/T+2 共存的关键

这是整个原型最容易做错的地方。矩阵里三条规则互相牵制：

- 卖出资金：提现 T+1（美）/ T+2（港），**但** 再买同市场即时；
- 跨市场再投资：要等交收释放。

所以现金必须拆成**三个量**，而不是一个 `available_cash`：

```
buying_power[market]  = available + pending(该 market)      ← 同市场买入用这个
withdrawable[market]  = available                            ← 提现 / 跨市场换汇用这个
pending(cash)         = Σ 未释放 settlement                 ← 对外展示"在途"
```

按 `(currency, market)` 分桶：

```python
buckets: dict[tuple[str, Market], CashBucket]
```

- **买入**（任何市场）：扣 `buying_power[market]` 口径
  → 优先扣 `pending[market]`，不足扣 `available`
- **卖出**：`settlement=INSTANT`（FX）→ 直接进 `available`；
  `T_N`（美/港）→ 生成 `SettlementItem` 进 `pending[market]`
- **跨市场**：只能动 `available`（HKD/USD 之间需显式换汇，见下）
- **释放**：`on_timer` 把到期的 `SettlementItem` 从 `pending` 挪到 `available`

换汇（跨市场再投资）：`CurrencyConverter` 用 provider 实时 `USDHKD` mid ± 可配点差（默认 10bp），
显式 `POST /api/cash/convert`，不做下单时隐式换汇 —— 避免"下个单莫名少了钱"。

> 若你倾向"统一 USD 记账"，模型退化为单桶，但会丢失"同市场可再买、跨市场不行"的区分能力。**推荐分币种分桶。**

外汇账户特殊性：`available` 以 margin 口径计，持仓按名义价值 × 杠杆（默认 1:20）占用保证金；
空头允许（`qty` 可为负），与股票的仅多头区分。原型阶段杠杆可配置，`equity` 展示浮动盈亏。

---

## 7. 费用引擎

```python
class FeeEngine:
    def calculate(self, spec: SymbolSpec, side: Side, qty: Decimal,
                  price: Decimal, is_sell_to_close: bool) -> list[FeeComponent]
```

- 成交额 `gross = qty * price * contract_size`
- `PERCENT`：`gross * rate`；`PER_SHARE`：`qty * rate`；`FLAT`：定额
- `min_amount` 下限；`round_up_to_whole` 生效（港股印花税向上取整到整元，最小 1 元）
- 港股四费按矩阵值：`stamp_duty=0.1%` 双边、`transaction_levy=0.0027%` 双边、
  `trading_fee=0.00565%` 双边、`settlement_fee=0.0042%` 双边
- 美股默认 `commission=0`（可配 `PER_SHARE` 或 0），`SEC fee` 预留但默认关
- 外汇无显式佣金，成本体现在 bid/ask 点差

费用币种 = 标的币种；现金扣减按 `(currency, market)` 桶。

---

## 8. 交收与时钟

```python
class Clock(Protocol):
    def now(self) -> datetime: ...          # UTC

class RealClock: ...
class SimClock:                             # 测试与"游戏快进"用
    def now(self) -> datetime
    def advance(self, delta: timedelta) -> None
```

- `Calendar.next_business_day(d)`：跳过周末；节假日表按市场可配（`holidays.json`，Phase 1 内置近两年美/港假期，缺表则只跳周末）
- 美股 `settles_on = next_business_day(trade_date, 1)`；港股 `+2`；外汇 `None`
- 释放时刻取该日 `00:00:00`（可配 `release_time`）
- **测试用 `SimClock`** 精确验证 T+1 / T+2，不 sleep、不 mock 系统时间

---

## 9. Provider 层

| Provider | 数据 | key | 降级 |
|---|---|---|---|
| `MockProvider` | 随机游走（三市场全覆盖），按各市场点差生成 bid/ask，支持历史 K 线合成 | 无 | — |
| `BiquoteProvider` | SignalR `hubs/tick` 推流 + REST 兜底；价格取 `mid`；`stale`/`marketState` 透传 | 无 | hub 连不上 → 3s REST 轮询 → 再不行 → Mock |
| `AlpacaProvider` | `data.alpaca.markets/v2/...` IEX feed，orders 不调用（只读行情） | paper key | 无 key → Mock（UI 标注） |
| `HkRealProvider` | `free-quote`/`QOS` 风格 HTTP+WS 适配器（形态参考：`GET /quote?symbol=` + `ws://.../subscribe`） | 可选 | 无 key → Mock |

统一约束：
- **只用行情接口，绝不调用任何下单端点**（Alpaca 仅 `GET /v2/stocks/*/quotes|bars`）
- key 仅从后端 `settings`（env / `.env`）读取；前端零接触
- 启动时打印每个市场的实际数据源与降级原因

Mock 随机游走：GBM，`sigma` 按市场配置（FX 更低、股票更高）；生成 tick 时同时产出
`bid = mid - spread/2`、`ask = mid + spread/2`；`stale=False`；历史 K 线由同参数生成，保证重启后图表连续。

---

## 10. 对外接口

### REST

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康检查 + 各市场数据源状态 |
| GET | `/api/markets` | 市场规则矩阵 + 标的清单（前端渲染规则提示） |
| GET | `/api/quote/{symbol}` | 单标的快照 |
| GET | `/api/history/{symbol}?interval=1m&limit=200` | K 线 |
| GET | `/api/account` | 账户（balances / buying_power / withdrawable / positions / equity） |
| GET | `/api/orders?status=open\|all` | 订单 |
| POST | `/api/orders` | 下单 |
| DELETE | `/api/orders/{id}` | 撤单 |
| GET | `/api/trades` | 成交记录（含费用明细） |
| POST | `/api/cash/convert` | 换汇（USD↔HKD） |
| POST | `/api/admin/reset` | 重置账户 |
| POST | `/api/admin/advance-time` | 模拟时钟快进（仅 SimClock） |

统一错误体：`{"error": {"code": "...", "message": "...", "details": {...}}}`；
`LOT_SIZE_VIOLATION` / `INSUFFICIENT_BUYING_POWER` / `NO_MARKET_DATA` / `UNKNOWN_SYMBOL` 等错误码。

### WebSocket `/ws`

客户端 → 服务端：

```json
{"type":"subscribe","channels":["tick","account","order","trade"],"symbols":["00700.HK"]}
{"type":"unsubscribe","symbols":["00700.HK"]}
{"type":"ping"}
```

服务端 → 客户端：

```json
{"type":"hello","server_time":"...","data_sources":{"FX":"biquote","US":"mock","HK":"mock"}}
{"type":"tick","symbol":"00700.HK","bid":380.2,"ask":380.6,"mid":380.4,"stale":false,"ts":"..."}
{"type":"account","balances":{"HKD":{"available":...,"pending":...}},"buying_power":{"HK":...},"positions":[...]}
{"type":"order","order":{...}}
{"type":"trade","trade":{...}}
{"type":"settlement","item":{...}}
```

`MarketHub` 只向各连接推送其订阅过的 symbol，账户频道按连接全量推（单账户原型）。

---

## 11. 前端

- Vite + React 19 + TS；`vite.config.ts` 里 `server.proxy` 把 `/api`、`/ws`（`ws:true`）转发到 `127.0.0.1:8000`，**避免 CORS**
- 图表：`lightweight-charts` **v5**（注意 v5 用 `chart.addSeries(CandlestickSeries, opts)`，`addCandlestickSeries` 已移除），
  历史走 REST，实时 tick 由 `lib/bars.ts` 聚合进最后一根 bar
- 状态：`useReducer` 单一状态树 `{quotes, account, positions, orders, trades}`；WS 事件即 action
- 布局：
  - 顶栏：市场 Tab（外汇 / 美股 / 港股）+ 合约选择 + 数据源徽标（live/mock）
  - 左：K 线（interval 切换 1m/5m/1h/1d）
  - 右：`CashPanel`（**可用 / 在途 / 本市场购买力** 三行分列，按币种分组）+ `OrderPanel`
  - 下：`PositionsTable` / `OpenOrders`（可撤）/ `TradesTable`（行展开看费用分项）
- 下单面板按当前标的 `lot_size` 动态显示"最小 100 股 / 步长 100 股"提示，前端做一次预校验（后端为准）
- 规则提示条：卖出后 `T+1/T+2` 到账、同市场可即时再买、跨市场需换汇 —— 直接从 `/api/markets` 渲染，不硬编码

---

## 12. 测试清单（pytest + pytest-asyncio）

| 文件 | 覆盖 |
|---|---|
| `test_mock_provider.py` | 随机游走 bid<ask、spread 合理、历史长度正确、**不触网**（无 requests/httpx 调用）、同 seed 可复现 |
| `test_symbols_lot_size.py` | 港股 7 个合法 lot 全部通过、非法值（如 300）拒绝；美股 1 股通过、0.5 股拒绝；外汇 0.01 手通过、0.005 拒绝 |
| `test_fees.py` | 港股 100k HKD 成交 → 印花税 100（取整）、征费 2.7、交易费 5.65、结算费 4.2；美股佣金 0；外汇无显式费 |
| `test_matching.py` | 限价买单在 `ask<=limit` 成交、`ask>limit` 不成交；市价单立即成交；**T+0：当日买入当日卖出成功**；无行情时市价单拒绝 |
| `test_settlement.py` | 港股卖出 → pending 增加、`SimClock` +1 日不释放、+2 日释放；美股 +1 日释放；外汇即时 available |
| `test_cross_market.py` | 港股卖出后：可继续买港股（用 pending）；买美股被拒（buying_power[US] 不变）；换汇后可通过 |

每个测试都用 `SimClock` + `MockProvider`，全套跑通应 < 2s，零网络。

---

## 13. 里程碑（与你给的交付顺序对齐）

| Phase | 内容 | 完成判据 |
|---|---|---|
| 0 | 本文档 | 你确认 |
| 1 | 后端骨架 + MockProvider + 撮合/费用/交收 + 测试 | `pytest` 全绿；`/api/account` 与 `/api/orders` 可 curl 跑通一单 |
| 2 | BiquoteProvider | `--market-data live` 下 FX tick 走真实 mid，stale 不入撮合；断网自动降级 Mock |
| 3 | AlpacaProvider | paper key 下美股 quote/bars 真数据；无 key 明确提示并回退 |
| 4 | HkRealProvider（可选） | 接口就绪即可，无 key 保持 Mock |
| 5 | 前端 | 三市场切换、K 线、下单、持仓/挂单/成交、可用/在途分列；本地 `npm.cmd run dev` 可用 |

每 Phase 结束暂停等你确认。

---

## 14. 已确认的决策（2026-10-16）

1. **现金模型**：✅ 分币种分桶。落地为 `total / margin_frozen / order_frozen / pending[(ccy, market)]`，
   对外 `available = total − frozen − pending`、`buying_power = available + 本市场 pending`。
2. **初始资金**：✅ USD 100,000 + HKD 800,000（`INITIAL_USD` / `INITIAL_HKD` 可改）。
3. **交收日历精度**：✅ 只跳周末，节假日表留空位（`core/calendar.py` 的 `HOLIDAYS`）。
4. **外汇杠杆**：✅ 1:20，开仓冻结保证金、平仓释放，浮动盈亏计入 `equity`。
5. **前端构建**：✅ 项目内 `npm.cmd` 安装 node_modules（registry 已实测可达）。

## 15. Phase 1 实现记录（2026-10-16）

已落地：
- `core/`：enums / models / symbols（18 标的）/ market_rules（矩阵）/ fees / calendar / clock
- `engine/`：account（现金簿 + 在途 + 保证金 + 持仓）/ matcher（挂单簿 + 触价）/ trading_engine（唯一写入口）/ events
- `providers/`：base + mock（三市场全覆盖）+ router（按市场路由，天然支持降级）
- `hub/`：market_hub（订阅聚合 + 预热 + 交收定时器）/ ws_manager（五频道广播）
- `api/`：market / trading / admin / ws 四组路由
- `tests/`：6 个文件 53 项，全绿，零网络

实现中补上的两个不变量（原计划没写细）：
1. `available` 必须扣除 `pending`，否则在途资金可被跨市场使用；
2. 买入时优先消耗本市场 `pending` 额度，否则同一笔钱会被重复计入购买力。

验证方式：`pytest` 53 passed；uvicorn 起服务后 HTTP 全流程（下单/交收/换汇/错误体）+ WS 推送（hello/tick/order/trade/account）均实测通过。

## 16. Phase 2 实现记录（2026-10-18）：真实行情接入

新增四个模块：

| 文件 | 职责 |
|---|---|
| `providers/signalr.py` | 手写 SignalR JSON 协议客户端（negotiate → WS → handshake → invoke → `0x1E` 分帧 → 心跳）。biquote 会一帧粘多条、也会半条半条地来，所以分帧做成纯函数便于测试。 |
| `providers/biquote.py` | 外汇。SignalR 推流，失败 3 次转 REST 轮询，再失败交给 fallback。**只取 `mid`**（实测 `last`/`volume` 恒 0，见下）。 |
| `providers/alpaca.py` | 美股。alpaca-py 的 `StockDataStream(feed=iex)` 跑在独立线程（它内部 `asyncio.run`），报价经 `call_soon_threadsafe` 回主循环；快照/历史走 `StockHistoricalDataClient`。 |
| `settings.py` | 改为经 `python-dotenv` 读 `.env`；新增 `ALPACA_*`、`BIQUOTE_ENABLED`、`QUOTE_STALE_AFTER`。 |

### 实测到的两个关键事实

1. **`mid` 是唯一可用价格**：实测 biquote 帧
   `{"symbol":"EURUSD","bid":1.12390,"ask":1.12396,"last":0.00000,"volume":0,...,"mid":1.12393}`。
2. **闭市会反复推旧帧**：SignalR 帧里**没有** `stale`/`marketState` 字段（只有 REST 有），
   所以 Provider 必须按 `timestamp` 年龄自行判 stale；周末收到的是周五 20:58 的帧。
   这条判定是三处一致的行为：Provider 标 `stale` → MarketHub 只广播不喂引擎 → 引擎 `on_quote` 二次拒绝 stale。
   预热阶段也补了一刀：stale 快照不进 `engine.last_quotes`，否则市价单会拿旧价成交。

### 降级链

```
biquote: SignalR ──失败×3──► REST 轮询 ──失败──► MockProvider
alpaca : IEX 流 ──无 key/连不上──► MockProvider（REST 快照同样回退）
```

`/api/health.provider_status` 暴露每路的 `mode`（`signalr`/`rest`/`stream`/`fallback`/`static`）与 `last_error`，
不含任何密钥。

### Topic 2 的联动测试（`tests/test_provider_engine_link.py`）

用 `StubProvider` 走**完整生产链路**（Router → Hub → 引擎），验证：

- 外汇 `mid=1.0500` 的 tick 触发了挂在 `1.0501` 的限价买单成交（成交价取 `ask=1.05004`）
- 美股 `mid=180.00` 的 tick 触发了挂在 `180.01` 的限价单成交
- `stale=True` 的 tick **不会**触发成交，且不污染 `last_quotes`
- `hub.close()` 后不残留任务

另加两条"真实帧直驱"测试：用实测抓到的 biquote `ReceiveTick` 帧与 Alpaca quote 帧的原始 payload，
经各自 Provider 的解析函数转成 `Quote` 后喂给引擎，同样能成交。这样"协议解析正确"和"能驱动撮合"同时被覆盖。

测试全程 `asyncio.wait_for` + 轮询超时，不存在无限等待；`pytest` 共 **79 项**，零网络。

### Live 实测结果（2026-10-18，周末）

```json
data_sources: {"FX":"biquote","US":"alpaca","HK":"mock"}
FX: {"name":"biquote","enabled":true,"mode":"signalr","last_error":null}
US: {"name":"alpaca","enabled":true,"feed":"iex","mode":"stream","last_error":null}
EURUSD: {"bid":"1.12390","ask":"1.12396","mid":"1.12393","stale":true,"source":"biquote","ts":"2026-10-16T20:58:43Z"}
EURUSD 1h bars: 真实 OHLC（17:00 1.12269 → 20:00 1.12392）
```

周末闭市 → 两侧报价 `stale=true` → 撮合被正确挡住（这正是设计意图，不是故障）。
工作日的开市时段应能看到 `stale=false` 的实时 tick 驱动挂单成交。


