"""C -> E: a meeting C could not read is not a meeting with no gaps (#248)."""

from __future__ import annotations

from autune_contracts import GapReport, fixtures


def test_a_report_from_before_2_5_reads_as_measured() -> None:
    report = GapReport.model_validate(fixtures.load("gap_report"))

    assert report.measured is None


def test_an_unmeasured_meeting_says_so_beside_its_empty_gaps() -> None:
    report = GapReport(meeting_id="mtg_abc", measured=False)

    assert report.gaps == [] and report.measured is False
    assert GapReport.model_validate(report.model_dump()).measured is False
