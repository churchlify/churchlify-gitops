# Operations

Upload supported `.mp4`, `.mov`, or `.mkv` files to the `videos/` prefix in
`sportif-ml-raw`. Create a provenance entry with the video SHA-256, source
rights status, jurisdiction, and explicit AI-training permission.

The active `sportif-video-ingest` WorkflowTemplate validates one approved source
video and extracts representative frames. It requires explicit MinIO object keys
for both the video and its provenance manifest. The full training workflow remains
staged and must not be submitted.

```bash
kubectl -n sportif-ml get pods,jobs,pvc
kubectl -n sportif-ml get externalsecret sportif-ml-storage-sync
kubectl -n sportif-ml rollout status deployment/mlflow
kubectl -n sportif-ml get pods -l app=mlflow \
  --sort-by=.metadata.creationTimestamp
kubectl -n sportif-ml get events --sort-by=.lastTimestamp
kubectl -n argocd get application sportif-ml-cvat
kubectl -n argocd get application sportif-ml-argo-workflows
kubectl -n sportif-ml get pods,pvc,service -l app.kubernetes.io/instance=cvat
kubectl -n sportif-ml rollout status \
  deployment/sportif-ml-argo-workflows-workflow-controller
kubectl get crd workflows.argoproj.io workflowtemplates.argoproj.io
kubectl -n sportif-ml get workflowtemplate sportif-video-ingest
kubectl -n sportif-ml get ingress cvat
kubectl -n sportif-ml get externalsecret sportif-ml-cvat-ingress-auth-sync
```

Use `https://annotate.churchlify.com` for a complete browser session. Enter the
ingress username `babs` and the password stored in the operator's macOS
Keychain, then sign in to CVAT as the separate human user `babs`. Retrieve the
CVAT password from Keychain service `annotate.churchlify.com/cvat`; the ingress
and CVAT passwords are distinct. The chart ingress routes backend paths and the
frontend under the same authenticated origin.

Argo Workflows is controller-only. There is no Argo Server Service or UI. Submit
an approved ingest run with a generated Workflow resource:

```bash
VIDEO_KEY='videos/<approved-video>.mp4'
PROVENANCE_KEY='videos/<video-id>/source-provenance.json'

cat <<EOF | kubectl -n sportif-ml create -f -
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: sportif-video-ingest-
spec:
  workflowTemplateRef:
    name: sportif-video-ingest
  arguments:
    parameters:
      - name: video-key
        value: ${VIDEO_KEY}
      - name: provenance-key
        value: ${PROVENANCE_KEY}
EOF
```

Watch with `kubectl -n sportif-ml get workflows,pods --watch`. A successful run
writes `input-validation.json` and `frame-extraction.json` to the provenance
bucket and JPEG frames plus `frames-manifest.json` to the frames bucket. Stage 4C
then writes `frame-selection.json` to provenance and
`selected-frames-manifest.json` to frames.

Before importing frames into CVAT, verify `frame-selection.json` has `status:
PASS`, its original count matches `frames-manifest.json`, its selected count
matches `selected-frames-manifest.json`, and every selected object key remains
under `videos/<videoId>/frames/`. Changing `FRAME_PHASH_THRESHOLD` changes the
selection set and requires a new workflow run and review.

## CVAT handoff

Do not activate or submit the handoff until the dedicated automation token is
Ready and the workflow image is pinned by digest. Submit one explicit handoff:

```bash
VIDEO_KEY='videos/<approved-video>.mp4'
PROVENANCE_KEY='videos/<video-id>/source-provenance.json'

cat <<EOF | kubectl -n sportif-ml create -f -
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: sportif-cvat-handoff-
spec:
  workflowTemplateRef:
    name: sportif-cvat-handoff
  arguments:
    parameters:
      - name: video-key
        value: ${VIDEO_KEY}
      - name: provenance-key
        value: ${PROVENANCE_KEY}
EOF
```

The workflow serializes handoffs, revalidates source rights, verifies
`selected-frames-manifest.json`, checks `frame-selection.json`, downloads only
same-video selected objects, verifies every SHA-256, creates or reuses the
single-label project, and creates or reuses `sportif-ball-<videoId>`. A retry
does not create a second matching task. It fails rather than guessing if CVAT
contains duplicate names or an existing task has a different frame count.

After success, inspect `videos/<videoId>/cvat-handoff.json` through the approved
MinIO operator path. Confirm `status: PASS`, the expected source SHA-256 and
selected count, project/task IDs and names, and
`annotationPolicy.datasetApprovalGranted: false`.

### Required human actions after handoff

1. Provide browser access through an approved authenticated ingress, private VPN,
   or controlled local reverse proxy. Do not expose the internal Services with an
   unauthenticated public ingress.
