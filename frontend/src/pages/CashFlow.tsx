import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ForecastChart, HBars } from "@/components/charts";
import { Delta, Kpi, SmePicker } from "@/components/common";
import { Badge, Button, Card, CardBody, CardHeader, Disclaimer, ErrorState, Label, NotAvailable, PageHeader, PageSkeleton, Segmented } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { inr, shortDate, title } from "@/lib/format";

const ZERO = { receipt_delay_days: 0, revenue_decline_pct: 0, expense_increase_pct: 0, additional_financing: 0, financing_day: 7, one_time_expense: 0, one_time_day: 14 };

function Lever({ label, value, min, max, step, onChange, fmt }: { label: string; value: number; min: number; max: number; step: number; onChange: (v: number) => void; fmt: (v: number) => string }) {
  return (
    <div><div className="flex justify-between text-xs"><Label className="mb-0">{label}</Label><span className="num font-semibold text-slate">{fmt(value)}</span></div>
      <input aria-label={label} type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} className="w-full accent-[#0A9396]" /></div>
  );
}

export default function CashFlow() {
  const qc = useQueryClient();
  const [sp, setSp] = useSearchParams();
  const [sme, setSme] = useState(sp.get("sme") ?? "SME-1006");
  const [horizon, setHorizon] = useState<"30" | "60" | "90">("60");
  const [sc, setSc] = useState(ZERO);
  const dirty = JSON.stringify(sc) !== JSON.stringify(ZERO);
  useEffect(() => { setSp({ sme }, { replace: true }); setSc(ZERO); }, [sme]); // eslint-disable-line
  const base = useQuery({ queryKey: ["cashflow", sme, horizon], queryFn: () => api.get(`/cashflow/${sme}?horizon=${horizon}`) });
  const bt = useQuery({ queryKey: ["cashflow-bt", sme], queryFn: () => api.get(`/cashflow/${sme}/backtest`) });
  const [debounced, setDebounced] = useState(sc);
  useEffect(() => { const t = setTimeout(() => setDebounced(sc), 250); return () => clearTimeout(t); }, [sc]);
  const scen = useQuery({ queryKey: ["cashflow-sc", sme, horizon, debounced], queryFn: () => api.post(`/cashflow/${sme}/scenario`, { horizon: +horizon, scenario: debounced }), enabled: dirty });
  const save = useMutation({ mutationFn: () => api.post(`/cashflow/${sme}/save`, { horizon: +horizon, scenario: dirty ? sc : null }), onSuccess: (r) => { toast.success(r.is_baseline ? "Baseline forecast saved" : "Scenario forecast saved"); qc.invalidateQueries(); }, onError: (e: Error) => toast.error(e.message) });

  const f = base.data?.forecast;
  const cmp = dirty ? scen.data?.scenario : null;
  const s = f?.summary, ss = cmp?.summary;
  const outflow = useMemo(() => Object.entries((f?.outflow_by_category ?? {}) as Record<string, number>).map(([k, v]) => ({ name: title(k), value: v })).sort((a, b) => b.value - a.value).slice(0, 7), [f]);

  return (
    <div>
      <PageHeader title="Cash-flow forecast" sub="A transparent baseline (weekday profile plus detected recurring obligations) with what-if levers. The band is a heuristic range, not a statistical guarantee."
        right={<><SmePicker value={sme} onChange={setSme} /><Segmented value={horizon} onChange={setHorizon} options={[{ value: "30", label: "30 days" }, { value: "60", label: "60 days" }, { value: "90", label: "90 days" }]} /></>} />
      {base.isLoading ? <PageSkeleton /> : base.isError ? <ErrorState error={base.error} retry={() => base.refetch()} /> : !base.data.available ? <NotAvailable reason={base.data.reason} /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Kpi label="Balance today" value={inr(f.history?.length ? f.history[f.history.length - 1].balance : base.data.sme.current_balance)} />
            <Kpi label={`Expected in ${horizon} days`} value={inr(s.expected_ending_balance)} hint={ss && <Delta v={ss.expected_ending_balance - s.expected_ending_balance} fmt={(n) => inr(n)} />} />
            <Kpi label="Lowest expected balance" value={inr(s.min_balance)} hint={s.low_balance_date ? `on ${shortDate(s.low_balance_date)}` : undefined} />
            <Kpi label="Shortfall" tone={s.days_to_shortfall != null ? "risk" : "teal"} value={s.days_to_shortfall != null ? `in ${s.days_to_shortfall} days` : "None projected"} hint={s.shortfall_date ? `${shortDate(s.shortfall_date)} · overdraw ${inr(s.shortfall_amount)}` : undefined} />
            <Kpi label="Scenario shortfall" value={!dirty ? "–" : ss ? (ss.days_to_shortfall != null ? `in ${ss.days_to_shortfall} days` : "None") : "…"} tone={ss?.days_to_shortfall != null ? "risk" : "default"} hint={dirty ? "with levers applied" : "move a lever to compare"} />
          </div>
          <div className="grid gap-4 xl:grid-cols-[1fr_330px]">
            <Card>
              <CardHeader title="Projected balance" sub={`Solid teal: baseline. Dashed sand: scenario. Shaded: heuristic range. Red line: zero balance.`}
                right={<div className="flex gap-2"><Button size="sm" variant="secondary" onClick={() => save.mutate()} loading={save.isPending}>{dirty ? "Save scenario" : "Save baseline"}</Button></div>} />
              <CardBody><ForecastChart series={f.series} history={f.history} buffer={f.buffer_threshold} compare={cmp?.series} height={340} />
                <p className="mt-2 text-xs text-muted">{f.uncertainty?.note ?? String(f.uncertainty ?? "")}</p></CardBody>
            </Card>
            <Card>
              <CardHeader title="What-if levers" right={dirty && <Button size="sm" variant="ghost" onClick={() => setSc(ZERO)}>Reset</Button>} />
              <CardBody className="space-y-3.5">
                <Lever label="Customer payments delayed by" value={sc.receipt_delay_days} min={0} max={45} step={1} onChange={(v) => setSc({ ...sc, receipt_delay_days: v })} fmt={(v) => `${v} days`} />
                <Lever label="Revenue decline" value={sc.revenue_decline_pct} min={0} max={50} step={1} onChange={(v) => setSc({ ...sc, revenue_decline_pct: v })} fmt={(v) => `${v}%`} />
                <Lever label="Expense increase" value={sc.expense_increase_pct} min={0} max={40} step={1} onChange={(v) => setSc({ ...sc, expense_increase_pct: v })} fmt={(v) => `${v}%`} />
                <Lever label="Additional financing" value={sc.additional_financing} min={0} max={10_000_000} step={250_000} onChange={(v) => setSc({ ...sc, additional_financing: v })} fmt={(v) => inr(v)} />
                {sc.additional_financing > 0 && <Lever label="Financing arrives on day" value={sc.financing_day} min={1} max={+horizon} step={1} onChange={(v) => setSc({ ...sc, financing_day: v })} fmt={(v) => `day ${v}`} />}
                <Lever label="One-time expense" value={sc.one_time_expense} min={0} max={5_000_000} step={100_000} onChange={(v) => setSc({ ...sc, one_time_expense: v })} fmt={(v) => inr(v)} />
                {sc.one_time_expense > 0 && <Lever label="Expense falls on day" value={sc.one_time_day} min={1} max={+horizon} step={1} onChange={(v) => setSc({ ...sc, one_time_day: v })} fmt={(v) => `day ${v}`} />}
                {scen.isError && <p className="text-xs text-risk">{(scen.error as Error).message}</p>}
              </CardBody>
            </Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <Card><CardHeader title="Recommended action" /><CardBody className="space-y-2 text-sm"><p className="font-semibold text-slate">{(cmp ?? f).recommended_action}</p>
              <ul className="list-disc space-y-1 pl-4 text-muted">{(f.factors ?? []).slice(0, 4).map((x: J, i: number) => <li key={i}>{typeof x === "string" ? x : x.text}</li>)}</ul></CardBody></Card>
            <Card><CardHeader title="Where the money goes" sub="Forecast outflows by category" /><CardBody><HBars data={outflow} nameKey="name" valueKey="value" fmt={(v) => inr(v)} height={220} color="#1D3557" /></CardBody></Card>
            <Card><CardHeader title="How reliable is this?" sub="30-day hold-out back-test" />
              <CardBody className="space-y-2 text-sm">{bt.isLoading ? <p className="text-muted">Running back-test…</p> : !bt.data?.available ? <NotAvailable reason={bt.data?.reason ?? "Back-test unavailable."} /> : (<>
                <p>Mean absolute balance error: <strong className="num">{inr(bt.data.mae_balance)}</strong> ({bt.data.mae_pct_of_monthly_outflow?.toFixed(1)}% of monthly outflow).</p>
                <p>Low-balance event in hold-out: actual <Badge tone={bt.data.actual_low ? "risk" : "teal"}>{bt.data.actual_low ? "yes" : "no"}</Badge> · predicted <Badge tone={bt.data.predicted_low ? "risk" : "teal"}>{bt.data.predicted_low ? "yes" : "no"}</Badge></p>
                <p className="text-xs text-muted">A baseline cannot anticipate shocks that start inside the window.</p></>)}
                <details className="text-xs"><summary className="cursor-pointer font-semibold text-muted">Assumptions</summary><ul className="mt-1 list-disc space-y-1 pl-4 text-muted">{(f.assumptions ?? []).map((a: string, i: number) => <li key={i}>{a}</li>)}</ul></details>
              </CardBody></Card>
          </div>
          <Disclaimer>Forecasts come from synthetic transactions and a transparent statistical baseline. Obligations (GST, rent, payroll, EMI) are detected from history, not read from a ledger.</Disclaimer>
        </div>)}
    </div>
  );
}
