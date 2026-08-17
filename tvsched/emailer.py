import copy
import os
import smtplib
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


def _parse_show_date(run_date: str, date_str: str) -> date:
    if not date_str:
        return date.max
    try:
        day, month = (int(p) for p in date_str.strip().rstrip(".").split("."))
    except ValueError:
        return date.max
    anchor = datetime.strptime(run_date, "%Y-%m-%d").date()
    candidate = date(anchor.year, month, day)
    if candidate < anchor - timedelta(days=3):
        candidate = date(anchor.year + 1, month, day)
    return candidate


def _send(msg):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    outgoing = copy.deepcopy(msg)
    outgoing["From"] = user
    outgoing["To"] = os.environ["EMAIL_TO"]
    with smtplib.SMTP(host, port) as server:
        server.starttls()
        server.login(user, password)
        server.send_message(outgoing)
    print(f"Email sent: {outgoing['Subject']}")


def send_notification_email(run_date: str, scored_shows: list[dict], access_token: str,
                            warnings: list[str] = None):
    base_url = os.environ["BASE_URL"]
    url = f"{base_url}/run/{run_date}?token={access_token}"
    total = len(scored_shows)

    by_date: dict[str, list] = defaultdict(list)
    for s in scored_shows:
        by_date[s.get("date", "")].append(s)

    ordered_dates = sorted(by_date, key=lambda d: _parse_show_date(run_date, d))
    date_lines = "\n".join(
        f"  {(by_date[d][0].get('weekday', '') + ' ' + d).strip()}: {len(by_date[d])}"
        for d in ordered_dates
    )

    banner = ""
    if warnings:
        banner = "INCOMPLETE RUN\n" + "".join(f"  - {w}\n" for w in warnings) + "\n"

    body = f"{banner}Shows found for the coming days:\n\n{date_lines}\n\n{url}"

    msg = MIMEText(body, "plain")
    flag = " [partial]" if warnings else ""
    msg["Subject"] = f"TV Guide{flag} - {run_date} ({total})"
    _send(msg)


def send_failure_email(run_date: str, stage: str, error: BaseException, log_url: str = ""):
    """Report a failed scheduled run.

    Deliberately depends on nothing but the SMTP settings, so that it still works when
    the database, the model, the sources or BASE_URL are the thing that broke.

    Args:
        run_date: The run that failed.
        stage: Human-readable name of the step that raised.
        error: The exception that ended the run.
        log_url: Optional Cloud Logging deep link.

    Raises:
        Exception: Propagates SMTP failures so the caller can log them loudly.
    """
    lines = [
        f"The TV scheduler run for {run_date} failed and produced no guide.",
        "",
        f"Stage:  {stage}",
        f"Error:  {type(error).__name__}: {error}",
    ]
    if log_url:
        lines += ["", f"Logs:   {log_url}"]
    lines += ["", "The next scheduled run will retry automatically."]

    msg = MIMEText("\n".join(lines), "plain")
    msg["Subject"] = f"TV Guide FAILED - {run_date} ({stage})"
    _send(msg)


def send_selection_email(run_date: str, selected_shows: list[dict], ics_content: str):
    if not selected_shows:
        return

    lines = []

    by_date: dict[str, list] = defaultdict(list)
    for s in selected_shows:
        by_date[s.get("date", "")].append(s)

    for date_str in sorted(by_date, key=lambda d: _parse_show_date(run_date, d)):
        shows = by_date[date_str]
        date_label = (shows[0].get("weekday", "") + " " + date_str).strip()
        lines.append(f"\n---- {date_label} ----")
        for s in shows:
            lines.append(f"  {s.get('time', '')}  {s.get('title', '')} / {s.get('channel', '')}")
            if s.get("Genre"):
                lines.append(f"  {s['Genre']}")
            if s.get("href"):
                lines.append(f"  {s['href']}")
            lines.append("")

    body = "\n".join(lines)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"TV Selection: {run_date} ({len(selected_shows)})"
    msg.attach(MIMEText(body, "plain"))

    part = MIMEText(ics_content, "calendar", "utf-8")
    part.add_header("Content-Disposition", "attachment",
                    filename=f"tv_schedule_{run_date}.ics")
    msg.attach(part)

    _send(msg)
