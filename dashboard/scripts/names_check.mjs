// No code names on screen: walk the Overview (with Engineering details open)
// and every Engineering tab at several moments, and list any snake_case word
// or bare channel code (IR, VB, ...) in the visible text.
//
//   node scripts/names_check.mjs [--url http://localhost:5173]
//
// Physics symbols written with an underscore (k_z, k_r in the FEM notes) are
// allowed and listed separately so a person can judge them.
import { chromium } from "playwright";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const SYMBOLS = new Set(["k_z", "k_r"]);
const MOMENTS = [["showcase", 2300], ["showcase", 2605], ["showcase", 2155], ["sensor_trouble", 2415],
  ["summer_overload", 1225], ["healthy", 1500]];

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
const found = new Map();
const scan = async (where) => {
  const text = await page.evaluate(() => document.body.innerText);
  for (const m of text.matchAll(/\b[a-z][a-z0-9]*_[a-z0-9_]+\b|\b(?:IR|IY|IB|VR|VY|VB)\b/g)) {
    const w = m[0];
    if (!found.has(w)) found.set(w, new Set());
    found.get(w).add(where);
  }
};
const setCursor = async (t) => {
  await page.$eval("input[type=range]", (el, v) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, String(t));
  await page.waitForTimeout(2500);
};

await page.goto(url, { waitUntil: "load" });
await page.getByText("What's happening").first().waitFor({ timeout: 30000 });
for (const [scen, t] of MOMENTS) {
  await page.getByRole("button", { name: "Overview", exact: true }).first().click();
  await page.getByRole("button", { name: "Settings" }).first().click();
  await page.getByLabel("Data file").selectOption(scen);
  await page.mouse.click(5, 300);
  await page.waitForTimeout(1500);
  await page.locator("input[type=range]").first().waitFor({ timeout: 60000 });
  await setCursor(t);
  const fold = page.getByRole("button", { name: /Engineering details/ }).first();
  if (await fold.count()) { await fold.click(); await page.waitForTimeout(500); }
  await scan(`${scen}@${t} overview`);
  await page.getByRole("button", { name: "Engineering", exact: true }).first().click();
  await page.waitForTimeout(1500);
  for (const tab of ["Measurements and alarms", "Characteristic curves", "Losses and efficiency", "Diagnostics",
    "Structured output", "Heat map (FEM)"]) {
    const b = page.getByRole("button", { name: tab, exact: true }).first();
    if (!(await b.count())) continue;
    await b.click();
    await page.waitForTimeout(2500);
    await scan(`${scen}@${t} ${tab}`);
  }
}
await browser.close();
let bad = 0;
for (const [w, where] of [...found.entries()].sort()) {
  const sym = SYMBOLS.has(w);
  if (!sym) bad += 1;
  console.log(`${sym ? "symbol" : "CODE  "}  ${w}  <- ${[...where].slice(0, 3).join("; ")}${where.size > 3 ? ` (+${where.size - 3})` : ""}`);
}
console.log(bad ? `FAIL - ${bad} code name(s) on screen` : "PASS - no code names on screen");
process.exit(bad ? 1 : 0);
