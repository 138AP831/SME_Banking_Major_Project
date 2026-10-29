import { useQuery } from "@tanstack/react-query";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import { api, type J } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Select } from "@/components/ui/primitives";

export function Kpi({ label, value, hint, tone = "default", icon }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "default" | "risk" | "teal" | "sand"; icon?: ReactNode }) {
  return (
    <div className={cn("rounded-[14px] border bg-paper p-4", tone === "risk" ? "border-risk/30" : "border-line")}>
      <div className="flex items-center justify-between text-xs font-semibold text-muted">{label}{icon}</div>
      <div className={cn("num mt-1.5 text-[28px] font-bold leading-none", tone === "risk" ? "text-risk" : tone === "teal" ? "text-teal-dark" : "text-slate")}>{value}</div>
      {hint && <div className="mt-2 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function SmePicker({ value, onChange, filter, className }: { value: string; onChange: (id: string) => void; filter?: (s: J) => boolean; className?: string }) {
  const q = useQuery({ queryKey: ["smes", "all"], queryFn: () => api.get("/smes") });
  const list: J[] = (q.data?.smes ?? []).filter(filter ?? (() => true));
  return (
    <Select aria-label="Select SME" value={value} onChange={(e) => onChange(e.target.value)} className={cn("w-[300px] max-w-full", className)}>
      {list.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.name}</option>)}
    </Select>
  );
}

export function Delta({ v, fmt }: { v: number; fmt: (n: number) => string }) {
  if (!v) return <span className="text-muted">no change</span>;
  const up = v > 0;
  return <span className={cn("inline-flex items-center gap-0.5 font-semibold", up ? "text-teal-dark" : "text-risk")}>{up ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}{fmt(Math.abs(v))}</span>;
}

export function ScoreGauge({ score, size = 140 }: { score: number | null | undefined; size?: number }) {
  if (score === null || score === undefined) return <div className="text-sm text-muted">Not available</div>;
  const min = 300, max = 900, p = Math.min(1, Math.max(0, (score - min) / (max - min)));
  const r = size / 2 - 12, c = Math.PI * r, color = p > 0.72 ? "#0A9396" : p > 0.5 ? "#E9C46A" : p > 0.33 ? "#c7791a" : "#b23a48";
  return (
    <div className="relative" style={{ width: size, height: size / 2 + 18 }} role="img" aria-label={`Model score ${score} on a 300 to 900 scale`}>
      <svg width={size} height={size / 2 + 12} viewBox={`0 0 ${size} ${size / 2 + 12}`}>
        <path d={`M12 ${size / 2} A ${r} ${r} 0 0 1 ${size - 12} ${size / 2}`} fill="none" stroke="#ebe7cf" strokeWidth="12" strokeLinecap="round" />
        <path d={`M12 ${size / 2} A ${r} ${r} 0 0 1 ${size - 12} ${size / 2}`} fill="none" stroke={color} strokeWidth="12" strokeLinecap="round" strokeDasharray={`${c * p} ${c}`} />
      </svg>
      <div className="absolute inset-x-0 bottom-0 text-center"><div className="num text-3xl font-bold leading-none text-slate">{score}</div><div className="mt-0.5 text-[11px] text-muted">300 – 900</div></div>
    </div>
  );
}
