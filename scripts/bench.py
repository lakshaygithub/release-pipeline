"""Measure read latency, e.g. cache on (port 8000) vs off (port 8001).

    python scripts/bench.py --url http://localhost:8000
    python scripts/bench.py --url http://localhost:8001

Creates --items items, reads each once to warm the cache, then reads them in a
random order. Reports two things:

  * end to end: what the client sees (includes network, HTTP and JSON overhead)
  * lookup:     the server's own Server-Timing for the cache or database lookup
"""

import argparse
import asyncio
import random
import statistics
import time

import httpx


def percentile(sorted_values: list[float], p: float) -> float:
    index = min(len(sorted_values) - 1, round(p / 100 * (len(sorted_values) - 1)))
    return sorted_values[index]


def lookup_ms(resp: httpx.Response) -> float | None:
    timing = resp.headers.get("Server-Timing", "")
    if "dur=" not in timing:
        return None
    return float(timing.split("dur=")[1].split(",")[0])


def summary(label: str, values: list[float]) -> str:
    values = sorted(values)
    return (
        f"{label:<12} p50 {percentile(values, 50):7.3f} ms   "
        f"p95 {percentile(values, 95):7.3f} ms   p99 {percentile(values, 99):7.3f} ms"
    )


async def run(url: str, total: int, concurrency: int, items: int, seed: int) -> None:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=10) as client:
        ids = []
        for n in range(items):
            created = await client.post(
                "/items", json={"name": f"Benchmark item {n}", "price_cents": 999}
            )
            created.raise_for_status()
            ids.append(created.json()["id"])

        for item_id in ids:  # warm-up: one read per item fills the cache
            (await client.get(f"/items/{item_id}")).raise_for_status()

        rng = random.Random(seed)
        queue: asyncio.Queue[int] = asyncio.Queue()
        for _ in range(total):
            queue.put_nowait(rng.choice(ids))

        end_to_end: list[float] = []
        lookups: list[float] = []
        cache_status: dict[str, int] = {}
        errors = 0

        async def worker() -> None:
            nonlocal errors
            while True:
                try:
                    item_id = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                start = time.perf_counter()
                resp = await client.get(f"/items/{item_id}")
                end_to_end.append((time.perf_counter() - start) * 1000)
                if resp.status_code != 200:
                    errors += 1
                    continue
                status = resp.headers.get("X-Cache", "?")
                cache_status[status] = cache_status.get(status, 0) + 1
                if (ms := lookup_ms(resp)) is not None:
                    lookups.append(ms)

        wall_start = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        wall = time.perf_counter() - wall_start

    print(f"target       {url}  ({items} items, random reads)")
    print(f"requests     {total} (concurrency {concurrency}, errors {errors})")
    print(f"responses    {', '.join(f'{k} {v}' for k, v in sorted(cache_status.items()))}")
    print(f"throughput   {total / wall:,.0f} req/s")
    print(summary("end to end", end_to_end) + f"   mean {statistics.fmean(end_to_end):.2f} ms")
    if lookups:
        print(summary("lookup", lookups))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--items", type=int, default=200)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.requests, args.concurrency, args.items, args.seed))


if __name__ == "__main__":
    main()
