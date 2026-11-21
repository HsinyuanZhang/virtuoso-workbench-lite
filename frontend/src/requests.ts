import type { Snapshot } from "./types";

export const statuses = ["draft", "submitted", "claimed", "planned", "approved", "executing", "result-pending", "completed", "rejected", "cancelled", "failed"] as const;
export type Status = typeof statuses[number];
export type Action = "submit" | "cancel" | "approve" | "revise" | "reject" | "accept";
export interface Endpoint { instance: string; terminal: string }
export interface Connect { op: "connect"; endpoints: [Endpoint, Endpoint]; net?: string; outcome?: string; affected_nets?: string[]; affected_top_level_pins?: string[] }
export interface Parameters { op: "set-parameters"; instance: string; parameters: Record<string, string> }
export type DraftChange = Connect | Parameters;
export type Change = DraftChange | { op: "disconnect"; instance: string; terminal: string }
  | { op: "add-instance"; name: string; library: string; cell: string; view: string; parameters: Record<string, string> }
  | { op: "remove-instance"; name: string } | { op: "add-pin"; name: string; direction: string; net: string };
export interface Target { source: string; library: string; cell: string; view: string }
export interface Summary {
  id: string; title: string; status: Status; target: Target; updated_at: string;
  claim: { agent: string; harness: string } | null; lease_expired: boolean;
}
export interface DesignRequest extends Summary {
  changes: Change[]; simulation: string; acceptance: string; note: string;
  plan: { agent: string; submitted_at: string; summary: string; steps: string[]; risks?: string } | null;
  decision: { action: string; by: string; at: string; comment: string } | null;
  result: { agent: string; submitted_at: string; outcome: string; summary: string;
    metrics: { name: string; value: string; unit?: string; target?: string; met?: boolean }[];
    artifacts: string[]; final_revision?: string } | null;
  history: { at: string; actor: string; event: string; from: string | null; to: string; message: string }[];
}

export function allowedActions(status: Status): Action[] {
  switch (status) {
    case "draft": return ["submit", "cancel"];
    case "submitted": case "claimed": case "approved": return ["cancel"];
    case "planned": return ["cancel", "approve", "revise", "reject"];
    case "result-pending": return ["accept", "revise"];
    default: return [];
  }
}

const name = /^[A-Za-z0-9_.$+!@-]{1,128}$/;
export function setParameters(changes: DraftChange[], instance: string, rows: [string, string][]): DraftChange[] {
  const parameters: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const [key, value] of rows) {
    if (!name.test(key) || key.trim() !== key || !value.length || value.length > 64) throw new Error("Use a valid parameter name and a value of 1–64 characters.");
    if (Object.hasOwn(parameters, key)) throw new Error(`Duplicate parameter: ${key}`);
    parameters[key] = value;
  }
  if (!rows.length || rows.length > 64) throw new Error("Add 1–64 parameter rows.");
  const change: Parameters = { op: "set-parameters", instance, parameters };
  const index = changes.findIndex((entry) => entry.op === "set-parameters" && entry.instance === instance);
  if (index < 0) return appendChange(changes, change);
  return changes.map((entry, position) => position === index ? change : entry);
}

export function appendChange(changes: DraftChange[], change: DraftChange): DraftChange[] {
  if (changes.length >= 200) throw new Error("A request can contain at most 200 changes.");
  return [...changes, change];
}

export function buildDraft(snapshot: Snapshot, changes: DraftChange[], fields: { title: string; simulation: string; acceptance: string; note: string }, submit: boolean) {
  if (!fields.title.trim() || fields.title.length > 120) throw new Error("Enter a title of 1–120 characters.");
  if (!changes.length || changes.length > 200) throw new Error("Add 1–200 changes.");
  if (fields.simulation.length > 4000 || fields.acceptance.length > 4000 || fields.note.length > 2000) throw new Error("Request text exceeds its limit.");
  const { source, library, cell, view, revision } = snapshot;
  return { target: { source, library, cell, view }, revision, ...fields, changes, submit };
}

