# Sportif Deployment Guide

Complete guide for deploying Sportif to the sports-tracking namespace in app-ops.

## Pre-Deployment Checklist

- [ ] sports-tracking namespace exists (shared with sport-track)
- [ ] External Secrets Operator can access `platform-secrets`
- [ ] Remote `sportif-secrets` contains `JWT_SECRET` and `IMMICH_API_KEY`
- [ ] Longhorn storage provisioned
- [ ] ArgoCD installed and configured
- [ ] cert-manager installed for TLS
- [ ] nginx-ingress installed

## Step 1: Create Required Secrets

```bash
# Sportif creates api-secrets and sportif-secrets through External Secrets.
# Verify the generated secrets after Argo CD sync:
kubectl get secret api-secrets sportif-secrets -n sports-tracking

# Verify
kubectl get secrets -n sports-tracking | grep api-secrets
```

## Step 2: Verify Shared Resources

```bash
# Check namespace exists
kubectl get ns sports-tracking

# Check existing resources in the shared namespace
kubectl get pods -n sports-tracking
kubectl get pvc -n sports-tracking

# Note: Sportif owns the shared media stack and secrets; sport-track consumes
# the media services and RWX PVC exposed in this namespace.
```

## Step 3: Deploy via ArgoCD

### Deploy Sportif (Single Consolidated Application)

```bash
cd app-ops

# Deploy Sportif - single application managing all 8 components
kubectl apply -f platform/argocd/sportif/app.yaml

# Verify application created
kubectl get applications -n argocd | grep sportif
argocd app get sportif
```

All components (api, recorder, preview, tracking, processing, cleanup, notification, uploader) are deployed together as a single unified Sportif application. This ensures:

- No resource conflicts with other apps (sport-track, etc.)
- Components can share resources within the Sportif namespace
- Single sync point for the entire platform
- Simplified rollbacks and dependency management

## Step 4: Monitor Deployment Progress

### Watch ArgoCD Sync

```bash
# Watch Sportif application
watch 'kubectl get applications -n argocd | grep sportif'

# Check Sportif application details
argocd app get sportif

# Watch sync progress
argocd app wait sportif

# View detailed sync status
argocd app get sportif --show-operation
```

### Monitor Pod Deployment

```bash
# Watch all Sportif pods
watch 'kubectl get pods -n sports-tracking -l part-of=sportif'

# Check specific component
kubectl get pods -n sports-tracking -l app=api
kubectl get pods -n sports-tracking -l app=recorder-worker
kubectl get pods -n sports-tracking -l app=preview-worker

# View pod events
kubectl describe pod -n sports-tracking <pod-name>
```

### Check Persistent Volumes

```bash
# Check PVC binding
kubectl get pvc -n sports-tracking

# Details
kubectl describe pvc recorder-segments-pvc -n sports-tracking
kubectl describe pvc processing-work-pvc -n sports-tracking
```

## Step 5: Verify Deployments

### API (REST Backend)

```bash
# Check API pod
kubectl get pods -n sports-tracking -l app=api

# Test health endpoint (port-forward)
kubectl port-forward -n sports-tracking svc/api-service 3000:3000 &
curl http://localhost:3000/health/live
curl http://localhost:3000/health/ready

# Test via ingress (if DNS is configured)
curl https://sportif.churchlify.com/health/live
```

### Recorder (Media Ingest)

```bash
# Check recorder pod
kubectl get pods -n sports-tracking -l app=recorder-worker

# Check HPA status
kubectl get hpa -n sports-tracking
kubectl describe hpa recorder-worker-hpa -n sports-tracking

# Test health
kubectl port-forward -n sports-tracking svc/recorder-worker 8000:8000 &
curl http://localhost:8000/health/live
```

### Preview Worker

```bash
# Check preview pod
kubectl get pods -n sports-tracking -l app=preview-worker

# Logs
kubectl logs -n sports-tracking -l app=preview-worker -f
```

### All Components

```bash
# Get all resources in sports-tracking
kubectl get all -n sports-tracking

# Detailed component view
kubectl get pods -n sports-tracking --show-labels
kubectl get services -n sports-tracking
kubectl get ingress -n sports-tracking
```

## Step 6: Configure DNS & TLS

### Verify Ingress

