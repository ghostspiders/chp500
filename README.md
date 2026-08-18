# CHP 500 指数编制

一个对标 **标普 500（S&P 500）** 特质的中国宽基指数设计方案与编制系统：

- **全域资产覆盖**：A 股 + 港股中资 + 美股中概（ADR）全量（MVP 先落地 A 股）
- **行业动态平衡**：自由流通市值加权 + 集中度监控（可选软/硬上限）
- **严格的盈利筛选**：TTM 4 季累计净利为正 + 最近单季为正（GAAP 口径）
- **指数除数（Divisor）** 维护走势连续；**委员会裁量**层保留非全自动定稿

---

## 项目状态

| 阶段 | 状态 | 交付物 |
|---|---|---|
| Phase 1 | ✅ 已完成 | 方法论文档 + 落地蓝图 |
| Phase 2 | ✅ 核心已实现 | 数据层 + 编制流水线 + 指数序列 + 可视化看板 + 回测（**A + 港股中资 + 美股中概 ADR 跨市场 demo 跑通**）|

> 当前仓库已包含**可运行的编制流水线**（demo 模式）。完整覆盖 A+HK+ADR 需在生产环境（东财市值主机可达或配 Tushare）启用。

---

## 文档导航

- [CHP500指数编制方法论](docs/CHP500指数编制方法论.md) — 规则、参数、算法伪代码、技术难点
- [落地蓝图（Phase 2）](docs/落地蓝图.md) — 架构、模块、配置、可视化与回测

---

## 快速开始（demo 模式）

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 编制一期成分 + 指数序列（用本环境可用接口 + 静态份额参考）
python scripts/build_index.py --mode demo --as-of 2026-08-13

# 扩展宇宙：全量 A 股(真实名/价/利+近似股本)+港股/美股参考，推向 ~500 成分
python scripts/build_index.py --mode demo --universe expanded --as-of 2026-08-13 --out-dir outputs/expanded

# 绩效统计
python scripts/backtest.py
```

## 前后端分离服务（Web 看板）

架构：**Python 后端（FastAPI REST API）** + **独立前端（frontend/ 静态页，无构建步骤）**。
后端只读取已落盘的 `outputs/<universe>` 产物，`/api/summary` 秒级响应；`/api/build`
在后台任务中跑编制流水线，前端轮询状态后刷新。

```bash
# 启动服务（默认 127.0.0.1:8000）
# 注意：服务只读取已落盘的 outputs/ 产物，全新克隆请先跑一次上面的 build 命令
python scripts/serve.py
# 或自定义端口 / 对外暴露
python scripts/serve.py --port 8000 --host 0.0.0.0

# 浏览器打开 http://localhost:8000
```

- 首页（`/`）即看板：KPI、指数走势、行业/市场分布、TOP20、成分明细表
- 后端接口：`/api/health`、`/api/universes`、`/api/summary?universe=expanded`、
  `POST /api/build`、`/api/build/status?universe=expanded`
- 前端纯静态（`frontend/index.html` / `app.js` / `style.css`），Plotly.js 走 CDN，无需 npm/打包；
  若前端独立部署到其它域名/端口，CORS 已放开（`allow_origins=["*"]`）

输出在 `outputs/`：
- `constituents.csv` 本期成分（权重、行业、达标诊断、超上限标记）
- `index.csv` 价格指数 + 全收益指数序列
- `meta.json` 运行元信息

---

## 项目结构

```
chp500/
├── config.py / config.yaml       # 参数表（对应方法论 §10）
├── api/                         # 后端服务（FastAPI，前后端分离）
│   ├── main.py                  # REST API 路由 + 静态前端挂载
│   └── aggregate.py             # 读取 outputs/<universe> 组装前端汇总视图
├── data/
│   ├── adapters.py              # AkShare 适配（已验证可用接口 + demo 快照）
│   ├── universe.py              # 扩展宇宙（全量 A 股 + 港/美参考，~500 成分）
│   ├── merge.py                 # 跨市场同一主体去重（A > HK > US）
│   ├── fx.py                    # 多币种汇率（中行中间价 + 静态兜底）
│   └── cache.py                 # parquet 缓存（支持 TTL，cache_ttl_days）
├── filter/screens.py            # 6 大准入筛选 + 剔除检查
├── sector/classifier.py         # 行业映射（东财行业->GICS风格）+ 配比
├── weight/calculator.py         # 自由流通加权 + 单股上限（none/monitored/hard）
├── rebalance/scheduler.py       # 季度再平衡 + 缓冲 + 快速纳入
├── committee.py                 # 委员会裁量层（非全自动定稿）
└── index/series.py              # 除数 + 价格/全收益指数
frontend/                        # 静态前端：index.html / app.js / style.css（Plotly.js CDN）
scripts/                         # serve.py / build_index.py / backtest.py
tests/                           # 单元测试（pytest，离线运行）
data/demo_universe.csv           # 演示用静态份额参考（近似，标注 illustrative）
```

## 运行测试

```bash
pip install -r requirements-dev.txt
pytest
```

测试全部离线运行（不触网），覆盖筛选/权重/行业/合并/指数序列/委员会/缓存/API 等核心模块。

---

## 数据流

```
[Universe] → [6 大准入筛选] → [行业配比] → [权重+除数] → [委员会复核]
         → [指数序列(价格/全收益)] → [看板 / 回测]
