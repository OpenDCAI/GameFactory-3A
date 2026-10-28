# Recovering Meshy generation tasks

Pass a stable `cache_dir` to `MeshyModel` and rerun identical inputs after a
polling timeout, download failure, or process restart:

```python
from models.gen_3d_object.meshy_model import MeshyModel

model = MeshyModel(cache_dir=".tmp/meshy-cache", timeout=300)
data = model.infer(prompt="a wooden chair", seed=42)
print(model.last_call_info)  # task_ids, cached, resumed, etc.
```

Each accepted task ID is atomically checkpointed **before polling**. Text
generation retains separate preview and refine IDs. A retry resumes preview if
refine has not been created; after preview succeeds it creates refine using the
original preview ID. Once refine is checkpointed, retries query only refine, so
they do not depend on the preview task remaining available. Image generation and
text generation with `texture=False` or `refine=False` recover one task.

Download URLs are fetched from task status again on retry rather than persisted.
Both stage records remain until the completed artifact and metadata have been
published. They are then removed in refine-first order. Cleanup errors leave the
completed cache usable. `cache_dir=None` disables caching and recovery. Public
inference signatures are unchanged.

## Storage, concurrency and parameter identity

Pending records live under `<cache_dir>/meshy/pending/<scope>.json`. The first
stage scope hashes the request fingerprint, actual client API base, and a digest
of the credential held by that client; the refine scope derives from it. Each
record contains only version, scope hash, task ID and creation time. Credentials,
prompts, image data, and URLs are not stored in pending records. Completed cache
metadata retains the existing request/response metadata behavior.

A single first-stage OS file lock covers the entire chain, download, cache
publication and cleanup. Waiting callers recheck the completed cache after
acquiring it. Waiting for the lock and polling each stage have separate `timeout`
budgets. Locks release on process exit; `.lock` files remain intentionally. Use a
local filesystem supporting OS locks and atomic replacement, not distributed
storage for cross-host coordination.

Text refine fingerprints now include `enable_pbr`, which was previously omitted.
Old text-refine cache entries therefore do not match the new key: the first
rerun can submit new paid tasks. Existing image and non-refined text cache keys
are unchanged. Other output-affecting inputs remain part of the fingerprint.

## Failures and deliberate replacement

Failed, canceled, expired or missing tasks, transient network failures, and
malformed checkpoints do not automatically authorize replacement paid tasks.
Errors during polling/download identify the task ID and checkpoint path. To
deliberately replace refine, stop all callers, inspect its provider status and
remove only its indicated JSON checkpoint; the next call reuses preview. To
replace preview too, remove **both** stage JSON records. Never delete lock files
while callers run. An orphaned refine record without preview blocks submission.

If checkpoint persistence fails after acceptance, the exception retains the
accepted ID: preserve it and inspect the provider before retrying. Recovery
starts only after checkpoint publication. Lost creation responses, crashes
before publication, and existing HTTP creation-POST retries remain ambiguous;
this does not guarantee exactly-once billing or provider idempotency. Directory
entry durability after power loss depends on the filesystem.

## Offline validation

```sh
python test/test_meshy_recovery.py
python test/test_tripo_recovery.py
python test/test_api_3d_object.py
```

These use fake transports, including real filesystem locks, child-process exit
and two-process contention. They require no credentials, paid API, GPU or engine.
The existing 3D operator integration additionally requires `trimesh`. They do not
verify live Meshy retention, billing, service behavior or generated asset quality.
