# Latency Benchmark

Measurement only, from one run against a deployed reference instance
(container host + a private object-storage bucket in the same region).
**No architecture or implementation decision is attached to these
numbers** — they are reported, not acted on.

Reproduce against your own deployment with:

```bash
uv run python scripts/benchmark_latency.py \
  --url https://<your-endpoint>/mcp --token <credential> \
  --bucket <artifact-bucket> --gcp-project <gcp-project> \
  --store-project <mak4i-project-id>
```

(Run just the MCP half with `--url`/`--token`, or just the store half with
`--bucket`/`--gcp-project`/`--store-project`.)

## Cold start

A fresh revision (identical image, redeployed only to force a new
container) was measured on its first request: **291ms** (curl,
`initialize`). Five subsequent requests to the same instance: **127–242ms**
— no dramatic cliff between the two. This means the 291ms figure is **not**
a genuine request-triggered cold start: the platform's deploy-time
readiness probe already boots and health-checks an instance before
routing traffic, so by the time the "first" external request arrives,
that instance has already paid its Python-import/module-init cost. A true
scale-to-zero → new-request cold start would require an extended idle
period to observe directly, which wasn't done here. This instance had no
minimum-instance floor set, so scale-to-zero and a genuine cold start do
happen during real idle periods — this benchmark just didn't capture one
directly.

## MCP end-to-end latency (warm, n=8 each, same session)

| Tool | min | median | max | mean |
|---|---|---|---|---|
| `mak4i_search` | 303ms | 315ms | 359ms | 320ms |
| `mak4i_get_current` | 518ms | 542ms | 622ms | 553ms |
| First call in a brand-new session | — | 522–728ms (two runs) | — | — |

`get_current` is consistently ~1.7x slower than `search`. Both tools funnel
through the same store-level full-project scan (`GCSArtifactStore._iter_all`
via `store.query`/`store.all`) — `get_current` additionally builds the full
resolution (lineage grouping, integrity check, conflict grouping) on top,
which the numbers below suggest is a small fraction of the difference; most
of it is more likely additional store/session overhead in the fuller
`get_current` code path (Discovery + full `store.all()` + Resolver +
ContextBuilder, vs. `search`'s single `store.query()` call) rather than the
in-memory resolution logic itself, which operates on already-fetched data
and is not store-bound.

## Store time in isolation (warm, n=8 each)

Measured by instantiating the real, unmodified `GCSArtifactStore` directly
against the bucket — no server, no MCP, no HTTP layer — over a small
project (single-digit artifact count):

| Operation | min | median | max | mean |
|---|---|---|---|---|
| `store.all()` (list + N × object download) | 530ms | 548ms | 618ms | 554ms |
| `store.query(status=active)` | 504ms | 522ms | 577ms | 527ms |
| First call in a new process (client + credential init) | — | 1094–1193ms (two runs) | — | — |

## An important caveat on comparing the two tables

**This isolated benchmark ran from a local machine, not co-located with
the store** — its object-store calls cross the public internet, while the
deployed service and the bucket are in the same region and talk over the
provider's internal network. That likely explains why the isolated
`store.query()` figure (522ms) is *larger* than the full end-to-end
`mak4i_search` MCP call measured against the live service (315ms), even
though `mak4i_search` internally makes the same store call plus MCP/HTTP/auth
overhead on top — the local-to-store network path is simply slower than
the same-region service-to-store path. **The isolated table should not be
read as "the store accounts for X of the Y total" by direct subtraction;
it's a same-code, different-network-path measurement**, useful for
confirming the store operations are the dominant, sequential (list + N
individual downloads, not batched or parallel) cost shape, not for a
precise same-network latency breakdown. A more precise breakdown would
require timing instrumentation inside the deployed service itself, which
was not added — no code changes were made for this benchmark.

## What this does and doesn't show

**Shown**: `GCSArtifactStore._iter_all()` does one list call followed by a
sequential download per object — an O(N) chain of individual round trips,
not a batch or parallel fetch. At the small artifact count used here,
that's enough to make `get_current` and `search` (which both go through
this path) measurably slower than a single-object `store.get()` would be
(not separately re-measured here, but implied by the linear-in-N shape).

**Not shown, and not claimed**: whether this matters at any particular
scale, whether it's the cause of anything perceived as slow in a specific
client session, or what (if anything) should change. Per instruction, no
such conclusion is drawn here.