export function sameTarget(target: Target, snapshot: Snapshot | null): boolean {
  return snapshot !== null && (["source", "library", "cell", "view"] as const).every((key) => target[key] === snapshot[key]);
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid server response.");
  return value as Record<string, unknown>;
}
function string(value: unknown): string {
  if (typeof value !== "string") throw new Error("Invalid server response.");
  return value;
}
function array<T>(value: unknown, parse: (entry: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new Error("Invalid server response.");
  return value.map(parse);
}
function optional(value: unknown): string | undefined { return value === undefined ? undefined : string(value); }
function nullable<T>(value: unknown, parse: (entry: Record<string, unknown>) => T): T | null { return value === null ? null : parse(object(value)); }

export function parseResponse(status: number, value: unknown): Record<string, unknown> {
  const data = object(value);
  if (status < 200 || status >= 300 || data.ok === false) {
    const error = typeof data.error === "string" ? data.error : object(data.error).message;
    const message = string(error);
    throw new Error(status === 409 ? `Conflict: ${message} Reload the schematic or request before you retry.` : message);
  }
  return data;
}

export function parseSummary(value: unknown): Summary {
  const data = object(value), target = object(data.target);
  const status = statuses.find((entry) => entry === data.status);
  if (!status || typeof data.lease_expired !== "boolean") throw new Error("Invalid server response.");
  return { id: string(data.id), title: string(data.title), status, updated_at: string(data.updated_at),
    target: { source: string(target.source), library: string(target.library), cell: string(target.cell), view: string(target.view) },
    claim: nullable(data.claim, (claim) => ({ agent: string(claim.agent), harness: string(claim.harness) })), lease_expired: data.lease_expired };
}

export function parseChange(value: unknown): Change {
  const change = object(value);
  const parameters = (value: unknown): Record<string, string> => Object.fromEntries(Object.entries(object(value)).map(([key, entry]) => [key, string(entry)]));
  switch (change.op) {
    case "connect": {
      const endpoints = array(change.endpoints, (entry) => { const end = object(entry); return { instance: string(end.instance), terminal: string(end.terminal) }; });
      if (endpoints.length !== 2) throw new Error("Invalid server response.");
      return { op: "connect", endpoints: [endpoints[0], endpoints[1]], net: optional(change.net), outcome: optional(change.outcome), affected_nets: change.affected_nets === undefined ? undefined : array(change.affected_nets, string), affected_top_level_pins: change.affected_top_level_pins === undefined ? undefined : array(change.affected_top_level_pins, string) };
    }
    case "set-parameters": return { op: change.op, instance: string(change.instance), parameters: parameters(change.parameters) };
    case "disconnect": return { op: change.op, instance: string(change.instance), terminal: string(change.terminal) };
    case "remove-instance": return { op: change.op, name: string(change.name) };
    case "add-instance": return { op: change.op, name: string(change.name), library: string(change.library), cell: string(change.cell), view: string(change.view), parameters: parameters(change.parameters) };
    case "add-pin": return { op: change.op, name: string(change.name), direction: string(change.direction), net: string(change.net) };
    default: throw new Error("Invalid server response.");
  }
}

export function parseRequest(value: unknown): DesignRequest {
  const data = object(value);
  return { ...parseSummary(data), changes: array(data.changes, parseChange), simulation: string(data.simulation), acceptance: string(data.acceptance), note: string(data.note),
    plan: nullable(data.plan, (plan) => ({ agent: string(plan.agent), submitted_at: string(plan.submitted_at), summary: string(plan.summary), steps: array(plan.steps, string), risks: optional(plan.risks) })),
    decision: nullable(data.decision, (decision) => ({ action: string(decision.action), by: string(decision.by), at: string(decision.at), comment: string(decision.comment) })),
    result: nullable(data.result, (result) => ({ agent: string(result.agent), submitted_at: string(result.submitted_at), outcome: string(result.outcome), summary: string(result.summary), final_revision: optional(result.final_revision), artifacts: array(result.artifacts, string),
      metrics: array(result.metrics, (value) => { const metric = object(value); if (metric.met !== undefined && typeof metric.met !== "boolean") throw new Error("Invalid server response."); return { name: string(metric.name), value: string(metric.value), unit: optional(metric.unit), target: optional(metric.target), met: metric.met }; }) })),
    history: array(data.history, (value) => { const entry = object(value); return { at: string(entry.at), actor: string(entry.actor), event: string(entry.event), from: entry.from === null ? null : string(entry.from), to: string(entry.to), message: string(entry.message) }; }),
  };
}

export function parseList(value: unknown): Summary[] { return array(value, parseSummary); }

export function singleFlight<T>(operation: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | null = null;
  return () => {
    if (!pending) pending = operation().finally(() => { pending = null; });
    return pending;
  };
}
