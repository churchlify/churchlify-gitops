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
  echo "WorkflowTemplate must remain staged until the trainer and data flow are complete" >&2
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
  "$repo_root/apps/sportif-ml/argo-workflows/values.yaml" \
  "$repo_root/platform/argocd/sportif-ml/argo-workflows.yaml" \
  "$repo_root/apps/sportif-ml/rbac.yaml" \
  "$repo_root/apps/sportif-ml/pipeline/workflows.yaml" <<'PY'
from pathlib import Path
import sys
import yaml

values = yaml.safe_load(Path(sys.argv[1]).read_text())
application = yaml.safe_load(Path(sys.argv[2]).read_text())
rbac = [item for item in yaml.safe_load_all(Path(sys.argv[3]).read_text()) if item]
workflow = yaml.safe_load(Path(sys.argv[4]).read_text())

if values.get("singleNamespace") is not True:
    raise SystemExit("Argo Workflows controller must remain namespace-scoped")
if values.get("createAggregateRoles") is not False:
    raise SystemExit("Argo Workflows aggregate ClusterRoles must remain disabled")
if values.get("server", {}).get("enabled") is not False:
    raise SystemExit("Argo Server must remain disabled until authenticated access is designed")
if values.get("crds") != {"install": True, "keep": True}:
    raise SystemExit("Argo Workflows CRDs must be installed and retained")

workflow_values = values.get("workflow", {})
if workflow_values.get("serviceAccount", {}).get("create") is not False:
    raise SystemExit("The chart must not create the Sportif workflow ServiceAccount")
if workflow_values.get("rbac", {}).get("create") is not False:
    raise SystemExit("The chart must not create Sportif workflow executor RBAC")

controller = values.get("controller", {})
if controller.get("clusterWorkflowTemplates", {}).get("enabled") is not False:
    raise SystemExit("ClusterWorkflowTemplate controller access must remain disabled")
if controller.get("nodeSelector") != {
    "kubernetes.io/os": "linux",
    "node-role.kubernetes.io/worker": "worker",
}:
    raise SystemExit("Argo Workflows controller must target normal Linux workers")
if not controller.get("resources", {}).get("requests") or not controller.get(
    "resources", {}
).get("limits"):
    raise SystemExit("Argo Workflows controller requires explicit resource bounds")

controller_tag = controller.get("image", {}).get("tag")
if controller_tag != (
    "v3.6.10@sha256:"
    "c289d4cb4592022d48faf0085d657cee8a96ff49f0e978c7a1672736be7f2083"
):
    raise SystemExit("Argo Workflows controller image must remain digest-pinned")
executor_tag = values.get("executor", {}).get("image", {}).get("tag")
if executor_tag != (
    "v3.6.10@sha256:"
    "701da40bf65f9699ea7a1e732dbca696590fecb835d1c8719f000fb60aa30133"
):
    raise SystemExit("Argo Workflows executor image must remain digest-pinned")

if application["metadata"].get("annotations", {}).get(
    "argocd.argoproj.io/sync-wave"
) != "2":
    raise SystemExit("Argo Workflows must follow the foundation and CVAT sync waves")
chart_source = application["spec"]["sources"][0]
if chart_source.get("repoURL") != "https://argoproj.github.io/argo-helm":
    raise SystemExit("Argo Workflows must use the official chart repository")
if chart_source.get("chart") != "argo-workflows":
    raise SystemExit("Unexpected Argo Workflows chart name")
if chart_source.get("targetRevision") != "0.45.20":
    raise SystemExit("Argo Workflows chart must remain pinned to 0.45.20")
if "$values/apps/sportif-ml/argo-workflows/values.yaml" not in chart_source.get(
    "helm", {}
).get("valueFiles", []):
    raise SystemExit("Argo Workflows Application must consume repository values")

objects = {(item["kind"], item["metadata"]["name"]): item for item in rbac}
service_account = objects.get(("ServiceAccount", "sportif-ml-pipeline"))
role = objects.get(("Role", "sportif-ml-pipeline"))
binding = objects.get(("RoleBinding", "sportif-ml-pipeline"))
if not all((service_account, role, binding)):
    raise SystemExit("Sportif workflow executor identity and RBAC are required")
if role.get("rules") != [{
    "apiGroups": ["argoproj.io"],
    "resources": ["workflowtaskresults"],
    "verbs": ["create", "patch"],
}]:
    raise SystemExit("Sportif workflow executor RBAC exceeds the required minimum")

