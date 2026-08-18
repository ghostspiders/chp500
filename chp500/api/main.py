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

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
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


# ---- 静态前端（置于 API 路由之后，避免覆盖 /api） ----
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
