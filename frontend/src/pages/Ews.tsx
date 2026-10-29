import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Badge, Button, Card, CardBody, CardHeader, Disclaimer, EmptyState, ErrorState, Input, Label, NotAvailable, PageHeader, PageSkeleton, Segmented, Select, SimBadge, StatusBadge, Textarea } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { inr, title } from "@/lib/format";
import { useRole } from "@/lib/role";
import { cn } from "@/lib/utils";

const COMP: [string, string, number][] = [["risk", "Risk", 40], ["exposure", "Exposure", 25], ["urgency", "Urgency", 25], ["actionability", "Actionability", 10]];

function Detail({ id }: { id: string }) {
  const { role } = useRole();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["alert", id], queryFn: () => api.get(`/ews/alerts/${id}`) });
  const [action, setAction] = useState(""), [outcome, setOutcome] = useState("no_change"), [notes, setNotes] = useState("");
  const fb = useMutation({ mutationFn: () => api.post(`/ews/alerts/${id}/feedback`, { action_taken: action, outcome, notes }), onSuccess: () => { toast.success("Feedback recorded"); setAction(""); setNotes(""); qc.invalidateQueries(); }, onError: (e: Error) => toast.error(e.message) });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} />;
  const a = q.data;
  const canFb = ["Relationship Manager", "Risk Manager", "Credit Analyst"].includes(role);
  return (
    <div className="space-y-4">
      <Card><CardHeader title={<Link to={`/portfolio/${a.sme_id}`} className="hover:underline">{a.sme_name}</Link>} sub={`${a.industry} · RM ${a.assigned_rm} · exposure ${inr(a.exposure)}`} right={<div className="flex gap-2"><StatusBadge value={a.priority_level} /><StatusBadge value={a.review_status} /></div>} />
        <CardBody className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-4">{COMP.map(([k, label, max]) => (
            <div key={k}><div className="flex justify-between text-xs"><span className="text-muted">{label}</span><span className="num font-semibold">{(a.components[k] ?? 0).toFixed(0)}/{max}</span></div>
              <div className="mt-1 h-2 rounded-full bg-ivory-2"><div className="h-2 rounded-full bg-navy" style={{ width: `${((a.components[k] ?? 0) / max) * 100}%` }} /></div></div>))}</div>
          <p className="text-sm"><span className="num text-2xl font-bold text-slate">{Math.round(a.priority_score)}</span><span className="text-muted"> / 100 priority. </span>{a.days_to_stress != null && <span>Stress expected in about <strong>{a.days_to_stress} days</strong>.</span>}</p>
          <div className="rounded-xl bg-teal-soft/50 p-3"><div className="text-xs font-semibold text-teal-dark">Recommended action</div><p className="text-sm font-semibold text-slate">{a.recommended_action}</p></div>
        </CardBody></Card>
      <Card><CardHeader title="Signals" /><CardBody className="space-y-2">{a.signals.map((s: J, i: number) => (
        <div key={i} className="rounded-lg border border-line bg-white p-3"><div className="flex items-center justify-between gap-2"><span className="text-sm font-semibold text-slate">{s.label}</span><div className="flex items-center gap-2">{s.is_simulated && <SimBadge>Simulated input</SimBadge>}<span className="num text-xs text-muted">severity {(s.severity * 100).toFixed(0)}</span></div></div>
          <p className="mt-1 text-sm text-ink">{s.description}</p></div>))}
        {a.missing_data.length > 0 && <div className="pt-1">{a.missing_data.map((m: string, i: number) => <NotAvailable key={i} reason={m} />)}</div>}</CardBody></Card>
      <Card><CardHeader title="Talking points for the conversation" /><CardBody><ul className="list-disc space-y-1.5 pl-5 text-sm">{a.talking_points.map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></CardBody></Card>
      <Card><CardHeader title="RM feedback" sub="Logged outcomes adjust future prioritisation slightly" />
        <CardBody className="space-y-3">
          {a.feedback.map((f: J) => <div key={f.id} className="rounded-lg bg-white p-2.5 text-sm"><div className="flex items-center justify-between"><span className="font-semibold text-slate">{f.action_taken}</span><StatusBadge value={f.outcome} /></div><div className="text-xs text-muted">{f.rm_name} · {f.notes}</div></div>)}
          {canFb ? (<div className="grid gap-2 md:grid-cols-2"><div className="md:col-span-2"><Label>Action taken</Label><Input value={action} onChange={(e) => setAction(e.target.value)} placeholder="e.g. Called the owner; agreed a cash-buffer review" /></div>
            <div><Label>Outcome</Label><Select value={outcome} onChange={(e) => setOutcome(e.target.value)}>{["resolved", "improving", "no_change", "worsening", "escalated"].map((o) => <option key={o} value={o}>{title(o)}</option>)}</Select></div>
            <div><Label>Notes</Label><Input value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
            <div className="md:col-span-2"><Button disabled={!action.trim()} loading={fb.isPending} onClick={() => fb.mutate()}>Log feedback</Button></div></div>) : <p className="text-xs text-muted">Feedback is logged by RMs, Credit Analysts and Risk Managers.</p>}
        </CardBody></Card>
    </div>
  );
}

