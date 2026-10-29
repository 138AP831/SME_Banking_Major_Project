import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export const ROLES = ["Relationship Manager", "Credit Analyst", "KYC Analyst", "Risk Manager", "SME Customer"] as const;
export type Role = (typeof ROLES)[number];
const USERS: Record<Role, string> = {
  "Relationship Manager": "Alex Morgan", "Credit Analyst": "Dev Krishnan", "KYC Analyst": "Isha Rao", "Risk Manager": "Marcus Fernandes", "SME Customer": "SME portal user",
};
export const NAV_BY_ROLE: Record<Role, string[]> = {
  "Relationship Manager": ["overview", "workflows", "prospecting", "ews", "portfolio", "kyc", "credit", "cashflow", "reviews", "models", "governance", "settings"],
  "Credit Analyst": ["overview", "workflows", "credit", "cashflow", "portfolio", "kyc", "reviews", "models", "governance", "settings"],
  "KYC Analyst": ["overview", "workflows", "kyc", "portfolio", "reviews", "governance", "settings"],
  "Risk Manager": ["overview", "workflows", "ews", "credit", "cashflow", "portfolio", "reviews", "models", "governance", "settings"],
  "SME Customer": ["portal"],
};

let current: Role = (localStorage.getItem("smeflow.role") as Role) || "Relationship Manager";
export const currentRole = () => current;
export const currentUser = () => USERS[current];

type Ctx = { role: Role; user: string; setRole: (r: Role) => void };
const RoleCtx = createContext<Ctx>({ role: current, user: USERS[current], setRole: () => {} });

export function RoleProvider({ children }: { children: ReactNode }) {
  const [role, setRoleState] = useState<Role>(current);
  useEffect(() => { current = role; try { localStorage.setItem("smeflow.role", role); } catch { /* ignore */ } }, [role]);
  const value = useMemo(() => ({ role, user: USERS[role], setRole: (r: Role) => { current = r; setRoleState(r); } }), [role]);
  return <RoleCtx.Provider value={value}>{children}</RoleCtx.Provider>;
}
export const useRole = () => useContext(RoleCtx);
