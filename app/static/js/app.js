/* RoadSense AI – single page front-end (vanilla JS, Chart.js, Leaflet) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const view = $("#view");
let CFG = null;
let charts = [];
let maps = [];
let pollTimer = null;

const SEV_ORDER = ["Critical", "Moderate", "Minor"];
const SEV_ICON = {
  Critical: '<svg viewBox="0 0 16 16" fill="currentColor"><path d="M8 1.5 15 14H1z" opacity=".25"/><path d="M8 1.2c.3 0 .6.2.8.5l6.6 11.7c.3.6-.1 1.3-.8 1.3H1.4c-.7 0-1.1-.7-.8-1.3L7.2 1.7c.2-.3.5-.5.8-.5zM7.2 6v4h1.6V6zm0 5.2v1.6h1.6v-1.6z"/></svg>',
  Moderate: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="7" opacity=".25"/><path d="M7.2 4h1.6v5H7.2zm0 6.2h1.6v1.6H7.2z"/></svg>',
  Minor: '<svg viewBox="0 0 16 16" fill="currentColor"><circle cx="8" cy="8" r="7" opacity=".25"/><path d="m6.8 10.6-2.4-2.4 1.1-1.1 1.3 1.3 3.7-3.7 1.1 1.1z"/></svg>',
};
const ICON = {
  pdf: '<svg viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm4 18H6V4h7v5h5zM8 13h8v2H8zm0 4h8v2H8z"/></svg>',
  csv: '<svg viewBox="0 0 24 24"><path d="M5 20h14v-2H5zm7-18-5.5 5.5 1.4 1.4L11 5.8V16h2V5.8l3.1 3.1 1.4-1.4z" transform="rotate(180 12 12)"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M6 19a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7H6zM19 4h-3.5l-1-1h-5l-1 1H5v2h14z"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"/></svg>',
  upload: '<svg viewBox="0 0 24 24"><path d="M19.35 10.04A7.49 7.49 0 0 0 12 4C9.11 4 6.6 5.64 5.35 8.04A5.994 5.994 0 0 0 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM14 13v4h-4v-4H7l5-5 5 5z"/></svg>',
  road: '<svg viewBox="0 0 24 24"><path d="M11 4h2v3h-2zm0 6h2v4h-2zm0 7h2v3h-2zM4.5 20 8 4h2L6.6 20zm15 0L16 4h-2l3.4 16z"/></svg>',
  refresh: '<svg viewBox="0 0 24 24"><path d="M17.65 6.35A7.96 7.96 0 0 0 12 4a8 8 0 1 0 7.73 10h-2.08A6 6 0 1 1 12 6c1.66 0 3.14.69 4.22 1.78L13 11h7V4z"/></svg>',
  pin: '<svg viewBox="0 0 24 24"><path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7zm0 9.5a2.5 2.5 0 1 1 0-5 2.5 2.5 0 0 1 0 5z"/></svg>',
};

// ----------------------------------------------------------------- utils
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
const label = (cls) => CFG.classes[cls]?.label ?? cls;
const clsColor = (cls) => CFG.classes[cls]?.color ?? "#888";
const sevColor = (s) => CFG.severity[s]?.color ?? "#888";
const chip = (s) => `<span class="chip ${s}">${SEV_ICON[s] || ""}${s}</span>`;
const rciColor = (v) => (v >= 70 ? "var(--minor)" : v >= 40 ? "var(--moderate)" : "var(--critical)");
const rciInk = (v) => (v >= 70 ? "var(--minor-ink)" : v >= 40 ? "var(--moderate-ink)" : "var(--critical-ink)");
const rciHex = (v) => (v >= 70 ? "#0ca30c" : v >= 40 ? "#fab219" : "#d03b3b");

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 2600);
}

function cleanup() {
  charts.forEach((c) => c.destroy());
  charts = [];
  maps.forEach((m) => m.remove());
  maps = [];
  clearInterval(pollTimer);
  pollTimer = null;
  window.onresize = null;
  document.onkeydown = null;
}

// ----------------------------------------------------------------- charts
function chartDefaults() {
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = "#52514e";
  Chart.defaults.borderColor = "#ecebe6";
  Chart.defaults.plugins.legend.display = false;
  Chart.defaults.plugins.tooltip.backgroundColor = "#1f2228";
  Chart.defaults.plugins.tooltip.padding = 10;
  Chart.defaults.plugins.tooltip.cornerRadius = 8;
  Chart.defaults.plugins.tooltip.boxPadding = 4;
  Chart.defaults.maintainAspectRatio = false;
}

function mkChart(canvas, cfg) {
  const c = new Chart(canvas, cfg);
  charts.push(c);
  return c;
}

const axisX = (extra = {}) => ({ grid: { color: "#ecebe6" }, border: { display: false }, ticks: { precision: 0 }, beginAtZero: true, ...extra });
const axisY = (extra = {}) => ({ grid: { display: false }, border: { color: "#cfcdc5" }, ...extra });

function hbar(canvas, labels, values, colors, unit = "defects") {
  return mkChart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 4, borderSkipped: "start", barPercentage: 0.62, categoryPercentage: 0.9 }] },
    options: {
      indexAxis: "y",
      scales: { x: axisX(), y: axisY() },
      plugins: { tooltip: { callbacks: { label: (c) => ` ${c.parsed.x} ${unit}` } } },
    },
  });
}

function stackedSeverity(canvas, matrix) {
  const classes = Object.keys(CFG.classes);
  return mkChart(canvas, {
    type: "bar",
    data: {
      labels: classes.map(label),
      datasets: SEV_ORDER.map((s) => ({
        label: s,
        data: classes.map((c) => matrix[c]?.[s] ?? 0),
        backgroundColor: sevColor(s),
        borderColor: "#ffffff",
        borderWidth: { left: 1, right: 1 },
        borderSkipped: false,
        borderRadius: 3,
        barPercentage: 0.62,
        categoryPercentage: 0.9,
      })),
    },
    options: {
      indexAxis: "y",
      scales: { x: axisX({ stacked: true }), y: axisY({ stacked: true }) },
      plugins: { tooltip: { mode: "index", intersect: false } },
    },
  });
}

function sevLegend() {
  return `<div class="legend">${SEV_ORDER.map((s) => `<span><i class="swatch" style="background:${sevColor(s)}"></i>${s}</span>`).join("")}</div>`;
}

// ----------------------------------------------------------------- router
const routes = [
  [/^#?\/?$/, renderDashboard, "dashboard"],
  [/^#\/new$/, renderNew, "new"],
  [/^#\/inspections$/, renderList, "inspections"],
  [/^#\/inspection\/(\w+)$/, renderInspection, "inspections"],
];

async function router() {
  cleanup();
  const h = location.hash || "#/";
  for (const [re, fn, nav] of routes) {
    const m = h.match(re);
    if (m) {
      $$(".sidebar nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === nav));
      window.scrollTo(0, 0);
      try {
        await fn(...m.slice(1));
      } catch (e) {
        console.error(e);
        view.innerHTML = `<div class="card empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`;
      }
      return;
    }
  }
  location.hash = "#/";
}

async function boot() {
  chartDefaults();
  CFG = await api("/api/config");
  updateModelStatus();
  window.addEventListener("hashchange", router);
  router();
}

async function updateModelStatus() {
  const el = $("#model-status");
  const cfg = await api("/api/config").catch(() => null);
  if (cfg && cfg.model && cfg.model.device) {
    CFG.model = cfg.model;
    el.classList.add("ready");
    el.innerHTML = `<span class="dot"></span><span>Models ready · ${cfg.model.device.toUpperCase()}<br>RDD2022 + pothole detector + segmenter</span>`;
  } else {
    setTimeout(updateModelStatus, 2000);
  }
}

// =================================================================== DASHBOARD
async function renderDashboard() {
  view.innerHTML = `<div class="page-head"><div><h1>Network dashboard</h1><p>Loading…</p></div></div>
    <div class="kpis">${'<div class="kpi skeleton" style="height:92px"></div>'.repeat(6)}</div>`;
  const s = await api("/api/stats");

  if (!s.roads_inspected) {
    view.innerHTML = `
      <div class="page-head"><div><h1>Network dashboard</h1><p>Road damage analytics across all inspections</p></div></div>
      <div class="card empty">
        ${ICON.road}
        <h2>No inspections yet</h2>
        <p>Upload road images or dashcam / CCTV video, and the deep-learning pipeline will detect potholes and cracks,<br>score their severity and build this dashboard.</p>
        <div class="actions" style="justify-content:center;margin-top:16px">
          <a class="btn primary lg" href="#/new">${ICON.plus}New inspection</a>
          <a class="btn lg" href="#/new" onclick="sessionStorage.setItem('pickSamples','1')">Try sample images</a>
        </div>
      </div>`;
    return;
  }

  const sevAll = s.by_severity;
  const sevPh = s.pothole_severity;
  view.innerHTML = `
    <div class="page-head">
      <div><h1>Network dashboard</h1><p>Road damage analytics across ${s.roads_inspected} completed inspection${s.roads_inspected === 1 ? "" : "s"}</p></div>
      <div class="actions">
        <a class="btn" href="/api/export.csv">${ICON.csv}Export all detections</a>
        <a class="btn primary" href="#/new">${ICON.plus}New inspection</a>
      </div>
    </div>

    <div class="kpis">
      <div class="kpi"><div class="label">Roads inspected</div><div class="value">${s.roads_inspected}</div><div class="hint">${s.needs_repair} need repair within 30 days</div></div>
      <div class="kpi"><div class="label">Defects detected</div><div class="value">${s.total_defects}</div><div class="hint">all damage types</div></div>
      <div class="kpi"><div class="label">Potholes detected</div><div class="value">${s.potholes}</div><div class="hint">verified by segmentation</div></div>
      <div class="kpi sev Critical"><div class="label">${SEV_ICON.Critical.replace("<svg", '<svg width="13" height="13" style="color:var(--critical)"')}Critical</div><div class="value" data-sev="Critical">${sevAll.Critical}</div><div class="hint" data-sevhint="Critical"></div></div>
      <div class="kpi sev Moderate"><div class="label">${SEV_ICON.Moderate.replace("<svg", '<svg width="13" height="13" style="color:var(--moderate-ink)"')}Moderate</div><div class="value" data-sev="Moderate">${sevAll.Moderate}</div><div class="hint" data-sevhint="Moderate"></div></div>
      <div class="kpi sev Minor"><div class="label">${SEV_ICON.Minor.replace("<svg", '<svg width="13" height="13" style="color:var(--minor)"')}Minor</div><div class="value" data-sev="Minor">${sevAll.Minor}</div><div class="hint" data-sevhint="Minor"></div></div>
      <div class="kpi"><div class="label">Avg. condition index</div><div class="value" style="color:${rciInk(s.avg_rci)}">${s.avg_rci ?? "–"}</div><div class="hint">0 failed · 100 perfect</div></div>
    </div>
    <div class="toolbar" style="margin-top:-6px">
      <span class="small muted">Severity tiles show</span>
      <div class="seg" id="sev-scope"><button class="on" data-v="all">All damage</button><button data-v="pothole">Potholes only</button></div>
    </div>

    <div class="grid g-12">
      <div class="card span-6"><div class="card-head"><h2>Defects by type</h2><span class="sub">RDD2022 classes + derived sections</span></div>
        <div class="card-body"><div class="chart-box"><canvas id="c-type"></canvas></div></div></div>
      <div class="card span-6"><div class="card-head"><h2>Severity by damage type</h2><span class="sub">count of defects</span></div>
        <div class="card-body"><div class="chart-box"><canvas id="c-matrix"></canvas></div>${sevLegend()}</div></div>

      <div class="card span-7"><div class="card-head"><h2>Inspection map</h2><span class="sub">coloured by road condition index</span></div>
        ${s.map.length ? '<div id="map"></div>' : `<div class="map-empty"><div>${ICON.pin.replace("<svg", '<svg width="40" height="40" fill="#cfcdc5"')}<br>No GPS-tagged inspections yet.<br><span class="small">Add coordinates when creating an inspection, or upload geotagged photos.</span></div></div>`}
      </div>
      <div class="card span-5"><div class="card-head"><h2>Repair priority</h2><span class="sub">worst roads first</span></div>
        <div class="table-wrap" style="max-height:330px"><table class="tbl">
          <thead><tr><th>Road</th><th class="r">RCI</th><th>Critical</th><th>Priority</th></tr></thead>
          <tbody>${s.worst.map((w) => `
            <tr class="clickable" onclick="location.hash='#/inspection/${w.id}'">
              <td><b>${esc(w.road_name || w.name)}</b><div class="small faint">${esc(w.location || w.name)}</div></td>
              <td class="r num"><b style="color:${rciInk(w.rci)}">${w.rci.toFixed(0)}</b><div class="small faint">${esc(w.rating)}</div></td>
              <td class="num">${w.critical ? chip("Critical").replace("Critical</span>", `${w.critical}</span>`) : '<span class="faint">0</span>'}</td>
              <td class="small">${esc(w.priority)}</td>
            </tr>`).join("")}</tbody></table></div>
      </div>

      <div class="card span-4"><div class="card-head"><h2>Road condition ratings</h2><span class="sub">inspections per band</span></div>
        <div class="card-body"><div class="chart-box short"><canvas id="c-rating"></canvas></div></div></div>
      <div class="card span-4"><div class="card-head"><h2>Severity score distribution</h2><span class="sub">0–100 per defect</span></div>
        <div class="card-body"><div class="chart-box short"><canvas id="c-hist"></canvas></div></div></div>
      <div class="card span-4"><div class="card-head"><h2>Inspection activity</h2><span class="sub">defects found per day</span></div>
        <div class="card-body"><div class="chart-box short"><canvas id="c-day"></canvas></div></div></div>
    </div>`;

  // severity tiles scope toggle
  const setScope = (scope) => {
    const src = scope === "pothole" ? sevPh : sevAll;
    const tot = SEV_ORDER.reduce((a, k) => a + src[k], 0) || 1;
    SEV_ORDER.forEach((k) => {
      $(`[data-sev="${k}"]`).textContent = src[k];
      $(`[data-sevhint="${k}"]`).textContent = `${Math.round((100 * src[k]) / tot)}% of ${scope === "pothole" ? "potholes" : "defects"}`;
    });
    $$("#sev-scope button").forEach((b) => b.classList.toggle("on", b.dataset.v === scope));
  };
  $$("#sev-scope button").forEach((b) => (b.onclick = () => setScope(b.dataset.v)));
  setScope("all");

  const classes = Object.keys(CFG.classes);
  hbar($("#c-type"), classes.map(label), classes.map((c) => s.by_class[c]), classes.map(clsColor));
  stackedSeverity($("#c-matrix"), s.matrix);

  const bands = ["Good", "Satisfactory", "Fair", "Poor", "Very Poor", "Serious", "Failed"];
  const ramp = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab"];
  mkChart($("#c-rating"), {
    type: "bar",
    data: { labels: bands, datasets: [{ data: bands.map((b) => s.ratings[b] || 0), backgroundColor: ramp, borderRadius: 4, borderSkipped: "start", barPercentage: 0.7 }] },
    options: { scales: { y: axisX(), x: axisY({ ticks: { autoSkip: false, maxRotation: 40, font: { size: 10.5 } } }) },
      plugins: { tooltip: { callbacks: { label: (c) => ` ${c.parsed.y} inspection(s)` } } } },
  });
  mkChart($("#c-hist"), {
    type: "bar",
    data: {
      labels: s.score_hist.map((_, i) => `${i * 10}–${i * 10 + 10}`),
      datasets: [{ data: s.score_hist, backgroundColor: s.score_hist.map((_, i) => (i >= 6 ? sevColor("Critical") : i >= 3 ? sevColor("Moderate") : sevColor("Minor"))),
        borderRadius: 3, borderSkipped: "start", barPercentage: 0.92, categoryPercentage: 0.95 }],
    },
    options: { scales: { y: axisX(), x: axisY({ ticks: { font: { size: 10 }, maxRotation: 0, autoSkip: true } }) },
      plugins: { tooltip: { callbacks: { title: (c) => `Score ${c[0].label}`, label: (c) => ` ${c.parsed.y} defects` } } } },
  });
  mkChart($("#c-day"), {
    type: "bar",
    data: { labels: s.per_day.map((d) => d.day.slice(5)), datasets: [{ data: s.per_day.map((d) => d.defects), backgroundColor: "#2a78d6", borderRadius: 4, borderSkipped: "start", maxBarThickness: 36 }] },
    options: { scales: { y: axisX(), x: axisY() },
      plugins: { tooltip: { callbacks: { title: (c) => s.per_day[c[0].dataIndex].day,
        label: (c) => { const d = s.per_day[c.dataIndex]; return [` ${d.defects} defects`, ` ${d.critical} critical`, ` ${d.inspections} inspection(s)`]; } } } } },
  });

  if (s.map.length) {
    const map = L.map("map", { scrollWheelZoom: false });
    maps.push(map);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(map);
    const pts = s.map.map((p) => {
      L.circleMarker([p.lat, p.lon], { radius: 9, color: "#fff", weight: 2, fillColor: rciHex(p.rci), fillOpacity: 0.95 })
        .bindPopup(`<b>${esc(p.name)}</b><br>RCI <b>${p.rci.toFixed(0)}</b> · ${esc(p.rating)}<br>${p.total} defects<br><a href="#/inspection/${p.id}">Open inspection →</a>`)
        .addTo(map);
      return [p.lat, p.lon];
    });
    map.fitBounds(pts, { padding: [40, 40], maxZoom: 15 });
  }
}

// =================================================================== NEW INSPECTION
async function renderNew() {
  let files = [];
  const picked = new Set();
  let view_mode = CFG.defaults.view;

  view.innerHTML = `
    <div class="page-head"><div><h1>New inspection</h1><p>Upload road images or a dashcam / CCTV video. Frames are extracted, analysed and scored automatically.</p></div></div>
    <div id="new-wrap" class="grid g-12">
      <div class="span-7" style="display:flex;flex-direction:column;gap:16px">
        <div class="card card-pad">
          <h2 style="margin-bottom:12px">1 · Media</h2>
          <label class="dropzone" id="drop">
            ${ICON.upload}
            <b>Drop images or a video here</b>
            <span class="muted">or click to browse · JPG, PNG, MP4, AVI, MOV, MKV · multiple images allowed</span>
            <input id="file" type="file" multiple accept="image/*,video/*" hidden>
          </label>
          <div class="filelist" id="filelist"></div>
        </div>
        <div class="card card-pad">
          <div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:10px;flex-wrap:wrap">
            <h2>…or try sample images <span class="faint small" style="font-weight:400">RDD2022 test set · 7 countries</span></h2>
            <div class="actions"><button class="btn" id="pick-all">Select all</button><button class="btn" id="pick-none">Clear</button></div>
          </div>
          <div class="samples" id="samples">${CFG.samples.map((n) => `
            <div class="sample" data-n="${esc(n)}" title="${esc(n)}"><img loading="lazy" src="/samples/${encodeURIComponent(n)}" alt=""><span>${esc(n.replace(/_\d+\.jpg$/, "").replace(/_/g, " "))}</span></div>`).join("")}
          </div>
        </div>
      </div>

      <div class="span-5" style="display:flex;flex-direction:column;gap:16px">
        <div class="card card-pad">
          <h2 style="margin-bottom:12px">2 · Road details <span class="faint small" style="font-weight:400">optional</span></h2>
          <div class="form-grid">
            <div class="field full"><label for="f-name">Inspection name</label><input type="text" id="f-name" placeholder="e.g. Morning patrol – Ward 12"></div>
            <div class="field"><label for="f-road">Road / segment</label><input type="text" id="f-road" placeholder="e.g. MG Road, km 2–4"></div>
            <div class="field"><label for="f-loc">Area / ward</label><input type="text" id="f-loc" placeholder="e.g. Ward 12, North zone"></div>
            <div class="field"><label for="f-lat">Latitude</label><input type="number" step="any" id="f-lat" placeholder="auto from photo EXIF"></div>
            <div class="field"><label for="f-lon">Longitude</label><input type="number" step="any" id="f-lon" placeholder="auto from photo EXIF"></div>
            <div class="field full"><div id="picker" class="map" style="height:180px;border-radius:8px;border:1px solid var(--border)"></div>
              <span class="help">Click the map to set the location, or <a href="#" id="geo">use my current location</a>.</span></div>
          </div>
        </div>
        <div class="card card-pad">
          <h2 style="margin-bottom:12px">3 · Analysis settings</h2>
          <div class="form-grid">
            <div class="field full"><label>Camera view</label>
              <div class="seg" id="viewmode"><button data-v="auto">Auto-detect</button><button data-v="dashcam">Dashcam / street</button><button data-v="topdown">Close-up / drone</button></div>
              <span class="help">Auto-detect decides per image: street-level views (sky visible) get perspective correction and stricter filtering; close-up or handheld photos of the pavement get close-up pothole detection.</span></div>
            <div class="field full"><label for="f-conf">Detection confidence threshold</label>
              <div class="range-row"><input type="range" id="f-conf" min="0.10" max="0.80" step="0.05" value="${CFG.defaults.conf}"><output id="o-conf">${CFG.defaults.conf.toFixed(2)}</output></div>
              <span class="help">Lower = more sensitive (more detections, more false alarms).</span></div>
            <div class="field full"><label for="f-fps">Video frame sampling</label>
              <div class="range-row"><input type="range" id="f-fps" min="1" max="10" step="1" value="${CFG.defaults.video_fps}"><output id="o-fps">${CFG.defaults.video_fps} fps</output></div>
              <span class="help">Frames analysed per second of video. Defects are tracked across frames so each is counted once.</span></div>
          </div>
          <div style="margin-top:18px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            <button class="btn primary lg" id="go" disabled>Run analysis</button>
            <span class="small muted" id="go-hint">Add media to start</span>
          </div>
        </div>
      </div>
    </div>`;

  const refresh = () => {
    const n = files.length + picked.size;
    const vids = files.filter((f) => f.type.startsWith("video/")).length;
    $("#go").disabled = n === 0 || (vids > 0 && n > 1);
    $("#go-hint").textContent = n === 0 ? "Add media to start"
      : vids > 1 || (vids && n > 1) ? "Upload one video at a time (without images)"
      : vids ? "1 video ready" : `${n} image${n > 1 ? "s" : ""} ready`;
    $("#filelist").innerHTML = files.map((f, i) => `
      <span class="filepill">${f.type.startsWith("video/") ? '<span class="vid"></span>' : `<img src="${URL.createObjectURL(f)}" alt="">`}
      <span title="${esc(f.name)}">${esc(f.name)}</span><span class="faint">${(f.size / 1e6).toFixed(1)} MB</span>
      <button data-i="${i}" aria-label="Remove">×</button></span>`).join("");
    $$("#filelist button").forEach((b) => (b.onclick = (e) => { e.preventDefault(); files.splice(+b.dataset.i, 1); refresh(); }));
  };
  const addFiles = (list) => {
    files.push(...[...list].filter((f) => /^(image|video)\//.test(f.type) || /\.(mp4|avi|mov|mkv|m4v|webm)$/i.test(f.name)));
    refresh();
  };
  const drop = $("#drop");
  $("#file").onchange = (e) => { addFiles(e.target.files); e.target.value = ""; };
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", (e) => addFiles(e.dataTransfer.files));

  const syncSamples = () => $$(".sample").forEach((el) => el.classList.toggle("on", picked.has(el.dataset.n)));
  $$(".sample").forEach((el) => (el.onclick = () => { picked.has(el.dataset.n) ? picked.delete(el.dataset.n) : picked.add(el.dataset.n); syncSamples(); refresh(); }));
  $("#pick-all").onclick = () => { CFG.samples.forEach((n) => picked.add(n)); syncSamples(); refresh(); };
  $("#pick-none").onclick = () => { picked.clear(); syncSamples(); refresh(); };
  if (sessionStorage.getItem("pickSamples")) {
    sessionStorage.removeItem("pickSamples");
    CFG.samples.forEach((n) => picked.add(n));
    syncSamples();
  }

  const setView = (v) => { view_mode = v; $$("#viewmode button").forEach((b) => b.classList.toggle("on", b.dataset.v === v)); };
  $$("#viewmode button").forEach((b) => (b.onclick = () => setView(b.dataset.v)));
  setView(view_mode);
  $("#f-conf").oninput = (e) => ($("#o-conf").textContent = (+e.target.value).toFixed(2));
  $("#f-fps").oninput = (e) => ($("#o-fps").textContent = `${e.target.value} fps`);

  // location picker
  const pm = L.map("picker", { scrollWheelZoom: false, attributionControl: false }).setView([20, 0], 1);
  maps.push(pm);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(pm);
  let marker = null;
  const setLoc = (lat, lon, zoom) => {
    $("#f-lat").value = lat.toFixed(6);
    $("#f-lon").value = lon.toFixed(6);
    marker ? marker.setLatLng([lat, lon]) : (marker = L.marker([lat, lon]).addTo(pm));
    if (zoom) pm.setView([lat, lon], zoom);
  };
  pm.on("click", (e) => setLoc(e.latlng.lat, e.latlng.lng));
  $("#geo").onclick = (e) => {
    e.preventDefault();
    navigator.geolocation?.getCurrentPosition((p) => setLoc(p.coords.latitude, p.coords.longitude, 15), () => toast("Location unavailable"));
  };

  refresh();
  $("#go").onclick = async () => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f));
    fd.append("samples", [...picked].join(","));
    fd.append("name", $("#f-name").value);
    fd.append("road_name", $("#f-road").value);
    fd.append("location", $("#f-loc").value);
    fd.append("lat", $("#f-lat").value);
    fd.append("lon", $("#f-lon").value);
    fd.append("conf", $("#f-conf").value);
    fd.append("view", view_mode);
    fd.append("video_fps", $("#f-fps").value);
    $("#go").disabled = true;
    $("#go-hint").textContent = "Uploading…";
    try {
      const { id } = await uploadWithProgress(fd, (p) => ($("#go-hint").textContent = `Uploading… ${Math.round(p * 100)}%`));
      location.hash = `#/inspection/${id}`;
    } catch (e) {
      toast(`Upload failed: ${e.message}`);
      $("#go").disabled = false;
      $("#go-hint").textContent = "";
    }
  };
}

function uploadWithProgress(fd, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", "/api/inspections");
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => {
      let body = {};
      try { body = JSON.parse(x.responseText); } catch (_) { /* ignore */ }
      x.status < 300 ? resolve(body) : reject(new Error(body.detail || x.statusText));
    };
    x.onerror = () => reject(new Error("network error"));
    x.send(fd);
  });
}