```bash
# Check ingress is created
kubectl get ingress -n sports-tracking

# Get ingress details
kubectl describe ingress api-ingress -n sports-tracking

# Get LoadBalancer IP (if using LoadBalancer ingress)
kubectl get svc -n ingress-nginx
```

### Setup DNS

```bash
# Point sportif.churchlify.com to ingress IP
# Update DNS provider (Cloudflare, Route53, etc.)

# Verify DNS resolves
nslookup sportif.churchlify.com
```

### Verify TLS Certificate

```bash
# Check certificate
kubectl get certificate -n sports-tracking
kubectl describe certificate sportif-api-tls -n sports-tracking

# Certificate details
kubectl get secret sportif-api-tls -n sports-tracking -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -text -noout
```

## Step 7: Post-Deployment Verification

### Full System Check

```bash
#!/bin/bash

echo "=== Sportif Deployment Status ==="

# ArgoCD Applications
echo -e "\n### ArgoCD Applications"
kubectl get applications -n argocd -l app=sportif --no-headers | wc -l | xargs echo "Applications:"

# Pods
echo -e "\n### Pod Status"
kubectl get pods -n sports-tracking -l part-of=sportif --no-headers
echo "Running: $(kubectl get pods -n sports-tracking -l part-of=sportif --field-selector=status.phase=Running --no-headers | wc -l)"
echo "Total: $(kubectl get pods -n sports-tracking -l part-of=sportif --no-headers | wc -l)"

# Services
echo -e "\n### Services"
kubectl get svc -n sports-tracking -l part-of=sportif

# Storage
echo -e "\n### Persistent Volumes"
kubectl get pvc -n sports-tracking -l part-of=sportif

# Ingress
echo -e "\n### Ingress"
kubectl get ingress -n sports-tracking

# Certificates
echo -e "\n### TLS Certificates"
kubectl get certificate -n sports-tracking
```

## Troubleshooting

### Application Won't Sync

```bash
# Check Sportif application details
argocd app get sportif

# Check sync status
argocd app get sportif --show-operation

# View ArgoCD logs
argocd app logs sportif

# Manually trigger sync
argocd app sync sportif

# Force sync if needed (use with caution)
argocd app sync sportif --force

# Check for resource conflicts
kubectl get events -n sports-tracking --sort-by='.lastTimestamp' | tail -20
```

### Pod CrashLoopBackOff

```bash
# Check pod logs
kubectl logs -n sports-tracking <pod-name> --previous
kubectl logs -n sports-tracking <pod-name>

# Check pod events
kubectl describe pod -n sports-tracking <pod-name>

# Check resource availability
kubectl top nodes
kubectl top pods -n sports-tracking
```

### Image Pull Errors

```bash
# Check image pull secrets
kubectl get secrets -n sports-tracking | grep docker

# Verify images exist in registry
# Check if ghcr.io images are accessible
curl -H "Accept: application/vnd.docker.distribution.manifest.v2+json" \
  https://ghcr.io/v2/agogos-llc/sportif-api/manifests/latest
```

### PVC Not Binding

```bash
# Check PVC status
kubectl get pvc -n sports-tracking

# Check PVC details
kubectl describe pvc recorder-segments-pvc -n sports-tracking

# Check Longhorn status
kubectl get longhorn-volumes -n longhorn-system

# Check node storage
kubectl top nodes
df -h on nodes
```

### DNS/Ingress Not Working

```bash
# Check ingress controller
kubectl get pods -n ingress-nginx

# Check ingress resource
kubectl describe ingress api-ingress -n sports-tracking

# Check DNS
nslookup sportif.churchlify.com
dig sportif.churchlify.com

# Test from cluster
kubectl run -it --rm debug --image=nicolaka/netshoot -n sports-tracking -- bash
curl http://sportif.churchlify.com
```

## Updating Images

### Automatic Updates (via CI/CD)

Images are automatically updated when:

1. Code is pushed to main branch in sportif repo
2. Build workflow builds new images: `sha-{commit-sha}`
3. Workflow updates kustomization.yaml in app-ops
4. ArgoCD detects changes and syncs (2-3 minute delay)

### Manual Image Update

```bash
# Update API image to specific tag
cd apps/sportif/api/overlays/production
kustomize edit set image ghcr.io/agogos-llc/sportif-api=ghcr.io/agogos-llc/sportif-api:sha-abc1234d

# Commit and push
git add kustomization.yaml
git commit -m "update sportif-api image to sha-abc1234d"
git push

# ArgoCD will auto-sync
```

