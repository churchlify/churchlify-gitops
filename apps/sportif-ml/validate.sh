#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
rendered="$(mktemp)"
trap 'rm -f "$rendered"' EXIT

kubectl kustomize "$repo_root/apps/sportif-ml" > "$rendered"

if grep -nE 'REPLACE|SET_FROM_|TODO_IMAGE|example.invalid' "$rendered"; then
  echo "active Sportif ML manifests contain a placeholder" >&2
  exit 1
fi

if grep -q '^kind: WorkflowTemplate$' "$rendered"; then
  echo "WorkflowTemplate must remain staged until Argo Workflows is installed" >&2
  exit 1
fi

if grep -q 'name: cvat$' "$rendered"; then
  echo "CVAT must be installed through its supported Helm chart" >&2
  exit 1
fi

if grep -q 'kubernetes.io/hostname:' "$rendered"; then
  echo "active Sportif ML manifests must not pin workloads to a node name" >&2
  exit 1
fi

if grep -q 'name: mlflow$' "$rendered" \
  && ! grep -q 'MLFLOW_S3_ENDPOINT_URL:' "$rendered"; then
  echo "MLflow requires MLFLOW_S3_ENDPOINT_URL for the MinIO artifact store" >&2
  exit 1
fi

if grep -q 'name: mlflow$' "$rendered" \
  && ! grep -q 'name: mlflow-s3-dependencies$' "$rendered"; then
  echo "MLflow requires the hash-locked S3 dependency ConfigMap" >&2
  exit 1
fi

if grep -q 'name: mlflow$' "$rendered" \
  && grep -q 'image: ghcr.io/mlflow/mlflow:v2.18.0$' "$rendered"; then
  echo "MLflow images must be pinned by digest" >&2
  exit 1
fi

if find "$repo_root" -type f \( -path '*/__pycache__/*' -o -name '*.pyc' \) \
  -not -path '*/.git/*' | grep -q .; then
  echo "compiled Python artifacts must not be committed" >&2
  exit 1
fi

python3 - "$repo_root/apps/sportif-ml" <<'PY'
from pathlib import Path
import sys
import yaml

for path in Path(sys.argv[1]).rglob("*.yaml"):
    list(yaml.safe_load_all(path.read_text()))
print("Sportif ML YAML parse: PASS")
PY

python3 - \
  "$repo_root/apps/sportif-ml/cvat/values.yaml" \
  "$repo_root/apps/sportif-ml/cvat/externalsecrets.yaml" \
  "$repo_root/platform/argocd/sportif-ml/cvat.yaml" <<'PY'
from pathlib import Path
import sys
import yaml

values = yaml.safe_load(Path(sys.argv[1]).read_text())
external_secrets = [
    item for item in yaml.safe_load_all(Path(sys.argv[2]).read_text()) if item
]
application = yaml.safe_load(Path(sys.argv[3]).read_text())

for component in ("postgresql", "redis"):
    if values[component].get("enabled") is not False:
        raise SystemExit(f"CVAT bundled {component} must remain disabled")
for component in ("analytics", "clickhouse", "nuclio", "traefik"):
    if values[component].get("enabled") is not False:
        raise SystemExit(f"CVAT bundled {component} must remain disabled")

if values["ingress"].get("enabled") is not False:
    raise SystemExit(
        "CVAT ingress must remain disabled until platform authentication is selected"
    )

backend = values["cvat"]["backend"]
frontend = values["cvat"]["frontend"]
opa = values["cvat"]["opa"]
kvrocks = values["cvat"]["kvrocks"]
initializer_annotations = backend.get("initializer", {}).get("annotations", {})
if initializer_annotations.get("argocd.argoproj.io/sync-options") != "Replace=true":
    raise SystemExit(
        "CVAT initializer Job requires Argo CD Replace=true because its pod "
        "template is immutable"
    )
if backend["defaultStorage"].get("storageClassName") != "longhorn":
    raise SystemExit("CVAT backend storage must use Longhorn")
if backend["defaultStorage"].get("accessModes") != ["ReadWriteMany"]:
    raise SystemExit("CVAT backend storage must be ReadWriteMany")
if kvrocks["defaultStorage"].get("storageClassName") != "longhorn":
    raise SystemExit("CVAT KVrocks storage must use Longhorn")
if kvrocks["defaultStorage"].get("accessModes") != ["ReadWriteOnce"]:
    raise SystemExit("CVAT KVrocks storage must be ReadWriteOnce")

for name, component in (
    ("backend", backend),
    ("frontend", frontend),
    ("opa", opa),
    ("kvrocks", kvrocks),
):
    resources = component.get("resources", {})
    if not resources.get("requests") or not resources.get("limits"):
        raise SystemExit(f"CVAT {name} requires explicit resource bounds")

postgres = next(
    item for item in external_secrets
    if item["metadata"]["name"] == "cvat-postgres-sync"
)
properties = {
    item["remoteRef"]["property"] for item in postgres["spec"]["data"]
}
if properties != {"CVAT_PGSQL_USER", "CVAT_PGSQL_PASSWORD"}:
    raise SystemExit("CVAT must use its dedicated PostgreSQL secret properties")

if application["metadata"].get("annotations", {}).get(
    "argocd.argoproj.io/sync-wave"
) != "1":
    raise SystemExit("CVAT child Application must follow the foundation sync wave")
chart_source = application["spec"]["sources"][0]
if chart_source.get("targetRevision") != (
    "125dd1e7006e7aadd8249c1256ec3a8945fcb191"
):
    raise SystemExit("CVAT source revision must remain immutable")
if "$values/apps/sportif-ml/cvat/values.yaml" not in (
    chart_source.get("helm", {}).get("valueFiles", [])
):
    raise SystemExit("CVAT child Application must consume repository values")

print("Sportif ML CVAT configuration validation: PASS")
PY

python3 - "$rendered" <<'PY'
from pathlib import Path
import sys
import yaml

resources = [
    resource
    for resource in yaml.safe_load_all(Path(sys.argv[1]).read_text())
    if resource
]
objects = {
    (resource["kind"], resource["metadata"]["name"]): resource
    for resource in resources
}

mlflow = objects.get(("Deployment", "mlflow"))
backend = objects.get(("PersistentVolumeClaim", "mlflow-backend"))
if mlflow and backend:
    access_modes = backend["spec"].get("accessModes", [])
    strategy = mlflow["spec"].get("strategy", {}).get("type", "RollingUpdate")
    if "ReadWriteOnce" in access_modes and strategy != "Recreate":
        raise SystemExit(
            "MLflow must use Recreate while SQLite is stored on its "
            "ReadWriteOnce PVC"
        )

    pod_spec = mlflow["spec"]["template"]["spec"]
    init_containers = {
        container["name"]: container
        for container in pod_spec.get("initContainers", [])
    }
    dependency_init = init_containers.get("install-s3-dependencies")
    if not dependency_init:
        raise SystemExit("MLflow requires install-s3-dependencies")
    args = dependency_init.get("args", [])
    for required_arg in ("--require-hashes", "--retries", "--timeout"):
        if required_arg not in args:
            raise SystemExit(
                f"MLflow dependency init container requires {required_arg}"
            )

print("Sportif ML rendered semantic validation: PASS")
PY

pycache="$(mktemp -d)"
trap 'rm -f "$rendered"; rm -rf "$pycache"' EXIT
PYTHONPYCACHEPREFIX="$pycache" python3 -m compileall -q \
  "$repo_root/apps/sportif-ml/trainer/sportif_ml"

echo "Sportif ML active manifest validation: PASS"
