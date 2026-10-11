// Silica-gel breather card and 3D model: default Unknown, each state changes
// the indicator, the explanation opens, no hot-spot or health change, the 3D
// view still renders, no text under 14 px, no console errors.
//
//   node scripts/breather_ui.mjs [--url http://localhost:5173] [--t 1500]
import { chromium } from "playwright";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const t = arg("t", "1500");
const results = [];
const check = (n, ok, d = "") => { results.push(ok); console.log(`${ok ? "PASS" : "FAIL"}  ${n}${d ? `  (${d})` : ""}`); };
const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
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
await page.waitForTimeout(3500);

const card = page.getByRole("region", { name: "Silica-gel breather condition" });
const sel = page.getByLabel("Silica-gel condition");
const tiles = async () => page.evaluate(() => [...document.querySelectorAll("section.card")]
  .filter((s) => /Hot-spot temperature|Health index/.test(s.innerText) && s.innerText.length < 400).map((s) => s.innerText.split("\n").slice(0, 3).join("|")));

check("default is Unknown", (await sel.inputValue()) === "unknown" && /Unknown/.test(await card.innerText()));
const before = await tiles();
const seen = {};
for (const [v, label] of [["effective", "Effective"], ["partial", "Partially exhausted"], ["saturated", "Saturated"], ["unknown", "Unknown"]]) {
  await sel.selectOption(v);
  await page.waitForTimeout(700);
  const txt = await card.innerText();
  const dot = await card.locator("span.rounded-full").first().evaluate((e) => getComputedStyle(e).backgroundColor);
  seen[v] = dot;
  check(`${v}: card shows "${label}"`, txt.includes(label) && /Not measured/.test(txt));
}
check("the four states have four different indicator colours", new Set(Object.values(seen)).size === 4, JSON.stringify(seen));
check("hot-spot and health tiles unchanged by the setting", JSON.stringify(await tiles()) === JSON.stringify(before));

await card.getByRole("button", { name: "How this works" }).click();
const info = await card.innerText();
check("explanation covers purpose, mechanism, limits, chain",
  ["What the breather is for", "How silica gel removes moisture", "What it cannot tell us", "Cause and effect chain",
    "not calculated"].every((k) => info.includes(k)));
check("legend shows the convention as illustrative", /Illustrative indicator convention/.test(info));
const small = await card.evaluate((el) => [...el.querySelectorAll("*")].filter((e) =>
  [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && parseFloat(getComputedStyle(e).fontSize) < 14).length);
check("no text under 14 px in the card", small === 0, `${small}`);
check("3D canvas present", (await page.locator("canvas").count()) > 0);
const sw = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
check("no horizontal page scroll", sw[0] <= sw[1], `${sw[0]} vs ${sw[1]}`);
check("no console errors", problems.length === 0, problems.slice(0, 2).join("; "));
await browser.close();
const ok = results.filter(Boolean).length;
console.log(`\n${ok} of ${results.length} breather UI checks passed`);
process.exit(ok === results.length ? 0 : 1);
