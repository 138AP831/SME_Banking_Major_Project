import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, BarChart3, Bell, Building2, ClipboardCheck, FileCheck2, Gauge, GitBranch, Landmark, LineChart, LogOut, RotateCcw, Settings, ShieldAlert, ShieldCheck, Sparkles, Target, Users } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { NAV_BY_ROLE, ROLES, useRole, type Role } from "@/lib/role";
import { cn } from "@/lib/utils";
import { Button, Dialog, DialogContent, Select } from "@/components/ui/primitives";
import { timeAgo } from "@/lib/format";

const ITEMS: Record<string, { to: string; label: string; icon: ReactNode }> = {
  overview: { to: "/", label: "Overview", icon: <Gauge /> },
  workflows: { to: "/studio", label: "AI Workflow Studio", icon: <GitBranch /> },
  prospecting: { to: "/prospecting", label: "Prospecting", icon: <Target /> },
  kyc: { to: "/kyc", label: "KYC Onboarding", icon: <FileCheck2 /> },
  credit: { to: "/credit", label: "Credit Intelligence", icon: <Landmark /> },
  cashflow: { to: "/cashflow", label: "Cash-Flow", icon: <LineChart /> },
  ews: { to: "/ews", label: "Early Warning", icon: <ShieldAlert /> },
  portfolio: { to: "/portfolio", label: "SME Portfolio", icon: <Building2 /> },
  reviews: { to: "/reviews", label: "Review Queue", icon: <ClipboardCheck /> },
  models: { to: "/models", label: "Model Performance", icon: <BarChart3 /> },
  governance: { to: "/governance", label: "Audit & Governance", icon: <ShieldCheck /> },
  settings: { to: "/settings", label: "Settings", icon: <Settings /> },
  portal: { to: "/portal", label: "SME Portal", icon: <Users /> },
};

function Logo() {
  return (
    <div className="flex items-center gap-2.5 px-5 py-5">
      <svg viewBox="0 0 32 32" className="h-8 w-8" aria-hidden><rect width="32" height="32" rx="8" fill="#0A9396" /><path d="M7 20c4 0 4-8 9-8s5 8 9 8" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" /><circle cx="25" cy="20" r="2.4" fill="#E9C46A" /></svg>
      <div className="leading-tight"><div className="text-[15px] font-bold text-white">SMEFlow</div><div className="text-[11px] text-white/60">Intelligence</div></div>
    </div>
  );
}

