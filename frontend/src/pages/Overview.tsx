import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Landmark, Radar, ShieldAlert, Target, UserCheck } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { HBars, RiskBars } from "@/components/charts";
import { Kpi } from "@/components/common";
import { Badge, Card, CardBody, CardHeader, EmptyState, ErrorState, PageHeader, PageSkeleton, StatusBadge } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { inr, num, shortDate, timeAgo, title } from "@/lib/format";
import { cn } from "@/lib/utils";

const ICON: Record<string, JSX.Element> = { workflow: <Radar />, kyc: <UserCheck />, credit: <Landmark />, forecast: <AlertTriangle />, alert: <ShieldAlert />, prospecting: <Target />, review: <UserCheck />, system: <Radar /> };

export default function Overview() {
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api.get("/overview"), refetchInterval: 8000 });
  const nav = useNavigate();
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  const fmtKpi = (k: J) => (k.value === null ? "Not available" : k.unit === "inr" ? inr(k.value) : num(k.value));
  const stages = [["Discover", "prospecting", `${d.top_leads.length} ranked leads`], ["Onboard", "kyc", `${d.kyc_pipeline.reduce((a: number, b: J) => a + (["approved"].includes(b.status) ? 0 : b.count), 0)} open KYC cases`],
    ["Assess credit", "credit", `${d.risk_distribution.reduce((a: number, b: J) => a + b.count, 0)} assessed`], ["Forecast cash flow", "cashflow", `${d.shortfalls.length} projected shortfalls`],
    ["Monitor risk", "ews", `${d.alerts_by_level.reduce((a: number, b: J) => a + b.count, 0)} open alerts`], ["Retain & grow", "portfolio", `${d.kpis[0].value} active clients`]];

  return (
    <div>
      <PageHeader title="Portfolio overview" sub={<>Where attention is needed across the SME lifecycle today. <Badge tone="sand">Synthetic demonstration data</Badge></>} />

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {d.kpis.map((k: J, i: number) => <Kpi key={k.key} label={k.label} value={fmtKpi(k)} hint={k.hint} tone={k.key === "alerts" || k.key === "shortfalls" ? (k.value > 0 ? "risk" : "default") : k.key === "reviews" && k.value > 0 ? "teal" : "default"} />)}
      </div>

      <Card className="mb-5 overflow-hidden">
        <div className="grid grid-cols-2 divide-x divide-line md:grid-cols-6">
          {stages.map(([label, to, hint], i) => (
            <button key={label} onClick={() => nav(`/${to}`)} className="group relative px-4 py-3.5 text-left hover:bg-ivory-2/60 cursor-pointer">
              <div className="flex items-center justify-between text-sm font-semibold text-slate">{label}<ArrowRight className="h-3.5 w-3.5 text-muted transition-transform group-hover:translate-x-0.5" /></div>
              <div className="mt-0.5 text-xs text-muted">{hint}</div>
              <div className="absolute inset-x-0 bottom-0 h-0.5 bg-teal/70" style={{ opacity: 0.25 + i * 0.12 }} />
            </button>))}
        </div>
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Needs attention" sub="Highest-priority early-warning alerts (priority = risk + exposure + urgency + actionability)" right={<Link to="/ews" className="text-xs font-semibold text-teal-dark hover:underline">All alerts</Link>} />
          <CardBody>
            {d.top_alerts.length === 0 ? <EmptyState title="No open alerts" hint="Run the Cash-flow & early warning workflow or scan the portfolio from Early Warning." /> : (
              <ul className="divide-y divide-line/70">
                {d.top_alerts.map((a: J) => (
                  <li key={a.id}><Link to={`/portfolio/${a.sme_id}`} className="flex items-center gap-3 py-2.5 hover:bg-ivory-2/50">
                    <div className={cn("grid h-9 w-9 place-items-center rounded-lg text-sm font-bold num", a.priority_level === "critical" ? "bg-risk-soft text-risk" : "bg-warn-soft text-warn")}>{Math.round(a.priority_score)}</div>
                    <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold text-slate">{a.sme_name}</div><div className="text-xs text-muted">{title(a.primary_signal)} · exposure {inr(a.exposure)}{a.days_to_stress != null ? ` · stress in ~${a.days_to_stress} d` : ""}</div></div>
                    <StatusBadge value={a.priority_level} /></Link></li>))}
              </ul>)}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Waiting for a person" sub="Workflows paused at a human review gate" />
          <CardBody>
            {d.pending_reviews.length === 0 ? <EmptyState title="Queue is clear" hint="Reviews appear here when a workflow needs a KYC, credit or risk decision." /> : (
              <ul className="space-y-2">{d.pending_reviews.map((r: J) => (
                <li key={r.id}><Link to="/reviews" className="flex items-center justify-between rounded-lg border border-sand/60 bg-sand-soft/40 px-3 py-2 hover:bg-sand-soft/70">
                  <div><div className="text-sm font-semibold text-slate">{r.sme_name}</div><div className="text-xs text-muted">{title(r.kind)}</div></div><ArrowRight className="h-4 w-4 text-muted" /></Link></li>))}</ul>)}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Credit risk mix" sub="Active clients by model risk category" />
          <CardBody><RiskBars data={d.risk_distribution} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Exposure by industry" sub="Outstanding exposure, active clients" />
          <CardBody><HBars data={d.exposure_by_industry.slice(0, 6)} nameKey="industry" valueKey="exposure" fmt={(v) => inr(v)} /></CardBody>
        </Card>
        <Card>
          <CardHeader title="Projected cash shortfalls" sub="Next 60 days, from latest baseline forecasts" right={<Link to="/cashflow" className="text-xs font-semibold text-teal-dark hover:underline">Cash-flow</Link>} />
          <CardBody>
            {d.shortfalls.length === 0 ? <EmptyState title="No shortfalls projected" /> : (<ul className="space-y-2">{d.shortfalls.map((s: J) => (
              <li key={s.sme_id}><Link to={`/cashflow?sme=${s.sme_id}`} className="flex items-center justify-between text-sm hover:underline"><span className="truncate font-semibold text-slate">{s.sme_name}</span>
                <span className="num shrink-0 text-risk">{inr(s.amount)} · {shortDate(s.shortfall_date)} (in {s.days} d)</span></Link></li>))}</ul>)}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Top new-business leads" sub="Consented prospects ranked by propensity (uncalibrated model score)" right={<Link to="/prospecting" className="text-xs font-semibold text-teal-dark hover:underline">Prospecting</Link>} />
          <CardBody>
            {d.top_leads.length === 0 ? <EmptyState title="No consented prospects" /> : (<ul className="space-y-2.5">{d.top_leads.map((l: J) => (
              <li key={l.sme_id} className="flex items-center justify-between gap-3"><div className="min-w-0"><div className="truncate text-sm font-semibold text-slate">{l.sme_name}</div><div className="text-xs text-muted">{title(l.product)} · {inr(l.est_value)}</div></div>
                <div className="num rounded-md bg-teal-soft px-2 py-1 text-sm font-bold text-teal-dark">{Math.round(l.propensity_score)}</div></li>))}</ul>)}
          </CardBody>
        </Card>

        <Card>
          <CardHeader title="Onboarding pipeline" sub="KYC cases by status" right={<Link to="/kyc" className="text-xs font-semibold text-teal-dark hover:underline">KYC</Link>} />
          <CardBody className="space-y-2">
            {d.kyc_pipeline.map((s: J) => (
              <div key={s.status} className="flex items-center gap-3"><div className="w-36 text-sm text-slate">{title(s.status)}</div>
                <div className="h-2 flex-1 rounded-full bg-ivory-2"><div className="h-2 rounded-full bg-teal" style={{ width: `${Math.min(100, s.count * 28)}%` }} /></div><div className="num w-5 text-right text-sm font-semibold">{s.count}</div></div>))}
          </CardBody>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader title="Recent AI activity" sub="Real events written by workflows and models to the audit trail" right={<Link to="/governance" className="text-xs font-semibold text-teal-dark hover:underline">Audit trail</Link>} />
          <CardBody>
            {d.ai_activity.length === 0 ? <EmptyState title="No AI activity yet" hint="Run a workflow in the AI Workflow Studio." /> : (
              <ul className="grid gap-x-8 gap-y-1 md:grid-cols-2">{d.ai_activity.map((e: J) => (
                <li key={e.id} className="flex items-start gap-2.5 py-1.5 text-sm"><span className="mt-0.5 text-teal [&_svg]:h-4 [&_svg]:w-4">{ICON[e.category] ?? ICON.system}</span>
                  <span className="min-w-0 flex-1"><span className="block text-ink">{e.summary}</span><span className="text-xs text-muted">{timeAgo(e.timestamp)}</span></span></li>))}</ul>)}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
