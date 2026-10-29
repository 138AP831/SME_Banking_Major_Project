import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, FlaskConical, Inbox, Loader2, X } from "lucide-react";
import { forwardRef, type ButtonHTMLAttributes, type HTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes, type TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- Button */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50 disabled:pointer-events-none whitespace-nowrap cursor-pointer",
  {
    variants: {
      variant: {
        primary: "bg-teal text-white hover:bg-teal-dark shadow-sm",
        secondary: "bg-white text-slate border border-line hover:bg-ivory-2",
        ghost: "text-slate hover:bg-ivory-2",
        danger: "bg-risk text-white hover:bg-[#962f3b]",
        sand: "bg-sand text-navy hover:bg-[#dcb55a]",
        dark: "bg-navy text-white hover:bg-navy-2",
      },
      size: { sm: "h-8 px-3", md: "h-9 px-4", lg: "h-11 px-5 text-base", icon: "h-8 w-8" },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);
export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> { asChild?: boolean; loading?: boolean }
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, asChild, loading, children, disabled, ...p }, ref) => {
  const C = asChild ? Slot : "button";
  return (
    <C ref={ref} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} {...p}>
      {asChild ? children : (<>{loading && <Loader2 className="h-4 w-4 animate-spin" />}{children}</>)}
    </C>
  );
});
Button.displayName = "Button";

/* ---------------------------------------------------------------- Card */
export const Card = ({ className, ...p }: HTMLAttributes<HTMLDivElement>) => <div className={cn("rounded-[14px] border border-line bg-paper", className)} {...p} />;
export const CardHeader = ({ title, sub, right, className }: { title: ReactNode; sub?: ReactNode; right?: ReactNode; className?: string }) => (
  <div className={cn("flex items-start justify-between gap-3 px-5 pt-4 pb-2", className)}>
    <div className="min-w-0"><h3 className="text-[15px] font-semibold">{title}</h3>{sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}</div>
    {right}
  </div>
);
export const CardBody = ({ className, ...p }: HTMLAttributes<HTMLDivElement>) => <div className={cn("px-5 pb-5 pt-2", className)} {...p} />;

/* ---------------------------------------------------------------- Badge */
const badge = cva("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "bg-ivory-2 text-slate", teal: "bg-teal-soft text-teal-dark", sand: "bg-sand-soft text-[#7a5a0b]", risk: "bg-risk-soft text-risk", warn: "bg-warn-soft text-warn",
      navy: "bg-navy text-white", outline: "border border-line text-muted bg-transparent",
    },
  }, defaultVariants: { tone: "neutral" },
});
export const Badge = ({ tone, className, ...p }: HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badge>) => <span className={cn(badge({ tone }), className)} {...p} />;

const TONES: Record<string, "teal" | "sand" | "warn" | "risk" | "neutral" | "navy"> = {
  low: "teal", moderate: "sand", elevated: "warn", high: "risk", critical: "risk", medium: "sand",
  eligible: "teal", refer: "sand", not_eligible: "risk", approved: "teal", eligible_for_approval: "teal", completed: "teal", running: "navy", queued: "neutral",
  awaiting_review: "sand", failed: "risk", cancelled: "neutral", exception: "risk", info_requested: "sand", in_progress: "neutral", rejected: "risk", pending: "sand", decided: "teal",
  open: "sand", in_review: "neutral", escalated: "risk", resolved: "teal", dismissed: "neutral", extracted: "teal", low_confidence: "warn", corrected: "teal", granted: "teal", withdrawn: "risk",
  active: "teal", onboarding: "sand", prospect: "neutral", info: "neutral", attention: "warn", stable: "teal", watch: "sand", shifted: "warn",
};
export const StatusBadge = ({ value, label }: { value: string | null | undefined; label?: string }) => {
  if (!value) return <Badge tone="outline">None</Badge>;
  return <Badge tone={TONES[value.toLowerCase()] ?? "neutral"}>{label ?? value.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}</Badge>;
};

