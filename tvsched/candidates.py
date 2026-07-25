from ranker import parse_start_min, slot_of

RANKED_SLOTS = ("early", "late")

# A channel weighted at or below this is treated as hidden rather than merely
# penalised, matching what ranker_prefs documents about large negative weights.
HIDDEN_CHANNEL_WEIGHT = -50.0

# Genres that are never wanted, checked before anything else. Documentaries are
# excluded deliberately: they carry crew credits and would otherwise look like
# produced fiction.
EXCLUDED_GENRE_MARKERS = (
    "nachricht", "magazin", "journal", "dokumentation", "dokumentarfilm", "doku",
    "reportage", "talk", "quiz", "sport", "wetter", "parlament", "gottesdienst",
    "ratgeber", "kabarett", "verbraucher",
)

# Explicit fiction genres, used only to rescue a film whose cast the API omitted.
# Deliberately excludes the bare word "film", which broadcasters also apply to
# documentaries.
FICTION_GENRE_MARKERS = (
    "spielfilm", "fernsehfilm", "kurzfilm", "liebesfilm", "abenteuerfilm",
    "krimi", "thriller", "drama", "komödie", "komodie", "tragikomödie",
    "serie", "reihe", "sitcom", "western", "science-fiction", "horror",
    "fantasy", "mystery",
)

# Crew roles that denote a presented format rather than a produced one.
PRESENTER_ROLES = ("moderation", "redaktion", "präsentation", "prasentation",
                   "gast", "kommentar", "reporter")

MIN_CANDIDATE_RATIO = 0.08
MIN_CANDIDATE_COUNT = 25


class ThinCandidateSet(RuntimeError):
    pass


def _genre_matches(genre: str, markers: tuple) -> bool:
    g = (genre or "").strip().lower()
    return bool(g) and any(m in g for m in markers)


def _crew_roles(show: dict) -> list[str]:
    roles = []
    for entry in (show.get("Crew") or "").split(","):
        role = entry.split(" - ")[0].strip().lower()
        if role:
            roles.append(role)
    return roles


def is_presented_format(show: dict) -> bool:
    """True when the only credited roles are presentation ones, as on a news bulletin."""
    roles = _crew_roles(show)
    return bool(roles) and all(any(p in role for p in PRESENTER_ROLES) for role in roles)


def is_fiction(show: dict) -> bool:
    """A cast means actors, which distinguishes drama from documentary and news."""
    if (show.get("Cast") or "").strip():
        return True
    return _genre_matches(show.get("Genre", ""), FICTION_GENRE_MARKERS)


def _dedupe_simulcasts(shows: list[dict], channel_prior: dict, default: float) -> list[dict]:
    """Collapse the same programme carried on several channels at the same moment.

    Radio Bremen carries most of NDR's evening schedule, and the regional channels
    share films, so the same broadcast can appear three or four times. The copy on
    the most preferred channel wins.
    """
    best: dict = {}
    for show in shows:
        key = (show.get("title"), show.get("start_utc") or (show.get("date"), show.get("time")))
        weight = channel_prior.get(show.get("channel"), default)
        incumbent = best.get(key)
        if incumbent is None or weight > channel_prior.get(incumbent.get("channel"), default):
            best[key] = show
    return [s for s in shows if best.get(
        (s.get("title"), s.get("start_utc") or (s.get("date"), s.get("time")))) is s]


def select_candidates(shows: list[dict], config) -> tuple[list[dict], dict]:
    """Reduce a full schedule to the programmes worth ranking.

    Keeps broadcasts that start inside a ranked slot on a channel that is not hidden,
    are not an excluded genre or a presented format, and look like fiction. Then
    collapses simulcasts of the same programme across channels.

    Args:
        shows: Every collected row for the run.
        config: The loaded RankerConfig, for slot boundaries and channel weights.

    Returns:
        (candidates, stats), where stats reports how many rows each gate removed.

    Raises:
        ThinCandidateSet: If implausibly few candidates survived, which points at
            degraded source metadata rather than a genuinely quiet week.
    """
    prior, default = config.channel_prior or {}, config.default_channel_score

    in_slot = [
        s for s in shows
        if slot_of(parse_start_min(s.get("time", "")), config.early_start_min,
                   config.late_start_min, config.late_end_min) in RANKED_SLOTS
    ]

    counts = {"hidden_channel": 0, "excluded_genre": 0, "presented_format": 0, "not_fiction": 0}
    kept = []
    for show in in_slot:
        if prior.get(show.get("channel"), default) <= HIDDEN_CHANNEL_WEIGHT:
            counts["hidden_channel"] += 1
        elif _genre_matches(show.get("Genre", ""), EXCLUDED_GENRE_MARKERS):
            counts["excluded_genre"] += 1
        elif is_presented_format(show) and not (show.get("Cast") or "").strip():
            counts["presented_format"] += 1
        elif not is_fiction(show):
            counts["not_fiction"] += 1
        else:
            kept.append(show)

    deduped = _dedupe_simulcasts(kept, prior, default)

    stats = {
        "collected": len(shows),
        "in_slot": len(in_slot),
        "candidates": len(deduped),
        "simulcasts_merged": len(kept) - len(deduped),
        **counts,
    }

    if in_slot and (len(deduped) < MIN_CANDIDATE_RATIO * len(in_slot)
                    and len(deduped) < MIN_CANDIDATE_COUNT):
        raise ThinCandidateSet(
            f"only {len(deduped)} candidates from {len(in_slot)} in-slot broadcasts; "
            "cast and genre metadata look degraded at the source"
        )

    return deduped, stats
