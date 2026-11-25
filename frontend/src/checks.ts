import type { Point } from "./types";

export interface Finding {
  code: string; severity: "error" | "warning"; message: string;
  instance?: string; terminal?: string; net?: string; pin?: string; points?: Point[];
}

export function parseFindings(value: unknown): Finding[] {
  if (!Array.isArray(value)) throw new Error("Invalid checks response.");
  return value.map((entry: unknown) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) throw new Error("Invalid checks response.");
    const data = entry as Record<string, unknown>;
    if (typeof data.code !== "string" || typeof data.message !== "string" || (data.severity !== "error" && data.severity !== "warning")) throw new Error("Invalid checks response.");
    const finding: Finding = { code: data.code, severity: data.severity, message: data.message };
    for (const field of ["instance", "terminal", "net", "pin"] as const) {
      if (data[field] !== undefined) {
        if (typeof data[field] !== "string") throw new Error("Invalid checks response.");
        finding[field] = data[field];
      }
    }
    if (data.points !== undefined) {
      if (!Array.isArray(data.points)) throw new Error("Invalid checks response.");
      finding.points = data.points.map((point: unknown): Point => {
        if (!Array.isArray(point) || point.length !== 2 || !point.every((coordinate: unknown) => typeof coordinate === "number" && Number.isFinite(coordinate))) throw new Error("Invalid checks response.");
        return [point[0] as number, point[1] as number];
      });
    }
    return finding;
  });
}

export function previewChecks<T>(load: (payload: T, signal: AbortSignal) => Promise<Finding[]>, apply: (findings: Finding[]) => void, failed: (message: string) => void, delay = 300) {
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let controller: AbortController | undefined;
  function cancel(): void {
    generation++;
    clearTimeout(timer);
    controller?.abort();
  }
  return {
    cancel,
    schedule(payload: T): void {
      cancel();
      const current = generation;
      timer = setTimeout(() => {
        const active = new AbortController();
        controller = active;
        void load(payload, active.signal).then((findings) => {
          if (current === generation) apply(findings);
        }).catch((error: unknown) => {
          if (current === generation && !active.signal.aborted) failed(error instanceof Error ? error.message : String(error));
        });
      }, delay);
    },
  };
}
