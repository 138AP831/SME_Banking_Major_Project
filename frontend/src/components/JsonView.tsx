import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

function Leaf({ v }: { v: unknown }) {
  if (v === null) return <span className="text-muted">null</span>;
  if (typeof v === "string") return <span className="text-teal-dark">"{v}"</span>;
  if (typeof v === "number") return <span className="num text-[#9a5b00]">{v}</span>;
  if (typeof v === "boolean") return <span className="text-risk">{String(v)}</span>;
  return <span>{String(v)}</span>;
}

function Node({ k, v, depth, open0 }: { k?: string; v: unknown; depth: number; open0: boolean }) {
  const [open, setOpen] = useState(open0);
  const isObj = v !== null && typeof v === "object";
  if (!isObj) return <div className="py-px pl-4">{k !== undefined && <span className="text-slate">{k}: </span>}<Leaf v={v} /></div>;
  const entries = Array.isArray(v) ? v.map((x, i) => [String(i), x] as const) : Object.entries(v as object);
  return (
    <div className="py-px">
      <button className="flex items-center gap-0.5 text-left cursor-pointer" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <ChevronRight className={cn("h-3.5 w-3.5 text-muted transition-transform", open && "rotate-90")} />
        {k !== undefined && <span className="text-slate">{k}: </span>}
        <span className="text-muted">{Array.isArray(v) ? `[${entries.length}]` : `{${entries.length}}`}</span>
      </button>
      {open && <div className="ml-2 border-l border-line pl-2">{entries.map(([ek, ev]) => <Node key={ek} k={ek} v={ev} depth={depth + 1} open0={depth < 0} />)}</div>}
    </div>
  );
}

export function JsonView({ data, empty = "No data recorded for this step." }: { data: unknown; empty?: string }) {
  if (data === null || data === undefined || (typeof data === "object" && Object.keys(data as object).length === 0)) return <p className="text-sm text-muted">{empty}</p>;
  return <div className="max-h-[340px] overflow-auto rounded-lg bg-white p-3 font-mono text-[12px] leading-relaxed">
    {typeof data === "object" ? Object.entries(data as object).map(([k, v]) => <Node key={k} k={k} v={v} depth={0} open0={false} />) : <Leaf v={data} />}
  </div>;
}
