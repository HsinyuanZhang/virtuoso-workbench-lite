import { expect, test } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import packageData from "../../fixtures/demo-symbols.json" with { type: "json" };
import { buildScene } from "../src/logic";
import { flipPreview, toggleFlip } from "../src/orientation";
import type { LibrarySymbolPackage, Point, Snapshot } from "../src/types";

const symbols = packageData as unknown as LibrarySymbolPackage;

function canvasPoint(point: Point, area: { left: number; right: number; top: number; bottom: number }, size: { width: number; height: number }): { x: number; y: number } {
  const scale = Math.max(0.01, Math.min(
    Math.max(1, size.width - 108) / Math.max(area.right - area.left, 0.1),
    Math.max(1, size.height - 90) / Math.max(area.top - area.bottom, 0.1),
  ));
  const viewX = size.width / 2 - (area.left + area.right) * scale / 2;
  const viewY = size.height / 2 + (area.top + area.bottom) * scale / 2;
  return { x: viewX + point[0] * scale, y: viewY - point[1] * scale };
}

function findPort(snapshot: Snapshot, name: string, terminal: string, packages: LibrarySymbolPackage[]) {
  const port = buildScene(snapshot, packages).devices.find((device) => device.instance.name === name)?.ports.find((entry) => entry.name === terminal);
  if (!port) throw new Error(`Missing ${name}.${terminal} port.`);
  return port;
}

async function fingerprint(page: import("@playwright/test").Page) {
  return page.locator("canvas").evaluate((canvas: HTMLCanvasElement) => {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas context is unavailable.");
    const data = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let hash = 2166136261;
    for (let index = 0; index < data.length; index += 4) {
      hash = Math.imul(hash ^ data[index], 16777619);
      hash = Math.imul(hash ^ data[index + 1], 16777619);
      hash = Math.imul(hash ^ data[index + 2], 16777619);
    }
    return hash;
  });
}

async function routeDemoWithSymbolWire(page: import("@playwright/test").Page) {
  await page.route("**/api/demo", async (route) => {
    const response = await route.fetch();
    const snapshot = await response.json() as Snapshot;
    await route.fulfill({
      response,
      json: {
        ...snapshot,
        wires: [...snapshot.wires, { net: "SYMBOL_SPAN", points: [[-5.3, 5.5], [-4.8, 5.5]] }],
      },
    });
  });
}

