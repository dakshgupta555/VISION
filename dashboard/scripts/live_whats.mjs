// Read what the Overview shows in live mode, exactly as rendered: the status
// pill, the "What's happening" card and the 3D callout. Used by
// check_live_faults.py; prints one JSON line.
//
//   node scripts/live_whats.mjs --url http://localhost:5191 [--out ../screens/live_x.png]
import { chromium } from "playwright";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const url = arg("url", "http://localhost:5173");
const out = arg("out", null);
const browser = await chromium.launch({ args: ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1366, height: 768 } });
const problems = [];
page.on("pageerror", (e) => problems.push(e.message));
page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
await page.goto(url, { waitUntil: "domcontentloaded" });
await page.getByRole("button", { name: "Live", exact: true }).first().click();
await page.getByText("What's happening").first().waitFor({ timeout: 60000 });
await page.waitForTimeout(6000);                 // components, callout and camera settle
const res = await page.evaluate(() => {
  const h = [...document.querySelectorAll("*")].find((e) => e.childElementCount === 0 && e.textContent.trim() === "What's happening");
  let card = h;
  while (card && !card.className?.toString().includes("card")) card = card.parentElement;
  const pill = document.querySelector("header [class*=rounded-full]");
  const banner = [...document.querySelectorAll("[role=alert]")].map((e) => e.innerText);
  return { whats: card ? card.innerText : null, status: pill ? pill.innerText : null,
    callout: [...document.querySelectorAll(".callout")].map((e) => e.innerText), banner };
});
if (out) await page.screenshot({ path: out, fullPage: true });
console.log(JSON.stringify({ ...res, problems }));
await browser.close();
