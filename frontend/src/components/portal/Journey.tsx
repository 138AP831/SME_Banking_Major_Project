import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle, FileUp, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, CardBody, CardHeader, Disclaimer, EmptyState, ErrorState, Input, Label, PageSkeleton, SimBadge, Switch, Table, Td, Th } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { dateTime, inr, title } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useReadinessRun } from "./useReadinessRun";

export type JourneyView = "readiness" | "plan" | "growth" | "privacy";
const QS = (id: string) => `?sme_id=${id}`;
const STATUS_TONE: Record<string, "teal" | "sand" | "warn" | "neutral"> = { likely_ready: "teal", nearly_ready: "sand", needs_improvement: "warn", more_information_needed: "neutral" };
const IND_DOT: Record<string, string> = { good: "bg-teal", watch: "bg-sand", gap: "bg-risk" };

function useP<T = J>(key: string, smeId: string, path: string) {
  return useQuery<T>({ queryKey: [`portal-${key}`, smeId], queryFn: () => api.get(`/portal/${path}${QS(smeId)}`) });
}

function RunButton({ smeId, label, onNeedConsent }: { smeId: string; label: string; onNeedConsent: () => void }) {
  const consent = useP("consent", smeId, "consent");
  const run = useReadinessRun(smeId);
  const granted = consent.data?.purposes?.find((p: J) => p.purpose === "readiness_assessment")?.granted;
  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button loading={run.running} disabled={consent.isLoading} onClick={() => (granted ? run.start() : onNeedConsent())}>{label}</Button>
      {granted === false && <span className="text-xs text-muted">Needs your permission first (Privacy &amp; consent).</span>}
      {run.running && run.progress && <span className="text-xs text-muted">Step {Math.min(run.progress.completed_steps + 1, run.progress.total_steps)} of {run.progress.total_steps}{run.progress.current_step ? `: ${run.progress.current_step}` : ""}</span>}
    </div>
  );
}

