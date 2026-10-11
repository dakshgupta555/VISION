// UI check for calibration from a test report: load the sample report, Apply,
// screenshot the Calibration tab and the Overview banner, Reset, and confirm the
// banner is gone. Fails on any console error or failed request.
//
//   node scripts/calib_ui.mjs [--url http://localhost:5173] [--out ../screens]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const out = arg("out", "../screens");
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
page.on("response", (r) => { if (r.status() >= 400) problems.push(`HTTP ${r.status()}: ${r.url()}`); });
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`); };

await page.addInitScript(() => localStorage.setItem("dtwin-theme", "light"));
await page.goto(url, { waitUntil: "load" });
await page.getByText("What's happening").first().waitFor({ timeout: 60000 });
check("no calibration banner by default", (await page.getByText("Calibrated from test report:").count()) === 0);
await page.getByRole("button", { name: "Engineering", exact: true }).first().click();
await page.getByRole("tab", { name: "Calibration" }).click();
await page.getByRole("button", { name: /Load sample 100 kVA/ }).click();
await page.getByRole("button", { name: "Apply to the twin" }).click();
await page.getByText("Applied: SAMPLE 100 kVA").waitFor({ timeout: 30000 });
await page.getByText("Calibrated from test report:").first().waitFor({ timeout: 30000 });
const derived = await page.getByText("DERIVED", { exact: true }).count();
check("sample applied: parameter table with provenance badges", derived >= 3, `${derived} DERIVED badges`);
await page.screenshot({ path: `${out}/calibration_tab.png`, fullPage: true });
await page.getByRole("button", { name: "Overview", exact: true }).first().click();
await page.getByText("What's happening").first().waitFor({ timeout: 60000 });
await page.waitForTimeout(1500);
check("Overview shows the calibration banner", (await page.getByText("Calibrated from test report: SAMPLE 100 kVA").count()) > 0);
await page.screenshot({ path: `${out}/calibration_overview.png` });
await page.getByRole("button", { name: "Engineering", exact: true }).first().click();
await page.getByRole("tab", { name: "Calibration" }).click();
await page.getByRole("button", { name: "Reset to defaults" }).click();
await page.getByText("No report applied").waitFor({ timeout: 30000 });
const gone = await page.waitForFunction(() => !document.body.innerText.includes("Calibrated from test report:"), null,
  { timeout: 10000 }).then(() => true).catch(() => false);
check("reset: banner gone", gone);
check("no console errors or failed requests", problems.length === 0, problems.slice(0, 3).join(" | "));
console.log(`${results.filter(Boolean).length} of ${results.length} calibration UI checks passed`);
await browser.close();
process.exit(results.every(Boolean) ? 0 : 1);
