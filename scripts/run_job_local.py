import argparse
import os
import sys
from pathlib import Path

env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

sys.path.insert(0, str(Path(__file__).parent.parent / "tvsched"))


def main():
    parser = argparse.ArgumentParser(description="Run the TV scheduler job locally.")
    parser.add_argument("--run-date",
                        help="Use this run_date instead of today (reuses stored rows if present)")
    parser.add_argument("--skip-email", action="store_true",
                        help="Score and print summary without sending email")
    parser.add_argument("--days", type=int, default=8, help="Days of schedule to collect (max 8)")
    args = parser.parse_args()

    required_vars = ["SECRET_KEY", "ACCESS_TOKEN", "JOB_TOKEN", "BASE_URL",
                     "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "EMAIL_TO"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing and not args.skip_email:
        print(f"Missing environment variables: {', '.join(missing)}")
        print("Create a .env file (see env.yaml.example) or export them before running.")
        sys.exit(1)

    from candidates import select_candidates
    from db import get_run_shows, get_training_data, get_training_stats, init_db, save_scores, save_shows
    from emailer import send_notification_email
    from ranker import load_config, load_model, score_shows
    from sources import collect_schedule
    from trainer import MIN_SELECTED, retrain_and_save

    init_db()

    from datetime import datetime
    run_date = args.run_date or datetime.now().strftime("%Y-%m-%d")
    print(f"Run date: {run_date}")

    total, n_selected = get_training_stats()
    if n_selected >= MIN_SELECTED:
        print(f"Retraining on {total} labeled shows ({n_selected} selected)...")
        retrain_and_save(get_training_data())

    existing = get_run_shows(run_date)
    if not existing:
        result = collect_schedule(days=args.days)
        save_shows(result.rows, run_date)
        print(f"Saved {len(result.rows)} rows "
              f"(sources ok: {', '.join(result.sources_ok) or 'none'}; "
              f"failed: {result.sources_failed or 'none'})")
    else:
        print(f"Using {len(existing)} stored rows for {run_date}")

    config = load_config()
    shows = get_run_shows(run_date)
    candidates, stats = select_candidates(shows, config)
    print(f"Candidates: {stats['candidates']} of {stats['in_slot']} in-slot "
          f"(from {stats['collected']} collected)")
    print(f"  dropped: {stats['hidden_channel']} hidden channel, {stats['excluded_genre']} genre, "
          f"{stats['presented_format']} presented, {stats['not_fiction']} not fiction, "
          f"{stats['simulcasts_merged']} simulcast")

    scored = score_shows(candidates, load_model(), config)
    save_scores(scored, run_date)

    must_watch = [s for s in scored if s.get("is_must_watch")]
    print(f"\nScored {len(scored)} shows. Must-watch: {len(must_watch)}")
    for s in must_watch[:10]:
        print(f"  *** {s.get('date','')} {s.get('time','')} {s.get('title','')} / {s.get('channel','')}")

    if args.skip_email:
        print("\n--skip-email set, not sending notification.")
        return

    send_notification_email(run_date, scored, access_token=os.environ["ACCESS_TOKEN"])
    print("Notification email sent.")


if __name__ == "__main__":
    main()
