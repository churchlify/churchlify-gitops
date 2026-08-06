# Manifest Directory Overhaul - Summary

## Changes Made

### File Reorganization

All deployment manifests now follow the `deployment-<service>.yaml` naming convention for consistency and clarity.

#### Files Renamed/Replaced:

| Old Name                         | New Name                           | Service                   | Status    |
| -------------------------------- | ---------------------------------- | ------------------------- | --------- |
| `deployment.yaml`                | `deployment-media-ingest.yaml`     | SRS RTMP Ingest           | ✓ Updated |
| `deployment-sport-track.yaml`    | `deployment-tracking.yaml`         | Tracking Worker           | ✓ Updated |
| `deployment-preview.yaml`        | `deployment-preview-streamer.yaml` | MediaMTX Preview Streamer | ✓ Updated |
| `deployment-live-stitcher.yaml`  | `deployment-stitch.yaml`           | Stitch Worker             | ✓ Updated |
| `deployment-reel-generator.yaml` | `deployment-reel.yaml`             | Reel Worker               | ✓ Updated |
| `recorder-deployment.yaml`       | `deployment-recorder.yaml`         | Recorder Worker           | ✓ Updated |

### Image Reference Updates

All obsolete `sport-track-stitch-v2:latest` references have been replaced with proper GHCR image tags:

#### New Image References:

```
✓ Recording:    ghcr.io/agogos-llc/sportif-recorder-worker:latest
28:✓ Tracking:     ghcr.io/agogos-llc/sportif-tracking-worker:latest
29:✓ Stitching:    ghcr.io/agogos-llc/sportif-stitch-worker:latest
30:✓ Reel Gen:     ghcr.io/agogos-llc/sportif-reel-worker:latest

◎ Third-party (unchanged):
  - ossrs/srs:5 (Media Ingest)
  - bluenviron/mediamtx:latest (Preview Streamer)
```

### Enhanced Manifest Structure

All deployment manifests now include:

- ✓ Proper labels and annotations
- ✓ Resource requests and limits
- ✓ Health checks (liveness & readiness probes)
- ✓ Service definitions
- ✓ ServiceAccounts for RBAC
- ✓ Consistent namespacing (sports-tracking)
- ✓ Anti-affinity rules where applicable

### Kustomization Updates

Updated `kustomization.yaml` to:

- Reference all new deployment files
- Organize resources by function (ingestion, processing, networking)
- Add clear comments for section grouping

## Deployment Services Map

```
┌─────────────────────────────────────────────┐
│          Ingestion & Preview Layer          │
├─────────────────────────────────────────────┤
│ • deployment-media-ingest.yaml              │
│   └─ SRS RTMP ingest (port 1935)            │
│                                              │
│ • deployment-preview-streamer.yaml          │
│   └─ MediaMTX preview streaming (8554)      │
│                                              │
│ • deployment-recorder.yaml                  │
│   └─ Recorder worker (segment recording)    │
├─────────────────────────────────────────────┤
│       Processing & Analysis Layer           │
├─────────────────────────────────────────────┤
│ • deployment-tracking.yaml                  │
│   └─ Tracking worker (ML-based tracking)    │
│                                              │
│ • deployment-stitch.yaml                    │
│   └─ Stitch worker (dual-camera stitching)  │
│                                              │
│ • deployment-reel.yaml                      │
│   └─ Reel worker (video composition)        │
└─────────────────────────────────────────────┘
```

## GitHub Actions Integration

The manifests are now ready to work with the new GitHub Actions workflows:

- **api-build.yml** → Builds and pushes API images to GHCR
- **workers-build.yml** → Builds and pushes all worker images to GHCR

When images are pushed, Kubernetes will pull the latest `latest` tag due to `imagePullPolicy: IfNotPresent` or can be configured for automatic rollouts with image update strategies.

## Deployment Instructions

To deploy all services:

```bash
# Validate manifests
kubectl apply -f manifest/ --dry-run=client

# Apply all manifests
kubectl apply -f manifest/

# Or use Kustomize
kustomize build manifest/ | kubectl apply -f -
```

## Monitoring Image Updates

For automatic image updates in Argo CD, configure the image update source:

```yaml
spec:
  source:
    plugin:
      name: kustomize
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

Then Argo will automatically detect and deploy new images pushed to GHCR.
