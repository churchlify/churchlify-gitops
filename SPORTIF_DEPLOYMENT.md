# Sportif Deployment Guide

Complete guide for deploying Sportif to the sports-tracking namespace in app-ops.

## Pre-Deployment Checklist

- [ ] sports-tracking namespace exists (shared with sport-track)
- [ ] Secrets created: `api-secrets` with JWT_SECRET
- [ ] Longhorn storage provisioned
- [ ] ArgoCD installed and configured
- [ ] cert-manager installed for TLS
- [ ] nginx-ingress installed

## Step 1: Create Required Secrets

```bash
# Create api-secrets in sports-tracking namespace
kubectl create secret generic api-secrets \
  --from-literal=JWT_SECRET='your-secure-jwt-secret-here' \
  -n sports-tracking

# Verify
kubectl get secrets -n sports-tracking | grep api-secrets
```

## Step 2: Verify Shared Resources

```bash
# Check namespace exists
kubectl get ns sports-tracking

# Check existing resources (from sport-track)
kubectl get pods -n sports-tracking
kubectl get pvc -n sports-tracking

# Note: Sportif will use shared namespace and secrets only
```

## Step 3: Deploy via ArgoCD

### Option A: Deploy All Components at Once

```bash
cd app-ops

# Apply all Sportif ArgoCD applications
kubectl apply -f platform/argocd/sportif/

# Verify applications created
kubectl get applications -n argocd | grep sportif
```

### Option B: Deploy Components Individually

```bash
# Deploy API only
kubectl apply -f platform/argocd/sportif/api.yaml
argocd app wait sportif-api

# Deploy Recorder
kubectl apply -f platform/argocd/sportif/recorder.yaml
argocd app wait sportif-recorder

# Deploy other components
kubectl apply -f platform/argocd/sportif/{preview,tracking,processing,cleanup,notification,uploader}.yaml
```

## Step 4: Monitor Deployment Progress

### Watch ArgoCD Sync

```bash
# Terminal 1: Watch all applications
watch 'kubectl get applications -n argocd | grep sportif'

# Terminal 2: Check application details
argocd app get sportif-api
argocd app get sportif-recorder
# ... etc for all apps
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
# Check ArgoCD application details
argocd app get sportif-api

# Check sync status
argocd app get sportif-api --show-operation

# View ArgoCD logs
argocd app logs sportif-api

# Manually trigger sync
argocd app sync sportif-api

# Force sync if needed
argocd app sync sportif-api --force
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
  https://ghcr.io/v2/churchlify/sportif-api/manifests/latest
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
# View revision history
argocd app history sportif-api

# Rollback to previous revision
argocd app rollback sportif-api 1  # revision number

# Rollback all applications
for app in sportif-{api,recorder,preview,tracking,processing,cleanup,notification,uploader}; do
  argocd app rollback $app 1
done
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

### Delete Specific Component

```bash
# Delete API application (keeps resources)
kubectl delete application sportif-api -n argocd

# Delete with cleanup (removes all resources)
kubectl delete application sportif-api -n argocd --cascade=foreground
```

### Delete All Sportif Resources

```bash
# Remove all ArgoCD applications
kubectl delete -f platform/argocd/sportif/

# Verify pods are terminating
watch 'kubectl get pods -n sports-tracking -l part-of=sportif'

# Remove any stuck pods
kubectl delete pod <pod-name> -n sports-tracking --grace-period=30 --force
```

## Isolation from sport-track

Sportif and sport-track share only:

- **Namespace**: `sports-tracking`
- **Pre-created secrets**: `api-secrets`
- **Infrastructure**: PostgreSQL, Redis (if shared)

**Not shared**:

- Deployments (independent pods)
- Storage (separate PVCs)
- ConfigMaps
- ServiceAccounts
- Network policies (can be applied per-app)

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
