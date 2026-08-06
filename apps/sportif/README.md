# Sportif - GitOps Manifests

Sportif is a comprehensive sports tracking and streaming platform with multi-camera support, live stitching, and AI-based analytics.

## Component Structure

```
apps/sportif/
├── api/                    # NestJS Backend API (REST, WebSocket)
├── recorder/               # Media ingest (SRS, MediaMTX)
├── preview/                # Live two-camera stitching
├── tracking/               # ML-based tracking (optional GPU)
├── processing/             # Video stitching & reel generation
├── cleanup/                # Job cleanup worker
├── notification/           # Event notifications
└── uploader/               # Cloud storage uploads
```

## Deployment Architecture

### Shared Resources

- **Namespace**: `sports-tracking` (shared with `sport-track`)
- **Secrets**: `api-secrets` and `sportif-secrets` (synced by Sportif ExternalSecrets)
- **Database**: PostgreSQL (shared infrastructure)
- **Redis**: Cache layer (shared infrastructure)
- **Media stack**: SRS, MediaMTX, media services, and `sports-media-pvc-rwx` are owned by Sportif

### Single Application Deployment

All components are managed by a single consolidated Sportif ArgoCD Application:

```bash
kubectl apply -f platform/argocd/sportif/app.yaml
```

This ensures:

- ✅ No resource conflicts with sport-track or other apps
- ✅ Components can share resources within Sportif namespace
- ✅ Single sync point for all 8 components
- ✅ Simplified rollbacks and dependency management

## Components Overview

### API (`api/`)

- **Port**: 3000
- **Domain**: sportif.churchlify.com (TLS via cert-manager)
- **Replicas**: 2 (production), 1 (staging)
- **Resources**: 250m-1000m CPU, 256Mi-1Gi memory
- **Configuration**: ConfigMap for non-sensitive values
- **Secrets**: JWT_SECRET (from api-secrets)
- **RBAC**: ServiceAccount with ConfigMap/Pod read access

### Recorder (`recorder/`)

- **Port**: 8000
- **Replicas**: 1 (with HPA: 1-4)
- **Storage**: 500Gi Longhorn PVC for segments
- **Components**:
  - SRS (Simple RTMP Server)
  - MediaMTX (RTSP/DASH streaming)
  - Media-ingest worker
- **Resources**: 500m-2000m CPU, 512Mi-2Gi memory
- **Metrics**: Prometheus at /metrics

### Preview (`preview/`)

- **Type**: FastAPI worker
- **Function**: Live two-camera stitching
- **Resources**: 1-4 cores CPU, 1-4Gi memory
- **Health**: Exec-based liveness/readiness probes
- **Config**: API_BASE_URL, MEDIAMTX_OUTPUT_BASE

### Tracking (`tracking/`)

- **Type**: ML-based computer vision
- **GPU Support**: Preferred but not required (nodeAffinity)
- **Resources**: 1-4 cores CPU, 2-8Gi memory
- **Note**: GPU resource requests commented out; uncomment if nodes available

### Processing (`processing/`)

- **Components**: Stitch worker + Reel generator
- **Storage**: 100Gi Longhorn PVC for temporary files
- **Function**: Video processing (concatenation, encoding)
- **Resources**: 1-4 cores CPU, 1-4Gi memory

### Cleanup (`cleanup/`)

- **Function**: Remove completed job resources
- **RBAC**: Pod delete, PVC read permissions
- **Resources**: 100m-500m CPU, 128Mi-512Mi memory

### Notification (`notification/`)

- **Function**: Event webhooks and notifications
- **Resources**: 100m-500m CPU, 128Mi-512Mi memory
- **Config**: ConfigMap read access

### Uploader (`uploader/`)

- **Function**: Cloud storage (S3, Azure, etc.)
- **Storage**: PVC read access
- **Resources**: 250m-1000m CPU, 256Mi-1Gi memory

## Secrets Management

### Required Secrets in sports-tracking

These secrets are created by the Sportif ExternalSecret resources before workloads start:

```bash
kubectl get secret api-secrets sportif-secrets -n sports-tracking
```

### Optional Secrets (per component)

- `recorder-credentials`: RTMP auth
- `s3-credentials`: Cloud storage access
- `notification-webhooks`: Webhook URLs

## Configuration

### ConfigMaps

- `api-config`: Non-sensitive API configuration
- `recorder-config`: Recorder segment settings
- `configmap-srs.yaml`: SRS server configuration
- `configmap-mediamtx.yaml`: MediaMTX streaming settings

### Network

- **Ingress**: API at sportif.churchlify.com
- **Services**: ClusterIP for internal communication
- **Rate Limiting**: 100 requests/minute on API ingress

## Deployment

### Prerequisites

