import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Phone } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { Badge, Button, Card, CardBody, CardHeader, Dialog, DialogContent, Disclaimer, EmptyState, ErrorState, Input, Label, PageHeader, PageSkeleton, Select, SimBadge, Table, Td, Th } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { inr, title } from "@/lib/format";
import { useRole } from "@/lib/role";

export default function Prospecting() {
  const { role } = useRole();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["leads"], queryFn: () => api.get("/prospecting/leads") });
  const [segment, setSegment] = useState(""), [product, setProduct] = useState(""), [consent, setConsent] = useState(""), [min, setMin] = useState(0);
  const [open, setOpen] = useState<J | null>(null);
  const rows = useMemo(() => (q.data?.leads ?? []).filter((l: J) => (!segment || l.segment === segment) && (!product || l.product === product) && (!consent || l.consent_status === consent) && l.propensity_score >= min), [q.data, segment, product, consent, min]);
  const outreach = useMutation({
    mutationFn: (l: J) => api.post(`/prospecting/${l.id}/outreach`, { action: l.outreach_action }),
    onSuccess: (r) => { toast.success(r.note); qc.invalidateQueries(); }, onError: (e: Error) => toast.error(e.message),
  });
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  return (
    <div>
      <PageHeader title="Prospecting and next-best product" sub="Ranks existing clients and prospects by suitability for each product. Outreach is blocked when marketing consent is not granted." />
      <div className="mb-4 grid gap-2 md:grid-cols-2"><Disclaimer>{q.data.model_note}</Disclaimer><Disclaimer>{q.data.data_note} Prospect rows come from simulated account-aggregator data.</Disclaimer></div>
      <Card className="mb-4 p-3"><div className="flex flex-wrap items-end gap-3">
        <div><Label>Segment</Label><Select value={segment} onChange={(e) => setSegment(e.target.value)} className="w-[140px]"><option value="">All</option><option value="client">Clients</option><option value="prospect">Prospects</option></Select></div>
        <div><Label>Product</Label><Select value={product} onChange={(e) => setProduct(e.target.value)} className="w-[220px]"><option value="">All products</option>{q.data.products.map((p: J) => <option key={p.id} value={p.id}>{p.label}</option>)}</Select></div>
        <div><Label>Consent</Label><Select value={consent} onChange={(e) => setConsent(e.target.value)} className="w-[140px]"><option value="">Any</option><option value="granted">Granted</option><option value="pending">Pending</option><option value="withdrawn">Withdrawn</option></Select></div>
        <div className="w-[200px]"><Label hint={`${min}+`}>Minimum score</Label><input type="range" min={0} max={90} step={5} value={min} onChange={(e) => setMin(+e.target.value)} className="w-full accent-[#0A9396]" aria-label="Minimum propensity score" /></div>
        <span className="ml-auto text-xs text-muted">{rows.length} leads</span>
      </div></Card>
      <Card>{rows.length === 0 ? <div className="p-6"><EmptyState title="No leads match these filters" /></div> : (
        <Table><thead><tr><Th>Business</Th><Th>Best-fit product</Th><Th className="text-right">Score</Th><Th className="text-right">Est. value</Th><Th>Consent</Th><Th>Data</Th><Th /></tr></thead>
          <tbody>{rows.map((l: J) => (
            <tr key={l.id} className="hover:bg-ivory-2/50 cursor-pointer" onClick={() => setOpen(l)}>
              <Td><div className="font-semibold text-slate">{l.name}</div><div className="text-xs text-muted">{l.industry} · {l.city}</div></Td>
              <Td>{l.product_label}</Td>
              <Td className="text-right"><span className="num inline-block min-w-9 rounded-md bg-teal-soft px-2 py-0.5 text-sm font-bold text-teal-dark">{Math.round(l.propensity_score)}</span></Td>
              <Td className="num text-right">{inr(l.est_value)}</Td>
              <Td>{l.outreach_allowed ? <Badge tone="teal">Granted</Badge> : <Badge tone="risk"><Ban className="h-3 w-3" />{title(l.consent_status)}</Badge>}</Td>
              <Td>{l.segment === "prospect" ? <SimBadge>Aggregator (simulated)</SimBadge> : <Badge tone="outline">Bank data</Badge>}</Td>
              <Td><Button size="sm" variant="ghost">Details</Button></Td></tr>))}</tbody></Table>)}
      </Card>
      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        {open && (
          <DialogContent side title={open.name} description={`${open.industry} · ${open.city} · ${open.segment}`}>
            <div className="space-y-4">
              <div className="rounded-xl bg-teal-soft/60 p-4"><div className="text-xs font-semibold text-teal-dark">Recommended</div><div className="text-lg font-bold text-slate">{open.product_label}</div><p className="mt-1 text-sm">{open.rationale}</p>
                <p className="mt-2 text-xs text-muted">Estimated opportunity {inr(open.est_value)} · ranking score {Math.round(open.propensity_score)}/100 (uncalibrated)</p></div>
              <div><h4 className="mb-2 text-sm font-semibold">Why this product</h4><ul className="list-disc space-y-1 pl-5 text-sm">{open.indicators.map((i: string, k: number) => <li key={k}>{i}</li>)}</ul></div>
              <div><h4 className="mb-2 text-sm font-semibold">Score by product</h4>{Object.entries(open.scores as Record<string, number>).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
                <div key={k} className="mb-1.5 flex items-center gap-3 text-sm"><div className="w-48 truncate">{title(k)}</div><div className="h-2 flex-1 rounded-full bg-ivory-2"><div className="h-2 rounded-full bg-teal" style={{ width: `${v}%` }} /></div><div className="num w-8 text-right">{Math.round(v)}</div></div>))}</div>
              {open.outreach_allowed ? (
                <div className="rounded-xl border border-line bg-white p-3"><div className="text-sm font-semibold text-slate">Suggested outreach</div><p className="text-sm">{open.outreach_action}</p>
                  <div className="mt-2 flex gap-2"><Button size="sm" disabled={role !== "Relationship Manager"} loading={outreach.isPending} onClick={() => outreach.mutate(open)}><Phone className="h-3.5 w-3.5" />Plan outreach</Button>
                    <Button size="sm" variant="secondary" asChild><Link to={`/portfolio/${open.id}`}>Customer 360</Link></Button></div>
                  {role !== "Relationship Manager" && <p className="mt-2 text-xs text-muted">Only a Relationship Manager can plan outreach. Recorded in the audit trail only - no message is sent.</p>}</div>
              ) : <div className="rounded-xl border border-risk/30 bg-risk-soft/50 p-3 text-sm"><strong className="text-risk">Outreach blocked.</strong> Marketing / data-use consent is “{open.consent_status}”. Request consent through the proper channel before contacting this business.</div>}
            </div>
          </DialogContent>)}
      </Dialog>
    </div>
  );
}
