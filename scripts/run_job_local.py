import argparse
import os
import sys
from pathlib import Path

DEFAULT_COLLECT_DAYS = 8
MUST_WATCH_PREVIEW = 10

REQUIRED_VARS = [
    "SECRET_KEY",
    "ACCESS_TOKEN",
    "JOB_TOKEN",
    "BASE_URL",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASS",
    "EMAIL_TO",
]

env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

sys.path.insert(0, str(Path(__file__).parent.parent / "tvsched"))


def _parse_args():
    parser = argparse.ArgumentParser(description="Run the TV scheduler job locally.")
    parser.add_argument("--run-date", help="Use this run_date instead of today (reuses stored rows if present)")
    parser.add_argument("--skip-email", action="store_true", help="Score and print summary without sending email")
    parser.add_argument("--days", type=int, default=DEFAULT_COLLECT_DAYS, help="Days of schedule to collect (max 8)")
    return parser.parse_args()


def _require_environment():
    missing = []
    for name in REQUIRED_VARS:
        if not os.environ.get(name):
            missing.append(name)
    if not missing:
        return
    print(f"Missing environment variables: {', '.join(missing)}")
    print("Create a .env file (see env.yaml.example) or export them before running.")
    sys.exit(1)


def _print_candidate_stats(stats):
    print(f"Candidates: {stats['candidates']} of {stats['in_slot']} in-slot "
          f"(from {stats['collected']} collected)")
    print(f"  dropped: {stats['hidden_channel']} hidden channel, {stats['excluded_genre']} genre, "
          f"{stats['presented_format']} presented, {stats['not_fiction']} not fiction, "
          f"{stats['simulcasts_merged']} simulcast")


def _print_must_watch(scored):
    must_watch = []
    for show in scored:
        if show.get("is_must_watch"):
            must_watch.append(show)
    print(f"\nScored {len(scored)} shows. Must-watch: {len(must_watch)}")
    for show in must_watch[:MUST_WATCH_PREVIEW]:
        print(f"  *** {show.get('date','')} {show.get('time','')} "
              f"{show.get('title','')} / {show.get('channel','')}")


def main():
    args = _parse_args()
    if not args.skip_email:
        _require_environment()

    from datetime import datetime

    from candidates import select_candidates
    from db import get_run_shows, get_training_data, get_training_stats, init_db, save_scores, save_shows
    from emailer import send_notification_email
    from ranker import load_config, load_model, score_shows
    from sources import collect_schedule
    from trainer import MIN_SELECTED, retrain_and_save

    init_db()
    run_date = args.run_date or datetime.now().strftime("%Y-%m-%d")
    print(f"Run date: {run_date}")

    training = get_training_stats()
    if training["selected"] >= MIN_SELECTED:
        print(f"Retraining on {training['total']} labeled shows "
              f"({training['selected']} selected)...")
        retrain_and_save(get_training_data())

    existing = get_run_shows(run_date)
    if existing:
        print(f"Using {len(existing)} stored rows for {run_date}")
    else:
        result = collect_schedule(days=args.days)
        save_shows(result.rows, run_date)
        print(f"Saved {len(result.rows)} rows "
              f"(sources ok: {', '.join(result.sources_ok) or 'none'}; "
              f"failed: {result.sources_failed or 'none'})")

    config = load_config()
    selected = select_candidates(get_run_shows(run_date), config)
    _print_candidate_stats(selected["stats"])

    scored = score_shows(selected["candidates"], load_model(), config)
    save_scores(scored, run_date)
    _print_must_watch(scored)

    if args.skip_email:
        print("\n--skip-email set, not sending notification.")
        return

    send_notification_email(run_date, scored, access_token=os.environ["ACCESS_TOKEN"])
    print("Notification email sent.")


if __name__ == "__main__":
    main()
