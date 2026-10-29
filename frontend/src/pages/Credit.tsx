import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ShieldAlert, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ScoreGauge, SmePicker } from "@/components/common";
import { Badge, Button, Card, CardBody, CardHeader, Disclaimer, ErrorState, Input, Label, NotAvailable, PageHeader, PageSkeleton, Select, StatusBadge, Switch } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { inr, pct, title } from "@/lib/format";
import { useRole } from "@/lib/role";

function Factors({ items, tone }: { items: J[]; tone: "pos" | "neg" }) {
  if (!items?.length) return <p className="text-sm text-muted">None identified.</p>;
  const max = Math.max(...items.map((f) => Math.abs(f.impact ?? f.contribution ?? 1)), 0.0001);
  return (
    <ul className="space-y-2.5">{items.map((f, i) => (
      <li key={i}><div className="text-sm text-ink">{f.text}</div>
        <div className="mt-1 h-1.5 rounded-full bg-ivory-2"><div className={`h-1.5 rounded-full ${tone === "pos" ? "bg-teal" : "bg-risk"}`} style={{ width: `${Math.max(8, (Math.abs(f.impact ?? f.contribution ?? 1) / max) * 100)}%` }} /></div></li>))}</ul>
  );
}

export default function Credit() {
  const { role } = useRole();
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const [sme, setSme] = useState(sp.get("sme") ?? "SME-1006");
  useEffect(() => { setSp({ sme }, { replace: true }); }, [sme]); // eslint-disable-line
  const [amount, setAmount] = useState(""), [tenure, setTenure] = useState("24");
  const [preview, setPreview] = useState<J | null>(null);
  const q = useQuery({ queryKey: ["credit", sme], queryFn: () => api.get(`/credit/${sme}`) });
  const model = useQuery({ queryKey: ["credit-model"], queryFn: () => api.get("/settings") });
  useEffect(() => { setPreview(null); setAmount(""); }, [sme]);
  const canAssess = ["Credit Analyst", "Relationship Manager", "Risk Manager"].includes(role);
  const assess = useMutation({
    mutationFn: (persist: boolean) => api.post(`/credit/${sme}/assess?persist=${persist}`, amount ? { requested_amount: +amount, tenure_months: +tenure } : {}),
    onSuccess: (r, persist) => { setPreview(r); if (persist) { toast.success("Assessment saved"); qc.invalidateQueries(); } }, onError: (e: Error) => toast.error(e.message),
  });
  const toggle = useMutation({ mutationFn: (v: boolean) => api.put("/credit/model-availability", { available: v }), onSuccess: () => { qc.invalidateQueries(); toast("Credit model availability changed"); }, onError: (e: Error) => toast.error(e.message) });

  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  const a: J | null = preview ? { ...preview.credit, policy_outcome: preview.policy.outcome, policy_rules: preview.policy.triggered, passed: preview.policy.passed, explanation: preview.narrative, limit_low: preview.credit.limit_low, limit_high: preview.credit.limit_high,
    model_score: preview.credit.model_score, positive_factors: preview.credit.positive_factors, risk_factors: preview.credit.risk_factors, preview: true } : d.assessment;

  return (
    <div>
      <PageHeader title="Credit intelligence" sub="Preliminary assessment: model score and PD, plain-language drivers, then a separate policy engine decides eligible, refer or not eligible."
        right={<SmePicker value={sme} onChange={setSme} filter={(s) => s.segment === "client"} />} />
      <div className="mb-4"><Disclaimer>Prototype model trained on synthetic data. Output is a preliminary assessment, not a bank credit decision.</Disclaimer></div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader title="Request" sub={d.application ? `Application ${d.application.id}` : "No open application - assessment uses an illustrative request"} right={d.application && <StatusBadge value={d.application.status} />} />
          <CardBody className="space-y-3">
            {d.application && <p className="text-sm">{d.application.purpose}: <strong>{inr(d.application.requested_amount)}</strong> over {d.application.tenure_months} months</p>}
            <div className="flex items-center justify-between rounded-lg bg-ivory-2/60 px-3 py-2 text-sm"><span>KYC status</span><span className="flex items-center gap-2"><StatusBadge value={d.kyc.status} /><Link to={`/kyc?sme=${sme}`} className="text-xs font-semibold text-teal-dark hover:underline">Open</Link></span></div>
            <div className="grid grid-cols-2 gap-2"><div><Label>What-if amount (₹)</Label><Input type="number" min={100000} step={50000} placeholder={d.application ? String(d.application.requested_amount) : "e.g. 3000000"} value={amount} onChange={(e) => setAmount(e.target.value)} /></div>
              <div><Label>Tenure</Label><Select value={tenure} onChange={(e) => setTenure(e.target.value)}>{[12, 18, 24, 36, 48, 60].map((t) => <option key={t} value={t}>{t} months</option>)}</Select></div></div>
            <div className="flex gap-2"><Button disabled={!canAssess} loading={assess.isPending} onClick={() => assess.mutate(false)}>Preview assessment</Button>
              <Button variant="secondary" disabled={!canAssess || !preview} onClick={() => assess.mutate(true)}>Save assessment</Button></div>
            {!canAssess && <p className="text-xs text-muted">The {role} role can view but not run assessments.</p>}
            <p className="text-xs text-muted">A saved assessment that needs a human decision appears in the Review Queue. For the full pipeline with a live pause, use the Credit assessment template in the Workflow Studio.</p>
            {["Risk Manager", "Credit Analyst"].includes(role) && (
              <div className="flex items-center justify-between rounded-lg border border-dashed border-sand p-3 text-sm"><div><div className="font-semibold text-slate">Credit model available</div><div className="text-xs text-muted">Turn off to test the deterministic scorecard fallback (no PD shown).</div></div>
                <Switch label="Credit model available" checked={model.data?.credit_model_available ?? d.model_available} onCheckedChange={(v) => toggle.mutate(v)} /></div>)}
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader title={a ? (a.preview ? "Assessment preview (not saved)" : "Latest assessment") : "Assessment"} right={a && <StatusBadge value={a.policy_outcome} />} />
          <CardBody>
            {!a ? <NotAvailable reason="No assessment on file for this SME. Preview one on the left or run the credit workflow." /> : (
              <div className="grid gap-5 md:grid-cols-[170px_1fr]">
                <div className="flex flex-col items-center gap-2"><ScoreGauge score={a.model_score} /><StatusBadge value={a.risk_category} label={`${a.risk_category} risk`} />
                  <div className="text-center text-xs text-muted">{a.pd_estimate != null ? <>Estimated PD <strong className="num text-slate">{pct(a.pd_estimate, 1)}</strong><div>synthetic-trained, uncalibrated</div></> : <>PD not available<div>scorecard fallback in use</div></>}</div></div>
                <div className="space-y-3"><p className="text-sm leading-relaxed">{a.explanation}</p>
                  <div className="flex flex-wrap gap-2 text-xs"><Badge tone="neutral">Suggested limit {inr(a.limit_low)} – {inr(a.limit_high)}</Badge><Badge tone="outline">{a.explanation_type === "shap" ? "SHAP explanation" : "Rule-based explanation"}</Badge><Badge tone="outline">{a.model_version}</Badge></div></div>
              </div>)}
          </CardBody>
        </Card>

        {a && (<>
          <Card><CardHeader title="What supports the score" /><CardBody><Factors items={a.positive_factors} tone="pos" /></CardBody></Card>
          <Card><CardHeader title="What raises risk" /><CardBody><Factors items={a.risk_factors} tone="neg" /></CardBody></Card>
          <Card>
            <CardHeader title="Policy rules" sub="Configurable demo policy, separate from the model" />
            <CardBody className="space-y-2 text-sm">
              {(a.policy_rules ?? []).length === 0 && <p className="flex items-center gap-2 text-teal-dark"><CheckCircle2 className="h-4 w-4" />No rule triggered.</p>}
              {(a.policy_rules ?? []).map((r: J) => (
                <div key={r.rule_id + r.message} className="flex gap-2">{r.outcome === "not_eligible" ? <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-risk" /> : <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[#b8860b]" />}
                  <div><span className="font-semibold text-slate">{r.rule_id}</span> <span className="text-ink">{r.message}</span></div></div>))}
              {(a.passed ?? []).map((r: J) => <div key={r.rule_id} className="flex gap-2 text-muted"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-teal" /><div><span className="font-semibold">{r.rule_id}</span> {r.message}</div></div>)}
            </CardBody>
          </Card>
        </>)}
        {d.history.length > 1 && <Card className="lg:col-span-3"><CardHeader title="Assessment history" /><CardBody className="flex flex-wrap gap-2">{d.history.map((h: J) => <Badge key={h.id} tone="neutral">{h.created_at.slice(0, 16).replace("T", " ")} · {h.model_score} · {title(h.policy_outcome)}</Badge>)}</CardBody></Card>}
      </div>
    </div>
  );
}
