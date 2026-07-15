# Frontend — Dokumentensuche

A React + TypeScript single-page app (Vite, Tailwind, shadcn/ui) for searching
the indexed documents: query box, mode toggle (lexical / semantic / hybrid), a
classification filter and a result list with highlighted snippets.

## Run it

```bash
nvm use                 # Node version from .nvmrc
npm install
cp .env.example .env.local   # then paste a bearer token into .env.local
npm run dev             # http://localhost:5173
```

The dev server proxies `/api/*` to the Flask API on `http://localhost:5002`
(see `vite.config.ts`), so there is no CORS in development. Start the backend
separately (`docker compose up -d`, then run the API), and make sure at least
one document has been ingested — otherwise searches return no hits.

## Auth (stopgap)

There is no login screen yet. The app reads a bearer token from
`VITE_DEV_TOKEN` in `.env.local` and sends it on every request. Get one from
Keycloak for a realm user (e.g. `ermittler`). The real authorization-code / PKCE
login is a later step; only `src/lib/api.ts` changes when it lands.

## Layout

- `src/lib/types.ts` — the search API contract, mirrored from the backend.
- `src/lib/api.ts` — typed client (`search()`), dev-token auth, `ApiError`.
- `src/lib/useSearch.ts` — runs searches, exposes the state, drops stale responses.
- `src/components/` — `SearchBar`, `ModeToggle`, `Filters`, `ResultList`,
  `ResultCard`, `HighlightedText` (renders `<em>` fragments without `innerHTML`).
- `src/components/ui/` — shadcn/ui primitives.

## Not yet in scope

Real OIDC login, the passage detail view (`GET /documents/<id>/passage`),
further filters (Aktenzeichen, Verfahren, date range, language), pagination and
the upload UI.
