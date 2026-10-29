import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NotAvailable, SimBadge, StatusBadge } from "@/components/ui/primitives";
import { ReviewPanel } from "@/components/ReviewPanel";
import { RoleProvider } from "@/lib/role";

describe("primitives", () => {
  it("NotAvailable shows the reason, not a number", () => {
    render(<NotAvailable reason="No labelled outcomes exist." />);
    expect(screen.getByText(/No labelled outcomes exist/)).toBeInTheDocument();
  });
  it("SimBadge labels simulated data", () => {
    render(<SimBadge />);
    expect(screen.getByText("Simulated")).toBeInTheDocument();
  });
  it("StatusBadge renders a readable label", () => {
    render(<StatusBadge value="awaiting_review" />);
    expect(screen.getByText(/awaiting review/i)).toBeInTheDocument();
  });
});

const review = {
  id: "REV-1", kind: "kyc_exception", status: "pending", sme_name: "Test Co", allowed_roles: ["KYC Analyst"],
  reasons: [{ rule_id: "KYC-001", message: "Open exceptions" }], action_labels: { approve: "Approve", reject: "Reject", request_info: "Request info" },
};

describe("ReviewPanel role gating", () => {
  it("does not offer decision buttons to a role that is not permitted", () => {
    const qc = new QueryClient();
    render(<QueryClientProvider client={qc}><RoleProvider><ReviewPanel review={review} /></RoleProvider></QueryClientProvider>);
    expect(screen.getByText("KYC-001")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Approve$/ })).not.toBeInTheDocument();
  });
});
