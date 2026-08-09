# Sri Apartment Homestay — Your Go-Live Guide

This is written specifically for putting your software live for your business. Follow it in order.

---

## What's already set up correctly for you

Your business details are already loaded as the system defaults (confirmed live, not just written into a file):

- Company: SRI APARTMENT HOMESTAY, "Luxury 2BHK Family Homestay"
- Address: 163, 2nd Cross Street, Ranga Nagar, Moolakulam, Puducherry - 605010
- GSTIN: 34PACPS5159H1Z5, UDYAM: UDYAM-PY-03-0032646
- Bank: Axis Bank, A/C 925020053638481, IFSC UTIB0003923
- UPI: 8870237237@axisbank
- Signatory: S. Sundaramurthy, Proprietor

You can edit any of these later from Settings in the app if anything changes (new bank account, updated phone number, etc.) — no code changes needed, ever.

## What is NOT set up yet — you must do this

Your actual rooms/units aren't in the system yet. I don't know your real room count, names, or nightly rates, so I didn't guess. Once you're logged in:

1. Go to Properties & Units
2. Add your property (e.g. "Sri Apartment Homestay")
3. Add each unit you actually rent out (e.g. "2BHK - Ground Floor", "2BHK - First Floor") with its real nightly rate

This takes about 2 minutes per unit and is the only "setup" step left that requires your input.

---

## Step 1: Choose where it runs — recommended: Railway

Railway is the lowest-effort option for a small business: you don't manage a server, it costs roughly $5-15/month depending on usage, and it deploys directly from your project files.

1. Go to railway.app and sign up (GitHub login is easiest)
2. Click New Project then Deploy from local directory / Empty Project
3. Add three services to the project:
   - PostgreSQL — Railway has a one-click "Add Database -> PostgreSQL" button
   - Backend — deploy the backend/ folder (Railway auto-detects the Dockerfile)
   - Frontend — deploy the frontend/ folder (Railway auto-detects the Dockerfile)
4. Set these environment variables on the Backend service:

DATABASE_URL=(copy the connection string Railway shows you for the Postgres service)
JWT_SECRET_KEY=0YE4EP7tzCW1u1fW1-aUCjL7yaF__aiZ4N_eIRZ1RggpqyljsEyZi7qZ7LWZZBgfRuOwpC42RCVdSiwSLAJo2A
ENVIRONMENT=production
CORS_ALLOWED_ORIGINS=(your frontend's Railway URL, e.g. https://sri-apartment-frontend.up.railway.app)

The JWT_SECRET_KEY above is a real, randomly generated secret — safe to use, but treat it as a password: don't share it publicly, and if it ever leaks, generate a new one (any Python: python3 -c "import secrets; print(secrets.token_urlsafe(64))").

5. Set this environment variable on the Frontend service:

VITE_API_BASE_URL=(your backend's Railway URL)/api/v1

6. Deploy both. Railway gives you two public URLs — the frontend one is what you and your staff will actually visit and use day to day.

If this feels like too much to do alone: hand this exact section to anyone with basic technical comfort (a local computer shop, a freelancer, a tech-savvy family member) — it's a 30-60 minute task for someone who's done it before.

## Step 2: First login and lockdown

1. Visit your frontend URL
2. Log in: homestaypdy@gmail.com / Sri@2026
3. Immediately go to Users, click your admin account, change the password. This default password is public (it's in this document and every doc file) — do not skip this.
4. Add your real rooms/units (see above)
5. If you'll have staff logging in, create their accounts now (Users -> Add User) with the staff role — they can't see billing/user-management/settings, only bookings/housekeeping/maintenance/inquiries

## Step 3: A real test booking, start to finish

Before trusting it with a real guest, do exactly this once yourself:

1. Inquiries -> create a quotation for a fake test guest, real dates, one of your real units
2. Download the quotation PDF — check it looks right (your logo, your bank details, correct room rate)
3. Set an advance amount -> Convert to Booking
4. Bookings -> Check In
5. Billing -> record the remaining payment -> Generate Invoice -> download it, check it looks right
6. Bookings -> Check Out
7. Go to Housekeeping -> confirm a cleaning task was automatically created for that room and it's marked dirty

If all seven steps work exactly as expected, the system is ready for a real guest.

## Step 4: Optional — email sending

If you want invoices/quotations emailed automatically instead of downloaded-and-forwarded manually:

1. Use a Gmail account (or your business email if it supports SMTP)
2. If Gmail: turn on 2-Step Verification, then create an App Password (Google Account -> Security -> App Passwords)
3. Set on the Backend service:

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=youremail@gmail.com
SMTP_PASSWORD=(the app password, not your real Gmail password)
SMTP_FROM_EMAIL=youremail@gmail.com

4. Test by generating an invoice for a booking with a real email address on it and using the Send Email button

Not required to start using the system — WhatsApp sending and manual PDF download/forwarding both work without this.

## Step 5: Backups (do this before you have real guest data you can't afford to lose)

Railway's Postgres has automatic backups on paid plans — check your plan includes this. If not, or as extra safety, the manual command (run from anywhere with psql installed, pointed at your Railway database URL):

pg_dump "(your Railway DATABASE_URL)" > backup_$(date +%Y%m%d).sql

Do this weekly at minimum once you have real bookings in the system.

---

## What "used perfectly" honestly means here

I've verified every workflow works correctly against this exact codebase, live, repeatedly, in this session. What I cannot promise: that your specific Railway account, your specific browser, or a scenario I haven't tested won't surface something. That's normal for any real software, not a flaw specific to this one. The difference this makes for you: if something looks wrong once you're live, it's very likely something in Steps 1-2 above (a misconfigured environment variable, an unset field), not a hidden defect — check those first, and come back and tell me exactly what you see if it isn't.
