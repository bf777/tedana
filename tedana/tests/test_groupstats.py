"""Tests for tedana.groupstats module."""

from pathlib import Path

import pytest

from tedana import groupstats


def test_generate_report(tmp_path):
    # Create a temporary directory for the report
    report_dir = tmp_path
    # report_dir.mkdir()

    # Generate the report
    groupstats.setup_tgs_report(report_dir)

    # Check that the report file was created
    report_file = report_dir / "open_tgs_report.py"
    assert report_file.exists()

    # Check that the report file is not empty
    assert report_file.stat().st_size > 0


if __name__ == "__main__":
    test_path = '/Users/brandon/Desktop/fMRI/tedana-test/tedana_input_dir'
    test_generate_report(Path(test_path))
