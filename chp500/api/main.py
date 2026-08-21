"""CHP 500 后端服务（FastAPI）。

架构：Python 后端（REST API） + 独立前端（frontend/ 静态页，通过 HTTP 调用本服务）。
- /api/*   : 数据接口（汇总、宇宙列表、健康检查、触发构建）
- /        : 静态前端（index.html / app.js / style.css）

本地启动：
    python scripts/serve.py
    # 或 uvicorn chp500.api.main:app --reload --port 8000
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

# 允许从仓库根导入 scripts.build_index
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_index import run as build_run  # noqa: E402
from . import aggregate  # noqa: E402
from ..data.sources import data_sources, validate as validate_sources  # noqa: E402

FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="CHP 500 Index API", version="1.0.0")

# 前后端分离：允许任意来源访问；前端同源部署、不发送凭证，故不启用 credentials
#（allow_origins=["*"] 与 allow_credentials=True 组合本身不合 CORS 规范）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 构建状态（进程内，仅用于前端轮询；生产应换为持久化任务队列）
_BUILDING: dict[str, str] = {}
_BUILDING_LOCK = threading.Lock()


def _set_build_status(universe: str, status: str) -> None:
    with _BUILDING_LOCK:
        _BUILDING[universe] = status


class BuildRequest(BaseModel):
    universe: str = "expanded"
    mode: str = "demo"  # live 模式尚未实现，见 build_index.build_snapshot
    as_of: str = ""
    markets: list[str] = ["A", "HK", "US"]

    @field_validator("universe")
    @classmethod
    def _check_universe(cls, v: str) -> str:
        if not aggregate.is_valid_universe_name(v):
            raise ValueError(
                "宇宙名仅允许字母/数字/下划线/连字符（长度 1~64），"
                f"收到：{v!r}"
            )
        return v

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v != "demo":
            raise ValueError(f"暂不支持 mode={v!r}：live 模式尚未实现，请使用 demo")
        return v

    @field_validator("markets")
    @classmethod
    def _check_markets(cls, v: list[str]) -> list[str]:
        allowed = {"A", "HK", "US"}
        upper = [m.upper() for m in v]
        bad = sorted(set(upper) - allowed)
        if bad:
            raise ValueError(f"市场代码仅支持 A/HK/US，收到：{', '.join(bad)}")
        if not upper:
            raise ValueError("markets 不能为空")
        return upper

    @field_validator("as_of")
    @classmethod
    def _check_as_of(cls, v: str) -> str:
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"as_of 须为 %Y-%m-%d 格式，收到：{v!r}")
        return v


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/universes")
def universes():
    return {"universes": aggregate.list_universes()}


@app.get("/api/sources")
def source_registry():
    """数据源注册表（config.yaml: data_sources）：每个源的接口明细与不可达策略。"""
    return {"sources": data_sources(), "problems": validate_sources()}


@app.get("/api/summary")
def summary(
    universe: str = Query(default="expanded", pattern=aggregate.UNIVERSE_NAME_PATTERN),
):
    try:
        return aggregate.load_summary(universe)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/build")
def build(req: BuildRequest, background_tasks: BackgroundTasks):
    out_dir = ROOT / "outputs" / req.universe
    as_of = req.as_of or datetime.now().strftime("%Y-%m-%d")

    def _job():
        try:
            build_run(
                as_of=as_of,
                mode=req.mode,
                out_dir=out_dir,
                markets=req.markets,
                universe=req.universe,
            )
            _set_build_status(req.universe, "done")
        except Exception as e:  # noqa: BLE001
            _set_build_status(req.universe, f"error: {e}")

    _set_build_status(req.universe, "running")
    background_tasks.add_task(_job)
    return {"status": "building", "universe": req.universe,
            "message": f"已在后台启动构建（输出目录 {out_dir}），完成后可轮询 /api/summary"}


@app.get("/api/build/status")
def build_status(
    universe: str = Query(default="expanded", pattern=aggregate.UNIVERSE_NAME_PATTERN),
):
    with _BUILDING_LOCK:
        status = _BUILDING.get(universe, "idle")
    return {"universe": universe, "status": status}


# ---- 常年运行：连续指数 / 再平衡历史 / 运行日志 / 基准 ----

@app.get("/api/universe/{universe}/history")
def universe_history(
    universe: str = Path(pattern=aggregate.UNIVERSE_NAME_PATTERN),
    from_date: str | None = Query(default=None, description="YYYY-MM-DD 下限"),
    to_date: str | None = Query(default=None, description="YYYY-MM-DD 上限"),
):
    try:
        df = aggregate.load_index_history(universe, from_date, to_date)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "universe": universe,
        "dates": df["date"].tolist(),
        "price_index": [float(x) for x in df["price_index"].tolist()],
        "total_return": [float(x) for x in df["total_return"].tolist()],
        "divisor": [float(x) for x in df["divisor"].tolist()],
        "rebalance_as_of": df["rebalance_as_of"].fillna("").tolist(),
    }


@app.get("/api/universe/{universe}/rebalances")
def universe_rebalances(universe: str = Path(pattern=aggregate.UNIVERSE_NAME_PATTERN)):
    try:
        return {"universe": universe, "rebalances": aggregate.list_rebalances(universe)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/universe/{universe}/rebalance/{as_of}")
def universe_rebalance_detail(
    universe: str = Path(pattern=aggregate.UNIVERSE_NAME_PATTERN),
    as_of: str = Path(),
):
    try:
        rows = aggregate.load_rebalance(universe, as_of)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not rows:
        raise HTTPException(status_code=404, detail=f"未找到再平衡 {as_of}")
    return {"universe": universe, "as_of": as_of, "constituents": rows}


@app.get("/api/universe/{universe}/runs")
def universe_runs(universe: str = Path(pattern=aggregate.UNIVERSE_NAME_PATTERN)):
    try:
        return {"universe": universe, "runs": aggregate.load_runs(universe)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/universe/{universe}/benchmarks")
def universe_benchmarks(universe: str = Path(pattern=aggregate.UNIVERSE_NAME_PATTERN)):
    try:
        return {"universe": universe, "benchmarks": aggregate.list_benchmarks(universe)}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/universe/{universe}/benchmark/{bench_id}")
def universe_benchmark_series(
    universe: str = Path(pattern=aggregate.UNIVERSE_NAME_PATTERN),
    bench_id: str = Path(),
):
    try:
        df = aggregate.load_benchmark_series(universe, bench_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if df.empty:
        raise HTTPException(status_code=404, detail=f"未找到基准 {bench_id}")
    return {
        "universe": universe,
        "bench_id": bench_id,
        "dates": df["date"].tolist(),
        "close": [float(x) for x in df["close"].tolist()],
    }


# ---- 静态前端（置于 API 路由之后，避免覆盖 /api） ----
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
