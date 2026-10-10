// Screenshot the network dashboard so a UI change is looked at, not assumed.
//
//   node scripts/shot.mjs [--tab network|planner|strategy] [--asset DT-007] [--budget 400000]
//                         [--out ../screens/net.png] [--width 1366] [--height 768] [--full]
//
// Needs :5180 (npx vite) and :8001 (uvicorn dtwin_network.app:app --port 8001).
// Prints console errors and failed requests; exits 1 if there are any.
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5180");
const tab = arg("tab", "network");
const asset = arg("asset", null);
const budget = arg("budget", null);
const out = arg("out", "../screens/network.png");
const width = Number(arg("width", 1366)), height = Number(arg("height", 768));
const full = process.argv.includes("--full");

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
const problems = [];
// a blocked map tile's own "Failed to load resource" line is the tile failure, counted below, not a page error
page.on("console", (m) => { if (m.type() === "error" && !/tile\.openstreetmap\.org/.test(m.location()?.url || "")) problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
// map tiles are counted apart: with --offline they are blocked on purpose, and a
// tile that fails online is handled by the map's own fallback, not a page error
const TILE = /tile\.openstreetmap\.org/;
const tiles = { ok: 0, failed: 0 };
page.on("requestfailed", (r) => { if (TILE.test(r.url())) tiles.failed += 1; else problems.push(`request failed: ${r.url()} ${r.failure()?.errorText}`); });
page.on("response", (r) => { if (TILE.test(r.url())) { if (r.status() < 400) tiles.ok += 1; else tiles.failed += 1; return; }
  if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
if (process.argv.includes("--offline")) await page.route(TILE, (r) => r.abort("internetdisconnected"));
await page.addInitScript(() => localStorage.setItem("dtwin-theme", "light"));
await page.goto(url, { waitUntil: "domcontentloaded" });
await page.getByText("What's happening in the network").first().waitFor({ timeout: 30000 });
const tabName = { network: "Network twin", map: "Map", planner: "Deployment planner", strategy: "Strategy comparison" }[tab];
if (tab !== "network") await page.getByRole("button", { name: tabName, exact: true }).click();
const demoStep = Number(arg("demo", 0));
if (demoStep) {
  await page.getByRole("button", { name: "Demo", exact: true }).click();
  await page.getByText("Demo 1 of 6").waitFor({ timeout: 60000 });
  for (let k = 2; k <= demoStep; k++) {
    await page.getByRole("button", { name: "Next", exact: true }).click();
    await page.getByText(`Demo ${k} of 6`).waitFor({ timeout: 60000 });
  }
}
if (budget) {
  await page.waitForSelector("input[type=range]");
  await page.$eval("input[type=range]", (el, v) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, String(budget));
}
if (asset) {
  await page.getByRole("button", { name: asset, exact: false }).first().click().catch(async () => {
    await page.locator(`text=${asset}`).first().click();
  });
}
await page.waitForTimeout(Number(arg("wait", 2500)));
if (process.argv.includes("--replay")) {
  await page.getByRole("button", { name: "Replay", exact: true }).click();
  await page.waitForTimeout(1500);
}
const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
// same rule as the single-transformer app: no visible text under 14 px
const small = await page.evaluate(() => {
  const out = [];
  for (const el of document.querySelectorAll("body *")) {
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const px = parseFloat(getComputedStyle(el).fontSize) * (el.ownerSVGElement ? (el.ownerSVGElement.getScreenCTM()?.a || 1) : 1);
    if (px < 13.95) out.push(`${el.tagName.toLowerCase()} ${px.toFixed(1)}px "${el.textContent.trim().slice(0, 30)}"`);
  }
  return out;
});
mkdirSync(dirname(out), { recursive: true });
await page.screenshot({ path: out, fullPage: full });
const offline = await page.evaluate(() => document.querySelector("[data-testid=geo-map]")?.dataset.offline || null);
console.log(`saved ${out}${hscroll ? "  WARNING: horizontal scroll" : ""}${offline ? `  map offline=${offline} tiles ok=${tiles.ok} failed=${tiles.failed}` : ""}`);
if (small.length) problems.push(`text under 14 px: ${small.slice(0, 5).join("; ")}`);
if (problems.length) { console.log(problems.join("\n")); }
await browser.close();
process.exit(problems.length ? 1 : 0);
