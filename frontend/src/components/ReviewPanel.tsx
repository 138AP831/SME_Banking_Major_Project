import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, HelpCircle, Lock, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api, type J } from "@/lib/api";
import { useRole } from "@/lib/role";
import { Badge, Button, Label, StatusBadge, Textarea } from "@/components/ui/primitives";
import { title as t } from "@/lib/format";

const ICON: Record<string, JSX.Element> = { approve: <CheckCircle2 className="h-4 w-4" />, reject: <XCircle className="h-4 w-4" />, request_info: <HelpCircle className="h-4 w-4" /> };

export function ReviewPanel({ review, onDecided }: { review: J; onDecided?: () => void }) {
  const { role } = useRole();
  const qc = useQueryClient();
  const [reason, setReason] = useState("");
  const [action, setAction] = useState<string>("");
  const allowed = (review.allowed_roles as string[]).includes(role);
  const m = useMutation({
    mutationFn: () => api.post(`/reviews/${review.id}/decision`, { action, reason }),
    onSuccess: (r) => {
      toast.success(r.workflow_resumed ? "Decision recorded - the workflow is resuming" : "Decision recorded");
      qc.invalidateQueries(); onDecided?.();
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const decided = review.status === "decided";
  return (
    <div className="space-y-3 rounded-xl border border-sand/70 bg-sand-soft/40 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="sand">{decided ? "Decided" : "Human decision required"}</Badge>
        <span className="text-sm font-semibold text-slate">{t(review.kind)}</span>
        <span className="text-xs text-muted">{review.sme_name}</span>
      </div>
      <ul className="space-y-1.5 text-sm">
        {(review.reasons ?? []).map((r: J, i: number) => (
          <li key={i} className="flex gap-2"><Badge tone="outline" className="shrink-0">{r.rule_id}</Badge><span>{r.message}</span></li>
        ))}
      </ul>
      {decided ? (
        <div className="rounded-lg bg-white p-3 text-sm">
          <div className="flex items-center gap-2"><StatusBadge value={review.action} /><span className="text-muted">by {review.reviewer_name} ({review.reviewer_role})</span></div>
          <p className="mt-1.5 text-ink">“{review.reason}”</p>
          {review.new_status && <p className="mt-1 text-xs text-muted">Status: {t(review.previous_status)} → {t(review.new_status)}</p>}
        </div>
      ) : !allowed ? (
        <div className="flex items-start gap-2 rounded-lg bg-white p-3 text-sm text-muted"><Lock className="mt-0.5 h-4 w-4 shrink-0" />
          <span>This decision belongs to: <strong className="text-slate">{review.allowed_roles.join(", ")}</strong>. You are viewing as {role}. Switch role in the top bar to decide.</span></div>
      ) : (
        <div className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {Object.entries(review.action_labels as Record<string, string>).map(([k, label]) => (
              <Button key={k} size="sm" variant={action === k ? (k === "reject" ? "danger" : "primary") : "secondary"} onClick={() => setAction(k)} aria-pressed={action === k}>{ICON[k]}{label}</Button>
            ))}
          </div>
          <div><Label hint="(recorded in the audit trail)">Reason</Label><Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Explain the basis for this decision (min. 10 characters)" /></div>
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted">The workflow stays paused until you decide. Nothing is approved automatically.</p>
            <Button disabled={!action || reason.trim().length < 10} loading={m.isPending} onClick={() => m.mutate()}>Submit decision</Button>
          </div>
        </div>
      )}
    </div>
  );
}
