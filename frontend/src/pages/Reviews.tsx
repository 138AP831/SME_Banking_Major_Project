import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ReviewPanel } from "@/components/ReviewPanel";
import { Badge, EmptyState, ErrorState, PageHeader, PageSkeleton, Segmented } from "@/components/ui/primitives";
import { api, type J } from "@/lib/api";
import { dateTime, inr } from "@/lib/format";

export default function Reviews() {
  const [status, setStatus] = useState<"pending" | "decided">("pending");
  const q = useQuery({ queryKey: ["reviews", status], queryFn: () => api.get(`/reviews?status=${status}`), refetchInterval: 5000 });
  return (
    <div>
      <PageHeader title="Review queue" sub="Every KYC exception, credit referral and critical escalation waits here for a person. Decisions need a reason and resume the paused workflow." right={<Segmented value={status} onChange={setStatus} options={[{ value: "pending", label: "Pending" }, { value: "decided", label: "Decided" }]} />} />
      {q.isLoading ? <PageSkeleton /> : q.isError ? <ErrorState error={q.error} retry={() => q.refetch()} /> : q.data.length === 0 ? (
        <EmptyState title={status === "pending" ? "Nothing is waiting for review" : "No decisions recorded yet"} hint={status === "pending" ? "Run the KYC exception scenario in the AI Workflow Studio to create a review item." : undefined} />
      ) : (
        <div className="space-y-4">{q.data.map((r: J) => (
          <div key={r.id}>
            <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted"><Link to={`/portfolio/${r.sme_id}`} className="font-semibold text-slate hover:underline">{r.sme_name}</Link><span>exposure {inr(r.exposure)}</span><span>raised {dateTime(r.created_at + "Z")}</span>
              {r.run_id && <Link to={`/studio?run=${r.run_id}`} className="font-semibold text-teal-dark hover:underline">Open workflow run</Link>}{!r.run_id && <Badge tone="outline">Raised from a direct assessment</Badge>}</div>
            <ReviewPanel review={r} />
          </div>))}</div>)}
    </div>
  );
}
