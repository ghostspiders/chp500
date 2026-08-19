# CHP 500 指数编制

一个对标 **标普 500（S&P 500）** 特质的中国宽基指数设计方案与编制系统：

- **全域资产覆盖**：A 股 + 港股中资 + 美股中概（ADR）全量（MVP 先落地 A 股）
- **行业动态平衡**：自由流通市值加权 + 集中度监控（可选软/硬上限）
- **严格的盈利筛选**：TTM 净利为正（腾讯 PE(TTM) 推导，与财报真值实测偏差<0.1%）
- **指数除数（Divisor）** 维护走势连续；**委员会裁量**层保留非全自动定稿

---

## 项目状态

| 阶段 | 状态 | 交付物 |
|---|---|---|
| Phase 1 | ✅ 已完成 | 方法论文档 + 落地蓝图 |
| Phase 2 | ✅ 核心已实现 | 数据层 + 编制流水线 + 指数序列 + 可视化看板 + 回测（**A + 港股中资 + 美股中概 ADR 跨市场 demo 跑通**）|

> 当前仓库已包含**可运行的编制流水线**（demo 模式）。市值/股本使用腾讯行情快照（直连可达）；原东财 push2 通路实测不可达，已移除。

---

## 文档导航

- [CHP500指数编制方法论](docs/CHP500指数编制方法论.md) — 规则、参数、算法伪代码、技术难点
- [落地蓝图（Phase 2）](docs/落地蓝图.md) — 架构、模块、配置、可视化与回测

---

## 快速开始（demo 模式）

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 编制一期成分 + 指数序列（真实数据源）
python scripts/build_index.py --mode demo --as-of 2026-08-13

# 扩展宇宙：全量 A 股(真实名/价/利+腾讯行情真实股本)+港股/美股参考，推向 ~500 成分
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
│   ├── spot.py                  # 腾讯行情快照（三市场市值/股本/PE 真实数据源）
│   ├── xueqiu.py                 # 雪球 A 股行业（直连，f10 affiliate_industry）
│   └── cache.py                 # parquet/JSON 缓存（支持 TTL，cache_ttl_days）
├── filter/screens.py            # 6 大准入筛选 + 剔除检查
├── sector/classifier.py         # 行业映射（中文行业->GICS风格）+ 配比
├── weight/calculator.py         # 自由流通加权 + 单股上限（none/monitored/hard）
├── rebalance/scheduler.py       # 季度再平衡 + 缓冲 + 快速纳入
├── committee.py                 # 委员会裁量层（非全自动定稿）
└── index/series.py              # 除数 + 价格/全收益指数
frontend/                        # 静态前端：index.html / app.js / style.css（Plotly.js CDN）
scripts/                         # serve.py / build_index.py / backtest.py
tests/                           # 单元测试（pytest，离线运行）
data/demo_universe.csv           # 精选 A 股成分定义（entity_id/上市日）；股本以真实数据源为准
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
| 总市值/流通市值/股本/IWF | 腾讯行情快照（真实） | 腾讯行情快照（真实） | 腾讯行情快照（真实） |
| TTM 净利 | 腾讯快照 PE(TTM) 推导（实测偏差<0.1%） | 腾讯快照 PE(TTM) 推导 | SEC EDGAR（真实，权威） |
| 汇率 | 央行中间价（真实） | 同左 | 同左 |

- IWF 口径为**流通市值/总市值**近似（不含战略持股扣减），A 股/美股由腾讯行情快照的流通市值提供；**港股腾讯快照无自由流通数据，流通市值以总市值近似（IWF 恒为 1）**，已知限制。`demo_universe.csv` 仅提供 A 股蓝筹的跨市场 `entity_id` 与上市日，不作股本/市值回落源。
- TTM 净利由 总市值/PE(TTM) 推导（腾讯快照）：与东财业绩报表真值的实测偏差<0.1%（PE 保留 2 位小数，舍入误差随 PE 增大略有放大）；亏损股 PE 为负、推导值为负，盈利筛选符号可靠。美股保留 SEC EDGAR 权威口径（含最新单季，并处理财年错位如阿里 3 月财年）。
- 每个成分在 `constituents.csv` 中带 `shares_source` / `profit_source` 列：`shares_source=tencent`（腾讯行情真实市值/股本）；`profit_source=tencent`（腾讯 PE 推导净利）或 `edgar`（SEC EDGAR 真实）；`missing`（真实源未覆盖，由筛选剔除）。
- **严格真实模式（无近似回落）**：所有市值/股本/IWF/TTM 净利（A/HK，PE 推导）来自腾讯行情快照，美股净利来自 SEC EDGAR，A 股行业来自雪球；**任一真实源不可达，构建直接报错终止**，绝不输出参考/合成/静态近似。本地 `demo_*.csv` 仅作成分定义与跨市场 `entity_id` 映射（去重）及**人工核定行业**（HK/US；免费行情接口无港美行业字段，属展示字段，见已知限制 #7）；A 股行业在选样后由雪球补齐，HK/US 上市日由 Sina 全量日线首日真实推导。

## 已接入数据源清单

