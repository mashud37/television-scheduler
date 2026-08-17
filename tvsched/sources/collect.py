import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import ard, zdf
from .channels import ARD_CHANNELS, ARD_ID_TO_CANONICAL, ZDF_CHANNELS

WEEKDAYS_DE = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

ARD_MAX_DAYS = 8
DEFAULT_DAYS = 8
DETAIL_FROM_HOUR = 18
DETAIL_TO_HOUR = 2
DEFAULT_WORKERS = 12

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class CollectResult:
    rows: list[dict] = field(default_factory=list)
    sources_ok: list[str] = field(default_factory=list)
    sources_failed: dict[str, str] = field(default_factory=dict)
    detail_attempted: int = 0
    detail_failed: int = 0

    @property
    def detail_failure_ratio(self) -> float:
        return self.detail_failed / self.detail_attempted if self.detail_attempted else 0.0


def _clean(text) -> str:
    if not text:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", str(text)))).strip()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _in_detail_window(start: datetime) -> bool:
    return start.hour >= DETAIL_FROM_HOUR or start.hour < DETAIL_TO_HOUR


def _row(channel: str, title: str, start: datetime, end: datetime | None, **extra) -> dict:
    end = end or start + timedelta(minutes=45)
    row = {
        "channel": channel,
        "title": title,
        "date": f"{start.day}.{start.month}.",
        "weekday": WEEKDAYS_DE[start.weekday()],
        "time": f"{start:%H:%M}-{end:%H:%M}",
        "start_utc": start.astimezone(timezone.utc).isoformat(),
        "end_utc": end.astimezone(timezone.utc).isoformat(),
        "href": "",
        "Country": "",
        "Year": "",
        "Genre": "",
        "Rating": "",
        "Description": "",
        "Quote": "",
        "Cast": "",
        "Crew": "",
    }
    row.update({k: v for k, v in extra.items() if v})
    return row


def _fetch_detail_one(item: tuple[str, dict, dict]) -> dict:
    source, row, raw = item
    fetch = zdf.fetch_detail if source == "zdf" else ard.fetch_teaser
    try:
        detail = fetch(raw)
        error = None
    except Exception as e:
        detail = {}
        error = e
    return {"item": item, "detail": detail, "error": error}


def _fetch_details(pending: list[tuple[str, dict, dict]], workers: int, log) -> dict:
    """Fetch the detail record for each pending broadcast and copy its fields onto the row."""
    if not pending:
        return {"attempted": 0, "failed": 0}

    failed = 0
    total = len(pending)
    log(f"  fetching {total} programme details ({workers} workers)")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = enumerate(pool.map(_fetch_detail_one, pending), start=1)
        for done, outcome in outcomes:
            source, row, _raw = outcome["item"]
            detail = outcome["detail"]
            err = outcome["error"]
            if done % 200 == 0 or done == total:
                log(f"  [{done}/{total}] details")
            if err is not None:
                failed += 1
                continue

            if source == "zdf":
                row["Cast"] = zdf.format_cast(detail) or row["Cast"]
                row["Crew"] = zdf.format_crew(detail) or row["Crew"]
                row["Year"] = _clean(detail.get("year")) or row["Year"]
                row["Country"] = _clean(detail.get("country")) or row["Country"]
                row["Genre"] = _clean(detail.get("genre")) or row["Genre"]
                row["href"] = detail.get(zdf.SHARING_URL_KEY) or zdf.search_url(row["title"], row["channel"])
                description = _clean(detail.get("text"))
            else:
                row["Cast"] = ard.format_cast(detail) or row["Cast"]
                row["Crew"] = ard.format_crew(detail) or row["Crew"]
                row["Genre"] = row["Genre"] or _clean((detail.get("grouping") or {}).get("title"))
                row["href"] = row["href"] or ard.search_url(row["title"])
                description = _clean(detail.get("synopsis"))

            if len(description) > len(row["Description"]):
                row["Description"] = description

    return {"attempted": total, "failed": failed}


