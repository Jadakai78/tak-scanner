const KV_KEY = "signal_bus";
const WRITE_SECRET = "jhl2026dragon";

const HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="theme-color" content="#0a0a0a" />
<meta name="mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="JHL Feed" />
<link rel="manifest" href="/manifest.webmanifest" />
<link rel="apple-touch-icon" href="/icon-192.png" />
<title>JHL Live Feed v2 — Tak Signal Terminal</title>
<style>
  :root {
    --bg: #0a0e14;
    --panel: #121822;
    --panel-2: #1a222f;
    --border: #232f3f;
    --text: #d6e0ef;
    --muted: #7c8aa0;
    --accent: #2dd4bf;
    --long: #22c55e;
    --short: #ef4444;
    --grade-s: #f5c518;
    --grade-a: #2dd4bf;
    --grade-b: #60a5fa;
    --grade-c: #94a3b8;
    --kill: #ef4444;
    --caution: #f59e0b;
    --fear: #ef4444;
    --greed: #22c55e;
    --neutral: #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: "SF Mono", "JetBrains Mono", "Fira Code", Consolas, monospace;
    font-size: 13px;
    line-height: 1.5;
    padding: 16px;
  }
  a { color: var(--accent); }
  .wrap { max-width: 1400px; margin: 0 auto; }

  .header {
    display: flex; align-items: center; justify-content: space-between;
    background: linear-gradient(90deg, var(--panel) 0%, var(--panel-2) 100%);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 20px;
    margin-bottom: 14px;
  }
  .brand { display: flex; align-items: baseline; gap: 12px; }
  .brand h1 { font-size: 18px; letter-spacing: 1px; color: var(--accent); }
  .brand .sub { color: var(--muted); font-size: 11px; }
  .header-right { display: flex; align-items: center; gap: 20px; }

  .fng { text-align: center; min-width: 120px; }
  .fng .label { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
  .fng .score { font-size: 26px; font-weight: 700; line-height: 1.1; }
  .fng .cls { font-size: 11px; }
  .fng-bar { height: 6px; border-radius: 3px; margin-top: 4px;
    background: linear-gradient(90deg, var(--fear), var(--caution), var(--greed)); position: relative; }
  .fng-bar .needle { position: absolute; top: -3px; width: 2px; height: 12px; background: #fff; border-radius: 1px; }

  .conn { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--muted); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .dot.live { background: var(--long); box-shadow: 0 0 8px var(--long); animation: pulse 1.8s infinite; }
  .dot.stale { background: var(--caution); }
  .dot.off { background: var(--short); }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .4; } }

  .status-row { display: grid; grid-template-columns: repeat(6, 1fr); gap: 10px; margin-bottom: 14px; }
  .stat {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 14px;
  }
  .stat .k { font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 1px; }
  .stat .v { font-size: 20px; font-weight: 700; margin-top: 2px; }
  .stat .v.small { font-size: 13px; font-weight: 500; }

  .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 14px; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } .status-row { grid-template-columns: repeat(3,1fr); } }

  .panel {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 14px 16px; margin-bottom: 14px;
  }
  .panel h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: 1.5px;
    color: var(--muted); margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center;
  }
  .panel h2 .badge { background: var(--panel-2); color: var(--accent); border-radius: 12px; padding: 2px 10px; font-size: 11px; }

  .regime-map { display: flex; flex-wrap: wrap; gap: 8px; }
  .regime-chip {
    background: var(--panel-2); border: 1px solid var(--border); border-radius: 6px;
    padding: 6px 10px; font-size: 12px; display: flex; gap: 8px; align-items: center;
  }
  .regime-chip .pr { font-weight: 700; }
  .regime-chip .rg { font-size: 10px; padding: 1px 6px; border-radius: 4px; }

  .signal-card {
    background: var(--panel-2); border: 1px solid var(--border); border-left: 3px solid var(--muted);
    border-radius: 8px; padding: 12px 14px; margin-bottom: 10px;
    cursor: pointer; transition: border-color .15s;
  }
  .signal-card.long { border-left-color: var(--long); }
  .signal-card.short { border-left-color: var(--short); }
  .signal-card:active { border-color: var(--accent); }
  .sig-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .sig-pair { font-size: 16px; font-weight: 700; }
  .sig-bias { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 700; margin-left: 8px; }
  .bias-LONG { background: rgba(34,197,94,.18); color: var(--long); }
  .bias-SHORT { background: rgba(239,68,68,.18); color: var(--short); }
  .grade { font-weight: 700; padding: 3px 10px; border-radius: 6px; font-size: 12px; }
  .grade-S { background: var(--grade-s); color: #000; box-shadow: 0 0 10px rgba(245,197,24,.5); }
  .grade-A { background: var(--grade-a); color: #000; }
  .grade-B { background: var(--grade-b); color: #000; }
  .grade-C { background: var(--grade-c); color: #000; }

  .sig-meta { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 11px; color: var(--muted); margin-bottom: 8px; }
  .sig-meta b { color: var(--text); font-weight: 600; }

  .sig-levels { display: grid; grid-template-columns: repeat(4,1fr); gap: 8px; font-size: 11px; }
  .lvl { background: var(--panel); border-radius: 6px; padding: 6px 8px; }
  .lvl .lk { color: var(--muted); font-size: 9px; text-transform: uppercase; }
  .lvl .lv { font-weight: 600; }

  .sig-tags { margin-top: 8px; display: flex; gap: 6px; flex-wrap: wrap; }
  .tag { font-size: 10px; padding: 2px 8px; border-radius: 4px; background: var(--panel); border: 1px solid var(--border); }
  .tag.caution { color: var(--caution); border-color: var(--caution); }
  .tag.clean { color: var(--long); }
  .tag.confirm { color: var(--grade-s); }
  .mtf-FULL { color: var(--long); }
  .mtf-PARTIAL { color: var(--muted); }
  .mtf-CONFLICT { color: var(--short); }

  .kill-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 7px 10px; border-bottom: 1px solid var(--border); font-size: 12px;
  }
  .kill-row:last-child { border-bottom: none; }
  .kill-reason { color: var(--kill); font-size: 10px; padding: 1px 7px; border-radius: 4px; background: rgba(239,68,68,.12); }

  .empty { color: var(--muted); font-style: italic; text-align: center; padding: 20px; font-size: 12px; }

  .eval-bar-wrap { background: var(--panel-2); border-radius: 6px; height: 22px; overflow: hidden; position: relative; margin-top: 6px; }
  .eval-bar { height: 100%; background: linear-gradient(90deg, var(--accent), var(--long)); border-radius: 6px; transition: width .4s; }
  .eval-bar-label { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; }

  .footer { text-align: center; color: var(--muted); font-size: 10px; margin-top: 14px; }
  .drop-hint {
    border: 1px dashed var(--border); border-radius: 8px; padding: 10px; text-align: center;
    color: var(--muted); font-size: 11px; margin-bottom: 14px; cursor: pointer;
  }
  .drop-hint.drag { border-color: var(--accent); color: var(--accent); background: rgba(45,212,191,.05); }
</style>
</head>
<body>
<div class="wrap">

  <div class="header">
    <div class="brand">
      <h1>JHL LIVE FEED</h1>
      <span class="sub">v2 · Tak Signal Terminal</span>
    </div>
    <div class="header-right">
      <div class="fng">
        <div class="label">Fear &amp; Greed</div>
        <div class="score" id="fng-score">—</div>
        <div class="cls" id="fng-cls">—</div>
        <div class="fng-bar"><div class="needle" id="fng-needle" style="left:50%"></div></div>
      </div>
      <div class="conn">
        <span class="dot off" id="conn-dot"></span>
        <span id="conn-text">disconnected</span>
      </div>
    </div>
  </div>

  <div class="drop-hint" id="drop">
    Polling <code>/api/signals</code> every 15s. No server? Drag &amp; drop <b>signal_bus.json</b> here (or click to browse).
    <input type="file" id="file-input" accept=".json" style="display:none" />
  </div>

  <div class="status-row">
    <div class="stat"><div class="k">Last Scan</div><div class="v small" id="s-last">—</div></div>
    <div class="stat"><div class="k">Next Scan</div><div class="v small" id="s-next">—</div></div>
    <div class="stat"><div class="k">Active Pairs</div><div class="v" id="s-active">0</div></div>
    <div class="stat"><div class="k">Signals Fired</div><div class="v" id="s-fired">0</div></div>
    <div class="stat"><div class="k">Killed</div><div class="v" id="s-killed">0</div></div>
    <div class="stat"><div class="k">S-Grade</div><div class="v" id="s-sgrade">0</div></div>
  </div>

  <div class="grid">
    <div>
      <div class="panel">
        <h2>Live Signals <span class="badge" id="sig-count">0</span></h2>
        <div id="signals"><div class="empty">Awaiting scan…</div></div>
      </div>
    </div>

    <div>
      <div class="panel">
        <h2>Regime Map <span class="badge" id="regime-count">0</span></h2>
        <div class="regime-map" id="regime-map"><div class="empty">—</div></div>
      </div>

      <div class="panel">
        <h2>🎯 RTS Sniper <span class="badge" id="rts-count">0</span></h2>
        <div id="rts-signals"><div class="empty">RTS Sniper — no kill shots this cycle.</div></div>
      </div>

      <div class="panel">
        <h2>Killed Signals <span class="badge" id="kill-count">0</span></h2>
        <div id="killed"><div class="empty">No kills this cycle.</div></div>
      </div>

      <div class="panel">
        <h2>Eval Progress</h2>
        <div style="font-size:11px;color:var(--muted)">Quiet hours (10PM–5AM CDT)</div>
        <div class="eval-bar-wrap">
          <div class="eval-bar" id="quiet-bar" style="width:0%"></div>
          <div class="eval-bar-label" id="quiet-label">—</div>
        </div>
        <div style="font-size:11px;color:var(--muted);margin-top:12px">Scan duration</div>
        <div class="eval-bar-wrap">
          <div class="eval-bar" id="dur-bar" style="width:0%"></div>
          <div class="eval-bar-label" id="dur-label">—</div>
        </div>
      </div>
    </div>
  </div>

  <div class="footer" id="footer">JHL Holdings · Tak v3 scanner → signal_bus.json · defensive read-only view</div>
</div>

<script>
"use strict";

const POLL_MS = 15000;
const STALE_MS = 5 * 60 * 1000;

const LOCAL_URL = "/api/signals";
const CF_URL    = "/api/signals";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>\"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function num(v, d = 2) {
  const n = Number(v);
  return isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: d }) : "—";
}
function fmtTime(iso) {
  if (!iso) return "—";
  const t = new Date(iso);
  if (isNaN(t)) return "—";
  return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) +
    " " + t.toLocaleDateString([], { month: "short", day: "numeric" });
}
function setConn(state, text) {
  const dot = document.getElementById("conn-dot");
  dot.className = "dot " + state;
  document.getElementById("conn-text").textContent = text;
}