// =================================================================== LIST
async function renderList() {
  const rows = await api("/api/inspections");
  view.innerHTML = `
    <div class="page-head"><div><h1>Inspections</h1><p>${rows.length} inspection${rows.length === 1 ? "" : "s"} on record</p></div>
      <div class="actions"><a class="btn primary" href="#/new">${ICON.plus}New inspection</a></div></div>
    ${rows.length ? `<div class="toolbar">
      <input class="search" id="q" placeholder="Search by name, road or area…">
      <div class="seg" id="flt"><button class="on" data-v="">All</button><button data-v="crit">Has critical</button><button data-v="poor">RCI &lt; 55</button><button data-v="video">Video</button></div>
    </div><div class="insp-grid" id="grid"></div>` : `<div class="card empty">${ICON.road}<h2>No inspections yet</h2><p><a href="#/new">Create the first one →</a></p></div>`}`;
  if (!rows.length) return;
  let f = "";
  const draw = () => {
    const q = $("#q").value.toLowerCase();
    const list = rows.filter((r) => (!q || `${r.name} ${r.road_name} ${r.location}`.toLowerCase().includes(q))
      && (!f || (f === "crit" && r.summary?.by_severity.Critical) || (f === "poor" && r.summary && r.summary.rci < 55) || (f === "video" && r.media_type === "video")));
    $("#grid").innerHTML = list.map(card).join("") || '<p class="muted">No matching inspections.</p>';
  };
  const card = (r) => {
    const s = r.summary;
    const badge = s ? `<span class="rci-badge"><b style="color:${rciInk(s.rci)}">${s.rci.toFixed(0)}</b><small>RCI</small></span>` : "";
    const status = r.status === "done" ? "" : `<div class="small" style="color:${r.status === "failed" ? "var(--critical-ink)" : "var(--accent)"}">${r.status === "failed" ? "Failed: " : ""}${esc(r.message)}${r.status === "processing" ? ` · ${Math.round(r.progress * 100)}%` : ""}</div>`;
    return `<a class="card insp-card" href="#/inspection/${r.id}">
      <div class="thumb" style="${r.thumb ? `background-image:url('${r.thumb}')` : ""}"><span class="type">${r.media_type}</span><span class="badge">${badge}</span></div>
      <div class="body">
        <div class="title">${esc(r.name)}</div>
        <div class="small muted">${esc([r.road_name, r.location].filter(Boolean).join(" · ") || "—")} · ${fmtDate(r.created_at)}</div>
        ${s ? `<div class="counts">${SEV_ORDER.map((k) => (s.by_severity[k] ? chip(k).replace(`${k}</span>`, `${s.by_severity[k]} ${k}</span>`) : "")).join("") || '<span class="chip Minor">No damage found</span>'}</div>
        <div class="small faint">${s.total} defects · ${esc(s.rating)} · ${esc(s.priority)}</div>` : status}
      </div></a>`;
  };
  $("#q").oninput = draw;
  $$("#flt button").forEach((b) => (b.onclick = () => { f = b.dataset.v; $$("#flt button").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  draw();
  if (rows.some((r) => r.status === "processing" || r.status === "queued")) pollTimer = setInterval(() => router(), 4000);
}

// =================================================================== INSPECTION DETAIL
async function renderInspection(id) {
  const insp = await api(`/api/inspections/${id}`);
  if (insp.status !== "done") return renderProgress(insp);
  const s = insp.summary;
  const frames = insp.frames;
  const isVideo = insp.media_type === "video";

  view.innerHTML = `
    <div class="page-head">
      <div><h1>${esc(insp.name)}</h1>
        <p>${esc([insp.road_name, insp.location].filter(Boolean).join(" · "))}${insp.road_name || insp.location ? " · " : ""}${fmtDate(insp.created_at)} ·
          ${s.frames_analyzed} ${isVideo ? "video frames" : "image(s)"} analysed${insp.lat != null ? ` · <span title="GPS">${insp.lat.toFixed(5)}, ${insp.lon.toFixed(5)}</span>` : ""}</p></div>
      <div class="actions">
        <a class="btn primary" href="/api/inspections/${id}/report.pdf">${ICON.pdf}PDF report</a>
        <a class="btn" href="/api/export.csv?inspection_id=${id}">${ICON.csv}CSV</a>
        <button class="btn" id="rerun" title="Run the latest models on the original uploads again">${ICON.refresh}Re-analyse</button>
        <button class="btn danger" id="del">${ICON.trash}Delete</button>
      </div>
    </div>

    <div class="grid g-12">
      <div class="card card-pad span-4">
        <div class="rci">
          <div class="rci-ring" style="--v:${s.rci};--c:${rciColor(s.rci)}" data-v="${s.rci.toFixed(0)}" role="img" aria-label="Road condition index ${s.rci.toFixed(0)} of 100"></div>
          <div class="meta"><span class="small muted">Road Condition Index</span><b>${esc(s.rating)}</b>
            <span class="small muted">Repair priority</span><div style="font-weight:600">${esc(s.priority)}</div></div>
        </div>
      </div>
      <div class="span-8"><div class="kpis k5" style="margin:0">
        <div class="kpi"><div class="label">Defects</div><div class="value">${s.total}</div><div class="hint">${s.frames_with_damage} of ${s.frames_analyzed} frames with damage</div></div>
        <div class="kpi"><div class="label">Potholes</div><div class="value">${s.by_class.pothole}</div><div class="hint">${s.by_class.alligator_crack} alligator · ${s.by_class.longitudinal_crack + s.by_class.transverse_crack} linear cracks</div></div>
        ${SEV_ORDER.map((k) => `<div class="kpi sev ${k}"><div class="label">${k}</div><div class="value">${s.by_severity[k]}</div><div class="hint">${k === "Critical" ? "score ≥ 60" : k === "Moderate" ? "score 30–59" : "score < 30"}</div></div>`).join("")}
      </div></div>
    </div>

    ${isVideo ? `
    <div class="card mt">
      ${insp.video_url ? `<video id="vid" controls preload="metadata" src="${insp.video_url}"></video>` : ""}
      <div class="card-head"><h2>Damage along the route</h2><span class="sub">${s.video ? `${s.video.duration}s video · ${s.video.sampled_frames} frames sampled · ${s.video.tracks_confirmed} unique defects after tracking` : ""} · click to seek</span></div>
      <div class="card-body"><div class="chart-box short"><canvas id="c-time"></canvas></div>${sevLegend()}</div>
    </div>` : ""}

    ${frames.length ? `
    <div class="viewer mt">
      <div class="card" style="overflow:hidden">
        <div class="stage" id="stage"><canvas id="cv"></canvas></div>
        <div class="stage-bar">
          <div class="small muted" id="frame-cap"></div>
          <div class="actions">
            <div class="seg" id="colorby"><button data-v="severity" class="on">Colour: severity</button><button data-v="class">type</button></div>
            <label class="check small"><input type="checkbox" id="t-labels" checked>Labels</label>
            <label class="check small"><input type="checkbox" id="t-orig">Original</label>
            <a class="btn" id="dl-frame" download>Download frame</a>
          </div>
        </div>
        <div class="filmstrip" id="strip"></div>
      </div>
      <div class="card" style="display:flex;flex-direction:column">
        <div class="card-pad filters">
          <h2>Filter</h2>
          <div class="grp" id="f-cls"></div>
          <div class="grp" id="f-sev" style="border-top:1px solid var(--border);padding-top:10px"></div>
        </div>
        <div class="detail-box" id="detail"><span class="faint small">Hover or click a detection to see its measurements and recommended repair.</span></div>
      </div>
    </div>` : `<div class="card empty mt"><h2>No frames stored</h2><p>No damage was confirmed in this ${insp.media_type}.</p></div>`}

    <div class="grid g-12 mt">
      <div class="card span-6"><div class="card-head"><h2>Defects by type</h2></div><div class="card-body"><div class="chart-box short"><canvas id="c-type"></canvas></div></div></div>
      <div class="card span-6"><div class="card-head"><h2>Severity by type</h2></div><div class="card-body"><div class="chart-box short"><canvas id="c-matrix"></canvas></div>${sevLegend()}</div></div>
    </div>

    <div class="card mt">
      <div class="card-head"><h2>Detections</h2><span class="sub" id="det-count"></span></div>
      <div class="table-wrap" style="margin-top:10px"><table class="tbl">
        <thead><tr><th></th><th>Type</th><th>Severity</th><th>Score</th><th>Measurements</th><th>Where</th><th>Recommended action</th></tr></thead>
        <tbody id="det-body"></tbody></table></div>
    </div>`;

  $("#rerun").onclick = async () => {
    if (!confirm("Analyse the original uploads again? The current results will be replaced.")) return;
    try {
      await api(`/api/inspections/${id}/rerun`, { method: "POST" });
      router();
    } catch (e) { toast(`Re-analyse failed: ${e.message}`); }
  };
  $("#del").onclick = async () => {
    if (!confirm("Delete this inspection and all its files?")) return;
    await api(`/api/inspections/${id}`, { method: "DELETE" });
    toast("Inspection deleted");
    location.hash = "#/inspections";
  };

  const classes = Object.keys(CFG.classes);
  hbar($("#c-type"), classes.map(label), classes.map((c) => s.by_class[c]), classes.map(clsColor));
  stackedSeverity($("#c-matrix"), s.matrix);

  if (isVideo && s.timeline) {
    const tl = s.timeline;
    const ch = mkChart($("#c-time"), {
      type: "bar",
      data: {
        labels: tl.map((p) => p.t.toFixed(1)),
        datasets: [...SEV_ORDER].reverse().map((k) => ({ label: k, data: tl.map((p) => p[k]), backgroundColor: sevColor(k), barPercentage: 1, categoryPercentage: 0.92, borderRadius: 2 })),
      },
      options: {
        scales: { x: axisY({ stacked: true, ticks: { maxTicksLimit: 14, callback: (v, i) => `${tl[i].t.toFixed(0)}s` } }), y: axisX({ stacked: true, title: { display: true, text: "defects in view" } }) },
        plugins: { tooltip: { mode: "index", intersect: false, callbacks: { title: (c) => `t = ${tl[c[0].dataIndex].t.toFixed(1)} s` } } },
        onClick: (e, els) => {
          const v = $("#vid");
          const pts = ch.getElementsAtEventForMode(e, "index", { intersect: false }, false);
          if (v && pts.length) { v.currentTime = tl[pts[0].index].t; v.scrollIntoView({ behavior: "smooth", block: "center" }); }
        },
      },
    });
  }

  if (frames.length) setupViewer(insp);
  else renderDetTable(insp, insp.detections, null);
}

// --------------------------------------------------------------- viewer
function setupViewer(insp) {
  const frames = insp.frames;
  const st = { fi: 0, colorBy: "severity", labels: true, orig: false, hover: null, sel: null,
    cls: new Set(Object.keys(CFG.classes)), sev: new Set(SEV_ORDER), img: new Image(), scale: 1 };
  // start on the worst frame
  st.fi = frames.reduce((b, f, i) => (f.max_score > frames[b].max_score ? i : b), 0);

  const counts = (key) => insp.detections.reduce((m, d) => ((m[d[key]] = (m[d[key]] || 0) + 1), m), {});
  const cc = counts("cls"), sc = counts("severity");
  $("#f-cls").innerHTML = `<h3 class="faint small" style="font-weight:600">Damage type</h3>` + Object.keys(CFG.classes).map((c) => `
    <label class="check"><input type="checkbox" data-cls="${c}" checked><i class="swatch" style="background:${clsColor(c)}"></i>${label(c)}<span class="count">${cc[c] || 0}</span></label>`).join("");
  $("#f-sev").innerHTML = `<h3 class="faint small" style="font-weight:600">Severity</h3>` + SEV_ORDER.map((s) => `
    <label class="check"><input type="checkbox" data-sev="${s}" checked>${chip(s)}<span class="count">${sc[s] || 0}</span></label>`).join("");
  $$("#f-cls input").forEach((i) => (i.onchange = () => { i.checked ? st.cls.add(i.dataset.cls) : st.cls.delete(i.dataset.cls); refreshAll(); }));
  $$("#f-sev input").forEach((i) => (i.onchange = () => { i.checked ? st.sev.add(i.dataset.sev) : st.sev.delete(i.dataset.sev); refreshAll(); }));
  $$("#colorby button").forEach((b) => (b.onclick = () => { st.colorBy = b.dataset.v; $$("#colorby button").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  $("#t-labels").onchange = (e) => { st.labels = e.target.checked; draw(); };
  $("#t-orig").onchange = (e) => { st.orig = e.target.checked; draw(); };

  const visible = (dets) => dets.filter((d) => st.cls.has(d.cls) && st.sev.has(d.severity));
  const cv = $("#cv");
  const ctx = cv.getContext("2d");

  function strip() {
    $("#strip").innerHTML = frames.map((f, i) => {
      const v = visible(f.detections);
      const worst = SEV_ORDER.find((s) => v.some((d) => d.severity === s));
      return `<button data-i="${i}" class="${i === st.fi ? "on" : ""}" title="${esc(f.source_name || "")}${f.t_sec != null ? ` @ ${f.t_sec}s` : ""}">
        <img loading="lazy" src="${f.image_url}" alt="">${worst ? `<span class="sev" style="background:${sevColor(worst)}"></span>` : ""}<span class="n">${v.length}</span></button>`;
    }).join("");
    $$("#strip button").forEach((b) => (b.onclick = () => loadFrame(+b.dataset.i)));
  }

  function loadFrame(i, selId = null) {
    st.fi = i;
    st.sel = selId;
    st.hover = null;
    const f = frames[i];
    $$("#strip button").forEach((b) => b.classList.toggle("on", +b.dataset.i === i));
    $(`#strip button[data-i="${i}"]`)?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    $("#frame-cap").textContent = `${f.source_name || "frame"}${f.t_sec != null ? ` · t = ${f.t_sec.toFixed(1)} s` : ""} · ${f.width}×${f.height} · ${i + 1}/${frames.length}`;
    $("#dl-frame").href = f.annotated_url;
    st.img = new Image();
    st.img.onload = () => { draw(); if (selId) showDetail(insp.detections.find((d) => d.id === selId)); };
    st.img.src = f.image_url;
  }

  function draw() {
    const f = frames[st.fi];
    if (!st.img.complete || !st.img.naturalWidth) return;
    const maxW = $("#stage").clientWidth, maxH = Math.min(window.innerHeight * 0.68, 640);
    const sc = Math.min(maxW / f.width, maxH / f.height);
    const dpr = window.devicePixelRatio || 1;
    cv.style.width = `${f.width * sc}px`;
    cv.style.height = `${f.height * sc}px`;
    cv.width = Math.round(f.width * sc * dpr);
    cv.height = Math.round(f.height * sc * dpr);
    st.scale = sc;
    ctx.setTransform(sc * dpr, 0, 0, sc * dpr, 0, 0);
    ctx.drawImage(st.img, 0, 0, f.width, f.height);
    if (st.orig) return;
    const lw = 2.4 / sc;
    const dets = visible(f.detections).slice().sort((a, b) => (a.cls === "damaged_section" ? -1 : 0) - (b.cls === "damaged_section" ? -1 : 0));
    const focus = st.hover ?? st.sel;
    for (const d of dets) {
      const col = st.colorBy === "severity" ? sevColor(d.severity) : clsColor(d.cls);
      const dim = focus && focus !== d.id;
      ctx.globalAlpha = dim ? 0.35 : 1;
      if (d.polygon && d.polygon.length > 2) {
        ctx.beginPath();
        d.polygon.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fillStyle = col + "55";
        ctx.fill();
        ctx.lineWidth = lw * 0.7;
        ctx.strokeStyle = col;
        ctx.stroke();
      }
      ctx.setLineDash(d.cls === "damaged_section" ? [10 / sc, 7 / sc] : []);
      ctx.lineWidth = focus === d.id ? lw * 1.8 : lw;
      ctx.strokeStyle = col;
      ctx.strokeRect(d.x1, d.y1, d.x2 - d.x1, d.y2 - d.y1);
      ctx.setLineDash([]);
      if (st.labels || focus === d.id) {
        const fs = 12.5 / sc;
        ctx.font = `600 ${fs}px ${getComputedStyle(document.body).fontFamily}`;
        const txt = `${label(d.cls)} · ${d.score.toFixed(0)}`;
        const tw = ctx.measureText(txt).width + 10 / sc;
        const th = fs * 1.55;
        // damaged sections are labelled on their bottom edge so they don't collide with member labels
        let ty = d.cls === "damaged_section" ? d.y2 - th : d.y1 - th;
        if (ty < 0) ty = d.y1;
        ctx.fillStyle = col;
        roundRect(ctx, d.x1 - lw / 2, ty, tw, th, 4 / sc);
        ctx.fill();
        ctx.fillStyle = (st.colorBy === "severity" && d.severity === "Moderate") ? "#1f1600" : "#ffffff";
        ctx.fillText(txt, d.x1 + 5 / sc, ty + fs * 1.12);
      }
    }
    ctx.globalAlpha = 1;
  }

  function hit(e) {
    const r = cv.getBoundingClientRect();
    const x = (e.clientX - r.left) / st.scale, y = (e.clientY - r.top) / st.scale;
    const ds = visible(frames[st.fi].detections).filter((d) => x >= d.x1 && x <= d.x2 && y >= d.y1 && y <= d.y2);
    ds.sort((a, b) => (a.x2 - a.x1) * (a.y2 - a.y1) - (b.x2 - b.x1) * (b.y2 - b.y1)); // smallest box wins
    return ds[0] || null;
  }
  cv.onmousemove = (e) => {
    const d = hit(e);
    const id = d ? d.id : null;
    if (id !== st.hover) {
      st.hover = id;
      draw();
      if (d) showDetail(d);
      else if (st.sel) showDetail(insp.detections.find((x) => x.id === st.sel));
      highlightRow(id ?? st.sel);
    }
  };
  cv.onmouseleave = () => { st.hover = null; draw(); highlightRow(st.sel); };
  cv.onclick = (e) => { const d = hit(e); st.sel = d ? d.id : null; draw(); if (d) showDetail(d); highlightRow(st.sel); };

  function highlightRow(id) {
    $$("#det-body tr").forEach((tr) => tr.classList.toggle("hl", +tr.dataset.id === id));
  }

  function refreshAll() {
    strip();
    draw();
    renderDetTable(insp, insp.detections.filter((d) => st.cls.has(d.cls) && st.sev.has(d.severity)), (d) => {
      const fi = frames.findIndex((f) => f.id === d.frame_id);
      loadFrame(fi, d.id);
      highlightRow(d.id);
      $("#stage").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }

  document.onkeydown = (e) => {
    if (e.target.tagName === "INPUT") return;
    if (e.key === "ArrowRight" && st.fi < frames.length - 1) loadFrame(st.fi + 1);
    if (e.key === "ArrowLeft" && st.fi > 0) loadFrame(st.fi - 1);
  };
  window.onresize = () => draw();
  refreshAll();
  loadFrame(st.fi);
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function metricsText(d) {
  const m = d.metrics || {};
  const out = [];
  if (m.area_pct != null) out.push(`area ${m.area_pct}% of frame`);
  if (m.length_pct != null) out.push(`length ${m.length_pct}% of frame diag.`);
  if (m.width_pct != null) out.push(`width idx ${m.width_pct}`);
  if (m.depth_index != null) out.push(`depth idx ${m.depth_index}`);
  if (m.crack_density != null) out.push(`density ${m.crack_density}`);
  if (m.members != null) out.push(`${m.members} defects clustered`);
  if (m.mask_used) out.push("mask ✓");
  return out.join(" · ");
}

function showDetail(d) {
  if (!d) return;
  const m = d.metrics || {};
  const rows = [
    ["Confidence", `${(d.conf * 100).toFixed(0)}%`],
    m.area_pct != null && ["Area", `${m.area_pct}% of frame${m.mask_used ? " (segmentation mask)" : ""}`],
    m.length_pct != null && ["Length", `${m.length_pct}% of frame diagonal`],
    m.width_pct != null && ["Width index", m.width_pct],
    m.depth_index != null && ["Depth index", `${m.depth_index} (0 flat → 1 deep)`],
    m.seg_conf ? ["Seg. verification", `${(m.seg_conf * 100).toFixed(0)}%`] : null,
    m.crack_density != null && ["Crack density", m.crack_density],
    m.members != null && ["Clustered defects", m.members],
    m.detected_by && ["Detected by", m.detected_by],
    m.scene && ["Photo type", m.scene],
    ["Perspective factor", `×${m.perspective_scale ?? 1}`],
    d.hits > 1 && ["Seen in frames", d.hits],
    ["Box", `${d.x1.toFixed(0)}, ${d.y1.toFixed(0)} → ${d.x2.toFixed(0)}, ${d.y2.toFixed(0)}`],
  ].filter(Boolean);
  $("#detail").innerHTML = `
    <div style="display:flex;gap:12px;align-items:flex-start">
      ${d.crop_url ? `<img class="crop" style="width:96px;height:72px" src="${d.crop_url}" alt="">` : ""}
      <div><div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap"><i class="swatch" style="background:${clsColor(d.cls)}"></i><b>${label(d.cls)}</b> <span class="faint small">${CFG.classes[d.cls].code}</span></div>
        <div style="margin-top:4px;display:flex;gap:8px;align-items:center">${chip(d.severity)}<b class="num">${d.score.toFixed(1)}</b><span class="faint small">/ 100</span></div></div>
    </div>
    <div class="kv">${rows.map(([k, v]) => `<span>${k}</span><span class="num">${esc(v)}</span>`).join("")}</div>
    <div style="margin-top:10px;padding:8px 10px;background:var(--surface-2);border-radius:8px;border:1px solid var(--border)"><span class="small muted">Recommended action</span><div style="font-weight:600">${esc(d.recommendation)}</div></div>`;
}

function renderDetTable(insp, dets, onPick) {
  const fmap = Object.fromEntries(insp.frames.map((f) => [f.id, f]));
  $("#det-count").textContent = `${dets.length} shown`;
  $("#det-body").innerHTML = dets.map((d) => {
    const f = fmap[d.frame_id] || {};
    return `<tr class="clickable" data-id="${d.id}">
      <td>${d.crop_url ? `<img class="crop" loading="lazy" src="${d.crop_url}" alt="">` : ""}</td>
      <td><span class="tag"><i class="swatch" style="background:${clsColor(d.cls)}"></i>${label(d.cls)}</span><div class="small faint">${CFG.classes[d.cls].code} · conf ${(d.conf * 100).toFixed(0)}%</div></td>
      <td>${chip(d.severity)}</td>
      <td><div class="scorebar"><b class="num" style="width:30px">${d.score.toFixed(0)}</b><div class="track"><div class="fill" style="width:${d.score}%;background:${sevColor(d.severity)}"></div></div></div></td>
      <td class="metrics">${esc(metricsText(d))}</td>
      <td class="small">${d.t_sec != null ? `${d.t_sec.toFixed(1)} s` : esc(f.source_name || "")}</td>
      <td class="small">${esc(d.recommendation)}</td></tr>`;
  }).join("") || `<tr><td colspan="7" class="muted" style="text-align:center;padding:24px">No detections match the current filters.</td></tr>`;
  if (onPick) $$("#det-body tr[data-id]").forEach((tr) => (tr.onclick = () => onPick(dets.find((d) => d.id === +tr.dataset.id))));
}

// --------------------------------------------------------------- progress
function renderProgress(insp) {
  const steps = insp.media_type === "video"
    ? ["Upload", "Frame extraction", "Detection & tracking", "Severity scoring", "Video encoding", "Report"]
    : ["Upload", "Pre-processing", "Detection", "Segmentation", "Severity scoring", "Report"];
  view.innerHTML = `
    <div class="page-head"><div><h1>${esc(insp.name)}</h1><p>${insp.status === "failed" ? "Analysis failed" : "Analysing… you can leave this page; processing continues in the background."}</p></div>
      <div class="actions"><button class="btn danger" id="del">${ICON.trash}Delete</button></div></div>
    <div class="card card-pad" style="max-width:820px">
      <div class="pipeline-steps" id="steps">${steps.map((s) => `<div>${s}</div>`).join("")}</div>
      <div class="progress" style="margin-top:14px"><div id="bar" style="width:0%"></div></div>
      <div style="display:flex;justify-content:space-between;margin-top:8px" class="small"><span id="msg" class="muted"></span><b id="pct" class="num"></b></div>
    </div>`;
  $("#del").onclick = async () => { await api(`/api/inspections/${insp.id}`, { method: "DELETE" }); location.hash = "#/inspections"; };
  const upd = (r) => {
    const p = Math.max(0.02, r.progress || 0);
    $("#bar").style.width = `${p * 100}%`;
    $("#pct").textContent = `${Math.round(p * 100)}%`;
    $("#msg").textContent = r.message || r.status;
    const active = r.status === "queued" ? 0 : Math.min(steps.length - 1, 1 + Math.floor(p * (steps.length - 1)));
    $$("#steps div").forEach((d, i) => { d.className = i < active ? "done" : i === active ? "active" : ""; });
    if (r.status === "failed") {
      $("#bar").style.background = "var(--critical)";
      $("#msg").innerHTML = `<span style="color:var(--critical-ink)">${esc(r.message)}</span>`;
      clearInterval(pollTimer);
    }
    if (r.status === "done") { clearInterval(pollTimer); router(); }
  };
  upd(insp);
  if (insp.status !== "failed") {
    pollTimer = setInterval(async () => {
      try { upd(await api(`/api/inspections/${insp.id}/status`)); } catch (_) { clearInterval(pollTimer); }
    }, 800);
  }
}

boot();
