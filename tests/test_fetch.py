from datetime import date

import pytest

from nhs_pipeline import fetch

PAGE = "https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/rtt-data-2026-27/"


def test_financial_years():
    assert fetch.financial_year(date(2026, 3, 31)) == "2025-26"
    assert fetch.financial_year(date(2026, 4, 1)) == "2026-27"
    assert fetch.financial_years("2023-24", date(2026, 11, 3)) == ["2023-24", "2024-25", "2025-26", "2026-27"]


@pytest.mark.parametrize(("month", "year", "expected"), [
    ("September", "2026", "2026-09"), ("Sep", "26", "2026-09"), ("JANUARY", "2024", "2024-01"),
])
def test_parse_period(month, year, expected):
    assert fetch.parse_period(month, year) == expected


def test_parse_period_rejects_unknown_month():
    with pytest.raises(ValueError):
        fetch.parse_period("Smarch", "2026")


def test_find_links_picks_monthly_files_and_prefers_revised():
    html = """
    <a href="/statistics/wp-content/uploads/sites/2/2026/10/Full-CSV-data-file-Aug26-ZIP-3M-abc.zip">
        Full CSV data file Aug26 (ZIP, 3M)</a>
    <a href="/statistics/wp-content/uploads/sites/2/2026/11/Full-CSV-data-file-Aug26-revised-ZIP-3M.zip">
        Full CSV data file Aug26 revised (ZIP, 3M)</a>
    <a href="/statistics/wp-content/uploads/sites/2/2026/11/Full-CSV-data-file-Sep26-ZIP-3M-xyz.zip">
        Full CSV data file Sep26 (ZIP, 3M)</a>
    <a href="/statistics/wp-content/uploads/sites/2/2026/11/Incomplete-Provider-Sep26-XLSX.xlsx">
        Incomplete Provider Sep26 (XLSX, 2M)</a>
    """
    links = fetch.find_links(html, PAGE, fetch.DATASETS["rtt"])
    assert [link.period for link in links] == ["2026-08", "2026-09"]
    assert links[0].revised and "revised" in links[0].url
    assert links[1].url.startswith("https://www.england.nhs.uk/statistics/wp-content/")


def test_find_links_ae_ignores_xls():
    html = """
    <a href="/x/Monthly-AE-September-2026.xls">Monthly A&amp;E September 2026 (XLS, 417KB)</a>
    <a href="/x/Monthly-AE-September-2026.csv">Monthly A&amp;E September 2026 (CSV, 28KB)</a>
    """
    links = fetch.find_links(html, PAGE, fetch.DATASETS["ae"])
    assert len(links) == 1 and links[0].url.endswith(".csv") and links[0].period == "2026-09"


def test_fetch_skips_files_already_held(tmp_path, monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, text="", content=b""):
            self.text, self.content = text, content

    class FakeClient:
        def get(self, url):
            calls.append(url)
            if url.endswith("/"):
                return FakeResponse(text='<a href="/f/a.csv">Monthly A&amp;E April 2026 (CSV, 1KB)</a>')
            return FakeResponse(content=b"Period,Org Code\n")

    monkeypatch.setattr(fetch, "financial_years", lambda first: ["2026-27"])
    fetch.fetch(tmp_path, "2026-27", ["ae"], FakeClient())
    assert len(calls) == 2
    entries = fetch.fetch(tmp_path, "2026-27", ["ae"], FakeClient())
    assert len(calls) == 3  # page only, file not downloaded again
    assert entries[0]["sha256"] and (tmp_path / "ae" / "2026-04.csv").exists()