function renderFng(fg) {
  const score = fg && fg.score != null ? Number(fg.score) : null;
  document.getElementById("fng-score").textContent = score == null ? "—" : score;
  document.getElementById("fng-cls").textContent = fg && fg.label ? fg.label : "—";
  const pct = score == null ? 50 : Math.max(0, Math.min(100, score));
  document.getElementById("fng-needle").style.left = pct + "%";
  const col = score == null ? "var(--neutral)" : score < 25 ? "var(--fear)"
    : score > 75 ? "var(--greed)" : "var(--neutral)";
  document.getElementById("fng-score").style.color = col;
  document.getElementById("fng-cls").style.color = col;
}

function renderStats(bus) {
  const st = bus.session_stats || {};
  document.getElementById("s-last").textContent = fmtTime(bus.last_scan);
  document.getElementById("s-next").textContent = fmtTime(bus.next_scan);
  document.getElementById("s-active").textContent = bus.active_pairs || 0;
  document.getElementById("s-fired").textContent = st.signals_fired || 0;
  document.getElementById("s-killed").textContent = st.signals_killed || 0;
  document.getElementById("s-sgrade").textContent = st.s_grade_count || 0;

  const quiet = !!bus.quiet_hours;
  document.getElementById("quiet-bar").style.width = quiet ? "100%" : "0%";
  document.getElementById("quiet-bar").style.background = quiet ? "var(--caution)" : "var(--long)";
  document.getElementById("quiet-label").textContent = quiet ? "ALERTS OFF (10PM–5AM)" : "ACTIVE";

  const dur = Number(st.scan_duration_sec) || 0;
  const durPct = Math.max(4, Math.min(100, (dur / 60) * 100));
  document.getElementById("dur-bar").style.width = durPct + "%";
  document.getElementById("dur-label").textContent = dur ? dur.toFixed(1) + "s" : "—";
}

