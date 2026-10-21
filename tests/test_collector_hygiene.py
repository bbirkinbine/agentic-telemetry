"""Public-repo hygiene over an explicit list of tracked collector files (spec 0003, criterion 9).

No home paths, no IPv4 outside RFC 5737 / loopback / `0.0.0.0`, and every
`host:port` names a placeholder, a compose service, `localhost` or a variable.
"""

from __future__ import annotations

import ipaddress
import re

import pytest
from collector_files import HYGIENE_FILES, REPO_ROOT, compose_services, read_text

ALLOWED_NETS = tuple(
    ipaddress.ip_network(net)
    for net in ("127.0.0.0/8", "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
)
# Four dotted octets, not part of a longer dotted run (so 0.163.1 or 1.2.3.4.5 never match).
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?!\.?\d|\w)")
# A name host directly before `:<digits>`. Hosts start with a letter, so times and
# IP literals never match; a `$NAME` shell or `${...}` reference is skipped by the
# look-behind; the look-ahead rejects image tags (`loki:3.7.9`) and digests
# (`sha256:0db2...`) because the digits run on into `.digit` or a word character.
HOST_PORT = re.compile(r"(?<![\w.$-])([A-Za-z][\w.-]*):(\d{1,5})(?!\.\d|\w)")


def offending_lines(path_text: str, pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    """Return `(line number, match)` for every match of `pattern`, one per occurrence."""
    return [
        (line_no, match.group(0))
        for line_no, line in enumerate(path_text.splitlines(), start=1)
        for match in pattern.finditer(line)
    ]


@pytest.mark.parametrize("path", HYGIENE_FILES)
def test_tracked_collector_file_has_no_home_directory_paths(path: str) -> None:
    text = read_text(REPO_ROOT / path)
    hits = offending_lines(text, re.compile(r"/(?:Users|home)/"))
    assert not hits, f"{path}: home-directory paths at {hits}"


@pytest.mark.parametrize("path", HYGIENE_FILES)
def test_tracked_collector_file_has_only_documentation_or_loopback_ipv4(path: str) -> None:
    text = read_text(REPO_ROOT / path)
    bad: list[tuple[int, str]] = []
    for line_no, literal in offending_lines(text, IPV4):
        try:
            address = ipaddress.IPv4Address(literal)
        except ValueError:
            continue  # an octet over 255: a version-like string, not an address
        if str(address) == "0.0.0.0":  # noqa: S104 (listen-on-all is allowed in configs)
            continue
        if not any(address in net for net in ALLOWED_NETS):
            bad.append((line_no, literal))
    assert not bad, f"{path}: IPv4 outside RFC 5737, loopback and 0.0.0.0 at {bad}"


@pytest.mark.parametrize("path", HYGIENE_FILES)
def test_tracked_collector_file_host_port_names_only_placeholders_or_services(
    path: str,
) -> None:
    text = read_text(REPO_ROOT / path)
    allowed_hosts = {*compose_services(), "localhost"}
    bad = [
        (line_no, literal)
        for line_no, literal in offending_lines(text, HOST_PORT)
        if not ((host := literal.rsplit(":", 1)[0]).endswith(".example") or host in allowed_hosts)
    ]
    assert not bad, (
        f"{path}: host:port must be *.example, a compose service, localhost or a "
        f"${{...}} reference; found {bad}"
    )