function Notifications() {
  const { role } = useRole();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["notifications", role], queryFn: () => api.get("/notifications"), refetchInterval: 6000, enabled: role !== "SME Customer" });
  const mine = (q.data?.items ?? []).filter((i: any) => i.for_you);
  return (
    <div className="relative">
      <button aria-label={`Notifications (${q.data?.count ?? 0})`} onClick={() => setOpen((o) => !o)} className="relative rounded-lg p-2 text-slate hover:bg-ivory-2 cursor-pointer">
        <Bell className="h-5 w-5" />
        {!!q.data?.count && <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-risk px-1 text-[10px] font-bold text-white">{q.data.count}</span>}
      </button>
      {open && (
        <div className="absolute right-0 z-40 mt-2 w-80 rounded-xl border border-line bg-paper p-2 shadow-xl">
          <p className="px-2 py-1 text-xs font-semibold text-muted">Needs your attention</p>
          {mine.length === 0 && <p className="px-2 py-4 text-sm text-muted">Nothing is waiting for the {role} role.</p>}
          {mine.map((i: any) => (
            <button key={i.id} onClick={() => { setOpen(false); nav(i.type === "review" ? "/reviews" : `/ews`); }} className="block w-full rounded-lg px-2 py-2 text-left hover:bg-ivory-2 cursor-pointer">
              <div className="text-sm font-semibold text-slate">{i.title}</div><div className="text-xs text-muted">{i.detail} · {timeAgo(i.created_at + "Z")}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function AppShell() {
  const { role, setRole, user } = useRole();
  const qc = useQueryClient();
  const nav = useNavigate();
  const loc = useLocation();
  const [resetOpen, setResetOpen] = useState(false);
  const [resetting, setResetting] = useState(false);
  const allowed = NAV_BY_ROLE[role];
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get("/health"), refetchInterval: 15000, retry: 1 });

  useEffect(() => {
    if (role === "SME Customer" && loc.pathname !== "/portal") nav("/portal", { replace: true });
    if (role !== "SME Customer" && loc.pathname === "/portal") nav("/", { replace: true });
  }, [role, loc.pathname, nav]);

  const switchRole = (r: Role) => { setRole(r); qc.clear(); toast(`Viewing as ${r}`); };
  const doReset = async () => {
    setResetting(true);
    try { await api.post("/admin/reset", { with_workflows: true }); qc.clear(); toast.success("Demo data reset and reseeded"); setResetOpen(false); nav("/"); }
    catch (e) { toast.error((e as Error).message); } finally { setResetting(false); }
  };

  return (
    <div className="flex h-full">
      <aside className="hidden w-[248px] shrink-0 flex-col bg-navy md:flex" aria-label="Primary">
        <Logo />
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {allowed.map((k) => (
            <NavLink key={k} to={ITEMS[k].to} end={ITEMS[k].to === "/"}
              className={({ isActive }) => cn("flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-white/75 transition-colors hover:bg-white/8 hover:text-white [&_svg]:h-[18px] [&_svg]:w-[18px]",
                isActive && "bg-teal/90 text-white hover:bg-teal/90")}>{ITEMS[k].icon}{ITEMS[k].label}</NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 px-5 py-4 text-[11px] leading-relaxed text-white/55">
          <Sparkles className="mb-1 h-3.5 w-3.5 text-sand" />
          All data is synthetic. External checks are simulated. Outputs are prototype assessments, not bank decisions.
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex min-w-0 items-center gap-2 border-b border-line bg-paper px-3 py-2.5 sm:gap-3 md:px-6">
          <div className="md:hidden"><svg viewBox="0 0 32 32" className="h-7 w-7"><rect width="32" height="32" rx="8" fill="#003049" /><path d="M7 20c4 0 4-8 9-8s5 8 9 8" fill="none" stroke="#0A9396" strokeWidth="3" strokeLinecap="round" /></svg></div>
          <span className="rounded-md bg-sand px-2 py-1 text-xs font-bold text-navy sm:hidden" title="This build runs on synthetic data only">DEMO</span>
          <span className="hidden rounded-md bg-sand px-2.5 py-1 text-xs font-bold text-navy sm:inline" title="This build runs on synthetic data only">DEMO ENVIRONMENT</span>
          <span className="hidden text-xs text-muted lg:inline">Data as of {health.data?.as_of ?? "…"} · synthetic</span>
          {health.isError && <span className="rounded-md bg-risk-soft px-2 py-1 text-xs font-semibold text-risk">API unreachable</span>}
          <div className="flex-1" />
          <label className="sr-only" htmlFor="role">Role</label>
          <div className="flex min-w-0 items-center gap-2">
            <Select id="role" value={role} onChange={(e) => switchRole(e.target.value as Role)} className="h-8 w-[150px] text-xs font-semibold sm:w-[210px]">
              {ROLES.map((r) => <option key={r}>{r}</option>)}
            </Select>
            <span className="hidden text-xs text-muted sm:inline">{user}</span>
          </div>
          {role !== "SME Customer" && <Notifications />}
          <Button variant="ghost" size="icon" aria-label="Reset demo data" title="Reset demo data" onClick={() => setResetOpen(true)}><RotateCcw className="h-4 w-4" /></Button>
        </header>
        <nav className="flex gap-1 overflow-x-auto border-b border-line bg-paper px-2 py-1 md:hidden" aria-label="Primary mobile">
          {allowed.map((k) => <NavLink key={k} to={ITEMS[k].to} end={ITEMS[k].to === "/"} className={({ isActive }) => cn("whitespace-nowrap rounded-md px-3 py-1.5 text-xs font-semibold", isActive ? "bg-navy text-white" : "text-slate")}>{ITEMS[k].label}</NavLink>)}
        </nav>
        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8"><div className="mx-auto max-w-[1360px]"><Outlet /></div></main>
      </div>

      <Dialog open={resetOpen} onOpenChange={setResetOpen}>
        <DialogContent title="Reset demo data?" description="Drops every record and reseeds the 40 synthetic SMEs, then runs the bootstrap workflows again. Run history, reviews and audit events are cleared.">
          <div className="flex justify-end gap-2"><Button variant="secondary" onClick={() => setResetOpen(false)}>Cancel</Button><Button variant="danger" loading={resetting} onClick={doReset}>Reset and reseed</Button></div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
export { Activity, LogOut };
