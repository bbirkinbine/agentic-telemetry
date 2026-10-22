"""Docs, templates and ignore rules for the inference host agent (spec 0007).

The README install steps and coverage, the amended D1 bind address on the Mac
(`0.0.0.0` on the home network, `127.0.0.1` elsewhere), the image-upgrade list,
the AGENTS.md don't-touch line and the `.gitignore` backstop for real targets files.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from collector_files import (
    AGENT_TARGETS_EXAMPLE,
    ENV_EXAMPLE,
    README,
    REPO_ROOT,
    markdown_section,
    parse_env_example,
    read_text,
    relative,
)

AGENTS_MD = REPO_ROOT / "AGENTS.md"
AGENT_SECTION = r"inference host agent"
RUN_LOCALLY = r"\brun locally on the mac\b"
IMAGES = r"\bimages\b"
VERIFICATION = r"^verification$"
BIND_ADDR_BULLET = re.compile(r"^(\s*)[-*]\s+`TELEMETRY_BIND_ADDR`")
AGENT_CONFIG_PATH = "collector/inference-agent/otel-agent.yaml"


def section_of(path_text: str, title_pattern: str, where: str) -> str:
    """Return a Markdown section body, asserting the heading exists."""
    body = markdown_section(path_text, title_pattern)
    assert body is not None, f"expected a heading matching /{title_pattern}/i in {where}"
    return body


def agent_section() -> str:
    """Return the README's 'Inference host agent' section."""
    return section_of(read_text(README), AGENT_SECTION, relative(README))


def bind_addr_bullet() -> str:
    """Return the Run-locally list item for `TELEMETRY_BIND_ADDR`, with its sub-lines."""
    lines = section_of(read_text(README), RUN_LOCALLY, relative(README)).splitlines()
    for index, line in enumerate(lines):
        match = BIND_ADDR_BULLET.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        block = [line]
        for later in lines[index + 1 :]:
            if later.strip() and len(later) - len(later.lstrip()) <= indent:
                break
            block.append(later)
        return "\n".join(block)
    raise AssertionError("expected a `- `TELEMETRY_BIND_ADDR`` bullet in 'Run locally on the Mac'")


def bind_addr_comment() -> str:
    """Return the comment lines directly above `TELEMETRY_BIND_ADDR=` in collector/env.example."""
    lines = read_text(ENV_EXAMPLE).splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("TELEMETRY_BIND_ADDR=")]
    assert len(starts) == 1, f"expected one TELEMETRY_BIND_ADDR= line in {relative(ENV_EXAMPLE)}"
    comment: list[str] = []
    for line in reversed(lines[: starts[0]]):
        if not line.startswith("#"):
            break
        comment.insert(0, line)
    return "\n".join(comment)


