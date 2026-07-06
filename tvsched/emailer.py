import os
import smtplib
from collections import defaultdict
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict


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


def _smtp_config():
    return {
        "host": os.environ["SMTP_HOST"],
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ["SMTP_USER"],
        "password": os.environ["SMTP_PASS"],
        "to": os.environ["EMAIL_TO"],
    }


def _send(msg):
    cfg = _smtp_config()
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
        server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.send_message(msg)
    print(f"Email sent: {msg['Subject']}")


def send_notification_email(run_date: str, scored_shows: List[Dict], access_token: str):
    base_url = os.environ["BASE_URL"]
    url = f"{base_url}/run/{run_date}?token={access_token}"
    total = len(scored_shows)

    by_date: Dict[str, List] = defaultdict(list)
    for s in scored_shows:
        by_date[s.get("date", "")].append(s)

    ordered_dates = sorted(by_date, key=lambda d: _parse_show_date(run_date, d))
    date_lines = "\n".join(
        f"  {(by_date[d][0].get('weekday', '') + ' ' + d).strip()}: {len(by_date[d])}"
        for d in ordered_dates
    )
    body = f"Shows found for the coming two weeks:\n\n{date_lines}\n\n{url}"

    msg = MIMEText(body, "plain")
    msg["Subject"] = f"TV Guide — {run_date} ({total})"
    _send(msg)


def send_selection_email(run_date: str, selected_shows: List[Dict], ics_content: str):
    if not selected_shows:
        return

    lines = []

    by_date: Dict[str, List] = defaultdict(list)
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
    msg["Subject"] = f"TV Selection — {run_date} ({len(selected_shows)})"
    msg.attach(MIMEText(body, "plain"))

    part = MIMEText(ics_content, "calendar", "utf-8")
    part.add_header("Content-Disposition", "attachment",
                    filename=f"tv_schedule_{run_date}.ics")
    msg.attach(part)

    _send(msg)
