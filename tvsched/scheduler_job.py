import os
from datetime import datetime

from db import (
    init_db, get_run_shows, save_shows, save_scores,
    get_training_data, get_training_stats,
)
from scraper import scrape_tvspielfilm
from ranker import load_model, load_config, score_shows
from trainer import retrain_and_save, MIN_SELECTED
from emailer import send_notification_email


def run():
    init_db()
    run_date = datetime.now().strftime("%Y-%m-%d")
    print(f"=== TV Scheduler job: {run_date} ===")

    total, n_selected = get_training_stats()
    if n_selected >= MIN_SELECTED:
        print(f"Retraining on {total} labeled shows ({n_selected} selected)...")
        retrain_and_save(get_training_data())

    existing = get_run_shows(run_date)
    if not existing:
        print("Scraping TVSpielfilm (14-day window)...")
        shows = scrape_tvspielfilm()
        save_shows(shows, run_date)
        print(f"Saved {len(shows)} shows for {run_date}")
    else:
        print(f"Using {len(existing)} cached shows for {run_date}")

    model = load_model()
    config = load_config()
    scored = score_shows(get_run_shows(run_date), model, config)
    save_scores(scored, run_date)
    print(f"Scored {len(scored)} shows")

    send_notification_email(run_date, scored, access_token=os.environ["ACCESS_TOKEN"])
    print("Notification email sent")


if __name__ == "__main__":
    run()
