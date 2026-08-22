# Hoardarr web interface

The web interface is a React/TypeScript/Vite client for the versioned Hoardarr API. It does not run storage commands or infer success from browser state. All storage mutations remain backend-enforced and fail closed.

The reproducible development runtime is Node.js 24.18.0 (recorded in `.node-version` and aligned with the appliance build host). Package versions are exact and `package-lock.json` is committed; use `npm ci` for release builds.

```powershell
npm ci
npm run dev
```

Vite proxies `/api` and `/health` to `http://127.0.0.1:8080` during local development. Set `VITE_HOARDARR_API_BASE` only when the API is on another origin.

Demonstration data is disabled by default. Copy `.env.example` to `.env.local` and set `VITE_HOARDARR_DEMO=true` to exercise the interface without a server. Demo mode is always identified by a persistent banner and never activates as a fallback after an API error.
