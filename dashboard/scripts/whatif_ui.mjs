// What-if tab: inputs propagate to the calculation chain, the silica-gel
// setting touches only the moisture pathway, Reset restores the baseline,
// leaving the tab restores the observed view.
//
//   node scripts/whatif_ui.mjs [--url http://localhost:5173] [--t 1500]
import { chromium } from "playwright";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const t = arg("t", "1500");
const results = [];
const check = (n, ok, d = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${n}${d ? `  (${d})` : ""}`); };
const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const problems = [];
page.on("pageerror", (e) => problems.push(e.message));
page.on("console", (m) => m.type() === "error" && problems.push(m.text()));
await page.addInitScript(() => localStorage.setItem("dtwin-theme", "light"));
await page.goto(url, { waitUntil: "load" });
await page.getByText("What's happening").first().waitFor({ timeout: 30000 });
await page.$eval("input[type=range]", (el, v) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}, t);
await page.waitForTimeout(3000);
await page.getByLabel("Silica-gel condition").selectOption("effective");   // the observed view's own setting

await page.getByRole("button", { name: "Simulate", exact: true }).click();
await page.getByText("Calculation chain").waitFor({ timeout: 15000 });
await page.waitForTimeout(1500);

const row = async (label) => page.evaluate((l) => {
  const tr = [...document.querySelectorAll("table tr")].find((r) => r.innerText.trim().startsWith(l));
  return tr ? [...tr.querySelectorAll("td")].slice(1).map((c) => parseFloat(c.innerText.replace(/,/g, ""))) : null;
}, label);
const setRange = async (idx, v) => page.evaluate(([i, val]) => {
  const el = document.querySelectorAll("input[type=range]")[i];
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, val);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}, [idx, v]);

const hs0 = await row("Steady hot-spot");
check("no edit: simulated equals baseline", hs0 && hs0[0] === hs0[1], JSON.stringify(hs0));

// load slider is the 2nd range (after input voltage)
await setRange(1, 140);
await page.waitForTimeout(1200);
const cu = await row("Copper loss");
const hs1 = await row("Steady hot-spot");
check("load up: copper loss and steady hot-spot rise", cu[1] > cu[0] && hs1[1] > hs1[0], `${cu} ${hs1}`);

// back to baseline
await page.getByRole("button", { name: "Reset to baseline" }).click();
await page.waitForTimeout(1200);
const hsR = await row("Steady hot-spot");
check("reset restores the baseline", hsR[0] === hsR[1] && hsR[0] === hs0[0], JSON.stringify(hsR));

// Rth control
await page.getByLabel("Thermal resistance multiplier").evaluate((el) => {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, "1.5");
  el.dispatchEvent(new Event("input", { bubbles: true }));
});
await page.waitForTimeout(1200);
const hsT = await row("Steady hot-spot");
const rt = await row("Steady top-oil rise");
check("Rth x1.5 raises oil rise and hot-spot", hsT[1] > hsT[0] && rt[1] > rt[0], `${rt} ${hsT}`);
await page.getByRole("button", { name: "Reset to baseline" }).click();
await page.waitForTimeout(1000);

// silica gel: moisture pathway changes, thermal chain does not
await page.getByLabel("Simulated silica-gel condition").selectOption("saturated");
await page.waitForTimeout(1000);
const hsS = await row("Steady hot-spot");
const faaS = await row("Ageing factor");
const text = await page.locator("body").innerText();
check("gel setting leaves hot-spot and ageing unchanged", hsS[0] === hsS[1] && hsS[1] === hs0[0] && faaS[0] === faaS[1]);
check("moisture pathway updates, moisture status 'not calculated'",
  /Saturated, needs inspection/.test(text) && /High/.test(text) && /not calculated: requires moisture-in-oil/.test(text));
await page.getByRole("button", { name: "Reset to baseline" }).click();
await page.waitForTimeout(800);
check("reset restores the gel condition it opened with", (await page.getByLabel("Simulated silica-gel condition").inputValue()) === "effective");

// leaving the tab: change gel again then exit
await page.getByLabel("Simulated silica-gel condition").selectOption("partial");
await page.getByRole("button", { name: "Exit simulation" }).click();
await page.getByText("What's happening").first().waitFor({ timeout: 15000 });
check("exit restores the observed view's gel condition", (await page.getByLabel("Silica-gel condition").inputValue()) === "effective");
check("no console errors", problems.length === 0, problems.slice(0, 2).join("; "));
await browser.close();
const ok = results.filter(Boolean).length;
console.log(`\n${ok} of ${results.length} what-if UI checks passed`);
process.exit(ok === results.length ? 0 : 1);
