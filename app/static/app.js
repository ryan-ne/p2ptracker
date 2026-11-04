"use strict";

const COLORS = { binance: "#e0a800", bybit: "#4f8cff", bitget: "#14b8a6" };
const TZ = "Asia/Yangon";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v) => (v == null ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 }));
const fmtTime = (ts, opts = {}) => new Date(ts * 1000).toLocaleString("en-GB", { timeZone: TZ, hour12: false, ...opts });

const state = { range: "24h", side: "", metric: "best", settings: null, chart: null };

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

/* ---------- live cards ---------- */
async function refreshLatest() {
  let d;
  try { d = await api("/api/latest"); } catch (e) { $("updated").textContent = "API error: " + e.message; return; }
  $("updated").textContent = d.ts ? `Updated ${fmtTime(d.ts)} (Yangon) · every ${d.interval}s` : "waiting for first poll…";
  $("bestBuy").textContent = d.best_buy ? fmt(d.best_buy.price) : "–";
  $("bestBuyEx").textContent = d.best_buy ? d.best_buy.exchange : "";
  $("bestSell").textContent = d.best_sell ? fmt(d.best_sell.price) : "–";
  $("bestSellEx").textContent = d.best_sell ? d.best_sell.exchange : "";
  $("gap").textContent = d.best_buy && d.best_sell ? fmt(d.best_sell.price - d.best_buy.price) : "–";

  $("cards").innerHTML = Object.entries(d.exchanges).map(([ex, v]) => {
    const st = v.status || {};
    const cls = st.ok === false ? "err" : st.empty ? "empty" : st.ok ? "ok" : "";
    const b = v.BUY, s = v.SELL;
    const spread = b && s ? fmt(b.best - s.best) : "–";
    const row = (label, x) => `<div class="row"><span class="side">${label}</span>
        <span><span class="big">${fmt(x?.best)}</span><div class="muted small">best</div></span>
        <span><span class="num">${fmt(x?.avg5)}</span><div class="muted small">avg5 · ${x?.ads ?? 0} ads</div></span></div>`;
    let foot = `Spread (buy − sell): <span class="num">${spread}</span>`;
    if (st.ok === false) foot += `<div class="err-msg">${esc(st.error)} (${st.fails} fails)</div>`;
    else if (st.empty) foot += `<div class="muted">No ads returned — region-blocked or filters too strict.</div>`;
    return `<div class="card"><h3><span class="dot ${cls}" style="background:${cls === "ok" ? COLORS[ex] : ""}"></span>${esc(ex)}</h3>
      ${row("BUY", b)}${row("SELL", s)}<div class="foot">${foot}</div></div>`;
  }).join("");
}

/* ---------- history chart ---------- */
function tickLabel(ts, span) {
  if (span <= 2 * 86400) return fmtTime(ts, { hour: "2-digit", minute: "2-digit" });
  if (span <= 60 * 86400) return fmtTime(ts, { day: "2-digit", month: "short", hour: "2-digit" });
  return fmtTime(ts, { day: "2-digit", month: "short", year: "2-digit" });
}

