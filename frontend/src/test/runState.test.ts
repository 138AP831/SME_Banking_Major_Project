import { describe, expect, it } from "vitest";
import { deriveNodeStates, edgeState, terminal } from "@/lib/runState";

const ids = ["a", "b", "c"];
const active = new Set(ids);

describe("deriveNodeStates", () => {
  it("starts everything pending and marks inactive nodes", () => {
    const s = deriveNodeStates(ids, new Set(["a"]), []);
    expect(s.a.status).toBe("pending");
    expect(s.b.status).toBe("inactive");
  });
  it("follows real events: started -> completed", () => {
    const s = deriveNodeStates(ids, active, [
      { node: "a", event_type: "started" },
      { node: "a", event_type: "completed", duration_ms: 12, output: { ok: 1 } },
      { node: "b", event_type: "started" },
    ]);
    expect(s.a.status).toBe("completed");
    expect(s.a.duration_ms).toBe(12);
    expect(s.b.status).toBe("running");
    expect(s.c.status).toBe("pending");
  });
  it("shows interrupted then resumed gates", () => {
    const base = [{ node: "a", event_type: "started" }, { node: "a", event_type: "interrupted" }];
    expect(deriveNodeStates(ids, active, base).a.status).toBe("interrupted");
    expect(deriveNodeStates(ids, active, [...base, { node: "a", event_type: "resumed" }]).a.status).toBe("running");
  });
  it("records failures", () => {
    const s = deriveNodeStates(ids, active, [{ node: "a", event_type: "failed", error: { message: "x" } }]);
    expect(s.a.status).toBe("failed");
  });
  it("marks untouched nodes skipped once a run completes", () => {
    const s = deriveNodeStates(ids, active, [{ node: "a", event_type: "completed" }], "completed");
    expect(s.b.status).toBe("skipped");
  });
});

describe("edgeState / terminal", () => {
  it("activates an edge while the target runs", () => {
    const s = deriveNodeStates(ids, active, [{ node: "a", event_type: "completed" }, { node: "b", event_type: "started" }]);
    expect(edgeState("a", "b", s)).toBe("active");
    expect(edgeState("b", "c", s)).toBe("idle");
  });
  it("terminal statuses", () => {
    expect(terminal("completed")).toBe(true);
    expect(terminal("awaiting_review")).toBe(false);
  });
});