## Scaling

### Manual Scaling

```bash
# Scale API replicas
kubectl scale deployment api -n sports-tracking --replicas=3

# Scale via overlay (permanent)
# Edit: apps/sportif/api/overlays/production/kustomization.yaml
# Change: count: 3
```

### Horizontal Pod Autoscaler

```bash
# View HPA status
kubectl get hpa -n sports-tracking
kubectl describe hpa recorder-worker-hpa -n sports-tracking

# Trigger scaling test
kubectl run -i --tty --rm load-generator --image=busybox /bin/sh
# In pod: while sleep 0.01; do wget -q -O- http://recorder-worker:8000/metrics; done

# Watch HPA scale
watch 'kubectl get hpa -n sports-tracking'
```

## Monitoring

### Prometheus Metrics

```bash
# API metrics available at
kubectl port-forward -n sports-tracking svc/api-service 3000:3000 &
curl http://localhost:3000/metrics

# Recorder metrics
kubectl port-forward -n sports-tracking svc/recorder-worker 8000:8000 &
curl http://localhost:8000/metrics
```

### Logs Aggregation

```bash
# All Sportif logs
kubectl logs -n sports-tracking -l part-of=sportif --all-containers=true -f

# Component-specific
kubectl logs -n sports-tracking -l app=api -f
kubectl logs -n sports-tracking -l app=recorder-worker -f
kubectl logs -n sports-tracking -l app=tracking-worker -f
```

## Rollback

### Rollback via ArgoCD

```bash
# View revision history for Sportif
argocd app history sportif

# Rollback to previous revision (all components together)
argocd app rollback sportif 1  # revision number

# View all revisions
argocd app history sportif --max-items=10
```

### Rollback via kubectl

```bash
# View rollout history
kubectl rollout history deployment api -n sports-tracking

# Rollback to previous version
kubectl rollout undo deployment api -n sports-tracking

# Rollback to specific revision
kubectl rollout undo deployment api -n sports-tracking --to-revision=2
```

## Cleanup

### Delete Sportif Application

```bash
# Delete Sportif application (keeps resources)
kubectl delete application sportif -n argocd

# Delete with cleanup (removes all Sportif resources)
kubectl delete application sportif -n argocd --cascade=foreground

# Verify pods are terminating
watch 'kubectl get pods -n sports-tracking -l part-of=sportif'

# Remove any stuck pods
kubectl delete pod <pod-name> -n sports-tracking --grace-period=30 --force
```

Note: The shared `sports-tracking` namespace will remain (used by sport-track). Only Sportif resources are removed.

## Shared media ownership and isolation from sport-track

Sportif and sport-track share:

- **Namespace**: `sports-tracking`
- **Secrets**: `api-secrets` and `sportif-secrets`, owned/synced by Sportif
- **Infrastructure**: PostgreSQL, Redis (if shared)
- **Media services**: `media-ingest-service`, `preview-streamer-service`
- **Storage**: `sports-media-pvc-rwx`, owned by Sportif

**Not shared**:

- Deployments (independent pods)
- **Storage**: Sportif owns the shared RWX media PVC; component-specific PVCs remain separate
- ConfigMaps
- ServiceAccounts
- Network policies (can be applied per-app)

Sport-track must not deploy duplicate SRS, MediaMTX, services, or `sports-media-pvc-rwx` resources. Those resources are managed by the Sportif Argo CD application.

To enforce stricter isolation:

```bash
# Create NetworkPolicy to restrict traffic
kubectl apply -f - <<EOF
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: sportif-isolation
  namespace: sports-tracking
spec:
  podSelector:
    matchLabels:
      part-of: sportif
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
      - podSelector:
          matchLabels:
            part-of: sportif
  egress:
    - to:
      - podSelector:
          matchLabels:
            part-of: sportif
    - to:
      - namespaceSelector:
          matchLabels:
            name: kube-system
EOF
```

## Support

For issues or questions:

- Check [apps/sportif/README.md](apps/sportif/README.md)
- Check [sportif GITOPS_SETUP.md](../sportif/GITOPS_SETUP.md)
- Review ArgoCD application status: `argocd app get sportif-<component>`
- Check pod logs: `kubectl logs -n sports-tracking <pod-name>`
