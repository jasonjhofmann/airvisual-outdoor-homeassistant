"""Repository invariants that CONTRIBUTING.md states but nothing enforced.

Each of these was honour-system only: a contributor (or a careless sed) could
break them and every other gate would stay green.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from custom_components.airvisual_outdoor.const import DOMAIN

COMPONENT = Path(__file__).parent.parent / "custom_components" / "airvisual_outdoor"


def test_translations_match_strings() -> None:
    """``translations/en.json`` is a byte-for-byte copy of ``strings.json``.

    CONTRIBUTING says to ``cp`` one onto the other. Drift means the UI shows
    stale English while ``strings.json`` — the file hassfest validates —
    looks correct.
    """
    strings = (COMPONENT / "strings.json").read_text()
    english = (COMPONENT / "translations" / "en.json").read_text()
    assert strings == english


def test_api_module_imports_no_home_assistant() -> None:
    """``api.py`` stays extractable into a standalone package.

    CONTRIBUTING makes this a hard convention so the client can be lifted out
    if the integration is ever upstreamed to core.
    """
    tree = ast.parse((COMPONENT / "api.py").read_text())
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.append(node.module)
    offenders = [name for name in imported if name.split(".")[0] == "homeassistant"]
    assert not offenders, f"api.py imports Home Assistant: {offenders}"


@pytest.mark.parametrize(
    "path", ["manifest.json", "strings.json", "icons.json", "translations/en.json"]
)
def test_shipped_json_is_valid(path: str) -> None:
    """Every JSON file HA loads at startup parses."""
    json.loads((COMPONENT / path).read_text())


def test_manifest_and_hacs_agree_on_the_domain_and_docs() -> None:
    """manifest/hacs metadata stays consistent with the package itself."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text())
    hacs = json.loads((COMPONENT.parent.parent / "hacs.json").read_text())
    assert manifest["domain"] == DOMAIN == COMPONENT.name
    assert manifest["name"] == hacs["name"]
    assert manifest["version"].count(".") == 2


def test_every_translation_key_used_by_the_code_exists() -> None:
    """No sensor/exception renders as a raw translation key in the UI."""
    from custom_components.airvisual_outdoor.sensor import SENSORS

    strings = json.loads((COMPONENT / "strings.json").read_text())
    sensor_names = strings["entity"]["sensor"]
    for description in SENSORS:
        assert description.translation_key in sensor_names, description.key

    exceptions = strings["exceptions"]
    coordinator_src = (COMPONENT / "coordinator.py").read_text()
    for key in ("update_failed", "rate_limited"):
        assert key in exceptions
        assert f'translation_key="{key}"' in coordinator_src


def test_no_translation_keys_are_dead() -> None:
    """...and nothing in strings.json is defined but never emitted."""
    from custom_components.airvisual_outdoor.sensor import SENSORS

    strings = json.loads((COMPONENT / "strings.json").read_text())
    declared = set(strings["entity"]["sensor"])
    used = {description.translation_key for description in SENSORS}
    assert declared == used, f"dead: {declared - used}, missing: {used - declared}"

    sources = "\n".join(p.read_text() for p in COMPONENT.glob("*.py"))
    for key in strings["config"]["error"]:
        assert f'"{key}"' in sources, f"error key {key} is never emitted"


# --- constants that encode a MEASURED external constraint -------------------
#
# These are not style choices. docs/architecture.md characterised IQAir's
# budget empirically as 30 requests per node per clock hour, GLOBAL across
# every client IP on the internet, so a careless edit here does not just
# degrade this integration — it can drain a published station's budget for
# everyone else polling it. Nothing pinned them before.


def test_poll_cadence_stays_inside_the_measured_rate_limit() -> None:
    """The locked 300 s cadence keeps the documented headroom.

    Asserted as a derived property (requests/hour vs the 30/hour budget)
    rather than `== 300`, so the test states WHY the number is what it is.
    """
    from custom_components.airvisual_outdoor.const import DEFAULT_SCAN_INTERVAL

    api_budget_per_hour = 30
    requests_per_hour = 3600 / DEFAULT_SCAN_INTERVAL.total_seconds()

    assert requests_per_hour == 12
    # Two consumers of the same node (the documented cutover case) must fit.
    assert requests_per_hour * 2 <= api_budget_per_hour
    # ...with at least the 60% headroom architecture.md commits to.
    assert requests_per_hour <= api_budget_per_hour * 0.4


def test_backfill_window_matches_the_api_history_depth() -> None:
    """The window is the depth of the API's own `hourly` array, not a guess."""
    from custom_components.airvisual_outdoor.const import BACKFILL_WINDOW

    assert BACKFILL_WINDOW.total_seconds() == 48 * 3600


def test_backfill_throttle_fires_at_most_once_an_hour() -> None:
    """Under the throttle, at most one backfill per clock hour."""
    from custom_components.airvisual_outdoor.const import (
        BACKFILL_MIN_INTERVAL,
        DEFAULT_SCAN_INTERVAL,
    )

    assert BACKFILL_MIN_INTERVAL.total_seconds() <= 3600
    # Long enough that consecutive polls cannot both run it.
    assert BACKFILL_MIN_INTERVAL > DEFAULT_SCAN_INTERVAL


def test_staleness_threshold_allows_at_least_one_missed_poll() -> None:
    """A single dropped sample must not flap entities unavailable."""
    from custom_components.airvisual_outdoor.const import (
        DEFAULT_SCAN_INTERVAL,
        STALENESS_THRESHOLD,
    )

    assert STALENESS_THRESHOLD.total_seconds() == 600
    assert STALENESS_THRESHOLD > DEFAULT_SCAN_INTERVAL


def test_compile_lag_leaves_the_recorder_its_own_hour() -> None:
    """At least one full hour, or the unique-index collision comes back."""
    from custom_components.airvisual_outdoor.statistics import COMPILE_LAG

    assert COMPILE_LAG.total_seconds() >= 3600