2. Sign in as a human CVAT user, not `sportif-ml-automation`.
3. Open `Sportif Soccer Ball Annotation` and locate
   `sportif-ball-<videoId>`.
4. Confirm the task has exactly the selected-frame count and only the `ball`
   rectangle label.
5. Assign an annotator. Assign a distinct reviewer where staffing permits; if
   one person performs both roles, record that exception in completion
   provenance.
6. Follow `annotation-guidelines.md`. Resolve all review issues before export.
7. Export **YOLO 1.1** only after review is complete. Record the task ID, export
   time, CVAT version, format, artifact SHA-256, annotator, reviewer, issue count,
   and review result in annotation-completion provenance.
8. Keep dataset preparation and training disabled until export validation and an
   explicit dataset approval pass.

To rotate or revoke the automation token, delete its CVAT token in Django,
create a replacement, patch `global-db-secrets`, wait for the ExternalSecret to
refresh, and only then resume handoffs. Existing CVAT tasks are unaffected.

## Ball training

The `sportif-ball-training` WorkflowTemplate is deployed through GitOps but does
not create a run automatically. It consumes only the approved dataset and uses a
mutex plus the `sportif-ml-work` ReadWriteOnce PVC to prevent concurrent runs.
The trainer image is private, digest-pinned, and pulled with the
`sportif-ml-registry` Secret generated by External Secrets. The corresponding
Docker config is stored out-of-band as `SPORTIF_ML_GHCR_CONFIG` in
`platform/global-db-secrets`; never commit or print it.

Before submission, verify the dataset approval, PVC, registry Secret, template,
and GPU:

```bash
kubectl -n sportif-ml get configmap sportif-ml-config \
  -o jsonpath='{.data.DATASET_ID}{" "}{.data.DATASET_APPROVED}{"\n"}'
kubectl -n sportif-ml get externalsecret sportif-ml-registry-sync
kubectl -n sportif-ml get secret sportif-ml-registry \
  -o jsonpath='{.type}{"\n"}'
kubectl -n sportif-ml get pvc sportif-ml-work
kubectl -n sportif-ml get workflowtemplate sportif-ball-training
kubectl get node k8s-gpu-node \
  -o jsonpath='{.status.allocatable.nvidia\.com/gpu}{"\n"}'
```

Submit one explicit run:

```bash
cat <<'EOF' | kubectl -n sportif-ml create -f -
apiVersion: argoproj.io/v1alpha1
kind: Workflow
metadata:
  generateName: sportif-ball-training-
spec:
  workflowTemplateRef:
    name: sportif-ball-training
  arguments:
    parameters:
      - name: dataset-id
        value: sportif-ball-v003
EOF
```

Watch with `kubectl -n sportif-ml get workflows,pods --watch`. A successful run
uploads candidate artifacts under
`s3://sportif-ml-models/candidates/sportif-ball-detector-v001/<workflow-name>/`
and mirrored provenance under
`s3://sportif-ml-provenance/models/sportif-ball-detector-v001/<workflow-name>/`.
Do not treat successful execution as model-release approval.

Evaluation fails the workflow before export or publication unless mAP50 is at
least `0.50`, mAP50-95 is at least `0.20`, precision is at least `0.60`, and
recall is at least `0.60`. A failed evaluator still writes `metrics.json` to the
run workspace with `executionStatus: PASS`, `qualityGateStatus: FAIL`, and the
exact threshold failures for diagnosis.

Training checkpoint selection is separately fail closed. Validation calibration
requires precision and recall of at least `0.60`, no more than `0.10` detections
per negative frame, and at least 10 tiny-object validation annotations before the
configured tiny-recall floor can establish checkpoint eligibility. If no epoch
meets those constraints, training must not create a validation-selected
`model.pt`; investigate or create a newly approved dataset version instead of
lowering the constraints.

The first live acceptance Workflow, `sportif-video-ingest-h46bk`, completed on
September 20, 2026 with both tasks successful. It produced and verified 4,724
frames for approved source `VID-20260920-001`. The verification compared actual
JPEG object count with both output documents, checked source SHA-256 linkage, and
confirmed the first and last manifest-referenced frame objects exist.

The Stage 4C acceptance Workflow, `sportif-video-ingest-2hflb`, completed on
September 21, 2026 with all three tasks successful. At threshold `8`, it selected
165 of 4,724 frames and rejected 4,559 near-duplicates. Independent verification
checked the selection status, video/source identity, counts, per-frame decision
mapping, perceptual hashes, duplicate references, and the SHA-256 of the first
and last selected JPEG objects.

Completed Workflows are retained for 24 hours by the template TTL. Worker pods
are removed after completion by pod GC, so use the Workflow status and persisted
MinIO documents as the durable operational record.
