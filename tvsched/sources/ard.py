import re
from datetime import date
from urllib.parse import quote

from .fetch import get_json

API_HOST = "https://programm-api.ard.de"
PROGRAM_PATH = "/program/api/program"

PRODUCTION_RE = re.compile(
    r"\s*[-–]?\s*"
    r"(Spielfilm|Fernsehfilm|Dokumentarfilm|Kurzfilm|Dokumentation|Doku|"
    r"Krimiserie|Krimireihe|Krimi|Serie|Reihe)"
    r"\s+([A-ZÄÖÜ][\wÄÖÜäöüß.\-/ ]*?)\s+(\d{4})\s*$"
)


def _flatten(node) -> list[dict]:
    if isinstance(node, dict):
        return [node]
    if isinstance(node, list):
        return [item for child in node for item in _flatten(child)]
    return []


def fetch_day(day: date) -> list[tuple[str, dict]]:
    """Fetch one day of programming for every ARD-family channel in a single call.

    Args:
        day: The broadcast day to fetch. ARD serves at most 8 days ahead.

    Returns:
        (channel_api_id, raw_entry) pairs across all channels the API returned.
    """
    payload = get_json(f"{API_HOST}{PROGRAM_PATH}?day={day.isoformat()}")
    out: list[tuple[str, dict]] = []
    for channel in payload.get("channels") or []:
        api_id = channel.get("id") or ""
        for entry in _flatten(channel.get("timeSlots")):
            if entry.get("broadcastedOn"):
                out.append((api_id, entry))
    return out


def describe(entry: dict) -> tuple[str, dict]:
    """Split an ARD listing entry into a clean title and its production metadata.

    ARD has no year, country or genre fields, but it appends them to the subline as
    "Spielfilm Deutschland 2024" for produced content. Pulling that apart recovers the
    three fields and keeps them out of the title, where they would otherwise pollute
    the title model's vocabulary.

    Args:
        entry: A raw listing entry.

    Returns:
        (title, extras) where extras holds any of Genre, Country and Year that were
        recovered, and is empty when the subline did not carry them.
    """
    core = (entry.get("coreTitle") or entry.get("title") or "").strip()
    subline = (entry.get("coreSubline") or entry.get("subline") or "").strip()

    extras: dict = {}
    match = PRODUCTION_RE.search(subline)
    if match:
        genre, country, year = match.group(1), match.group(2).strip(" -"), match.group(3)
        extras = {"Genre": genre, "Country": country, "Year": year}
        subline = subline[: match.start()].strip(" -")

    if subline and subline.lower() != core.lower():
        return f"{core} - {subline}", extras
    return core, extras


def search_url(title: str) -> str:
    """A Mediathek search link for a programme.

    ARD's own `grouping.url` deep-links a Mediathek show page that 404s whenever the
    programme is not currently available on demand, which is most of the linear
    schedule. A search link always resolves and still lands on the programme when it
    is there.
    """
    core = (title or "").split(" - ")[0]
    core = re.sub(r"\s*\(\d+\)\s*$", "", core).strip()
    return f"https://www.ardmediathek.de/suche/{quote(core)}" if core else ""


def fetch_teaser(entry: dict) -> dict:
    """Fetch the teaser record, which carries castAndCrew and the mediathek grouping."""
    href = ((entry.get("links") or {}).get("self") or {}).get("href")
    if not href or not href.startswith(f"{API_HOST}/program/api/teaser"):
        return {}
    return get_json(href)


def _credits(teaser: dict, kind: str) -> list[dict]:
    return [e for e in (teaser.get("castAndCrew") or []) if e.get("type") == kind]


def format_cast(teaser: dict) -> str:
    parts = []
    for e in _credits(teaser, "cast"):
        name, role = (e.get("name") or "").strip(), (e.get("role") or "").strip()
        if name:
            parts.append(f"{name} - {role}" if role else name)
    return ", ".join(parts)


def format_crew(teaser: dict) -> str:
    parts = []
    for e in _credits(teaser, "crew"):
        name, role = (e.get("name") or "").strip(), (e.get("role") or "").strip()
        if name:
            parts.append(f"{role} - {name}" if role else name)
    return ", ".join(parts)
