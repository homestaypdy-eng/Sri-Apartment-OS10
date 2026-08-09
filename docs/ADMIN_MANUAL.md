# Sri Apartment OS — Admin Manual

## Getting Started

Log in at the frontend URL with your admin email and password. The default seeded admin is `homestaypdy@gmail.com` / `Sri@2026` — **change this password immediately** via the Users page.

## What Only Admins Can Do (vs. Staff)

| Action | Admin | Staff |
|---|---|---|
| Create/deactivate users, change roles | Yes | No |
| Update business Settings | Yes | View only |
| Create properties/units | Yes | View/book only |
| Approve/reject leave requests | Yes | Apply only |
| Generate and mark payroll paid | Yes | No |
| Create shifts, holidays | Yes | View only |
| View audit logs and login history | Yes | No |
| Issue refunds | Yes | No |
| Manage OTA channel settings, promo codes | Yes | Can trigger a sync |
| Bookings, housekeeping, maintenance, inquiries, inventory stock movements | Yes | Yes |

## Business Settings

Go to Settings to update company name, address, GST number, bank/UPI details, and default terms/house-rules/cancellation-policy text. **Note:** Settings is currently informational and not yet wired into invoice/quotation PDF generation (those still use their own built-in defaults) — a deliberate choice to avoid touching already-tested PDF code in this pass. Wiring it in later is a small, low-risk change.

## Users & Roles

Two roles exist: `admin` and `staff`. Create new users under Users → Add User. Deactivating a user immediately blocks new logins (existing tokens still work until they expire naturally — there's no forced token revocation yet).

## Reports & Sales Funnel

The Reports page has date-range and property filters, KPI cards, charts, and a Sales Funnel section showing lead counts by stage (new/contacted/follow_up/converted/lost) with a conversion rate. Export any report as PDF or Excel via the buttons at the bottom.

## Inventory

Inventory → Items shows stock levels; a low-stock badge appears when current stock falls to or below the minimum. Every Stock In/Stock Out creates an audit trail — you can always see how current stock got to its current number.

## Staff Management

- Attendance: one record per user per day.
- Leave: staff apply, admins approve/reject.
- Payroll: generate once per user per month; mark paid when disbursed.
- Shifts/Holidays: planning reference — no automatic conflict-checking against attendance yet.

## OTA Channel Manager

Booking.com, Airbnb, and Agoda are listed with "not live" status — this is honest, not a bug. Real connectivity requires becoming an approved partner with each platform (a business/legal process this software can't do for you). The Manual Sync button runs against a clearly-labeled mock adapter so the intended workflow is visible today, ready to connect to real APIs once partner credentials exist.

## Payment Gateway (Razorpay)

Order creation and refunds return "not configured" until real Razorpay credentials are set in the environment. Webhook signature verification works fully once the webhook secret is set, independent of the other credentials — this protects the system from fake payment-confirmation calls.

## Notifications

WhatsApp sending opens a pre-filled chat — you still click send and attach the PDF yourself (a WhatsApp platform limitation, not a shortcut taken here). Email requires SMTP environment variables; until set, it honestly reports not-configured rather than pretending to send. Automatic reminder emails only run if the scheduler is enabled — otherwise trigger them manually from Notifications.

## Guest Portal

Guests log in at the `/guest/login` URL (a separate page from the staff/admin login) using the phone number on their booking. It sends them a login link with a token — until real email/SMTP delivery is configured, the token is shown directly on screen for testing purposes (clearly labeled as such; remove reliance on this before onboarding real guests). Once logged in, guests see their own bookings only, can download their invoice, and can raise service requests, complaints, or stay-extension requests — all of which appear in your Guest Requests page to action.

## Audit & Security

Audit logs capture settings changes and user role/activation changes. Login history captures every login attempt. There's no MFA yet. See the README's known gaps for the full list of what isn't production-hardened.
