import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, CheckCircle2, Eye, EyeOff, FileText, FileUp, Info, Pencil, ShieldQuestion, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ReviewPanel } from "@/components/ReviewPanel";
import { Badge, Button, Card, CardBody, CardHeader, Dialog, DialogContent, Disclaimer, EmptyState, ErrorState, Input, Label, PageHeader, PageSkeleton, Select, SimBadge, StatusBadge } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { dateTime, pct, title } from "@/lib/format";
import { useRole } from "@/lib/role";
import { cn } from "@/lib/utils";

const CORRECTABLE: Record<string, string[]> = { PAN: ["pan", "name"], AADHAAR: ["name", "aadhaar_last4"], GST: ["gstin", "legal_name", "trade_name"], INCORPORATION: ["registration_number", "company_name"], BANK_STATEMENT: ["account_holder", "account_number", "ifsc"], UTILITY_BILL: ["consumer_name", "address"], ITR: ["pan", "name"] };

function Detail({ appId }: { appId: string }) {
  const { role } = useRole();
  const qc = useQueryClient();
  const [reveal, setReveal] = useState(false);
  const [edit, setEdit] = useState<J | null>(null), [vals, setVals] = useState<Record<string, string>>({});
  const [docType, setDocType] = useState("");
  const file = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const q = useQuery({ queryKey: ["kyc-app", appId, reveal, role], queryFn: () => api.get(`/kyc/applications/${appId}?reveal=${reveal}`) });
  const samples = useQuery({ queryKey: ["kyc-samples", q.data?.sme_id], queryFn: () => api.get(`/kyc/samples/${q.data.sme_id}`), enabled: !!q.data?.sme_id });
  const inval = () => { qc.invalidateQueries({ queryKey: ["kyc-app"] }); qc.invalidateQueries({ queryKey: ["kyc-list"] }); qc.invalidateQueries({ queryKey: ["reviews"] }); };
  const upload = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); if (docType) fd.append("doc_type", docType); return api.upload(`/kyc/applications/${appId}/documents`, fd); },
    onSuccess: (r) => { toast.success(`Processed as ${title(r.doc_type)}: ${r.status}`); inval(); }, onError: (e: Error) => toast.error(e.message),
  });
  const loadSample = useMutation({ mutationFn: (key: string) => api.post(`/kyc/applications/${appId}/load-sample`, { key }), onSuccess: () => { toast.success("Sample document processed"); inval(); }, onError: (e: Error) => toast.error(e.message) });
  const correct = useMutation({ mutationFn: () => api.patch(`/kyc/documents/${edit!.id}`, { corrections: Object.fromEntries(Object.entries(vals).filter(([, x]) => x)) }), onSuccess: () => { toast.success("Correction saved and case re-evaluated"); setEdit(null); inval(); }, onError: (e: Error) => toast.error(e.message) });
  const evaluate = useMutation({ mutationFn: () => api.post(`/kyc/applications/${appId}/evaluate`), onSuccess: () => { toast.success("Re-evaluated"); inval(); }, onError: (e: Error) => toast.error(e.message) });
  const start = useMutation({ mutationFn: () => api.post("/workflows/runs", { template: "kyc_onboarding", sme_id: q.data.sme_id }), onSuccess: (r) => { toast.success("KYC workflow started"); window.location.assign(`/studio?run=${r.run_id}`); }, onError: (e: Error) => toast.error(e.message) });

  if (q.isLoading) return <PageSkeleton />;
  if (q.isError) return <ErrorState error={q.error} retry={() => q.refetch()} />;
  const d = q.data;
  const canEdit = role === "KYC Analyst" && !["approved", "rejected"].includes(d.status);
  const canUpload = (role === "KYC Analyst" || role === "Relationship Manager") && !["approved", "rejected"].includes(d.status);
  const canReveal = role === "KYC Analyst" || role === "Credit Analyst";
  const onFiles = (fs: FileList | null) => { if (fs?.[0]) upload.mutate(fs[0]); };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2"><h2 className="text-xl font-bold text-slate">{d.sme_name}</h2><StatusBadge value={d.status} /><span className="text-sm text-muted">{d.industry}</span>
        <div className="ml-auto flex gap-2">
          {canReveal && <Button size="sm" variant="ghost" onClick={() => setReveal((r) => !r)}>{reveal ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}{reveal ? "Mask identifiers" : "Reveal identifiers"}</Button>}
          <Button size="sm" variant="secondary" onClick={() => evaluate.mutate()} loading={evaluate.isPending}>Re-evaluate</Button>
          <Button size="sm" onClick={() => start.mutate()} loading={start.isPending}>Run KYC workflow</Button></div></div>
      {d.pending_review && d.pending_review.run_id && <div className="rounded-xl border border-sand/70 bg-sand-soft/50 p-3 text-sm">A workflow is paused on this case. <Link to="/reviews" className="font-semibold text-teal-dark underline">Decide in the review queue</Link> or <Link to={`/studio?run=${d.pending_review.run_id}`} className="font-semibold text-teal-dark underline">watch the run</Link>.</div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader title="Required documents" />
          <CardBody className="space-y-1.5">{d.required_documents.map((r: J) => (
            <div key={r.type} className="flex items-center gap-2 text-sm">{r.present ? <CheckCircle2 className="h-4 w-4 text-teal" /> : <AlertOctagon className="h-4 w-4 text-warn" />}<span className={r.present ? "" : "font-semibold text-slate"}>{r.label}</span>{!r.present && <Badge tone="warn" className="ml-auto">Missing</Badge>}</div>))}
            {canUpload && (
              <div className="pt-3">
                <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); onFiles(e.dataTransfer.files); }}
                  className={cn("rounded-xl border-2 border-dashed p-4 text-center", drag ? "border-teal bg-teal-soft/40" : "border-line")}>
                  <FileUp className="mx-auto mb-1 h-6 w-6 text-muted" />
                  <p className="text-sm font-semibold text-slate">Drop a document here</p><p className="text-xs text-muted">PDF, PNG or JPG up to 8 MB{!d.ocr_available && ". Scanned images need OCR, which is not installed on this server"}</p>
                  <div className="mx-auto mt-2 flex max-w-xs gap-2"><Select aria-label="Document type" value={docType} onChange={(e) => setDocType(e.target.value)} className="h-8 text-xs"><option value="">Auto-detect type</option>{d.doc_types.map((t: J) => <option key={t.type} value={t.type}>{t.label}</option>)}</Select>
                    <Button size="sm" variant="secondary" loading={upload.isPending} onClick={() => file.current?.click()}><Upload className="h-3.5 w-3.5" />Browse</Button></div>
                  <input ref={file} type="file" accept=".pdf,.png,.jpg,.jpeg" hidden onChange={(e) => { onFiles(e.target.files); e.target.value = ""; }} data-testid="kyc-file" />
                </div>
                {samples.data && (
                  <details className="mt-3 text-xs"><summary className="cursor-pointer font-semibold text-muted">Use a synthetic sample document</summary>
                    <p className="my-1 text-muted">{samples.data.banner}</p>
                    <ul className="space-y-1">{samples.data.samples.map((s: J) => (
                      <li key={s.key} className="flex items-center justify-between gap-2"><span className="truncate">{s.label}</span>
                        <span className="flex shrink-0 gap-1"><a className="rounded px-2 py-1 font-semibold text-teal-dark hover:bg-ivory-2" href={`/api/kyc/samples/download/${encodeURIComponent(s.key)}`}>Download</a>
                          <button className="cursor-pointer rounded px-2 py-1 font-semibold text-teal-dark hover:bg-ivory-2" onClick={() => loadSample.mutate(s.key)}>Process</button></span></li>))}</ul></details>)}
              </div>)}
          </CardBody>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader title="Issues for the analyst" sub={d.exceptions.length ? `${d.exceptions.length} open` : "No open issues"} />
          <CardBody>{d.exceptions.length === 0 ? <p className="flex items-center gap-2 text-sm text-teal-dark"><CheckCircle2 className="h-4 w-4" />All checks passed on the documents received.</p> : (
            <ul className="space-y-2">{d.exceptions.map((e: J, i: number) => (
              <li key={i} className={cn("rounded-lg border p-3 text-sm", e.severity === "blocking" ? "border-risk/30 bg-risk-soft/40" : "border-warn/30 bg-warn-soft/40")}>
                <div className="flex items-center gap-2"><Badge tone={e.severity === "blocking" ? "risk" : "warn"}>{e.severity === "blocking" ? "Blocking" : "Needs attention"}</Badge><span className="font-mono text-xs text-muted">{e.code}</span></div>
                <p className="mt-1">{e.message}</p></li>))}</ul>)}
          </CardBody>
        </Card>

        <Card className="xl:col-span-3">
          <CardHeader title="Documents" sub="Extracted with rule-based text parsing. Sensitive values are masked unless revealed by an authorised role." />
          <CardBody className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{d.documents.length === 0 ? <div className="md:col-span-3"><EmptyState title="No documents yet" hint="Upload a document or process a synthetic sample to start the case." /></div> : d.documents.map((doc: J) => (
            <div key={doc.id} className="rounded-xl border border-line bg-white p-3">
              <div className="flex items-start justify-between gap-2"><div className="flex items-center gap-2"><FileText className="h-4 w-4 text-teal" /><div><div className="text-sm font-semibold text-slate">{doc.label}</div><div className="max-w-[180px] truncate text-xs text-muted">{doc.filename}</div></div></div>
                <div className="text-right"><StatusBadge value={doc.status} /><div className="num mt-1 text-xs text-muted" title="Extraction confidence">{pct(doc.confidence)} · {doc.method.replace(/_/g, " ")}</div></div></div>
              {doc.error && <p className="mt-2 text-xs text-risk">{doc.error}</p>}
              <dl className="mt-2 space-y-1 text-xs">{Object.entries(doc.fields as Record<string, J>).filter(([, f]) => f.value).slice(0, 7).map(([k, f]) => (
                <div key={k} className="flex justify-between gap-2"><dt className="text-muted">{title(k)}</dt><dd className={cn("truncate text-right", f.valid === false && "font-semibold text-risk", f.confidence < 0.75 && "text-warn")} title={`confidence ${pct(f.confidence)}`}>{String(f.value)}{f.corrected && <Badge tone="teal" className="ml-1">edited</Badge>}</dd></div>))}</dl>
              {doc.validation.filter((v: J) => !v.passed).map((v: J, i: number) => <p key={i} className="mt-1.5 flex gap-1 text-xs text-risk"><AlertOctagon className="mt-0.5 h-3 w-3 shrink-0" />{v.message}</p>)}
              {canEdit && <Button size="sm" variant="ghost" className="mt-2" onClick={() => { setEdit(doc); setVals({}); }}><Pencil className="h-3.5 w-3.5" />Correct fields</Button>}
            </div>))}</CardBody>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader title="Cross-document checks" sub="Names, identifiers, dates and addresses compared across documents" />
          <CardBody>{d.cross_checks.length === 0 ? <p className="text-sm text-muted">Upload at least two documents to compare them.</p> : (
            <ul className="space-y-1.5 text-sm">{d.cross_checks.map((c: J, i: number) => (
              <li key={i} className="flex items-start gap-2">{c.passed ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-teal" /> : <AlertOctagon className={cn("mt-0.5 h-4 w-4 shrink-0", c.severity === "blocking" ? "text-risk" : "text-warn")} />}<span className={c.passed ? "text-muted" : ""}>{c.message}</span></li>))}</ul>)}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="External verification" right={<SimBadge>Simulated</SimBadge>} />
          <CardBody className="space-y-2 text-sm">
            <div className="flex items-start gap-2 rounded-lg bg-sand-soft/50 p-3 text-[#6b5210]"><ShieldQuestion className="mt-0.5 h-4 w-4 shrink-0" /><span>{d.external_verification.notice ?? "No registry (PAN, GST, MCA, UIDAI) checks are performed in this prototype."}</span></div>
            {(d.external_verification.checks ?? []).map((c: J) => <div key={c.name} className="flex justify-between"><span>{c.name}</span><Badge tone="outline" title={c.label}>Not performed</Badge></div>)}
          </CardBody>
        </Card>
        <Card className="xl:col-span-3">
          <CardHeader title="Case timeline" />
          <CardBody><ol className="space-y-2 border-l border-line pl-4">{[...d.timeline].reverse().map((t: J, i: number) => (
            <li key={i} className="relative text-sm"><span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-teal" /><span className="text-ink">{t.detail}</span><div className="text-xs text-muted">{dateTime(t.ts + "Z")} · {t.actor}</div></li>))}</ol></CardBody>
        </Card>
      </div>

      <Dialog open={!!edit} onOpenChange={(o) => !o && setEdit(null)}>
        {edit && (
          <DialogContent title={`Correct ${edit.label}`} description="Corrections are validated with the same format rules, saved with an audit entry, and trigger re-evaluation of the case.">
            <div className="space-y-3">{(CORRECTABLE[edit.doc_type] ?? []).map((f) => (
              <div key={f}><Label hint={f === "aadhaar_last4" ? "(enter 12 digits or the last 4; only the last 4 are stored)" : undefined}>{title(f)}</Label>
                <Input placeholder={String(edit.fields[f]?.value ?? "")} value={vals[f] ?? ""} onChange={(e) => setVals({ ...vals, [f]: e.target.value })} /></div>))}
              <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setEdit(null)}>Cancel</Button><Button disabled={!Object.values(vals).some(Boolean)} loading={correct.isPending}
                onClick={() => correct.mutate()}>Save correction</Button></div></div>
          </DialogContent>)}
      </Dialog>
    </div>
  );
}

export default function Kyc() {
  const [sp, setSp] = useSearchParams();
  const list = useQuery({ queryKey: ["kyc-list"], queryFn: () => api.get("/kyc/applications"), refetchInterval: 6000 });
  const sme = sp.get("sme");
  const selected = list.data?.find((a: J) => a.sme_id === sme) ?? null;
  useEffect(() => { if (!sme && list.data?.length) { const first = list.data.find((a: J) => a.status !== "approved") ?? list.data[0]; setSp({ sme: first.sme_id }, { replace: true }); } }, [sme, list.data]); // eslint-disable-line
  if (list.isLoading) return <PageSkeleton />;
  if (list.isError) return <ErrorState error={list.error} retry={() => list.refetch()} />;
  return (
    <div>
      <PageHeader title="KYC onboarding" sub="Upload documents, see what was extracted and how confident it is, resolve mismatches, and route exceptions to an analyst." />
      <div className="mb-4"><Disclaimer>Aadhaar numbers are never stored; only the last four digits are kept. Registry verification (PAN, GST, MCA, UIDAI) is simulated and labelled as such.</Disclaimer></div>
      <div className="grid gap-4 lg:grid-cols-[290px_1fr]">
        <Card className="h-fit">
          <CardHeader title="Cases" sub={`${list.data.length} applications`} />
          <CardBody className="space-y-1 pt-0">{[...list.data].sort((x: J, y: J) => Number(x.status === "approved") - Number(y.status === "approved")).map((a: J) => (
            <button key={a.id} onClick={() => setSp({ sme: a.sme_id })} className={cn("w-full cursor-pointer rounded-lg px-3 py-2 text-left hover:bg-ivory-2", a.sme_id === sme && "bg-ivory-2 ring-1 ring-teal/40")}>
              <div className="flex items-center justify-between gap-2"><span className="truncate text-sm font-semibold text-slate">{a.sme_name}</span><StatusBadge value={a.status} /></div>
              <div className="mt-0.5 text-xs text-muted">{a.documents} docs · {a.missing} missing · {a.exceptions} issue(s)</div></button>))}</CardBody>
        </Card>
        {selected ? <Detail appId={selected.id} /> : <EmptyState icon={<Info className="h-7 w-7" />} title="Select a case" />}
      </div>
    </div>
  );
}
export { ReviewPanel };
