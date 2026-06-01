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

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def main():
    parser = argparse.ArgumentParser(description="Run the TV scheduler job locally.")
    parser.add_argument("--run-date", help="Use this run_date instead of today (skips scraping if data exists)")
    parser.add_argument("--skip-email", action="store_true", help="Score and print summary without sending email")
    args = parser.parse_args()

    required_vars = ["SECRET_KEY", "ACCESS_TOKEN", "JOB_TOKEN", "BASE_URL",
                     "SMTP_HOST", "SMTP_USER", "SMTP_PASS", "EMAIL_TO"]
    missing = [v for v in required_vars if not os.environ.get(v)]
    if missing and not args.skip_email:
        print(f"Missing environment variables: {', '.join(missing)}")
        print("Create a .env file (see env.yaml.example) or export them before running.")
        sys.exit(1)

    from db import init_db, get_run_shows, save_shows, save_scores, get_training_data, get_training_stats
    from scraper import scrape_tvspielfilm
    from ranker import load_model, load_config, score_shows
    from trainer import retrain_and_save, MIN_SELECTED
    from emailer import send_notification_email

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
        print("Scraping TVSpielfilm (this may take 10-30 minutes)...")
        shows = scrape_tvspielfilm()
        save_shows(shows, run_date)
        print(f"Saved {len(shows)} shows")
    else:
        print(f"Using {len(existing)} cached shows for {run_date}")

    model = load_model()
    config = load_config()
    shows = get_run_shows(run_date)
    scored = score_shows(shows, model, config)
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
