"use strict";

const CHART_CFG = { responsive: true, displayModeBar: false };
const CHART_FONT = { family: "'Inter','PingFang SC','Microsoft YaHei',sans-serif", size: 12, color: "#8ba0bd" };
// 坐标轴配置用工厂函数：Plotly 会原地改写传入的 axis 对象（如自动判定 type），
// 若多个图表共享同一对象，前面的图表会把 type 改成 date，污染后续图表（曾致市场图/TOP30 轴错乱）。
const axis = () => ({
  gridcolor: "rgba(148,163,184,0.10)", zeroline: false,
  linecolor: "rgba(148,163,184,0.22)", tickfont: { size: 11, color: "#8ba0bd" },
});
const PAPER = {
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  font: CHART_FONT, margin: { t: 16, l: 52, r: 18, b: 38 },
};
const PALETTE = [
  "#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa",
  "#f87171", "#2dd4bf", "#facc15", "#c084fc", "#fb923c",
  "#4ade80", "#38bdf8", "#e879f9", "#94a3b8", "#fda4af",
];
const MKT_COLOR = { A: "#60a5fa", HK: "#34d399", US: "#c084fc" };
const pct = (x) => (x * 100).toFixed(2) + "%";
const svg = (inner) =>
  `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;

const KPI_ICONS = {
  成分数: '<polygon points="12 2 2 7 12 12 22 7 12 2"></polygon><polyline points="2 17 12 22 22 17"></polyline><polyline points="2 12 12 17 22 12"></polyline>',
  候选池: '<ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"></path><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path>',
  Top1: '<circle cx="12" cy="8" r="7"></circle><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"></polyline>',
  Top30: '<line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line>',
  HHI: '<path d="M21.21 15.89A10 10 0 1 1 8 2.83"></path><path d="M22 12A10 10 0 0 0 12 2v10z"></path>',
  有效成分数: '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline>',
};

const $ = (id) => document.getElementById(id);
const statusEl = $("status");

function setStatus(msg, kind) {
  statusEl.textContent = msg || "";
  statusEl.className = "status" + (kind ? " " + kind : "");
}

function chart(divId, data, layout) {
  // 图表高度一律由 CSS 控制（.plot 高度），不再在 layout 中写死，避免容器与 SVG 高度不一致
  Plotly.newPlot(divId, data, { ...PAPER, ...layout }, CHART_CFG).catch((e) => {
    setStatus(`图表 ${divId} 渲染失败：${(e && e.message) || e}`, "err");
  });
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
      ? "真实股本 " + Math.round(d.real_shares_ratio * 100) + "%" +
        " · 真实利润 " + Math.round((d.real_profit_ratio || 0) * 100) + "%"
      : "";
    setStatus("已加载 · as_of " + (d.as_of || "?") + (cover ? " · " + cover : ""), "ok");
  } catch (e) {
    setStatus("错误：" + e.message, "err");
  }
}

function render(d) {
  // ---- KPI ----
  const c = d.concentration || {};
  const kpiDefs = [
    ["成分数", d.n_constituents, KPI_ICONS.成分数],
    ["候选池", d.n_universe, KPI_ICONS.候选池],
    ["Top1", pct(c.top1), KPI_ICONS.Top1],
    ["Top30", pct(c.top30), KPI_ICONS.Top30],
    ["HHI", (c.hhi || 0).toFixed(4), KPI_ICONS.HHI],
    ["有效成分数", Math.round(c.effective_n || 0), KPI_ICONS.有效成分数],
  ];
  $("kpis").innerHTML = kpiDefs
    .map(([k, v, ic]) => `<div class="kpi"><div class="kpi-icon">${svg(ic)}</div>
      <div class="kpi-v">${v}</div><div class="kpi-k">${k}</div></div>`)
    .join("");

  // ---- 指数走势 ----
  if (d.index) {
    const i = d.index;
    chart("chart-index", [
      { x: i.dates, y: i.price_index, name: "价格指数", type: "scatter", mode: "lines",
        line: { color: "#60a5fa", width: 2 }, fill: "tozeroy", fillcolor: "rgba(96,165,250,0.06)",
        hovertemplate: "%{x|%Y-%m-%d}<br>%{y:.2f}<extra>价格指数</extra>" },
      { x: i.dates, y: i.total_return, name: "全收益指数", type: "scatter", mode: "lines",
        line: { color: "#34d399", width: 2, dash: "dot" },
        hovertemplate: "%{x|%Y-%m-%d}<br>%{y:.2f}<extra>全收益指数</extra>" },
    ], { legend: { orientation: "h", x: 0, y: 1.14, font: { size: 12, color: "#b9c8dd" } },
         xaxis: axis(), yaxis: { ...axis(), tickformat: ",.0f" } });
  } else {
    $("chart-index").innerHTML = '<p class="muted">无指数序列</p>';
  }

  // ---- 行业饼图（高度由 CSS #chart-sector 控制；放大 + 右侧图例）----
  chart("chart-sector", [{
    labels: d.sectors.map((s) => s.sector),
    values: d.sectors.map((s) => s.weight),
    type: "pie", hole: 0.55,
    textinfo: "percent", textposition: "inside", insidetextorientation: "horizontal",
    textfont: { size: 12, color: "#0b1120" },
    hovertemplate: "%{label}：%{percent}<extra></extra>",
    marker: { colors: PALETTE.slice(0, Math.max(d.sectors.length, 1)),
              line: { color: "#0f172a", width: 1.5 } },
  }], {
    showlegend: true,
    legend: { orientation: "v", x: 1.04, y: 0.98, font: { size: 12, color: "#b9c8dd" },
              bgcolor: "rgba(0,0,0,0)", tracegroupgap: 4 },
    margin: { l: 24, r: 130, t: 12, b: 24 },
  });

  // ---- 市场柱状（高度由 CSS 控制；rangemode=tozero 保证从 0 起）----
  chart("chart-market", [{
    x: d.markets.map((m) => m.market),
    y: d.markets.map((m) => m.weight),
    type: "bar",
    text: d.markets.map((m) => pct(m.weight)),
    textposition: "outside", textfont: { size: 12, color: "#b9c8dd" },
    hovertemplate: "%{x}：%{y:.1%}<extra></extra>",
    marker: { color: d.markets.map((m) => MKT_COLOR[m.market] || "#60a5fa") },
  }], { xaxis: axis(), yaxis: { ...axis(), rangemode: "tozero", tickformat: ".0%" }, bargap: 0.35 });

  // ---- Top30 横向柱（高度由 CSS .card.wide .plot 控制；rangemode=tozero 保证条形长度如实反映权重）----
  const top = d.top.slice().reverse();
  const n = Math.max(top.length - 1, 1);
  const barColors = top.map((_, i) => `rgba(96,165,250,${(0.95 - (i / n) * 0.55).toFixed(3)})`);
  chart("chart-top", [{
    y: top.map((t) => t.name), x: top.map((t) => t.weight),
    type: "bar", orientation: "h",
    text: top.map((t) => pct(t.weight)), textposition: "outside",
    textfont: { size: 11, color: "#cbd5e1" },
    hovertemplate: "%{y}：%{x:.2%}<extra></extra>",
    marker: { color: barColors },
  }], { xaxis: { ...axis(), rangemode: "tozero", tickformat: ".0%" }, margin: { l: 96, r: 26, t: 8, b: 32 } });

  // ---- 明细表 ----
  const rows = d.constituents.map((r, i) =>
    `<tr>
      <td class="num muted">${i + 1}</td>
      <td class="code">${r.code}</td>
      <td>${r.name}</td>
      <td><span class="mkt-chip mkt-${r.market}">${r.market}</span></td>
      <td>${r.sector || "—"}</td>
      <td class="num">${pct(r.weight)}</td>
    </tr>`
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
