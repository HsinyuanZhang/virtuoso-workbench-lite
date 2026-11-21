import { test, expect } from "@playwright/test";
import { readFile } from "node:fs/promises";

test("demo renders a schematic and saves a reviewed handoff", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.locator("#cell-title")).toHaveText("Differential pair");
  await expect(page.locator("#statistics")).toHaveText("5 instances · 6 I/O pins · 15 wire segments");
  const hasDevicePixels = await page.locator("canvas").evaluate((canvas: HTMLCanvasElement) => {
    const image = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height);
    for (let i = 0; i < image.data.length; i += 4) {
      if (image.data[i] === 26 && image.data[i + 1] === 39 && image.data[i + 2] === 33) return true;
    }
    return false;
  });
  expect(hasDevicePixels).toBe(true);
  await page.locator("#instances").selectOption("MP1");
  await page.locator("#terminal-list button").filter({ hasText: /^DNREF$/ }).click();
  await page.locator("#instances").selectOption("MP2");
  await page.locator("#terminal-list button").filter({ hasText: /^DVOUT$/ }).click();
  await expect(page.locator("#wire-effect")).toContainText("Merges distinct nets");
  await page.locator("#note").fill("Review this proposed merge before routing.");
  const saved = page.waitForResponse((r) => r.url().endsWith("/api/handoffs") && r.request().method() === "POST");
  await page.getByRole("button", { name: "Save handoff", exact: true }).click();
  const response = await saved;
  expect(response.status()).toBe(201);
  const receipt = await response.json() as { path: string };
  const plan = JSON.parse(await readFile(receipt.path, "utf8")) as { schema: string; operation: { outcome: string } };
  expect(plan.schema).toBe("circuit-workbench/wire-intent-v1");
  expect(plan.operation.outcome).toBe("merge-existing-nets");
  await expect(page.locator("#receipt-path")).toHaveText(receipt.path);
  await expect(page.getByRole("button", { name: "Save handoff", exact: true })).toBeDisabled();
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("demo-handoff.png"), fullPage: true });
});
