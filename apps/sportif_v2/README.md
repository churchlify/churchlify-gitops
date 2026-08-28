# Sportif v2 - API split (parallel verification)

Parallel deployment of the sportif API's 3-way process split (api /
queue-worker / notifications), replacing the single `api` component of
the live `sportif` app (`../sportif/`). Everything else in that app
(recorder, preview, tracking, processing, cleanup, notification,
uploader, and the shared media stack) is untouched and stays owned by
`sportif`.

## Why a separate app instead of editing `sportif/api/` in place

The 3-way split changes what the api image runs (3 processes instead
of 1) and how it's shaped (own ConfigMap/Deployment/Service per
process). Editing `sportif/api/` directly would mean the moment ArgoCD
syncs, the old `api` Deployment/ConfigMap/Service are replaced in
place with no way to compare old vs. new behavior, and no quick revert
short of re-reverting the manifest and waiting for another sync.
Standing up `sportif-v2` alongside it means:

- The v1 `api` component keeps serving `sportif.churchlify.com` from
  the same image tag it's on today, untouched, for the entire
  verification window.
- `sportif-v2` gets its own hostname (`v2.sportif.churchlify.com`,
  see `api-v2/base/ingress.yaml`) to test against without touching
  live traffic.
- Cutover and rollback are both just Ingress/DNS changes, not a
  redeploy.

## Naming

Every resource is suffixed `-v2` (`api-v2`, `queue-worker-v2`,
`notifications-v2`, `api-v2-config`, ...) since this app shares the
`sports-tracking` namespace with `sportif` (needed so `queue-worker-v2`
can reach `recording-service`, `vision-processing-service`, etc. by
their short in-namespace names, same reasoning as `sportif/api`'s own
ConfigMap). Matches the `churchlify-api` / `churchlify-api-v2` pattern
already used elsewhere in this repo (`apps/churchlify/api-v2/`).

## Secrets

`api-v2`/`queue-worker-v2`/`notifications-v2` read `sportif-secrets`
(the live v1 Secret, synced by `../sportif/shared/external-secret.yaml`)
directly via `envFrom` -- same `JWT_SECRET`/`DATABASE_URL`/`REDIS_URL`/
`REDIS_PASSWORD` as v1, so v2 talks to the same database and honors
existing v1-issued JWTs. `shared/secret-extras.yaml` is a small,
separately-owned placeholder Secret (`api-v2-extra-secrets`) for the 3
keys `sportif-secrets` doesn't have: `SMTP_PASSWORD`,
`INTERNAL_API_KEY`, `SENDER_PROFILE_ENCRYPTION_KEY`. Replace those
placeholders (or fold them into `global-db-secrets` and switch this to
an ExternalSecret) before relying on SMTP or the internal API key in
production.

Nothing in `apps/sportif/shared/external-secret.yaml` is modified by
this app -- the live v1 secret sync is untouched.

## Deploying

```bash
kubectl apply -f platform/argocd/sportif/app-v2.yaml
argocd app get sportif-v2
argocd app sync sportif-v2   # no automated sync -- first deploy is manual
```

## Verifying

```bash
kubectl get pods -n sports-tracking -l part-of=sportif-v2
kubectl logs -n sports-tracking deploy/api-v2 -f
curl https://v2.sportif.churchlify.com/health/ready
```

Exercise a real login and a match-list fetch against
`v2.sportif.churchlify.com` to confirm `JWT_SECRET`/`DATABASE_URL`
reuse actually works end-to-end.

## Cutover

Once verified:

1. Point `sportif.churchlify.com`'s Ingress (or DNS) at the `api-v2`
   Service instead of `api`, or repoint the client apps at
   `v2.sportif.churchlify.com` directly, per your rollout preference.
2. Bump `api-v2`/`queue-worker-v2`/`notifications-v2` replica counts to
   match v1's production counts (`api`: 2) once traffic moves over.
3. Remove `api/overlays/production` from `../sportif/kustomization.yaml`
   and delete `apps/sportif/api/` once nothing points at it.
4. Retire `api-v2-ingress`/`v2.sportif.churchlify.com` and, if desired,
   rename `-v2` resources back to their plain names in a follow-up
   change (optional -- purely cosmetic once v1's `api` is gone).

## Rollback

Revert the commit that repointed traffic in step 1 above. `sportif-v2`
keeps running independently the whole time, so rollback never involves
redeploying v1.
