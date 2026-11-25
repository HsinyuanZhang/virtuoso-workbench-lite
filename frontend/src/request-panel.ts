import { allowedActions, parseList, parseRequest, parseResponse, singleFlight } from "./requests";
import type { DesignRequest, Action, Summary } from "./requests";

export async function requestApi(path: string, body?: unknown): Promise<Record<string, unknown>> {
  const response = await fetch(path, body === undefined ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return parseResponse(response.status, await response.json());
}

function node<K extends keyof HTMLElementTagNameMap>(tag: K, text: string, className?: string): HTMLElementTagNameMap[K] {
  const result = document.createElement(tag);
  result.textContent = text;
  if (className) result.className = className;
  return result;
}

export class RequestPanel {
  selected: DesignRequest | null = null;
  private selectedId: string | null = null;
  private generation = 0;
  private busy = false;
  private stopped = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private summaries: Summary[] = [];
  private list = document.getElementById("request-list")!;
  private details = document.getElementById("request-details")!;
  private status = document.getElementById("request-poll-status")!;

  constructor(private changed: (request: DesignRequest | null) => void, private message: (message: string, error?: boolean) => void) {
    void this.tick();
    window.addEventListener("pagehide", () => { this.stopped = true; clearTimeout(this.timer); });
  }

  private async tick(): Promise<void> {
    await this.refresh();
    if (!this.stopped) this.timer = setTimeout(() => { void this.tick(); }, 5000);
  }

  refresh = singleFlight(() => this.poll());

  private async poll(): Promise<void> {
    try {
      this.summaries = parseList((await requestApi("/api/requests")).requests);
      this.renderList();
      this.status.textContent = this.summaries.length ? "Updates every 5 seconds." : "No requests yet.";
      this.status.classList.remove("error");
      if (this.selectedId && !this.busy) await this.load(this.selectedId, false);
    } catch (error) {
      this.status.textContent = `Cannot refresh requests: ${error instanceof Error ? error.message : String(error)}`;
      this.status.classList.add("error");
    }
  }

  async select(id: string): Promise<void> {
    this.selectedId = id;
    this.selected = null;
    this.changed(null);
    this.details.hidden = false;
    this.details.replaceChildren(node("p", "Loading request…"));
    this.renderList();
    try { await this.load(id, true); }
    catch (error) { this.details.replaceChildren(node("p", error instanceof Error ? error.message : String(error), "error")); }
  }

  private async load(id: string, force: boolean): Promise<void> {
    const generation = ++this.generation;
    const request = parseRequest((await requestApi(`/api/requests/${encodeURIComponent(id)}`)).request);
    if (id !== this.selectedId || generation !== this.generation || this.busy) return;
    const changed = force || this.selected?.updated_at !== request.updated_at || this.selected?.history.length !== request.history.length;
    this.selected = request;
    if (changed) this.renderDetails();
    this.changed(request);
  }

  private renderList(): void {
    this.list.replaceChildren(...[...this.summaries].reverse().map((request) => {
      const entry = node("li", ""), button = node("button", request.title);
      button.type = "button";
      button.setAttribute("aria-pressed", String(request.id === this.selectedId));
      button.append(node("span", request.id), node("span", `${request.status} · ${request.claim?.agent ?? "Unclaimed"}${request.lease_expired ? " · lease expired" : ""}`), node("span", request.updated_at));
      button.addEventListener("click", () => { void this.select(request.id); });
      entry.append(button);
      return entry;
    }));
  }

  private renderDetails(): void {
    const request = this.selected;
    if (!request) return;
    const previousComment = this.details.querySelector<HTMLTextAreaElement>("#decision-comment")?.value ?? "";
    const scroll = this.details.scrollTop;
    this.details.hidden = false;
    this.details.replaceChildren(node("h2", request.title), node("p", `${request.id} · ${request.target.library}/${request.target.cell}/${request.target.view}`));
    const status = node("p", request.status);
    status.id = "request-status";
    this.details.append(status);
    const section = (title: string, text: string): void => { this.details.append(node("h3", title), node("p", text || "Not provided.")); };
    if (request.plan) {
      section("Agent plan", `${request.plan.agent} · ${request.plan.submitted_at}\n${request.plan.summary}`);
      const steps = node("ol", "");
      steps.append(...request.plan.steps.map((step) => node("li", step)));
      this.details.append(steps);
      section("Risks", request.plan.risks ?? "None stated.");
    }
    const actions = allowedActions(request.status);
    if (actions.length) {
      const comment = node("textarea", "");
      comment.id = "decision-comment";
      comment.maxLength = 2000;
      comment.rows = 2;
      comment.value = previousComment;
      const label = node("label", "Review comment");
      label.htmlFor = comment.id;
      const buttons = node("div", "", "actions");
      for (const action of actions) {
        const button = node("button", action[0].toUpperCase() + action.slice(1));
        button.type = "button";
        button.dataset.action = action;
        const event = action === "approve" ? "plan" : action === "accept" ? "result" : undefined;
        const reviewedEvent = event === undefined ? undefined : request.history.map((entry) => entry.event).lastIndexOf(event);
        button.addEventListener("click", () => { void this.act(request.id, action, comment.value, reviewedEvent); });
        buttons.append(button);
      }
      this.details.append(label, comment, buttons);
    }
    if (request.result) {
      const result = request.result;
      section("Result", `${result.agent} · ${result.submitted_at}\n${result.summary}`);
      this.details.append(node("p", result.outcome, result.outcome === "met" ? "met" : result.outcome === "error" || result.outcome === "not-met" ? "error" : undefined));
      const table = node("table", ""), head = node("thead", ""), row = node("tr", "");
      for (const heading of ["Metric", "Value", "Target", "Met"]) row.append(node("th", heading));
      head.append(row);
      const body = node("tbody", "");
      for (const metric of result.metrics) {
        const row = node("tr", "");
        row.append(node("td", metric.name), node("td", `${metric.value} ${metric.unit ?? ""}`.trim()), node("td", metric.target ?? "—"), node("td", metric.met === undefined ? "Not stated" : metric.met ? "Yes" : "No", metric.met === undefined ? undefined : metric.met ? "met" : "error"));
        body.append(row);
      }
      table.append(head, body);
      this.details.append(table);
      section("Artifacts", result.artifacts.join("\n"));
      if (result.final_revision) section("Final revision", result.final_revision);
    }
    if (request.decision) section("Last decision", `${request.decision.action} · ${request.decision.by} · ${request.decision.at}\n${request.decision.comment}`);
    section("Simulation setup", request.simulation);
    section("Acceptance targets", request.acceptance);
    section("Note", request.note);
    this.details.append(node("h3", "Changes"));
    const changes = node("ol", "", "change-list");
    changes.append(...request.changes.map((change) => node("li", JSON.stringify(change))));
    this.details.append(changes, node("h3", "History"));
    const history = node("ol", "");
    history.id = "request-history";
    history.append(...request.history.map((entry) => node("li", `${entry.at} · ${entry.actor} · ${entry.event} · ${entry.from ?? "new"} → ${entry.to}${entry.message ? ` · ${entry.message}` : ""}`)));
    this.details.append(history);
    this.details.scrollTop = scroll;
  }

  private async act(id: string, action: Action, comment: string, reviewedEvent?: number): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    ++this.generation;
    this.details.querySelectorAll("button").forEach((button) => { button.disabled = true; });
    try {
      const request = parseRequest((await requestApi(`/api/requests/${encodeURIComponent(id)}/${action}`, { comment, reviewed_event: reviewedEvent })).request);
      if (this.selectedId === id) {
        this.selected = request;
        this.renderDetails();
        this.changed(request);
      }
      this.message(`Request ${request.status}.`);
    } catch (error) { this.message(error instanceof Error ? error.message : String(error), true); }
    finally {
      this.busy = false;
      this.details.querySelectorAll("button").forEach((button) => { button.disabled = false; });
      await this.refresh();
    }
  }
}
