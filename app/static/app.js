"use strict";

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const EX_COLOR = (ex) => css(`--${ex}`) || "#848e9c";
// Payment-method accent bars, matching the colours Binance P2P shows for MMK methods.
const PM_KNOWN = { kbzpay: "#1e88e5", "cb pay": "#1e88e5", uabpay: "#1e88e5", mmqr: "#1e88e5",
  wavepay: "#fcd535", "wave pay": "#fcd535", "wave money": "#fcd535", "aya pay": "#f6465d", "nug pay": "#f6465d",
  "bank transfer": "#2ebd85", "cash deposit to bank": "#2ebd85", "spring development bank": "#2ebd85" };
const PM_COLORS = ["#fcd535", "#2ebd85", "#1e88e5", "#f6465d", "#b7bdc6"];
const TZ = "Asia/Yangon";
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 2) => (v == null ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d }));
const fmtTime = (ts, opts = {}) => new Date(ts * 1000).toLocaleString("en-GB", { timeZone: TZ, hour12: false, ...opts });
const opSym = (op) => (op === "<=" ? "≤" : "≥");
const pmColor = (name) => PM_KNOWN[String(name).toLowerCase()] || PM_COLORS[[...String(name)].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % PM_COLORS.length];

const state = { side: "BUY", ex: "", range: "24h", histSide: "", metric: "best", settings: null, chart: null, filtersDirty: false };

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (r.status === 401) {  // session expired or password changed
    location.replace("/login?next=" + encodeURIComponent(location.pathname));
    throw new Error("login required");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function setSeg(container, attr, value) {
  container.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset[attr] === value));
}

