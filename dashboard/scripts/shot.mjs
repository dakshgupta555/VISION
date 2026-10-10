// Screenshot the running dashboard so a UI change is looked at, not assumed.
//
//   node scripts/shot.mjs [--t <cursor index>] [--scenario <name>] [--out <file.png>]
//                         [--width 1440] [--height 900] [--clip <css selector>]
//
// Needs the dashboard (npx vite, :5173) and backend (:8000) running.
// Prints browser console errors and failed requests: a blank panel with an
// error in the console is a broken page, whatever the picture looks like.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const t = arg("t", null);
const scenario = arg("scenario", null);
const mode = arg("mode", null);                 // "live" clicks the Live switch
const view = arg("view", null);                 // "twin" opens the Digital twin page
const select = arg("select", null);             // a component name to click in the list
const slider = arg("slider", null);             // "index=value": set the n-th range input after clicks
const out = arg("out", "../screens/dashboard.png");
const clip = arg("clip", null);
const width = Number(arg("width", 1440)), height = Number(arg("height", 900));

// software WebGL so the 3D twin renders headless (SwiftShader)
const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("requestfailed", (r) => problems.push(`request failed: ${r.url()} ${r.failure()?.errorText}`));
page.on("response", (r) => { if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`); });

await page.goto(url, { waitUntil: mode === "live" ? "domcontentloaded" : "networkidle" });
await page.waitForFunction(() => !document.body.innerText.includes("Loading twin"), null, { timeout: 30000 });

if (mode === "live") {
  await page.getByRole("button", { name: "Live", exact: true }).first().click();
  await page.waitForTimeout(Number(arg("wait", 4000)));
}
if (scenario) {
  await page.selectOption("header select:last-of-type", scenario);
  await page.waitForTimeout(500);
  await page.waitForFunction(() => !document.body.innerText.includes("Loading twin"), null, { timeout: 30000 });
}
if (t !== null) {
  // the replay scrubber is the first range input on the page
  await page.$eval("input[type=range]", (el, v) => {
    const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, String(t));
}
if (mode !== "live") await page.waitForLoadState("networkidle");   // an SSE stream never goes idle
if (view === "twin" || view === "engineering") {
  await page.getByRole("button", { name: view === "twin" ? "Digital twin" : "Engineering", exact: true }).first().click();
  await page.waitForTimeout(Number(arg("twinwait", 5000)));
  if (select) { await page.getByRole("button", { name: select }).first().click(); await page.waitForTimeout(1500); }
  for (const b of (arg("click", "") || "").split(",").filter(Boolean)) {   // e.g. --click "X-ray,Simulate"
    await page.getByRole("button", { name: b, exact: true }).first().click(); await page.waitForTimeout(1200);
  }
}
if (slider) {
  const [idx, val] = slider.split("=");
  await page.$$eval("input[type=range]", (els, [k, v]) => {
    const el = els[Number(k)]; const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    set.call(el, v); el.dispatchEvent(new Event("input", { bubbles: true }));
  }, [idx, val]);
  await page.waitForTimeout(2500);
}
await page.waitForTimeout(800);                 // charts settle

mkdirSync(dirname(out), { recursive: true });
if (clip) {
  await page.locator(clip).first().screenshot({ path: out });
} else {
  await page.screenshot({ path: out, fullPage: true });
}
const dims = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.scrollHeight]);
const canvases = await page.evaluate(() => document.querySelectorAll("canvas").length);
const glinfo = await page.evaluate(() => { const c = document.createElement("canvas"); const g = c.getContext("webgl2") || c.getContext("webgl");
  if (!g) return "no WebGL"; const d = g.getExtension("WEBGL_debug_renderer_info"); return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : "WebGL"; });
console.log(`WebGL: ${glinfo}`);
console.log(`saved ${out}  page ${dims[0]}x${dims[1]}  canvases ${canvases}`);
console.log(problems.length ? `PROBLEMS (${problems.length}):\n  ` + problems.join("\n  ") : "no console errors, no failed requests");
await browser.close();
