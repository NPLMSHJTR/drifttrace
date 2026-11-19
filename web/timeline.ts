#!/usr/bin/env node
/**
 * Builds a single-file timeline page from the drifttrace manifest.
 *
 * Each series is drawn as one SVG polyline; labelled incidents are blue bands
 * and predicted intervals are red. Twenty series of 2,880 points stay responsive
 * because the path is built once per series rather than per frame.
 *
 * Usage: node --experimental-strip-types timeline.ts --manifest reports/manifest.json --out reports/timeline.html
 */

import { readFileSync, writeFileSync } from "node:fs";

type Anomaly = { start: number; end: number; type: string };
type Interval = { start: number; end: number; peak: number; direction: string };
type Series = { id: string; values: number[]; anomalies: Anomaly[]; intervals: Interval[] };
type Metrics = {
  precision: number;
  recall: number;
  f1: number;
  detection_rate: number;
  detected: number;
  anomalies: number;
  false_alarm_intervals: number;
  points: number;
  by_type: Record<string, { detected: number; total: number }>;
};
type Manifest = {
  step_minutes: number;
  metrics: Metrics;
  baseline: Metrics | null;
  series: Series[];
};

function arg(name: string, fallback: string): string {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

const manifestPath = arg("manifest", "reports/manifest.json");
const outPath = arg("out", "reports/timeline.html");
const manifest: Manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
const payload = {
  step_minutes: manifest.step_minutes,
  metrics: manifest.metrics,
  baseline: manifest.baseline,
  series: manifest.series,
};

const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>drifttrace timeline</title>
<style>
  :root { --bg:#0b1020; --panel:#121a2c; --line:#26314a; --text:#e6ecf6; --muted:#8fa0b8;
          --truth:#3b82f6; --alarm:#ef4444; --accent:#22c55e; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text);
         font:14px/1.5 ui-sans-serif, system-ui, "Segoe UI", sans-serif; }
  header { display:flex; flex-wrap:wrap; gap:22px; align-items:baseline;
           padding:16px 20px; border-bottom:1px solid var(--line); }
  h1 { font-size:16px; margin:0; }
  .metric { color:var(--muted); } .metric b { color:var(--text); font-variant-numeric:tabular-nums; }
  main { display:grid; grid-template-columns:190px 1fr; }
  nav { border-right:1px solid var(--line); max-height:calc(100vh - 70px); overflow:auto; }
  button.item { display:block; width:100%; text-align:left; padding:9px 12px; background:none;
                border:0; border-bottom:1px solid var(--line); color:var(--text);
                font:inherit; cursor:pointer; }
  button.item[aria-current="true"] { background:#1b2440; box-shadow:inset 3px 0 0 var(--accent); }
  button.item span { color:var(--muted); font-size:12px; }
  section { padding:18px 20px; }
  .frame { position:relative; border:1px solid var(--line); background:var(--panel); }
  svg { display:block; width:100%; height:300px; }
  .controls { display:flex; gap:18px; margin:12px 0; color:var(--muted); }
  table { border-collapse:collapse; margin-top:10px; }
  td, th { padding:5px 10px; border-bottom:1px solid var(--line); text-align:left; }
  th { color:var(--muted); font-weight:500; }
  kbd { border:1px solid var(--line); border-radius:4px; padding:0 4px; font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>drifttrace · anomaly review</h1>
  <span class="metric">precision <b id="p"></b></span>
  <span class="metric">recall <b id="r"></b></span>
  <span class="metric">F1 <b id="f"></b></span>
  <span class="metric">incidents <b id="d"></b></span>
  <span class="metric">false alarms <b id="fa"></b></span>
  <span class="metric">baseline F1 <b id="bf"></b></span>
</header>
<main>
  <nav id="list"></nav>
  <section>
    <div class="frame"><svg id="chart" viewBox="0 0 1000 300" preserveAspectRatio="none"></svg></div>
    <div class="controls">
      <span><kbd>←</kbd> <kbd>→</kbd> series</span>
      <label><input type="checkbox" id="truth" checked> labelled</label>
      <label><input type="checkbox" id="pred" checked> predicted</label>
    </div>
    <table id="detail"></table>
  </section>
</main>
<script>
const DATA = ${JSON.stringify(payload).replace(/</g, "\\u003c")};
const chart = document.getElementById("chart");
const list = document.getElementById("list");
const detail = document.getElementById("detail");
let index = 0;

document.getElementById("p").textContent = DATA.metrics.precision.toFixed(3);
document.getElementById("r").textContent = DATA.metrics.recall.toFixed(3);
document.getElementById("f").textContent = DATA.metrics.f1.toFixed(3);
document.getElementById("d").textContent = DATA.metrics.detected + "/" + DATA.metrics.anomalies;
document.getElementById("fa").textContent = DATA.metrics.false_alarm_intervals;
document.getElementById("bf").textContent = DATA.baseline ? DATA.baseline.f1.toFixed(3) : "n/a";

DATA.series.forEach((series, i) => {
  const button = document.createElement("button");
  button.className = "item";
  button.innerHTML = "<strong>" + series.id + "</strong><br><span>" +
    series.anomalies.length + " incidents · " + series.intervals.length + " alarms</span>";
  button.addEventListener("click", () => show(i));
  list.appendChild(button);
});

function band(x, width, color, opacity) {
  return '<rect x="' + x + '" y="0" width="' + width + '" height="300" fill="' + color +
    '" opacity="' + opacity + '"/>';
}

function show(i) {
  index = (i + DATA.series.length) % DATA.series.length;
  const series = DATA.series[index];
  const n = series.values.length;
  const min = Math.min(...series.values);
  const max = Math.max(...series.values);
  const span = (max - min) || 1;
  const x = (point) => (point / (n - 1)) * 1000;
  const y = (value) => 290 - ((value - min) / span) * 270;

  let svg = "";
  if (document.getElementById("truth").checked) {
    series.anomalies.forEach((a) => { svg += band(x(a.start), Math.max(1, x(a.end + 1) - x(a.start)), "#3b82f6", 0.18); });
  }
  if (document.getElementById("pred").checked) {
    series.intervals.forEach((a) => { svg += band(x(a.start), Math.max(1, x(a.end + 1) - x(a.start)), "#ef4444", 0.16); });
  }
  const points = series.values.map((value, idx) => x(idx).toFixed(1) + "," + y(value).toFixed(1)).join(" ");
  svg += '<polyline points="' + points + '" fill="none" stroke="#93c5fd" stroke-width="1.1"/>';
  svg += '<line x1="0" y1="290" x2="1000" y2="290" stroke="#26314a"/>';
  chart.innerHTML = svg;

  [...list.children].forEach((child, idx) => child.setAttribute("aria-current", String(idx === index)));
  const rows = series.anomalies.map((a) => {
    const hit = series.intervals.some((p) => p.start <= a.end && a.start <= p.end);
    return "<tr><td>" + a.type + "</td><td>" + a.start + "–" + a.end + "</td><td>" +
      (hit ? "detected" : "missed") + "</td></tr>";
  }).join("");
  const alarms = series.intervals.filter((p) =>
    !series.anomalies.some((a) => p.start <= a.end && a.start <= p.end));
  detail.innerHTML = rows + alarms.map((p) =>
    "<tr><td>false alarm</td><td>" + p.start + "–" + p.end + "</td><td>score " + p.peak.toFixed(1) +
    "</td></tr>").join("");
}

document.getElementById("truth").addEventListener("change", () => show(index));
document.getElementById("pred").addEventListener("change", () => show(index));
document.addEventListener("keydown", (event) => {
  if (event.key === "ArrowRight") show(index + 1);
  if (event.key === "ArrowLeft") show(index - 1);
});
show(0);
</script>
</body>
</html>
`;

writeFileSync(outPath, html, "utf8");
console.log(`wrote ${outPath} (${(Buffer.byteLength(html) / 1024).toFixed(0)} KB, ${payload.series.length} series)`);