/* ---------- ticker + exchange cards ---------- */
async function refreshLatest() {
  let d;
  try { d = await api("/api/latest"); } catch (e) { $("updated").textContent = "API error"; document.querySelector(".live").classList.add("stale"); return; }
  const stale = !d.ts || Date.now() / 1000 - d.ts > d.interval * 4;
  document.querySelector(".live").classList.toggle("stale", stale);
  $("updated").textContent = d.ts ? `Live · ${fmtTime(d.ts, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : "waiting for first poll…";

  $("bestBuy").textContent = d.best_buy ? fmt(d.best_buy.price) : "–";
  $("bestBuyEx").textContent = d.best_buy ? d.best_buy.exchange : "";
  $("bestSell").textContent = d.best_sell ? fmt(d.best_sell.price) : "–";
  $("bestSellEx").textContent = d.best_sell ? d.best_sell.exchange : "";
  const gap = d.best_buy && d.best_sell ? d.best_sell.price - d.best_buy.price : null;
  $("gap").textContent = gap == null ? "–" : (gap > 0 ? "+" : "") + fmt(gap);
  $("gap").className = "tk-val " + (gap > 0 ? "up" : "");

  $("exTicker").innerHTML = Object.entries(d.exchanges).map(([ex, v]) => `<div class="ex">
      <b><span class="ex-dot" style="background:${EX_COLOR(ex)}"></span>${esc(ex)}</b>
      <span><span class="up num">${fmt(v.BUY?.best)}</span> / <span class="down num">${fmt(v.SELL?.best)}</span></span></div>`).join("");

  $("cards").innerHTML = Object.entries(d.exchanges).map(([ex, v]) => {
    const st = v.status || {};
    const stateTxt = st.ok === false ? `<span class="state down">● error</span>` : st.empty ? `<span class="state" style="color:var(--warn)">● no ads</span>` : st.ok ? `<span class="state up">● live</span>` : `<span class="state muted">● –</span>`;
    const b = v.BUY, s = v.SELL;
    const row = (label, cls, x) => `<div class="row"><span class="side ${cls}">${label}</span>
        <span><span class="big ${cls}">${fmt(x?.best)}</span><div class="muted small">best</div></span>
        <span><span class="num">${fmt(x?.avg5)}</span><div class="muted small">avg 5 · ${x?.ads ?? 0} ads</div></span></div>`;
    const err = st.ok === false ? `<div class="err-msg">${esc(st.error)} (${st.fails} fails)</div>` : "";
    return `<div class="card"><h3><span class="ex-dot" style="background:${EX_COLOR(ex)}"></span>${esc(ex)}${stateTxt}</h3>
      ${row("BUY", "up", b)}${row("SELL", "down", s)}${err}
      <div class="foot"><span>Spread (buy − sell)</span><span class="num">${b && s ? fmt(b.best - s.best) : "–"}</span></div></div>`;
  }).join("");
}

/* ---------- P2P ad list ---------- */
async function refreshAds() {
  const q = new URLSearchParams({ side: state.side, limit: 10 });
  if (state.ex) q.set("exchange", state.ex);
  let d;
  try { d = await api("/api/ads?" + q); } catch { return; }
  const isBuy = state.side === "BUY";
  if (!d.ads.length) {
    $("ads").innerHTML = `<div class="empty">No ads match right now. Check the exchange status or loosen your filters.</div>`;
    return;
  }
  $("ads").innerHTML = d.ads.map((a, i) => {
    const pms = a.pay_methods.map((p) => `<span class="pm" style="--pm:${pmColor(p)}">${esc(p)}</span>`).join("");
    return `<div class="ad">
      <div class="adv"><span class="avatar">${esc((a.merchant || "?").trim()[0] || "?")}</span>
        <div style="min-width:0"><div class="adv-name">${esc(a.merchant || "—")}</div>
        <span class="ex-badge"><span class="ex-dot" style="background:${EX_COLOR(a.exchange)}"></span>${esc(a.exchange)}</span></div></div>
      <div class="pr"><div class="price"><span class="cur">K</span>${fmt(a.price)}</div>${i === 0 ? `<span class="best-tag">Best price</span>` : ""}</div>
      <div class="limits"><div><span class="k">Available</span><span class="num">${fmt(a.available)} USDT</span></div>
        <div><span class="k">Limit</span><span class="num">${fmt(a.min, 0)} – ${a.max == null ? "∞" : fmt(a.max, 0)} MMK</span></div></div>
      <div class="pms">${pms || '<span class="muted">—</span>'}</div>
      <div class="act"><button class="${isBuy ? "buy" : "sell"}" data-price="${a.price}" data-ex="${esc(a.exchange)}" title="Create an alert at this price">🔔 Alert</button></div>
    </div>`;
  }).join("");
}

$("sideTabs").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.side = b.dataset.side; setSeg($("sideTabs"), "side", state.side); refreshAds();
});
$("exChips").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b) return;
  state.ex = b.dataset.ex; setSeg($("exChips"), "ex", state.ex); refreshAds();
});
$("ads").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-price]"); if (!b) return;
  $("ruleEx").value = b.dataset.ex;
  $("ruleSide").value = state.side;
  $("ruleOp").value = state.side === "BUY" ? "<=" : ">=";
  $("ruleThreshold").value = b.dataset.price;
  $("alerts").scrollIntoView();
  $("ruleThreshold").focus();
  setMsg("ruleMsg", "Prefilled from ad — adjust the price and press Add alert.");
});

/* ---------- history chart ---------- */
function tickLabel(ts, span) {
  if (span <= 2 * 86400) return fmtTime(ts, { hour: "2-digit", minute: "2-digit" });
  if (span <= 60 * 86400) return fmtTime(ts, { day: "2-digit", month: "short", hour: "2-digit" });
  return fmtTime(ts, { day: "2-digit", month: "short", year: "2-digit" });
}

async function refreshHistory() {
  const q = new URLSearchParams({ range: state.range });
  if (state.histSide) q.set("side", state.histSide);
  let d;
  try { d = await api("/api/history?" + q); } catch { return; }
  const idx = state.metric === "best" ? 1 : 2;
  const span = d.to - d.from;
  const datasets = d.series.map((s) => ({
    label: `${s.exchange} ${s.side}`,
    data: s.points.map((p) => ({ x: p[0], y: p[idx] })),
    borderColor: EX_COLOR(s.exchange), backgroundColor: EX_COLOR(s.exchange),
    borderDash: s.side === "SELL" ? [6, 4] : [],
    borderWidth: 1.6, pointRadius: s.points.length < 60 ? 2 : 0, tension: 0.15, spanGaps: false,
  }));
  const hidden = new Set((state.chart?.data.datasets || []).filter((_, i) => !state.chart.isDatasetVisible(i)).map((x) => x.label));
  datasets.forEach((ds) => { ds.hidden = hidden.has(ds.label); });

  if (state.chart) {
    Object.assign(state.chart.options.scales.x, { min: d.from, max: d.to });
    state.chart.options.scales.x.ticks.callback = (v) => tickLabel(v, span);
    state.chart.data.datasets = datasets;
    state.chart.update("none");
    return;
  }
  const grid = css("--border"), text = css("--muted");
  state.chart = new Chart($("chart"), {
    type: "line",
    data: { datasets },
    options: {
      animation: false, responsive: true, maintainAspectRatio: false, parsing: false, normalized: true,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        x: { type: "linear", min: d.from, max: d.to, grid: { color: grid }, border: { color: grid }, ticks: { color: text, maxTicksLimit: 8, callback: (v) => tickLabel(v, span) } },
        y: { position: "right", grid: { color: grid }, border: { color: grid }, ticks: { color: text, callback: (v) => fmt(v) } },
      },
      plugins: {
        legend: { labels: { color: text, boxWidth: 14, font: { family: "IBM Plex Sans" } } },
        tooltip: { backgroundColor: css("--panel-2"), borderColor: grid, borderWidth: 1, titleColor: css("--text"), bodyColor: css("--text"),
          callbacks: { title: (items) => fmtTime(items[0].parsed.x), label: (c) => ` ${c.dataset.label}: ${fmt(c.parsed.y)}` } },
      },
    },
  });
}

function bindSeg(id, attr, key, after) {
  $(id).addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state[key] = b.dataset[attr]; setSeg($(id), attr, state[key]); after();
  });
}
bindSeg("rangeSeg", "v", "range", refreshHistory);
bindSeg("sideSeg", "v", "histSide", refreshHistory);
bindSeg("metricSeg", "v", "metric", refreshHistory);

/* ---------- alert rules & events ---------- */
function setMsg(id, text, kind = "") { const el = $(id); el.textContent = text; el.className = "msg " + kind; }

async function refreshRules() {
  const rules = await api("/api/alerts");
  const head = `<div class="trow head"><span>#</span><span>Rule</span><span>Last fired</span><span></span></div>`;
  $("rules").innerHTML = head + (rules.map((r) => `<div class="trow ${r.enabled ? "" : "off"}">
      <span class="muted">${r.id}</span>
      <span><span class="side-pill ${esc(r.side)}">${esc(r.side)}</span> <span style="text-transform:capitalize">${esc(r.exchange)}</span>
        ${r.metric === "avg5" ? "avg 5" : "best"} ${opSym(r.op)} <b class="num">${fmt(r.threshold)}</b> <span class="muted small">· ${r.cooldown_sec}s</span></span>
      <span class="small muted">${r.last_fired_at ? fmtTime(r.last_fired_at, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "never"}</span>
      <span class="row-btns">
        <button class="btn-ghost" data-act="toggle" data-id="${r.id}" data-en="${r.enabled ? 0 : 1}">${r.enabled ? "Pause" : "Resume"}</button>
        <button class="btn-danger" data-act="del" data-id="${r.id}">Delete</button></span></div>`).join("")
    || `<div class="empty">No alerts yet. Use the form, or 🔔 on any ad.</div>`);
}

