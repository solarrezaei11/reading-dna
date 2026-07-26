# ReadingDNA data handling

ReadingDNA is designed primarily for local development. Before using a hosted
backend, define the deployment's access, retention, logging, deletion, and
privacy policies.

Only process another person's reading history when you have an appropriate
basis to do so. A publicly viewable Goodreads profile does not by itself
define how that data may be reused.

## Data processed

- Goodreads RSS or CSV imports can contain book titles, authors, ratings,
  shelves, and written reviews.
- The configured backend receives that reading history to build the profile,
  recommendations, map, and rating predictions.
- The hosted LLM provider(s) you have configured receive a bounded summary of
  the reading history and generated profile when an LLM-powered feature runs.
  This is Cerebras by default, and additionally Groq and/or OpenRouter when you
  enable them for the recommendation battle (`GROQ_API_KEY` /
  `OPENROUTER_API_KEY`). Imported text is treated as untrusted data, but it
  still leaves the local environment for those calls.
- Goodreads, Open Library, and OverDrive receive the lookup data required for
  their respective import, metadata, cover, and availability requests.
- The browser stores the active analysis input in session storage so it is
  scoped to the current browser tab. It can survive page reloads and is
  normally removed when that tab session ends.
- The backend keeps model, library-catalog, and embedding caches in process
  memory. Those caches are not durable storage and are lost when the process
  exits.

## Persistence

Prediction logging is disabled by default. Setting
`ENABLE_PREDICTION_LOG=true` enables a local JSONL log under `backend/`.
Treat that file as private reading-history data, apply an appropriate retention
policy, and do not expose it through a public endpoint.

ReadingDNA does not provide user accounts or multi-tenant isolation. A hosted
deployment should sit behind an authenticated ingress or trusted server-side
proxy before accepting private user data. The optional shared bearer token is
only a deployment-wide gate; it is not per-user authentication or
authorization. The built-in rate limiter is process-local, so multi-instance
deployments need a shared limiter if they require a global policy.

Hosting platforms, reverse proxies, and observability products may retain
request metadata or errors independently of ReadingDNA. Review those systems
and avoid logging request bodies or secrets.

## Secrets

Keep `CEREBRAS_API_KEY`, any optional provider keys (`GROQ_API_KEY`,
`OPENROUTER_API_KEY`), and any backend access token in `.env.local` or the
hosting platform's secret store. Never prefix secrets with `NEXT_PUBLIC_`,
commit `.env.local`, or place API keys in browser code. Use HTTPS for any
non-local deployment so reading history and authorization headers are
encrypted in transit.