实际接入的数据源如下（**未使用 Tushare**），每项均标注可达性与不可达时的处理。
**权威清单为 `config.yaml: data_sources`**（每个源一个配置条目：base_url/接口明细/覆盖字段/失败策略，运行时可经 `GET /api/sources` 查看；直连源的地址可在该配置中改址覆盖）——本节为摘要说明：

| 数据源 | 接入位置 | 覆盖字段 | 可达性 | 不可达时处理 |
|---|---|---|---|---|
| **AkShare / 新浪行情** | `adapters.fetch_a_quotes_sina` · `fetch_hk_us_hist` | A/HK/US 现价、前复权日线、成交量、ADTV | ✅ 直连可达 | — |
| **雪球个股信息** | `xueqiu.fetch_a_industry`（A 股直连） | A 股行业 | ✅ 可达（需 cookie 引导） | 归"其他" |
| **腾讯行情 qt.gtimg.cn** | `spot.fetch_spot`（A/HK/US） | 真实总/流通市值、总股本、流通股本、IWF、PE(TTM)->TTM 净利 | ✅ 直连可达 | **报错终止** |
| **SEC EDGAR** | `edgar.fetch_us_net_income` | 美股中概 ADR 的 TTM/单季净利润 | ✅ 免费免认证、权威 | — |
| **中行汇率** | `fx.currency_boc_sina` | USD/HKD 央行中间价（/100） | ✅ 直连可达 | — |
| **本地成分定义** | `data/demo_*.csv` | 成分定义、跨市场 `entity_id` 映射（去重，不含任何份额/盈利/行业/上市日近似值） | ✅ 本地 | — |

成分定义见 `data/demo_*.csv`（含 HK/US 人工核定行业列）；HK/US 上市日由 Sina 全量日线首日真实推导。

## 已知限制（重要，使用须知）

1. **数据源环境依赖**：新浪行情/腾讯行情快照/雪球/SEC EDGAR 当前网络实测均可达；原东财 push2（市值）与东财业绩 yjbb/港股财报（净利+行业）通路已移除（减少数据源接入：净利改由腾讯 PE(TTM) 推导、A 股行业改由雪球提供），不可达即报错终止（见上方「严格真实模式」）。**生产模式**（`--mode live`）仍未实现，可基于现有快照通路补齐。
2. **跨市场合并规则（去重、不重复计入）**：同一经济主体若同时在 A/港股/ADR 上市，按**主上市地优先级 A > HK > US** 计为单一成分（与 MSCI/FTSE 全球指数一致，避免跨市场重复计入）。仅在某市场挂牌的主体（如腾讯仅在港股、拼多多仅在美国）则按该市场计价并做多币种折算——此即「全域覆盖」的实现。实体映射键为各参考表的 `entity_id` 列。
3. **流动性阈值按市场分设**：A 股"6 个月累计成交量/自由流通股"的全额周转口径对大市值股（如大行）普遍失真，故 A 股下限仅设为 `0.02`（仅剔除近零成交的失真/僵尸样本）；港股/美股换手率结构更低，沿用 `0.30`，避免误杀中资港股/中概 ADR。分市场下限见 `config.yaml: liquidity_ratio_min_by_market`。盈利门槛（TTM 净利>0）对全市场统一适用；原「最新单季净利>0」检查随东财业绩源移除而取消（美股经 EDGAR 仍有单季口径）。
4. **指数序列处理**：历史行情用**前复权(qfq)** 价格以保证连续；跨源偶有缺失交易日，按「个股最新已知价向前填充(ffill)」对齐，避免成分缺数导致指数无谓跳变。全收益指数当前**未含分红**（== 价格指数），接分红数据后即分叉。
5. **Sina 限流/瞬时失败**：`stock_hk_daily`/`stock_us_daily` 偶发返回空表/缺字段，适配器已做 3 次重试与字段校验；历史行情按 `code` 缓存于 `.cache/`（parquet），有效期由 `cache_ttl_days`（默认 7 天）控制，过期自动重取，重跑可加速并提升稳定性。

6. **两种宇宙规模可选**：`--universe curated`（默认，约 60 只精选参考：A 蓝筹 + 港股中资 + 中概 ADR）与 `--universe expanded`（全量 A 股 + 港股/美股参考集，目标推向 ~500）。两种模式股本/市值均来自腾讯行情真实快照，点位与权重用于验证大规模下的行业平衡、集中度与再平衡机制；扩展 A 股宇宙可落盘查看：`python -c "from chp500.data.universe import persist_expanded_a_universe; persist_expanded_a_universe('2026-08-13')"` → `data/demo_universe_expanded.csv`。
7. **行业为展示字段（关键词近似）**：A 股行业取自雪球 affiliate_industry（如"白酒"）；**HK/US 行业来自参考表人工核定列**（免费行情/雪球接口实测均无港美行业字段，`demo_hk.csv`/`demo_us.csv` 提供）。统一经关键词映射到 GICS 风格板块，未覆盖的行业词归入"其他"（仅影响行业分布展示与 soft 上限模式，不影响选样与权重）。

---

## 免责声明

本项目为**指数编制方法论研究与教学用途**，所有规则、参数、成分均为示例性设计，**不构成任何投资建议**。实际投资请参考官方授权指数与持牌机构。
