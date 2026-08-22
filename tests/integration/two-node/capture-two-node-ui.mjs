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
const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 }, colorScheme: "dark" });
  const page = await context.newPage();
  await page.goto(baseURL, { waitUntil: "networkidle" });
  await page.locator("#username").fill("validation-owner");
  await page.locator("#password").fill("Hoardarr-Isolated-Validation-Only-11!");
  await page.getByRole("button", { name: "Login" }).click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();
  await page.getByRole("button", { name: "Storage", exact: true }).click();
  await page.getByRole("heading", { name: "Storage", exact: true }).waitFor();
  const sharedRow = page.locator(".redundancy-storage-row").filter({ hasText: "Shared Media" });
  await sharedRow.getByRole("button", { name: "Manage" }).click();
  await page.locator(".redundancy-management").waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-reconnected-overview.png`), fullPage: true });
  await page.getByRole("button", { name: "Controllers & paths" }).click();
  await page.locator(".controller-card").first().waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-path-topology.png`), fullPage: true });
  await page.getByRole("button", { name: "Performance" }).click();
  await page.locator(".redundancy-graph").first().waitFor();
  await page.locator(".failover-marker").first().waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-reconnected-history.png`), fullPage: true });
  await page.getByRole("button", { name: "Events" }).click();
  await page.locator(".redundancy-events").waitFor();
  await page.screenshot({ path: path.join(outputDirectory, `${nodeSlug}-failover-events.png`), fullPage: true });
  await context.tracing.start({ screenshots: true, snapshots: true });
  await page.getByRole("button", { name: "Performance" }).click();
  await page.getByRole("button", { name: "Events" }).click();
  await context.tracing.stop({ path: path.join(outputDirectory, `${nodeSlug}-browser-trace.zip`) });
} finally {
  await browser.close();
}
