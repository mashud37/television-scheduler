import json
import time
import urllib.error
import urllib.request


class SourceError(RuntimeError):
    pass


class SourceAuthError(SourceError):
    pass


def get_json(url: str, headers: dict | None = None, timeout: int = 30, retries: int = 3) -> dict:
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
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SourceAuthError(f"{e.code} {e.reason} for {url}") from e
            last = e
        except Exception as e:
            last = e
        if attempt < retries:
            time.sleep(2 ** (attempt - 1))
    raise SourceError(f"{type(last).__name__}: {last} for {url}")
