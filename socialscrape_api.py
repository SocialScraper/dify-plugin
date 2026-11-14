"""A small client for the SocialScrape v1 API.

Every request goes to one fixed origin (API_BASE), with a finite timeout and
redirects not followed. The API key is sent only as an x-api-key header and
never appears in an error message.
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Optional

import httpx

API_BASE = "https://api.socialscrape.dev/v1"
USER_AGENT = "socialscrape-dify-plugin/0.1.0"

# One request: 10 s to connect. A short video without captions is transcribed
# while the request is held open, which can take 15 to 45 s, so reads get 60 s.
TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# Any request still running after about 25 s is answered with a job (HTTP 202).
# How long a tool waits for that job before giving up, and how often it checks.
# Dify gives a tool call 120 s in total.
JOB_WAIT_SECONDS = 75.0
JOB_POLL_SECONDS = 5.0
JOB_ID = re.compile(r"^job_[A-Za-z0-9]+$")

# Platforms whose profile is looked up by handle.
PROFILE_PLATFORMS = ("instagram", "tiktok", "twitter", "threads", "bluesky", "snapchat", "twitch", "truthsocial")
# Platforms whose list of a profile's posts is looked up by handle.
POST_PLATFORMS = ("instagram", "tiktok", "threads", "bluesky", "truthsocial")


class SocialScrapeError(ValueError):
    """An API error, with the API's own code and message."""

    def __init__(self, message: str, code: Optional[str] = None, status: Optional[int] = None):
        super().__init__(message)
        self.code = code
        self.status = status


def _error(status: int, payload: Any) -> SocialScrapeError:
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        text = error.get("message") or "Request failed."
        return SocialScrapeError(f"{code}: {text}" if code else text, code=code, status=status)
    if status == 401:
        return SocialScrapeError("Invalid or missing API key.", code="unauthorized", status=status)
    return SocialScrapeError(f"SocialScrape answered HTTP {status}.", status=status)


def bare_handle(value: Any) -> str:
    """A handle without spaces or a leading at sign. Raises when nothing is left."""
    handle = str(value or "").strip().lstrip("@").strip()
    if not handle:
        raise ValueError("handle is required: the account name, with or without the @.")
    return handle


def pick_platform(value: Any, allowed: tuple[str, ...]) -> str:
    platform = str(value or "").strip().lower()
    if platform == "x":
        platform = "twitter"
    if platform not in allowed:
        raise ValueError("platform must be one of: " + ", ".join(allowed) + ".")
    return platform


class SocialScrapeClient:
    def __init__(self, api_key: Optional[str], *, transport: Optional[httpx.BaseTransport] = None,
                 sleep: Optional[Callable[[float], None]] = None, clock: Optional[Callable[[], float]] = None):
        key = (api_key or "").strip()
        if not key:
            raise SocialScrapeError("A SocialScrape API key is required.", code="unauthorized")
        self._client = httpx.Client(
            base_url=API_BASE,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"x-api-key": key, "User-Agent": USER_AGENT, "Accept": "application/json"},
            transport=transport,
        )
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SocialScrapeClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> tuple[int, dict[str, Any]]:
        query = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        try:
            response = self._client.get(path, params=query)
        except httpx.TimeoutException:
            raise SocialScrapeError("SocialScrape did not answer in time. Try again.", code="timeout") from None
        except httpx.HTTPError:
            raise SocialScrapeError("Could not reach SocialScrape. Try again.", code="network") from None
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 300 or not isinstance(payload, dict) or payload.get("ok") is False:
            raise _error(response.status_code, payload)
        return response.status_code, payload

    def _wait_for_job(self, payload: dict[str, Any]) -> tuple[dict[str, Any], Optional[str]]:
        """Poll a queued job, for free, until it finishes or JOB_WAIT_SECONDS pass.

        Returns the latest answer and, when the job is still running, its id.
        A failed job raises its own error from the poll.
        """
        job_id = _job_id(payload)
        if not job_id:
            return payload, None
        if not JOB_ID.match(job_id):
            raise SocialScrapeError("SocialScrape queued a job without a usable job id.", code="upstream_error")
        deadline = self._clock() + JOB_WAIT_SECONDS
        while self._clock() < deadline:
            self._sleep(JOB_POLL_SECONDS)
            _, payload = self._get(f"/jobs/{job_id}")
            if not _job_id(payload):
                return payload, None
        return payload, job_id

    def _finished(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The finished answer, never a job: a job still running after the wait is an error."""
        payload, running = self._wait_for_job(payload)
        if running:
            raise SocialScrapeError(
                f"SocialScrape is still working on this request (job {running}). Try again in a minute.",
                code="timeout",
            )
        return payload

    def credits(self) -> dict[str, Any]:
        _, payload = self._get("/account/credits")
        data = payload.get("data") or {}
        return {"credits_remaining": data.get("credits_remaining"), "team_id": data.get("team_id")}

    def profile(self, platform: str, handle: str) -> dict[str, Any]:
        _, payload = self._get(f"/{platform}/profile", {"handle": handle})
        return _with_usage(self._finished(payload))

    def profile_posts(self, platform: str, handle: str, cursor: Optional[str] = None) -> dict[str, Any]:
        # TikTok publishes videos only, so its list lives under /profile/videos.
        route = "videos" if platform == "tiktok" else "posts"
        _, payload = self._get(f"/{platform}/profile/{route}", {"handle": handle, "cursor": cursor})
        payload = self._finished(payload)
        data = payload.get("data") or {}
        result = {
            "posts": data.get("items") or [],
            "next_cursor": data.get("cursor"),
            "has_more": bool(data.get("has_more")),
        }
        result["usage"] = _usage(payload)
        return result

    def transcript(self, url: str, language: Optional[str], timestamps: bool, allow_long: bool) -> dict[str, Any]:
        """Fetch a transcript; when the API queues a long transcription job, wait for it.

        The wait is bounded by JOB_WAIT_SECONDS. A job still running after that
        comes back as {"status": "processing", "job_id": ...}: calling again
        with the same link returns the finished transcript from the cache.
        """
        params: dict[str, Any] = {"url": url, "language": language}
        if allow_long:
            params["allow_long_transcription"] = "true"
        _, payload = self._get("/transcript", params)
        payload, running = self._wait_for_job(payload)
        if running:
            return {
                "status": "processing",
                "job_id": running,
                "url": url,
                "message": (
                    "The transcription is still running. Call get_transcript again with the same link in a "
                    "minute or two: the finished transcript is cached, so it uses credits only once."
                ),
            }
        result = _with_usage(payload)
        if not timestamps:
            result.pop("segments", None)
        return result


def _job_id(payload: dict[str, Any]) -> Optional[str]:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("object") == "job":
        return data.get("job_id")
    return None


def _usage(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "credits_charged": payload.get("credits_charged"),
        "credits_remaining": payload.get("credits_remaining"),
        "cached": bool(payload.get("cached")),
    }


def _with_usage(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload.get("data") or {})
    data["usage"] = _usage(payload)
    return data
