import { describe, expect, it } from "vitest";
import demoData from "../../src/virtuoso_workbench/demo.json";
import { allowedActions, appendChange, buildDraft, parseChange, parseList, parseRequest, parseResponse, sameTarget, setParameters, singleFlight, statuses } from "./requests";
import type { Connect, Status } from "./requests";
import type { Snapshot } from "./types";

const snapshot = { ...demoData, source: "demo", revision: "synthetic" } as unknown as Snapshot;
const connect: Connect = { op: "connect", endpoints: [{ instance: "MP1", terminal: "D" }, { instance: "MP2", terminal: "D" }] };
const fields = { title: "Review load", simulation: "Run DC.", acceptance: "Keep bias stable.", note: "Use scratch OA." };
const target = { source: snapshot.source, library: snapshot.library, cell: snapshot.cell, view: snapshot.view };
const summary = { id: "req-123456789abc", title: fields.title, status: "submitted", target, updated_at: "2026-01-01T00:00:00Z", claim: null, lease_expired: false };
const request = { ...summary, ...fields, changes: [connect], plan: null, decision: null, result: null, history: [] };

describe("draft builder", () => {
  it("appends without mutation and merges parameter edits for the same instance", () => {
    const changes = appendChange([], connect);
    const parameters = setParameters(changes, "MP1", [["w", "2u"], ["l", "180n"]]);
    expect(changes).toEqual([connect]);
    const updated = setParameters(parameters, "MP1", [["w", "4u"]]);
    expect(updated).toHaveLength(2);
    expect(updated[1]).toEqual({ op: "set-parameters", instance: "MP1", parameters: { w: "4u" } });
    expect(parameters[1]).toMatchObject({ parameters: { w: "2u", l: "180n" } });
    expect(setParameters(updated, "MP2", [["w", "2u"]])).toHaveLength(3);
  });
  it("builds saved and submitted drafts with the current target and revision", () => {
    expect(buildDraft(snapshot, [connect], fields, false)).toEqual({ target, revision: "synthetic", ...fields, changes: [connect], submit: false });
    expect(buildDraft(snapshot, [connect], fields, true).submit).toBe(true);
  });
  const invalidRows: [string, string][][] = [[], [["", "1"]], [["bad/name", "1"]], [["w\n", "1"]], [["w", ""]], [["w", "x".repeat(65)]], [["w", "1"], ["w", "2"]], Array.from({ length: 65 }, (_, i) => [`p${i}`, "1"])];
  it.each(invalidRows.map((rows) => ({ rows })))("rejects invalid parameter rows $rows", ({ rows }) => {
    expect(() => setParameters([], "MP1", rows)).toThrow();
  });
  it("supports prototype-like parameter names as data", () => {
    const changes = setParameters([], "MP1", [["__proto__", "1"]]);
    expect(JSON.stringify(changes)).toContain('"__proto__":"1"');
  });
  it("rejects empty drafts, long text and excess changes", () => {
    expect(() => buildDraft(snapshot, [], fields, false)).toThrow();
    for (const invalid of [{ title: " " }, { title: "x".repeat(121) }, { simulation: "x".repeat(4001) }, { acceptance: "x".repeat(4001) }, { note: "x".repeat(2001) }]) {
      expect(() => buildDraft(snapshot, [connect], { ...fields, ...invalid }, true)).toThrow();
    }
    expect(() => appendChange(Array.from({ length: 200 }, () => connect), connect)).toThrow();
    expect(() => setParameters(Array.from({ length: 200 }, () => connect), "MP1", [["w", "2u"]])).toThrow();
  });
  it("matches all four target fields before highlighting", () => {
    expect(sameTarget(target, snapshot)).toBe(true);
    expect(sameTarget(target, null)).toBe(false);
    for (const key of ["source", "library", "cell", "view"] as const) expect(sameTarget({ ...target, [key]: "different" }, snapshot)).toBe(false);
  });
});

