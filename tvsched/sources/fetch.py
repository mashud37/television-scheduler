import json
import time
import urllib.error
import urllib.request


class SourceError(RuntimeError):
    pass


class SourceAuthError(SourceError):
    pass


REQUEST_TIMEOUT_SECONDS = 30
REQUEST_RETRIES = 3
MAX_RETRY_AFTER_SECONDS = 30


def get_json(
    url: str,
    headers: dict | None = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    retries: int = REQUEST_RETRIES,
) -> dict:
    """GET a URL and parse the response as JSON, retrying transient failures.

    Args:
        url: Fully-formed request URL including query string.
        headers: Extra request headers.
        timeout: Per-attempt socket timeout in seconds.
        retries: Total attempts before giving up.

    Returns:
        The decoded JSON body.

    Raises:
        SourceAuthError: On 401 or 403, which signals a credential or blocking problem
            rather than a transient one, and is never retried.
        SourceError: On any other failure once retries are exhausted.
    """
    last = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, headers=headers or {})
        wait = 2 ** attempt
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SourceAuthError(f"{e.code} {e.reason} for {url}") from e
            asked = e.headers.get("Retry-After", "")
            if e.code == 429 and asked.isdigit():
                wait = min(int(asked), MAX_RETRY_AFTER_SECONDS)
            last = e
        except Exception as e:
            last = e
        if attempt < retries:
            time.sleep(wait)
    raise SourceError(f"{type(last).__name__}: {last} for {url}")
