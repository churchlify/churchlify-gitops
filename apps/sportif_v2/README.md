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
separately-owned `ExternalSecret` (`api-v2-extra-secrets-sync`, target
`api-v2-extra-secrets`) for the 3 keys `sportif-secrets` doesn't have:
`SMTP_PASSWORD` and `INTERNAL_API_KEY` come straight from
`global-db-secrets`; the sender-profile encryption key is synced from
that same remote object's `NOTIFICATION_PROFILES_KEY` property and
renamed to `SENDER_PROFILE_ENCRYPTION_KEY` in the target template (that's
the env var name `apps/api/src/notifications/sender-profile-crypto.ts`
actually reads).

Nothing in `apps/sportif/shared/external-secret.yaml` is modified by
this app -- the live v1 secret sync is untouched.

### GameMaster MinIO credentials

`shared/gamemaster-storage-external-secret.yaml` separately syncs the dedicated,
non-administrative GameMaster backend identity from:

```text
remote key: sportif-minio-secrets
properties:
  GAMEMASTER_S3_ACCESS_KEY
  GAMEMASTER_S3_SECRET_KEY
```

into:

```text
Kubernetes Secret: api-v2-gamemaster-storage-secrets
```

The API and queue worker reference this Secret as optional only while
`GAMEMASTER_MULTIPART_ENABLED` is `false`, allowing Infrastructure to provision
the remote secret without breaking the existing disabled deployment. Make both
references required before activating GameMaster multipart uploads.

The mounted identity must be a dedicated application service identity, not a
MinIO root/administrator credential. One-time orphan discovery and cleanup use a
separate ephemeral operator Secret described below.

The current `sportif-v2-bucket` value is provisional pending the separate
per-environment bucket architecture decision. Do not enable the feature until
that decision and the live matrix are recorded.

Populate the remote `sportif-minio-secrets` object before syncing the GitOps
commit that introduces this ExternalSecret. The workload references are optional
while disabled, so a delayed target Secret does not block pod startup, but the
ExternalSecret itself will report `SecretSyncedError` until both remote properties
exist.

## Deploying

```bash
kubectl apply -f platform/argocd/sportif_v2/app-v2.yaml
argocd app get sportif-v2
argocd app sync sportif-v2   # optional: automated sync is enabled
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

## GameMaster MinIO/STS validation

The operator-run Kustomize target is:

```text
apps/sportif_v2/gamemaster-validation
```

It is intentionally excluded from this app's root `kustomization.yaml`; ArgoCD
must not continuously recreate the one-off Job or own its private cleanup input
Secret.

Before running it:

1. Create and attach the reviewed lifecycle policy to a dedicated MinIO backend
   identity.
2. Populate `sportif-minio-secrets` in `platform-secrets` and confirm
   `api-v2-gamemaster-storage-secrets-sync` is Ready.
3. Build the Sportif API from application commit `ebd9f53` or later, which
   packages `scripts/validate-gamemaster-minio-sts.mjs` in the runtime image.
4. Replace the candidate image tag in the rendered Job with that published
   image's immutable digest.
5. Keep `GAMEMASTER_MULTIPART_ENABLED=false`.

Create a private env file outside Git:

```env
GAMEMASTER_LEGACY_CLEANUP_OBJECT_KEY=matches/<match>/game-masters/<master>/parts/part-<index>.<mp4|mov>
GAMEMASTER_CLEANUP_S3_ACCESS_KEY=<one-time-cleanup-access-key>
GAMEMASTER_CLEANUP_S3_SECRET_KEY=<one-time-cleanup-secret-key>
# Optional when the cleanup identity itself is temporary:
GAMEMASTER_CLEANUP_S3_SESSION_TOKEN=<one-time-cleanup-session-token>
```

Create the ephemeral input Secret without printing values:

```bash
kubectl -n sports-tracking create secret generic \
  gamemaster-sts-validation-inputs \
  --from-env-file=/private/path/gamemaster-sts-validation.env \
  --dry-run=client -o yaml | kubectl apply -f -
```

Render and apply the Job with the published digest:

```bash
kubectl kustomize apps/sportif_v2/gamemaster-validation \
  | sed 's#ghcr.io/agogos-llc/sportif-api:sha-b3242e4#ghcr.io/agogos-llc/sportif-api@sha256:<published-digest>#' \
  | kubectl apply -f -

kubectl -n sports-tracking wait \
  --for=condition=complete job/gamemaster-minio-sts-validation \
  --timeout=20m

kubectl -n sports-tracking logs \
  job/gamemaster-minio-sts-validation \
  > /private/path/sportif-minio-sts-result.json
```

Confirm the output contains no credential-like fields and reports success:

```bash
if grep -Eiq \
  'accessKey|secretAccessKey|sessionToken|authorization|uploadId|signature' \
  /private/path/sportif-minio-sts-result.json; then
  echo 'unsafe validation output' >&2
  exit 1
fi
jq -e '.passed == true' /private/path/sportif-minio-sts-result.json
```

Capture sanitized evidence, then remove the one-off resources:

```bash
kubectl -n sports-tracking delete \
  job/gamemaster-minio-sts-validation \
  secret/gamemaster-sts-validation-inputs
```

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
