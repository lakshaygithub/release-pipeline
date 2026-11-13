# release-pipeline

A small catalog API used to practise the path from a laptop to production:
a read-through cache in front of the database, tests and a Docker build on
every push, and Kubernetes rollouts that undo themselves when a release is broken.

**Status: in progress.** The service, tests, Docker setup, Kubernetes manifests and
CI pipeline are in place. Benchmarks and results are still to be measured (see below).

## What's here

| Piece | What it does |
| --- | --- |
| `app/` | FastAPI service storing items in MySQL, with a Redis read-through cache |
| `tests/` | pytest suite: cache hits and misses, invalidation on update and delete, Redis outage, readiness |
| `Dockerfile`, `docker-compose.yml` | Local stack: MySQL, Redis, and two copies of the API (cache on and off) |
| `k8s/` | Namespace, MySQL, Redis and the API Deployment with readiness and liveness probes |
| `scripts/deploy.sh` | Rolls out an image; if the new pods never become ready, runs `kubectl rollout undo` |
| `scripts/bench.py` | Measures p50 / p95 / p99 read latency and throughput |
| `.github/workflows/ci.yml` | Lint and tests, then deploys to a temporary kind cluster and checks that a broken release is rolled back |

## Design notes

**Caching.** `GET /items/{id}` checks Redis first and falls back to MySQL on a miss,
then stores the result with a TTL. Writes go to MySQL first and then delete the cached
copy, so the next read refills the cache from the source of truth. Each response carries
an `X-Cache: HIT | MISS | BYPASS` header so the behaviour can be checked from outside.

**Redis is optional.** If Redis is down, cache calls fail fast (0.5 s timeout) and the
service keeps answering from MySQL. Readiness depends on the database, not the cache, so
a Redis outage slows the service down instead of taking it offline.

**Health checks.** `/healthz` (liveness) only says the process is running.
`/readyz` (readiness) checks the database. Keeping them separate means a pod that can't
serve traffic is taken out of the load balancer instead of being restarted in a loop.

**Safe rollouts.** The Deployment uses `maxUnavailable: 0` and `maxSurge: 1`, so an old
pod is only removed once a new one passes readiness. A broken release therefore never
receives traffic; its rollout stalls, hits `progressDeadlineSeconds`, and `deploy.sh`
rolls back to the previous revision.

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
python scripts/bench.py --url http://localhost:8000 --requests 5000 --concurrency 50
python scripts/bench.py --url http://localhost:8001 --requests 5000 --concurrency 50
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

To be measured.

| Measurement | Value |
| --- | --- |
| Read latency p50 / p99, cache on | — |
| Read latency p50 / p99, cache off | — |
| Time to detect and roll back a broken release | — |

## Next steps

- Record the benchmark and rollback numbers above
- Schema migrations (Alembic) instead of `create_all`
- Push images to a registry and deploy from CI to a real cluster
