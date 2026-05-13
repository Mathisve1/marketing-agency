"""Kling AI direct API client - Omni-Video endpoint, JWT auth, sync polling.

Authentication uses Kling's documented JWT pattern: HS256-signed token with
the access key as `iss`, 30-minute expiry, and a 5-second nbf buffer. Tokens
are cached and re-minted ~5 minutes before expiry.

The Omni-Video endpoint is a unified V2V/I2V surface introduced in Kling's
enterprise API. Static assets (character + product images) live in
`image_list`; motion references live in `video_list`; the text prompt
references them via `<<<image_n>>>` / `<<<video_n>>>` tags so the model
knows which asset to apply where.

Every API call is appended to logs/kling-api.jsonl for audit + credit
reconciliation. Binary fields are redacted in the log so it stays readable.

Endpoint paths are env-configurable so a Kling schema change is a .env edit:
  KLING_API_BASE_URL  (default: https://api-singapore.klingai.com)
  KLING_OMNI_ENDPOINT (default: /v1/videos/omni-video)
  KLING_POLL_PATH     (default: /v1/videos/{task_id})
  KLING_MODEL         (default: kling-v3-omni)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

import jwt
import requests

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Config (env-overridable)
# --------------------------------------------------------------------------- #

BASE_URL = os.getenv("KLING_API_BASE_URL", "https://api-singapore.klingai.com")
OMNI_ENDPOINT = os.getenv("KLING_OMNI_ENDPOINT", "/v1/videos/omni-video")
POLL_PATH = os.getenv("KLING_POLL_PATH", "/v1/videos/{task_id}")
DEFAULT_MODEL = os.getenv("KLING_MODEL", "kling-v3-omni")

TOKEN_TTL_SEC = 30 * 60
TOKEN_REFRESH_BUFFER = 5 * 60
POLL_INTERVAL_SEC = 5
POLL_MAX_WAIT_SEC = int(os.getenv("KLING_POLL_MAX_WAIT_SEC", str(20 * 60)))
HTTP_TIMEOUT_SEC = 60

SUCCESS_STATUSES = {"succeed", "success", "completed"}
FAILURE_STATUSES = {"failed", "error"}


class KlingAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


# --------------------------------------------------------------------------- #
# JWT
# --------------------------------------------------------------------------- #


def encode_jwt_token(access_key: str, secret_key: str, ttl_sec: int = TOKEN_TTL_SEC) -> tuple[str, int]:
    """Build a Kling JWT per their HS256 spec. Returns (token, expiry_epoch)."""
    now = int(time.time())
    expires_at = now + ttl_sec
    headers = {"alg": "HS256", "typ": "JWT"}
    payload = {"iss": access_key, "exp": expires_at, "nbf": now - 5}
    token = jwt.encode(payload, secret_key, algorithm="HS256", headers=headers)
    return token, expires_at


# --------------------------------------------------------------------------- #
# Encoding helpers - accept Path/local-file (-> base64) or http(s) URL (pass through)
# --------------------------------------------------------------------------- #


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _image_field(value: Union[str, Path]) -> str:
    if isinstance(value, Path) or not str(value).startswith(("http://", "https://")):
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        return _b64(path)
    return str(value)


def _video_field(value: Union[str, Path]) -> str:
    """Same accept-rule as _image_field. Hard-stops oversized inlines so we
    don't silently blow up the request body - surface this to the user instead."""
    if isinstance(value, Path) or not str(value).startswith(("http://", "https://")):
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {path}")
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 25:
            raise ValueError(
                f"Reference video {path} is {size_mb:.1f}MB; inline base64 would "
                f"bloat the request. Upload to a public URL (S3/CDN) and pass that."
            )
        return _b64(path)
    return str(value)