function signalCard(s, idx) {
  const bias = String(s.bias || "").toUpperCase();
  const grade = String(s.grade || "").toUpperCase();
  const mtf = String(s.mtf_verdict || s.mtfverdict || "PARTIAL").toUpperCase();
  const tags = [];

  if (String(s.remi_status || s.remistatus || "").toUpperCase() === "CLEAN") {
    tags.push('<span class="tag clean">REMI CLEAN</span>');
  }
  if (s.remi_caution || s.remicaution) {
    tags.push('<span class="tag caution">CAUTION</span>');
  }
  tags.push(`<span class="tag mtf-${esc(mtf)}">MTF ${esc(mtf)}</span>`);

  return `
    <div class="signal-card ${bias.toLowerCase()}" onclick="openModal(${idx})">
      <div class="sig-top">
        <div>
          <span class="sig-pair">${esc(s.pair)}</span>
          <span class="sig-bias bias-${esc(bias)}">${esc(bias)}</span>
        </div>
        <div><span class="grade grade-${esc(grade)}">${esc(grade)}</span></div>
      </div>
      <div class="sig-meta">
        <span>engine <b>${esc(s.engine)}</b></span>
        <span>conv <b>${num(s.conviction, 3)}</b></span>
        <span>RR <b>${num(s.rr, 2)}</b></span>
        <span>regime <b>${esc(s.regime)}</b></span>
      </div>
      <div class="sig-levels">
        <div class="lvl"><div class="lk">Entry</div><div class="lv">${num(s.entry, 6)}</div></div>
        <div class="lvl"><div class="lk">Stop</div><div class="lv">${num(s.sl, 6)}</div></div>
        <div class="lvl"><div class="lk">Target</div><div class="lv">${num(s.tp, 6)}</div></div>
        <div class="lvl"><div class="lk">MTF</div><div class="lv">${num(s.mtf_score || s.mtfscore, 2)}</div></div>
      </div>
      <div class="sig-tags">${tags.join("")}</div>
    </div>`;
}