templates = {item["name"]: item for item in workflow["spec"]["templates"]}
train = templates["train"]["container"]
if train.get("nodeSelector") != {"accelerator": "nvidia-v100"}:
    raise SystemExit("Training must use the verified live GPU selector")
if train.get("resources", {}).get("limits", {}).get("nvidia.com/gpu") != "1":
    raise SystemExit("Training must request exactly one Kubernetes GPU")

print("Sportif ML Argo Workflows configuration validation: PASS")
PY

python3 - \
  "$repo_root/apps/sportif-ml/cvat/values.yaml" \
  "$repo_root/apps/sportif-ml/cvat/externalsecrets.yaml" \
  "$repo_root/platform/argocd/sportif-ml/cvat.yaml" \
  "$repo_root/apps/sportif-ml/cvat/initializer-config.yaml" <<'PY'
from pathlib import Path
import sys
import yaml

values = yaml.safe_load(Path(sys.argv[1]).read_text())
external_secrets = [
    item for item in yaml.safe_load_all(Path(sys.argv[2]).read_text()) if item
]
application = yaml.safe_load(Path(sys.argv[3]).read_text())
initializer_config = yaml.safe_load(Path(sys.argv[4]).read_text())

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
if initializer_annotations.get("argocd.argoproj.io/hook") != "PreSync":
    raise SystemExit(
        "CVAT initializer must run as an Argo CD PreSync hook"
    )
delete_policies = set(
    initializer_annotations.get("argocd.argoproj.io/hook-delete-policy", "").split(",")
)
if delete_policies != {"BeforeHookCreation", "HookSucceeded"}:
    raise SystemExit("CVAT initializer requires safe hook deletion policies")
initializer = backend.get("initializer", {})
mounts = initializer.get("additionalVolumeMounts", [])
if not any(
    mount.get("mountPath") == "/etc/cvat/init.d/10-no-analytics.sh"
    and mount.get("readOnly") is True
    for mount in mounts
):
    raise SystemExit("CVAT initializer must mount the no-analytics override")
script = initializer_config.get("data", {}).get("10-no-analytics.sh", "")
for required in ("cmd_init()", "manage.py migrate", "manage.py migrateredis"):
    if required not in script:
        raise SystemExit(f"CVAT initializer override is missing {required}")
for forbidden in ("wait_for_clickhouse", "components/analytics/clickhouse/init.py"):
    if forbidden in script:
        raise SystemExit("CVAT no-analytics initializer must not invoke ClickHouse")
if backend["defaultStorage"].get("storageClassName") != "longhorn":
    raise SystemExit("CVAT backend storage must use Longhorn")
if backend["defaultStorage"].get("accessModes") != ["ReadWriteMany"]:
    raise SystemExit("CVAT backend storage must be ReadWriteMany")
if kvrocks["defaultStorage"].get("storageClassName") != "longhorn":
    raise SystemExit("CVAT KVrocks storage must use Longhorn")
if kvrocks["defaultStorage"].get("accessModes") != ["ReadWriteOnce"]:
    raise SystemExit("CVAT KVrocks storage must be ReadWriteOnce")
if kvrocks["defaultStorage"].get("size") != "100Gi":
    raise SystemExit("CVAT must preserve the existing 100Gi KVrocks claim")

worker_expression = {
    "key": "node-role.kubernetes.io/worker",
    "operator": "In",
    "values": ["worker"],
}
for name, component in (
    ("backend", backend),
    ("frontend", frontend),
    ("opa", opa),
    ("kvrocks", kvrocks),
):
    terms = (
        component.get("affinity", {})
        .get("nodeAffinity", {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", {})
        .get("nodeSelectorTerms", [])
    )
    expressions = [
        expression
        for term in terms
        for expression in term.get("matchExpressions", [])
    ]
    if worker_expression not in expressions:
        raise SystemExit(f"CVAT {name} must target normal worker nodes")

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
sync_options = application["spec"]["syncPolicy"].get("syncOptions", [])
if "RespectIgnoreDifferences=true" not in sync_options:
    raise SystemExit("CVAT sync must respect scoped ignored differences")
ignored = application["spec"].get("ignoreDifferences", [])
if ignored != [{
    "group": "apps",
    "kind": "StatefulSet",
    "name": "cvat-kvrocks",
    "jsonPointers": ["/spec/volumeClaimTemplates"],
}]:
    raise SystemExit("CVAT may ignore only the immutable KVrocks claim template")

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
