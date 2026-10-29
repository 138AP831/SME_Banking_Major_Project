import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Badge, Button, Card, CardBody, CardHeader, Disclaimer, ErrorState, Input, Label, PageHeader, PageSkeleton, Table, Td, Th } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { title } from "@/lib/format";
import { useRole } from "@/lib/role";

export default function SettingsPage() {
  const { role } = useRole();
  const qc = useQueryClient();
  const s = useQuery({ queryKey: ["settings"], queryFn: () => api.get("/settings") });
  const pol = useQuery({ queryKey: ["policy"], queryFn: () => api.get("/governance/policy") });
  const [delay, setDelay] = useState(450);
  const [draft, setDraft] = useState<Record<string, string>>({});
  useEffect(() => { if (s.data) setDelay(s.data.demo_step_delay_ms); }, [s.data]);
  const saveDelay = useMutation({ mutationFn: () => api.put("/settings", { demo_step_delay_ms: delay }), onSuccess: () => { toast.success("Step delay updated"); qc.invalidateQueries({ queryKey: ["settings"] }); }, onError: (e: Error) => toast.error(e.message) });
  const savePolicy = useMutation({ mutationFn: () => api.put("/governance/policy", { policy: Object.fromEntries(Object.entries(draft).map(([k, v]) => [k, +v])) }),
    onSuccess: (r) => { toast.success(`${Object.keys(r.changed).length} threshold(s) updated and audited`); setDraft({}); qc.invalidateQueries(); }, onError: (e: Error) => toast.error(e.message) });
  if (s.isLoading || pol.isLoading) return <PageSkeleton />;
  if (s.isError) return <ErrorState error={s.error} />;
  if (pol.isError) return <ErrorState error={pol.error} />;
  const p = pol.data.policy as Record<string, number | string>;
  const canEdit = role === "Risk Manager";
  return (
    <div>
      <PageHeader title="Settings" sub="Demo behaviour, model and LLM configuration, and the policy thresholds used by the rules engine." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader title="Workflow pacing" sub="Delay between steps so a live run is easy to follow. Events are still real; 0 runs at full speed." />
          <CardBody className="space-y-3"><div className="flex items-end gap-3"><div><Label>Step delay (ms)</Label><Input type="number" min={0} max={3000} step={50} value={delay} onChange={(e) => setDelay(+e.target.value)} className="w-[140px]" /></div><Button onClick={() => saveDelay.mutate()} loading={saveDelay.isPending}>Apply</Button></div></CardBody></Card>
        <Card><CardHeader title="Environment" />
          <CardBody className="space-y-2 text-sm">
            <div className="flex justify-between"><span className="text-muted">Data as of</span><span>{s.data.as_of_date}</span></div>
            <div className="flex justify-between"><span className="text-muted">Data</span><Badge tone="sand">Synthetic</Badge></div>
            <div className="flex justify-between"><span className="text-muted">Optional LLM</span>{s.data.llm.enabled ? <Badge tone="teal">{s.data.llm.provider}</Badge> : <Badge tone="outline">Off - deterministic explanations</Badge>}</div>
            <div className="flex justify-between"><span className="text-muted">Credit model</span><Badge tone={s.data.credit_model_available ? "teal" : "warn"}>{s.data.credit_model_available ? "Available" : "Disabled (fallback test)"}</Badge></div>
            <p className="text-xs text-muted">{s.data.llm.note} The app works fully without an API key.</p></CardBody></Card>
        <Card className="lg:col-span-2"><CardHeader title="Policy thresholds" sub="Separate from the models. Only the Risk Manager role can change them; each change is audited." />
          <CardBody>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(p).filter(([k]) => k !== "version").map(([k, v]) => (
              <div key={k}><Label hint={draft[k] !== undefined && String(draft[k]) !== String(v) ? `(was ${v})` : undefined}>{title(k)}</Label>
                <Input type="number" step="any" disabled={!canEdit} value={draft[k] ?? String(v)} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} /></div>))}</div>
            <div className="mt-4 flex items-center gap-3"><Button disabled={!canEdit || Object.keys(draft).length === 0} loading={savePolicy.isPending} onClick={() => savePolicy.mutate()}>Save thresholds</Button>
              {!canEdit && <span className="text-xs text-muted">Switch to the Risk Manager role to edit thresholds.</span>}</div>
          </CardBody></Card>
        <Card className="lg:col-span-2"><CardHeader title="Rule catalogue" /><CardBody>
          <Table><thead><tr><Th>Rule</Th><Th>What it checks</Th><Th>Routes to review</Th></tr></thead><tbody>{pol.data.rules.map((r: J) => (
            <tr key={r.rule_id}><Td className="font-mono text-xs">{r.rule_id}</Td><Td>{r.title}</Td><Td>{r.review_kind ? <Badge tone="sand">{title(r.review_kind)} ({pol.data.allowed_roles[r.review_kind].join(", ")})</Badge> : <Badge tone="outline">No review</Badge>}</Td></tr>))}</tbody></Table></CardBody></Card>
        <div className="lg:col-span-2"><Disclaimer>Outcomes of this demo policy engine are not bank credit decisions. {s.data.fault_injection}</Disclaimer></div>
      </div>
    </div>
  );
}
