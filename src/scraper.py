from __future__ import annotations

import re
import time
from typing import Optional, Dict, Any, List

import pandas as pd
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.tvspielfilm.de/tv-programm/sendungen/"

DEFAULT_FIXED_QS = {
    "filter": "1",
    "order": "time",
    "date": "twoWeeks",
    "publictv": "1",
    "cat[]": ["SP", "SE"],
    "time": "prime",
    "channel": "",
}


def _parse_row(tr) -> Optional[Dict[str, str]]:
    ch_img = tr.select_one("td.programm-col1 img[alt]")
    channel = " ".join(ch_img.get("alt").split()) if ch_img and ch_img.get("alt") else None

    day_el = tr.select_one("td.col-2 span")
    time_el = tr.select_one("td.col-2 strong")
    day = day_el.get_text(" ", strip=True) if day_el else None
    time_ = time_el.get_text(" ", strip=True) if time_el else None

    show_a = tr.select_one("a[href*='/tv-programm/sendung/']")
    href = show_a.get("href") if show_a and show_a.get("href") else None

    title_el = show_a.select_one("strong") if show_a else None
    title = title_el.get_text(" ", strip=True) if title_el else (
        show_a.get_text(" ", strip=True) if show_a else None
    )
    title = re.sub(r"\s+", " ", title).strip() if title else None

    if href and href.startswith("/"):
        href = "https://www.tvspielfilm.de" + href

    if not (channel and day and time_ and title):
        return None

    return {"channel": channel, "day_raw": day, "time": time_, "title": title, "href": href}


def _get_max_page(soup) -> int:
    pages = []
    for a in soup.select(".pagination__items a"):
        t = a.get_text(strip=True)
        if t.isdigit():
            pages.append(int(t))
    return max(pages) if pages else 1


def _scrape_detail(href: str, session=None, timeout: int = 30) -> Dict[str, str]:
    empty = {
        "Country": "", "Year": "", "Genre": "", "Rating": "",
        "Description": "", "Quote": "", "Cast": "", "Crew": "",
    }
    s = session or requests.Session()
    if not href:
        return empty

    url = href if href.startswith("http") else "https://www.tvspielfilm.de" + href
    try:
        r = s.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "de-DE,de;q=0.9",
        })
        r.raise_for_status()
    except Exception as e:
        print(f"Detail page failed ({url}): {e}")
        return empty

    soup = BeautifulSoup(r.text, "html.parser")

    country = year = genre = ""
    underline = soup.select_one("div.stage-underline.gray span.text-row")
    if underline:
        txt = " ".join(underline.get_text(" ", strip=True).split())
        m = re.search(r"^\s*([A-Za-zÄÖÜäöüß/]+)\s+(\d{4})\s*\|\s*(.+?)\s*$", txt)
        if m:
            country, year, genre = m.group(1), m.group(2), m.group(3)
        else:
            parts = [p.strip() for p in txt.split("|")]
            if len(parts) >= 2:
                left = parts[0].split()
                if len(left) >= 2 and re.match(r"^\d{4}$", left[-1]):
                    year = left[-1]
                    country = " ".join(left[:-1]).strip()
                genre = parts[1].strip()

    rating = ""
    rating_el = soup.select_one("div.content-rating__rating-genre__thumb[class*='rating-']")
    if rating_el:
        cls = " ".join(rating_el.get("class", []))
        m = re.search(r"\brating-(\d+)\b", cls)
        if m:
            rating = m.group(1)

    desc = ""
    desc_sec = soup.select_one("section.broadcast-detail__description")
    if desc_sec:
        desc = " ".join(desc_sec.get_text(" ", strip=True).split())

    quote = ""
    q = soup.select_one("blockquote.content-rating__rating-genre__conclusion-quote")
    if q:
        quote = " ".join(q.get_text(" ", strip=True).split())

    def parse_dl(headline_text):
        h = soup.find("p", class_="headline",
                      string=re.compile(rf"^\s*{re.escape(headline_text)}\s*$"))
        if not h:
            return []
        dl = h.find_next("dl")
        if not dl:
            return []
        out = []
        for dt in dl.find_all("dt", recursive=False):
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            k = " ".join(dt.get_text(" ", strip=True).split())
            v = " ".join(dd.get_text(" ", strip=True).split())
            if k or v:
                out.append((k, v))
        return out

    cast = ", ".join(f"{k} - {v}" if v else k for k, v in parse_dl("Cast"))
    crew = ", ".join(f"{k} - {v}" if v else k for k, v in parse_dl("Crew"))

    return {
        "Country": country, "Year": year, "Genre": genre, "Rating": rating,
        "Description": desc, "Quote": quote, "Cast": cast, "Crew": crew,
    }


def scrape_tvspielfilm(
    *,
    fixed_qs: Optional[Dict[str, Any]] = None,
    timeout_seconds: int = 30,
    sleep_seconds: float = 0.3,
    log=print,
) -> List[Dict]:
    qs_base = dict(DEFAULT_FIXED_QS)
    if fixed_qs:
        qs_base.update(fixed_qs)

    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})

    rows = []
    page = 1
    max_page = 1

    while page <= max_page:
        qs = dict(qs_base, page=str(page))
        r = s.get(BASE_URL, params=qs, timeout=timeout_seconds)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        max_page = max(max_page, _get_max_page(soup))
        log(f"Scraping page {page}/{max_page}")

        for tr in soup.select("tr.hover"):
            x = _parse_row(tr)
            if x:
                rows.append(x)
        page += 1

    if not rows:
        raise RuntimeError("Scraper returned no rows — site structure may have changed.")

    df = pd.DataFrame(rows).drop_duplicates().reset_index(drop=True)
    df["channel"] = (
        df["channel"]
        .str.replace(r"\bProgramm\b", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    day_split = df["day_raw"].str.extract(
        r"^\s*(?P<weekday>[A-Za-zÄÖÜäöüß\.]+)\s*(?P<date>\d{1,2}\.\d{1,2}\.)\s*$"
    )
    df["weekday"] = day_split["weekday"]
    df["date"] = day_split["date"]
    df = df.drop(columns=["day_raw"])

    log(f"Fetched {len(df)} shows — now scraping detail pages...")

    details = []
    total = len(df)
    for i, href in enumerate(df["href"].fillna("").tolist(), start=1):
        details.append(_scrape_detail(href, session=s, timeout=timeout_seconds))
        if i % 20 == 0:
            log(f"  Detail scraping: {i}/{total}")
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    log(f"Detail scraping complete.")
    detail_df = pd.DataFrame(details)
    result = pd.concat([df.reset_index(drop=True), detail_df.reset_index(drop=True)], axis=1)
    return result.to_dict(orient="records")