const PATTERN_LABEL = { spike: "Sudden spike", drop: "Sudden drop", trend_up: "Rising run", trend_down: "Falling run" };

async function refreshEvents() {
  const [alerts, patterns] = await Promise.all([
    api("/api/alert-events?limit=30"),
    api("/api/pattern-events?limit=30").catch(() => []),
  ]);
  const rows = [
    ...alerts.map((e) => ({ ts: e.ts, side: e.side, exchange: e.exchange, price: e.price,
      what: `#${e.rule_id} ${e.metric === "avg5" ? "avg 5" : "best"} ${opSym(e.op)} ${fmt(e.threshold)}` })),
    ...patterns.map((p) => ({ ts: p.ts, side: p.side, exchange: p.exchange, price: p.end_price, pattern: true,
      what: `${PATTERN_LABEL[p.kind] || esc(p.kind)} <span class="${p.pct > 0 ? "up" : "down"}">${p.pct > 0 ? "+" : ""}${fmt(p.pct)}%</span> / ${p.minutes} min` })),
  ].sort((a, b) => b.ts - a.ts).slice(0, 30);
  const head = `<div class="trow head"><span>Time</span><span>Market</span><span>Price</span><span>Trigger</span></div>`;
  $("events").innerHTML = head + (rows.map((e) => `<div class="trow">
      <span class="small muted">${fmtTime(e.ts, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
      <span><span class="side-pill ${esc(e.side)}">${esc(e.side)}</span> <span style="text-transform:capitalize">${esc(e.exchange)}</span></span>
      <span class="num ${e.side === "BUY" ? "up" : "down"}">${fmt(e.price)}</span>
      <span class="small muted">${e.pattern ? '<span class="pattern-tag">Pattern</span> ' : ""}${e.what}</span></div>`).join("")
    || `<div class="empty">No alerts fired yet.</div>`);
}

