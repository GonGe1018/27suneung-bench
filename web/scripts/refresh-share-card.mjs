import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { preview } from "vite";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const output = resolve(webRoot, process.argv[2] ?? "dist");
const dimensions = { width: 1200, height: 630 };
const styles = `
  body {
    display: grid;
    place-items: center;
    width: 1200px;
    height: 630px;
    overflow: hidden;
    padding: 32px 44px;
    background: var(--canvas);
  }
  .accuracy-card {
    --chart-height: 308px;
    width: 100%;
    margin: 0;
    padding: 22px 22px 20px;
  }
  .accuracy-chart { grid-template-columns: 50px minmax(0, 1fr); gap: 13px; }
  .accuracy-axis { margin-top: 47px; font-size: 17px; }
  .accuracy-series { padding-top: 47px; }
  .accuracy-series::before { inset-block-start: 47px; }
  .accuracy-mark .model-name {
    min-height: 73px;
    margin: 11px 2px 3px;
    font-size: 19px;
    line-height: 1.15;
  }
  .accuracy-mark .model-icon { width: 23px; height: 23px; }
  .accuracy-value { font-size: 18px; }
  .accuracy-stats { font-size: 14px; }
  .accuracy-series:has(> :nth-child(10)) .model-name { font-size: 15px; }
  .accuracy-series:has(> :nth-child(10)) .accuracy-value { font-size: 13px; }
  .accuracy-series:has(> :nth-child(10)) .accuracy-stats { font-size: 12px; }
`;

const indexPath = resolve(output, "index.html");
const html = await readFile(indexPath, "utf8");
const shareImagePath = /\/share-card-2027(?:-[a-f0-9]{12})?\.png/g;
if (!html.match(shareImagePath)) throw new Error("Missing share card metadata");

const server = await preview({
  configFile: false,
  root: webRoot,
  build: { outDir: output },
  preview: { host: "127.0.0.1", port: 0 },
});

try {
  const browser = await chromium.launch({ channel: "chrome" });
  try {
    const page = await browser.newPage({ viewport: dimensions, colorScheme: "dark" });
    await page.goto(`${server.resolvedUrls.local[0]}?year=2027`);
    await page.locator(".accuracy-mark").first().waitFor({ timeout: 10000 });
    await page.addStyleTag({ content: styles });
    await page.locator(".accuracy-card").evaluate((chart) => {
      chart.querySelector(".accuracy-heading").remove();
      document.body.replaceChildren(chart);
    });
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    const chart = await page.locator(".accuracy-card").boundingBox();
    if (chart.y + chart.height > dimensions.height - 16) {
      throw new Error("The score chart exceeds the share image height");
    }

    const image = await page.screenshot({ animations: "disabled" });
    const hash = createHash("sha256").update(image).digest("hex").slice(0, 12);
    const filename = `share-card-2027-${hash}.png`;
    await writeFile(resolve(output, filename), image);
    await writeFile(indexPath, html.replaceAll(shareImagePath, `/${filename}`));
    console.log("Rendered " + filename);
  } finally {
    await browser.close();
  }
} finally {
  await server.close();
}