export default function Ews() {
  const { role } = useRole();
  const qc = useQueryClient();
  const [level, setLevel] = useState<"" | "critical" | "high" | "medium" | "low">("");
  const [sel, setSel] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["alerts", level], queryFn: () => api.get(`/ews/alerts${level ? `?level=${level}` : ""}`) });
  const scan = useMutation({ mutationFn: () => api.post("/ews/scan"), onSuccess: (r) => { toast.success(`Portfolio re-scored: ${r.alerts_updated} alert(s) updated`); qc.invalidateQueries(); }, onError: (e: Error) => toast.error(e.message) });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const alerts: J[] = q.data.alerts;
  const cur = sel ?? alerts[0]?.id ?? null;
  return (
    <div>
      <PageHeader title="Early-warning system" sub="Ranks clients that need an RM conversation. Priority is explainable: risk, exposure, urgency and actionability each add visible points." right={<>
        <Segmented value={level} onChange={setLevel} options={[{ value: "", label: "All" }, { value: "critical", label: "Critical" }, { value: "high", label: "High" }, { value: "medium", label: "Medium" }]} />
        <Button variant="secondary" size="sm" disabled={!["Risk Manager", "Relationship Manager"].includes(role)} loading={scan.isPending} onClick={() => scan.mutate()}><Radar className="h-3.5 w-3.5" />Scan portfolio</Button></>} />
      <div className="mb-4 grid gap-2 md:grid-cols-2"><Disclaimer>{q.data.priority_formula}</Disclaimer><Disclaimer>{q.data.data_note}</Disclaimer></div>
      {alerts.length === 0 ? <EmptyState title="No open alerts" hint="Scan the portfolio to re-score every active client." /> : (
        <div className="grid gap-4 lg:grid-cols-[360px_1fr]">
          <Card className="h-fit"><CardBody className="space-y-1 pt-3">{alerts.map((a) => (
            <button key={a.id} onClick={() => setSel(a.id)} className={cn("flex w-full cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 text-left hover:bg-ivory-2", a.id === cur && "bg-ivory-2 ring-1 ring-teal/40")}>
              <div className={cn("grid h-10 w-10 shrink-0 place-items-center rounded-lg text-sm font-bold num", a.priority_level === "critical" ? "bg-risk-soft text-risk" : a.priority_level === "high" ? "bg-warn-soft text-warn" : "bg-sand-soft text-[#7a5a0b]")}>{Math.round(a.priority_score)}</div>
              <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold text-slate">{a.sme_name}</div><div className="truncate text-xs text-muted">{title(a.primary_signal)} · {inr(a.exposure)}</div></div><StatusBadge value={a.review_status} /></button>))}</CardBody></Card>
          {cur && <Detail id={cur} key={cur} />}
        </div>)}
      <Badge tone="outline" className="mt-4">Effectiveness metrics are shown in Model Performance only when real outcome data exists</Badge>
    </div>
  );
}
