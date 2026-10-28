"""
models/gen_3d_object/meshy_model.py

MeshyModel — wrapper around Meshy's cloud 3D-generation API (image-to-3D and
text-to-3D), producing GLB / FBX / OBJ / USDZ / STL.

Reference: https://docs.meshy.ai/  (base https://api.meshy.ai)

Second backend for the `gen_3d_object` slot. Its `infer` / `infer_and_save`
signatures are identical to `TripoModel`'s and positionally identical to
`Trellis2Model`'s, so `Gen3DObjectOperator` dispatches on class name alone
(model_require.md R6).

CONTRACT DEVIATIONS (model_require.md targets local-weight models; the rules that
replace it live in agent_skills/develop_harness/api_model_require.md):
  C1  R2.1/R2.2 → R9.1  `model_path` carries a *model version id* ("meshy-6").
  C2  R2.3      → R9.2  `device` accepted for interface parity and ignored.
  C3  R3.6/R5.5 → R9.3  no @torch.no_grad(); torch is not involved.
  C4  R4.1-R4.3 → R9.4  `unload()` is a no-op that closes the HTTP session.
  C5  R3.3/R3.4 → R9.5  `seed` is forwarded for text-to-3D only, where the API
                        accepts it; image-to-3D has no seed. Reproducibility is
                        NOT guaranteed server-side and is never faked locally.
  C6  R3.1      → R9.6  task-based API (submit → poll → download); `infer()`
                        blocks until completion or `timeout` (default 300 s).
                        Text-to-3D runs two tasks (preview → refine) when a
                        texture is requested; both ids are logged.
  C7  R1.2             uses the documented `infer_and_save(..., output_path)`
                        exception; the bytes-returning `infer()` exists alongside.
  C8  (new)     → R9.8  `cache_dir` prevents re-billing an identical request.
  C9  (new)     → R9.9  `max_retries` + exponential backoff; retryable and
                        terminal failures are split.

Environment:
    MESHY_API_KEY   required at the first infer() call, not at construction.
                    Get one at https://www.meshy.ai/api  (a `msy_dummy_...`
                    test-mode key returns canned assets without spending credits).
    MESHY_API_BASE  optional override of the API root.
    `pip install requests` is needed for the API backends only.

Usage:
    from models.gen_3d_object.meshy_model import MeshyModel
    model = MeshyModel(model_path="meshy-6", output_format="fbx")
    path = model.infer_and_save(image, output_path="out/model.fbx", seed=42)
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from models.common import cloud_api
from models.common.glb_utils import glb_summary
from models.common.task_recovery import PendingTaskStore

logger = logging.getLogger(__name__)

SIGNUP_URL = "https://www.meshy.ai/api"

DEFAULT_API_BASE = "https://api.meshy.ai"

#: Short names accepted for `model_path`. Unknown values pass through untouched.
MODEL_ALIASES: dict[str, str] = {
    "meshy": "meshy-6",
    "meshy-6-text-to-3d": "meshy-6",
    "meshy-6-image-to-3d": "meshy-6",
    "meshy-5-text-to-3d": "meshy-5",
    "meshy-5-image-to-3d": "meshy-5",
}

DEFAULT_MODEL = "meshy-6"

DONE_STATES = ("succeeded", "success", "completed")
FAILED_STATES = ("failed", "canceled", "cancelled", "expired")

#: Meshy rejects a target_polycount outside this range with a 400 (terminal).
POLYCOUNT_RANGE = (100, 300_000)

#: Formats the API can return directly, i.e. keys of `model_urls`.
SUPPORTED_FORMATS = ("glb", "fbx", "obj", "usdz", "stl", "mtl", "3mf")


class MeshyModel:
    """
    Cloud image-to-3D / text-to-3D through Meshy.

    Args:
        model_path: [C1] Meshy `ai_model` id ("meshy-6", "meshy-5", "latest"),
                    or an alias from `MODEL_ALIASES`.
        device:     [C2] Accepted for interface parity, ignored. "cpu" is fine.
        api_key:    Explicit key; defaults to `$MESHY_API_KEY`, read at first call.
        timeout:    [C6] Seconds to wait for one generation task (each stage of
                    the text-to-3D preview → refine chain gets the full budget).
        poll_interval: Seconds between status polls.
        max_retries: [C9] Extra attempts for retryable HTTP failures.
        cache_dir:  [C8] Directory for completed responses and accepted-task
                    checkpoints. None disables both caching and recovery.
        output_format: Any of `SUPPORTED_FORMATS`; picked out of `model_urls`.
        low_poly:   [B4.3] Sets `model_type="lowpoly"`, i.e. the service produces
                    a low-poly mesh instead of us decimating one locally.
        texture / enable_pbr / topology / should_remesh:
                    Forwarded to the generation endpoints.
        refine:     Text-to-3D only. Meshy's preview stage returns an untextured
                    mesh; the refine stage textures it and costs extra credits.
                    Ignored when `texture=False`.
        api_base:   Override the API root (defaults to `$MESHY_API_BASE`).
        verbose:    Print task progress; off by default (R3.7).
    """

    PROVIDER = "meshy"

    def __init__(
        self,
        model_path: str = DEFAULT_MODEL,
        device: str = "cuda",
        *,
        api_key: Optional[str] = None,
        # Measured: one image-to-3D with PBR textures took 283 s here, and the
        # Tripo backend took 601 s for the same job. Timing out does not refund
        # the credits, so the default errs long; the error carries the task_id.
        timeout: int = 1800,
        poll_interval: float = 3.0,
        max_retries: int = 3,
        cache_dir: Optional[str] = None,
        output_format: str = "glb",
        low_poly: bool = False,
        texture: bool = True,
        enable_pbr: bool = True,
        topology: str = "triangle",
        should_remesh: Optional[bool] = None,
        refine: bool = True,
        api_base: Optional[str] = None,
        http_timeout: int = 60,
        verbose: bool = False,
        **model_specific: Any,
    ):
        # [CONTRACT-DEVIATION C1] model_path is a version id, not a weight location.
        # [CONTRACT-DEVIATION C2] device accepted for interface parity, ignored.
        self.model_path = MODEL_ALIASES.get(str(model_path).lower(), model_path)
        self.device = device

        # R2.4 — store everything before anything loads.
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.cache_dir = cache_dir
        self.output_format = output_format.lower().lstrip(".")
        self.low_poly = low_poly
        self.texture = texture
        self.enable_pbr = enable_pbr
        self.topology = topology
        self.should_remesh = should_remesh
        self.refine = refine
        self.api_base = (api_base or os.environ.get("MESHY_API_BASE")
                         or DEFAULT_API_BASE)
        self.http_timeout = http_timeout
        self.verbose = verbose
        self.model_specific = model_specific

        if self.output_format not in SUPPORTED_FORMATS:
            raise ValueError(
                f"output_format={self.output_format!r} is not one of "
                f"{SUPPORTED_FORMATS}. Meshy returns these directly in model_urls."
            )

        self._client: Optional[cloud_api.CloudAPIClient] = None
        self._cache = cloud_api.ResponseCache(cache_dir, self.PROVIDER)

        #: Record of the most recent **real** call — see `TripoModel.last_call_info`.
        self.last_call_info: dict[str, Any] = {}

    # ── plumbing ──────────────────────────────────────────────────────────────

    @property
    def client(self) -> cloud_api.CloudAPIClient:
        """HTTP client, created on first use. [R9.7] The key is resolved here."""
        if self._client is None:
            key = cloud_api.require_api_key(
                self.api_key, "MESHY_API_KEY", SIGNUP_URL, who="MeshyModel")
            self._client = cloud_api.CloudAPIClient(
                self.api_base, key,
                timeout=self.http_timeout,
                max_retries=self.max_retries,
            )
        return self._client

    def unload(self) -> None:
        """[C4] Close the HTTP session. Idempotent, and safe before any call."""
        if self._client is not None:
            self._client.close()
            self._client = None

    close = unload

    @staticmethod
    def _task_id_of(resp: Any) -> str:
        """Meshy answers a creation call with `{"result": "<task id>"}`."""
        if isinstance(resp, dict):
            for key in ("result", "id", "task_id"):
                value = resp.get(key)
                if isinstance(value, str) and value:
                    return value
        raise cloud_api.CloudAPIError(
            f"Meshy accepted the request but returned no task id: {resp!r}")

    def _fetch_task(self, endpoint: str, task_id: str) -> dict:
        task = self.client.request("GET", f"{endpoint}/{task_id}")
        if not isinstance(task, dict):
            raise cloud_api.CloudAPIError(
                f"unexpected task payload for {task_id}: {task!r}", task_id=task_id)
        return task

    def _model_url(self, task: dict) -> str:
        """URL of the requested format, with an actionable message when absent."""
        urls = task.get("model_urls") or {}
        url = urls.get(self.output_format)
        if isinstance(url, str) and url.startswith("http"):
            return url
        raise cloud_api.CloudAPIError(
            f"task {task.get('id')} finished but has no {self.output_format!r} "
            f"URL. Available: {sorted(urls)}. Set output_format to one of those.",
            task_id=task.get("id"), payload=task)

    def _clamp_polycount(self, value: Optional[int]) -> Optional[int]:
        """
        Keep `target_polycount` inside the documented range.

        `Gen3DObjectOperator` forwards TRELLIS.2's default of 1_000_000, which
        Meshy rejects with a terminal 400. Clamping (loudly) keeps the operator
        interchangeable (R6) instead of making the caller special-case backends.
        """
        if value is None:
            return None
        low, high = POLYCOUNT_RANGE
        clamped = max(low, min(int(value), high))
        if clamped != int(value):
            logger.warning("[meshy] target_polycount %s clamped to %s (API range %s-%s)",
                           value, clamped, low, high)
        return clamped

    # ── inference ─────────────────────────────────────────────────────────────

    def infer(
        self,
        image: Any = None,
        seed: int = 42,
        decimation_target: Optional[int] = None,
        texture_size: Optional[int] = None,
        *,
        prompt: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        image_url: Optional[str] = None,
        verbose: Optional[bool] = None,
        **provider_kwargs: Any,
    ) -> bytes:
        """
        Generate one 3D asset and return the model file bytes.

        Args:
            image: Reference image as `PIL.Image.Image`, `np.ndarray` or PNG
                   `bytes` — sent inline as a base64 data URI. Not a path (R3.2).
            seed: [C5] Forwarded for text-to-3D only; image-to-3D has no seed
                   parameter. Reproducibility is not guaranteed.
            decimation_target: Target face count → `target_polycount`, clamped to
                   the API's 100-300000 range.
            texture_size: Accepted for signature parity with `Trellis2Model` and
                   **ignored** — Meshy exposes no texture resolution parameter.
            prompt: Text prompt for text-to-3D. [A4 decision] Same rationale as
                   `TripoModel`: keeps text-to-3D reachable without a second slot.
            negative_prompt: Sent as `texture_prompt`'s counterpart is absent in
                   the API — rejected explicitly rather than silently dropped.
            image_url: Publicly reachable image URL, used instead of the data URI.
            verbose: Override the constructor's verbosity for this call.
            **provider_kwargs: Merged into the request body verbatim.

        Returns:
            The model file as `bytes`, in `output_format`.

        Raises:
            CloudAPIAuthError / CloudAPIQuotaError / CloudTaskFailed /
            CloudTaskTimeout — see `models/common/cloud_api.py`.
        """
        verbose = self.verbose if verbose is None else verbose
        if image is None and image_url is None and not prompt:
            raise ValueError(
                "MeshyModel.infer needs an image (`image=` / `image_url=`) or a "
                "text `prompt=`; got neither.")
        if negative_prompt:
            raise ValueError(
                "Meshy has no negative_prompt parameter. Fold the constraint into "
                "`prompt`, or use TripoModel which supports it.")
        if texture_size is not None:
            logger.debug("[meshy] texture_size=%s ignored; the API has no equivalent",
                         texture_size)

        polycount = self._clamp_polycount(decimation_target)
        use_image = image is not None or image_url is not None

        payload: dict[str, Any] = {
            "ai_model": self.model_path,
            "model_type": "lowpoly" if self.low_poly else "standard",
            "topology": self.topology,
        }
        if polycount is not None:
            payload["target_polycount"] = polycount
        if self.should_remesh is not None:
            payload["should_remesh"] = self.should_remesh

        if use_image:
            payload["should_texture"] = self.texture
            payload["enable_pbr"] = self.enable_pbr
        else:
            payload["mode"] = "preview"
            payload["prompt"] = prompt
            # [CONTRACT-DEVIATION C5] accepted by the text endpoint only.
            payload["seed"] = int(seed)
        payload.update(provider_kwargs)

        # [C8] Everything that changes the output goes into the key; the image is
        # represented by its hash, and the API key never appears.
        png = cloud_api.image_to_png_bytes(image) if image is not None else None
        fingerprint = {
            "provider": self.PROVIDER,
            "payload": payload,
            "image_sha256": cloud_api.bytes_hash(png) if png else None,
            "image_url": image_url,
            "refine": self.refine and self.texture and not use_image,
            "output_format": self.output_format,
        }
        if self.refine and self.texture and not use_image:
            fingerprint["refine_enable_pbr"] = self.enable_pbr
        cache_key = cloud_api.request_hash(fingerprint)
        cached = self._cache.get(cache_key, ext=self.output_format)
        if cached is not None:
            return self._cached_result(cached, cache_key)

        if not self._cache.enabled:
            return self._generate(cache_key, payload, png, image_url, use_image, verbose)

        client = self.client
        key = getattr(client, "_api_key", None) or cloud_api.require_api_key(
            self.api_key, "MESHY_API_KEY", SIGNUP_URL, who="MeshyModel")
        scope = cloud_api.request_hash({
            "request": cache_key,
            "api_base": getattr(client, "base_url", self.api_base).rstrip("/"),
            "credential_sha256": cloud_api.bytes_hash(key.encode("utf-8")),
        })
        # The first-stage lock protects the entire chain, including refine,
        # download and cache publication. Each stage retains its own task ID.
        first = PendingTaskStore(self._cache.root, scope)
        refine = PendingTaskStore(self._cache.root, cloud_api.request_hash({
            "request_scope": scope, "stage": "refine",
        })) if self.texture and self.refine and not use_image else None
        with first.lock(timeout=self.timeout):
            cached = self._cache.get(cache_key, ext=self.output_format)
            if cached is not None:
                return self._cached_result(cached, cache_key)
            return self._generate(cache_key, payload, png, image_url, use_image,
                                  verbose, first=first, refine=refine)

    def _cached_result(self, data: bytes, cache_key: str) -> bytes:
        self.last_call_info = {
            "provider": self.PROVIDER, "cached": True, "cache_key": cache_key,
            "resumed": False, "bytes": len(data),
            **(glb_summary(data) if self.output_format == "glb" else {}),
        }
        return data

    def _generate(self, cache_key: str, payload: dict, png: Optional[bytes],
                  image_url: Optional[str], use_image: bool, verbose: bool, *,
                  first: Optional[PendingTaskStore] = None,
                  refine: Optional[PendingTaskStore] = None) -> bytes:
        first_id = first.load() if first is not None else None
        refine_id = refine.load() if refine is not None else None
        if refine_id and not first_id:
            raise refine._error("Refine checkpoint has no preview checkpoint",
                                task_id=refine_id)
        resumed = first_id is not None

        t0 = time.time()
        task_ids: list[str] = []

        if use_image:
            endpoint = "/openapi/v1/image-to-3d"
            payload["image_url"] = (
                image_url if png is None else cloud_api.image_to_data_uri(png))
            task = self._submit_and_wait(endpoint, payload, verbose=verbose,
                                         task_ids=task_ids, store=first, task_id=first_id)
        else:
            endpoint = "/openapi/v2/text-to-3d"
            if refine_id:
                # Refine is already accepted; do not depend on preview retention.
                task_ids.append(first_id)
            else:
                task = self._submit_and_wait(endpoint, payload, verbose=verbose,
                                             task_ids=task_ids, store=first, task_id=first_id)
            if self.texture and self.refine:
                # [C6] Preview geometry is untextured; refine is a second billed
                # task that paints it. Skip with texture=False or refine=False.
                refine_payload = {
                    "mode": "refine",
                    "preview_task_id": task_ids[-1],
                    "ai_model": self.model_path,
                    "enable_pbr": self.enable_pbr,
                }
                task = self._submit_and_wait(endpoint, refine_payload,
                                             verbose=verbose, task_ids=task_ids,
                                             store=refine, task_id=refine_id)

        final_store = refine if refine is not None else first
        try:
            data = self.client.download(self._model_url(task))
        except cloud_api.CloudAPIError as exc:
            self._recovery_error(exc, final_store, task_ids[-1])
            raise
        elapsed = time.time() - t0

        info = glb_summary(data) if self.output_format == "glb" else {"bytes": len(data)}
        self.last_call_info = {
            "provider": self.PROVIDER,
            "model": self.model_path,
            "task_id": task_ids[-1],
            "task_ids": task_ids,
            "elapsed_sec": round(elapsed, 2),
            "credits": task.get("credits_consumed", task.get("credits")),
            "cached": False,
            "resumed": resumed,
            "cache_key": cache_key,
            "format": self.output_format,
            # The provider's own render of what it produced. Recording the URL
            # (rather than downloading it here) keeps the model out of artifact
            # placement (R1.2) while giving the caller a free visual check —
            # "the asset exists" and "the asset is what was asked for" are
            # different claims, and only a picture settles the second one.
            "preview_url": task.get("thumbnail_url"),
            **info,
        }
        # R9.8 — one line per billed call.
        logger.info(
            "[meshy] task=%s model=%s %.1fs credits=%s triangles=%s bytes=%s",
            task_ids[-1], self.model_path, elapsed, self.last_call_info.get("credits"),
            info.get("triangles"), len(data),
        )
        if verbose:
            print(f"[meshy] {self.last_call_info}")

        self._cache.put(cache_key, data, {
            "provider": self.PROVIDER, "payload": payload,
            **{k: v for k, v in self.last_call_info.items() if k != "cache_key"},
        }, ext=self.output_format)
        # Clear refine first: a cleanup failure must not leave an orphaned
        # refine ID without its preview ID. The completed cache is authoritative.
        for store in (refine, first):
            if store is not None:
                try:
                    store.clear()
                except OSError:
                    logger.warning("[meshy] could not remove checkpoint %s", store.path)
                    break
        return data

    @staticmethod
    def _recovery_error(exc: cloud_api.CloudAPIError,
                        store: Optional[PendingTaskStore], task_id: str) -> None:
        exc.task_id = exc.task_id or task_id
        if store is not None:
            exc.args = (f"{exc} Task {task_id} checkpoint: {store.path}. "
                        "Rerun the same input to resume; remove the checkpoint only "
                        "to explicitly allow a new paid task.",)

    def _submit_and_wait(self, endpoint: str, payload: dict, *,
                         verbose: bool, task_ids: list[str],
                         store: Optional[PendingTaskStore] = None,
                         task_id: Optional[str] = None) -> dict:
        """Resume a stage, or checkpoint its accepted ID before polling."""
        if task_id is None:
            created = self.client.request("POST", endpoint, json_body=payload)
            task_id = self._task_id_of(created)
            if store is not None:
                store.save(task_id)
        task_ids.append(task_id)
        if verbose:
            print(f"[meshy] polling task {task_id} ({endpoint}, "
                  f"mode={payload.get('mode', 'image')})")
        try:
            return cloud_api.poll_task(
                lambda: self._fetch_task(endpoint, task_id),
                task_id=task_id,
                timeout=self.timeout,
                poll_interval=self.poll_interval,
                done_states=DONE_STATES,
                failed_states=FAILED_STATES,
                verbose=verbose,
            )
        except cloud_api.CloudAPIError as exc:
            self._recovery_error(exc, store, task_id)
            raise

    def infer_and_save(
        self,
        image: Any = None,
        output_path: Optional[str] = None,
        seed: int = 42,
        decimation_target: Optional[int] = None,
        texture_size: Optional[int] = None,
        *,
        prompt: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        image_url: Optional[str] = None,
        verbose: Optional[bool] = None,
        **provider_kwargs: Any,
    ) -> str:
        """
        [C7] The documented R1.2 exception: run `infer()` and write the result.

        The path comes **in** from the caller and is never derived here.

        Args:
            output_path: Destination file. Parent directories are created.
            (everything else: see `infer`)

        Returns:
            `str(output_path)`.
        """
        if not output_path:
            raise ValueError("output_path is required (model_require.md R1.2)")
        data = self.infer(
            image, seed, decimation_target, texture_size,
            prompt=prompt, negative_prompt=negative_prompt, image_url=image_url,
            verbose=verbose, **provider_kwargs,
        )
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return str(out)

    # ── account ───────────────────────────────────────────────────────────────

    def balance(self) -> dict:
        """Remaining credits: ``{"balance": int}``. Cheap and unbilled."""
        resp = self.client.request("GET", "/openapi/v1/balance")
        return resp if isinstance(resp, dict) else {"balance": resp}
