import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, EmptyState, ErrorState, Input, PageHeader, PageSkeleton, Select, StatusBadge, Table, Td, Th } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { inr, pct } from "@/lib/format";

export default function Portfolio() {
  const q = useQuery({ queryKey: ["smes", "all"], queryFn: () => api.get("/smes") });
  const [text, setText] = useState(""), [segment, setSegment] = useState(""), [industry, setIndustry] = useState(""), [rm, setRm] = useState("");
  const rows = useMemo(() => (q.data?.smes ?? []).filter((s: J) => (!text || `${s.name} ${s.id}`.toLowerCase().includes(text.toLowerCase())) && (!segment || (segment === "onboarding" ? s.onboarding_status === "onboarding" : s.segment === segment && s.onboarding_status !== "onboarding"))
    && (!industry || s.industry === industry) && (!rm || s.assigned_rm === rm)), [q.data, text, segment, industry, rm]);
  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  return (
    <div>
      <PageHeader title="SME portfolio" sub="One record per SME. Open a customer to see KYC, credit, cash-flow, alerts and next-best actions on a shared data backbone." />
      <Card className="mb-4 p-3">
        <div className="flex flex-wrap gap-2">
          <div className="relative w-[260px]"><Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted" /><Input aria-label="Search SMEs" className="pl-8" placeholder="Search name or ID" value={text} onChange={(e) => setText(e.target.value)} /></div>
          <Select aria-label="Segment" className="w-[150px]" value={segment} onChange={(e) => setSegment(e.target.value)}><option value="">All segments</option><option value="client">Clients</option><option value="onboarding">Onboarding</option><option value="prospect">Prospects</option></Select>
          <Select aria-label="Industry" className="w-[200px]" value={industry} onChange={(e) => setIndustry(e.target.value)}><option value="">All industries</option>{q.data.industries.map((i: string) => <option key={i}>{i}</option>)}</Select>
          <Select aria-label="Relationship manager" className="w-[170px]" value={rm} onChange={(e) => setRm(e.target.value)}><option value="">All RMs</option>{q.data.rms.map((i: string) => <option key={i}>{i}</option>)}</Select>
          <span className="ml-auto self-center text-xs text-muted">{rows.length} of {q.data.smes.length}</span>
        </div>
      </Card>
      <Card>
        {rows.length === 0 ? <div className="p-6"><EmptyState title="No SMEs match these filters" /></div> : (
          <Table>
            <thead><tr><Th>SME</Th><Th>Industry</Th><Th>Stage</Th><Th className="text-right">Exposure</Th><Th className="text-right">Utilisation</Th><Th className="text-right">Model score</Th><Th>Alert</Th><Th>RM</Th></tr></thead>
            <tbody>{rows.map((s: J) => (
              <tr key={s.id} className="hover:bg-ivory-2/50">
                <Td><Link to={`/portfolio/${s.id}`} className="font-semibold text-slate hover:underline">{s.name}</Link><div className="text-xs text-muted">{s.id} · {s.city}</div></Td>
                <Td>{s.industry}</Td><Td><StatusBadge value={s.onboarding_status} /></Td>
                <Td className="num text-right">{s.outstanding_exposure ? inr(s.outstanding_exposure) : "–"}</Td><Td className="num text-right">{s.sanctioned_limit ? pct(s.utilization) : "–"}</Td>
                <Td className="num text-right">{s.score ?? <span className="text-muted">–</span>}{s.risk_category && <div className="text-xs text-muted">{s.risk_category}</div>}</Td>
                <Td>{s.alert_level ? <StatusBadge value={s.alert_level} /> : <Badge tone="outline">None</Badge>}</Td><Td className="text-muted">{s.assigned_rm}</Td>
              </tr>))}</tbody>
          </Table>)}
      </Card>
    </div>
  );
}
