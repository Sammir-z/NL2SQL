"""Pytest configuration and test execution logger."""

import datetime
from pathlib import Path

import pytest

LOG_FILE = Path("logs/test_pytest.log")


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write("\n" + "=" * 80 + "\n")
        f.write(
            f"Pytest Session Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        f.write("=" * 80 + "\n")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()

    if report.when == "call":
        status = report.outcome.upper()
        duration = f"{report.duration:.4f}s"
        nodeid = item.nodeid
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {status:7} {nodeid} ({duration})\n")
            if report.failed and report.longrepr:
                f.write(f"--- FAILURE DETAILS ---\n{report.longrepr}\n-----------------------\n")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write("-" * 80 + "\n")
        f.write(
            f"Pytest Session Finished: exitstatus={exitstatus} "
            f"at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        f.write("=" * 80 + "\n\n")
