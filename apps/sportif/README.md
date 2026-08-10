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
- **Secret**: `sportif-secrets` (synced by Sportif ExternalSecret)
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
- **Secrets**: JWT_SECRET and IMMICH_API_KEY (from sportif-secrets)
- **RBAC**: ServiceAccount with ConfigMap/Pod read access

### Recorder (`recorder/`)

- **Port**: 8000
- **Replicas**: 1 (with HPA: 1-4)
- **Storage**: 100Gi Longhorn PVC for segments
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
kubectl get secret sportif-secrets -n sports-tracking
```

The `platform-secrets` ClusterSecretStore reads `global-db-secrets` from the
`platform` namespace. Add `JWT_SECRET`, `IMMICH_API_KEY`, and `TURN_SECRET` to
that source Secret using your approved secret-management process. Sportif and
`platform/coturn` must receive the same `TURN_SECRET` so MediaMTX can generate
temporary TURN credentials. If the media pipeline uses it, add `stream-key`
there as well. Do not commit any of these values to Git.

The `platform-secrets` ClusterSecretStore must expose `JWT_SECRET`,
`IMMICH_API_KEY`, and `TURN_SECRET` in the `global-db-secrets` remote object,
along with the PostgreSQL properties used to build `DATABASE_URL`.

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
- **Preview Ingress**: Match-scoped WebRTC and HLS previews at preview.sportif.churchlify.com
- **Services**: ClusterIP for internal communication
- **Rate Limiting**: 100 requests/minute on API ingress

## Deployment

### Prerequisites

1. Namespace `sports-tracking` already created (shared with sport-track)
2. External Secrets Operator can create `sportif-secrets`
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

### Preview Stream Testing

Preview streams are match-scoped. For a match ID such as `match-123`, use
these MediaMTX stream paths:

- Left camera: `match-123_left`
- Right camera: `match-123_right`
- Stitched preview: `match-123_stitched_preview`

The preview ingress exposes each stream through both WebRTC and HLS. It strips
the `/webrtc` or `/hls` routing prefix before forwarding the match-scoped path
to MediaMTX.

#### Playback URLs

WebRTC player endpoints:

```text
https://preview.sportif.churchlify.com/webrtc/match-123_left
https://preview.sportif.churchlify.com/webrtc/match-123_right
https://preview.sportif.churchlify.com/webrtc/match-123_stitched_preview
```

HLS playlist endpoints:

```text
https://preview.sportif.churchlify.com/hls/match-123_left/index.m3u8
https://preview.sportif.churchlify.com/hls/match-123_right/index.m3u8
https://preview.sportif.churchlify.com/hls/match-123_stitched_preview/index.m3u8
```

Internal publishers publish the same match-scoped paths over RTSP:

```text
rtsp://preview-streamer-service.sports-tracking.svc.cluster.local:8554/match-123_left
rtsp://preview-streamer-service.sports-tracking.svc.cluster.local:8554/match-123_right
rtsp://preview-streamer-service.sports-tracking.svc.cluster.local:8554/match-123_stitched_preview
```

#### Verify Kubernetes resources

After Argo CD syncs the Sportif application, verify the deployment, service,
ingress, certificate, endpoints, and MediaMTX logs:

```bash
kubectl get deploy preview-streamer -n sports-tracking
kubectl get svc preview-streamer-service -n sports-tracking
kubectl get ingress preview-streamer-ingress -n sports-tracking
kubectl describe ingress preview-streamer-ingress -n sports-tracking
kubectl get certificate sportif-preview-tls -n sports-tracking
kubectl get endpoints preview-streamer-service -n sports-tracking
kubectl get deploy coturn -n platform
kubectl get svc coturn -n platform
kubectl logs -n sports-tracking deploy/preview-streamer -f
```

The internal service must expose RTSP `8554`, WebRTC signaling `8889`, HLS
`8888`, and RTMP `1936`. The ingress must route `/webrtc` to `8889` and `/hls`
to `8888`. WebRTC media relays through the existing `platform/coturn`
deployment when a direct peer path is unavailable.

#### Verify DNS and TLS

Create or verify both DNS records before testing:

- Point `preview.sportif.churchlify.com` at the nginx ingress controller's
  public address. This carries HTTPS WebRTC signaling and HLS.
- Keep the existing `turn.churchlify.com` record pointed at the
  `platform/coturn` LoadBalancer. MediaMTX and browser clients use TURN on TCP
  `3478`, with relay ports `49160-49200`.

Retrieve the addresses and verify DNS and HTTPS:

```bash
kubectl get ingress preview-streamer-ingress -n sports-tracking
kubectl get svc coturn -n platform
nslookup preview.sportif.churchlify.com
nslookup turn.churchlify.com
curl -vI https://preview.sportif.churchlify.com/hls/match-123_left/index.m3u8
```

A playlist request can return a not-found response until the corresponding
match-scoped stream is being published.

#### Publish an end-to-end test stream

From a workstation with `kubectl` and FFmpeg, forward the internal RTSP port:

```bash
kubectl port-forward -n sports-tracking svc/preview-streamer-service 8554:8554
```

In a second terminal, publish a generated video and audio test pattern as the
left camera for `match-123`:

```bash
ffmpeg -re \
  -f lavfi -i testsrc=size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=1000:sample_rate=48000 \
  -c:v libx264 -preset veryfast -tune zerolatency \
  -pix_fmt yuv420p -c:a aac \
  -f rtsp rtsp://localhost:8554/match-123_left
