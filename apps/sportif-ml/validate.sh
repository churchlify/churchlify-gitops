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

pycache="$(mktemp -d)"
trap 'rm -f "$rendered"; rm -rf "$pycache"' EXIT
PYTHONPYCACHEPREFIX="$pycache" python3 -m compileall -q \
  "$repo_root/apps/sportif-ml/trainer/sportif_ml"

echo "Sportif ML active manifest validation: PASS"
