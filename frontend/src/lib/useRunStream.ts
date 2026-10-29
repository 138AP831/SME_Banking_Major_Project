import { useCallback, useEffect, useRef, useState } from "react";
import { api, type J } from "./api";

/** Loads a run, then follows its real execution events over SSE (falls back to polling if SSE fails). */
export function useRunStream(runId: string | null) {
  const [run, setRun] = useState<J | null>(null);
  const [events, setEvents] = useState<J[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState(false);
  const last = useRef(0);
  const es = useRef<EventSource | null>(null);
  const poll = useRef<number | null>(null);
  const [nonce, setNonce] = useState(0);

  const refreshRun = useCallback(async () => {
    if (!runId) return;
    try { const r = await api.get(`/workflows/runs/${runId}`); setRun(r); return r; } catch (e) { setError((e as Error).message); }
  }, [runId]);

  useEffect(() => {
    last.current = 0; setEvents([]); setRun(null); setError(null);
    if (!runId) return;
    let closed = false;
    const add = (ev: J) => { if (ev.seq > last.current) { last.current = ev.seq; setEvents((p) => [...p, ev]); } };
    const stopPoll = () => { if (poll.current) { window.clearInterval(poll.current); poll.current = null; } };
    const startPoll = () => {
      stopPoll();
      poll.current = window.setInterval(async () => {
        try { const evs: J[] = await api.get(`/workflows/runs/${runId}/events?after=${last.current}`); evs.forEach(add); const r = await refreshRun();
          if (r && ["completed", "failed", "cancelled", "awaiting_review"].includes(r.status) && evs.length === 0) { setLive(false); stopPoll(); } } catch { /* retry */ }
      }, 700);
    };
    (async () => {
      const r = await refreshRun();
      if (closed || !r) return;
      r.events.forEach(add);
      if (["completed", "failed", "cancelled"].includes(r.status)) { setLive(false); return; }
      try {
        const src = new EventSource(`/api/workflows/runs/${runId}/stream?after=${last.current}`);
        es.current = src; setLive(true);
        src.addEventListener("node_event", (m) => { add(JSON.parse((m as MessageEvent).data)); });
        src.addEventListener("run_state", async () => { src.close(); setLive(false); await refreshRun(); });
        src.onerror = () => { src.close(); if (!closed) startPoll(); };
      } catch { startPoll(); }
    })();
    return () => { closed = true; es.current?.close(); stopPoll(); setLive(false); };
  }, [runId, nonce, refreshRun]);

  // keep the run header (status, final output) in sync with arriving events
  useEffect(() => {
    const t = events[events.length - 1];
    if (t && ["run_completed", "run_failed", "run_cancelled", "run_paused"].includes(t.event_type)) refreshRun();
  }, [events, refreshRun]);

  const reconnect = useCallback(() => setNonce((n) => n + 1), []);
  return { run, events, live, error, reconnect, refreshRun };
}
