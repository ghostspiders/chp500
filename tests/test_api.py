"""API（FastAPI）端点测试：健康、汇总、构建参数校验、CORS、状态。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chp500.api import main as api_main


@pytest.fixture
def client(monkeypatch, tmp_path):
    # 隔离产物目录并替换真实构建，避免触网
    monkeypatch.setattr(api_main.aggregate, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_main, "build_run", lambda **kw: None)
    return TestClient(api_main.app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_summary_404_when_not_built(client):
    r = client.get("/api/summary", params={"universe": "expanded"})
    assert r.status_code == 404
    assert "未找到" in r.json()["detail"]


def test_summary_rejects_path_traversal(client):
    r = client.get("/api/summary", params={"universe": "../etc"})
    assert r.status_code == 422


def test_summary_rejects_empty_universe(client):
    r = client.get("/api/summary", params={"universe": ""})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"universe": "../x"}, "宇宙名"),
        ({"universe": "a/b"}, "宇宙名"),
        ({"mode": "live"}, "live"),
        ({"mode": "anything"}, "mode"),
        ({"markets": ["XX"]}, "市场代码"),
        ({"markets": []}, "markets"),
        ({"as_of": "2026/08/17"}, "as_of"),
        ({"as_of": "not-a-date"}, "as_of"),
    ],
)
def test_build_rejects_invalid_payloads(client, payload, fragment):
    body = {"universe": "expanded", "mode": "demo", "as_of": "", "markets": ["A", "HK", "US"]}
    body.update(payload)
    r = client.post("/api/build", json=body)
    assert r.status_code == 422, payload
    assert fragment in r.text


def test_build_accepts_valid_request_and_marks_done(client):
    r = client.post("/api/build", json={"universe": "expanded", "mode": "demo",
                                        "as_of": "2026-08-15", "markets": ["a", "hk", "us"]})
    assert r.status_code == 200
    assert r.json()["status"] == "building"
    # TestClient 会在响应后同步执行后台任务（build_run 已被替换为空操作）
    s = client.get("/api/build/status", params={"universe": "expanded"})
    assert s.status_code == 200
    assert s.json()["status"] == "done"


def test_build_status_defaults_idle_and_rejects_bad_name(client):
    s = client.get("/api/build/status", params={"universe": "whatever1"})
    assert s.json()["status"] == "idle"
    bad = client.get("/api/build/status", params={"universe": "../x"})
    assert bad.status_code == 422


def test_cors_allows_any_origin_without_credentials(client):
    r = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert r.headers.get("access-control-allow-origin") == "*"
    # allow_credentials 已移除（与通配 origin 组合不合 CORS 规范）
    assert "access-control-allow-credentials" not in r.headers


def test_root_serves_frontend(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
