import { afterEach, expect, it, vi } from "vitest";
import { parseFindings, previewChecks } from "./checks";
import type { Finding } from "./checks";

const finding: Finding = { code: "floating-terminal", severity: "error", message: "MN3.B has no net.", instance: "MN3", terminal: "B", points: [[1.7, -6]] };
afterEach(() => vi.useRealTimers());

it("parses findings, optional locations, warnings, and empty results", () => {
  expect(parseFindings([])).toEqual([]);
  expect(parseFindings([finding])).toEqual([finding]);
  const warning = { code: "net-not-joined", severity: "warning", message: "Two groups.", net: "N", pin: "OUT" };
  expect(parseFindings([warning])).toEqual([warning]);
});

const invalid: unknown[] = [null, {}, [null], [[]], [{}], [{ ...finding, severity: "info" }], [{ ...finding, net: 1 }], [{ ...finding, points: "invalid" }], [{ ...finding, points: [[0]] }], [{ ...finding, points: [[0, Infinity]] }], [{ ...finding, points: [["0", 1]] }]];
it.each(invalid.map((value) => ({ value })))("rejects malformed findings $value", ({ value }) => {
  expect(() => parseFindings(value)).toThrow("Invalid checks response.");
});

it("debounces edits and ignores a late response from an old draft", async () => {
  vi.useFakeTimers();
  const apply = vi.fn(), failed = vi.fn();
  let first: (findings: Finding[]) => void = () => {};
  let second: (findings: Finding[]) => void = () => {};
  const load = vi.fn<(payload: number, signal: AbortSignal) => Promise<Finding[]>>()
    .mockImplementationOnce(() => new Promise((resolve) => { first = resolve; }))
    .mockImplementationOnce(() => new Promise((resolve) => { second = resolve; }));
  const preview = previewChecks(load, apply, failed);
  preview.schedule(1);
  preview.schedule(2);
  await vi.advanceTimersByTimeAsync(300);
  expect(load).toHaveBeenCalledTimes(1);
  expect(load.mock.calls[0][0]).toBe(2);
  preview.schedule(3);
  expect(load.mock.calls[0][1].aborted).toBe(true);
  await vi.advanceTimersByTimeAsync(300);
  second([]);
  first([finding]);
  await vi.advanceTimersByTimeAsync(0);
  expect(apply).toHaveBeenCalledExactlyOnceWith([]);
  expect(failed).not.toHaveBeenCalled();
});

it("cancels pending work on a snapshot change and reports current failures", async () => {
  vi.useFakeTimers();
  const load = vi.fn<(payload: number, signal: AbortSignal) => Promise<Finding[]>>().mockRejectedValue(new Error("Stale revision"));
  const failed = vi.fn(), apply = vi.fn();
  const preview = previewChecks(load, apply, failed);
  preview.schedule(1);
  preview.cancel();
  await vi.advanceTimersByTimeAsync(300);
  expect(load).not.toHaveBeenCalled();
  preview.schedule(2);
  await vi.advanceTimersByTimeAsync(300);
  expect(failed).toHaveBeenCalledExactlyOnceWith("Stale revision");
  expect(apply).not.toHaveBeenCalled();
});
