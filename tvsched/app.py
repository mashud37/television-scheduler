import html
import os
import re
import secrets
from datetime import datetime

import pandas as pd
from db import (
    get_run_shows_with_scores,
    get_session_selections,
    get_titles_from_previous_runs,
    get_training_data,
    init_db,
    save_session,
    session_done,
)
from emailer import send_selection_email
from flask import (
    Flask,
    abort,
    redirect,
    render_template_string,
    request,
    session,
    url_for,
)
from ics import Calendar, Event
from ranker import load_config, save_config
from trainer import retrain_and_save

init_db()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("HTTPS", "false").lower() == "true"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

ACCESS_TOKEN = os.environ["ACCESS_TOKEN"]
JOB_TOKEN = os.environ["JOB_TOKEN"]

RATING_SYMBOLS = {"1": "👍", "2": "👉", "3": "👎"}

SLOT_ORDER = {"early": 0, "late": 1, "night": 2}

TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TV Guide &mdash; {{ run_date }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: sans-serif; max-width: 980px; margin: 2rem auto; padding: 0 1.5rem; color: #111; }
h1 { font-size: 1.4rem; margin-bottom: 0.2rem; }
.subtitle { color: #6b7280; font-size: 0.9rem; margin-bottom: 1.5rem; }
h2 { font-size: 1.05rem; font-weight: 700; margin: 2rem 0 0.5rem;
     padding-bottom: 0.35rem; border-bottom: 2px solid #e5e7eb; }
h3 { font-size: 0.78rem; color: #9ca3af; text-transform: uppercase;
     letter-spacing: 0.06em; margin: 0.9rem 0 0.35rem; }
.show { padding: 0.7rem; margin: 0.35rem 0; border: 1px solid #e5e7eb;
        border-radius: 6px; display: flex; gap: 0.75rem; align-items: flex-start;
        cursor: pointer; }
.show:hover { border-color: #93c5fd; }
.show.must { border-color: #2563eb; background: #eff6ff; }
.show.selected, .show:has(input:checked) { border-color: #16a34a; background: #f0fdf4; }
.show:has(input:disabled) { cursor: default; }
input[type=checkbox] { width: 18px; height: 18px; flex-shrink: 0; margin-top: 2px; cursor: pointer; }
.body { flex: 1; min-width: 0; }
.title { font-weight: 600; font-size: 0.94rem; line-height: 1.4; }
.badge { display: inline-block; background: #2563eb; color: #fff;
         font-size: 0.68rem; padding: 0.1rem 0.38rem; border-radius: 3px;
         margin-left: 0.4rem; vertical-align: middle; }
.badge.new { background: #16a34a; }
.badge.premiere { background: #7c3aed; }
.meta { color: #6b7280; font-size: 0.79rem; margin-top: 0.2rem; }
.desc { color: #374151; font-size: 0.84rem; margin-top: 0.35rem; line-height: 1.5; }
.score { color: #9ca3af; font-size: 0.75rem; white-space: nowrap;
         padding-top: 2px; min-width: 38px; text-align: right; }
.bar { position: sticky; bottom: 0; background: #fff; border-top: 1px solid #e5e7eb;
       padding: 0.85rem 0; margin-top: 1rem; display: flex; align-items: center; gap: 1rem; }
button { background: #2563eb; color: #fff; padding: 0.55rem 1.6rem;
         border: none; border-radius: 4px; font-size: 0.95rem; cursor: pointer; }
button:hover { background: #1d4ed8; }
.done-banner { background: #dcfce7; border: 1px solid #bbf7d0; padding: 0.7rem 1rem;
               border-radius: 4px; margin-bottom: 1rem; font-size: 0.9rem; }
</style>
</head>
<body>
<h1>TV Guide &mdash; {{ run_date }}</h1>
<p class="subtitle">{{ total }} shows &nbsp;&mdash;&nbsp; <a href="/settings" style="color:#6b7280;font-size:0.85rem;">Settings</a></p>

{% if done %}
<div class="done-banner">Saved, calendar sent.</div>
{% endif %}

<form method="post" action="/run/{{ run_date }}/select">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">

{% for date_label, slots in groups.items() %}
<h2>{{ date_label }}</h2>
{% for slot, shows in slots.items() %}
<h3>{{ slot_labels[slot] }}</h3>
{% for show in shows %}
{% set is_sel = show.id in selected_ids %}
<label class="show{% if show.is_must_watch %} must{% endif %}{% if is_sel %} selected{% endif %}">
  <input type="checkbox" name="selected" value="{{ show.id }}"
    {% if is_sel or (not done and show.is_must_watch) %}checked{% endif %}
    {% if done %}disabled{% endif %}>
  <div class="body">
    <div class="title">
      {{ show.title }}
      {% if show.is_must_watch %}<span class="badge">Must Watch</span>{% endif %}
      {% if show.is_premiere %}<span class="badge premiere">Premiere</span>{% elif show.is_new %}<span class="badge new">New</span>{% endif %}
    </div>
    <div class="meta">
      {{ show.channel }}
      &nbsp;|&nbsp; {{ show.time }}
      {%- if show.Genre %} &nbsp;|&nbsp; {{ show.Genre }}{% endif %}
      {%- if show.Year %} ({{ show.Year }}){% endif %}
      {%- if show.Rating and show.Rating in rating_symbols %} &nbsp;|&nbsp; {{ rating_symbols[show.Rating] }}{% endif %}
    </div>
    {% if show.Description %}
    <div class="desc">{{ show.Description }}</div>
    {% endif %}
  </div>
  {% if show.score_pct is not none %}
  <div class="score">{{ show.score_pct }}%</div>
  {% endif %}
</label>
{% endfor %}
{% endfor %}
{% endfor %}

{% if not done %}
<div class="bar">
  <button type="submit">Save &amp; send calendar</button>
</div>
{% endif %}
</form>
</body>
</html>"""

SETTINGS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings</title>
<style>
* { box-sizing: border-box; }
body { font-family: sans-serif; max-width: 600px; margin: 2rem auto; padding: 0 1.5rem; color: #111; }
h1 { font-size: 1.4rem; margin-bottom: 0.5rem; }
label { display: block; font-size: 0.9rem; font-weight: 600; margin-bottom: 0.3rem; margin-top: 1.2rem; }
.hint { color: #6b7280; font-size: 0.82rem; margin-bottom: 0.6rem; }
textarea { width: 100%; height: 220px; font-family: monospace; font-size: 0.88rem;
           padding: 0.5rem; border: 1px solid #e5e7eb; border-radius: 4px; resize: vertical; }
button { background: #2563eb; color: #fff; padding: 0.55rem 1.6rem;
         border: none; border-radius: 4px; font-size: 0.95rem; cursor: pointer; margin-top: 1rem; }
button:hover { background: #1d4ed8; }
.back { display: inline-block; margin-top: 1rem; color: #6b7280; font-size: 0.85rem; }
{% if saved %}.banner { background: #dcfce7; border: 1px solid #bbf7d0; padding: 0.6rem 1rem;
                        border-radius: 4px; margin-bottom: 1rem; font-size: 0.9rem; }{% endif %}
</style>
</head>
<body>
<h1>Settings</h1>
{% if saved %}<div class="banner">Saved.</div>{% endif %}
<form method="post" action="/settings">
<input type="hidden" name="csrf_token" value="{{ csrf_token }}">

<label for="keywords">Must-watch keywords</label>
<p class="hint">One keyword per line. Shows whose title matches are always ranked first and pre-checked.</p>
<textarea id="keywords" name="keywords">{{ keywords }}</textarea>

<label for="channel_prior">Channel preferences</label>
<p class="hint">One <code>Channel Name: weight</code> per line. Higher = preferred; negative penalises (use a large negative like <code>-100</code> to effectively hide a channel).</p>
<textarea id="channel_prior" name="channel_prior" style="height:160px">{{ channel_prior }}</textarea>

<label>Time slot boundaries (HH:MM)</label>
<p class="hint">Three boundaries split the evening into <b>early</b> (early–late) and <b>late</b> (late–end) viewing slots. Everything outside is bucketed as <i>night</i>.</p>
<div style="display:flex;gap:0.6rem;align-items:center">
  <input type="text" name="early_start" value="{{ early_start }}" placeholder="20:15" style="flex:1;padding:0.45rem;border:1px solid #e5e7eb;border-radius:4px;font-family:monospace">
  &mdash;
  <input type="text" name="late_start"  value="{{ late_start  }}" placeholder="21:35" style="flex:1;padding:0.45rem;border:1px solid #e5e7eb;border-radius:4px;font-family:monospace">
  &mdash;
  <input type="text" name="late_end"    value="{{ late_end    }}" placeholder="23:15" style="flex:1;padding:0.45rem;border:1px solid #e5e7eb;border-radius:4px;font-family:monospace">
</div>

<button type="submit">Save</button>
</form>
<a class="back" href="/">← Back</a>
</body>
</html>"""


def _to_pydatetime(value):
    """A plain Python datetime for either a pandas Timestamp or a datetime."""
    convert = getattr(value, "to_pydatetime", None)
    if convert is None:
        return value
    return convert()


def _slot_labels(config) -> dict:
    """Slot headings named after the boundaries the ranker actually used."""
    early = _format_hhmm(config.early_start_min)
    late = _format_hhmm(config.late_start_min)
    end = _format_hhmm(config.late_end_min)
    return {
        "early": f"Prime time ({early}–{late})",
        "late": f"Late night ({late}–{end})",
        "night": "Night",
    }


def _group_shows(shows: list) -> dict:
    valid_scores = [s["final_score"] for s in shows if s.get("final_score") is not None]
    if valid_scores:
        lo, hi = min(valid_scores), max(valid_scores)
        rng = (hi - lo) if hi > lo else 1.0
        for s in shows:
            fs = s.get("final_score")
            s["score_pct"] = int(round((fs - lo) / rng * 100)) if fs is not None else None
    else:
        for s in shows:
            s["score_pct"] = None

    raw: dict = {}
    for show in shows:
        wd = show.get("weekday") or ""
        dt = show.get("date") or ""
        label = f"{wd} {dt}".strip()
        slot = show.get("slot") or "night"
        m = re.match(r"(\d+)\.(\d+)\.", str(dt))
        if m:
            day, month = int(m.group(1)), int(m.group(2))
            dk = month * 100 + day
        else:
            dk = 0
        raw.setdefault(label, {"key": dk, "slots": {}})
        raw[label]["slots"].setdefault(slot, []).append(show)

    groups: dict = {}
    for label in sorted(raw, key=lambda d: raw[d]["key"]):
        slots = raw[label]["slots"]
        slot_od: dict = {}
        for slot in sorted(slots, key=lambda s: SLOT_ORDER.get(s, 99)):
            slot_od[slot] = sorted(slots[slot], key=lambda s: (s.get("rank_in_group") or 9999))
        groups[label] = slot_od

    return groups


def _build_ics(selected_shows: list) -> str:
    cal = Calendar()
    now = datetime.now()

    for s in selected_shows:
        try:
            if s.get("start_utc") and s.get("end_utc"):
                dt_start = datetime.fromisoformat(s["start_utc"])
                dt_end = datetime.fromisoformat(s["end_utc"])
            else:
                date_str = (s.get("date") or "").strip().rstrip(".")
                time_str = s.get("time") or ""
                d_parts = date_str.split(".")
                day, month = int(d_parts[0]), int(d_parts[1])
                t_parts = time_str.split("-")
                start_s = t_parts[0].strip()
                end_s = t_parts[1].strip()

                year = now.year + (1 if month < now.month else 0)
                base = pd.Timestamp(year=year, month=month, day=day)
                dt_start = (base + pd.Timedelta(start_s + ":00")).tz_localize("Europe/Berlin")
                dt_end = (base + pd.Timedelta(end_s + ":00")).tz_localize("Europe/Berlin")
                if dt_end <= dt_start:
                    dt_end += pd.Timedelta(days=1)

            e = Event()
            is_new = str(s.get("Year", "")) == str(now.year)
            prefix = "NEU: " if is_new else ""
            e.name = f"{prefix}{s.get('title', '')} / {s.get('channel', '')}"
            e.begin = _to_pydatetime(dt_start)
            e.end = _to_pydatetime(dt_end)
            e.description = s.get("href") or s.get("Description") or ""
            cal.events.add(e)
        except Exception as err:
            print(f"ICS: skipping '{s.get('title', '?')}': {err}")

    return str(cal)


@app.before_request
def require_auth():
    if request.endpoint in ("static", "run_job", "healthz"):
        return
    if session.get("authed"):
        return
    token = request.args.get("token", "")
    if token and secrets.compare_digest(token, ACCESS_TOKEN):
        session["authed"] = True
        session["csrf_token"] = secrets.token_hex(16)
        return redirect(request.path)
    abort(401)


@app.route("/healthz")
def healthz():
    return "ok", 200


@app.route("/")
def index():
    run_date = datetime.now().strftime("%Y-%m-%d")
    return redirect(url_for("show_run", run_date=run_date))


@app.route("/run/<run_date>")
def show_run(run_date):
    shows = get_run_shows_with_scores(run_date)
    if not shows:
        return f"<p>No shows found for {html.escape(run_date)}.</p>", 404

    current_year = str(datetime.now().year)
    known_titles = get_titles_from_previous_runs(run_date)
    for s in shows:
        s["is_new"] = s.get("Year") == current_year
        s["is_premiere"] = s["is_new"] and s.get("title", "") not in known_titles

    done = session_done(run_date)
    selected_ids = get_session_selections(run_date) if done else set()
    csrf_token = session.setdefault("csrf_token", secrets.token_hex(16))
    groups = _group_shows(shows)

    return render_template_string(
        TEMPLATE,
        run_date=run_date,
        total=len(shows),
        groups=groups,
        done=done,
        selected_ids=selected_ids,
        csrf_token=csrf_token,
        slot_labels=_slot_labels(load_config()),
        rating_symbols=RATING_SYMBOLS,
    )


@app.route("/run/<run_date>/select", methods=["POST"])
def select(run_date):
    if session.get("csrf_token") != request.form.get("csrf_token"):
        abort(403)
    if session_done(run_date):
        return redirect(url_for("show_run", run_date=run_date))

    shows = get_run_shows_with_scores(run_date)
    all_ids = [s["id"] for s in shows]
    selected_ids = request.form.getlist("selected")

    save_session(run_date, selected_ids, all_ids)

    selected_set = {str(i) for i in selected_ids}
    selected_shows = [s for s in shows if str(s["id"]) in selected_set]
    ics_content = _build_ics(selected_shows)

    try:
        send_selection_email(run_date, selected_shows, ics_content)
    except Exception as e:
        print(f"Selection email failed: {e}")

    try:
        retrain_and_save(get_training_data())
    except Exception as e:
        print(f"Retraining failed: {e}")

    return redirect(url_for("show_run", run_date=run_date))


def _format_hhmm(mins) -> str:
    try:
        mins = int(mins)
    except (TypeError, ValueError):
        return ""
    return f"{mins // 60:02d}:{mins % 60:02d}"


def _parse_channel_prior(text: str) -> dict:
    """Parse a 'Channel: weight' per-line textarea into {channel: float}."""
    out: dict = {}
    for line in (text or "").splitlines():
        if ":" not in line:
            continue
        name, val = line.rsplit(":", 1)
        name = name.strip()
        try:
            out[name] = float(val.strip())
        except ValueError:
            continue
    return out


@app.route("/settings", methods=["GET", "POST"])
def settings():
    saved = False

    if request.method == "POST":
        if session.get("csrf_token") != request.form.get("csrf_token"):
            abort(403)
        cfg = load_config()
        cfg.must_watch_keywords = [
            k.strip() for k in request.form.get("keywords", "").splitlines() if k.strip()
        ]
        cfg.channel_prior = _parse_channel_prior(request.form.get("channel_prior", ""))
        for field_name, form_name in (
            ("early_start_min", "early_start"),
            ("late_start_min", "late_start"),
            ("late_end_min", "late_end"),
        ):
            raw_value = request.form.get(form_name, "")
            match = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", raw_value) if raw_value else None
            if not match:
                continue
            hours = int(match.group(1))
            minutes = int(match.group(2))
            if not (0 <= hours <= 24 and 0 <= minutes < 60):
                continue
            setattr(cfg, field_name, hours * 60 + minutes)
        try:
            save_config(cfg)
            saved = True
        except Exception as e:
            print(f"Settings save failed: {e}")

    cfg = load_config()
    csrf_token = session.setdefault("csrf_token", secrets.token_hex(16))
    return render_template_string(
        SETTINGS_TEMPLATE,
        keywords="\n".join(cfg.must_watch_keywords),
        channel_prior="\n".join(f"{k}: {v}" for k, v in (cfg.channel_prior or {}).items()),
        early_start=_format_hhmm(cfg.early_start_min),
        late_start=_format_hhmm(cfg.late_start_min),
        late_end=_format_hhmm(cfg.late_end_min),
        csrf_token=csrf_token,
        saved=saved,
    )


@app.route("/internal/run-job", methods=["POST", "GET"])
def run_job():
    token = request.args.get("token", "")
    if not secrets.compare_digest(token, JOB_TOKEN):
        abort(401)
    from scheduler_job import run
    force = request.args.get("force", "").lower() in ("1", "true", "yes")
    try:
        result = run(force=force)
    except Exception as e:
        return f"error: {e}", 500
    if result.get("skipped"):
        return f"skipped: {result['reason']}", 200
    return f"ok: scored {result.get('scored', 0)}", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
