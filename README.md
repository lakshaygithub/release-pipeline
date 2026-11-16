# release-pipeline

A small catalog API used to practise the path from a laptop to production:
a read-through cache in front of the database, tests and a Docker build on
every push, and Kubernetes rollouts that undo themselves when a release is broken.

Everything below is measured, not estimated: the numbers come from `scripts/bench.py`
on my laptop and from the CI logs.

## What's here

| Piece | What it does |
| --- | --- |
| `app/` | FastAPI service storing items in MySQL, with a Redis read-through cache |
| `tests/` | pytest suite (16 tests): cache hits and misses, invalidation, Redis outage, paging, readiness |
| `Dockerfile`, `docker-compose.yml` | Local stack: MySQL, Redis, and two copies of the API (cache on and off) |
| `k8s/` | Namespace, MySQL, Redis and the API Deployment with readiness and liveness probes |
| `scripts/deploy.sh` | Rolls out an image; if the new pods never become ready, runs `kubectl rollout undo` |
| `scripts/bench.py` | Random reads across many items; reports end-to-end latency and the server's own lookup time |
| `.github/workflows/ci.yml` | Lint and tests, then deploys to a temporary kind cluster and checks that a broken release is rolled back |

## Design notes

**Caching.** `GET /items/{id}` checks Redis first and falls back to MySQL on a miss,
then stores the result with a TTL. Writes go to MySQL first and then delete the cached
copy, so the next read refills the cache from the source of truth. Each response carries
an `X-Cache: HIT | MISS | BYPASS` header so the behaviour can be checked from outside.

**Measuring the cache honestly.** Item reads return a standard `Server-Timing` header
with the time spent in the cache or database lookup. That separates what the cache
changes from everything it can't (HTTP, JSON, network), which turned out to matter.

**Paging.** `GET /items?limit=20&after_id=<last id>` uses keyset pagination instead of
`OFFSET`, so deep pages stay fast and items aren't skipped or repeated when rows are
added or deleted between requests.

**Redis is optional.** If Redis is down, cache calls fail fast (0.5 s timeout) and the
service keeps answering from MySQL. Readiness depends on the database, not the cache, so
a Redis outage slows the service down instead of taking it offline.

**Health checks.** `/healthz` (liveness) only says the process is running.
`/readyz` (readiness) checks the database. Keeping them separate means a pod that can't
serve traffic is taken out of the load balancer instead of being restarted in a loop.

**Safe rollouts.** The Deployment uses `maxUnavailable: 0` and `maxSurge: 1`, so an old
pod is only removed once a new one passes readiness. A broken release therefore never
receives traffic; its rollout stalls, hits `progressDeadlineSeconds`, and `deploy.sh`
rolls back to the previous revision. The deadline is 30 s: a healthy pod is ready in a
few seconds, so that still leaves plenty of room.

To test that path, CI builds a deliberately broken image (`FAIL_READINESS=true`, which
makes `/readyz` return 503), deploys it, and checks that the running image afterwards
is the last good one.

## Run it locally

```bash
docker compose up -d --build
curl localhost:8000/readyz
curl -X POST localhost:8000/items -H 'content-type: application/json' \
     -d '{"name": "Butter Chicken", "price_cents": 1599}'
curl -i localhost:8000/items/1    # X-Cache: MISS, then HIT on the next call
```

Port 8000 runs with the cache and port 8001 without it.

### Tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
ruff check . && pytest -q
```

### Benchmark: cache on vs off

```bash
python scripts/bench.py --url http://localhost:8000   # cache on
python scripts/bench.py --url http://localhost:8001   # cache off
```

### Deploy to a local Kubernetes cluster

```bash
kind create cluster --name dev
docker build -t release-pipeline:initial .
kind load docker-image release-pipeline:initial --name dev
kubectl apply -f k8s/

docker build --build-arg APP_VERSION=v2 -t release-pipeline:v2 .
kind load docker-image release-pipeline:v2 --name dev
scripts/deploy.sh v2

# A broken release: readiness fails, so the rollout is undone automatically
docker build --build-arg FAIL_READINESS=true -t release-pipeline:bad .
kind load docker-image release-pipeline:bad --name dev
scripts/deploy.sh bad
```

## Results

### Cache on vs off

`scripts/bench.py` on a laptop (Docker Desktop, 2 API workers, MySQL 8.4, Redis 7.4):
5,000 random reads across 200 items, three runs each. Ranges are across the three runs.

| Measurement | Cache on (Redis) | Cache off (MySQL) |
| --- | --- | --- |
| Lookup inside the server, p50 (50 concurrent clients) | 0.16 – 0.17 ms | 0.43 – 0.69 ms |
| Lookup inside the server, p99 (50 concurrent clients) | 1.3 – 1.7 ms | 3.0 – 9.6 ms |
| End to end, p50 (one request at a time) | 4.9 ms | 4.3 ms |
| End to end, p50 (50 concurrent clients) | 58 – 66 ms | 57 – 85 ms |

**What this shows.** Redis makes the lookup itself about 3× faster and cuts its tail
(p99) by 2–6×. But the lookup is well under 1 ms of a ~4.5 ms request, so end-to-end
latency barely moves; the run-to-run spread is bigger than the cache's effect. For a
primary-key read that MySQL already keeps in memory, the cache isn't what limits this
service: HTTP handling, JSON and the network are. A cache would pay off for slower
queries or a database under heavier load. My first benchmark (one item, read over and
over, end-to-end only) showed no difference at all, which is why the `Server-Timing`
header exists.

### Rollouts (from the CI logs)

| Measurement | Value |
| --- | --- |
| Good release, 3 replicas, rolled out one pod at a time | 22 s |
| Broken release detected and rolled back, `progressDeadlineSeconds: 60` | 60 s |
| Broken release detected and rolled back, `progressDeadlineSeconds: 30` | 31 s |

During the broken rollout the old pods keep serving: `maxUnavailable: 0` means no old
pod is removed until a new one is ready, and the broken pods never become ready.

## Next steps

- Schema migrations (Alembic) instead of `create_all`
- Push images to a registry and deploy from CI to a real cluster
- Try a slower, cache-worthy query (e.g. a filtered listing) to see where the cache starts paying off
