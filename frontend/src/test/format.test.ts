import { describe, expect, it } from "vitest";
import { inr, num, pct, title } from "@/lib/format";

describe("format helpers", () => {
  it("never fabricates a value for missing numbers", () => {
    expect(pct(null)).toBe("Not available");
    expect(num(undefined)).toBe("Not available");
  });
  it("formats rupees", () => {
    expect(inr(1234567)).toBe("₹12.3 L");
    expect(inr(null)).toBe("Not available");
  });
  it("title-cases snake case and keeps acronyms", () => {
    expect(title("kyc_exception")).toBe("KYC exception");
    expect(title("credit_referral")).toBe("Credit referral");
  });
});
