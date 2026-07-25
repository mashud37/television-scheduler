import base64
import os
from datetime import datetime
from urllib.parse import quote

from .fetch import get_json

API_HOST = "https://api.zdf.de"
BROADCASTS_PATH = "/cmdm/epg/broadcasts"
BROADCASTS_KEY = "http://zdf.de/rels/cmdm/broadcasts"
PROGRAMME_ITEM_KEY = "http://zdf.de/rels/cmdm/programme-item"
VIDEO_PAGE_KEY = "http://zdf.de/rels/content/video-page"
SHARING_URL_KEY = "http://zdf.de/rels/sharing-url"

_DEFAULT_KEY_B64 = "YWhCYWVNZWVrYWl5NW9oc2FpNGJlZTRraTZPb3BvaTVxdWFpbGllYg=="

PAGE_SIZE = 100
MAX_PAGES = 20


def api_key() -> str:
    return os.environ.get("ZDF_API_KEY") or base64.b64decode(_DEFAULT_KEY_B64).decode()


def _headers() -> dict:
    return {
        "Accept": "application/vnd.de.zdf.v1.0+json",
        "Origin": "https://www.zdf.de",
        "Api-Auth": f"Bearer {api_key()}",
    }


def fetch_broadcasts(service_id: str, start: datetime, end: datetime) -> list[dict]:
    """Fetch every EPG broadcast for one ZDF-family channel in a time range.

    Args:
        service_id: The tvServices identifier, e.g. "zdf" or "arte".
        start: Timezone-aware range start.
        end: Timezone-aware range end.

    Returns:
        Raw broadcast objects, in ascending airtime order.
    """
    frm, to = quote(start.isoformat()), quote(end.isoformat())
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{API_HOST}{BROADCASTS_PATH}?limit={PAGE_SIZE}&order=asc"
            f"&tvServices={service_id}&from={frm}&to={to}&page={page}"
        )
        items = get_json(url, headers=_headers()).get(BROADCASTS_KEY) or []
        out.extend(items)
        if len(items) < PAGE_SIZE:
            break
    return out


def fetch_detail(broadcast: dict) -> dict:
    """Fetch the programme-item record, resolving the public page URL alongside it.

    The programme-item only links an internal content path, which is not reachable on
    www.zdf.de. The viewer-facing URL lives one hop further on, in the epg-page's
    sharing-url relation, so it is resolved here and returned under SHARING_URL_KEY.
    """
    path = broadcast.get(PROGRAMME_ITEM_KEY)
    if not path:
        return {}
    detail = get_json(f"{API_HOST}{path}", headers=_headers())

    page = detail.get(VIDEO_PAGE_KEY)
    if page:
        try:
            detail[SHARING_URL_KEY] = get_json(
                f"{API_HOST}{page}", headers=_headers()
            ).get(SHARING_URL_KEY, "")
        except Exception:
            detail[SHARING_URL_KEY] = ""
    return detail


def search_url(title: str, channel: str = "") -> str:
    """A search link, for programmes whose detail record carries no public page URL."""
    core = (title or "").split(" - ")[0].strip()
    if not core:
        return ""
    if channel == "ARTE":
        return f"https://www.arte.tv/de/search/?q={quote(core)}"
    return f"https://www.zdf.de/suche?q={quote(core)}"


def format_cast(detail: dict) -> str:
    entries = ((detail.get("actorDetails") or {}).get("actorDetail")) or []
    parts = []
    for e in entries:
        name = (e.get("name") or "").strip()
        role = (e.get("role") or "").strip()
        if name:
            parts.append(f"{name} - {role}" if role else name)
    return ", ".join(parts)


def format_crew(detail: dict) -> str:
    entries = ((detail.get("crewDetails") or {}).get("crewDetail")) or []
    parts = []
    for e in entries:
        fn = (e.get("function") or "").strip()
        name = (e.get("name") or "").strip()
        if fn.islower():
            fn = fn.capitalize()
        if name:
            parts.append(f"{fn} - {name}" if fn else name)
    return ", ".join(parts)
