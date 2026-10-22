"""Static checks on Loki's ingest window in `collector/loki-config.yaml` (spec 0006).

Loki refuses a line older than `reject_old_samples_max_age` (default one week),
so a first backfill of older ledger lines would never reach Loki. Spec 0006
widens the window to the 730-day retention period, keeps the check itself on,
and keeps the compactor applying retention.
"""

from __future__ import annotations

from collector_files import COLLECTOR_DIR, load_yaml_mapping, relative

LOKI_CONFIG = COLLECTOR_DIR / "loki-config.yaml"
WINDOW = "17520h"  # 730 days, equal to retention_period


def section(name: str) -> dict[str, object]:
    """Return one top-level mapping of the Loki config, asserting it exists."""
    value = load_yaml_mapping(LOKI_CONFIG).get(name)
    assert isinstance(value, dict), f"expected a `{name}` mapping in {relative(LOKI_CONFIG)}"
    return value


def test_old_sample_window_is_730_days() -> None:
    limits = section("limits_config")

    assert limits.get("reject_old_samples_max_age") == WINDOW, (
        f"expected limits_config.reject_old_samples_max_age == {WINDOW!r} "
        f"(the default one week drops a backfill), got {limits.get('reject_old_samples_max_age')!r}"
    )


def test_old_sample_window_equals_the_retention_period() -> None:
    limits = section("limits_config")

    assert limits.get("retention_period") == WINDOW, limits.get("retention_period")
    assert limits.get("reject_old_samples_max_age") == limits.get("retention_period"), (
        "expected reject_old_samples_max_age to equal retention_period, so every line "
        "Loki keeps is a line it accepts"
    )


def test_old_sample_rejection_stays_on() -> None:
    limits = section("limits_config")

    assert limits.get("reject_old_samples", True) is True, (
        "expected reject_old_samples absent (default true) or true; widen the window, "
        "do not switch the check off"
    )


def test_compactor_applies_retention() -> None:
    assert section("compactor").get("retention_enabled") is True
