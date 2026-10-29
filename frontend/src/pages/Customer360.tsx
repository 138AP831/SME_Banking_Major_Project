import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Eye, EyeOff, FlaskConical, Play } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ForecastChart, MonthlyFlow } from "@/components/charts";
import { Kpi, ScoreGauge } from "@/components/common";
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, ErrorState, NotAvailable, PageHeader, PageSkeleton, SimBadge, StatusBadge } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { dateTime, inr, pct, title } from "@/lib/format";
import { useRole } from "@/lib/role";

export default function Customer360() {
  const { id = "" } = useParams();
  const { role } = useRole();
  const nav = useNavigate();
  const [reveal, setReveal] = useState(false);
  const q = useQuery({ queryKey: ["c360", id, reveal, role], queryFn: () => api.get(`/smes/${id}/360?reveal=${reveal}`) });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data, p = d.profile;
  const canReveal = role === "KYC Analyst" || role === "Credit Analyst";
  return (
    <div>
      <Link to="/portfolio" className="mb-2 inline-flex items-center gap-1 text-xs font-semibold text-muted hover:text-slate"><ArrowLeft className="h-3.5 w-3.5" />Portfolio</Link>
      <PageHeader title={p.name} sub={<>{p.id} · {p.industry} · {p.city}, {p.state} · {p.legal_form} · RM {p.assigned_rm}</>}
        right={<><StatusBadge value={p.onboarding_status} /><StatusBadge value={p.consent_status} label={`Consent ${p.consent_status}`} />
          <Button variant="secondary" size="sm" onClick={() => nav(`/studio`)}><Play className="h-3.5 w-3.5" />Open Workflow Studio</Button></>} />
      {p.persona && <p className="mb-4 -mt-3 text-xs text-muted"><FlaskConical className="mr-1 inline h-3.5 w-3.5" />Demo persona: {p.persona}</p>}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Kpi label="Model score" value={d.credit?.score ?? "Not available"} hint={d.credit ? `${d.credit.risk_category} risk` : "No assessment yet - run a workflow"} />
        <Kpi label="Exposure" value={p.outstanding_exposure ? inr(p.outstanding_exposure) : "None"} hint={p.sanctioned_limit ? `Limit ${inr(p.sanctioned_limit)}` : "No facility"} />
        <Kpi label="Utilisation" value={p.sanctioned_limit ? pct(p.utilization) : "–"} hint={p.utilization_note ? <SimBadge>Simulated history</SimBadge> : undefined} />
        <Kpi label="Balance today" value={inr(p.current_balance)} hint={d.forecast?.shortfall_date ? <span className="text-risk">Shortfall projected {d.forecast.shortfall_date}</span>   : d.forecast ? "No shortfall projected" : "Forecast not available"} tone={d.forecast?.shortfall_date ? "risk" : "default"} />
        <Kpi label="Bureau score" value={p.bureau_score ?? "Not available"} hint={<span>{p.bureau_note}</span>} />
      </div>

      {d.pending_reviews.length > 0 && <div className="mb-4 rounded-xl border border-sand/70 bg-sand-soft/50 p-3 text-sm"><strong>{d.pending_reviews.length} human review pending.</strong> <Link to="/reviews" className="font-semibold text-teal-dark underline">Open the review queue</Link></div>}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Cash flow" sub="Last 12 months of receipts and payments from the synthetic transaction feed" />
          <CardBody><MonthlyFlow data={d.monthly} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Next best actions" />
          <CardBody className="space-y-2">{d.next_best_actions.map((a: J, i: number) => (
            <div key={i} className="rounded-lg border border-line bg-white p-3"><div className="text-sm font-semibold text-slate">{a.label}</div><div className="mt-0.5 text-xs text-muted">{a.reason}</div>
              {a.target && <Link to={`/${a.target}${["kyc", "credit", "cashflow"].includes(a.target) ? `?sme=${p.id}` : ""}`} className="mt-1 inline-block text-xs font-semibold text-teal-dark hover:underline">Open {a.target}</Link>}</div>))}</CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Cash-flow outlook" sub={d.forecast ? (d.forecast.live ? "60-day baseline computed live - no forecast has been saved for this SME yet" : `${d.forecast.horizon_days}-day baseline forecast saved ${dateTime(d.forecast.created_at + "Z")}`) : undefined} />
          <CardBody>{d.forecast ? <ForecastChart series={d.forecast.series} history={d.balance_history.slice(-45)} height={240} /> : <NotAvailable reason="No forecast has been generated for this SME yet. Open Cash-Flow or run a workflow." />}</CardBody>
        </Card>
        <Card>
          <CardHeader title="Credit assessment" right={d.credit && <StatusBadge value={d.credit.policy_outcome} />} />
          <CardBody>
            {!d.credit ? <NotAvailable reason={p.segment === "client" ? "No credit assessment on file. Run the credit workflow." : "Credit assessment applies to clients and onboarding SMEs with a request."} /> : (
              <div className="space-y-2"><div className="flex justify-center"><ScoreGauge score={d.credit.score} /></div>
                <p className="text-xs text-muted">PD {d.credit.pd != null ? pct(d.credit.pd, 1) : "n/a (scorecard fallback)"} · suggested limit {inr(d.credit.limit_low)} – {inr(d.credit.limit_high)}</p>
                <p className="text-sm leading-relaxed text-ink">{d.credit.explanation}</p>
                <Link to={`/credit?sme=${p.id}`} className="text-xs font-semibold text-teal-dark hover:underline">Full assessment and factors</Link></div>)}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="KYC" right={d.kyc && <StatusBadge value={d.kyc.status} />} />
          <CardBody className="text-sm">{!d.kyc ? <p className="text-muted">No KYC application (prospect).</p> : (<div className="space-y-1"><p>{d.kyc.exceptions} open exception(s) · {d.kyc.missing.length} missing document(s)</p>
            <Link to={`/kyc?sme=${p.id}`} className="text-xs font-semibold text-teal-dark hover:underline">Open KYC case</Link></div>)}</CardBody>
        </Card>
        <Card>
          <CardHeader title="Early-warning" right={d.alert && <StatusBadge value={d.alert.priority_level} />} />
          <CardBody className="text-sm">{!d.alert ? <p className="text-muted">No open alert.</p> : (<div className="space-y-1.5"><p className="font-semibold text-slate">Priority {Math.round(d.alert.priority_score)} · {title(d.alert.primary_signal)}</p>
            <p>{d.alert.recommended_action}</p><ul className="list-disc pl-4 text-xs text-muted">{d.alert.talking_points.slice(0, 2).map((t: string, i: number) => <li key={i}>{t}</li>)}</ul></div>)}</CardBody>
        </Card>
        <Card>
          <CardHeader title="Next-best product" right={d.recommendation && <Badge tone={d.recommendation.outreach_allowed ? "teal" : "risk"}>{d.recommendation.outreach_allowed ? "Outreach allowed" : "Outreach blocked"}</Badge>} />
          <CardBody className="text-sm">{!d.recommendation ? <p className="text-muted">No recommendation yet.</p> : (<div className="space-y-1"><p className="font-semibold text-slate">{title(d.recommendation.product)} <span className="num text-muted">· score {Math.round(d.recommendation.propensity_score)}</span></p><p className="text-xs text-muted">{d.recommendation.rationale}</p></div>)}</CardBody>
        </Card>

        <Card>
          <CardHeader title="Identity" right={canReveal ? <Button size="sm" variant="ghost" onClick={() => setReveal((r) => !r)}>{reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}{reveal ? "Mask" : "Reveal"}</Button> : undefined} />
          <CardBody className="space-y-1 text-sm">
            <Row k="PAN" v={<span className="font-mono">{p.pan}</span>} /><Row k="GSTIN" v={<span className="font-mono">{p.gstin}</span>} /><Row k="Director" v={p.director_name} />
            <Row k="Contact" v={p.contact_email} /><Row k="Phone" v={p.contact_phone} />
            {!canReveal && <p className="pt-1 text-xs text-muted">Identifiers are masked for the {role} role. Only KYC and Credit Analysts can reveal them; each reveal is audited.</p>}
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader title="Customers and receipts" sub="Top payers by share of receipts (last 12 months)" />
          <CardBody>{d.top_customers.length === 0 ? <EmptyState title="No receipts on file" /> : d.top_customers.map((c: J) => (
            <div key={c.name} className="mb-2 flex items-center gap-3 text-sm"><div className="w-64 truncate">{c.name}</div><div className="h-2 flex-1 rounded-full bg-ivory-2"><div className="h-2 rounded-full bg-teal" style={{ width: `${c.share * 100}%` }} /></div><div className="num w-12 text-right">{pct(c.share)}</div></div>))}</CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title="Recent audit activity" />
          <CardBody><ul className="divide-y divide-line/60">{d.audit.length === 0 ? <li className="py-2 text-sm text-muted">Nothing recorded yet.</li> : d.audit.map((e: J) => (
            <li key={e.id} className="py-2 text-sm"><span className="text-ink">{e.summary}</span><div className="text-xs text-muted">{dateTime(e.timestamp)} · {e.actor}</div></li>))}</ul></CardBody>
        </Card>
        <Card>
          <CardHeader title="Workflow runs" />
          <CardBody>{d.recent_runs.length === 0 ? <p className="text-sm text-muted">No runs for this SME.</p> : d.recent_runs.map((r: J) => (
            <Link key={r.id} to={`/studio?run=${r.id}`} className="flex items-center justify-between py-1.5 text-sm hover:underline"><span>{title(r.template)}</span><StatusBadge value={r.status} /></Link>))}</CardBody>
        </Card>
      </div>
    </div>
  );
}
const Row = ({ k, v }: { k: string; v: React.ReactNode }) => <div className="flex justify-between gap-3"><span className="text-muted">{k}</span><span className="truncate text-right">{v}</span></div>;
