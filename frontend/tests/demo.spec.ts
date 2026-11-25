import { test, expect } from "@playwright/test";
import type { Page, TestInfo } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

function agent(args: string[], payload?: unknown): { request: { id: string; status: string; claim: { token: string } } } {
  return JSON.parse(execFileSync("../.venv/bin/vwb", ["--state-dir", process.env.VWB_E2E_STATE_DIR!, "agent", ...args, "--agent", "demo-agent", "--harness", "playwright"], {
    encoding: "utf8", input: payload === undefined ? undefined : JSON.stringify(payload),
  })) as { request: { id: string; status: string; claim: { token: string } } };
}

async function build(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator("#cell-title")).toHaveText("Differential pair");
  await expect(page.locator("#save")).toBeDisabled();
  await page.locator("#instances").selectOption("MP1");
  await page.locator("#terminal-list button").filter({ hasText: /^DNREF$/ }).click();
  await page.locator("#instances").selectOption("MP2");
  await page.locator("#terminal-list button").filter({ hasText: /^DVOUT$/ }).click();
  await expect(page.locator("#wire-effect")).toContainText("Merges distinct nets");
  await page.getByRole("button", { name: "Add connect", exact: true }).click();
  await page.locator("#instances").selectOption("MP1");
  await page.getByRole("textbox", { name: "Parameter name", exact: true }).fill("w");
  await page.getByRole("textbox", { name: "Parameter value", exact: true }).fill("2u");
  await page.getByRole("button", { name: "Add parameters", exact: true }).click();
  await page.getByRole("textbox", { name: "Parameter value", exact: true }).fill("3u");
  await page.getByRole("button", { name: "Add parameters", exact: true }).click();
  await expect(page.locator("#draft-changes > li")).toHaveCount(2);
  await expect(page.locator("#draft-changes")).toContainText('"w":"3u"');
  await page.getByLabel("Title", { exact: true }).fill("Review differential load");
  await page.getByLabel("Simulation setup", { exact: true }).fill("Run DC and AC in a scratch library.");
  await page.getByLabel("Acceptance targets", { exact: true }).fill("Gain must exceed 40 dB. Bias must remain below 1 mA.");
  await page.getByLabel("Note for the agent", { exact: true }).fill("Review the net merge before any OA edit.");
}

async function save(page: Page, submit: boolean): Promise<string> {
  const response = page.waitForResponse((response) => response.url().endsWith("/api/requests") && response.request().method() === "POST");
  await page.locator(submit ? "#submit-draft" : "#save").click();
  const received = await response;
  expect(received.status()).toBe(201);
  return ((await received.json()) as { request: { id: string } }).request.id;
}

async function screenshot(page: Page, info: TestInfo, name: string): Promise<void> {
  await page.locator(".right").evaluate((panel: HTMLElement) => { panel.scrollTop = panel.querySelector<HTMLElement>(".review")!.offsetTop - panel.offsetTop; });
  await page.screenshot({ path: join(process.env.VWB_SCREENSHOT_DIR ?? info.outputDir, name), fullPage: true });
}

