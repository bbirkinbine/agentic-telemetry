"""Static checks on the launchd job for `ship-ledgers.sh` (spec 0006).

The tracked plist is a template: every path and host in it is a placeholder,
and the owner copies it to `~/Library/LaunchAgents/` with real values. It is
parsed with `plistlib` so the checks run on Linux CI too; `plutil -lint` runs
on the Mac at implement time, not here. Keys follow launchd.plist(5):
`StartInterval` is an integer, `EnvironmentVariables` holds strings only.
"""

from __future__ import annotations

import ipaddress
import plistlib

from collector_files import SHIP_PLIST, read_text, relative

APPLE_DTD = "-//Apple//DTD PLIST 1.0//EN"
SCRIPT_SUFFIX = "collector/mac-agent/ship-ledgers.sh"
INTERVAL_SECONDS = 900  # 15 minutes, as the script's header promises
RFC5737_NETS = tuple(
    ipaddress.ip_network(net) for net in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
HOME_MARKERS = ("/Users/", "/home/", "~")


def load_plist() -> dict[str, object]:
    """Parse the tracked plist as XML, asserting it exists and holds a dictionary."""
    text = read_text(SHIP_PLIST)
    data = plistlib.loads(text.encode("utf-8"), fmt=plistlib.FMT_XML)
    assert isinstance(data, dict), f"expected {relative(SHIP_PLIST)} to hold a <dict>"
    return data


def environment_variables() -> dict[str, object]:
    """Return the plist's `EnvironmentVariables` dictionary, asserting it exists."""
    variables = load_plist().get("EnvironmentVariables")
    assert isinstance(variables, dict), (
        f"expected an EnvironmentVariables <dict> in {relative(SHIP_PLIST)}"
    )
    return variables


def is_documentation_ipv4(value: str) -> bool:
    """Return True when `value` is an IPv4 literal inside an RFC 5737 range."""
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return any(address in net for net in RFC5737_NETS)


def test_plist_is_xml_with_the_apple_doctype() -> None:
    text = read_text(SHIP_PLIST)

    assert text.lstrip().startswith("<?xml"), f"expected an XML plist, got {text[:40]!r}"
    assert "<!DOCTYPE plist" in text and APPLE_DTD in text, (
        f"expected the Apple plist DOCTYPE ({APPLE_DTD}) in {relative(SHIP_PLIST)}"
    )


def test_label_matches_the_file_name_in_the_example_namespace() -> None:
    label = load_plist().get("Label")

    assert label == SHIP_PLIST.stem, f"expected Label {SHIP_PLIST.stem!r}, got {label!r}"
    assert isinstance(label, str) and label.startswith("com.example."), label


def test_job_runs_every_15_minutes() -> None:
    interval = load_plist().get("StartInterval")

    assert type(interval) is int, f"expected StartInterval as <integer>, got {interval!r}"
    assert interval == INTERVAL_SECONDS, f"expected StartInterval {INTERVAL_SECONDS}"


def test_job_runs_the_tracked_script_with_system_bash() -> None:
    arguments = load_plist().get("ProgramArguments")

    assert isinstance(arguments, list) and len(arguments) >= 2, arguments
    assert arguments[0] == "/bin/bash", (
        f"expected /bin/bash (the 3.2 the script targets), got {arguments[0]!r}"
    )
    assert isinstance(arguments[-1], str) and arguments[-1].endswith(SCRIPT_SUFFIX), (
        f"expected the last argument to end with {SCRIPT_SUFFIX}, got {arguments[-1]!r}"
    )


def test_environment_names_projects_root_and_vm_as_strings() -> None:
    variables = environment_variables()

    for name in ("PROJECTS_ROOT", "TELEMETRY_VM"):
        value = variables.get(name)
        assert isinstance(value, str) and value, (
            f"expected a non-empty <string> for {name}, got {value!r}"
        )


def test_vm_is_a_placeholder_host_or_documentation_address() -> None:
    vm = environment_variables().get("TELEMETRY_VM")

    assert isinstance(vm, str), vm
    assert vm.endswith(".example") or is_documentation_ipv4(vm), (
        f"expected TELEMETRY_VM under .example (RFC 2606) or in RFC 5737, got {vm!r}"
    )


def test_log_paths_are_absolute_placeholders() -> None:
    data = load_plist()

    for key in ("StandardOutPath", "StandardErrorPath"):
        path = data.get(key)
        assert isinstance(path, str) and path.startswith("/"), (
            f"expected {key} to be an absolute path, got {path!r}"
        )
        found = [marker for marker in HOME_MARKERS if marker in path]
        assert not found, f"expected {key} to be a placeholder path, got {path!r} ({found})"


def test_job_is_not_kept_alive() -> None:
    keep_alive = load_plist().get("KeepAlive", False)

    assert keep_alive is False, (
        f"expected no KeepAlive (the job runs on StartInterval and exits), got {keep_alive!r}"
    )
