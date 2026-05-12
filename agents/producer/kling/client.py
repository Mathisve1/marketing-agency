"""Kling AI direct API client with JWT auth and a sync polling loop.

Authentication uses Kling's documented JWT pattern: HS256-signed token with
the access key as `iss`, 30-minute expiry, and a 5-second nbf buffer. Tokens
are cached and re-minted ~5 minutes before expiry to amortize auth cost.

Every API call is appended to logs/kling-api.jsonl for audit + credit
reconciliation.

Endpoint paths are env-configurable so a Kling schema change is a .env edit,
not a code release:
  KLING_API_BASE_URL  (default: https://api.klingai.com)
  KLING_V2V_ENDPOINT  (default: /v1/videos/video2video)
  KLING_I2V_ENDPOINT  (default: /v1/videos/image2video)
  KLING_T2V_ENDPOINT  (default: /v1/videos/text2video)
  KLING_POLL_PATH     (default: /v1/videos/{task_id})
  KLING_MODEL         (default: kling-v3-master)
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
# Config (env-overridable; defaults track current public-wrapper conventions)
# --------------------------------------------------------------------------- #

BASE_URL = os.getenv("KLING_API_BASE_URL", "https://api.klingai.com")
V2V_ENDPOINT = os.getenv("KLING_V2V_ENDPOINT", "/v1/videos/video2video")
I2V_ENDPOINT = os.getenv("KLING_I2V_ENDPOINT", "/v1/videos/image2video")
T2V_ENDPOINT = os.getenv("KLING_T2V_ENDPOINT", "/v1/videos/text2video")
POLL_PATH = os.getenv("KLING_POLL_PATH", "/v1/videos/{task_id}")
DEFAULT_MODEL = os.getenv("KLING_MODEL", "kling-v3-master")

TOKEN_TTL_SEC = 30 * 60         # Kling JWT lifetime per docs
TOKEN_REFRESH_BUFFER = 5 * 60   # re-mint 5 minutes before expiry
POLL_INTERVAL_SEC = 5
POLL_MAX_WAIT_SEC = int(os.getenv("KLING_POLL_MAX_WAIT_SEC", str(20 * 60)))
HTTP_TIMEOUT_SEC = 60

# Terminal statuses - defensive because Kling has shipped two enums across versions.
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
# Encoding helpers
# --------------------------------------------------------------------------- #


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _image_field(value: Union[str, Path, None]) -> Optional[str]:
    """Accept a local Path (base64-inline) or an http(s) URL (pass through)."""
    if value is None:
        return None
    if isinstance(value, Path) or not str(value).startswith(("http://", "https://")):
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {path}")
        return _b64(path)
    return str(value)


def _video_field(value: Union[str, Path, None]) -> Optional[str]:
    """Same accept-rule as _image_field. Hard-stops oversized inlines so we
    don't silently blow up the request body - surface this to the user instead."""
    if value is None:
        return None
    if isinstance(value, Path) or not str(value).startswith(("http://", "https://")):
        path = Path(value)
        if not path.exists():
            raise FileNotFoundError(f"Video not found: {path}")
        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > 25:
            raise ValueError(
                f"Referral video {path} is {size_mb:.1f}MB; inline base64 would "
                f"bloat the request. Upload to a public URL (S3/CDN) and pass that."
            )
        return _b64(path)
    return str(value)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class KlingClient:
    """Direct Kling API client. Construct once per session; token cache is internal."""

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

        # Redact heavy binary fields so the audit log stays readable.
        redacted = {
            k: ("<base64>" if k in {"image", "image_tail", "reference_video"} and v else v)
            for k, v in body.items()
        }
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

    # ---- submission methods ----

    def submit_video_to_video(
        self,
        reference_video: Union[str, Path],
        prompt: str,
        *,
        character_image: Union[str, Path, None] = None,
        product_image: Union[str, Path, None] = None,
        negative_prompt: str = "",
        model: str = DEFAULT_MODEL,
        duration: int = 10,
        aspect_ratio: str = "9:16",
        mode: str = "professional",
        cfg_scale: float = 0.5,
        camera_control: Optional[dict] = None,
        extra: Optional[dict] = None,
    ) -> str:
        """Submit a V2V (motion control) task. Returns task_id."""
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "mode": mode,
            "cfg_scale": cfg_scale,
            "reference_video": _video_field(reference_video),
        }
        if character_image is not None:
            body["image"] = _image_field(character_image)
        if product_image is not None:
            body["image_tail"] = _image_field(product_image)
        if camera_control:
            body["camera_control"] = camera_control
        if extra:
            body.update(extra)

        resp = self._post(V2V_ENDPOINT, body)
        return _extract_task_id(resp)

    def submit_image_to_video(
        self,
        image: Union[str, Path],
        prompt: str,
        *,
        image_tail: Union[str, Path, None] = None,
        negative_prompt: str = "",
        model: str = DEFAULT_MODEL,
        duration: int = 10,
        aspect_ratio: str = "9:16",
        mode: str = "professional",
        cfg_scale: float = 0.5,
        camera_control: Optional[dict] = None,
    ) -> str:
        """I2V fallback for runs without a referral video."""
        body = {
            "model": model,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "duration": duration,
            "aspect_ratio": aspect_ratio,
            "mode": mode,
            "cfg_scale": cfg_scale,
            "image": _image_field(image),
        }
        if image_tail is not None:
            body["image_tail"] = _image_field(image_tail)
        if camera_control:
            body["camera_control"] = camera_control
        resp = self._post(I2V_ENDPOINT, body)
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
# Defensive response shape helpers - Kling has shipped multiple shapes
# across v1/v2 endpoints; centralise the variance here.
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
