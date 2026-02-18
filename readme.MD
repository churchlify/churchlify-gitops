# App Ops - ArgoCD Configuration

This repository contains infrastructure-as-code configurations for managing applications in a Kubernetes cluster using ArgoCD.

## Directory Structure

```
app_ops/
├── apps/                      # Application manifests
│   ├── churchlify/
│   ├── ontime/
│   └── schoolrider/
└── platform/                  # Platform-wide configurations
    ├── argocd/               # ArgoCD application definitions
    └── namespaces/           # Kubernetes namespace definitions
```

## Adding a New App to ArgoCD

Follow this checklist when adding a new application to the cluster:

### Phase 1: Pre-Deployment Setup

- [ ] **Create Namespace**
  - [ ] Create `platform/namespaces/{app-name}.yaml`
  - [ ] Define namespace with appropriate labels
  - [ ] Example:
    ```yaml
    apiVersion: v1
    kind: Namespace
    metadata:
      name: {app-name}
      labels:
        app: {app-name}
    ```
  - [ ] Apply namespace: `kubectl apply -f platform/namespaces/{app-name}.yaml`

- [ ] **Create Secrets & ExternalSecrets Configuration**
  - [ ] Create `apps/{app-name}/api/base/externalsecret-global.yaml`
  - [ ] Define ExternalSecret resource to pull secrets from vault/secret manager
  - [ ] Ensure secret locations are configured in your secret backend
  - [ ] Example:
    ```yaml
    apiVersion: external-secrets.io/v1beta1
    kind: ExternalSecret
    metadata:
      name: {app-name}-secrets
      namespace: {app-name}
    spec:
      secretStoreRef:
        name: vault
        kind: ClusterSecretStore
      target:
        name: {app-name}-secrets
        creationPolicy: Owner
      data:
        - secretKey: DB_PASSWORD
          remoteRef:
            key: secret/data/{app-name}/db-password
    ```
  - [ ] Verify secret store exists in cluster
  - [ ] Create corresponding secrets in your secret management backend

### Phase 2: Application Configuration

- [ ] **Create Application Directory Structure**
  - [ ] Create `apps/{app-name}/` directory
  - [ ] Create subdirectories:
    - [ ] `apps/{app-name}/api/base/` - Base Kustomization resources
    - [ ] `apps/{app-name}/api/overlays/production/` - Production overlay
    - [ ] `apps/{app-name}/webapp/` - Web application resources (if applicable)
    - [ ] `apps/{app-name}/website/` - Website resources (if applicable)

- [ ] **Configure Base Kustomization**
  - [ ] Create `apps/{app-name}/api/base/kustomization.yaml`
  - [ ] Create `apps/{app-name}/api/base/service.yaml`
  - [ ] Create `apps/{app-name}/api/base/deployment.yaml`
  - [ ] Include reference to `externalsecret-global.yaml`

- [ ] **Configure Production Overlay**
  - [ ] Create `apps/{app-name}/api/overlays/production/kustomization.yaml`
  - [ ] Define production-specific patches and configurations
  - [ ] Example:
    ```yaml
    apiVersion: kustomize.config.k8s.io/v1beta1
    kind: Kustomization
    
    bases:
      - ../../base
    
    replicas:
      - name: {app-name}
        count: 3
    ```

### Phase 3: ArgoCD Configuration

- [ ] **Create ArgoCD Application Manifest**
  - [ ] Create `platform/argocd/{app-name}/api.yaml`
  - [ ] Define ArgoCD Application resource
  - [ ] Ensure namespace references match created namespace
  - [ ] Example:
    ```yaml
    apiVersion: argoproj.io/v1alpha1
    kind: Application
    metadata:
      name: {app-name}-api
      namespace: argocd
    spec:
      project: default
      source:
        repoURL: {repo-url}
        path: apps/{app-name}/api/overlays/production
        targetRevision: main
      destination:
        server: https://kubernetes.default.svc
        namespace: {app-name}
      syncPolicy:
        automated:
          prune: true
          selfHeal: true
        syncOptions:
          - CreateNamespace=false
    ```

- [ ] **Create Additional Application Manifests (if needed)**
  - [ ] `platform/argocd/{app-name}/webapp.yaml` - for web app
  - [ ] `platform/argocd/{app-name}/website.yaml` - for website

### Phase 4: Deployment & Verification

- [ ] **Commit Changes**
  - [ ] Stage all new configuration files
  - [ ] ```bash
    git add apps/{app-name} platform/argocd/{app-name} platform/namespaces/{app-name}.yaml
    ```
  - [ ] Commit with descriptive message
  - [ ] ```bash
    git commit -m "feat: add {app-name} application configuration for ArgoCD"
    ```

- [ ] **Push to Repository**
  - [ ] ```bash
    git push -u origin main
    ```

- [ ] **Verify ArgoCD Application**
  - [ ] Check ArgoCD UI: `https://{argocd-url}`
  - [ ] Verify application appears in application list
  - [ ] Check synchronization status
  - [ ] Verify no sync errors

- [ ] **Verify Namespace & Resources**
  - [ ] ```bash
    kubectl get namespace {app-name}
    kubectl get pods -n {app-name}
    kubectl get secrets -n {app-name}
    ```

- [ ] **Verify ExternalSecrets**
  - [ ] ```bash
    kubectl get externalsecrets -n {app-name}
    kubectl describe externalsecret {app-name}-secrets -n {app-name}
    ```

- [ ] **Test Application**
  - [ ] Verify application pods are running
  - [ ] Check application logs for errors
  - [ ] Test application endpoints/functionality

## Quick Commands Reference

```bash
# View all namespaces
kubectl get namespaces

# View ArgoCD applications
kubectl get applications -n argocd

# Manually sync an application
argocd app sync {app-name}-api

# View application details
argocd app get {app-name}-api

# Check external secrets status
kubectl get externalsecrets -A

# View ArgoCD logs
kubectl logs -f deployment/argocd-server -n argocd
```

## Troubleshooting

### ExternalSecret Not Syncing
- Verify secret store configuration
- Check credentials for secret backend
- Ensure secret path exists in backend
- Check ExternalSecret events: `kubectl describe externalsecret {app-name}-secrets -n {app-name}`

### ArgoCD Application Out of Sync
- Check for manual changes in cluster: `kubectl diff -f apps/{app-name}/...`
- Review ArgoCD application health status
- Check application resource events
- Manually trigger sync if needed

### Namespace Not Created
- Verify namespace manifest syntax
- Check RBAC permissions
- Ensure kubectl context is correct
- Apply namespace manually if needed

## Resources

- [ArgoCD Documentation](https://argo-cd.readthedocs.io/)
- [Kustomize Documentation](https://kustomize.io/)
- [External Secrets Operator](https://external-secrets.io/)
- [Kubernetes Documentation](https://kubernetes.io/docs/)
