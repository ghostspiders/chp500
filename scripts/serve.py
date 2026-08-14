"""启动 CN 500 后端 + 前端服务。

用法:
  python scripts/serve.py [--port 8000] [--host 0.0.0.0]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保仓库根（含 cn500 包）在路径中
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from cn500.api.main import app


def main():
    ap = argparse.ArgumentParser(description="CN 500 服务（FastAPI + 静态前端）")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