test("demo request: draft, CLI plan, human approval, CLI result, and acceptance", async ({ page }, info) => {
  const errors: string[] = [];
  let creates = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => { if (request.method() === "POST" && request.url().endsWith("/api/requests")) creates++; });
  await build(page);
  expect(creates).toBe(0);
  const hasGreenDevices = await page.locator("canvas").evaluate((canvas: HTMLCanvasElement) => {
    const pixels = canvas.getContext("2d")!.getImageData(0, 0, canvas.width, canvas.height).data;
    for (let index = 0; index < pixels.length; index += 4) if (pixels[index] === 0 && pixels[index + 1] === 255 && pixels[index + 2] === 0) return true;
    return false;
  });
  expect(hasGreenDevices).toBe(true);
  await screenshot(page, info, "step2-draft.png");
  const id = await save(page, true);
  await expect(page.locator("#request-status")).toHaveText("submitted");
  await expect(page.locator('[data-action="approve"]')).toHaveCount(0);
  const claimed = agent(["claim", "--next"]);
  expect(claimed.request.id).toBe(id);
  const token = claimed.request.claim.token;
  agent(["plan", id, "--token", token, "--file", "-"], { summary: "Review connectivity and tune the load in scratch OA.", steps: ["Check the base revision.", "Apply the approved changes in scratch OA.", "Run DC and AC."], risks: "The proposed connection merges two nets. Check the bias first." });
  await expect(page.locator("#request-status")).toHaveText("planned", { timeout: 15000 });
  await expect(page.locator("#request-details")).toContainText("The proposed connection merges two nets.");
  await expect(page.locator('[data-action="accept"]')).toHaveCount(0);
  await page.getByLabel("Review comment", { exact: true }).fill("Approved for scratch OA only. Check the bias before AC.");
  await screenshot(page, info, "step2-approval.png");
  await page.locator('[data-action="approve"]').click();
  await expect(page.locator("#request-status")).toHaveText("approved");
  agent(["start", id, "--token", token]);
  await expect(page.locator("#request-status")).toHaveText("executing", { timeout: 15000 });
  await expect(page.locator("#request-details [data-action]")).toHaveCount(0);
  agent(["result", id, "--token", token, "--file", "-"], { outcome: "met", summary: "Synthetic test results meet both targets. No OA was edited.", metrics: [{ name: "gain", value: "42", unit: "dB", target: "> 40 dB", met: true }, { name: "bias", value: "0.8", unit: "mA", target: "< 1 mA", met: true }], artifacts: ["results/demo-summary.txt"] });
  await expect(page.locator("#request-status")).toHaveText("result-pending", { timeout: 15000 });
  await expect(page.locator("#request-details tbody tr")).toHaveCount(2);
  await expect(page.locator("#request-details")).toContainText("results/demo-summary.txt");
  await page.getByLabel("Review comment", { exact: true }).fill("Both targets met. Accept the result.");
  await page.locator('[data-action="accept"]').click();
  await expect(page.locator("#request-status")).toHaveText("completed");
  await expect(page.locator("#request-history li")).toHaveCount(8);
  await expect(page.locator("#request-history")).toContainText("Both targets met. Accept the result.");
  for (const event of ["claim", "plan", "approve", "start", "result", "accept"]) await expect(page.locator("#request-history")).toContainText(event);
  await expect(page.locator("#request-details [data-action]")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("save a draft, submit it, and reject the agent plan", async ({ page }) => {
  await build(page);
  await page.getByRole("button", { name: "Remove change 2", exact: true }).click();
  await expect(page.locator("#draft-changes > li")).toHaveCount(1);
  const id = await save(page, false);
  await expect(page.locator("#request-status")).toHaveText("draft");
  await expect(page.locator('[data-action="approve"]')).toHaveCount(0);
  await page.locator('[data-action="submit"]').click();
  await expect(page.locator("#request-status")).toHaveText("submitted");
  const token = agent(["claim", id]).request.claim.token;
  agent(["plan", id, "--token", token, "--file", "-"], { summary: "Merge the proposed nets.", steps: ["Review topology."], risks: "The bias can change." });
  await expect(page.locator("#request-status")).toHaveText("planned", { timeout: 15000 });
  await page.getByLabel("Review comment", { exact: true }).fill("Do not merge these nets.");
  await page.locator('[data-action="reject"]').click();
  await expect(page.locator("#request-status")).toHaveText("rejected");
  await expect(page.locator("#request-history")).toContainText("Do not merge these nets.");
  await expect(page.locator("#request-details [data-action]")).toHaveCount(0);
});

test("a real stale revision response preserves the local draft", async ({ page }) => {
  await build(page);
  await page.route("**/api/requests", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const payload = route.request().postDataJSON() as Record<string, unknown>;
    await route.continue({ postData: JSON.stringify({ ...payload, revision: "stale-synthetic-revision" }) });
  });
  const response = page.waitForResponse((response) => response.url().endsWith("/api/requests") && response.request().method() === "POST");
  await page.locator("#submit-draft").click();
  expect((await response).status()).toBe(409);
  await expect(page.locator("#status")).toContainText("Conflict:");
  await expect(page.locator("#status")).toContainText("Reload the schematic");
  await expect(page.locator("#draft-changes > li")).toHaveCount(2);
  await expect(page.getByLabel("Title", { exact: true })).toHaveValue("Review differential load");
});

test("demo checks highlight a finding and update when a draft connects the floating body", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.locator("#checks-status")).toContainText("Current snapshot · 1 finding");
  const finding = page.locator("#check-findings button");
  await expect(finding).toHaveCount(1);
  await expect(finding).toContainText("error · floating-terminal");
  await finding.click();
  await expect(page.locator("#instances")).toHaveValue("MN3");
  await expect(finding).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("textbox", { name: "Parameter name", exact: true }).fill("w");
  await page.getByRole("textbox", { name: "Parameter value", exact: true }).fill("2u");
  await page.getByRole("button", { name: "Add parameters", exact: true }).click();
  await page.getByLabel("Title", { exact: true }).fill("Tie body to source");
  await expect(page.locator("#checks-status")).toContainText("Snapshot + draft preview · 1 finding");
  await expect(page.locator("#submit-draft")).toBeEnabled();
  await finding.click();
  await screenshot(page, info, "step2-checks.png");
  await page.locator("#terminal-list button").filter({ hasText: /^BOpen$/ }).click();
  await page.locator("#terminal-list button").filter({ hasText: /^SVSS$/ }).click();
  await page.getByRole("button", { name: "Add connect", exact: true }).click();
  await expect(page.locator("#checks-status")).toContainText("Snapshot + draft preview · 0 findings");
  await expect(finding).toHaveCount(0);
  await expect(page.locator("#submit-draft")).toBeEnabled();
  await page.getByRole("button", { name: "Remove change 2", exact: true }).click();
  await expect(page.locator("#checks-status")).toContainText("Snapshot + draft preview · 1 finding");
  await expect(finding).toContainText("MN3.B has no net.");
});

