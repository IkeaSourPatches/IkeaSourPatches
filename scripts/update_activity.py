#!/usr/bin/env python3
"""Render a local profile card from GitHub's public contribution calendar.

Run with --input calendar.html to replay a saved response without network access.
The summary, dates, daily tooltips, and levels must agree before the old card is
replaced. An upstream markup change therefore fails visibly instead of publishing
an empty or misleading card. This reads public data and needs no API token.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import tempfile
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo


CALENDAR_URL = "https://github.com/users/IkeaSourPatches/contributions"
REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Activity:
    first_day: date
    last_day: date
    days: int
    contributions: int
    active_days: int
    longest_run: int


class CalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: dict[str, tuple[date, int]] = {}
        self.tooltips: dict[str, str] = {}
        self.ranges: list[tuple[date, date]] = []
        self.summaries: list[str] = []
        self._tooltip_key: str | None = None
        self._tooltip_text: list[str] = []
        self._in_summary = False
        self._summary_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if attributes.get("data-from") and attributes.get("data-to"):
            self.ranges.append(
                (
                    date.fromisoformat(attributes["data-from"][:10]),
                    date.fromisoformat(attributes["data-to"][:10]),
                )
            )
        if tag == "td" and attributes.get("data-date"):
            identifier = attributes.get("id")
            if not identifier or identifier in self.cells:
                raise ValueError("Missing or duplicate contribution-cell ID")
            level = int(attributes.get("data-level", "-1"))
            if level not in range(5):
                raise ValueError("Unrecognized contribution-cell level")
            self.cells[identifier] = (date.fromisoformat(attributes["data-date"]), level)
        if tag == "tool-tip":
            if self._tooltip_key is not None:
                raise ValueError("Unexpected nested calendar tooltip")
            self._tooltip_key = attributes.get("for")
            self._tooltip_text = []
        if tag == "h2" and attributes.get("id") == "js-contribution-activity-description":
            self._in_summary = True
            self._summary_text = []

    def handle_data(self, text: str) -> None:
        if self._tooltip_key is not None:
            self._tooltip_text.append(text)
        if self._in_summary:
            self._summary_text.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tool-tip" and self._tooltip_key is not None:
            if self._tooltip_key in self.tooltips:
                raise ValueError("Duplicate calendar tooltip")
            self.tooltips[self._tooltip_key] = " ".join("".join(self._tooltip_text).split())
            self._tooltip_key = None
        if tag == "h2" and self._in_summary:
            self.summaries.append(" ".join("".join(self._summary_text).split()))
            self._in_summary = False


def parse_activity(html: str) -> Activity:
    calendar = CalendarParser()
    calendar.feed(html)
    calendar.close()
    if len(calendar.ranges) != 1 or len(calendar.summaries) != 1:
        raise ValueError("Expected one complete public contribution calendar")
    summary = re.fullmatch(
        r"([\d,]+) contributions? in the last year", calendar.summaries[0]
    )
    if summary is None:
        raise ValueError("GitHub's contribution summary format changed")
    expected_total = int(summary[1].replace(",", ""))
    first_day, last_day = calendar.ranges[0]
    days = (last_day - first_day).days + 1
    if not 365 <= days <= 373 or len(calendar.cells) != days:
        raise ValueError("Contribution calendar does not contain a complete past year")

    daily: dict[date, int] = {}
    for identifier, (day, level) in calendar.cells.items():
        tooltip = calendar.tooltips.get(identifier, "")
        match = re.fullmatch(
            r"(No|[\d,]+) contributions? on "
            r"([A-Za-z]+) (\d{1,2})(?:st|nd|rd|th)?(?:, (\d{4}))?\.",
            tooltip,
        )
        if match is None:
            raise ValueError(f"Missing or unrecognized daily count for {day}")
        count = 0 if match[1] == "No" else int(match[1].replace(",", ""))
        # Include the cell year so a real February 29 does not default to 1900.
        tooltip_day = datetime.strptime(f"{match[2]} {match[3]} {day.year}", "%B %d %Y")
        if (tooltip_day.month, tooltip_day.day) != (day.month, day.day):
            raise ValueError(f"Tooltip and cell dates disagree for {day}")
        if match[4] is not None and int(match[4]) != day.year:
            raise ValueError(f"Tooltip and cell years disagree for {day}")
        if (count == 0) != (level == 0):
            raise ValueError(f"Tooltip count and calendar level disagree for {day}")
        if day in daily:
            raise ValueError(f"Duplicate contribution date: {day}")
        daily[day] = count

    expected_dates = {first_day + timedelta(days=offset) for offset in range(days)}
    if set(daily) != expected_dates:
        raise ValueError("Contribution calendar has missing or out-of-range dates")
    total = sum(daily.values())
    if total != expected_total:
        raise ValueError(f"Daily counts ({total}) disagree with GitHub's summary ({expected_total})")

    longest_run = current_run = 0
    for day in sorted(daily):
        current_run = current_run + 1 if daily[day] else 0
        longest_run = max(longest_run, current_run)
    return Activity(first_day, last_day, days, total, sum(n > 0 for n in daily.values()), longest_run)


def describe_activity(activity: Activity, updated: date) -> str:
    return escape(
        f"IkeaSourPatches's GitHub contributions shown on the public profile in the past year, "
        f"{activity.first_day} through {activity.last_day}: "
        f"{activity.contributions} contributions across {activity.active_days} active days. "
        f"Longest run: {activity.longest_run} consecutive active days. Updated {updated}."
    )


def render_card(activity: Activity, updated: date) -> str:
    description = describe_activity(activity, updated)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="640" height="180" viewBox="0 0 640 180" fill="none" role="img" aria-labelledby="title desc">
  <title id="title">A year of little things — GitHub profile activity</title>
  <desc id="desc">{description}</desc>
  <rect width="640" height="180" rx="16" fill="#F5F1E8"/>
  <path d="M214 65V132M426 65V132" stroke="#DAD8CC"/>
  <text x="28" y="33" fill="#596658" font-family="Arial, Helvetica, sans-serif" font-size="18">Past year on GitHub</text>
  <g fill="#29382F" font-family="Arial, Helvetica, sans-serif" font-size="44" font-weight="700">
    <text x="28" y="96">{activity.contributions:,}</text>
    <text x="242" y="96">{activity.active_days}</text>
    <text x="454" y="96">{activity.longest_run}<tspan font-size="22" font-weight="400"> days</tspan></text>
  </g>
  <g fill="#596658" font-family="Arial, Helvetica, sans-serif" font-size="18">
    <text x="28" y="125">Contributions</text>
    <text x="242" y="125">Active days</text>
    <text x="454" y="125">Longest run</text>
  </g>
  <path d="M28 157H48" stroke="#D5693E" stroke-width="3"/>
  <text x="61" y="162" fill="#596658" font-family="Arial, Helvetica, sans-serif" font-size="14">Public calendar · Updated {updated}</text>
  <rect x="555" y="22" width="10" height="10" rx="2" fill="#DAD8CC"/>
  <rect x="570" y="22" width="10" height="10" rx="2" fill="#E8C9AE"/>
  <rect x="585" y="22" width="10" height="10" rx="2" fill="#E69C72"/>
  <rect x="600" y="22" width="10" height="10" rx="2" fill="#D5693E"/>
</svg>
'''


def render_mobile_card(activity: Activity, updated: date) -> str:
    description = describe_activity(activity, updated)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="360" height="240" viewBox="0 0 360 240" fill="none" role="img" aria-labelledby="title desc">
  <title id="title">A year of little things — GitHub profile activity</title>
  <desc id="desc">{description}</desc>
  <rect width="360" height="240" rx="16" fill="#F5F1E8"/>
  <text x="22" y="33" fill="#596658" font-family="Arial, Helvetica, sans-serif" font-size="18">Past year on GitHub</text>
  <rect x="286" y="22" width="9" height="9" rx="2" fill="#E8C9AE"/>
  <rect x="300" y="22" width="9" height="9" rx="2" fill="#E69C72"/>
  <rect x="314" y="22" width="9" height="9" rx="2" fill="#D5693E"/>
  <g fill="#29382F" font-family="Arial, Helvetica, sans-serif" font-size="32" font-weight="700">
    <text x="22" y="88">{activity.contributions:,}</text>
    <text x="22" y="136">{activity.active_days}</text>
    <text x="22" y="184">{activity.longest_run}</text>
  </g>
  <g fill="#596658" font-family="Arial, Helvetica, sans-serif" font-size="18">
    <text x="154" y="83">Contributions</text>
    <text x="154" y="131">Active days</text>
    <text x="154" y="179">Longest run (days)</text>
  </g>
  <path d="M22 204H338" stroke="#DAD8CC"/>
  <text x="22" y="225" fill="#596658" font-family="Arial, Helvetica, sans-serif" font-size="13">Updated {updated} · Public calendar</text>
</svg>
'''


def validate_card(contents: str) -> None:
    root = ET.fromstring(contents)
    namespace = "{http://www.w3.org/2000/svg}"
    if (
        root.tag != namespace + "svg"
        or root.find(namespace + "title") is None
        or root.find(namespace + "desc") is None
    ):
        raise ValueError("Generated card is not a complete accessible SVG")


def write_atomic(destination: Path, contents: str) -> None:
    if destination.exists() and destination.read_text(encoding="utf-8") == contents:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Replay a saved GitHub calendar HTML response")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "assets/activity.svg")
    parser.add_argument(
        "--mobile-output", type=Path, default=REPO_ROOT / "assets/activity-mobile.svg",
        help="Mobile card path; also written when --output overrides the desktop path",
    )
    parser.add_argument(
        "--updated", type=date.fromisoformat,
        default=datetime.now(ZoneInfo("Asia/Singapore")).date(),
        help="Update date, YYYY-MM-DD; defaults to today's date in Singapore",
    )
    arguments = parser.parse_args()
    try:
        if arguments.input is not None:
            html = arguments.input.read_text(encoding="utf-8")
        else:
            request = Request(CALENDAR_URL, headers={"User-Agent": "IkeaSourPatches-profile-activity"})
            with urlopen(request, timeout=30) as response:
                html = response.read().decode("utf-8")
        activity = parse_activity(html)
        if arguments.updated < activity.last_day:
            raise ValueError("Update date precedes the calendar's last date")
        if arguments.output.resolve() == arguments.mobile_output.resolve():
            raise ValueError("Desktop and mobile cards need different output paths")
        desktop_card = render_card(activity, arguments.updated)
        mobile_card = render_mobile_card(activity, arguments.updated)
        # Validate both before replacing either previously generated card.
        validate_card(desktop_card)
        validate_card(mobile_card)
        write_atomic(arguments.output, desktop_card)
        write_atomic(arguments.mobile_output, mobile_card)
    except (ValueError, OSError, ET.ParseError) as error:
        raise SystemExit(f"Activity update failed: {error}") from error
    print(
        f"Parsed {activity.days} days ({activity.first_day} through {activity.last_day}): "
        f"{activity.contributions} contributions, {activity.active_days} active days, "
        f"longest run {activity.longest_run} days. "
        f"Wrote {arguments.output} and {arguments.mobile_output}."
    )


if __name__ == "__main__":
    main()
