# Sri Apartment customer website

Customer-facing React/Vite application, imported from the local website served on port 5175. OS10's staff frontend remains in `../frontend`; the backend remains in `../backend`.

## Local development

```sh
cd customer-website
npm ci
cp .env.example .env.local
npm run dev
```

Vite uses port **5175** with `strictPort`; it will not silently select another port. Set `VITE_API_BASE_URL` to your approved local backend. Direct cross-origin access also requires that backend's CORS allowlist to include the website origin.

For the existing isolated customer-auth sandbox, copy `.env.development.example` to `.env.development.local`. Confirm that `127.0.0.1:8026` is your disposable sandbox before using it. This repository does not automatically start, seed, migrate or restart a backend. The isolated Vite proxy allows only the explicitly listed customer endpoints; it is not a general administrative proxy. Optional remote proxy mode permits only public reads and must never forward customer or payment mutations remotely.

Do not commit `.env.local`, `.env.development.local`, credentials, databases, `node_modules` or generated `dist` output. Every `VITE_*` value is public in the browser bundle. Razorpay secrets belong only in the backend; checkout obtains the publishable key from OS10's hold response.

## Checks

```sh
npm test
npm run lint
npm run build
```

`test` runs mocked frontend regression tests. `build` runs TypeScript checks and writes `dist/`. The GitHub workflow tests and builds this directory; it does not deploy it.

## API and launch status

OS10 is authoritative for authentication, verified customer sessions, availability, quotations, taxes, booking holds, payments and confirmation. Customer flow: availability → select flat → login/sign up → Customer Details → Booking Review → payment → server-verified confirmation.

This is a source snapshot, not a launch certification or deployment. Local OS10 backend changes are not included in this website-only branch. Customer authentication and identity screens require compatible reviewed backend routes and schema. Synthetic OTPs and a disabled local payment provider do not establish live readiness. Zotel cross-channel exclusivity remains a launch blocker; never treat website availability or a successful build as permission to open sales.

The six intended launch flats are A1, A2, B3, B4, C5 and C6. Their marketing assets are in `src/content/apartments.ts`; backend UUID/mapping checks must govern sale authorization.
