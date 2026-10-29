export const inr = (v: number | null | undefined, opts: { compact?: boolean } = {}) => {
  if (v === null || v === undefined || Number.isNaN(v)) return "Not available";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(a >= 1e8 ? 1 : 2)} Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(a >= 1e6 ? 1 : 2)} L`;
  if (opts.compact && a >= 1e3) return `${s}₹${(a / 1e3).toFixed(1)}K`;
  return `${s}₹${Math.round(a).toLocaleString("en-IN")}`;
};
export const pct = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? "Not available" : `${(v * 100).toFixed(d)}%`);
export const num = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? "Not available" : v.toLocaleString("en-IN", { maximumFractionDigits: d }));
const ACR: Record<string, string> = { kyc: "KYC", ews: "EWS", pd: "PD", gst: "GST", pan: "PAN", sme: "SME", emi: "EMI", dscr: "DSCR", rm: "RM", id: "ID", ifsc: "IFSC", cin: "CIN", itr: "ITR", ocr: "OCR" };
export const title = (s: string | null | undefined) => (s ? s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()).split(" ").map((w) => ACR[w.toLowerCase()] ?? w).join(" ") : "");
export const shortDate = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso.length <= 10 ? iso + "T00:00:00" : iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
};
export const dateTime = (iso: string | null | undefined) => {
  if (!iso) return "";
  return new Date(iso).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
};
export const timeAgo = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
};