describe("human actions", () => {
  const expected: Record<Status, string[]> = {
    draft: ["submit", "cancel"], submitted: ["cancel"], claimed: ["cancel"],
    planned: ["cancel", "approve", "revise", "reject"], approved: ["cancel"],
    executing: [], "result-pending": ["accept", "revise"], completed: [], rejected: [], cancelled: [], failed: [],
  };
  it.each(statuses)("shows only legal actions for %s", (status) => expect(allowedActions(status)).toEqual(expected[status]));
});

describe("response parsing", () => {
  it("parses empty lists, unclaimed requests and complete agent responses", () => {
    expect(parseList([])).toEqual([]);
    expect(parseList([summary])).toEqual([summary]);
    expect(parseRequest(request)).toEqual(request);
    const complete = { ...request, claim: { agent: "demo-agent", harness: "test" },
      plan: { agent: "demo-agent", submitted_at: "now", summary: "Tune load", steps: ["Run DC"], risks: "Bias shift" },
      decision: { action: "approve", by: "human", at: "now", comment: "Use scratch." },
      result: { agent: "demo-agent", submitted_at: "now", outcome: "met", summary: "Stable", metrics: [{ name: "gain", value: "42", unit: "dB", target: "40", met: true }, { name: "bias", value: "1" }], artifacts: ["results/dc.txt"], final_revision: "final" },
      history: [{ at: "now", actor: "human", event: "create", from: null, to: "draft", message: "" }] };
    expect(parseRequest(complete)).toEqual(complete);
    expect(parseResponse(200, { ok: true, requests: [] })).toEqual({ ok: true, requests: [] });
  });
  it.each([null, [], {}, { ...summary, status: "unknown" }, { ...summary, claim: {} }, { ...summary, lease_expired: "false" }])("rejects malformed summary %j", (value) => expect(() => parseList([value])).toThrow());
  it.each([{}, { ...request, changes: [{}] }, { ...request, plan: {} }, { ...request, result: {} }, { ...request, history: [null] }, { ...request, simulation: null }])("rejects malformed request %j", (value) => expect(() => parseRequest(value)).toThrow());
  it("parses every operation and rejects malformed endpoints and parameters", () => {
    for (const change of [connect, { op: "set-parameters", instance: "MP1", parameters: { w: "2u" } }, { op: "disconnect", instance: "MP1", terminal: "D" }, { op: "remove-instance", name: "MP1" }, { op: "add-pin", name: "OUT", direction: "output", net: "VOUT" }, { op: "add-instance", name: "MNEW", library: "devices", cell: "pmos", view: "symbol", parameters: {} }]) expect(parseChange(change)).toEqual(change);
    for (const change of [{ ...connect, endpoints: [] }, { ...connect, endpoints: [null, null] }, { op: "set-parameters", instance: "MP1", parameters: { w: 2 } }]) expect(() => parseChange(change)).toThrow();
  });
  it("shows server errors, including stale-revision conflicts", () => {
    expect(() => parseResponse(409, { ok: false, error: { code: "stale-revision", message: "Snapshot revision changed." } })).toThrow("Conflict: Snapshot revision changed.");
    expect(() => parseResponse(403, { error: "Origin rejected." })).toThrow("Origin rejected.");
    expect(() => parseResponse(200, { ok: false, error: { message: "Failed" } })).toThrow("Failed");
    for (const data of [null, [], {}, { error: 3 }]) expect(() => parseResponse(500, data)).toThrow();
  });
});

it("does not overlap refreshes and permits retry after success or failure", async () => {
  let calls = 0;
  let resolve: (value: number) => void = () => {};
  let reject: (reason: Error) => void = () => {};
  const refresh = singleFlight(() => {
    calls++;
    return new Promise<number>((yes, no) => { resolve = yes; reject = no; });
  });
  const first = refresh();
  expect(refresh()).toBe(first);
  expect(calls).toBe(1);
  resolve(1);
  await expect(first).resolves.toBe(1);
  const second = refresh();
  expect(calls).toBe(2);
  reject(new Error("Offline"));
  await expect(second).rejects.toThrow("Offline");
  const third = refresh();
  expect(calls).toBe(3);
  resolve(3);
  await expect(third).resolves.toBe(3);
});