test("resolved artwork is selectable, flip preview stays aligned, and draft topology stays fixed", async ({ page }) => {
  await page.route("**/api/symbols", (route) => route.fulfill({ json: { packages: [symbols] } }));
  await page.goto("/");
  await expect(page.locator("#symbol-status")).toContainText("resolved");
  await expect(page.locator("#statistics")).toContainText("Symbols: 4 resolved · 1 fallback");

  const screenshotPath = process.env.VWB_SYMBOL_SCREENSHOT_PATH ?? resolve("../.artifacts/library-symbols-demo.png");
  await page.screenshot({ path: screenshotPath, fullPage: true });
  const resolvedPixels = await fingerprint(page);
  const fallbackPage = await page.context().newPage();
  await fallbackPage.route("**/api/symbols", (route) => route.fulfill({ json: { packages: [] } }));
  await fallbackPage.goto(new URL("/", page.url()).toString());
  await expect(fallbackPage.locator("#symbol-status")).toContainText("library package not bound");
  const fallbackPixels = await fingerprint(fallbackPage);
  expect(resolvedPixels).not.toBe(fallbackPixels);
  await fallbackPage.close();

  const snapshot = await page.evaluate(async () => await (await fetch("/api/demo")).json()) as Snapshot;
  const scene = buildScene(snapshot, [symbols]);
  const size = await page.locator("canvas").evaluate((canvas: HTMLCanvasElement) => ({ width: canvas.clientWidth, height: canvas.clientHeight }));
  const overview = page.locator("#topology-overview");
  await page.getByRole("button", { name: /VDD/ }).first().click();
  const membership = await overview.textContent();
  const originalAnchor = findPort(snapshot, "MP1", "G", [symbols]).position;
  await page.locator("canvas").click({ position: canvasPoint(originalAnchor, scene.area, size) });
  await expect(page.locator("#endpoints")).toContainText("MP1.G");
  const wire = snapshot.wires.find((entry) => entry.net === "VDD");
  if (!wire || wire.points.length < 2) throw new Error("Missing VDD wire fixture.");
  const midpoint: Point = [(wire.points[0][0] + wire.points[1][0]) / 2, (wire.points[0][1] + wire.points[1][1]) / 2];
  const sample = canvasPoint(midpoint, scene.area, size);
  const wirePixelBefore = await page.locator("canvas").evaluate((canvas: HTMLCanvasElement, point: { x: number; y: number }) => {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas context is unavailable.");
    const ratio = window.devicePixelRatio || 1;
    return [...context.getImageData(Math.round(point.x * ratio), Math.round(point.y * ratio), 1, 1).data];
  }, sample);

  await page.getByRole("button", { name: "Flip left-right", exact: true }).click();
  await expect(page.locator("#checks-status")).toContainText("Snapshot + draft preview");
  await expect(page.locator("#statistics")).toContainText("Symbols: 4 resolved · 1 fallback");
  await expect(overview).toHaveText(membership ?? "");
  const change = toggleFlip([], snapshot, "MP1");
  const preview = flipPreview(snapshot, change);
  const mirroredAnchor = findPort(preview, "MP1", "G", [symbols]).position;
  const wirePixelAfter = await page.locator("canvas").evaluate((canvas: HTMLCanvasElement, point: { x: number; y: number }) => {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas context is unavailable.");
    const ratio = window.devicePixelRatio || 1;
    return [...context.getImageData(Math.round(point.x * ratio), Math.round(point.y * ratio), 1, 1).data];
  }, sample);
  expect(wirePixelAfter).toEqual(wirePixelBefore);

  await page.locator("canvas").click({ position: canvasPoint(mirroredAnchor, scene.area, size) });
  await expect(page.locator("#endpoints")).not.toContainText("MP1.G");
  await page.locator("canvas").click({ position: canvasPoint(mirroredAnchor, scene.area, size) });
  const otherAnchor = findPort(preview, "MP2", "G", [symbols]).position;
  await page.locator("canvas").click({ position: canvasPoint(otherAnchor, scene.area, size) });
  await expect(page.locator("#endpoints")).toContainText("MP1.G");
  await expect(page.locator("#endpoints")).toContainText("MP2.G");
  await expect(page.locator("#wire-status")).toContainText("Review this proposed connection");
});

