# Kustomize Setup for C06 — Complete

## Overview

Your C06 Kubernetes manifests are now organized for GitOps deployment via ArgoCD using Kustomize. This allows you to:

✅ Deploy C06 independently via `kubectl apply -k manifest/c06-ingest`  
✅ Deploy C06 via ArgoCD Applications for continuous GitOps sync  
✅ Add future chunks (C07, C09, C10) without touching C06 manifests  
✅ Override images and replicas for staging/production environments  

## Directory Structure

```
manifest/
├── README.md                        # Usage guide
├── KUSTOMIZE_SETUP.md              # This file
├── kustomization.yaml              # Root (references all chunks)
├── argocd-application-c06.yaml     # ArgoCD Application for C06
│
├── shared/                         # Shared resources (namespace)
│   ├── kustomization.yaml
│   └── namespace.yaml
│
├── c06-ingest/                     # ✅ C06 Ready for deployment
│   ├── kustomization.yaml
│   ├── deployment-recorder.yaml    # ConfigMap + Deployment + HPA + PDB
│   ├── deployment-media-ingest.yaml
│   ├── configmap-srs.yaml
│   ├── configmap-mediamtx.yaml
│   ├── pvc-recorder.yaml
│   └── rbac-recorder.yaml
│
├── c07-preview/                    # C07 stub (will populate in implementation)
├── c09-tracking/                   # C09 stub
└── c10-processing/                 # C10 stub
```

## Quick Start

### Deploy C06 Locally

```bash
# Validate manifest
kubectl kustomize manifest/c06-ingest

# Deploy to cluster
kubectl apply -k manifest/c06-ingest

# Verify deployment
kubectl get pods -n sports-tracking
kubectl logs -f deployment/recorder-worker -n sports-tracking
```

### Deploy via ArgoCD

```bash
# Install ArgoCD Application for C06
kubectl apply -f manifest/argocd-application-c06.yaml

# Watch sync progress
kubectl get application -n argocd
argocd app get sportif-c06-ingest
```

## What's Deployed

### Namespaces
- `sports-tracking` — Main namespace for all Sportif services

### ConfigMaps
- `recorder-config` — Recorder worker configuration (segment duration, retry logic, log level)
- `srs-config` — SRS media server configuration
- `mediamtx-config` — MediaMTX configuration

### PersistentVolumeClaims
- `recorder-segments-pvc` — 500Gi Longhorn storage for recorded segments

### Deployments
- `recorder-worker` — FFmpeg-based recorder (1-4 replicas, HPA enabled)
- `media-ingest` — SRS RTMP ingest server (1 replica)

### RBAC
- `recorder-worker` ServiceAccount, Role, and RoleBinding

### High Availability
- **HPA**: Recorder worker scales 1-4 replicas based on CPU (70%) / Memory (80%)
- **PDB**: Minimum 1 replica always available (pod disruption budget)
- **Pod Anti-Affinity**: Prefers separate nodes for redundancy

## Next Steps

### 1. Deploy to Your Cluster

```bash
# Option A: kubectl (direct)
kubectl apply -k manifest/c06-ingest

# Option B: ArgoCD (GitOps)
kubectl apply -f manifest/argocd-application-c06.yaml
```

### 2. Verify Health

```bash
# Check pods are running
kubectl get pods -n sports-tracking

# Recorder worker health
kubectl port-forward -n sports-tracking svc/recorder-worker 8000:8000
curl http://localhost:8000/health/ready

# SRS media ingest
kubectl port-forward -n sports-tracking svc/media-ingest 1935:1935
```

### 3. Run Integration Tests

```bash
# From the sportif repo root
python -m pytest workers/recorder/tests/test_recorder.py -v

# Or directly against deployed service
export RECORDER_URL=http://$(kubectl get svc -n sports-tracking recorder-worker -o jsonpath='{.status.loadBalancer.ingress[0].ip}'):8000
pytest workers/recorder/tests/test_recorder.py -v
```

### 4. Prepare for C07

When C07 (preview/stitching) is ready:

1. Create `manifest/c07-preview/` with its resources
2. Uncomment `- c07-preview` in `manifest/kustomization.yaml`
3. Optionally create ArgoCD Application for C07
4. Commit and push

## Customization Examples

### Override Recorder Image Tag

Edit `manifest/c06-ingest/kustomization.yaml`:

```yaml
images:
  - name: ghcr.io/agogos-llc/sportif-recorder-worker
    newTag: v1.0.0-rc1  # or sha256:abc123...
```

Then apply:

```bash
kubectl apply -k manifest/c06-ingest
```

### Scale Replicas for Load Testing

```yaml
replicas:
  - name: recorder-worker
    count: 3  # Scale to 3 (will auto-scale up to 4 via HPA)
```

### Create Environment-Specific Overlays

```bash
mkdir -p manifest/overlays/{staging,production}
```

**manifest/overlays/production/kustomization.yaml:**

```yaml
resources:
  - ../../c06-ingest

images:
  - name: ghcr.io/agogos-llc/sportif-recorder-worker
    newTag: v1.0.0  # Production tag

replicas:
  - name: recorder-worker
    count: 2

commonAnnotations:
  environment: production
```

Deploy:

```bash
kubectl apply -k manifest/overlays/production
```

## Troubleshooting

### Manifest Validation Error

```bash
# Dry-run to catch issues early
kubectl apply -k manifest/c06-ingest --dry-run=client

# Or with kustomize directly
kustomize build manifest/c06-ingest | kubectl apply --dry-run=client -f -
```

### Pods Not Starting

```bash
# Check events
kubectl describe pod -n sports-tracking -l app=recorder-worker

# View logs
kubectl logs -n sports-tracking -l app=recorder-worker --all-containers

# Check PVC is bound
kubectl get pvc -n sports-tracking
kubectl describe pvc recorder-segments-pvc -n sports-tracking
```

### ArgoCD Sync Issues

```bash
# Check Application status
argocd app get sportif-c06-ingest

# View sync logs
argocd app logs sportif-c06-ingest

# Force sync
argocd app sync sportif-c06-ingest
```

## References

- [Kustomize Documentation](https://kustomize.io/)
- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [C06 Implementation Checklist](../IMPLEMENTATION_CHECKLIST.md)
- [Recorder Worker Service](../workers/recorder/README.md)
