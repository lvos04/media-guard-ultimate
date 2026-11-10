// Renders docs/design/previews/*.html to PNG at 1440 and 390 px.
// Usage: node docs/design/tools/screenshot.cjs [name-filter]
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const dir = path.join(__dirname, "..", "previews");
const out = path.join(dir, "screenshots");
fs.mkdirSync(out, { recursive: true });
const filter = process.argv[2] || "";
(async () => {
  const browser = await chromium.launch();
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith(".html") && x.includes(filter))) {
    for (const [w, h, tag] of [[1440, 900, "1440"], [390, 844, "390"]]) {
      const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: w < 500 ? 2 : 1 });
      await page.goto("file://" + path.join(dir, f));
      await page.waitForTimeout(150);
      await page.screenshot({ path: path.join(out, f.replace(".html", `-${tag}.png`)), fullPage: w > 500 && process.env.FULL !== "0" });
      if (w < 500 && process.env.FULL !== "0") {
        await page.addStyleTag({ content: ".bottomnav{position:static!important} .preview-note{display:none}" });
        await page.screenshot({ path: path.join(out, f.replace(".html", `-${tag}-full.png`)), fullPage: true });
      }
      await page.close();
    }
    console.log("shot", f);
  }
  await browser.close();
})();
