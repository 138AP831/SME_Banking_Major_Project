import type { J } from "./api";

export type NodeStatus = "pending" | "running" | "completed" | "failed" | "interrupted" | "skipped" | "inactive";
export interface NodeState { status: NodeStatus; duration_ms?: number | null; input?: J; output?: J; metrics?: J; error?: J; events: J[] }

/** Derive per-node state purely from the real backend events. */
export function deriveNodeStates(nodeIds: string[], activeIds: Set<string>, events: J[], runStatus?: string): Record<string, NodeState> {
  const out: Record<string, NodeState> = {};
  for (const id of nodeIds) out[id] = { status: activeIds.has(id) ? "pending" : "inactive", events: [] };
  for (const e of events) {
    const s = out[e.node];
    if (!s) continue;
    s.events.push(e);
    if (e.event_type === "started" || e.event_type === "resumed") { s.status = "running"; if (e.input) s.input = e.input; }
    else if (e.event_type === "completed") { s.status = "completed"; s.duration_ms = (s.duration_ms ?? 0) + (e.duration_ms ?? 0); s.output = e.output; s.metrics = e.metrics; }
    else if (e.event_type === "failed") { s.status = "failed"; s.error = e.error; s.duration_ms = e.duration_ms; }
    else if (e.event_type === "interrupted") { s.status = "interrupted"; s.metrics = e.metrics; s.duration_ms = e.duration_ms; }
  }
  if (runStatus === "completed" || runStatus === "failed" || runStatus === "cancelled") {
    for (const id of nodeIds) if (out[id].status === "pending") out[id].status = runStatus === "completed" ? "skipped" : "pending";
  }
  return out;
}

export function edgeState(src: string, dst: string, states: Record<string, NodeState>): "idle" | "active" | "done" {
  const a = states[src], b = states[dst];
  if (src === "__start__") return b && b.status !== "pending" && b.status !== "inactive" && b.status !== "skipped" ? (b.status === "running" ? "active" : "done") : "idle";
  if (dst === "__end__") return a?.status === "completed" ? "done" : "idle";
  if (!a || !b) return "idle";
  if (a.status === "completed" && (b.status === "running")) return "active";
  if (a.status === "completed" && (b.status === "completed" || b.status === "interrupted" || b.status === "failed")) return "done";
  return "idle";
}

export function terminal(status?: string) { return status === "completed" || status === "failed" || status === "cancelled"; }