/* ------------------------------------------------------------ steps 1-3 */
function SubmissionForm({ smeId }: { smeId: string }) {
  const qc = useQueryClient();
  const q = useP("submission", smeId, "submission");
  const [f, setF] = useState<J>({ annual_revenue: "", annual_expenses: "", other_liabilities: "", requested_amount: "", tenure_months: "24", purpose: "", city: "", loans: [{ lender: "", outstanding: "", monthly_emi: "" }] });
  const loaded = useRef<string | null>(null);
  useEffect(() => {
    const s = q.data?.submission;
    if (!q.data || loaded.current === smeId + (s?.version ?? 0)) return;
    loaded.current = smeId + (s?.version ?? 0);
    setF({ annual_revenue: s?.financials?.annual_revenue ?? "", annual_expenses: s?.financials?.annual_expenses ?? "", other_liabilities: s?.financials?.other_liabilities ?? "", requested_amount: s?.requested_amount ?? "",
      tenure_months: s?.tenure_months ?? "24", purpose: s?.purpose ?? "", city: s?.location?.city ?? q.data.prefill?.city ?? "", loans: s?.existing_loans?.length ? s.existing_loans : [{ lender: "", outstanding: "", monthly_emi: "" }] });
  }, [q.data, smeId]);
  const save = useMutation({
    mutationFn: () => api.post(`/portal/submission${QS(smeId)}`, { business_profile: { business_name: q.data?.prefill?.business_name, industry: q.data?.prefill?.industry, employees: q.data?.prefill?.employees },
      financials: { annual_revenue: f.annual_revenue, annual_expenses: f.annual_expenses, other_liabilities: f.other_liabilities }, existing_loans: f.loans.filter((l: J) => l.lender), requested_amount: f.requested_amount || null,
      tenure_months: f.tenure_months, purpose: f.purpose, location: { city: f.city } }),
    onSuccess: (r) => { toast.success(r.message); qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("portal-") }); }, onError: (e: Error) => toast.error(e.message),
  });
  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); return api.upload(`/portal/documents${QS(smeId)}`, fd); },
    onSuccess: (r) => { toast(r.message); qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("portal-") }); }, onError: (e: Error) => toast.error(e.message),
  });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });
  const setLoan = (i: number, k: string, v: string) => setF({ ...f, loans: f.loans.map((l: J, j: number) => (j === i ? { ...l, [k]: v } : l)) });
  const docs = q.data.documents;
  return (
    <Card>
      <CardHeader title="1. Your business information" sub={q.data.submission ? `Saved as version ${q.data.submission.version}. Saving again creates a new version that future assessments will use.` : "Tell us about your business. Figures can be approximate."} />
      <CardBody className="space-y-4">
        <div className="grid gap-3 md:grid-cols-3">
          <div><Label>Annual revenue (₹) *</Label><Input type="number" min={0} value={f.annual_revenue} onChange={set("annual_revenue")} /></div>
          <div><Label>Annual expenses (₹)</Label><Input type="number" min={0} value={f.annual_expenses} onChange={set("annual_expenses")} /></div>
          <div><Label>Other liabilities (₹)</Label><Input type="number" min={0} value={f.other_liabilities} onChange={set("other_liabilities")} /></div>
          <div><Label>Requested loan amount (₹)</Label><Input type="number" min={100000} step={50000} value={f.requested_amount} onChange={set("requested_amount")} /></div>
          <div><Label>Tenure (months)</Label><Input type="number" min={3} max={120} value={f.tenure_months} onChange={set("tenure_months")} /></div>
          <div><Label>Business location (city)</Label><Input value={f.city} onChange={set("city")} /></div>
          <div className="md:col-span-3"><Label>What would the loan be for?</Label><Input value={f.purpose} onChange={set("purpose")} placeholder="e.g. Raw material purchase" /></div>
        </div>
        <div>
          <Label hint="(optional)">Existing loans</Label>
          {f.loans.map((l: J, i: number) => (
            <div key={i} className="mb-2 grid gap-2 sm:grid-cols-3">
              <Input placeholder="Lender" value={l.lender} onChange={(e) => setLoan(i, "lender", e.target.value)} />
              <Input type="number" placeholder="Outstanding (₹)" value={l.outstanding} onChange={(e) => setLoan(i, "outstanding", e.target.value)} />
              <Input type="number" placeholder="Monthly instalment (₹)" value={l.monthly_emi} onChange={(e) => setLoan(i, "monthly_emi", e.target.value)} />
            </div>))}
          {f.loans.length < 5 && <button className="text-xs font-semibold text-teal-dark cursor-pointer" onClick={() => setF({ ...f, loans: [...f.loans, { lender: "", outstanding: "", monthly_emi: "" }] })}>+ Add another loan</button>}
        </div>
        <div className="flex flex-wrap items-center gap-3"><Button loading={save.isPending} disabled={!f.annual_revenue} onClick={() => save.mutate()}>Save information</Button></div>
        <div className="rounded-xl border border-line bg-white p-3">
          <div className="mb-2 flex items-center justify-between"><span className="text-xs font-semibold text-slate">Documents ({docs.present_count} of {docs.required_count})</span>
            <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-line px-2.5 py-1 text-xs font-semibold text-slate hover:bg-ivory-2"><FileUp className="h-3.5 w-3.5" />{upload.isPending ? "Uploading…" : "Upload a document"}
              <input type="file" className="sr-only" accept=".pdf,.png,.jpg,.jpeg,.txt" onChange={(e) => { const file = e.target.files?.[0]; if (file) upload.mutate(file); e.target.value = ""; }} /></label></div>
          <div className="grid gap-1 sm:grid-cols-2">{docs.documents.map((d: J) => <div key={d.label} className="flex items-center gap-2 text-sm">{d.uploaded ? <CheckCircle2 className="h-4 w-4 text-teal" /> : <Circle className="h-4 w-4 text-muted" />}{d.label}</div>)}</div>
          <p className="mt-2 text-xs text-muted">PAN, GSTIN and Aadhaar details are handled by our KYC team and are never shown or shared from this page.</p>
        </div>
      </CardBody>
    </Card>
  );
}

