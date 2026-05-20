# GitOps

ArgoCD reconciles the Helm chart at `charts/nebulastream` and the upstream
`kube-prometheus-stack` chart for observability.

## Bootstrap (one-time)

```bash
# 1. Install ArgoCD into the cluster
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# 2. Edit gitops/argocd/*.yaml — replace REPLACE_ME with your GitHub org/user
# 3. Apply the root "app of apps" — ArgoCD then picks up everything else
kubectl apply -f gitops/argocd/project.yaml
kubectl apply -f gitops/argocd/app-of-apps.yaml

# 4. Get the admin password and port-forward the UI
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

## How it works

```
git push main
   │
   ▼
ArgoCD detects change in charts/nebulastream  →  helm template  →  apply to cluster
                                                  │
                                                  └──  prune resources removed from git
```

Self-heal is enabled — any manual `kubectl edit` is reverted within ~3 minutes.
