import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/Shell";
import { PageSkeleton } from "@/components/ui/primitives";

const Overview = lazy(() => import("@/pages/Overview"));
const Studio = lazy(() => import("@/pages/Studio"));
const Prospecting = lazy(() => import("@/pages/Prospecting"));
const Kyc = lazy(() => import("@/pages/Kyc"));
const Credit = lazy(() => import("@/pages/Credit"));
const CashFlow = lazy(() => import("@/pages/CashFlow"));
const Ews = lazy(() => import("@/pages/Ews"));
const Portfolio = lazy(() => import("@/pages/Portfolio"));
const Customer360 = lazy(() => import("@/pages/Customer360"));
const Reviews = lazy(() => import("@/pages/Reviews"));
const Models = lazy(() => import("@/pages/Models"));
const Governance = lazy(() => import("@/pages/Governance"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));
const Portal = lazy(() => import("@/pages/Portal"));

export default function App() {
  return (
    <Suspense fallback={<div className="p-8"><PageSkeleton /></div>}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Overview />} />
          <Route path="studio" element={<Studio />} />
          <Route path="prospecting" element={<Prospecting />} />
          <Route path="kyc" element={<Kyc />} />
          <Route path="credit" element={<Credit />} />
          <Route path="cashflow" element={<CashFlow />} />
          <Route path="ews" element={<Ews />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="portfolio/:id" element={<Customer360 />} />
          <Route path="reviews" element={<Reviews />} />
          <Route path="models" element={<Models />} />
          <Route path="governance" element={<Governance />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="portal" element={<Portal />} />
          <Route path="*" element={<div className="p-10 text-center text-muted">Page not found.</div>} />
        </Route>
      </Routes>
    </Suspense>
  );
}
