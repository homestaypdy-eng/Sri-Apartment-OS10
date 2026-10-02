import { beforeEach, describe, expect, it, vi } from "vitest";

const { post, requestUse, responseUse } = vi.hoisted(() => ({
  post: vi.fn(),
  requestUse: vi.fn(),
  responseUse: vi.fn(),
}));

vi.mock("axios", () => ({
  default: {
    create: () => ({
      post,
      get: vi.fn(),
      interceptors: { request: { use: requestUse }, response: { use: responseUse } },
    }),
    post,
  },
}));

import { customerLogin, customerVerifyLoginOtp, initializeCustomerIdentityProfile } from "./api";

describe("customer login API contract", () => {
  beforeEach(() => post.mockReset());

  it("starts email/password authentication using the backend channel and identifier fields", async () => {
    post.mockResolvedValue({ data: { challenge_id: "challenge-1" } });

    await customerLogin("ada@example.com", "Passw0rd1");

    expect(post).toHaveBeenCalledWith("/customer-auth/login", {
      channel: "email",
      identifier: "ada@example.com",
      password: "Passw0rd1",
    });
  });

  it("verifies the OTP challenge through the session-issuing backend endpoint", async () => {
    post.mockResolvedValue({ data: { access_token: "session-token" } });

    await customerVerifyLoginOtp("challenge-1", "123456");

    expect(post).toHaveBeenCalledWith("/customer-auth/login/verify-otp", {
      challenge_id: "challenge-1",
      code: "123456",
    });
  });

  it("initializes identity without allowing the browser to select a Guest", async () => {
    post.mockResolvedValue({ data: { status: "not_required" } });

    await initializeCustomerIdentityProfile();

    expect(post).toHaveBeenCalledWith("/customer-auth/me/identity/profile");
  });
});