test("unapplied parameter rows survive connect edits for the same instance", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.locator("#cell-title")).toHaveText("Differential pair");
  await page.locator("#instances").selectOption("MN3");
  const names = page.getByRole("textbox", { name: "Parameter name", exact: true });
  const values = page.getByRole("textbox", { name: "Parameter value", exact: true });
  await names.fill("w");
  await values.fill("2u");
  await page.getByRole("button", { name: "Add row", exact: true }).click();
  await page.locator("#terminal-list button").filter({ hasText: /^BOpen$/ }).click();
  await page.locator("#terminal-list button").filter({ hasText: /^SVSS$/ }).click();
  await page.getByRole("button", { name: "Add connect", exact: true }).click();
  await expect(names).toHaveCount(2);
  await expect(names.first()).toHaveValue("w");
  await expect(values.first()).toHaveValue("2u");
  await expect(names.last()).toHaveValue("");
  await page.getByRole("button", { name: "Remove change 1", exact: true }).click();
  await expect(values.first()).toHaveValue("2u");
  await page.getByRole("button", { name: "Remove parameter row", exact: true }).last().click();
  await page.getByRole("button", { name: "Add parameters", exact: true }).click();
  await expect(page.locator("#draft-changes")).toContainText('"w":"2u"');
  await values.fill("3u");
  await page.locator("#instances").selectOption("MP1");
  await expect(names).toHaveValue("");
  await expect(values).toHaveValue("");
  await page.locator("#instances").selectOption("MN3");
  await expect(values).toHaveValue("2u");
  await screenshot(page, info, "parameter-input-preserved.png");
});

test("discarded parameter rows reset on reopen, remove, and save", async ({ page }) => {
  await page.goto("/");
  const names = page.getByRole("textbox", { name: "Parameter name", exact: true });
  const values = page.getByRole("textbox", { name: "Parameter value", exact: true });
  const apply = page.getByRole("button", { name: "Add parameters", exact: true });
  const fill = async () => {
    await names.fill("width");
    await values.fill("2u");
  };
  const blank = async () => {
    await expect(names).toHaveValue("");
    await expect(values).toHaveValue("");
  };

  await fill();
  const opened = page.waitForResponse((reply) => reply.url().endsWith("/api/demo"));
  await page.locator("#open-demo").click();
  await opened;
  await blank();

  await fill();
  await apply.click();
  await page.getByRole("button", { name: "Remove change 1", exact: true }).click();
  await blank();

  await fill();
  await apply.click();
  await page.getByLabel("Title", { exact: true }).fill("Store parameter edit");
  await save(page, false);
  await expect(page.locator("#draft-changes > li")).toHaveCount(0);
  await blank();
});

test("flip previews MN1 geometry, reports broken wiring, toggles back, and submits", async ({ page }, info) => {
  await page.goto("/");
  await expect(page.locator("#checks-status")).toContainText("Current snapshot · 1 finding");
  await page.locator("#instances").selectOption("MN1");
  await expect(page.locator("#master")).toContainText("R0");
  const canvas = page.locator("canvas");
  const before = await canvas.evaluate((element: HTMLCanvasElement) => element.toDataURL());
  await page.getByRole("button", { name: "Flip left-right", exact: true }).click();
  await expect(page.locator("#master")).toContainText("MY");
  await expect(page.locator("#draft-changes")).toContainText("MN1: R0 → MY");
  const finding = page.locator("#check-findings button").filter({ hasText: "dangling-wire-end" }).first();
  await expect(finding).toBeVisible();
  expect(await canvas.evaluate((element: HTMLCanvasElement) => element.toDataURL())).not.toBe(before);
  await finding.click();
  await page.screenshot({ path: join(process.env.VWB_SCREENSHOT_DIR ?? info.outputDir, "step2-flip.png"), fullPage: true });
  await page.getByRole("button", { name: "Flip left-right", exact: true }).click();
  await expect(page.locator("#master")).toContainText("R0");
  await expect(page.locator("#draft-changes > li")).toHaveCount(0);
  await expect(page.locator("#checks-status")).toContainText("Current snapshot · 1 finding");
  await expect(finding).toHaveCount(0);
  await page.getByRole("button", { name: "Flip left-right", exact: true }).click();
  await expect(finding).toBeVisible();
  await page.getByLabel("Title", { exact: true }).fill("Flip MN1 and repair wiring");
  await page.getByLabel("Simulation setup", { exact: true }).fill("Run DC after the approved flip and wire repair.");
  await page.getByLabel("Acceptance targets", { exact: true }).fill("Keep the bbox center fixed. Repair disconnected wires.");
  await screenshot(page, info, "step2-gray.png");
  const response = page.waitForResponse((response) => response.url().endsWith("/api/requests") && response.request().method() === "POST");
  await page.locator("#submit-draft").click();
  const result = await response;
  expect(result.status()).toBe(201);
  const payload = await result.json() as { request: { changes: unknown[] } };
  expect(payload.request.changes).toEqual([{ op: "flip-horizontal", instance: "MN1", from_orientation: "R0", to_orientation: "MY" }]);
  await expect(page.locator("#request-status")).toHaveText("submitted");
});