/** Marks data that is simulated / synthetic so it can never be mistaken for a live source. */
export const SimBadge = ({ children = "Simulated", title }: { children?: ReactNode; title?: string }) => (
  <span title={title ?? "Simulated / synthetic in this prototype"} className="inline-flex items-center gap-1 rounded-full border border-dashed border-sand bg-sand-soft/60 px-2 py-0.5 text-[11px] font-semibold text-[#7a5a0b]">
    <FlaskConical className="h-3 w-3" />{children}
  </span>
);

/* ---------------------------------------------------------------- Inputs */
const field = "h-9 w-full rounded-lg border border-line bg-white px-3 text-sm text-ink placeholder:text-muted/70 focus:border-teal focus:outline-none focus:ring-2 focus:ring-teal/25 disabled:bg-ivory";
export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, r) => <input ref={r} className={cn(field, className)} {...p} />);
Input.displayName = "Input";
export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(({ className, ...p }, r) => <select ref={r} className={cn(field, "pr-8", className)} {...p} />);
Select.displayName = "Select";
export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, r) => <textarea ref={r} className={cn(field, "h-auto min-h-[84px] py-2", className)} {...p} />);
Textarea.displayName = "Textarea";
export const Label = ({ children, className, hint }: { children: ReactNode; className?: string; hint?: ReactNode }) => (
  <label className={cn("mb-1 block text-xs font-semibold text-slate", className)}>{children}{hint && <span className="ml-1 font-normal text-muted">{hint}</span>}</label>
);

export const Switch = ({ checked, onCheckedChange, label }: { checked: boolean; onCheckedChange: (v: boolean) => void; label: string }) => (
  <SwitchPrimitive.Root checked={checked} onCheckedChange={onCheckedChange} aria-label={label}
    className="relative h-6 w-11 shrink-0 cursor-pointer rounded-full bg-line transition-colors data-[state=checked]:bg-teal">
    <SwitchPrimitive.Thumb className="block h-5 w-5 translate-x-0.5 rounded-full bg-white shadow transition-transform data-[state=checked]:translate-x-[22px]" />
  </SwitchPrimitive.Root>
);

export const Segmented = <T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label: string }[] }) => (
  <div className="inline-flex rounded-lg border border-line bg-white p-0.5" role="tablist">
    {options.map((o) => (
      <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
        className={cn("rounded-md px-3 py-1 text-xs font-semibold transition-colors cursor-pointer", value === o.value ? "bg-navy text-white" : "text-slate hover:bg-ivory-2")}>{o.label}</button>
    ))}
  </div>
);

/* ---------------------------------------------------------------- Dialog / Tabs / Tooltip */
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;
export function DialogContent({ title, description, children, className, side }: { title: string; description?: string; children: ReactNode; className?: string; side?: boolean }) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-navy/45 backdrop-blur-[1px]" />
      <DialogPrimitive.Content
        className={cn("fixed z-50 bg-paper shadow-2xl focus:outline-none", side ? "right-0 top-0 h-full w-[min(560px,100vw)] overflow-y-auto border-l border-line" : "left-1/2 top-1/2 max-h-[88vh] w-[min(620px,94vw)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-line", className)}>
        <div className="flex items-start justify-between gap-4 border-b border-line px-6 py-4">
          <div><DialogPrimitive.Title className="text-lg font-semibold text-slate">{title}</DialogPrimitive.Title>
            {description ? <DialogPrimitive.Description className="mt-0.5 text-sm text-muted">{description}</DialogPrimitive.Description> : <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>}</div>
          <DialogPrimitive.Close className="rounded-md p-1 text-muted hover:bg-ivory-2 cursor-pointer" aria-label="Close"><X className="h-5 w-5" /></DialogPrimitive.Close>
        </div>
        <div className="px-6 py-5">{children}</div>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}
