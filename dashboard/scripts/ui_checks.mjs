// Interaction checks that a static screenshot cannot make: timeline hover
// names, the "?" help, the engineering-details fold, the dark theme, no
// horizontal page scroll and no visible text under 14 px.
//
//   node scripts/ui_checks.mjs [--url http://localhost:5173] [--t 2300] [--out ../screens]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const out = arg("out", "../screens");
const t = arg("t", "2300");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const results = [];
const check = (name, ok, detail = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`); };

for (const [w, h] of [[1366, 768], [1920, 1080]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  const problems = [];
  page.on("pageerror", (e) => problems.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
  await page.addInitScript(() => localStorage.setItem("dtwin-theme", "light"));
  await page.goto(url, { waitUntil: "load" });
  await page.getByText("What's happening").first().waitFor({ timeout: 30000 });
  await page.$eval("input[type=range]", (el, v) => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(el, v);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }, t);
  await page.waitForTimeout(3500);

  const sw = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
  check(`${w}: no horizontal page scroll`, sw[0] <= sw[1], `${sw[0]} vs ${sw[1]}`);

  // smallest visible text (HTML and chart SVG), ignoring the 3D canvas and the scaled heat-map SVG
  const small = await page.evaluate(() => {
    const bad = [];
    for (const el of document.querySelectorAll("body *")) {
      if (!el.childNodes.length || ![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const fs = parseFloat(getComputedStyle(el).fontSize);
      if (fs < 14) bad.push(`${el.tagName.toLowerCase()} ${fs}px "${el.textContent.trim().slice(0, 30)}"`);
    }
    return bad;
  });
  check(`${w}: no visible text under 14 px`, small.length === 0, small.slice(0, 5).join("; "));

  const caps = await page.evaluate(() => [...document.querySelectorAll("body *")]
    .filter((el) => getComputedStyle(el).textTransform === "uppercase" && el.textContent.trim()).length);
  check(`${w}: no ALL-CAPS labels`, caps === 0, `${caps} elements`);

  const mono = await page.evaluate(() => [...document.querySelectorAll("body *")]
    .filter((el) => /mono|menlo|consolas|courier/i.test(getComputedStyle(el).fontFamily) && el.textContent.trim()).length);
  check(`${w}: no monospace text`, mono === 0, `${mono} elements`);

  const dots = await page.evaluate(() => (document.body.innerText.match(/ · /g) || []).length);
  check(`${w}: no strings joined with " · "`, dots === 0, `${dots} found`);

  if (w === 1366) {
    // timeline hover shows the fault name
    const band = page.locator('button[aria-label*=" to "]').first();
    const label = await band.getAttribute("aria-label");
    await band.hover();
    await page.waitForTimeout(300);
    const tipText = await page.locator("div.card.whitespace-nowrap").first().innerText().catch(() => "");
    check("timeline band hover names the fault", tipText.length > 0 && label.startsWith(tipText.split("\n")[0].split(" ")[0]), tipText.split("\n")[0]);
    await page.screenshot({ path: `${out}/p4a_check_timeline_hover.png`, clip: { x: 0, y: 0, width: w, height: 150 } });

    // the "?" on What's happening
    await page.getByRole("button", { name: "What is What's happening?" }).click();
    await page.waitForTimeout(300);
    const helpText = await page.getByRole("dialog").first().innerText();
    check("help popover opens with an explanation", helpText.length > 80, `${helpText.length} chars`);
    await page.screenshot({ path: `${out}/p4a_check_help.png` });
    await page.keyboard.press("Escape");

    // engineering details, closed by default, then opened
    const fold = page.getByRole("button", { name: /^Engineering details/ });
    check("engineering details closed by default", (await fold.getAttribute("aria-expanded")) === "false");
    await fold.click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${out}/p4a_check_details_open_1366.png`, fullPage: true });

    // dark theme from the settings menu
    await page.getByRole("button", { name: "Settings" }).click();
    await page.getByRole("button", { name: "Dark", exact: true }).click();
    await page.mouse.click(5, 300);
    await page.waitForTimeout(1500);
    const theme = await page.evaluate(() => document.documentElement.dataset.theme);
    check("dark theme toggle", theme === "dark");
    await page.screenshot({ path: `${out}/p4a_check_dark_1366.png` });
    await page.evaluate(() => localStorage.setItem("dtwin-theme", "light"));
  }
  check(`${w}: no console errors`, problems.length === 0, problems.slice(0, 3).join("; "));
  await page.close();
}
await browser.close();
const ok = results.filter(Boolean).length;
console.log(`${ok} of ${results.length} UI checks passed`);
process.exit(ok === results.length ? 0 : 1);
