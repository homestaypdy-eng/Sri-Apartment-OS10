import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveApiBaseUrl } from "./apiBaseUrl";

describe("resolveApiBaseUrl", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("keeps the local backend default for development", () => {
    expect(resolveApiBaseUrl(undefined)).toBe("http://localhost:8000/api/v1");
  });

  it("respects an explicitly configured API base URL", () => {
    expect(resolveApiBaseUrl("https://api.sriapartment.in")).toBe("https://api.sriapartment.in");
  });

  it("fails safely instead of using localhost in an unconfigured production build", () => {
    vi.stubEnv("DEV", false);
    expect(() => resolveApiBaseUrl(undefined)).toThrow("VITE_API_BASE_URL must be configured for production builds.");
  });
});