export const Tabs = TabsPrimitive.Root;
export const TabsList = ({ className, ...p }: TabsPrimitive.TabsListProps) => <TabsPrimitive.List className={cn("flex gap-1 border-b border-line", className)} {...p} />;
export const TabsTrigger = ({ className, ...p }: TabsPrimitive.TabsTriggerProps) => (
  <TabsPrimitive.Trigger className={cn("-mb-px cursor-pointer border-b-2 border-transparent px-3.5 py-2 text-sm font-semibold text-muted hover:text-slate data-[state=active]:border-teal data-[state=active]:text-slate", className)} {...p} />
);
export const TabsContent = TabsPrimitive.Content;
export const TooltipProvider = TooltipPrimitive.Provider;
export const Tip = ({ text, children }: { text: ReactNode; children: ReactNode }) => (
  <TooltipPrimitive.Root delayDuration={150}>
    <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
    <TooltipPrimitive.Portal><TooltipPrimitive.Content sideOffset={6} className="z-[60] max-w-xs rounded-lg bg-navy px-3 py-2 text-xs leading-snug text-white shadow-lg">{text}<TooltipPrimitive.Arrow className="fill-navy" /></TooltipPrimitive.Content></TooltipPrimitive.Portal>
  </TooltipPrimitive.Root>
);

/* ---------------------------------------------------------------- states */
export const Skeleton = ({ className }: { className?: string }) => <div className={cn("animate-pulse rounded-lg bg-ivory-2", className)} />;
export const PageSkeleton = () => (
  <div className="space-y-4" aria-busy="true" aria-label="Loading">
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-24" />)}</div>
    <div className="grid gap-4 lg:grid-cols-3"><Skeleton className="h-64 lg:col-span-2" /><Skeleton className="h-64" /></div>
  </div>
);
export const EmptyState = ({ title, hint, action, icon }: { title: string; hint?: ReactNode; action?: ReactNode; icon?: ReactNode }) => (
  <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line px-6 py-10 text-center">
    <div className="text-muted">{icon ?? <Inbox className="h-7 w-7" />}</div>
    <p className="font-semibold text-slate">{title}</p>{hint && <p className="max-w-md text-sm text-muted">{hint}</p>}{action}
  </div>
);
export const ErrorState = ({ error, retry }: { error: unknown; retry?: () => void }) => (
  <div role="alert" className="flex items-start gap-3 rounded-xl border border-risk/30 bg-risk-soft/60 p-4">
    <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-risk" />
    <div className="min-w-0 flex-1"><p className="font-semibold text-risk">This view could not load</p><p className="mt-0.5 text-sm text-ink">{error instanceof Error ? error.message : String(error)}</p></div>
    {retry && <Button size="sm" variant="secondary" onClick={retry}>Try again</Button>}
  </div>
);
export const NotAvailable = ({ reason }: { reason: string }) => (
  <div className="rounded-lg border border-dashed border-line bg-white/60 px-4 py-3 text-sm"><span className="font-semibold text-slate">Not available. </span><span className="text-muted">{reason}</span></div>
);

/* ---------------------------------------------------------------- page chrome */
export function PageHeader({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div><h1 className="text-[26px] font-bold leading-tight">{title}</h1>{sub && <p className="mt-1 max-w-3xl text-sm text-muted">{sub}</p>}</div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}
export const Disclaimer = ({ children }: { children: ReactNode }) => (
  <p className="rounded-lg border border-sand/60 bg-sand-soft/50 px-3 py-2 text-xs text-[#6b5210]">{children}</p>
);

/* ---------------------------------------------------------------- table */
export const Table = ({ className, ...p }: HTMLAttributes<HTMLTableElement>) => <div className="overflow-x-auto"><table className={cn("w-full text-left text-sm", className)} {...p} /></div>;
export const Th = ({ className, ...p }: HTMLAttributes<HTMLTableCellElement>) => <th className={cn("whitespace-nowrap border-b border-line px-3 py-2 text-xs font-semibold text-muted", className)} {...p} />;
export const Td = ({ className, ...p }: HTMLAttributes<HTMLTableCellElement>) => <td className={cn("border-b border-line/60 px-3 py-2.5 align-middle", className)} {...p} />;