def _redact_asset_list(asset_list: list[dict]) -> list[dict]:
    """Replace base64 image_url / video_url payloads with '<base64>' in audit-log copies."""
    out = []
    for item in asset_list:
        redacted = dict(item)
        for key in ("image_url", "video_url"):
            val = redacted.get(key)
            if val and isinstance(val, str) and not val.startswith(("http://", "https://")):
                redacted[key] = "<base64>"
        out.append(redacted)
    return out


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class KlingClient:
    """Direct Kling Omni-Video API client. Construct once per session; token cache is internal."""

    def __init__(
        self,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: str = BASE_URL,
        audit_log_path: Optional[Path] = None,
    ):
        self.access_key = access_key or os.getenv("KLING_API_KEY")
        self.secret_key = secret_key or os.getenv("KLING_API_SECRET")
        if not self.access_key or not self.secret_key:
            raise RuntimeError(
                "KLING_API_KEY and KLING_API_SECRET must be set in .env "
                "(or passed explicitly to KlingClient)."
            )
        self.base_url = base_url.rstrip("/")
        self.audit_log_path = audit_log_path or Path("logs/kling-api.jsonl")
        self._token: Optional[str] = None
        self._token_expires_at: int = 0

    # ---- JWT token cache ----

    def _bearer_token(self) -> str:
        if self._token and time.time() < self._token_expires_at - TOKEN_REFRESH_BUFFER:
            return self._token
        self._token, self._token_expires_at = encode_jwt_token(self.access_key, self.secret_key)
        logger.debug("Minted fresh Kling JWT (exp=%s)", self._token_expires_at)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bearer_token()}",
            "Content-Type": "application/json",
        }

    # ---- audit log ----

    def _audit(self, event: dict[str, Any]) -> None:
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str, ensure_ascii=False) + "\n")

    # ---- HTTP primitives ----

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.base_url}{path}"
        try:
            r = requests.post(url, json=body, headers=self._headers(), timeout=HTTP_TIMEOUT_SEC)
        except requests.RequestException as e:
            self._audit({"op": "POST", "path": path, "status": "network_error", "error": str(e)})
            raise KlingAPIError(f"Kling POST {path} failed: {e}") from e

        # Redact base64 payloads inside image_list / video_list for the audit log.
        redacted = dict(body)
        if "image_list" in redacted:
            redacted["image_list"] = _redact_asset_list(redacted["image_list"])
        if "video_list" in redacted:
            redacted["video_list"] = _redact_asset_list(redacted["video_list"])

        self._audit({
            "op": "POST", "path": path, "status_code": r.status_code,
            "request": redacted, "response": _truncate(r.text, 4000),
        })
        if not r.ok:
            raise KlingAPIError(
                f"Kling POST {path} -> {r.status_code}: {r.text}",
                status_code=r.status_code, payload=r.text,
            )
        return r.json()

    def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        try:
            r = requests.get(url, headers=self._headers(), timeout=HTTP_TIMEOUT_SEC)
        except requests.RequestException as e:
            self._audit({"op": "GET", "path": path, "status": "network_error", "error": str(e)})
            raise KlingAPIError(f"Kling GET {path} failed: {e}") from e

        self._audit({
            "op": "GET", "path": path, "status_code": r.status_code,
            "response": _truncate(r.text, 2000),
        })
        if not r.ok:
            raise KlingAPIError(
                f"Kling GET {path} -> {r.status_code}: {r.text}",
                status_code=r.status_code, payload=r.text,
            )
        return r.json()

    # ---- submission ----

    def submit_omni_video(
        self,
        prompt: str,
        *,
        images: Optional[list[Union[str, Path]]] = None,
        videos: Optional[list[Union[str, Path]]] = None,
        image_type: str = "first_frame",
        video_refer_type: str = "feature",
        keep_original_sound: bool = False,
        negative_prompt: str = "",
        model: str = DEFAULT_MODEL,
        duration: int = 10,
        aspect_ratio: str = "9:16",
        mode: str = "professional",
        cfg_scale: float = 0.5,
        extra: Optional[dict] = None,
    ) -> str:
        """Submit a Kling Omni-Video task. Returns task_id.

        `images` and `videos` are positional lists - their order determines
        the <<<image_n>>> / <<<video_n>>> tag mapping in the prompt
        (1-indexed: images[0] -> <<<image_1>>>, videos[0] -> <<<video_1>>>).
        Each entry can be a local Path / path-string (base64-inlined) or an
        http(s) URL (passed through).

        Permissive on count - validate the agency convention
        ([character, product]) inside the Producer agent, not here.

        image_type:          per-image type tag ('first_frame' by default per Omni docs).
        video_refer_type:    per-video role ('feature' for motion reference).
        keep_original_sound: whether the reference video's audio influences the output.
        """
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "mode": mode,
            "cfg_scale": cfg_scale,
        }

        if images:
            body["image_list"] = [
                {"image_url": _image_field(img), "type": image_type}
                for img in images
            ]
        if videos:
            body["video_list"] = [
                {
                    "video_url": _video_field(vid),
                    "refer_type": video_refer_type,
                    "keep_original_sound": "yes" if keep_original_sound else "no",
                }
                for vid in videos
            ]
        if extra:
            body.update(extra)

        resp = self._post(OMNI_ENDPOINT, body)
        return _extract_task_id(resp)

    # ---- polling ----

    def poll_task(self, task_id: str) -> dict:
        return self._get(POLL_PATH.format(task_id=task_id))

    def wait_for_completion(
        self,
        task_id: str,
        poll_interval_sec: int = POLL_INTERVAL_SEC,
        max_wait_sec: int = POLL_MAX_WAIT_SEC,
    ) -> dict:
        """Block until terminal status or `max_wait_sec`. Returns final task."""
        deadline = time.time() + max_wait_sec
        while True:
            task = self.poll_task(task_id)
            status = _status_of(task).lower()
            if status in SUCCESS_STATUSES:
                return task
            if status in FAILURE_STATUSES:
                err = task.get("error") or task.get("message") or "unknown failure"
                raise KlingAPIError(f"Kling task {task_id} failed: {err}", payload=task)
            if time.time() >= deadline:
                raise TimeoutError(
                    f"Kling task {task_id} did not complete within {max_wait_sec}s "
                    f"(last status: {status})"
                )
            time.sleep(poll_interval_sec)

    # ---- download ----

    def download_video(self, task: dict, dest_path: Path) -> Path:
        """Stream the final MP4 to `dest_path`. Kling URLs expire ~24h post-generation."""
        url = _video_url_of(task)
        if not url:
            raise KlingAPIError("Completed task has no video URL", payload=task)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with requests.get(url, stream=True, timeout=HTTP_TIMEOUT_SEC) as r:
            r.raise_for_status()
            with dest_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
        self._audit({
            "op": "DOWNLOAD",
            "task_id": (task.get("task_id") or task.get("id") or (task.get("data") or {}).get("task_id")),
            "dest": str(dest_path), "size_bytes": dest_path.stat().st_size,
        })
        return dest_path


# --------------------------------------------------------------------------- #
# Defensive response shape helpers - Kling ships multiple shapes across
# endpoint versions; centralise the variance here.
# --------------------------------------------------------------------------- #


def _truncate(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[:max_len] + "...<truncated>"


def _extract_task_id(resp: dict) -> str:
    if "task_id" in resp:
        return str(resp["task_id"])
    data = resp.get("data") or {}
    for key in ("task_id", "id"):
        if key in data:
            return str(data[key])
    raise KlingAPIError("No task_id in Kling response", payload=resp)


def _status_of(task: dict) -> str:
    data = task.get("data") or task
    return str(data.get("task_status") or data.get("status") or "pending")


def _video_url_of(task: dict) -> Optional[str]:
    data = task.get("data") or task
    result = data.get("task_result") or data
    videos = result.get("videos")
    if isinstance(videos, list) and videos:
        return videos[0].get("url")
    return result.get("video_url") or result.get("videoUrl")
