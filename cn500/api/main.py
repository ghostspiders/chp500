"""CN 500 后端服务（FastAPI）。

架构：Python 后端（REST API） + 独立前端（frontend/ 静态页，通过 HTTP 调用本服务）。
- /api/*   : 数据接口（汇总、宇宙列表、健康检查、触发构建）
- /        : 静态前端（index.html / app.js / style.css）

本地启动：
    python scripts/serve.py
    # 或 uvicorn cn500.api.main:app --reload --port 8000
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 允许从仓库根导入 scripts.build_index
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_index import run as build_run  # noqa: E402
from . import aggregate  # noqa: E402

FRONTEND_DIR = ROOT / "frontend"

app = FastAPI(title="China 500 Index API", version="1.0.0")

# 前后端分离：允许跨域（前端若独立部署到其它端口也可访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 构建状态（进程内，仅用于前端轮询；生产应换为持久化任务队列）
_BUILDING: dict[str, str] = {}


class BuildRequest(BaseModel):
    universe: str = "expanded"
    mode: str = "demo"
    as_of: str = ""
    markets: list[str] = ["A", "HK", "US"]


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/universes")
def universes():
    return {"universes": aggregate.list_universes()}


@app.get("/api/summary")
def summary(universe: str = "expanded"):
    try:
        return aggregate.load_summary(universe)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/build")
def build(req: BuildRequest, background_tasks: BackgroundTasks):
    out_dir = ROOT / "outputs" / req.universe
    as_of = req.as_of or datetime.now().strftime("%Y-%m-%d")
    _BUILDING[req.universe] = "running"

    def _job():
        try:
            build_run(
                as_of=as_of,
                mode=req.mode,
                out_dir=out_dir,
                markets=[m.upper() for m in req.markets],
                universe=req.universe,
            )
            _BUILDING[req.universe] = "done"
        except Exception as e:  # noqa: BLE001
            _BUILDING[req.universe] = f"error: {e}"

    background_tasks.add_task(_job)
    return {"status": "building", "universe": req.universe,
            "message": f"已在后台启动构建（输出目录 {out_dir}），完成后可轮询 /api/summary"}


@app.get("/api/build/status")
def build_status(universe: str = "expanded"):
    return {"universe": universe, "status": _BUILDING.get(universe, "idle")}


# ---- 静态前端（置于 API 路由之后，避免覆盖 /api） ----
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