$("rules").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const id = b.dataset.id;
  try {
    if (b.dataset.act === "del") {
      if (!confirm(`Delete alert #${id}?`)) return;
      await api(`/api/alerts/${id}`, { method: "DELETE" });
    } else {
      await api(`/api/alerts/${id}`, { method: "PATCH", body: JSON.stringify({ enabled: b.dataset.en === "1" }) });
    }
    refreshRules();
  } catch (err) { setMsg("ruleMsg", err.message, "err"); }
});

$("ruleForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  body.threshold = Number(body.threshold); body.cooldown_sec = Number(body.cooldown_sec || 0);
  try {
    const r = await api("/api/alerts", { method: "POST", body: JSON.stringify(body) });
    setMsg("ruleMsg", `Alert #${r.id} added.`, "ok");
    e.target.threshold.value = "";
    refreshRules();
  } catch (err) { setMsg("ruleMsg", "Error: " + err.message, "err"); }
});

/* ---------- settings / filters ---------- */
async function loadSettings() {
  const s = await api("/api/settings");
  state.settings = s;
  $("paused").checked = !!s.alerts_paused;
  setIntervalUI(s.poll_interval);
  $("tgState").textContent = s.telegram_enabled ? "Telegram connected" : "Telegram not configured";
  if (!$("ruleEx").options.length) {
    $("ruleEx").innerHTML = ["any", ...s.exchanges].map((x) => `<option value="${esc(x)}">${x === "any" ? "Any exchange" : esc(x[0].toUpperCase() + x.slice(1))}</option>`).join("");
    $("exChips").innerHTML = `<button data-ex="" class="on">All exchanges</button>` +
      s.exchanges.map((x) => `<button data-ex="${esc(x)}">${esc(x)}</button>`).join("");
  }
  if (!state.filtersDirty) renderFilters();
}

