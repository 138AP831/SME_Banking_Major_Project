import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Kpi } from "@/components/common";
import { JsonView } from "@/components/JsonView";
import { Badge, Card, CardBody, CardHeader, Dialog, DialogContent, EmptyState, ErrorState, Input, PageHeader, PageSkeleton, Select, StatusBadge, Table, Td, Th } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { dateTime, title } from "@/lib/format";
import { cn } from "@/lib/utils";
import { CheckCircle2 } from "lucide-react";

export default function Governance() {
  const [category, setCategory] = useState(""), [severity, setSeverity] = useState(""), [ai, setAi] = useState(""), [text, setText] = useState("");
  const [open, setOpen] = useState<J | null>(null);
  const qs = new URLSearchParams({ ...(category && { category }), ...(severity && { severity }), ...(ai && { ai_only: "true" }), ...(text && { q: text }), limit: "120" }).toString();
  const sum = useQuery({ queryKey: ["gov-sum"], queryFn: () => api.get("/governance/summary"), refetchInterval: 8000 });
  const arch = useQuery({ queryKey: ["architecture"], queryFn: () => api.get("/bank/architecture") });
  const audit = useQuery({ queryKey: ["audit", qs], queryFn: () => api.get(`/governance/audit?${qs}`), refetchInterval: 8000 });
  if (sum.isLoading) return <PageSkeleton />;
  if (sum.isError) return <ErrorState error={sum.error} retry={() => sum.refetch()} />;
  const s = sum.data;
  return (
    <div>
      <PageHeader title="Audit and governance" sub="Who or what did what, when and why. AI activity and human overrides are recorded separately so they can be reviewed." />
      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Kpi label="Audit events" value={s.audit_events} /><Kpi label="AI-generated events" value={s.ai_events} /><Kpi label="Reviews pending" value={s.reviews.pending} tone={s.reviews.pending ? "teal" : "default"} />
        <Kpi label="Human decisions" value={s.reviews.decided} hint={`${s.reviews.by_action.approve} approve · ${s.reviews.by_action.reject} reject · ${s.reviews.by_action.request_info} more info`} /><Kpi label="Failed runs" value={s.failed_runs} hint={`of ${s.workflow_runs} runs`} tone={s.failed_runs ? "risk" : "default"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Audit trail" right={<span className="text-xs text-muted">{audit.data?.total ?? 0} events</span>} />
          <CardBody>
            <div className="mb-3 flex flex-wrap gap-2">
              <Input aria-label="Search audit" className="w-[200px]" placeholder="Search summaries" value={text} onChange={(e) => setText(e.target.value)} />
              <Select aria-label="Category" className="w-[170px]" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">All categories</option>{(audit.data?.categories ?? []).map((c: string) => <option key={c}>{c}</option>)}</Select>
              <Select aria-label="Severity" className="w-[160px]" value={severity} onChange={(e) => setSeverity(e.target.value)}><option value="">Any severity</option><option>info</option><option>attention</option><option>critical</option></Select>
              <Select aria-label="Actor type" className="w-[170px]" value={ai} onChange={(e) => setAi(e.target.value)}><option value="">AI and humans</option><option value="1">AI activity only</option></Select>
            </div>
            {audit.isLoading ? <PageSkeleton /> : audit.data.events.length === 0 ? <EmptyState title="No events match" /> : (
              <div className="max-h-[560px] overflow-y-auto"><Table><thead><tr><Th>When</Th><Th>Actor</Th><Th>Event</Th><Th /></tr></thead><tbody>{audit.data.events.map((e: J) => (
                <tr key={e.id} className="cursor-pointer hover:bg-ivory-2/50" onClick={() => setOpen(e)}>
                  <Td className="whitespace-nowrap text-xs text-muted">{dateTime(e.timestamp)}</Td>
                  <Td className="text-xs"><div className="font-semibold text-slate">{e.actor}</div><div className="text-muted">{e.role}</div></Td>
                  <Td><span className={cn("text-sm", e.severity === "critical" && "font-semibold text-risk")}>{e.summary}</span></Td>
                  <Td>{e.is_ai ? <Badge tone="teal">AI</Badge> : <Badge tone="outline">Human</Badge>}</Td></tr>))}</tbody></Table></div>)}
          </CardBody>
        </Card>
        <div className="space-y-4">
          {arch.data && <Card><CardHeader title="Two-sided data architecture" sub="Bank and customer data meet only at the consent & exchange gate." /><CardBody className="space-y-2">
            {arch.data.data_layer.map((d: J, i: number) => <div key={d.id} className="rounded-lg border border-line bg-white p-2.5"><div className="text-sm font-semibold text-slate">{i + 1}. {d.label}</div><p className="text-xs text-muted">{d.contents.join(" · ")}</p></div>)}
            <p className="text-xs text-muted">Bank-only outputs (scores, alerts, RM notes, escalations) are never loaded into customer-side workflows or APIs.</p></CardBody></Card>}
          <Card><CardHeader title="Controls in place" /><CardBody className="space-y-3">{s.controls.map((c: J) => (
            <div key={c.control} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-teal" /><div><div className="text-sm font-semibold text-slate">{c.control}</div><p className="text-xs leading-relaxed text-muted">{c.detail}</p></div></div>))}</CardBody></Card>
          <Card><CardHeader title="Human overrides" sub="Latest reviewer decisions" /><CardBody className="space-y-2">{s.overrides.length === 0 ? <p className="text-sm text-muted">No decisions yet. Resolve an item in the <Link to="/reviews" className="font-semibold text-teal-dark underline">review queue</Link>.</p> : s.overrides.map((o: J) => (
            <div key={o.id} className="rounded-lg bg-white p-2.5 text-sm"><div className="flex items-center justify-between"><span className="font-semibold text-slate">{title(o.kind)}</span><StatusBadge value={o.action} /></div><p className="mt-1 text-xs text-muted">{o.reviewer} ({o.role}): “{o.reason}”</p></div>))}</CardBody></Card>
        </div>
      </div>
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>{open && <DialogContent title={title(open.action)} description={`${open.id} · ${dateTime(open.timestamp)}`}>
        <p className="mb-3 text-sm">{open.summary}</p><JsonView data={{ actor: open.actor, role: open.role, category: open.category, entity: `${open.entity_type}:${open.entity_id}`, sme_id: open.sme_id, run_id: open.run_id, details: open.details }} />
        <div className="mt-3 flex gap-3 text-sm">{open.run_id && <Link className="font-semibold text-teal-dark underline" to={`/studio?run=${open.run_id}`}>Open workflow run</Link>}{open.sme_id && <Link className="font-semibold text-teal-dark underline" to={`/portfolio/${open.sme_id}`}>Customer 360</Link>}</div></DialogContent>}</Dialog>
    </div>
  );
}
