// Calculation panels on the four Overview tiles: hover, click-to-pin, touch
// tap (no hover), keyboard, placement inside the viewport, text size, errors.
//
//   node scripts/explain_ui.mjs [--url http://localhost:5173] [--t 1500] [--out ../screens]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const t = arg("t", "1500");
const out = arg("out", "../screens");
mkdirSync(out, { recursive: true });
const TILES = ["hot-spot temperature", "load", "top-oil temperature", "health index"];
const results = [];
const check = (n, ok, d = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${n}${d ? `  (${d})` : ""}`); };
const browser = await chromium.launch();

async function open(ctxOpts) {
  const context = await browser.newContext(ctxOpts);
  const page = await context.newPage();
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
  return { page, problems, context };
}

// ---- desktop: hover, pin, keyboard, placement, text size, content
for (const [w, h] of [[1366, 768], [1920, 1080]]) {
  const { page, problems, context } = await open({ viewport: { width: w, height: h } });
  for (const tile of TILES) {
    const btn = page.getByRole("button", { name: `Show how ${tile} is calculated` });
    await btn.hover();
    const dlg = page.getByRole("dialog", { name: /is calculated/ });
    await dlg.waitFor({ timeout: 5000 });
    await page.waitForFunction(() => /Live calculation trace/.test(document.querySelector('[role="dialog"][aria-label*="calculated"]')?.innerText || ""));
    const box = await dlg.boundingBox();
    check(`${w}: ${tile}: hover opens, fully inside the window`, box.x >= 0 && box.y >= 0 && box.x + box.width <= w && box.y + box.height <= h,
      `${Math.round(box.x)},${Math.round(box.y)} ${Math.round(box.width)}x${Math.round(box.height)}`);
    const txt = await dlg.innerText();
    check(`${w}: ${tile}: has equation, trace, graph, sensitivity, quality`,
      ["Governing physics", "Live calculation trace", "Graph", "Sensitivity", "Data quality"].every((k) => txt.includes(k)));
    check(`${w}: ${tile}: no NaN or undefined shown`, !/NaN|undefined|Infinity/.test(txt));
    const small = await dlg.evaluate((el) => [...el.querySelectorAll("*")].filter((e) =>
      [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 14).length);
    check(`${w}: ${tile}: no text under 14 px`, small === 0, `${small} elements`);
    check(`${w}: ${tile}: graph drawn`, (await dlg.locator("svg.recharts-surface").count()) > 0);
    if (w === 1366 && tile === "hot-spot temperature") await page.screenshot({ path: `${out}/explain_ui_hotspot.png` });
    await page.mouse.move(5, h - 5);
    await page.waitForTimeout(600);
    check(`${w}: ${tile}: closes when the pointer leaves`, !(await dlg.isVisible()));
  }
  // keyboard: focus the button, Enter pins, Escape closes
  const kb = page.getByRole("button", { name: "Show how health index is calculated" });
  await kb.focus();
  await page.keyboard.press("Enter");
  await page.mouse.move(5, h - 5);
  await page.waitForTimeout(500);
  const d2 = page.getByRole("dialog", { name: /is calculated/ });
  check(`${w}: Enter pins the panel`, await d2.isVisible());
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
  check(`${w}: Escape closes it`, !(await d2.isVisible()));
  check(`${w}: no console errors`, problems.length === 0, problems.slice(0, 2).join("; "));
  await context.close();
}

// ---- touch: no hover, a tap opens and pins; a tap outside closes
{
  const { page, problems, context } = await open({ viewport: { width: 820, height: 1100 }, hasTouch: true, isMobile: false });
  const btn = page.getByRole("button", { name: "Show how load is calculated" });
  await btn.tap();
  const dlg = page.getByRole("dialog", { name: /is calculated/ });
  await dlg.waitFor({ timeout: 5000 });
  const box = await dlg.boundingBox();
  check("touch: tap opens the panel", await dlg.isVisible());
  check("touch: panel inside the window", box.x >= 0 && box.x + box.width <= 820 && box.y >= 0 && box.y + box.height <= 1100);
  await page.mouse.click(5, 5);
  await page.waitForTimeout(400);
  check("touch: outside tap closes it", !(await dlg.isVisible()));
  check("touch: no console errors", problems.length === 0, problems.slice(0, 2).join("; "));
  await context.close();
}
await browser.close();
const ok = results.filter(Boolean).length;
console.log(`\n${ok} of ${results.length} explain UI checks passed`);
process.exit(ok === results.length ? 0 : 1);
