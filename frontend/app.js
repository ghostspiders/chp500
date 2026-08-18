"use strict";

const PLOT_LAYOUT = { margin: { t: 10, l: 40, r: 10, b: 30 }, font: { size: 12 } };
const PLOT_CFG = { responsive: true, displayModeBar: false };
const pct = (x) => (x * 100).toFixed(2) + "%";

const $ = (id) => document.getElementById(id);
const statusEl = $("status");

function setStatus(msg, kind) {
  statusEl.textContent = msg || "";
  statusEl.className = "status" + (kind ? " " + kind : "");
}

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
  return res.json();
}

async function loadSummary() {
  const universe = $("universe").value;
  setStatus("加载中…", "info");
  try {
    const d = await api("/api/summary?universe=" + encodeURIComponent(universe));
    render(d);
    const cover = (d.real_shares_ratio != null)
      ? " · 真实股本 " + Math.round(d.real_shares_ratio * 100) + "%" +
        " / 真实利润 " + Math.round((d.real_profit_ratio || 0) * 100) + "%"
      : "";
    setStatus("已加载 · as_of " + (d.as_of || "?") + cover, "ok");
  } catch (e) {
    setStatus("错误：" + e.message, "err");
  }
}

function render(d) {
  // KPI
  const c = d.concentration || {};
  $("kpis").innerHTML = [
    ["成分数", d.n_constituents],
    ["候选池", d.n_universe],
    ["Top1", pct(c.top1)],
    ["Top30", pct(c.top30)],
    ["HHI", (c.hhi || 0).toFixed(4)],
    ["有效成分数", Math.round(c.effective_n || 0)],
  ].map(([k, v]) => `<div class="kpi"><div class="kpi-v">${v}</div><div class="kpi-k">${k}</div></div>`).join("");

  // 指数走势
  if (d.index) {
    const i = d.index;
    Plotly.newPlot("chart-index", [
      { x: i.dates, y: i.price_index, name: "价格指数", type: "scatter", mode: "lines", line: { color: "#2563eb" } },
      { x: i.dates, y: i.total_return, name: "全收益指数", type: "scatter", mode: "lines", line: { color: "#16a34a", dash: "dot" } },
    ], { ...PLOT_LAYOUT, legend: { orientation: "h" } }, PLOT_CFG);
  } else {
    $("chart-index").innerHTML = '<p class="muted">无指数序列</p>';
  }

  // 行业饼图
  Plotly.newPlot("chart-sector",
    [{ labels: d.sectors.map((s) => s.sector), values: d.sectors.map((s) => s.weight), type: "pie", hole: 0.35 }],
    { ...PLOT_LAYOUT, height: 320 }, PLOT_CFG);

  // 市场柱状
  Plotly.newPlot("chart-market",
    [{ x: d.markets.map((m) => m.market), y: d.markets.map((m) => m.weight), type: "bar",
       text: d.markets.map((m) => pct(m.weight)), textposition: "outside" }],
    { ...PLOT_LAYOUT, yaxis: { tickformat: ".0%" }, height: 320 }, PLOT_CFG);

  // Top30 横向柱
  const top = d.top.slice().reverse();
  Plotly.newPlot("chart-top",
    [{ y: top.map((t) => t.name), x: top.map((t) => t.weight), type: "bar", orientation: "h",
       text: top.map((t) => pct(t.weight)), textposition: "outside" }],
    { ...PLOT_LAYOUT, xaxis: { tickformat: ".0%" }, height: 700, margin: { l: 90, r: 20, t: 10, b: 30 } }, PLOT_CFG);

  // 明细表
  const rows = d.constituents.map((r, i) =>
    `<tr><td>${i + 1}</td><td>${r.code}</td><td>${r.name}</td><td>${r.market}</td><td>${r.sector}</td><td>${pct(r.weight)}</td></tr>`
  ).join("");
  $("tbl").querySelector("tbody").innerHTML = rows;
  $("tbl-count").textContent = "（" + d.constituents.length + " 只）";
}

async function doBuild() {
  const universe = $("universe").value;
  setStatus("构建中（后台任务，请稍候）…", "info");
  try {
    await api("/api/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ universe, mode: "demo", markets: ["A", "HK", "US"] }),
    });
    pollBuild(universe);
  } catch (e) {
    setStatus("构建请求失败：" + e.message, "err");
  }
}

async function pollBuild(universe) {
  for (let i = 0; i < 120; i++) {
    try {
      const s = await api("/api/build/status?universe=" + encodeURIComponent(universe));
      if (s.status === "done") { setStatus("构建完成 · 正在刷新", "ok"); return loadSummary(); }
      if (s.status && s.status.startsWith("error")) { setStatus("构建失败：" + s.status, "err"); return; }
    } catch (_) {}
    setStatus("构建中… " + (i * 5) + "s", "info");
    await new Promise((r) => setTimeout(r, 5000));
  }
  setStatus("构建超时（仍在后台运行），可手动刷新", "err");
}

$("reload").onclick = loadSummary;
$("build").onclick = doBuild;
$("universe").onchange = loadSummary;
loadSummary();