function ProfileCard({ smeId }: { smeId: string }) {
  const q = useP("profile", smeId, "financial-profile");
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const p = q.data;
  const tiles: [string, string, string?][] = [
    ["Monthly inflow", inr(p.revenue.avg_monthly_inflow), `${p.revenue.trend_pct_per_month >= 0 ? "+" : ""}${p.revenue.trend_pct_per_month.toFixed(1)}% per month trend`],
    ["Monthly outflow", inr(p.expenses.avg_monthly_outflow)],
    ["Monthly instalments", inr(Math.max(p.liabilities.monthly_debt_service_seen ?? 0, p.liabilities.declared_monthly_emi ?? 0))],
    ["Late / returned payments", `${p.repayment.late_instalments_6m} / ${p.repayment.returned_payments_90d}`, "6 months / 90 days"],
    ["Cash cover", `${Math.round(p.health.liquidity_cover_days)} days`, p.cash_flow.days_to_shortfall != null ? `Shortfall possible in ~${p.cash_flow.days_to_shortfall} days` : "No shortfall projected in 60 days"],
  ];
  const months = p.cash_flow.monthly_inflows as number[];
  return (
    <Card>
      <CardHeader title="2. Your financial profile" sub={p.note} />
      <CardBody className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">{tiles.map(([l, v, h]) => <div key={l} className="rounded-xl border border-line bg-white p-3"><div className="text-[11px] font-semibold text-muted">{l}</div><div className="num mt-1 text-lg font-bold text-slate">{v}</div>{h && <div className="text-[11px] text-muted">{h}</div>}</div>)}</div>
        <Table><thead><tr><Th>Month (oldest first)</Th>{months.map((_, i) => <Th key={i} className="text-right">M{i + 1}</Th>)}</tr></thead>
          <tbody><tr><Td>Money in</Td>{months.map((m, i) => <Td key={i} className="num text-right">{inr(m, { compact: true })}</Td>)}</tr>
            <tr><Td>Money out</Td>{(p.cash_flow.monthly_outflows as number[]).map((m, i) => <Td key={i} className="num text-right">{inr(m, { compact: true })}</Td>)}</tr></tbody></Table>
      </CardBody>
    </Card>
  );
}

function ReadinessCard({ smeId, goPrivacy }: { smeId: string; goPrivacy: () => void }) {
  const q = useP("readiness", smeId, "readiness");
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const r = q.data.latest;
  return (
    <Card>
      <CardHeader title="3. Indicative loan readiness" sub="An indicative view against illustrative criteria. It is not an approval and not a guarantee." right={r && <Badge tone={STATUS_TONE[r.status] ?? "neutral"}>{r.status_label}</Badge>} />
      <CardBody className="space-y-4">
        {!r ? <EmptyState title="No assessment yet" hint="Save your information, then run an assessment." /> : (<>
          <div className="flex items-center gap-4">
            <div><div className="num text-3xl font-bold text-slate">{r.readiness_index}<span className="text-base text-muted"> / 100</span></div><div className="text-[11px] text-muted">Readiness index (not a credit score)</div></div>
            <div className="h-2 flex-1 rounded-full bg-ivory-2"><div className="h-2 rounded-full bg-teal" style={{ width: `${r.readiness_index}%` }} /></div>
            {r.change && <Badge tone={r.change.delta >= 0 ? "teal" : "warn"}>{r.change.delta >= 0 ? "+" : ""}{r.change.delta} since last time</Badge>}
          </div>
          <p className="text-sm text-ink">{r.explanation}</p>
          <div className="space-y-2">{r.indicators.map((i: J) => (
            <div key={i.code} className="rounded-lg border border-line bg-white p-3">
              <div className="flex items-center justify-between gap-2"><span className="flex items-center gap-2 text-sm font-semibold text-slate"><span className={cn("h-2.5 w-2.5 rounded-full", IND_DOT[i.status])} />{i.label}</span><span className="text-xs text-muted">{i.value}</span></div>
              <p className="mt-1 text-sm">{i.explanation}</p><p className="text-[11px] text-muted">Illustrative target: {i.target}</p>
            </div>))}</div>
        </>)}
        <RunButton smeId={smeId} label={r ? "Request a fresh assessment" : "Run my assessment"} onNeedConsent={goPrivacy} />
        <Disclaimer>{q.data.disclaimer}</Disclaimer>
      </CardBody>
    </Card>
  );
}

