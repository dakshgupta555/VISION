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
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("requestfailed", (r) => problems.push(`request failed: ${r.url()} ${r.failure()?.errorText}`));
page.on("response", (r) => { if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
await page.addInitScript(() => localStorage.setItem("dtwin-theme", "light"));
await page.goto(url, { waitUntil: "domcontentloaded" });
await page.getByText("What's happening in the network").first().waitFor({ timeout: 30000 });
const tabName = { network: "Network twin", planner: "Deployment planner", strategy: "Strategy comparison" }[tab];
if (tab !== "network") await page.getByRole("button", { name: tabName, exact: true }).click();
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
const hscroll = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
mkdirSync(dirname(out), { recursive: true });
await page.screenshot({ path: out, fullPage: full });
console.log(`saved ${out}${hscroll ? "  WARNING: horizontal scroll" : ""}`);
if (problems.length) { console.log(problems.join("\n")); }
await browser.close();
process.exit(problems.length ? 1 : 0);
