---
name: cirra-frontend
description: Cirra Next.js 14 frontend conventions - app router pages under app/(app), the UI kit (Button, Card, Dialog, Input/Select/Textarea/Label, Table/Td/Tabs/StatusPill, Badge tones, Skeleton, EmptyState), data fetching with react-query and the axios api client, permissions with useMe().can, toasts, design tokens, sidebar navigation, and how to typecheck/lint/build in Docker. Use for any change under frontend/.
---

# Frontend conventions

## Structure
- Pages: `frontend/app/(app)/<area>/page.tsx` (signed-in shell), dynamic routes use `useParams()`;
  public pages at `app/login`, `app/portal`, `app/sign`, `app/csat`, `app/unsubscribe`, `app/forms`.
- Feature components in `frontend/components/*.tsx` (one file per area: `service.tsx`, `serviceops.tsx`,
  `campaigns.tsx`, `journeys.tsx`, `listviews.tsx`, `reportviz.tsx`, `objects.tsx`, …); Admin panels in
  `components/admin/*.tsx`.
- UI kit in `components/ui/`: `button` (variants primary|secondary|ghost|outline|destructive|ai, `loading`),
  `card` (`Card`, `CardHeader {title, description, action, icon}`, `CardBody`), `dialog` (`Dialog`,
  `DialogContent title=…`), `input` (`Input`, `Select`, `Textarea`, `Label`), `extra` (`Table head=[…]`,
  `Td`, `Tabs`, `StatusPill`, `Field`), `badge` (tones neutral|primary|ai|good|warning|critical|outline),
  `misc` (`Skeleton`, `EmptyState`, `Avatar`, `Tooltip`, `Dropdown…`).
- `lib/api.ts`: `api` (axios, `/api/v1`), `get<T>(url, params)`, `errorMessage(e)`, `downloadFile(path, name)`.
  `lib/me.ts`: `useMe()` → `{ me, can(resource, action) }`, `ROLE_LABELS`. `lib/types.ts`: shared types.
- Navigation: `components/AppShell.tsx` `NAV` (items gated by `resource`), custom objects added by
  `CustomObjectsNav`; `PageHeader {title, description, actions}`.

- Help center: content lives in `backend/app/help/content.py` (roles, areas with `pages` and how-tos, processes,
  glossary); `services/help.py` serves it (`/help`, `/help/areas/{key}`, `/help/search`, `/help/context`,
  `/help/data-model`, `/help/ask`). UI: `app/(app)/help` (tabs via `?tab=`), `app/(app)/help/areas/[key]`,
  `components/help.tsx` (`HelpDrawer` opened by the header button or the ? key through `ui.helpOpen`,
  `ProcessMap`, `DataModelExplorer`, `HelpLinks`). A new screen: add its route to an area's `pages` so the
  drawer finds it, and to the page list in `tests/test_help.py`.

## Patterns
- Queries: `useQuery({ queryKey: [area, …], queryFn: () => get(…) })`; mutations invalidate by the area key
  (e.g. `["cases"]`) so lists, views and detail pages refresh together.
- Feedback with `toast.success/error(errorMessage(e))` from `sonner`; errors from the API are already worded
  for users (`detail`).
- Gate UI with `can()`, but the API is the real guard.
- Colours come from CSS tokens in `app/globals.css` (`--status-good|warning|critical`, `primary`, `muted`,
  `subtle`); support light and dark mode; use `tabular` for numbers.
- Accessible labels: every control has a `<Label htmlFor>` or `aria-label` (the browser journeys rely on them).
- `useEffect` bodies use braces: an expression body that returns a Promise (e.g. `scrollIntoView` in new
  Chromium) crashes React with "destroy is not a function".
- `localStorage` only for per-browser conveniences, wrapped in try/catch (see `listviews.tsx`).
- Localisation (`lib/i18n.tsx`): the user's locale (`me.preferences.locale`, else the browser) drives number,
  currency and date formats through `money`, `fmtNumber`, `fmtDateTime`, `shortDate`, `relativeDays`
  (`lib/utils`) and `fmtMoney` (`ui/extra`); never hard-code `"en-US"`. UI strings: `const t = useT()` then
  `t("key", vars)`; add the key to `en` and the other dictionaries (missing keys fall back to English).
  English output is unchanged byte for byte, so existing journeys keep working.

## Checks (host has no node_modules)
```bash
cd <repo> && tar --exclude=node_modules --exclude=.next -cf - frontend | MSYS_NO_PATHCONV=1 docker run --rm -i \
  node:20-alpine sh -c "mkdir /w && cd /w && tar xf - && cd frontend && npm ci --silent && npx tsc --noEmit && npx next lint"
docker compose up -d --build web     # then browse http://localhost:3000
```
Add or update a browser journey in `e2e/` for new flows (see `cirra-testing`).

## Mobile, offline and translation
- `public/sw.js` (service worker: shell cache, network-first pages and everyday API reads, push) is registered in
  production builds only (`components/mobile.tsx`). Signing out clears its API cache and the offline outbox.
- `lib/offline.ts`: POST /activities, POST /tasks and PATCH /tasks/{id} made offline are queued and replayed; other
  writes fail normally. Mutations use `networkMode: "always"` so they reach the queue.
- `lib/translate.ts` + `lib/phrases/<lang>.json`: rendered English copy is translated in place for es/fr/de/hi.
  New UI copy: add it to the phrase catalogues (English text → translation); mark record data with
  `translate="no"` where it could collide with a UI phrase. Key-based `t()` strings stay the first choice for the shell.
- Every request sends `X-Cirra-Host` (and `X-Cirra-Tenant` when `?workspace=` was chosen at sign-in); use
  `authHeaders()` for raw `fetch` downloads.
