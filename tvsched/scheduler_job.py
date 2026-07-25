import os
from datetime import datetime

from candidates import select_candidates
from db import (
    clear_run,
    get_run_shows,
    get_training_data,
    get_training_stats,
    init_db,
    last_scored_run_date,
    save_scores,
    save_shows,
    session_done,
)
from emailer import send_failure_email, send_notification_email
from ranker import load_config, load_model, score_shows
from sources import collect_schedule
from sources.channels import unpriced_channels
from trainer import MIN_SELECTED, retrain_and_save

STEPS = (
    "Init database",
    "Retrain model (if enough selections)",
    "Collect schedule from ARD and ZDF APIs",
    "Select rankable candidates",
    "Score candidates",
    "Send notification email",
)

CYCLE_DAYS = int(os.environ.get("CYCLE_DAYS", "7"))
COLLECT_DAYS = int(os.environ.get("COLLECT_DAYS", "8"))
DETAIL_FAILURE_ALERT = 0.30


def _log_url() -> str:
    project = os.environ.get("GCP_PROJECT", "")
    if not project:
        return ""
    return (
        "https://console.cloud.google.com/logs/query"
        f"?project={project}"
        ";query=resource.type%3D%22cloud_run_revision%22%20"
        "resource.labels.service_name%3D%22tvsched%22"
    )


def due(run_date: str) -> tuple[bool, str]:
    """Whether a full run is due, given the last one that produced scores."""
    last = last_scored_run_date()
    if not last:
        return True, "no previous run on record"
    try:
        delta = (datetime.strptime(run_date, "%Y-%m-%d")
                 - datetime.strptime(last, "%Y-%m-%d")).days
    except ValueError:
        return True, f"unparseable last run date {last!r}"
    if delta >= CYCLE_DAYS:
        return True, f"{delta} days since {last}"
    return False, f"only {delta} of {CYCLE_DAYS} days since {last}"


def run(force: bool = False):
    run_date = datetime.now().strftime("%Y-%m-%d")
    print(f"=== TV Scheduler job: {run_date} ===")

    init_db()
    ready, why = due(run_date)
    if not ready and not force:
        print(f"Nothing to do: {why}. Exiting.")
        return {"skipped": True, "reason": why}

    print(f"Run is due: {why}")
    for i, name in enumerate(STEPS, start=1):
        print(f"  · {i}/{len(STEPS)}  {name}")

    stage = STEPS[0]
    warnings: list[str] = []
    try:
        print(f"[1/{len(STEPS)}] {STEPS[0]}")

        stage = STEPS[1]
        print(f"[2/{len(STEPS)}] {stage}")
        total, n_selected = get_training_stats()
        if n_selected >= MIN_SELECTED:
            print(f"Retraining on {total} labeled shows ({n_selected} selected)")
            retrain_and_save(get_training_data())
        else:
            print(f"Skipping retrain: {n_selected}/{MIN_SELECTED} selections")

        config = load_config()

        stage = STEPS[2]
        print(f"[3/{len(STEPS)}] {stage}")
        if force and not session_done(run_date):
            removed = clear_run(run_date)
            if removed:
                print(f"force: discarded {removed} stored rows so they are collected afresh")
        existing = get_run_shows(run_date)
        if existing:
            print(f"Using {len(existing)} cached rows for {run_date}")
            shows = existing
        else:
            result = collect_schedule(days=COLLECT_DAYS)
            save_shows(result.rows, run_date)
            print(f"Saved {len(result.rows)} rows for {run_date}")
            shows = get_run_shows(run_date)

            for name, reason in result.sources_failed.items():
                warnings.append(f"{name.upper()} API unavailable, its channels are missing ({reason})")
            if result.detail_failure_ratio > DETAIL_FAILURE_ALERT:
                warnings.append(
                    f"{result.detail_failure_ratio:.0%} of programme details failed to load, "
                    "so cast and crew are incomplete"
                )

        unpriced = unpriced_channels(config.channel_prior)
        if unpriced:
            print(f"WARNING: channels without a channel_prior weight: {', '.join(unpriced)}")

        stage = STEPS[3]
        print(f"[4/{len(STEPS)}] {stage}")
        candidates, stats = select_candidates(shows, config)
        print(f"Candidates: {stats['candidates']} of {stats['in_slot']} in-slot "
              f"(from {stats['collected']} collected)")
        print(f"  dropped: {stats['hidden_channel']} hidden channel, "
              f"{stats['excluded_genre']} excluded genre, "
              f"{stats['presented_format']} presented format, "
              f"{stats['not_fiction']} not fiction, "
              f"{stats['simulcasts_merged']} simulcast duplicates")

        stage = STEPS[4]
        print(f"[5/{len(STEPS)}] {stage}")
        scored = score_shows(candidates, load_model(), config)
        save_scores(scored, run_date)
        print(f"Scored {len(scored)} shows")

        stage = STEPS[5]
        print(f"[6/{len(STEPS)}] {stage}")
        send_notification_email(
            run_date, scored,
            access_token=os.environ["ACCESS_TOKEN"],
            warnings=warnings or None,
        )
        print("Notification email sent" + (" (partial)" if warnings else ""))
        return {"skipped": False, "scored": len(scored), "warnings": warnings}

    except Exception as e:
        print(f"Scheduled job FAILED during '{stage}': {type(e).__name__}: {e}")
        try:
            send_failure_email(run_date, stage, e, _log_url())
            print("Failure notification sent")
        except Exception as mail_error:
            print(f"CRITICAL: could not send failure notification: "
                  f"{type(mail_error).__name__}: {mail_error}")
        raise


if __name__ == "__main__":
    run()
