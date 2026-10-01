"""Tests for the repair issues of SLEMS."""

from types import SimpleNamespace
from unittest.mock import patch

from custom_components.slems import problems
from custom_components.slems.const import DOMAIN, OperatingMode
from custom_components.slems.controller import ControlStatus


class FakeIssues:
    """Issue registry of Home Assistant, reduced to what the reporter uses."""

    def __init__(self, existing: set[tuple[str, str]]) -> None:
        self.issues = {key: None for key in existing}
        self.async_get = lambda _hass: self
        self.IssueSeverity = SimpleNamespace(WARNING="warning")

    def async_create_issue(self, _hass, domain, issue_id, **_kwargs) -> None:
        self.issues[(domain, issue_id)] = None

    def async_delete_issue(self, _hass, domain, issue_id) -> None:
        self.issues.pop((domain, issue_id), None)


def coordinator(batteries) -> SimpleNamespace:
    return SimpleNamespace(
        batteries=batteries,
        settings=SimpleNamespace(operating_mode=OperatingMode.ACTIVE),
        controller=SimpleNamespace(status=ControlStatus.ACTIVE),
        grid_meter=None,
        grid_meter_error=None,
    )


def test_first_update_removes_issues_of_removed_batteries() -> None:
    kept = SimpleNamespace(
        subentry_id="kept", name="Kept", not_responding=True, unreadable_since=None,
        release_pending_since=None,
    )
    registry = FakeIssues(
        {
            (DOMAIN, "battery_not_responding_removed"),
            (DOMAIN, "battery_not_responding_kept"),
            ("other", "battery_not_responding_removed"),
        }
    )
    reporter = problems.ProblemReporter(None, coordinator([kept]))
    with (
        patch.object(problems, "ir", registry),
        patch.object(reporter, "_update_cap_notifications", lambda *_: None),
    ):
        reporter.update(0.0)
    assert set(registry.issues) == {
        (DOMAIN, "battery_not_responding_kept"),
        ("other", "battery_not_responding_removed"),
    }


def test_outstanding_release_becomes_a_repair_issue() -> None:
    battery = SimpleNamespace(
        subentry_id="b1", name="Venus", not_responding=False, unreadable_since=None,
        release_pending_since=100.0,
    )
    registry = FakeIssues(set())
    reporter = problems.ProblemReporter(None, coordinator([battery]))
    with (
        patch.object(problems, "ir", registry),
        patch.object(reporter, "_update_cap_notifications", lambda *_: None),
    ):
        reporter.update(200.0)
        assert (DOMAIN, "battery_release_failed_b1") not in registry.issues
        reporter.update(100.0 + problems.RELEASE_FAILED_AFTER_S)
        assert (DOMAIN, "battery_release_failed_b1") in registry.issues
        battery.release_pending_since = None
        reporter.update(1000.0)
        assert (DOMAIN, "battery_release_failed_b1") not in registry.issues