/* ------------------------------------------------------------ steps 4 + 6 */
function PlanView({ smeId, goPrivacy }: { smeId: string; goPrivacy: () => void }) {
  const qc = useQueryClient();
  const plan = useP("plan", smeId, "plan");
  const rd = useP("readiness", smeId, "readiness");
  const toggle = useMutation({ mutationFn: (v: { id: string; done: boolean }) => api.post(`/portal/plan/${v.id}${QS(smeId)}`, { done: v.done }), onSuccess: () => qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("portal-") }), onError: (e: Error) => toast.error(e.message) });
  if (plan.isLoading || rd.isLoading) return <PageSkeleton />;
  if (plan.isError) return <ErrorState error={plan.error} retry={() => plan.refetch()} />;
  const p = plan.data, hist: J[] = rd.data?.history ?? [];
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="4. Your improvement plan" sub="Actions that would strengthen the areas where the indicative view found gaps, and why each one matters." right={<Badge tone="teal">{p.progress.completed} of {p.progress.total} done</Badge>} />
        <CardBody className="space-y-3">
          <div className="h-2 rounded-full bg-ivory-2"><div className="h-2 rounded-full bg-teal transition-all" style={{ width: `${p.progress.percent}%` }} /></div>
          {p.items.length === 0 ? <EmptyState title="No actions yet" hint="Run an assessment and your plan will appear here." /> : p.items.map((a: J) => (
            <div key={a.id} className={cn("rounded-xl border border-line bg-white p-3", a.status !== "open" && "opacity-80")}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><div className="text-sm font-semibold text-slate">{a.title}</div><p className="mt-1 text-sm"><span className="font-semibold">Why it matters: </span>{a.why}</p><p className="mt-1 text-sm text-muted"><span className="font-semibold text-slate">How: </span>{a.how}</p>
                  {a.status === "done" && a.latest_indicator_status && a.latest_indicator_status !== "good" && <p className="mt-1 text-xs text-[#7a5a0b]">Marked done. Your latest assessment still sees room to improve here; request a fresh assessment to update it.</p>}
                  {a.status === "resolved" && <p className="mt-1 text-xs text-teal-dark">Your latest assessment shows this area is now in good shape.</p>}</div>
                {a.status === "resolved" ? <Badge tone="teal">Improved</Badge> : <Button size="sm" variant={a.status === "done" ? "secondary" : "primary"} loading={toggle.isPending && toggle.variables?.id === a.id} onClick={() => toggle.mutate({ id: a.id, done: a.status !== "done" })}>{a.status === "done" ? "Undo" : "Mark done"}</Button>}
              </div>
            </div>))}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="6. Your progress" sub="Each assessment uses the latest information you submitted." />
        <CardBody className="space-y-3">
          {hist.length === 0 ? <p className="text-sm text-muted">No assessments yet.</p> : (
            <Table><thead><tr><Th>#</Th><Th>When</Th><Th>Indicative status</Th><Th className="text-right">Index</Th><Th>Information used</Th></tr></thead>
              <tbody>{[...hist].reverse().map((h, i, arr) => { const prev = arr[i + 1]; return (
                <tr key={h.version}><Td>{h.version}{h.is_reassessment ? " (reassessment)" : ""}</Td><Td>{dateTime(h.created_at + "Z")}</Td><Td>{h.status_label}</Td>
                  <Td className="num text-right">{h.readiness_index}{prev && <span className={cn("ml-1 text-xs", h.readiness_index - prev.readiness_index >= 0 ? "text-teal-dark" : "text-warn")}>({h.readiness_index - prev.readiness_index >= 0 ? "+" : ""}{h.readiness_index - prev.readiness_index})</span>}</Td>
                  <Td>{h.submission_version ? `Submission v${h.submission_version}` : "Account statements only"}</Td></tr>); })}</tbody></Table>)}
          <RunButton smeId={smeId} label="Request a fresh assessment" onNeedConsent={goPrivacy} />
          <p className="text-xs text-muted">Changed something? Update your information under “My application” first, then request a fresh assessment.</p>
        </CardBody>
      </Card>
    </div>
  );
}