```

---

## 数据源矩阵（三市场）

| 数据项 | A 股 | 港股中资 | 美股中概 |
|---|---|---|---|
| 历史日线（价格/成交量） | 新浪（前复权） | 新浪 | 新浪 |
| 总市值/流通市值/股本/IWF | 东财 push2 快照（真实） | 东财 push2 快照（真实） | 东财 push2 快照（真实） |
| TTM/最新单季净利 | 东财 yjbb_em（真实） | 东财港股财报（真实，按披露期拼 TTM） | SEC EDGAR（真实，权威） |
| 汇率 | 央行中间价（真实） | 同左 | 同左 |

- IWF 口径为**流通市值/总市值**近似（不含战略持股扣减）；`demo_universe.csv` 中 43 只蓝筹的人工核定自由流通值（如大行 0.2）优先覆盖（`shares_source=reference`）。
- **东财 push2 / 东财港股财报需国内网络或 VPN**；断网自动降级：A 股回落合成股本（`synthetic`）、港/美回落静态参考（`static`），产物列与看板状态行标注真实数据覆盖率，**绝不静默混用**。
- 港股财报金额按报告货币取值，中资股绝大多数以 CNY 报告，个别 USD 报告者（如中芯国际）存在未折算偏差；半年披露制下"最新单季"有季度披露时精确差分，否则按天数比例折算。美股 EDGAR 同理处理财年错位（如阿里 3 月财年）。
- 每个成分在 `constituents.csv` 中带 `shares_source` / `profit_source` 列（em/edgar/reference/static/synthetic）。

## 已知限制（重要，使用须知）

1. **数据源环境依赖**：新浪行情/汇率主机直连可达；东财 push2（市值/股本/港股财报）需国内网络或 VPN，断线自动降级并标记（见上方数据源矩阵）。**生产模式**（`--mode live`）仍未实现，可基于现有东财快照通路补齐。
2. **演示份额/盈利为近似（标注 illustrative）**：`data/demo_universe.csv`（A）、`data/demo_hk.csv`、`data/demo_us.csv`（中资港股/中概 ADR）中的总股本、IWF、TTM 净利润、行业为**近似参考**，用于跑通跨市场流程；生产应取实时自由流通股本与审计财报。HK/US 仅覆盖约 40+ 只代表性中资股（非全量）。扩展宇宙（`--universe expanded`）中 A 股的**公司名、现价、TTM 净利润、行业、流动性为真实值**（Sina / yjbb_em 抓取），仅**总股本与 IWF 为贴近真实 A 股规模分布的合成近似**（本沙箱不可达东财实时股本）。
3. **跨市场合并规则（去重、不重复计入）**：同一经济主体若同时在 A/港股/ADR 上市，按**主上市地优先级 A > HK > US** 计为单一成分（与 MSCI/FTSE 全球指数一致，避免跨市场重复计入）。仅在某市场挂牌的主体（如腾讯仅在港股、拼多多仅在美国）则按该市场计价并做多币种折算——此即「全域覆盖」的实现。实体映射键为各参考表的 `entity_id` 列。
4. **流动性阈值按市场分设**：A 股"6 个月累计成交量/自由流通股"的全额周转口径对大市值股（如大行）普遍失真，且扩展宇宙的自由流通股为合成近似，故 A 股下限仅设为 `0.02`（仅剔除近零成交的失真/僵尸样本）；港股/美股换手率结构更低，沿用 `0.30`，避免误杀中资港股/中概 ADR。分市场下限见 `config.yaml: liquidity_ratio_min_by_market`。盈利门槛（TTM 与最新单季净利>0）对全市场统一适用。
5. **指数序列处理**：历史行情用**前复权(qfq)** 价格以保证连续；跨源偶有缺失交易日，按「个股最新已知价向前填充(ffill)」对齐，避免成分缺数导致指数无谓跳变。全收益指数当前**未含分红**（== 价格指数），接分红数据后即分叉。
6. **Sina 限流/瞬时失败**：`stock_hk_daily`/`stock_us_daily` 偶发返回空表/缺字段，适配器已做 3 次重试与字段校验；历史行情按 `code` 缓存于 `.cache/`（parquet），有效期由 `cache_ttl_days`（默认 7 天）控制，过期自动重取，重跑可加速并提升稳定性。
7. **两种宇宙规模可选**：`--universe curated`（默认，约 60 只精选参考：A 蓝筹 + 港股中资 + 中概 ADR）与 `--universe expanded`（全量 A 股 + 港股/美股参考集，目标推向 ~500）。扩展模式下 A 股真实字段同上，但总股本/IWF 为合成近似，故点位与权重为**演示性**：用于验证大规模下的行业平衡、集中度与再平衡机制；成分数量与排序会随合成种子 (`seed=42`) 与行情日而变。扩展 A 股宇宙可落盘查看：`python -c "from chp500.data.universe import persist_expanded_a_universe; persist_expanded_a_universe('2026-08-13')"` → `data/demo_universe_expanded.csv`。

---

## 免责声明

本项目为**指数编制方法论研究与教学用途**，所有规则、参数、成分均为示例性设计，**不构成任何投资建议**。实际投资请参考官方授权指数与持牌机构。
