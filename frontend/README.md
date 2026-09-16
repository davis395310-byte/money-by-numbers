# Money By Numbers — Frontend

Next.js (App Router) + TypeScript frontend for the Money By Numbers NFL
intelligence + sportsbook-affiliate platform. Mobile-first, dark theme.

## Prerequisites

- Node.js 24+ and npm 10+

## Getting started

```bash
npm install
```

## Development

```bash
npm run dev
```

Runs the dev server at http://localhost:3000. The frontend calls the
backend API (default http://localhost:8000) entirely from client
components, so dev works even with no backend running — data sections
render honest "DATA UNAVAILABLE" empty states.

## Production build

```bash
npm run build
```

Builds the static production site. All API calls happen in client
components (`useEffect`), so the build never depends on the backend.

```bash
npm start
```

Serves the production build.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Base URL of the Money By Numbers backend API |

Set it at build time or via your hosting provider's environment settings:

```bash
NEXT_PUBLIC_API_URL=https://api.example.com npm run build
```

## Structure

- `app/` — routes: `/`, `/nfl`, `/best-bets`, `/track-record`, `/numby`, `/how-it-works`
- `components/` — Header, Footer, PlaceholderPage, `home/` section components
- `lib/api.ts` — typed backend API client (`/api/health`, `/api/data/health`)
- `lib/config.ts` — site constants (brand, API URL, affiliate disclosure)

## Rules this frontend follows

- No fabricated data, odds, predictions, injuries, weather, affiliate URLs,
  or model statistics anywhere. Missing data renders as "DATA UNAVAILABLE".
- Brand is Money By Numbers only.
