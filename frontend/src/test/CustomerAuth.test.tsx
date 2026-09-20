import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { CustomerAuthProvider } from "../context/CustomerAuthContext";
import CustomerLoginPage from "../pages/CustomerLoginPage";
import CustomerSignUpPage from "../pages/CustomerSignUpPage";
import CustomerForgotPasswordPage from "../pages/CustomerForgotPasswordPage";

const customerRegister = vi.fn();
const customerLogin = vi.fn();
const customerForgotPassword = vi.fn();

vi.mock("../lib/api", () => ({
  customerRegister: (...args: unknown[]) => customerRegister(...args),
  customerLogin: (...args: unknown[]) => customerLogin(...args),
  customerLogout: vi.fn().mockResolvedValue(undefined),
  customerForgotPassword: (...args: unknown[]) => customerForgotPassword(...args),
  customerResetPassword: vi.fn(),
  extractErrorMessage: (e: unknown) => (e as Error)?.message ?? "error",
}));

function renderWithProviders(ui: React.ReactElement) {
  return render(
    <MemoryRouter>
      <CustomerAuthProvider>{ui}</CustomerAuthProvider>
    </MemoryRouter>
  );
}

describe("CustomerSignUpPage", () => {
  beforeEach(() => {
    customerRegister.mockReset();
  });

  it("requires both Terms and Privacy checkboxes before submitting", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CustomerSignUpPage />);
    await user.type(screen.getByLabelText("First name"), "Ada");
    await user.type(screen.getByLabelText("Last name"), "Lovelace");
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "Passw0rd1");
    await user.click(screen.getByRole("checkbox", { name: /Privacy Policy/i }));
    await user.click(screen.getByRole("button", { name: "Create Account" }));

    expect(customerRegister).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(/Terms/);
  });

  it("has no Date of Birth field", () => {
    renderWithProviders(<CustomerSignUpPage />);
    expect(screen.queryByLabelText(/date of birth/i)).not.toBeInTheDocument();
  });

  it("submits the exact expected payload once both checkboxes are accepted", async () => {
    customerRegister.mockResolvedValue({
      id: "1", first_name: "Ada", last_name: "Lovelace", email: "ada@example.com", email_verified_at: null, created_at: "now",
    });
    const user = userEvent.setup();
    renderWithProviders(<CustomerSignUpPage />);
    await user.type(screen.getByLabelText("First name"), "Ada");
    await user.type(screen.getByLabelText("Last name"), "Lovelace");
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "Passw0rd1");
    await user.click(screen.getByRole("checkbox", { name: /Terms & Conditions/i }));
    await user.click(screen.getByRole("checkbox", { name: /Privacy Policy/i }));
    await user.click(screen.getByRole("button", { name: "Create Account" }));

    await waitFor(() =>
      expect(customerRegister).toHaveBeenCalledWith({
        first_name: "Ada",
        last_name: "Lovelace",
        email: "ada@example.com",
        password: "Passw0rd1",
        terms_accepted: true,
        privacy_accepted: true,
      })
    );
    expect(await screen.findByText(/account has been created/i)).toBeInTheDocument();
  });

  it("shows the server's validation error and does not silently succeed", async () => {
    customerRegister.mockRejectedValue(new Error("An account with this email already exists."));
    const user = userEvent.setup();
    renderWithProviders(<CustomerSignUpPage />);
    await user.type(screen.getByLabelText("First name"), "Ada");
    await user.type(screen.getByLabelText("Last name"), "Lovelace");
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "Passw0rd1");
    await user.click(screen.getByRole("checkbox", { name: /Terms & Conditions/i }));
    await user.click(screen.getByRole("checkbox", { name: /Privacy Policy/i }));
    await user.click(screen.getByRole("button", { name: "Create Account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("An account with this email already exists.");
  });
});

describe("CustomerLoginPage", () => {
  beforeEach(() => {
    localStorage.clear();
    customerLogin.mockReset();
  });

  it("logs in and stores the customer session token", async () => {
    customerLogin.mockResolvedValue({
      access_token: "tok123",
      token_type: "bearer",
      customer: { id: "1", first_name: "Ada", last_name: "Lovelace", email: "ada@example.com", email_verified_at: null, created_at: "now" },
    });
    const user = userEvent.setup();
    renderWithProviders(<CustomerLoginPage />);
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "Passw0rd1");
    await user.click(screen.getByRole("button", { name: "Log In" }));

    await waitFor(() => expect(customerLogin).toHaveBeenCalledWith("ada@example.com", "Passw0rd1"));
    await waitFor(() => expect(localStorage.getItem("customer_access_token")).toBe("tok123"));
  });

  it("shows the backend's generic anti-enumeration error verbatim on failure", async () => {
    customerLogin.mockRejectedValue(new Error("Incorrect email or password"));
    const user = userEvent.setup();
    renderWithProviders(<CustomerLoginPage />);
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Log In" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password");
    expect(localStorage.getItem("customer_access_token")).toBeNull();
  });

  it("Google/Facebook buttons show an honest not-configured notice, never a fake login", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CustomerLoginPage />);
    await user.click(screen.getByRole("button", { name: /Continue with Google/i }));
    expect(await screen.findByText(/isn't set up yet/i)).toBeInTheDocument();
    expect(customerLogin).not.toHaveBeenCalled();
    expect(localStorage.getItem("customer_access_token")).toBeNull();

    await user.click(screen.getByRole("button", { name: /Continue with Facebook/i }));
    expect(await screen.findByText(/isn't set up yet/i)).toBeInTheDocument();
  });

  it("has a link to Sign Up and a link to Guest Login", () => {
    renderWithProviders(<CustomerLoginPage />);
    expect(screen.getByRole("link", { name: /create an account/i })).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /guest login/i }).length).toBeGreaterThan(0);
  });
});

describe("CustomerForgotPasswordPage", () => {
  beforeEach(() => {
    customerForgotPassword.mockReset();
  });

  it("shows the generic response and the dev reset token when present", async () => {
    customerForgotPassword.mockResolvedValue({
      message: "If that email is registered, a password reset link has been sent.",
      dev_reset_token: "abc123token",
    });
    const user = userEvent.setup();
    renderWithProviders(<CustomerForgotPasswordPage />);
    await user.type(screen.getByLabelText("Email"), "ada@example.com");
    await user.click(screen.getByRole("button", { name: "Request Reset Link" }));

    expect(await screen.findByText(/password reset link has been sent/i)).toBeInTheDocument();
    expect(screen.getByText("abc123token")).toBeInTheDocument();
  });

  it("never renders a dev token field when the backend omits it", async () => {
    customerForgotPassword.mockResolvedValue({ message: "If that email is registered, a password reset link has been sent." });
    const user = userEvent.setup();
    renderWithProviders(<CustomerForgotPasswordPage />);
    await user.type(screen.getByLabelText("Email"), "unknown@example.com");
    await user.click(screen.getByRole("button", { name: "Request Reset Link" }));

    await screen.findByText(/password reset link has been sent/i);
    expect(screen.queryByText(/development mode/i)).not.toBeInTheDocument();
  });
});
