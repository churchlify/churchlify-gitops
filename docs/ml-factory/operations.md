# Operations

Upload supported `.mp4`, `.mov`, or `.mkv` files to the `videos/` prefix in
`sportif-ml-raw`. Create a provenance entry with the video SHA-256, source
rights status, jurisdiction, and explicit AI-training permission.

The checked-in workflow is staged and is not operational yet. Once its missing
storage/CVAT/metrics/publishing stages are implemented and it is activated, the
dataset approval parameter must be explicitly set to `true` after CVAT review.

```bash
kubectl -n sportif-ml get pods,jobs,pvc
kubectl -n sportif-ml get externalsecret sportif-ml-storage-sync
kubectl -n sportif-ml get events --sort-by=.lastTimestamp
```
