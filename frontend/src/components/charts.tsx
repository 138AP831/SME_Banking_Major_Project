import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr, shortDate } from "@/lib/format";
import type { J } from "@/lib/api";

export const C = { teal: "#0A9396", navy: "#003049", slate: "#1D3557", sand: "#E9C46A", risk: "#b23a48", warn: "#c7791a", grid: "#e6e2cb", muted: "#667a8c" };
export const RISK_COLORS: Record<string, string> = { Low: C.teal, Moderate: C.sand, Elevated: C.warn, High: C.risk };
const axis = { fontSize: 11, fill: C.muted };

export function RiskBars({ data }: { data: { category: string; count: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke={C.grid} /><XAxis dataKey="category" tick={axis} axisLine={false} tickLine={false} /><YAxis tick={axis} allowDecimals={false} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "#ebe7cf80" }} formatter={(v: number) => [`${v} clients`, "Count"]} />
        <Bar dataKey="count" radius={[6, 6, 0, 0]}>{data.map((d) => <Cell key={d.category} fill={RISK_COLORS[d.category]} />)}</Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function HBars({ data, nameKey, valueKey, color = C.teal, fmt = (v: number) => String(v), height = 220 }: { data: J[]; nameKey: string; valueKey: string; color?: string; fmt?: (v: number) => string; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }}>
        <XAxis type="number" hide /><YAxis type="category" dataKey={nameKey} width={140} tick={{ ...axis, fill: C.slate }} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "#ebe7cf80" }} formatter={(v: number) => [fmt(v), ""]} />
        <Bar dataKey={valueKey} fill={color} radius={[0, 6, 6, 0]} barSize={16} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Forecast with uncertainty band. `series` rows: {date, balance, low, high}. The band is heuristic and labelled as such by callers. */
export function ForecastChart({ series, history, buffer, height = 300, compare }: { series: J[]; history?: J[]; buffer?: number; height?: number; compare?: J[] }) {
  const hist = (history ?? []).map((h) => ({ date: h.date, actual: h.balance }));
  const rows = [...hist, ...series.map((s, i) => ({ date: s.date, balance: s.balance, band: (s.lower ?? s.low) != null ? [s.lower ?? s.low, s.upper ?? s.high] : undefined, scenario: compare?.[i]?.balance }))];
  const zero = Math.min(0, ...series.map((s) => s.lower ?? s.low ?? s.balance));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 10, right: 12, left: 4, bottom: 0 }}>
        <defs><linearGradient id="band" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={C.teal} stopOpacity={0.22} /><stop offset="100%" stopColor={C.teal} stopOpacity={0.08} /></linearGradient></defs>
        <CartesianGrid vertical={false} stroke={C.grid} />
        <XAxis dataKey="date" tick={axis} tickFormatter={shortDate} minTickGap={36} axisLine={false} tickLine={false} />
        <YAxis tick={axis} tickFormatter={(v) => inr(v, { compact: true })} width={76} axisLine={false} tickLine={false} domain={[zero < 0 ? zero * 1.1 : 0, "auto"]} />
        <Tooltip formatter={(v: any, n: string) => (Array.isArray(v) ? [`${inr(v[0])} – ${inr(v[1])}`, "Likely range"] : [inr(v), n === "actual" ? "Actual balance" : n === "scenario" ? "Scenario" : "Forecast balance"])} labelFormatter={(l) => shortDate(String(l))} />
        <Area dataKey="band" stroke="none" fill="url(#band)" isAnimationActive={false} />
        <Line dataKey="actual" stroke={C.slate} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
        <Line dataKey="balance" stroke={C.teal} strokeWidth={2.4} dot={false} isAnimationActive={false} />
        {compare && <Line dataKey="scenario" stroke={C.sand} strokeWidth={2.4} strokeDasharray="5 4" dot={false} isAnimationActive={false} />}
        <ReferenceLine y={0} stroke={C.risk} strokeWidth={1.2} />
        {buffer ? <ReferenceLine y={buffer} stroke={C.warn} strokeDasharray="4 4" label={{ value: "Minimum buffer", fill: C.warn, fontSize: 10, position: "insideTopRight" }} /> : null}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function MonthlyFlow({ data }: { data: J[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }} barGap={2}>
        <CartesianGrid vertical={false} stroke={C.grid} /><XAxis dataKey="month" tick={axis} axisLine={false} tickLine={false} tickFormatter={(m) => m.slice(2)} />
        <YAxis tick={axis} tickFormatter={(v) => inr(v, { compact: true })} width={76} axisLine={false} tickLine={false} />
        <Tooltip formatter={(v: number, n: string) => [inr(v), n === "inflow" ? "Receipts" : "Payments"]} cursor={{ fill: "#ebe7cf80" }} />
        <Bar dataKey="inflow" fill={C.teal} radius={[4, 4, 0, 0]} /><Bar dataKey="outflow" fill={C.slate} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function Sparkline({ data, color = C.teal, height = 44 }: { data: number[]; color?: string; height?: number }) {
  const rows = data.map((v, i) => ({ i, v }));
  return <ResponsiveContainer width="100%" height={height}><AreaChart data={rows}><Area dataKey="v" stroke={color} fill={color} fillOpacity={0.15} strokeWidth={2} dot={false} isAnimationActive={false} /></AreaChart></ResponsiveContainer>;
}