test("visible wires stay selectable inside resolved symbols but not fallback rectangles", async ({ page }) => {
  await page.route("**/api/symbols", (route) => route.fulfill({ json: { packages: [symbols] } }));
  await routeDemoWithSymbolWire(page);
  await page.goto("/");
  await expect(page.locator("#symbol-status")).toContainText("resolved");
  const snapshot = await page.evaluate(async () => await (await fetch("/api/demo")).json()) as Snapshot;
  const scene = buildScene(snapshot, [symbols]);
  const size = await page.locator("canvas").evaluate((canvas: HTMLCanvasElement) => ({ width: canvas.clientWidth, height: canvas.clientHeight }));
  const point: Point = [-5.05, 5.5];
  const device = scene.devices.find((entry) => entry.instance.name === "MP1");
  if (!device) throw new Error("Missing resolved PMOS instance.");
  expect(point[0]).toBeGreaterThan(device.hitBounds.left);
  expect(point[0]).toBeLessThan(device.hitBounds.right);
  expect(point[1]).toBeGreaterThan(device.hitBounds.bottom);
  expect(point[1]).toBeLessThan(device.hitBounds.top);
  const sample = canvasPoint(point, scene.area, size);
  const pixel = await page.locator("canvas").evaluate((canvas: HTMLCanvasElement, position: { x: number; y: number }) => {
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas context is unavailable.");
    const ratio = window.devicePixelRatio || 1;
    return [...context.getImageData(Math.round(position.x * ratio), Math.round(position.y * ratio), 1, 1).data];
  }, sample);
  expect(pixel[2]).toBeGreaterThan(pixel[0]);
  await page.locator("canvas").click({ position: sample });
  await expect(page.locator("#topology-overview")).toContainText("SYMBOL_SPAN");

  const fallbackPage = await page.context().newPage();
  await fallbackPage.route("**/api/symbols", (route) => route.fulfill({ json: { packages: [] } }));
  await routeDemoWithSymbolWire(fallbackPage);
  await fallbackPage.goto(new URL("/", page.url()).toString());
  const fallbackSnapshot = await fallbackPage.evaluate(async () => await (await fetch("/api/demo")).json()) as Snapshot;
  const fallbackScene = buildScene(fallbackSnapshot, []);
  const fallbackSize = await fallbackPage.locator("canvas").evaluate((canvas: HTMLCanvasElement) => ({ width: canvas.clientWidth, height: canvas.clientHeight }));
  const fallbackDevice = fallbackScene.devices.find((entry) => entry.instance.name === "MP1");
  if (!fallbackDevice) throw new Error("Missing fallback PMOS instance.");
  expect(point[0]).toBeGreaterThan(fallbackDevice.hitBounds.left);
  expect(point[0]).toBeLessThan(fallbackDevice.hitBounds.right);
  expect(point[1]).toBeGreaterThan(fallbackDevice.hitBounds.bottom);
  expect(point[1]).toBeLessThan(fallbackDevice.hitBounds.top);
  await fallbackPage.locator("canvas").click({ position: canvasPoint(point, fallbackScene.area, fallbackSize) });
  await expect(fallbackPage.locator("#topology-overview")).not.toContainText("SYMBOL_SPAN");
  await fallbackPage.close();
});

test("one immutable package serves two isolated state directories", async ({ page }) => {
  const root = await mkdtemp(join(tmpdir(), "vwb-symbol-state-"));
  const repo = new URL("../../", import.meta.url).pathname;
  const packagePath = new URL("../../fixtures/demo-symbols.json", import.meta.url).pathname;
  const python = process.env.PYTHON ?? "python";
  const start = (state: string) => {
    const child = spawn(python, ["-m", "virtuoso_workbench", "serve", "--port", "0", "--state-dir", state, "--symbol-package", packagePath], {
      cwd: repo,
      env: { ...process.env, PYTHONPATH: join(repo, "src") },
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    const ready = new Promise<string>((resolveReady, reject) => {
      const timer = setTimeout(() => reject(new Error("Workbench did not start.")), 10000);
      child.stdout.on("data", (chunk: Buffer) => {
        output += chunk.toString();
        const line = output.split("\n")[0];
        try {
          const value = JSON.parse(line) as { url?: string };
          if (value.url) { clearTimeout(timer); resolveReady(value.url); }
        } catch { /* wait for the startup line */ }
      });
      child.on("error", (error) => { clearTimeout(timer); reject(error); });
      child.on("exit", (code) => { if (code) { clearTimeout(timer); reject(new Error(`Workbench exited with ${code}.`)); } });
    });
    return { child, ready };
  };
  const first = start(join(root, "state-one"));
  const second = start(join(root, "state-two"));
  try {
    for (const server of [first, second]) {
      await page.goto(await server.ready);
      await expect(page.locator("#symbol-status")).toContainText("resolved");
      const response = await page.evaluate(async () => await (await fetch("/api/symbols")).json());
      expect(response.packages[0].digest).toBe(symbols.digest);
    }
  } finally {
    first.child.kill(); second.child.kill();
    await rm(root, { recursive: true, force: true });
  }
});

test("missing package keeps the rectangle fallback and names the reason", async ({ page }) => {
  await page.route("**/api/symbols", (route) => route.fulfill({ json: { packages: [] } }));
  await page.goto("/");
  await expect(page.locator("#symbol-status")).toContainText("library package not bound");
  await expect(page.locator("#statistics")).toContainText("5 fallback");
});
