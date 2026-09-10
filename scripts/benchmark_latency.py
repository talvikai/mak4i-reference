"""Latency benchmark against a deployed MAK4I service.

Measurement only — this script makes no claims about what, if anything,
should change; see docs/LATENCY_BENCHMARK.md for a specific run's results
and the explicit note that no architecture/implementation decision is
attached to them.

Two independent measurements:
1. End-to-end MCP tool-call latency over the real network (search,
   get_current), via a real mcp.client.Client — same path any real client
   uses.
2. Store latency in isolation, using the real, unmodified
   GCSArtifactStore class directly against a bucket — no server, no MCP,
   no HTTP layer, to separate store time from the rest of the stack.

Nothing is hard-coded to any particular deployment. Supply the target
through CLI flags or the matching environment variables:

    MAK4I_BENCHMARK_URL            --url            MCP endpoint (…/mcp)
    MAK4I_BENCHMARK_TOKEN          --token          bearer credential (raw)
    MAK4I_BENCHMARK_BUCKET         --bucket         artifact bucket (store half)
    MAK4I_BENCHMARK_GCP_PROJECT    --gcp-project    GCP project for the GCS client
    MAK4I_BENCHMARK_STORE_PROJECT  --store-project  MAK4I project id to scan

    uv run python scripts/benchmark_latency.py --url https://…/mcp --token … \
        --bucket … --gcp-project … --store-project …

Run only the MCP half by supplying just --url/--token; run only the store
half by supplying just --bucket/--gcp-project/--store-project.
"""

from __future__ import annotations

import argparse
import os
import statistics
import time

import httpx2

from mak4i.store.gcs import GCSArtifactStore
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client


def _summarize(label: str, values: list[float]) -> None:
    print(
        f"{label}: n={len(values)} min={min(values):.1f}ms "
        f"median={statistics.median(values):.1f}ms max={max(values):.1f}ms "
        f"mean={statistics.mean(values):.1f}ms"
    )


async def _timed_call(client: Client, tool: str, args: dict) -> tuple[float, bool]:
    t0 = time.perf_counter()
    result = await client.call_tool(tool, args)
    return (time.perf_counter() - t0) * 1000, result.is_error


async def benchmark_mcp(url: str, token: str, n: int) -> None:
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    transport = streamable_http_client(url, http_client=http_client)

    async with Client(server=transport) as client:
        first_ms, first_err = await _timed_call(client, "mak4i_search", {"actor": "latency-bench"})
        print(f"first call in a new session: mak4i_search {first_ms:.1f}ms error={first_err}")

        search_times = []
        for _ in range(n):
            ms, _ = await _timed_call(client, "mak4i_search", {"actor": "latency-bench"})
            search_times.append(ms)

        get_current_times = []
        for _ in range(n):
            ms, _ = await _timed_call(client, "mak4i_get_current", {"actor": "latency-bench"})
            get_current_times.append(ms)

    print()
    print(f"=== MCP end-to-end latency (warm, same session, n={n} each) ===")
    _summarize("search", search_times)
    _summarize("get_current", get_current_times)


def benchmark_store(bucket: str, gcp_project: str, store_project: str, n: int) -> None:
    store = GCSArtifactStore(bucket, project=gcp_project)

    t0 = time.perf_counter()
    first_result = store.all(project=store_project)
    first_ms = (time.perf_counter() - t0) * 1000
    print(
        f"first store.all() call (includes GCS client/ADC init): "
        f"{first_ms:.1f}ms, {len(first_result)} artifacts"
    )

    all_times, query_times = [], []
    for _ in range(n):
        t0 = time.perf_counter()
        store.all(project=store_project)
        all_times.append((time.perf_counter() - t0) * 1000)
    for _ in range(n):
        t0 = time.perf_counter()
        store.query(project=store_project, status="active")
        query_times.append((time.perf_counter() - t0) * 1000)

    print()
    print(f"=== GCS/store latency in isolation (warm, n={n} each) ===")
    _summarize("store.all() — list_blobs + N x download_as_bytes", all_times)
    _summarize("store.query(status=active)", query_times)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("MAK4I_BENCHMARK_URL"))
    parser.add_argument("--token", default=os.environ.get("MAK4I_BENCHMARK_TOKEN"))
    parser.add_argument("--bucket", default=os.environ.get("MAK4I_BENCHMARK_BUCKET"))
    parser.add_argument("--gcp-project", default=os.environ.get("MAK4I_BENCHMARK_GCP_PROJECT"))
    parser.add_argument(
        "--store-project", default=os.environ.get("MAK4I_BENCHMARK_STORE_PROJECT")
    )
    parser.add_argument("--n", type=int, default=8)
    args = parser.parse_args()

    import asyncio

    ran_something = False

    if args.url:
        if not args.token:
            parser.error("--url requires --token (or MAK4I_BENCHMARK_TOKEN)")
        asyncio.run(benchmark_mcp(args.url, args.token, args.n))
        ran_something = True

    if args.bucket or args.gcp_project or args.store_project:
        missing = [
            name
            for name, val in (
                ("--bucket", args.bucket),
                ("--gcp-project", args.gcp_project),
                ("--store-project", args.store_project),
            )
            if not val
        ]
        if missing:
            parser.error(f"store benchmark needs all of: {', '.join(missing)}")
        print()
        benchmark_store(args.bucket, args.gcp_project, args.store_project, args.n)
        ran_something = True

    if not ran_something:
        parser.error(
            "nothing to do — supply --url/--token for the MCP half and/or "
            "--bucket/--gcp-project/--store-project for the store half"
        )


if __name__ == "__main__":
    main()
