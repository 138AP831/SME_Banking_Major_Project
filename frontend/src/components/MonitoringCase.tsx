import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, CardBody, CardHeader, ErrorState, Input, Label, PageSkeleton, Select, StatusBadge, Textarea } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { dateTime, inr, title } from "@/lib/format";
import { useRole } from "@/lib/role";

const CASE_ROLES = ["Relationship Manager", "Risk Manager", "Credit Analyst"];
const RECEIVER_ROLES = ["Risk Manager", "Credit Analyst"];

/** Existing-loan monitoring steps 6 and 7: RM contact & verify, then escalate if unresolved. Bank-only (staff roles). */
export function MonitoringCase({ alertId }: { alertId: string }) {
  const { role } = useRole();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["case", alertId], queryFn: () => api.get(`/bank/cases/${alertId}`) });
  const refresh = () => qc.invalidateQueries();
  const [c, setC] = useState({ channel: "phone", issue_verified: "confirmed", outcome: "no_change", business_situation: "", commitment_amount: "", commitment_due: "", notes: "" });
  const [esc, setEsc] = useState({ target: "Credit Risk", reason: "" });
  const [res, setRes] = useState("");
  const onErr = (e: Error) => toast.error(e.message);
  const contact = useMutation({ mutationFn: () => api.post(`/bank/cases/${alertId}/contacts`, { ...c, commitment_amount: c.commitment_amount || null, commitment_due: c.commitment_due || null }), onSuccess: () => { toast.success("Contact recorded"); setC({ ...c, business_situation: "", commitment_amount: "", commitment_due: "", notes: "" }); refresh(); }, onError: onErr });
  const commit = useMutation({ mutationFn: (v: { id: string; status: string }) => api.post(`/bank/contacts/${v.id}/commitment`, { status: v.status }), onSuccess: refresh, onError: onErr });
  const escalate = useMutation({ mutationFn: () => api.post(`/bank/cases/${alertId}/escalate`, esc), onSuccess: () => { toast.success(`Escalated to ${esc.target}`); setEsc({ ...esc, reason: "" }); refresh(); }, onError: onErr });
  const escStatus = useMutation({ mutationFn: (v: { id: string; status: string }) => api.post(`/bank/escalations/${v.id}/status`, { status: v.status, resolution: res }), onSuccess: () => { toast.success("Escalation updated"); setRes(""); refresh(); }, onError: onErr });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data, canAct = CASE_ROLES.includes(role), canReceive = RECEIVER_ROLES.includes(role);
  const steps: [string, boolean][] = [["Early warning", d.steps.early_warning], ["Prioritised", d.steps.prioritised], ["Contact & verify", d.steps.contact_verify], ["Escalated", d.steps.escalated]];
  return (
    <>
      <Card>
        <CardHeader title="Monitoring case: contact, verify, escalate" sub="Confirm the signal with the borrower, record any repayment commitment, and escalate if distress persists or a commitment is missed. Bank-only; never shown to the customer." />
        <CardBody className="space-y-4">
          <div className="flex flex-wrap gap-3">{steps.map(([l, ok]) => <span key={l} className="flex items-center gap-1.5 text-sm font-semibold text-slate">{ok ? <CheckCircle2 className="h-4 w-4 text-teal" /> : <Circle className="h-4 w-4 text-muted" />}{l}</span>)}</div>
          {d.contacts.map((x: J) => (
            <div key={x.id} className="rounded-lg border border-line bg-white p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-semibold text-slate">{title(x.channel)} contact · {x.rm_name}</span>
                <span className="flex gap-1.5"><Badge tone={x.issue_verified === "confirmed" ? "risk" : x.issue_verified === "not_confirmed" ? "teal" : "sand"}>Signal {title(x.issue_verified).toLowerCase()}</Badge><StatusBadge value={x.outcome} /></span></div>
              <p className="mt-1">{x.business_situation}</p>
              {x.commitment && <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-sand-soft/50 p-2 text-xs"><span>Commitment {inr(x.commitment.amount)} by {x.commitment.due}</span><StatusBadge value={x.commitment.status} />{x.commitment.overdue && <Badge tone="risk">Overdue</Badge>}
                {x.commitment.status === "pending" && canAct && <span className="ml-auto flex gap-1.5"><Button size="sm" variant="secondary" onClick={() => commit.mutate({ id: x.id, status: "kept" })}>Kept</Button><Button size="sm" variant="secondary" onClick={() => commit.mutate({ id: x.id, status: "missed" })}>Missed</Button></span>}</div>}
              <div className="mt-1 text-[11px] text-muted">{dateTime(x.created_at + "Z")}</div>
            </div>))}
          {canAct ? (
            <div className="grid gap-2 md:grid-cols-3">
              <div><Label>Channel</Label><Select value={c.channel} onChange={(e) => setC({ ...c, channel: e.target.value })}>{["phone", "visit", "email", "video"].map((o) => <option key={o} value={o}>{title(o)}</option>)}</Select></div>
              <div><Label>Is the signal accurate?</Label><Select value={c.issue_verified} onChange={(e) => setC({ ...c, issue_verified: e.target.value })}>{["confirmed", "partially_confirmed", "not_confirmed", "unreachable"].map((o) => <option key={o} value={o}>{title(o)}</option>)}</Select></div>
              <div><Label>Outcome</Label><Select value={c.outcome} onChange={(e) => setC({ ...c, outcome: e.target.value })}>{["resolved", "improving", "no_change", "worsening"].map((o) => <option key={o} value={o}>{title(o)}</option>)}</Select></div>
              <div className="md:col-span-3"><Label>Business situation</Label><Textarea rows={2} value={c.business_situation} onChange={(e) => setC({ ...c, business_situation: e.target.value })} placeholder="What did the borrower say about the business and the cause of the signal?" /></div>
              <div><Label hint="(optional)">Repayment commitment (₹)</Label><Input type="number" min={0} value={c.commitment_amount} onChange={(e) => setC({ ...c, commitment_amount: e.target.value })} /></div>
              <div><Label hint="(with amount)">Due date</Label><Input type="date" value={c.commitment_due} onChange={(e) => setC({ ...c, commitment_due: e.target.value })} /></div>
              <div><Label>Notes</Label><Input value={c.notes} onChange={(e) => setC({ ...c, notes: e.target.value })} /></div>
              <div className="md:col-span-3"><Button loading={contact.isPending} disabled={c.business_situation.trim().length < 10} onClick={() => contact.mutate()}>Record contact</Button></div>
            </div>) : <p className="text-xs text-muted">Contacts are recorded by Relationship Managers, Credit Analysts and Risk Managers.</p>}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Escalation" sub="Refer to Credit Risk or Collections. The reason and a snapshot of the evidence are kept with the case." />
        <CardBody className="space-y-3">
          {d.escalations.map((e: J) => (
            <div key={e.id} className="rounded-lg border border-line bg-white p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2"><span className="font-semibold text-slate">{e.target} · {title(e.trigger)}</span><StatusBadge value={e.status} /></div>
              <p className="mt-1">{e.reason}</p><p className="mt-1 text-xs text-muted">By {e.created_by} ({e.created_role}) · {dateTime(e.created_at + "Z")} · evidence kept: {e.evidence?.alert?.signals?.length ?? 0} signal(s), {e.evidence?.contacts?.length ?? 0} contact(s), exposure {inr(e.evidence?.exposure)}</p>
              {e.resolution && <p className="mt-1 text-xs"><span className="font-semibold">Resolution: </span>{e.resolution}</p>}
              {canReceive && e.status !== "closed" && <div className="mt-2 flex flex-wrap items-end gap-2">{e.status === "open" && <Button size="sm" variant="secondary" onClick={() => escStatus.mutate({ id: e.id, status: "accepted" })}>Accept</Button>}
                <Input className="max-w-xs" placeholder="How was it resolved?" value={res} onChange={(ev) => setRes(ev.target.value)} /><Button size="sm" disabled={res.trim().length < 10} onClick={() => escStatus.mutate({ id: e.id, status: "closed" })}>Close</Button></div>}
            </div>))}
          {d.eligibility.can_escalate && canAct ? (
            <div className="grid gap-2 md:grid-cols-[180px_1fr]">
              <div><Label>Refer to</Label><Select value={esc.target} onChange={(e) => setEsc({ ...esc, target: e.target.value })}><option>Credit Risk</option><option>Collections</option></Select></div>
              <div><Label>Reason for escalation</Label><Input value={esc.reason} onChange={(e) => setEsc({ ...esc, reason: e.target.value })} placeholder="Why is this unresolved?" /></div>
              <div className="md:col-span-2 flex items-center gap-3"><Button variant="danger" loading={escalate.isPending} disabled={esc.reason.trim().length < 10} onClick={() => escalate.mutate()}>Escalate case</Button><span className="text-xs text-muted">Trigger: {title(d.eligibility.trigger)}</span></div>
            </div>) : d.escalations.length === 0 && <p className="text-sm text-muted">{d.eligibility.reason}</p>}
        </CardBody>
      </Card>

      {d.timeline.length > 0 && (
        <Card><CardHeader title="Case audit trail" /><CardBody><ul className="space-y-1.5 text-sm">{d.timeline.map((t: J, i: number) => <li key={i} className="flex gap-3"><span className="w-28 shrink-0 text-xs text-muted">{dateTime(t.at)}</span><span>{t.summary}</span></li>)}</ul></CardBody></Card>)}
    </>
  );
}
