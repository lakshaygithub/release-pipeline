#!/usr/bin/env bash
# Roll out a new image tag. If the new pods don't pass their readiness checks
# in time, roll back to the previous revision and exit non-zero.
#
#   scripts/deploy.sh <image-tag>
set -euo pipefail

TAG="${1:?usage: deploy.sh <image-tag>}"
NS="${NAMESPACE:-release-pipeline}"
TIMEOUT="${ROLLOUT_TIMEOUT:-90s}"

previous=$(kubectl -n "$NS" get deployment/api -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "Deploying release-pipeline:${TAG} (currently ${previous})"

start=$(date +%s)
kubectl -n "$NS" set image deployment/api api="release-pipeline:${TAG}"

if kubectl -n "$NS" rollout status deployment/api --timeout="$TIMEOUT"; then
  echo "Rollout of ${TAG} succeeded in $(( $(date +%s) - start ))s"
  exit 0
fi

echo "Rollout of ${TAG} failed its health checks after $(( $(date +%s) - start ))s; rolling back to ${previous}"
kubectl -n "$NS" rollout undo deployment/api
kubectl -n "$NS" rollout status deployment/api --timeout="$TIMEOUT"
echo "Rolled back to ${previous}; total time $(( $(date +%s) - start ))s"
exit 1