def _collect_zdf(start: datetime, end: datetime, log) -> dict:
    rows: list[dict] = []
    pending: list[tuple] = []
    total = len(ZDF_CHANNELS)
    for i, channel in enumerate(ZDF_CHANNELS, start=1):
        log(f"  [{i}/{total}] ZDF API: {channel.canonical}")
        for b in zdf.fetch_broadcasts(channel.api_id, start, end):
            begin = _parse_dt(b.get("effectiveAirtimeBegin") or b.get("airtimeBegin"))
            title = _clean(b.get("title"))
            if not begin or not title:
                continue
            subtitle = _clean(b.get("subtitle"))
            if subtitle and subtitle.lower() != title.lower():
                title = f"{title} - {subtitle}"
            row = _row(
                channel.canonical, title, begin,
                _parse_dt(b.get("effectiveAirtimeEnd") or b.get("airtimeEnd")),
                Description=_clean(b.get("text")),
            )
            rows.append(row)
            if _in_detail_window(begin):
                pending.append(("zdf", row, b))
    return {"rows": rows, "pending": pending}


def _collect_ard(first_day: date, days: int, log) -> dict:
    rows: list[dict] = []
    pending: list[tuple] = []
    wanted = {c.api_id for c in ARD_CHANNELS}
    for i in range(days):
        day = first_day + timedelta(days=i)
        log(f"  [{i + 1}/{days}] ARD API: {day.isoformat()}")
        for api_id, entry in ard.fetch_day(day):
            if api_id not in wanted:
                continue
            begin = _parse_dt(entry.get("broadcastedOn"))
            described = ard.describe(entry)
            title = _clean(described["title"])
            extras = described["extras"]
            if not begin or not title:
                continue
            row = _row(
                ARD_ID_TO_CANONICAL[api_id], title, begin,
                _parse_dt(entry.get("broadcastEnd")),
                Description=_clean(entry.get("synopsis")),
                **extras,
            )
            rows.append(row)
            if _in_detail_window(begin):
                pending.append(("ard", row, entry))
    return {"rows": rows, "pending": pending}


def collect_schedule(days: int = DEFAULT_DAYS, workers: int = DEFAULT_WORKERS, log=print) -> CollectResult:
    """Collect the full broadcast schedule from the ARD and ZDF APIs.

    Each source is fetched independently so that one failing does not lose the other.
    Every listing row is returned; programme details (cast, crew, year, country, genre)
    are fetched only for evening broadcasts, where the ranked slots live.

    Args:
        days: How many days ahead to collect. Capped at 8 by the ARD API.
        workers: Parallel workers for detail fetches.
        log: Line logger.

    Returns:
        A CollectResult carrying the rows plus per-source success and failure detail.

    Raises:
        RuntimeError: If no source yielded any row.
    """
    days = min(days, ARD_MAX_DAYS)
    now = datetime.now().astimezone()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    result = CollectResult()
    pending: list[tuple] = []

    log(f"Collecting {days} days from ZDF API (ZDF family, 3sat, phoenix, ARTE)")
    try:
        collected = _collect_zdf(start, start + timedelta(days=days), log)
        rows, todo = collected["rows"], collected["pending"]
        result.rows.extend(rows)
        pending.extend(todo)
        result.sources_ok.append("zdf")
        log(f"ZDF API: {len(rows)} entries")
    except Exception as e:
        result.sources_failed["zdf"] = f"{type(e).__name__}: {e}"
        log(f"ZDF API FAILED: {type(e).__name__}: {e}")

    log(f"Collecting {days} days from ARD API (Das Erste and third channels)")
    try:
        collected = _collect_ard(now.date(), days, log)
        rows, todo = collected["rows"], collected["pending"]
        result.rows.extend(rows)
        pending.extend(todo)
        result.sources_ok.append("ard")
        log(f"ARD API: {len(rows)} entries")
    except Exception as e:
        result.sources_failed["ard"] = f"{type(e).__name__}: {e}"
        log(f"ARD API FAILED: {type(e).__name__}: {e}")

    if not result.rows:
        raise RuntimeError(
            "No schedule rows from any source: "
            + "; ".join(f"{k}: {v}" for k, v in result.sources_failed.items())
        )

    detail_result = _fetch_details(pending, workers, log)
    result.detail_attempted = detail_result["attempted"]
    result.detail_failed = detail_result["failed"]
    return result
