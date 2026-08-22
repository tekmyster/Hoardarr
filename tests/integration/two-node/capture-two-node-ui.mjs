#!/usr/bin/env node
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const require = createRequire(new URL("../../../frontend/package.json", import.meta.url));
const { chromium } = require("@playwright/test");

const [baseURL, outputDirectory, nodeSlug] = process.argv.slice(2);
if (!baseURL || !outputDirectory || !nodeSlug) {
  throw new Error("usage: capture-two-node-ui.mjs <base-url> <output-directory> <node-slug>");
}

await fs.mkdir(outputDirectory, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  args: ["--enable-precise-memory-info"],
});
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
  const page = await context.newPage();
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator("#username").fill("validation-owner");
  await page.locator("#password").fill("Hoardarr-Isolated-Validation-Only-11!");
  await page.getByRole("button", { name: "Login" }).click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();
  await page.getByRole("button", { name: "Analytics", exact: true }).click();
  await page.getByRole("heading", { name: "Storage Analytics", exact: true }).waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-analytics-history.png`), fullPage: true });
  await page.getByRole("button", { name: "Storage", exact: true }).click();
  await page.locator("h1").filter({ hasText: /^Storage$/ }).waitFor();
  const sharedRow = page.locator(".redundancy-storage-row").filter({ hasText: "Shared Media" });
  await sharedRow.getByRole("button", { name: "Manage" }).click();
  await page.locator(".redundancy-management").waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-reconnected-overview.png`), fullPage: true });
  await page.getByRole("button", { name: "Controllers & paths" }).click();
  await page.locator(".controller-card").first().waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-path-topology.png`), fullPage: true });
  await page.getByRole("button", { name: "Performance" }).click();
  await page.locator(".redundancy-graph").first().waitFor();
  // SVG line annotations have zero CSS width, so Playwright correctly treats
  // them as non-visible even though the browser renders their stroke.
  await page.locator(".failover-marker").first().waitFor({ state: "attached" });
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-reconnected-history.png`), fullPage: true });

  const heap = async () => {
    await page.requestGC();
    return page.evaluate(() => ({
      usedJSHeapSize: performance.memory?.usedJSHeapSize ?? null,
      totalJSHeapSize: performance.memory?.totalJSHeapSize ?? null,
    }));
  };
  const graphShape = async () => page.locator(".path-series").evaluateAll((series) => ({
    series: series.length,
    maximumCommands: Math.max(0, ...series.map((item) => (item.getAttribute("d")?.match(/[ML]/g) ?? []).length)),
  }));
  const initialHeap = await heap();
  let warmHeap = initialHeap;
  let maximumSeries = 0;
  let maximumCommands = 0;
  await context.tracing.start({ screenshots: true, snapshots: true });
  for (let cycle = 0; cycle < 80; cycle += 1) {
    await page.getByRole("button", { name: cycle % 2 === 0 ? "Events" : "Controllers & paths" }).click();
    await page.getByRole("button", { name: "Performance" }).click();
    await page.locator(".redundancy-graph").first().waitFor();
    const shape = await graphShape();
    maximumSeries = Math.max(maximumSeries, shape.series);
    maximumCommands = Math.max(maximumCommands, shape.maximumCommands);
    if (cycle === 19) warmHeap = await heap();
  }
  const finalHeap = await heap();
  const memoryEvidence = {
    cycles: 80,
    initial: initialHeap,
    afterWarmup: warmHeap,
    final: finalHeap,
    postWarmupGrowthBytes: finalHeap.usedJSHeapSize === null || warmHeap.usedJSHeapSize === null
      ? null
      : finalHeap.usedJSHeapSize - warmHeap.usedJSHeapSize,
    maximumRenderedSeries: maximumSeries,
    maximumCommandsPerSeries: maximumCommands,
    acceptanceEnvelopeBytes: 16 * 1024 * 1024,
  };
  if (memoryEvidence.postWarmupGrowthBytes !== null && memoryEvidence.postWarmupGrowthBytes > memoryEvidence.acceptanceEnvelopeBytes) {
    throw new Error(`browser heap grew ${memoryEvidence.postWarmupGrowthBytes} bytes after warm-up`);
  }
  if (memoryEvidence.maximumCommandsPerSeries < 2) {
    throw new Error("persisted controller telemetry did not produce a visible graph series");
  }
  await fs.writeFile(
    path.join(outputDirectory, `${nodeSlug}-browser-memory.json`),
    `${JSON.stringify(memoryEvidence, null, 2)}\n`,
  );
  await context.tracing.stop({ path: path.join(outputDirectory, `${nodeSlug}-browser-trace.zip`) });
  await page.getByRole("button", { name: "Events" }).click();
  await page.locator(".redundancy-events").waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-failover-events.png`), fullPage: true });
} finally {
  await browser.close();
}