async function refreshHistory() {
  const q = new URLSearchParams({ range: state.range });
  if (state.side) q.set("side", state.side);
  let d;
  try { d = await api("/api/history?" + q); } catch { return; }
  const idx = state.metric === "best" ? 1 : 2;
  const span = d.to - d.from;
  const datasets = d.series.map((s) => ({
    label: `${s.exchange} ${s.side}`,
    data: s.points.map((p) => ({ x: p[0], y: p[idx] })),
    borderColor: COLORS[s.exchange] || "#888",
    backgroundColor: COLORS[s.exchange] || "#888",
    borderDash: s.side === "SELL" ? [6, 4] : [],
    borderWidth: 1.6, pointRadius: s.points.length < 60 ? 2 : 0, tension: 0.15, spanGaps: false,
  }));
  const grid = getComputedStyle(document.documentElement).getPropertyValue("--border").trim();
  const text = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
  const hidden = new Set((state.chart?.data.datasets || []).filter((_, i) => !state.chart.isDatasetVisible(i)).map((x) => x.label));
  datasets.forEach((ds) => { ds.hidden = hidden.has(ds.label); });

  if (state.chart) {
    state.chart.data.datasets = datasets;
    state.chart.options.scales.x.min = d.from; state.chart.options.scales.x.max = d.to;
    state.chart.options.scales.x.ticks.callback = (v) => tickLabel(v, span);
    state.chart.update("none");
    return;
  }
  state.chart = new Chart($("chart"), {
    type: "line",
    data: { datasets },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { type: "linear", min: d.from, max: d.to, grid: { color: grid }, ticks: { color: text, maxTicksLimit: 8, callback: (v) => tickLabel(v, span) } },
        y: { grid: { color: grid }, ticks: { color: text, callback: (v) => fmt(v) } },
      },
      plugins: {
        legend: { labels: { color: text, boxWidth: 14 } },
        tooltip: { callbacks: { title: (items) => fmtTime(items[0].parsed.x), label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y)}` } },
      },
    },
  });
}

/* ---------- alert rules & events ---------- */
async function refreshRules() {
  const rules = await api("/api/alerts");
  $("rules").innerHTML = rules.map((r) => `<tr class="${r.enabled ? "" : "off"}">
      <td>${r.id}</td>
      <td>${esc(r.exchange)} ${esc(r.side)} ${esc(r.metric)} ${r.op === "<=" ? "≤" : "≥"} <b class="num">${fmt(r.threshold)}</b>
        <span class="muted small">cd ${r.cooldown_sec}s</span></td>
      <td class="small">${r.last_fired_at ? fmtTime(r.last_fired_at) : "–"}</td>
      <td style="white-space:nowrap">
        <button data-act="toggle" data-id="${r.id}" data-en="${r.enabled ? 0 : 1}">${r.enabled ? "Disable" : "Enable"}</button>
        <button data-act="del" data-id="${r.id}" class="danger">Delete</button></td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">No rules yet.</td></tr>`;
}

async function refreshEvents() {
  const ev = await api("/api/alert-events?limit=30");
  $("events").innerHTML = ev.map((e) => `<tr>
      <td class="small">${fmtTime(e.ts)}</td><td>${esc(e.exchange)} ${esc(e.side)}</td>
      <td class="num">${fmt(e.price)}</td><td class="small">#${e.rule_id} ${esc(e.metric)} ${e.op === "<=" ? "≤" : "≥"} ${fmt(e.threshold)}</td></tr>`).join("")
    || `<tr><td colspan="4" class="muted">No alerts fired yet.</td></tr>`;
}

$("rules").addEventListener("click", async (e) => {
  const b = e.target.closest("button"); if (!b) return;
  const id = b.dataset.id;
  try {
    if (b.dataset.act === "del") {
      if (!confirm(`Delete rule #${id}?`)) return;
      await api(`/api/alerts/${id}`, { method: "DELETE" });
    } else {
      await api(`/api/alerts/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: b.dataset.en === "1" }) });
    }
    refreshRules();
  } catch (err) { $("ruleMsg").textContent = err.message; }
});

$("ruleForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries(f.entries());
  body.threshold = Number(body.threshold); body.cooldown_sec = Number(body.cooldown_sec || 0);
  try {
    const r = await api("/api/alerts", { method: "POST", body: JSON.stringify(body) });
    $("ruleMsg").textContent = `Added rule #${r.id}`;
    e.target.threshold.value = "";
    refreshRules();
  } catch (err) { $("ruleMsg").textContent = "Error: " + err.message; }
});

/* ---------- settings / filters ---------- */
async function loadSettings() {
  const s = await api("/api/settings");
  state.settings = s;
  $("paused").checked = !!s.alerts_paused;
  const exSel = $("ruleEx");
  if (!exSel.options.length) exSel.innerHTML = ["any", ...s.exchanges].map((x) => `<option value="${esc(x)}">${esc(x)}</option>`).join("");
  if (!state.filtersDirty) renderFilters();
}
$("filters").addEventListener("input", () => { state.filtersDirty = true; });

function renderFilters() {
  const s = state.settings;
  $("filters").innerHTML = s.exchanges.map((ex) => {
    const cur = s.filters[ex] || { pay_methods: [], amount: null };
    const all = [...new Set([...(s.pay_methods_seen[ex] || []), ...cur.pay_methods])].sort();
    const boxes = all.map((p) => `<label><input type="checkbox" data-ex="${esc(ex)}" value="${esc(p)}" ${cur.pay_methods.includes(p) ? "checked" : ""}> ${esc(p)}</label>`).join("")
      || `<span class="muted small">none seen yet</span>`;
    return `<fieldset><legend>${esc(ex)}</legend>
      <label>Trade amount (MMK) <input type="number" min="0" step="any" data-amt="${esc(ex)}" value="${cur.amount ?? ""}" placeholder="any" style="width:130px"></label>
      <div class="pm">${boxes}</div></fieldset>`;
  }).join("");
}

$("saveFilters").addEventListener("click", async () => {
  const filters = {};
  for (const ex of state.settings.exchanges) {
    const amt = document.querySelector(`[data-amt="${CSS.escape(ex)}"]`).value;
    const pms = [...document.querySelectorAll(`input[type=checkbox][data-ex="${CSS.escape(ex)}"]:checked`)].map((i) => i.value);
    filters[ex] = { pay_methods: pms, amount: amt ? Number(amt) : null };
  }
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ filters }) });
    $("filterMsg").textContent = "Saved — applies from next poll.";
    state.filtersDirty = false;
    await loadSettings();
  } catch (err) { $("filterMsg").textContent = "Error: " + err.message; }
});

$("paused").addEventListener("change", (e) =>
  api("/api/settings", { method: "PUT", body: JSON.stringify({ alerts_paused: e.target.checked }) }));

/* ---------- controls ---------- */
$("rangeSeg").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.range = b.dataset.v;
  document.querySelectorAll("#rangeSeg button").forEach((x) => x.classList.toggle("on", x === b));
  refreshHistory();
});
$("sideSel").addEventListener("change", (e) => { state.side = e.target.value; refreshHistory(); });
$("metricSel").addEventListener("change", (e) => { state.metric = e.target.value; refreshHistory(); });

/* ---------- boot ---------- */
(async function init() {
  await loadSettings().catch(() => {});
  refreshLatest(); refreshHistory(); refreshRules().catch(() => {}); refreshEvents().catch(() => {});
  setInterval(refreshLatest, 5000);
  setInterval(() => { if (state.range === "1h" || state.range === "24h") refreshHistory(); }, 15000);
  setInterval(() => { refreshRules().catch(() => {}); refreshEvents().catch(() => {}); }, 15000);
  setInterval(() => { loadSettings().catch(() => {}); }, 60000);
})();
