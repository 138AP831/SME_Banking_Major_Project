import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";

/** Starts the customer readiness workflow (and, after it, the improvement workflow) and follows it until it completes. */
export function useReadinessRun(smeId: string) {
  const qc = useQueryClient();
  const [runId, setRunId] = useState<string | null>(null);
  const start = useMutation({ mutationFn: () => api.post(`/portal/readiness/run?sme_id=${smeId}`, {}), onSuccess: (r) => setRunId(r.run_id), onError: (e: Error) => toast.error(e.message) });
  const st = useQuery({
    queryKey: ["portal-run", runId], enabled: !!runId, queryFn: () => api.get(`/portal/runs/${runId}?sme_id=${smeId}`),
    refetchInterval: (q) => (["completed", "failed", "cancelled"].includes(q.state.data?.status) ? false : 600),
  });
  const status = st.data?.status;
  useEffect(() => {
    if (!runId || !status) return;
    if (status === "completed") {
      toast.success("Assessment updated");
      const refresh = () => qc.invalidateQueries({ predicate: (q) => String(q.queryKey[0]).startsWith("portal-") });
      refresh();
      const t = [setTimeout(refresh, 2500), setTimeout(refresh, 6000)]; // the improvement plan and market view follow the readiness run
      setRunId(null);
      return () => t.forEach(clearTimeout);
    }
    if (status === "failed") { toast.error("Something went wrong. Please try again."); setRunId(null); }
  }, [status, runId, qc]);
  return { start: () => start.mutate(), running: start.isPending || !!runId, progress: st.data as { total_steps: number; completed_steps: number; current_step: string | null } | undefined };
}