/* ------------------------------------------------------------ step 5 */
function GrowthView({ smeId, goPrivacy }: { smeId: string; goPrivacy: () => void }) {
  const q = useP("growth", smeId, "growth");
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const g = q.data;
  if (g.consent_required) return <Card><CardHeader title="5. Business growth intelligence" /><CardBody className="space-y-3"><p className="text-sm">To look up nearby businesses and market signals we need your permission to use your city and business category.</p><Button onClick={goPrivacy}>Open Privacy &amp; consent</Button></CardBody></Card>;
  if (!g.market) return <Card><CardHeader title="5. Business growth intelligence" /><CardBody><EmptyState title="No market view yet" hint="Run a readiness assessment; your market view and SWOT are prepared right after it." /></CardBody></Card>;
  const m = g.market, w = g.growth?.swot ?? {};
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="5. Business growth intelligence" sub={`${m.category} · ${m.location}`} right={m.is_synthetic ? <SimBadge>Synthetic demo data</SimBadge> : <Badge tone="teal">Google Places</Badge>} />
        <CardBody className="space-y-4">
          <Disclaimer>{m.note}</Disclaimer>
          <div className="grid gap-3 sm:grid-cols-4">{[["Nearby competitors", m.signals.nearby_competitors], ["Competitor density", title(m.signals.competitor_density)], ["Average rating", m.signals.average_rating ?? "n/a"], ["Demand trend", m.signals.demand_trend ? title(m.signals.demand_trend) : "n/a"]].map(([l, v]) => (
            <div key={String(l)} className="rounded-xl border border-line bg-white p-3"><div className="text-[11px] font-semibold text-muted">{l}</div><div className="num mt-1 text-lg font-bold text-slate">{v as string}</div></div>))}</div>
          <Table><thead><tr><Th>Nearby business</Th><Th className="text-right">Rating</Th><Th className="text-right">Distance</Th></tr></thead><tbody>{m.competitors.map((c: J) => <tr key={c.name}><Td>{c.name}</Td><Td className="num text-right">{c.rating ?? "n/a"}</Td><Td className="num text-right">{c.distance_km != null ? `${c.distance_km} km` : ""}</Td></tr>)}</tbody></Table>
        </CardBody>
      </Card>
      <Card><CardHeader title="SWOT" sub="From your readiness indicators and the market signals above." />
        <CardBody className="grid gap-3 md:grid-cols-2">{([["strengths", "Strengths", "teal"], ["weaknesses", "Weaknesses", "warn"], ["opportunities", "Opportunities", "teal"], ["threats", "Threats", "risk"]] as const).map(([k, l, tone]) => (
          <div key={k} className="rounded-xl border border-line bg-white p-3"><Badge tone={tone}>{l}</Badge><ul className="mt-2 list-disc space-y-1 pl-5 text-sm">{(w[k] ?? []).map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></div>))}</CardBody></Card>
      <Card><CardHeader title="Growth recommendations" /><CardBody><ul className="list-disc space-y-1.5 pl-5 text-sm">{(g.growth?.recommendations ?? []).map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></CardBody></Card>
    </div>
  );
}

/* ------------------------------------------------------------ consent & privacy */
function PrivacyView({ smeId }: { smeId: string }) {
  const qc = useQueryClient();
  const q = useP("consent", smeId, "consent");
  const act = useP("activity", smeId, "activity");
  const set = useMutation({ mutationFn: (v: { purpose: string; granted: boolean }) => api.post(`/portal/consent${QS(smeId)}`, v), onSuccess: () => { toast.success("Your choice was saved"); qc.invalidateQueries({ predicate: (x) => String(x.queryKey[0]).startsWith("portal-") }); }, onError: (e: Error) => toast.error(e.message) });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Privacy & consent" sub={q.data.note} right={<Badge tone="outline"><ShieldCheck className="h-3 w-3" />You are in control</Badge>} />
        <CardBody className="space-y-3">{q.data.purposes.map((p: J) => (
          <div key={p.purpose} className="flex items-start justify-between gap-4 rounded-xl border border-line bg-white p-4">
            <div className="min-w-0"><div className="text-sm font-semibold text-slate">{p.label}</div><p className="mt-1 text-sm">{p.description}</p>
              {p.shares.length > 0 && <p className="mt-1 text-xs text-muted"><span className="font-semibold text-slate">What is shared: </span>{p.shares.join(" · ")}</p>}
              <p className="mt-1 text-xs text-muted">{p.not_shared}</p>{p.updated_at && <p className="mt-1 text-[11px] text-muted">Last changed {dateTime(p.updated_at + "Z")}</p>}</div>
            <Switch checked={p.granted} onCheckedChange={(v) => set.mutate({ purpose: p.purpose, granted: v })} label={p.label} />
          </div>))}
          <Disclaimer>The bank&apos;s internal assessments, notes and risk information are never shown in this portal.</Disclaimer></CardBody>
      </Card>
      <Card><CardHeader title="What has happened with your information" /><CardBody>
        {(act.data?.events ?? []).length === 0 ? <p className="text-sm text-muted">Nothing yet.</p> : <ul className="space-y-1.5 text-sm">{act.data.events.map((e: J) => <li key={e.id} className="flex gap-3"><span className="w-32 shrink-0 text-xs text-muted">{dateTime(e.at)}</span><span>{e.summary}</span></li>)}</ul>}
      </CardBody></Card>
    </div>
  );
}

export function Journey({ view, smeId, setView }: { view: JourneyView; smeId: string; setView: (v: string) => void }) {
  const goPrivacy = () => setView("privacy");
  if (view === "privacy") return <PrivacyView smeId={smeId} />;
  if (view === "plan") return <PlanView smeId={smeId} goPrivacy={goPrivacy} />;
  if (view === "growth") return <GrowthView smeId={smeId} goPrivacy={goPrivacy} />;
  return <div className="space-y-4"><SubmissionForm smeId={smeId} /><ProfileCard smeId={smeId} /><ReadinessCard smeId={smeId} goPrivacy={goPrivacy} /></div>;
}