function renderSignals(signals) {
  const el = document.getElementById("signals");
  const raw = Array.isArray(signals) ? signals : [];
  const list = raw.filter((s) => {
    const action = String(s.action_state || s.actionstate || "").toUpperCase();
    const intent = String(s.intent || "").toUpperCase();
    const grade = String(s.grade || "").toUpperCase();

    if (s.feed_eligible === false || s.feedeligible === false) return false;
    if (action === "WAIT" || intent === "WAIT" || intent === "PROBE") return false;
    if (action && action !== "CLICK") return false;

    return grade === "S" || grade === "A";
  });

  document.getElementById("sig-count").textContent = list.length;
  if (!list.length) {
    el.innerHTML = '<div class="empty">No attack-ready kills this cycle.</div>';
    return;
  }
  window.__liveSignals = list;
  el.innerHTML = list.map((s, i) => signalCard(s, i)).join("");
}

function renderRTSSignals(signals) {
  const el = document.getElementById("rts-signals");
  const raw = Array.isArray(signals) ? signals : [];
  const list = raw.filter((s) => {
    const action = String(s.action_state || s.actionstate || "").toUpperCase();
    const intent = String(s.intent || "").toUpperCase();
    const grade = String(s.grade || "").toUpperCase();
    if (!(grade === "S" || grade === "A")) return false;
    if (action && action !== "CLICK") return false;
    return ["ATTACKTRAP","ATTACKBREAK","ATTACK","ATTACK_TRAP","ATTACK_BREAK"].includes(intent);
  });

  document.getElementById("rts-count").textContent = list.length;
  if (!list.length) {
    el.innerHTML = '<div class="empty">RTS Sniper — no kill shots this cycle.</div>';
    return;
  }
  el.innerHTML = list.map((s, i) => signalCard(s, i)).join("");
}

