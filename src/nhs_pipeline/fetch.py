"""Find and download the monthly NHS England statistics files.

NHS England publishes one page per financial year for each collection. Each
page links to one file per month. File names carry random suffixes, so the
links are found by their link text, never by guessing URLs.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

BASE = "https://www.england.nhs.uk/statistics/statistical-work-areas/"
USER_AGENT = (
    "nhs-performance-pipeline/1.0 "
    "(+https://github.com/AbelYonathan-max/nhs-performance-pipeline)"
)

MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "january", "february", "march", "april", "may", "june",
            "july", "august", "september", "october", "november", "december",
        ],
        start=1,
    )
}


@dataclass(frozen=True)
class Dataset:
    key: str
    title: str
    page: str
    link_text: re.Pattern[str]
    suffix: str


DATASETS: dict[str, Dataset] = {
    "ae": Dataset(
        key="ae",
        title="A&E Attendances and Emergency Admissions",
        page=BASE
        + "ae-waiting-times-and-activity/ae-attendances-and-emergency-admissions-{fy}/",
        # e.g. "Monthly A&E September 2026 (CSV, 28KB)"
        link_text=re.compile(
            r"^Monthly A&E (?P<month>[A-Za-z]+) (?P<year>\d{4})\b.*\bCSV\b", re.I
        ),
        suffix=".csv",
    ),
    "rtt": Dataset(
        key="rtt",
        title="Consultant-led Referral to Treatment Waiting Times",
        page=BASE + "rtt-waiting-times/rtt-data-{fy}/",
        # e.g. "Full CSV data file Sep26 (ZIP, 3M)"
        link_text=re.compile(
            r"^Full CSV data file (?P<month>[A-Za-z]{3})(?P<year>\d{2})\b", re.I
        ),
        suffix=".zip",
    ),
}


@dataclass
class SourceFile:
    dataset: str
    period: str  # YYYY-MM
    url: str
    link_text: str
    page: str
    revised: bool


@dataclass
class ManifestEntry(SourceFile):
    path: str
    sha256: str
    bytes: int
    downloaded_at: str


def financial_year(day: date) -> str:
    """'2026-27' for any date from 1 April 2026 to 31 March 2027."""
    start = day.year if day.month >= 4 else day.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def financial_years(first: str, today: date | None = None) -> list[str]:
    """Every financial year label from `first` up to the current one."""
    today = today or date.today()
    start = int(first[:4])
    end = int(financial_year(today)[:4])
    return [f"{y}-{(y + 1) % 100:02d}" for y in range(start, end + 1)]


def parse_period(month: str, year: str) -> str:
    """Turn ('September', '2026') or ('Sep', '26') into '2026-09'."""
    key = month.lower()
    number = MONTHS.get(key) or next(
        (n for name, n in MONTHS.items() if name.startswith(key[:3])), None
    )
    if number is None:
        raise ValueError(f"unknown month {month!r}")
    full_year = int(year) if len(year) == 4 else 2000 + int(year)
    return f"{full_year:04d}-{number:02d}"


def find_links(html: str, page_url: str, dataset: Dataset) -> list[SourceFile]:
    """Return one file per month, preferring a revised file when there are two."""
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, SourceFile] = {}
    for anchor in soup.find_all("a", href=True):
        text = " ".join(anchor.get_text(" ", strip=True).split())
        match = dataset.link_text.search(text)
        if not match:
            continue
        href = urljoin(page_url, anchor["href"])
        if not href.lower().split("?")[0].endswith(dataset.suffix):
            continue
        period = parse_period(match["month"], match["year"])
        revised = "revis" in text.lower()
        candidate = SourceFile(dataset.key, period, href, text, page_url, revised)
        current = found.get(period)
        if current is None or (revised and not current.revised):
            found[period] = candidate
    return sorted(found.values(), key=lambda s: s.period)


class Client:
    """Small polite HTTP client: one request a second, retries with backoff."""

    def __init__(self, delay: float = 1.0, retries: int = 4) -> None:
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self.retries = retries
        self._last = 0.0

    def get(self, url: str) -> requests.Response:
        for attempt in range(self.retries + 1):
            wait = self.delay - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.session.get(url, timeout=60)
                if response.status_code == 404:
                    response.raise_for_status()
                if response.status_code < 500 and response.status_code != 429:
                    response.raise_for_status()
                    return response
                log.warning("HTTP %s for %s", response.status_code, url)
            except requests.ConnectionError as exc:
                log.warning("connection error for %s: %s", url, exc)
            if attempt < self.retries:
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError(f"giving up on {url}")


def discover(dataset: Dataset, first_fy: str, client: Client) -> list[SourceFile]:
    files: list[SourceFile] = []
    for fy in financial_years(first_fy):
        url = dataset.page.format(fy=fy)
        try:
            response = client.get(url)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                log.info("no page yet for %s %s", dataset.key, fy)
                continue
            raise
        links = find_links(response.text, url, dataset)
        log.info("%s %s: %d monthly files", dataset.key, fy, len(links))
        files.extend(links)
    return files


def load_manifest(raw_dir: Path) -> dict[str, dict]:
    path = raw_dir / "manifest.json"
    if not path.exists():
        return {}
    return {f"{e['dataset']}/{e['period']}": e for e in json.loads(path.read_text())}


def save_manifest(raw_dir: Path, entries: dict[str, dict]) -> None:
    ordered = [entries[k] for k in sorted(entries)]
    (raw_dir / "manifest.json").write_text(json.dumps(ordered, indent=2) + "\n")


def fetch(
    raw_dir: Path,
    first_fy: str,
    datasets: list[str] | None = None,
    client: Client | None = None,
) -> list[dict]:
    """Download every monthly file not already held. Returns the manifest."""
    client = client or Client()
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(raw_dir)
    for key in datasets or list(DATASETS):
        dataset = DATASETS[key]
        sources = discover(dataset, first_fy, client)
        if not sources:
            raise RuntimeError(f"no files found for {key}; has the page layout changed?")
        for source in sources:
            ident = f"{source.dataset}/{source.period}"
            target = raw_dir / source.dataset / f"{source.period}{dataset.suffix}"
            known = manifest.get(ident)
            if known and known["url"] == source.url and target.exists():
                continue
            content = client.get(source.url).content
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            entry = ManifestEntry(
                **asdict(source),
                path=target.relative_to(raw_dir).as_posix(),
                sha256=hashlib.sha256(content).hexdigest(),
                bytes=len(content),
                downloaded_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )
            manifest[ident] = asdict(entry)
            log.info("downloaded %s (%d bytes)", ident, len(content))
            save_manifest(raw_dir, manifest)
    save_manifest(raw_dir, manifest)
    return [manifest[k] for k in sorted(manifest)]
