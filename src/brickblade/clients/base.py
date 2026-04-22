from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class ClientError(Exception):
    """Generic client failure (network, auth, parsing)."""


class NotFound(ClientError):
    """Upstream returned 404 or empty result."""


class RateLimited(ClientError):
    """Upstream returned 429."""


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 1.5,
    **kwargs,
) -> httpx.Response:
    """GET/POST with retry on 429 and 5xx. Raises on 4xx (except 429)."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            r = client.request(method, url, **kwargs)
        except httpx.HTTPError as e:
            last_exc = e
            log.warning("%s %s failed: %s (attempt %d)", method, url, e, attempt + 1)
            time.sleep(backoff ** attempt)
            continue

        if r.status_code == 404:
            raise NotFound(f"{method} {url} → 404")
        if r.status_code == 429:
            retry_after = float(r.headers.get("retry-after", backoff ** attempt))
            log.warning("429 on %s, sleeping %.1fs", url, retry_after)
            time.sleep(retry_after)
            continue
        if 500 <= r.status_code < 600:
            log.warning(
                "%d on %s (attempt %d/%d)", r.status_code, url, attempt + 1, retries
            )
            time.sleep(backoff ** attempt)
            continue
        if r.status_code >= 400:
            raise ClientError(f"{method} {url} → {r.status_code}: {r.text[:200]}")
        return r

    if last_exc:
        raise ClientError(f"{method} {url} failed after {retries} retries: {last_exc}")
    raise RateLimited(f"{method} {url} rate-limited after {retries} retries")