function renderKilled(killed) {
  const el = document.getElementById("killed");
  const list = Array.isArray(killed) ? killed : [];
  document.getElementById("kill-count").textContent = list.length;
  if (!list.length) {
    el.innerHTML = '<div class="empty">No kills this cycle.</div>';
    return;
  }
  el.innerHTML = list.map((k) => `
    <div class="kill-row">
      <span><b>${esc(k.pair)}</b> ${esc(k.bias || "")} ${esc(k.engine || "")}</span>
      <span class="kill-reason">${esc(k.kill_reason || k.killreason || "KILLED")}</span>
    </div>`).join("");
}

let busData = null;
window.__liveSignals = [];

function openModal(idx) {
  const s = window.__liveSignals[idx];
  if (!s) return;
  alert(`${s.pair} ${s.bias} ${s.grade} | ${s.engine} | intent=${s.intent || "—"}`);
}

function render(bus) {
  if (!bus || typeof bus !== "object") return;
  busData = bus;
  renderFng(bus.f_g || bus.fg);
  renderStats(bus);
  renderRegime(bus.regime_map || {});
  renderSignals(bus.signals || []);
  renderRTSSignals(bus.signals || []);
  renderKilled(bus.killed_signals || []);
  const lastIso = bus.last_scan;
  const last = lastIso ? new Date(lastIso).getTime() : 0;
  const age = last ? Date.now() - last : Infinity;
  if (age < STALE_MS) setConn("live", `live • ${fmtTime(lastIso)}`);
  else if (isFinite(age)) setConn("stale", `stale • ${fmtTime(lastIso)}`);
  else setConn("off", "no scan data");
}

async function poll() {
  const urls = [LOCAL_URL, CF_URL];
  for (const url of urls) {
    try {
      const r = await fetch(url, { cache: "no-store" });
      const bus = await r.json();
      if (bus && !bus.error) { render(bus); return; }
    } catch (e) {}
  }
  setConn("off", "no signal source");
}

const drop = document.getElementById("drop");
const fileInput = document.getElementById("file-input");
drop.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (e) => {
  const f = e.target.files[0]; if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    try { render(JSON.parse(reader.result)); setConn("live", `loaded ${f.name}`); }
    catch { setConn("off", "invalid JSON"); }
  };
  reader.readAsText(f);
});
["dragenter","dragover"].forEach((ev) => {
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); });
});
["dragleave","drop"].forEach((ev) => {
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); });
});
drop.addEventListener("drop", (e) => {
  const f = e.dataTransfer.files[0]; if (!f) return;
  const reader = new FileReader();
  reader.onload = () => {
    try { render(JSON.parse(reader.result)); setConn("live", `loaded ${f.name}`); }
    catch { setConn("off", "invalid JSON"); }
  };
  reader.readAsText(f);
});

poll();
setInterval(poll, POLL_MS);
</script>
</body>
</html>
`;

function corsHeaders(contentType) {
  const h = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, PUT, POST, OPTIONS",
    "Access-Control-Allow-Headers": "X-JHL-Secret, Content-Type",
  };
  if (contentType) h["Content-Type"] = contentType;
  return h;
}

export default {
  async fetch(request, env) {
    const JHL_BUS = env.JHL_BUS;
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders() });
    }

    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/index.html")) {
      return new Response(HTML, {
        headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-cache" }
      });
    }

    if (request.method === "GET" && url.pathname === "/api/signals") {
      const data = await JHL_BUS.get(KV_KEY);
      if (!data) {
        return new Response(JSON.stringify({ error: "no data yet" }), {
          status: 404, headers: corsHeaders("application/json"),
        });
      }
      return new Response(data, { headers: corsHeaders("application/json") });
    }

    if ((request.method === "PUT" || request.method === "POST") && url.pathname === "/update") {
      const auth = request.headers.get("X-JHL-Secret");
      if (auth !== WRITE_SECRET) return new Response("Unauthorized", { status: 401 });
      const body = await request.text();
      await JHL_BUS.put(KV_KEY, body);
      return new Response(JSON.stringify({ ok: true }), { headers: corsHeaders("application/json") });
    }

    return new Response("Not found", { status: 404 });
  },
};