1. Namespace `sports-tracking` already created (shared with sport-track)
2. External Secrets Operator can create `api-secrets` and `sportif-secrets`
3. Longhorn storage provisioned
4. ArgoCD installed and configured

### Deploy Sportif

```bash
# From app-ops repository
kubectl apply -f platform/argocd/sportif/app.yaml

# Verify deployment
kubectl get applications -n argocd | grep sportif

# Check Sportif application status
argocd app get sportif
argocd app wait sportif
```

All 8 components (api, recorder, preview, tracking, processing, cleanup, notification, uploader) are deployed together as a single application.

## Monitoring

### Check Pod Status

```bash
kubectl get pods -n sports-tracking -l part-of=sportif
kubectl get pods -n sports-tracking -l app=api
kubectl get pods -n sports-tracking -l app=recorder-worker
```

### View Logs

```bash
# API logs
kubectl logs -n sports-tracking -l app=api --all-containers=true -f

# Recorder logs
kubectl logs -n sports-tracking -l app=recorder-worker -f

# Preview worker logs
kubectl logs -n sports-tracking -l app=preview-worker -f
```

### Health Checks

```bash
# API health
curl https://sportif.churchlify.com/health/live
curl https://sportif.churchlify.com/health/ready

# Recorder health
curl http://recorder-worker:8000/health/live
```

## Image Updates

Images are automatically updated by the CI/CD pipeline in the sportif repository:

1. Push to main branch → Images built as `sha-{commit-sha}`
2. Workflow updates production overlays with new image tags
3. ArgoCD detects changes and auto-syncs (2-3 minute delay)

### Manual Image Update

```bash
cd apps/sportif/api/overlays/production
kustomize edit set image ghcr.io/agogos-llc/sportif-api=ghcr.io/agogos-llc/sportif-api:sha-newsha
git add kustomization.yaml
git commit -m "update sportif-api image"
git push
```

## Scaling

### Horizontal Pod Autoscaling

**Recorder**: HPA configured (1-4 replicas)

```bash
kubectl get hpa -n sports-tracking
kubectl describe hpa recorder-worker-hpa -n sports-tracking
```

**API**: Manual scaling in overlays

```bash
# Staging: 1 replica (in overlays/staging/kustomization.yaml)
# Production: 2 replicas (in overlays/production/kustomization.yaml)
```

### Vertical Scaling

Adjust resource requests/limits in component base deployments:

```yaml
resources:
  requests:
    cpu: 250m
    memory: 256Mi
  limits:
    cpu: 1000m
    memory: 1Gi
```

## Troubleshooting

### Pod CrashLoopBackOff

1. Check logs: `kubectl logs -n sports-tracking <pod-name>`
2. Check events: `kubectl describe pod -n sports-tracking <pod-name>`
3. Verify secrets: `kubectl get secrets -n sports-tracking`

### PVC Not Binding

1. Check Longhorn status: `kubectl get pvc -n sports-tracking`
2. Check node storage: `kubectl describe nodes`

### ArgoCD Sync Failing

1. Check kustomize build: `kustomize build apps/sportif/api/overlays/production`
2. Check for invalid YAML: `kubectl apply -f ... --dry-run=client`
3. ArgoCD logs: `argocd app logs sportif-api`

### Network/Ingress Issues

1. Check ingress: `kubectl get ingress -n sports-tracking`
2. Check DNS: `nslookup sportif.churchlify.com`
3. Check cert: `kubectl get certificate -n sports-tracking`

## Isolation from sport-track

- **Consolidated Application**: Single Sportif app manages all 8 components
- **No resource conflicts**: Removed duplicate mediamtx, srs, preview-streamer configs
- **Shared media stack**: Sportif owns SRS, MediaMTX, their services, and shared storage
- **Independent scaling**: Sportif scales independently
- **Storage**: Sportif-owned shared RWX PVC plus component-specific PVCs
- **RBAC isolation**: Service accounts are Sportif-specific
- **Namespace sharing**: sports-tracking namespace shared with sport-track
- **Network policies**: Can be added if stricter isolation needed

### Media Stack Ownership

Sportif is the source of truth for the shared media infrastructure:

- **SRS (RTMP Server)**: Managed by Sportif's shared component
- **MediaMTX (Streaming)**: Managed by Sportif's shared component
- **Services**: `media-ingest-service` and `preview-streamer-service` are published by Sportif
- **Storage**: `sports-media-pvc-rwx` is managed by Sportif and consumed by both applications
- **sport-track**: Consumes Sportif's media services and shared storage; it must not redeploy them

This design avoids duplicate ownership and allows Sportif and sport-track to coexist in the shared namespace.

## Related Documentation

- [GITOPS_SETUP.md](../../../sportif/GITOPS_SETUP.md) - CI/CD pipeline documentation
- [sport-track](../sport-track) - Related sports platform
- [app-ops](../..) - Platform operations repository