```

Leave FFmpeg running while testing playback. Repeat with
`match-123_right` or `match-123_stitched_preview` to test the other paths.

#### Test HLS playback

Check that the playlist and media segments are available:

```bash
curl -fsS https://preview.sportif.churchlify.com/hls/match-123_left/index.m3u8
ffplay https://preview.sportif.churchlify.com/hls/match-123_left/index.m3u8
```

The HLS URL can also be loaded by an HLS-compatible browser player, VLC, or a
frontend using `hls.js`.

#### Test WebRTC playback

Open the MediaMTX WebRTC player endpoint in a browser:

```text
https://preview.sportif.churchlify.com/webrtc/match-123_left
```

For a WHEP-compatible client, use:

```text
https://preview.sportif.churchlify.com/webrtc/match-123_left/whep
```

HTTPS ingress exposes MediaMTX WebRTC signaling. WebRTC media uses
the existing coturn deployment at `turn.churchlify.com:3478` with TURN REST
shared-secret authentication. MediaMTX generates temporary credentials from
`TURN_SECRET`; the secret is synchronized into `sportif-secrets` from the same
`global-db-secrets` property used by `platform/coturn`. If the player page
loads but media does not start, verify TURN DNS, TCP `3478`, and relay ports
`49160-49200` through the load balancer, firewall, and upstream router. HLS
remains available over standard HTTPS when WebRTC connectivity is unavailable.

The platform mediasoup deployment is a separate SFU and is not in the preview
playback path. Using it for Sportif previews would require application-level
mediasoup signaling plus an RTSP/RTP producer bridge for each match-scoped
stream; it cannot be selected as a drop-in MediaMTX ICE server.

#### Troubleshooting previews

```bash
# Confirm nginx routes and TLS status.
kubectl describe ingress preview-streamer-ingress -n sports-tracking

# Confirm the shared TURN service has an external address.
kubectl get svc coturn -n platform

# Confirm MediaMTX received the TURN secret without printing its value.
kubectl get secret sportif-secrets -n sports-tracking \
  -o jsonpath='{.data.TURN_SECRET}' | grep -q . && echo TURN_SECRET_PRESENT

# Confirm MediaMTX sees the publisher and playback requests.
kubectl logs -n sports-tracking deploy/preview-streamer --tail=200

# Bypass ingress to isolate MediaMTX or ingress failures.
kubectl port-forward -n sports-tracking svc/preview-streamer-service 8888:8888 8889:8889
curl -vI http://localhost:8888/match-123_left/index.m3u8
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
