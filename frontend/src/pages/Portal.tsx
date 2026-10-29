import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Circle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ForecastChart } from "@/components/charts";
import { Badge, Button, Card, CardBody, CardHeader, Disclaimer, ErrorState, Input, Label, PageHeader, PageSkeleton, Select } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { inr } from "@/lib/format";

export default function Portal() {
  const qc = useQueryClient();
  const [sme, setSme] = useState("");
  const companies = useQuery({ queryKey: ["portal-companies"], queryFn: () => api.get("/portal/companies") });
  const q = useQuery({ queryKey: ["portal", sme], queryFn: () => api.get(`/portal/me${sme ? `?sme_id=${sme}` : ""}`) });
  const [amount, setAmount] = useState(""), [purpose, setPurpose] = useState("");
  const submit = useMutation({ mutationFn: () => api.post(`/portal/credit-request${q.data ? `?sme_id=${q.data.company.id}` : ""}`, { amount: +amount, purpose }), onSuccess: (r) => { toast.success(r.message); setAmount(""); setPurpose(""); qc.invalidateQueries({ queryKey: ["portal"] }); }, onError: (e: Error) => toast.error(e.message) });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader title={`Welcome, ${d.company.name}`} sub="Your accounts, onboarding progress and requests in one place." right={
        <Select aria-label="Company" className="w-[260px]" value={sme || d.company.id} onChange={(e) => setSme(e.target.value)}>{(companies.data ?? []).map((c: J) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select>} />
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <Card className="p-4"><div className="text-xs font-semibold text-muted">Current balance</div><div className="num mt-1 text-2xl font-bold text-slate">{inr(d.balance)}</div></Card>
        <Card className="p-4"><div className="text-xs font-semibold text-muted">Sanctioned limit</div><div className="num mt-1 text-2xl font-bold text-slate">{d.limit ? inr(d.limit) : "None"}</div></Card>
        <Card className="p-4"><div className="text-xs font-semibold text-muted">Your relationship manager</div><div className="mt-1 text-lg font-bold text-slate">{d.company.rm}</div></Card>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {d.onboarding && (
          <Card><CardHeader title="Account opening" right={<Badge tone={d.onboarding.status === "Complete" ? "teal" : "sand"}>{d.onboarding.status}</Badge>} />
            <CardBody className="space-y-2">{d.onboarding.required.map((r: J) => <div key={r.label} className="flex items-center gap-2 text-sm">{r.uploaded ? <CheckCircle2 className="h-4 w-4 text-teal" /> : <Circle className="h-4 w-4 text-muted" />}{r.label}</div>)}
              {d.onboarding.requests.map((m: string, i: number) => <p key={i} className="rounded-lg bg-sand-soft/60 p-2.5 text-sm">{m}</p>)}
              <p className="text-xs text-muted">Please send any requested documents to your relationship manager.</p></CardBody></Card>)}
        {d.credit_application && (
          <Card><CardHeader title="Your credit request" right={<Badge tone="sand">{d.credit_application.status}</Badge>} /><CardBody className="text-sm"><p>{d.credit_application.purpose}</p><p className="num mt-1 text-lg font-bold text-slate">{inr(d.credit_application.amount)}</p></CardBody></Card>)}
        {d.cash_flow && (
          <Card className="md:col-span-2"><CardHeader title="Your cash outlook" sub={d.cash_flow.message} /><CardBody><ForecastChart series={d.cash_flow.series} height={220} /></CardBody></Card>)}
        <Card className="md:col-span-2"><CardHeader title="Request financing" sub="Tell us how much you need. This is a request, not an approval." />
          <CardBody><div className="grid gap-3 sm:grid-cols-[180px_1fr_auto] sm:items-end"><div><Label>Amount (₹)</Label><Input type="number" min={100000} step={50000} value={amount} onChange={(e) => setAmount(e.target.value)} /></div>
            <div><Label>What for?</Label><Input value={purpose} onChange={(e) => setPurpose(e.target.value)} placeholder="e.g. Raw material purchase" /></div>
            <Button disabled={!amount} loading={submit.isPending} onClick={() => submit.mutate()}>Send request</Button></div></CardBody></Card>
      </div>
      <div className="mt-4"><Disclaimer>{d.note}</Disclaimer></div>
    </div>
  );
}
