# Sportif Kubernetes Manifests

This directory contains Kustomize-organized Kubernetes manifests for the Sportif platform, structured for GitOps deployment via ArgoCD.

## Directory Structure

```
manifest/
├── kustomization.yaml              # Root kustomization (references all chunks)
├── argocd-application-*.yaml       # ArgoCD Application resources
├── namespace.yaml                  # Shared namespace definition
├── service.yaml                    # Shared API service
├── service-ai.yaml                 # Shared AI service
├── ingress.yaml                    # Shared ingress configuration
├── pvc-rwx.yaml                    # Shared persistent volume claims
│
├── base/                           # Shared base resources
│   └── kustomization.yaml          # References all shared resources
│
├── c06-ingest/                     # C06 — Ingest, segmented recording, and stream health
│   ├── kustomization.yaml
│   ├── deployment-recorder.yaml    # Recorder worker + ConfigMaps + HPA + PDB
│   ├── deployment-media-ingest.yaml
│   ├── configmap-srs.yaml
│   ├── configmap-mediamtx.yaml
│   ├── pvc-recorder.yaml
│   └── rbac-recorder.yaml
│
├── c07-preview/                    # C07 — Preview and live two-camera stitching (NOT STARTED)
│   └── kustomization.yaml
│
├── c09-tracking/                   # C09 — AI tracking, jersey OCR, face matching (NOT STARTED)
│   └── kustomization.yaml
│
└── c10-processing/                 # C10 — Reels, captions, upload, cleanup (NOT STARTED)
    └── kustomization.yaml
```

## Usage

### Prerequisites

- Kubernetes cluster 1.24+
- `kubectl` configured to target your cluster
- Kustomize 4.0+ (or `kubectl apply -k`)
- (Optional) ArgoCD 2.0+ for GitOps-style deployments

### Option 1: Deploy C06 Only (Recommended for Testing)

```bash
# Validate the manifest
kubectl kustomize manifest/c06-ingest

# Deploy
kubectl apply -k manifest/c06-ingest

# Check status
kubectl get pods -n sports-tracking
kubectl logs -f deployment/recorder-worker -n sports-tracking
```

### Option 2: Deploy via ArgoCD

**For C06 only:**

```bash
kubectl apply -f manifest/argocd-application-c06.yaml
```

**For full stack (when ready):**

```bash
kubectl apply -f manifest/argocd-application-c06.yaml
# Then uncomment chunks in manifest/kustomization.yaml and commit
```

### Option 3: Deploy Full Stack (All Chunks)

```bash
# Uncomment chunks in manifest/kustomization.yaml first
kubectl apply -k manifest
```

## Chunk Dependencies

```
C06 ──→ C07 ──→ C08 ──→ C09
              │           │
              └─────┬─────┘
                    ↓
                   C10 ──→ C11
```

- **C06** is independent and can be deployed first
- **C07** depends on C06 (preview stitching needs stable ingest)
- **C08** depends on C06 and C07 (orchestration worker)
- **C09** depends on C03 and C08 (tracking queue)
- **C10** depends on C08 and C09 (reels/upload)
- **C11** depends on C08 and C10 (notifications)

## Enabling Chunks in kustomization.yaml

As each chunk is completed, uncomment its base entry:

```yaml
bases:
  - c06-ingest          # ✓ C06 — READY
  # - c07-preview       # C07 — uncomment when ready
  # - c09-tracking      # C09 — uncomment when ready
  # - c10-processing    # C10 — uncomment when ready
```

## Customization

### Environment-Specific Overlays

To create staging/production overlays:

```bash
mkdir -p manifest/overlays/{staging,production}
```

**manifest/overlays/staging/kustomization.yaml:**

```yaml
bases:
  - ../../c06-ingest

images:
  - name: ghcr.io/agogos-llc/sportif-recorder-worker
    newTag: v1.0.0-staging

replicas:
  - name: recorder-worker
    count: 2  # More replicas for load testing
```

**manifest/overlays/production/kustomization.yaml:**

```yaml
bases:
  - ../../c06-ingest

images:
  - name: ghcr.io/agogos-llc/sportif-recorder-worker
    newTag: v1.0.0

commonAnnotations:
  promotion.argocd.argoproj.io/from: staging
```

Deploy with:

```bash
kubectl apply -k manifest/overlays/staging
kubectl apply -k manifest/overlays/production
```

### Image Updates

Update the image tag in any `kustomization.yaml`:

```yaml
images:
  - name: ghcr.io/agogos-llc/sportif-recorder-worker
    newTag: v1.2.3
```

Then apply:

```bash
kubectl apply -k manifest/c06-ingest
```

Or use ArgoCD Image Updater (configured in Application spec):

```yaml
spec:
  source:
    plugin:
      env:
        - name: ARGOCD_COMPARE_RESULT
          value: 'true'
  # ... ArgoCD Image Updater will automatically update images in registry
```

## Common Tasks

### Check Rollout Status

```bash
kubectl rollout status deployment/recorder-worker -n sports-tracking
```

### View Logs

```bash
# Recorder worker
kubectl logs -f deployment/recorder-worker -n sports-tracking

# All C06 pods
kubectl logs -f -l chunk=c06-ingest -n sports-tracking
```

### Verify PVC

```bash
kubectl get pvc -n sports-tracking
kubectl describe pvc recorder-segments-pvc -n sports-tracking
```

### Port Forward for Local Testing

```bash
# Recorder worker API
kubectl port-forward -n sports-tracking svc/recorder-worker 8000:8000

# Media ingest RTMP
kubectl port-forward -n sports-tracking svc/media-ingest 1935:1935
```

### Rollback a Chunk

```bash
# Revert the manifest changes in git, then reapply
git revert <commit-hash>
kubectl apply -k manifest/c06-ingest
```

Or via ArgoCD:

```bash
# View sync history
argocd app history sportif-c06-ingest

# Rollback to previous sync
argocd app rollback sportif-c06-ingest <revision>
```

## Troubleshooting

### Manifest Validation

```bash
# Validate kustomization
kubectl kustomize manifest/c06-ingest | kubectl apply --dry-run=client -f -

# Or with kustomize directly
kustomize build manifest/c06-ingest | kubectl apply --dry-run=client -f -
```

### Missing Resources

If a chunk references a resource that doesn't exist:

```bash
# Check what resources kustomize will generate
kubectl kustomize manifest/c06-ingest | grep -E "^kind:|^  name:"
```

### Namespace Issues

```bash
# Verify namespace exists
kubectl get ns sports-tracking

# Check labels
kubectl get ns sports-tracking -o yaml
```

## Contributing

When implementing a new chunk:

1. Create `manifest/<chunk-id>/kustomization.yaml` with:
   - Base reference
   - Chunk-specific resources
   - Appropriate labels and annotations
   - Image tags and replica counts

2. Add chunk-specific manifests (deployments, configmaps, rbac)

3. Update root `kustomization.yaml` with commented base reference

4. Test locally:
   ```bash
   kubectl apply -k manifest/<chunk-id>
   ```

5. Verify in staging via ArgoCD

6. Update `IMPLEMENTATION_CHECKLIST.md` with deployment evidence

## References

- [Kustomize Documentation](https://kustomize.io/)
- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [Kubernetes Manifests](./manifest/)
- [C06 Deployment Guide](../docs/C06-DEPLOYMENT.md)