def git_ignores(path: str) -> bool:
    """True when the repository's ignore rules match `path`, whether or not it exists."""
    git = shutil.which("git")
    assert git, "expected git on PATH to evaluate .gitignore"
    result = subprocess.run(  # noqa: S603 (fixed argv, no shell)
        [git, "check-ignore", "--no-index", "-q", path],
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode in {0, 1}, f"git check-ignore failed for {path!r}"
    return result.returncode == 0


def test_agent_section_gives_the_install_and_run_steps() -> None:
    body = agent_section()
    for token in (
        "compose.yaml",
        "otel-agent.yaml",
        "env.example",
        "chown 10001:10001",
        "/var/lib/agentic-telemetry/queue",
        "docker compose up -d",
        "13133",
    ):
        assert token in body, f"expected {token!r} in the README 'Inference host agent' section"


def test_agent_section_names_the_compose_plugin_and_llama_cpp_metrics_flag() -> None:
    """D4 (Compose v2 plugin on the host) and D5 (`--metrics` on llama.cpp)."""
    body = agent_section()
    assert re.search(r"\bplugin\b", body, re.IGNORECASE), "expected the Compose plugin step"
    assert "--metrics" in body, "expected the llama.cpp --metrics prerequisite"


def test_agent_section_says_files_are_copied_and_a_change_needs_a_re_copy() -> None:
    body = agent_section()
    assert re.search(r"\bcop(y|ies|ied)\b", body, re.IGNORECASE), (
        "expected the step that copies the tracked files to the inference host"
    )
    assert re.search(r"\bre-?cop|\bcop\w*\b[^.]*\bagain\b", body, re.IGNORECASE), (
        "expected the README to say a config change needs a re-copy"
    )


def test_agent_section_says_the_example_ports_are_placeholders() -> None:
    body = agent_section()
    assert re.search(r"\bplaceholders?\b", body, re.IGNORECASE), (
        "expected the agent section to call the example ports placeholders"
    )
    assert "ENDPOINTS.md" in body, "expected the real ports to be pointed at ENDPOINTS.md"
    groups = json.loads(read_text(AGENT_TARGETS_EXAMPLE))
    ports = {str(group["targets"][0]).rpartition(":")[2] for group in groups}
    missing = sorted(port for port in ports if port not in body)
    assert not missing, f"expected the agent section to name the example ports {missing}"


def test_agent_section_warns_to_keep_the_example_out_of_the_targets_directory() -> None:
    """The `*.json` glob would scrape placeholder ports if the example sat beside targets.json."""
    body = agent_section()
    for name in ("targets.example.json", "targets.json"):
        assert name in body, f"expected the agent section to name {name}"


def test_coverage_text_names_maya_and_sybil_as_uncovered() -> None:
    blocks = [b for b in re.split(r"\n\s*\n", read_text(README)) if "uncovered" in b.lower()]
    prose = [b for b in blocks if not b.lstrip().startswith("|")]
    naming_both = [b for b in prose if "Project Maya" in b and "Sybil" in b]
    assert naming_both, (
        "expected a README coverage paragraph or list that names Project Maya and Sybil "
        "as uncovered"
    )


def test_endpoints_section_no_longer_says_the_stack_does_not_scrape_the_servers() -> None:
    text = read_text(README)
    assert "Today the stack does not scrape these servers" not in text, (
        "expected the endpoints section to describe the inference host agent instead"
    )


def test_run_locally_bind_addr_covers_home_network_and_away() -> None:
    """Amended D1: `0.0.0.0` on the home network; stop or go back to loopback elsewhere."""
    bullet = bind_addr_bullet()
    for address in ("0.0.0.0", "127.0.0.1"):  # noqa: S104 (documented, not bound here)
        assert address in bullet, f"expected the TELEMETRY_BIND_ADDR bullet to name {address}"
    assert re.search(r"\bstop\b", bullet, re.IGNORECASE), (
        "expected the bullet to say to stop the stack away from home"
    )


def test_run_locally_drops_the_dhcp_reservation_guidance() -> None:
    body = section_of(read_text(README), RUN_LOCALLY, relative(README))
    for rejected in ("reservation", "Private Wi-Fi"):
        assert rejected not in body, f"amended D1 drops {rejected!r} guidance from Run locally"
    assert re.search(r"\bfirewall\b", body, re.IGNORECASE), (
        "expected Run locally to say the macOS firewall must allow Docker"
    )


def test_env_example_bind_addr_comment_names_all_interfaces_at_home_and_keeps_loopback() -> None:
    comment = bind_addr_comment()
    for address in ("0.0.0.0", "127.0.0.1"):  # noqa: S104 (documented, not bound here)
        assert address in comment, f"expected the TELEMETRY_BIND_ADDR comment to name {address}"
    assert not re.search(r"never\W+0\.0\.0\.0", comment, re.IGNORECASE), (
        "amended D1 replaces 'never 0.0.0.0' with 0.0.0.0 on the home network"
    )
    assert "reserv" not in comment.lower(), "amended D1 needs no DHCP-reserved address"
    value = parse_env_example().get("TELEMETRY_BIND_ADDR")
    assert value == "127.0.0.1", f"expected the example value to stay 127.0.0.1, got {value!r}"


def test_images_upgrade_list_names_the_agent_compose_file() -> None:
    body = section_of(read_text(README), IMAGES, relative(README))
    assert "inference-agent/compose.yaml" in body, (
        "expected the image-upgrade list to name collector/inference-agent/compose.yaml"
    )
    assert "all four places" not in body, "expected the upgrade list to count five places"


def test_verification_records_the_agent_config_validation() -> None:
    body = section_of(read_text(README), VERIFICATION, relative(README))
    entries = re.split(r"\n(?=[-*] )", body)  # top-level bullets with their sub-items
    recorded = [
        entry
        for entry in entries
        if "0007" in entry and "otel-agent.yaml" in entry and "validate" in entry
    ]
    assert recorded, (
        "expected a Verification entry naming spec 0007, otel-agent.yaml and its validate run"
    )


def test_agents_md_dont_touch_list_names_the_agent_config() -> None:
    body = section_of(read_text(AGENTS_MD), r"don.t-touch", relative(AGENTS_MD))
    assert AGENT_CONFIG_PATH in body, f"expected {AGENT_CONFIG_PATH} in AGENTS.md Don't-touch"


def test_gitignore_ignores_real_targets_files_but_not_the_example() -> None:
    for path in (
        "collector/inference-agent/targets.json",
        "collector/inference-agent/targets.prod.json",
        "collector/inference-agent/.env",
    ):
        assert git_ignores(path), f"expected .gitignore to ignore {path}"
    example = "collector/inference-agent/targets.example.json"
    assert not git_ignores(example), f"expected .gitignore to keep {example} tracked"