function renderFilters() {
  const s = state.settings;
  $("filters-body").innerHTML = s.exchanges.map((ex) => {
    const cur = s.filters[ex] || { pay_methods: [], amount: null };
    const all = [...new Set([...(s.pay_methods_seen[ex] || []), ...cur.pay_methods])].sort((a, b) => a.localeCompare(b));
    const chips = all.map((p) => `<label class="pm-chip"><input type="checkbox" data-ex="${esc(ex)}" value="${esc(p)}" ${cur.pay_methods.includes(p) ? "checked" : ""}><span>${esc(p)}</span></label>`).join("")
      || `<span class="muted small">No payment methods seen yet.</span>`;
    return `<fieldset><legend><span class="ex-dot" style="background:${EX_COLOR(ex)}"></span>${esc(ex)}</legend>
      <label class="amt">Trade amount (MMK) <input type="number" min="0" step="any" data-amt="${esc(ex)}" value="${cur.amount ?? ""}" placeholder="Any amount"></label>
      <div class="pm-chips">${chips}</div></fieldset>`;
  }).join("");
}
$("filters-body").addEventListener("input", () => { state.filtersDirty = true; });

$("saveFilters").addEventListener("click", async () => {
  const filters = {};
  for (const ex of state.settings.exchanges) {
    const amt = document.querySelector(`[data-amt="${CSS.escape(ex)}"]`).value;
    const pms = [...document.querySelectorAll(`input[type=checkbox][data-ex="${CSS.escape(ex)}"]:checked`)].map((i) => i.value);
    filters[ex] = { pay_methods: pms, amount: amt ? Number(amt) : null };
  }
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ filters }) });
    setMsg("filterMsg", "Saved — applies from the next poll.", "ok");
    state.filtersDirty = false;
    await loadSettings();
  } catch (err) { setMsg("filterMsg", "Error: " + err.message, "err"); }
});

$("paused").addEventListener("change", (e) =>
  api("/api/settings", { method: "PUT", body: JSON.stringify({ alerts_paused: e.target.checked }) }));

/* ---------- poll interval ---------- */
const fmtSecs = (s) => (s < 60 ? `${s}s` : s % 60 === 0 ? `${s / 60} min` : `${s}s`);

function setIntervalUI(secs) {
  if (!secs) return;
  secs = Math.round(secs);
  state.interval = secs;
  const sel = $("pollSel");
  if (![...sel.options].some((o) => Number(o.value) === secs)) {
    sel.add(new Option(fmtSecs(secs), secs));  // value set via .env that isn't a preset
  }
  sel.value = String(secs);
  $("intervalNote").textContent = fmtSecs(secs);
}

$("pollSel").addEventListener("change", async (e) => {
  const prev = state.interval;
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ poll_interval: Number(e.target.value) }) });
    setIntervalUI(Number(e.target.value));
    liveLoop(true);
  } catch (err) {
    setIntervalUI(prev);
    alert("Could not change interval: " + err.message);
  }
});

// Page refresh follows the server's poll interval (never faster than 5s).
let liveTimer = null;
function liveLoop(now = false) {
  clearTimeout(liveTimer);
  const run = () => { refreshLatest(); refreshAds(); liveTimer = setTimeout(run, Math.max(5, state.interval || 5) * 1000); };
  if (now) run(); else liveTimer = setTimeout(run, Math.max(5, state.interval || 5) * 1000);
}

/* ---------- nav highlight ---------- */
const navLinks = [...document.querySelectorAll(".nav a")];
const observer = new IntersectionObserver((entries) => {
  entries.forEach((en) => {
    if (en.isIntersecting) navLinks.forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + en.target.id));
  });
}, { rootMargin: "-40% 0px -55% 0px" });
["market", "history", "alerts", "filters"].forEach((id) => observer.observe($(id)));

/* ---------- boot ---------- */
$("logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" }).catch(() => {});
  location.replace("/login");
});

(async function init() {
  api("/api/me").then((m) => { $("logout").hidden = !m.auth; }).catch(() => {});
  await loadSettings().catch(() => {});
  refreshLatest(); refreshAds(); refreshHistory();
  refreshRules().catch(() => {}); refreshEvents().catch(() => {});
  liveLoop();
  setInterval(() => { if (state.range === "1h" || state.range === "24h") refreshHistory(); }, 15000);
  setInterval(() => { refreshRules().catch(() => {}); refreshEvents().catch(() => {}); }, 15000);
  setInterval(() => { loadSettings().catch(() => {}); }, 60000);
})();
