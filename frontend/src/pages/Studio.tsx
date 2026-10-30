import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dagre from "@dagrejs/dagre";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertOctagon, Ban, CheckCircle2, CircleDashed, Clock, FlaskConical, Loader2, PauseCircle, Play, RotateCcw, SkipForward, Square, UserCheck } from "lucide-react";
import { memo, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { JsonView } from "@/components/JsonView";
import { ReviewPanel } from "@/components/ReviewPanel";
import { SmePicker } from "@/components/common";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, Label, PageHeader, Select, StatusBadge, Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { dateTime, title } from "@/lib/format";
import { useRole } from "@/lib/role";
import { deriveNodeStates, edgeState, terminal, type NodeState } from "@/lib/runState";
import { useRunStream } from "@/lib/useRunStream";
import { cn } from "@/lib/utils";

const W = 188, H = 64;
const TYPE_COLOR: Record<string, string> = { customer: "#0A9396", data: "#1D3557", validation: "#1D3557", features: "#14506f", model: "#0A9396", policy: "#7a5a0b", human: "#7a5a0b", explain: "#14506f" };

const ICON = (s: NodeState["status"]) => {
  switch (s) {
    case "completed": return <CheckCircle2 className="h-4 w-4 text-teal" />;
    case "running": return <Loader2 className="h-4 w-4 animate-spin text-teal" />;
    case "failed": return <AlertOctagon className="h-4 w-4 text-risk" />;
    case "interrupted": return <PauseCircle className="h-4 w-4 text-[#b8860b]" />;
    case "skipped": return <SkipForward className="h-4 w-4 text-muted" />;
    case "inactive": return <Ban className="h-4 w-4 text-muted/60" />;
    default: return <CircleDashed className="h-4 w-4 text-muted" />;
  }
};

const FlowNode = memo(({ data, selected }: NodeProps) => {
  const d = data as J, st: NodeState = d.state;
  const border = st.status === "running" ? "border-teal pulse" : st.status === "completed" ? "border-teal" : st.status === "failed" ? "border-risk" : st.status === "interrupted" ? "border-sand pulse-sand" : "border-line";
  const bg = st.status === "completed" ? "bg-teal-soft" : st.status === "failed" ? "bg-risk-soft" : st.status === "interrupted" ? "bg-sand-soft" : st.status === "inactive" ? "bg-ivory-2/60" : "bg-paper";
  return (
    <div data-testid={`node-${d.id}`} data-status={st.status} className={cn("rounded-xl border-2 px-3 py-2 shadow-sm transition-colors", border, bg, selected && "ring-2 ring-navy", st.status === "inactive" && "opacity-55")} style={{ width: W, height: H }}>
      <Handle type="target" position={Position.Top} className="!h-2 !w-2 !border-0 !bg-navy/50" />
      <div className="flex items-center gap-2">{ICON(st.status)}<span className="truncate text-[13px] font-semibold text-slate">{d.label}</span></div>
      <div className="mt-1 flex items-center justify-between text-[11px] text-muted">
        <span style={{ color: TYPE_COLOR[d.type] }} className="font-semibold">{d.type === "human" ? "human gate" : d.type}</span>
        <span className="num">{st.duration_ms != null ? `${st.duration_ms} ms` : st.status === "inactive" ? "not in template" : st.status === "skipped" ? "not required" : st.status === "interrupted" ? "waiting" : ""}</span>
      </div>
      <Handle type="source" position={Position.Bottom} className="!h-2 !w-2 !border-0 !bg-navy/50" />
    </div>
  );
});
FlowNode.displayName = "FlowNode";
const nodeTypes = { step: FlowNode };

function layout(spec: J, states: Record<string, NodeState>): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", ranksep: 34, nodesep: 26, marginx: 10, marginy: 10 });
  g.setDefaultEdgeLabel(() => ({}));
  const real = spec.nodes.filter((n: J) => n.active);
  real.forEach((n: J) => g.setNode(n.id, { width: W, height: H }));
  g.setNode("__start__", { width: 70, height: 36 }); g.setNode("__end__", { width: 70, height: 36 });
  spec.edges.forEach((e: J) => g.setEdge(e.source, e.target));
  dagre.layout(g);
  const nodes: Node[] = [
    ...real.map((n: J) => { const p = g.node(n.id); return { id: n.id, type: "step", position: { x: p.x - W / 2, y: p.y - H / 2 }, data: { ...n, state: states[n.id] }, draggable: false }; }),
    ...["__start__", "__end__"].map((id) => { const p = g.node(id); return { id, position: { x: p.x - 35, y: p.y - 18 }, data: { label: id === "__start__" ? "Start" : "End" }, draggable: false, selectable: false,
      style: { width: 70, height: 36, borderRadius: 18, background: "#003049", color: "#fff", fontSize: 12, fontWeight: 600, display: "grid", placeItems: "center", border: "none" }, sourcePosition: Position.Bottom, targetPosition: Position.Top } as Node; }),
  ];
  const edges: Edge[] = spec.edges.map((e: J) => {
    const st = edgeState(e.source, e.target, states);
    return { id: `${e.source}-${e.target}`, source: e.source, target: e.target, className: st === "done" ? "done" : st === "active" ? "active" : "", type: "smoothstep", markerEnd: { type: MarkerType.ArrowClosed, color: st === "idle" ? "#9fb3c0" : "#0A9396", width: 16, height: 16 } };
  });
  return { nodes, edges };
}

