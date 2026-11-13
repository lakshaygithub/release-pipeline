"""Measure read latency for one item, e.g. cache on (port 8000) vs off (port 8001).

    python scripts/bench.py --url http://localhost:8000 --requests 5000 --concurrency 50
    python scripts/bench.py --url http://localhost:8001 --requests 5000 --concurrency 50
"""

import argparse
import asyncio
import statistics
import time

import httpx


def percentile(sorted_values: list[float], p: float) -> float:
    index = min(len(sorted_values) - 1, round(p / 100 * (len(sorted_values) - 1)))
    return sorted_values[index]


async def run(url: str, total: int, concurrency: int, warmup: int) -> None:
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=url, limits=limits, timeout=10) as client:
        created = await client.post("/items", json={"name": "Benchmark item", "price_cents": 999})
        created.raise_for_status()
        path = f"/items/{created.json()['id']}"

        for _ in range(warmup):
            (await client.get(path)).raise_for_status()

        latencies: list[float] = []
        errors = 0
        queue: asyncio.Queue[int] = asyncio.Queue()
        for i in range(total):
            queue.put_nowait(i)

        async def worker() -> None:
            nonlocal errors
            while True:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                start = time.perf_counter()
                resp = await client.get(path)
                latencies.append((time.perf_counter() - start) * 1000)
                if resp.status_code != 200:
                    errors += 1

        wall_start = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        wall = time.perf_counter() - wall_start

    latencies.sort()
    print(f"target       {url}{path}")
    print(f"requests     {total} (concurrency {concurrency}, errors {errors})")
    print(f"throughput   {total / wall:,.0f} req/s")
    print(f"p50          {percentile(latencies, 50):.2f} ms")
    print(f"p95          {percentile(latencies, 95):.2f} ms")
    print(f"p99          {percentile(latencies, 99):.2f} ms")
    print(f"mean         {statistics.fmean(latencies):.2f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=200)
    args = parser.parse_args()
    asyncio.run(run(args.url, args.requests, args.concurrency, args.warmup))


if __name__ == "__main__":
    main()
