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


def due(run_date: str) -> dict:
    """Whether a full run is due, given the last one that produced scores."""
    last = last_scored_run_date()
    if not last:
        return {"ready": True, "reason": "no previous run on record"}
    try:
        delta = (datetime.strptime(run_date, "%Y-%m-%d")
                 - datetime.strptime(last, "%Y-%m-%d")).days
    except ValueError:
        return {"ready": True, "reason": f"unparseable last run date {last!r}"}
    if delta >= CYCLE_DAYS:
        return {"ready": True, "reason": f"{delta} days since {last}"}
    return {"ready": False, "reason": f"only {delta} of {CYCLE_DAYS} days since {last}"}


def _retrain_if_enough_labels():
    stats = get_training_stats()
    selected = stats["selected"]
    if selected < MIN_SELECTED:
        print(f"Skipping retrain: {selected}/{MIN_SELECTED} selections")
        return
    print(f"Retraining on {stats['total']} labeled shows ({selected} selected)")
    retrain_and_save(get_training_data())


def _collect_shows(run_date, force):
    """Fetch this run's programmes, reusing whatever is already stored for the date.

    Args:
        run_date: the run's date key.
        force: when true, discard stored rows first so they are collected afresh.

    Returns:
        {"shows": the stored rows, "warnings": readable notes about API problems}
    """
    if force and not session_done(run_date):
        removed = clear_run(run_date)
        if removed:
            print(f"force: discarded {removed} stored rows so they are collected afresh")
    existing = get_run_shows(run_date)
    if existing:
        print(f"Using {len(existing)} cached rows for {run_date}")
        return {"shows": existing, "warnings": []}

    result = collect_schedule(days=COLLECT_DAYS)
    save_shows(result.rows, run_date)
    print(f"Saved {len(result.rows)} rows for {run_date}")
    warnings = []
    for name, reason in result.sources_failed.items():
        warnings.append(f"{name.upper()} API unavailable, its channels are missing ({reason})")
    if result.detail_failure_ratio > DETAIL_FAILURE_ALERT:
        warnings.append(
            f"{result.detail_failure_ratio:.0%} of programme details failed to load, "
            "so cast and crew are incomplete"
        )
    return {"shows": get_run_shows(run_date), "warnings": warnings}


def _print_candidate_stats(stats):
    print(f"Candidates: {stats['candidates']} of {stats['in_slot']} in-slot "
          f"(from {stats['collected']} collected)")
    print(f"  dropped: {stats['hidden_channel']} hidden channel, "
          f"{stats['excluded_genre']} excluded genre, "
          f"{stats['presented_format']} presented format, "
          f"{stats['not_fiction']} not fiction, "
          f"{stats['simulcasts_merged']} simulcast duplicates")


def _logs_url():
    project = os.environ.get("GCP_PROJECT", "")
    if not project:
        return ""
    return (
        "https://console.cloud.google.com/logs/query"
        f"?project={project}"
        ";query=resource.type%3D%22cloud_run_revision%22%20"
        "resource.labels.service_name%3D%22tvsched%22"
    )


def _report_failure(run_date, stage, error):
    """Announce the failed stage and try to email it, never masking the original error."""
    print(f"Scheduled job FAILED during '{stage}': {type(error).__name__}: {error}")
    try:
        send_failure_email(run_date, stage, error, _logs_url())
        print("Failure notification sent")
    except Exception as mail_error:
        print(f"CRITICAL: could not send failure notification: "
              f"{type(mail_error).__name__}: {mail_error}")


def run(force: bool = False):
    run_date = datetime.now().strftime("%Y-%m-%d")
    print(f"=== TV Scheduler job: {run_date} ===")

    init_db()
    due_status = due(run_date)
    if not due_status["ready"] and not force:
        print(f"Nothing to do: {due_status['reason']}. Exiting.")
        return {"skipped": True, "reason": due_status["reason"]}

    print(f"Run is due: {due_status['reason']}")
    for number, name in enumerate(STEPS, start=1):
        print(f"  · {number}/{len(STEPS)}  {name}")

    stage = STEPS[0]
    warnings: list[str] = []
    try:
        print(f"[1/{len(STEPS)}] {STEPS[0]}")

        stage = STEPS[1]
        print(f"[2/{len(STEPS)}] {stage}")
        _retrain_if_enough_labels()
        config = load_config()

        stage = STEPS[2]
        print(f"[3/{len(STEPS)}] {stage}")
        collected = _collect_shows(run_date, force)
        shows = collected["shows"]
        warnings = collected["warnings"]
        unpriced = unpriced_channels(config.channel_prior)
        if unpriced:
            print(f"WARNING: channels without a channel_prior weight: {', '.join(unpriced)}")

        stage = STEPS[3]
        print(f"[4/{len(STEPS)}] {stage}")
        selected = select_candidates(shows, config)
        _print_candidate_stats(selected["stats"])

        stage = STEPS[4]
        print(f"[5/{len(STEPS)}] {stage}")
        scored = score_shows(selected["candidates"], load_model(), config)
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
        _report_failure(run_date, stage, e)
        raise


if __name__ == "__main__":
    run()