function RunMeta({ run, live }: { run: J; live: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      <StatusBadge value={run.status} />
      {live && <Badge tone="teal"><span className="h-1.5 w-1.5 animate-pulse rounded-full bg-teal" /> Live stream</Badge>}
      <span className="font-semibold text-slate">{run.template_label}</span><span className="text-muted">{run.sme_name}</span>
      <span className="font-mono text-xs text-muted">{run.id}</span>
      {run.scenario && <Badge tone="sand">Scenario {run.scenario}</Badge>}
      {run.retry_of && <Badge tone="outline">Retry of {run.retry_of}</Badge>}
      {run.duration_ms != null && <span className="inline-flex items-center gap-1 text-xs text-muted"><Clock className="h-3 w-3" />{(run.duration_ms / 1000).toFixed(1)} s</span>}
    </div>
  );
}

export default function Studio() {
  const { role } = useRole();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const [template, setTemplate] = useState("full_lifecycle");
  const [sme, setSme] = useState("SME-1003");
  const [fault, setFault] = useState("");
  const [selNode, setSelNode] = useState<string | null>(null);
  const runId = sp.get("run");
  const setRunId = (id: string | null) => setSp(id ? { run: id } : {}, { replace: true });

  const templates = useQuery({ queryKey: ["wf-templates"], queryFn: () => api.get("/workflows/templates") });
  const scenarios = useQuery({ queryKey: ["wf-scenarios"], queryFn: () => api.get("/workflows/scenarios") });
  const runs = useQuery({ queryKey: ["wf-runs"], queryFn: () => api.get("/workflows/runs?limit=30"), refetchInterval: 4000 });
  const { run, events, live, error, reconnect } = useRunStream(runId);
  const viewTemplate = run?.template ?? template;
  const graph = useQuery({ queryKey: ["wf-graph", viewTemplate], queryFn: () => api.get(`/workflows/graph/${viewTemplate}`) });

  const start = useMutation({
    mutationFn: (body: J) => api.post("/workflows/runs", body),
    onSuccess: (r) => { setRunId(r.run_id); setSelNode(null); qc.invalidateQueries({ queryKey: ["wf-runs"] }); },
    onError: (e: Error) => toast.error(e.message),
  });
  const cancel = useMutation({ mutationFn: () => api.post(`/workflows/runs/${runId}/cancel`), onSuccess: (r) => toast(r.note), onError: (e: Error) => toast.error(e.message) });
  const retry = useMutation({ mutationFn: () => api.post(`/workflows/runs/${runId}/retry`), onSuccess: (r) => setRunId(r.run_id), onError: (e: Error) => toast.error(e.message) });

  const states = useMemo(() => {
    if (!graph.data) return {} as Record<string, NodeState>;
    const ids = graph.data.nodes.map((n: J) => n.id);
    const active = new Set<string>(graph.data.nodes.filter((n: J) => n.active).map((n: J) => n.id));
    return deriveNodeStates(ids, active, events, run?.status);
  }, [graph.data, events, run?.status]);
  const flow = useMemo(() => (graph.data ? layout(graph.data, states) : null), [graph.data, states]);

  // auto-focus the node currently executing so the viewer follows the run
  useEffect(() => {
    const live = Object.entries(states).find(([, s]) => s.status === "running" || s.status === "interrupted");
    if (live && run && !terminal(run.status)) setSelNode(live[0]);
  }, [states, run]);

  const sel = selNode ? states[selNode] : null;
  const selMeta = graph.data?.nodes.find((n: J) => n.id === selNode);
  const pendingReview = run?.reviews?.find((r: J) => r.status === "pending");
  const startRun = (extra: J = {}) => start.mutate({ template, sme_id: sme, ...(fault ? { inject_fault: fault } : {}), ...extra });
  const visibleNodes: string[] = (templates.data?.find((t: J) => t.id === template)?.nodes) ?? [];

  return (
    <div>
      <PageHeader title="AI Workflow Studio" sub="Run a real LangGraph pipeline against one SME and watch each node execute. Every state shown here comes from events emitted by the backend while it runs." />

      <Card className="mb-4">
        <CardBody className="space-y-3 pt-4">
          <div className="flex flex-wrap items-end gap-3">
            <div><Label>Workflow template</Label>
              <Select value={template} onChange={(e) => { setTemplate(e.target.value); setFault(""); }} className="w-[260px]">
                {["bank", "customer"].map((p) => <optgroup key={p} label={p === "bank" ? "Bank Portal workflows" : "Customer Portal workflows"}>{(templates.data ?? []).filter((t: J) => t.portal === p).map((t: J) => <option key={t.id} value={t.id}>{t.label}</option>)}</optgroup>)}
              </Select></div>
            <div><Label>SME</Label><SmePicker value={sme} onChange={setSme} /></div>
            <Button onClick={() => startRun()} loading={start.isPending} disabled={role === "SME Customer"}><Play className="h-4 w-4" />Run workflow</Button>
            <div className="ml-auto flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-muted">Demo scenarios</span>
              {(scenarios.data ?? []).map((s: J) => (
                <Button key={s.id} size="sm" variant="secondary" title={s.expect} onClick={() => start.mutate({ scenario: s.id })} data-testid={`scenario-${s.id}`}>{s.label}</Button>
              ))}
            </div>
          </div>
          <p className="text-xs text-muted">{(() => { const t = templates.data?.find((x: J) => x.id === template); return t ? `${t.portal === "bank" ? "Bank Portal" : "Customer Portal"} · ${t.workflow}. ${t.description}` : ""; })()}</p>
          <details className="text-xs">
            <summary className="cursor-pointer font-semibold text-muted">Testing options</summary>
            <div className="mt-2 flex flex-wrap items-center gap-2"><FlaskConical className="h-4 w-4 text-[#7a5a0b]" />
              <span>Inject a failure into a node to see error handling and retry (<Badge tone="sand">test only</Badge>):</span>
              <Select value={fault} onChange={(e) => setFault(e.target.value)} className="h-8 w-[240px] text-xs"><option value="">No fault</option>{visibleNodes.map((n) => <option key={n} value={n}>{title(n)}</option>)}</Select>
            </div>
          </details>
        </CardBody>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1fr_400px]">
        <div className="space-y-4">
          {pendingReview && (
            <div>
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-slate"><UserCheck className="h-4 w-4 text-[#b8860b]" />The graph is paused at the human review gate</div>
              <ReviewPanel review={pendingReview} onDecided={() => { setTimeout(reconnect, 300); }} />
            </div>)}
          <Card>
            <CardHeader title={run ? <RunMeta run={run} live={live} /> : "Pipeline"} sub={!run ? "Choose a template and SME, then run - or open a past run on the right." : undefined}
              right={run && (
                <div className="flex gap-2">
                  {!terminal(run.status) && <Button size="sm" variant="secondary" onClick={() => cancel.mutate()}><Square className="h-3.5 w-3.5" />Cancel</Button>}
                  {terminal(run.status) && <Button size="sm" variant="secondary" onClick={() => retry.mutate()}><RotateCcw className="h-3.5 w-3.5" />Retry</Button>}
                </div>)} />
            <CardBody className="pt-0">
              <div className="h-[600px] overflow-hidden rounded-xl border border-line bg-white" data-testid="flow-canvas">
                {graph.isError ? <ErrorState error={graph.error} /> : !flow ? <div className="grid h-full place-items-center text-sm text-muted">Loading graph…</div> : (
                  <ReactFlow nodes={flow.nodes} edges={flow.edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.08 }} minZoom={0.3} maxZoom={1.4} nodesConnectable={false}
                    onNodeClick={(_, n) => n.type === "step" && setSelNode(n.id)} proOptions={{ hideAttribution: true }}>
                    <Background color="#e6e2cb" gap={22} /><Controls showInteractive={false} />
                  </ReactFlow>)}
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted">
                <span className="inline-flex items-center gap-1"><CheckCircle2 className="h-3 w-3 text-teal" />completed</span><span className="inline-flex items-center gap-1"><Loader2 className="h-3 w-3 text-teal" />running</span>
                <span className="inline-flex items-center gap-1"><PauseCircle className="h-3 w-3 text-[#b8860b]" />waiting for a human</span><span className="inline-flex items-center gap-1"><SkipForward className="h-3 w-3" />not required for this case</span>
                <span className="inline-flex items-center gap-1"><Ban className="h-3 w-3" />not part of this template</span>
              </div>
            </CardBody>
          </Card>

          {error && <ErrorState error={error} />}
          {run?.status === "failed" && run.error && (
            <div role="alert" className="rounded-xl border border-risk/30 bg-risk-soft/60 p-4 text-sm"><p className="font-semibold text-risk">Run failed: {run.error.type}</p><p className="mt-0.5">{run.error.message}</p>
              <p className="mt-1 text-xs text-muted">Completed steps are preserved in the event log. Use Retry to start a new run.</p></div>)}
          {run?.status === "cancelled" && <div className="rounded-xl border border-line bg-white p-4 text-sm">This run was cancelled before it finished. No results were persisted.</div>}
          {run?.status === "completed" && run.final_output && (
            <Card><CardHeader title="Result" sub={run.final_output.explanation ? undefined : "No narrative was generated"} right={<StatusBadge value={run.final_output.policy_outcome} />} />
              <CardBody className="space-y-3 text-sm">
                <p className="leading-relaxed text-ink">{run.final_output.explanation}</p>
                <div className="flex flex-wrap gap-2 text-xs">
                  {run.final_output.headline.credit_score != null && <Badge tone="neutral">Score {run.final_output.headline.credit_score} · {run.final_output.headline.risk_category}</Badge>}
                  {run.final_output.headline.days_to_shortfall != null && <Badge tone="risk">Shortfall in {run.final_output.headline.days_to_shortfall} days</Badge>}
                  {run.final_output.headline.priority != null && <Badge tone="warn">EWS priority {Math.round(run.final_output.headline.priority)}</Badge>}
                  {run.final_output.headline.top_product && <Badge tone="teal">Top product: {title(run.final_output.headline.top_product)}</Badge>}
                </div>
                {run.reviews?.filter((r: J) => r.status === "decided").map((r: J) => <ReviewPanel key={r.id} review={r} />)}
                <p className="text-xs text-muted">Demo policy outcome on synthetic data - not a bank decision. Records written: {Object.keys(run.final_output.persisted ?? {}).map(title).join(", ") || "none"}.</p>
              </CardBody></Card>)}
        </div>

        <div className="space-y-4">
          <Card className="min-h-[300px]">
            <CardHeader title={selMeta ? selMeta.label : "Step inspector"} sub={selMeta?.description ?? "Select a step on the canvas to inspect its input, output and timing."} right={sel && <Badge tone="outline">{sel.status}</Badge>} />
            <CardBody>
              {!selNode || !sel ? <EmptyState title="No step selected" hint="Click any node. While a run is live the inspector follows the active step." /> : (
                <Tabs defaultValue="output" key={selNode}>
                  <TabsList><TabsTrigger value="output">Output</TabsTrigger><TabsTrigger value="input">Input</TabsTrigger><TabsTrigger value="metrics">Metrics</TabsTrigger>{sel.error && <TabsTrigger value="error">Error</TabsTrigger>}</TabsList>
                  <div className="pt-3">
                    <TabsContent value="output"><JsonView data={sel.output} empty={sel.status === "pending" ? "This step has not run yet." : sel.status === "inactive" ? "This step is not part of the selected template." : sel.status === "skipped" ? "Skipped: the policy engine did not require it for this case." : sel.status === "interrupted" ? "Paused here: waiting for an authorised reviewer to decide. The output appears once they do." : "No output recorded yet."} /></TabsContent>
                    <TabsContent value="input"><JsonView data={sel.input} empty="No input recorded yet." /></TabsContent>
                    <TabsContent value="metrics"><JsonView data={{ ...(sel.metrics ?? {}), duration_ms: sel.duration_ms }} /></TabsContent>
                    {sel.error && <TabsContent value="error"><JsonView data={sel.error} /></TabsContent>}
                  </div>
                </Tabs>)}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Event log" sub={`${events.length} events from the backend`} />
            <CardBody className="max-h-[260px] overflow-y-auto pt-0">
              {events.length === 0 ? <p className="text-sm text-muted">No events yet.</p> : (
                <ol className="space-y-1 font-mono text-[11.5px]" data-testid="event-log">
                  {events.map((e) => (
                    <li key={e.seq} className="flex gap-2"><span className="w-6 shrink-0 text-right text-muted">{e.seq}</span>
                      <span className={cn("w-[82px] shrink-0", e.event_type === "failed" || e.event_type === "run_failed" ? "text-risk" : e.event_type === "interrupted" || e.event_type === "run_paused" ? "text-[#b8860b]" : "text-teal-dark")}>{e.event_type}</span>
                      <span className="truncate text-ink">{e.node.replace(/__/g, "")}</span></li>))}
                </ol>)}
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Recent runs" />
            <CardBody className="max-h-[230px] space-y-1 overflow-y-auto pt-0">
              {runs.isLoading ? <p className="text-sm text-muted">Loading…</p> : (runs.data ?? []).length === 0 ? <p className="text-sm text-muted">No runs yet. Start one above.</p> : (runs.data ?? []).map((r: J) => (
                <button key={r.id} onClick={() => { setRunId(r.id); setSelNode(null); }} className={cn("flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-ivory-2 cursor-pointer", r.id === runId && "bg-ivory-2")}>
                  <span className="min-w-0"><span className="block truncate font-semibold text-slate">{r.sme_name}</span><span className="text-muted">{r.template_label} · {dateTime(r.started_at)}</span></span><StatusBadge value={r.status} />
                </button>))}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
