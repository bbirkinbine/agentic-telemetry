"""Drive `collector/mac-agent/ship-ledgers.sh` against synthetic trees (spec 0004).

Every run gets an environment built from nothing: `PATH` (a stub `bin/` first,
then the system directories that hold `rsync`), `HOME` and `PROJECTS_ROOT` under
the test's `tmp_path`, plus per-test overrides. The developer's real `HOME`,
`CLAUDE_CONFIG_DIR`, `CODEX_HOME` and Pi overrides never reach the script.
`nc` and `ssh` are always stubs that record their calls, so no test touches the
network. The host directory is read as the one child of `--dest`; the value of
`hostname -s` is never asserted. Nothing here imports `agentic_telemetry`.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from claude_code_builders import cc_assistant, cc_user
from codex_builders import codex_meta, codex_rollout_name, codex_token_count, codex_turn_context
from run_table_builders import (
    assistant_message,
    review_record,
    run_record,
    session_header,
    write_ledger,
    write_result_dir,
    write_session,
    write_state,
)
from session_table_builders import (
    pi_assistant,
    pi_header,
    pi_session_file_name,
    write_jsonl,
    write_jsonl_zst,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "collector" / "mac-agent" / "ship-ledgers.sh"
BASH = "/bin/bash"
PI_SLUG = "--workspace-alpha-project--"
CC_SLUG = "-workspace-beta-project"
CODEX_DAY = "2026/10/20"

# Files under a project that the reader allowlist ships, for a project's run ID.
SHIPPED_TEMPLATE = (
    ".agentic/delegate/ledger.jsonl",
    ".agentic/delegate/results/{run}/result.json",
    ".agentic/delegate/results/{run}/state.json",
    ".agentic/delegate/results/{run}/session/pi-{run}.jsonl",
    ".agentic/telemetry/tool-calls.jsonl",
)
# Files planted in every project that must never ship (credentials, caches, logs).
PLANTED_TEMPLATE = (
    ".agentic/delegate/results/{run}/pi-agent/auth.json",
    ".agentic/delegate/results/{run}/pi-agent/sessions/pi-home.jsonl",
    ".agentic/delegate/results/{run}/segments/0001/session/segment.jsonl",
    ".agentic/delegate/results/{run}/prompt.md",
    ".agentic/delegate/results/{run}/handoff.json",
    ".agentic/delegate/results/{run}/harness.log",
    ".agentic/delegate/results/{run}/last-diagnostics.txt",
    ".agentic/delegate/results/{run}/session/notes.txt",
    ".agentic/delegate/npm-cache/_cacache/index-v5/index.jsonl",
    ".agentic/delegate/npm-cache/package.json",
    ".agentic/delegate/delegate.log",
    ".agentic/telemetry/notes.txt",
    "README.md",
    "src/example_module.py",
)


@dataclass(frozen=True)
class ShipEnv:
    """The synthetic world one script run sees."""

    root: Path
    home: Path
    projects: Path
    bin_dir: Path
    calls_log: Path


def real_rsync() -> str:
    """Return the first `rsync` on the caller's PATH; fail by name when there is none."""
    found = shutil.which("rsync")
    assert found, (
        "rsync is not on PATH: the ship-ledgers tests run the real rsync "
        "(install it; these tests do not skip)"
    )
    return found


def make_ship_env(tmp_path: Path) -> ShipEnv:
    """Create HOME, PROJECTS_ROOT and a stub bin/ with recording `nc` (exit 1) and `ssh`."""
    env = ShipEnv(
        root=tmp_path,
        home=tmp_path / "home",
        projects=tmp_path / "projects",
        bin_dir=tmp_path / "stub-bin",
        calls_log=tmp_path / "stub-calls.log",
    )
    for directory in (env.home, env.projects, env.bin_dir):
        directory.mkdir(parents=True, exist_ok=True)
    env.calls_log.touch()
    write_recording_stub(env, "nc", exit_code=1)
    write_recording_stub(env, "ssh", exit_code=0)
    return env


def _write_stub(env: ShipEnv, name: str, tail: str) -> Path:
    """Write an executable bash stub that logs its argv as one tab-separated line."""
    log = shlex.quote(str(env.calls_log))
    body = (
        "#!/bin/bash\n"
        f"{{ printf '%s' {shlex.quote(name)}; for a in \"$@\"; do printf '\\t%s' \"$a\"; done;"
        f" printf '\\n'; }} >> {log}\n"
        f"{tail}\n"
    )
    stub = env.bin_dir / name
    stub.write_text(body, encoding="utf-8")
    stub.chmod(0o755)
    return stub


def write_recording_stub(env: ShipEnv, name: str, exit_code: int) -> Path:
    """Install a stub `name` that records its call and exits `exit_code`."""
    return _write_stub(env, name, f"exit {exit_code}")


def write_rsync_passthrough(env: ShipEnv, fail_when_arg_contains: str | None = None) -> Path:
    """Install a recording `rsync` that runs the real one, or exits 23 for a matching arg."""
    real = shlex.quote(real_rsync())
    tail = f'exec {real} "$@"'
    if fail_when_arg_contains is not None:
        needle = shlex.quote(fail_when_arg_contains)
        tail = f'for a in "$@"; do case "$a" in *{needle}*) exit 23;; esac; done\n' + tail
    return _write_stub(env, "rsync", tail)


def stub_calls(env: ShipEnv, name: str) -> list[list[str]]:
    """Return the argv lists the stub `name` recorded, in call order."""
    calls: list[list[str]] = []
    for line in env.calls_log.read_text(encoding="utf-8").splitlines():
        command, *args = line.split("\t")
        if command == name:
            calls.append(args)
    return calls


def script_path_value(env: ShipEnv) -> str:
    """Return PATH for the script: stub bin/ first, then system dirs holding rsync and nc."""
    dirs = [str(env.bin_dir), str(Path(real_rsync()).parent)]
    real_nc = shutil.which("nc")
    if real_nc:
        dirs.append(str(Path(real_nc).parent))
    dirs += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    return os.pathsep.join(dict.fromkeys(dirs))


def run_ship(
    env: ShipEnv, *args: str, extra_env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the script with `/bin/bash` in an environment built from nothing."""
    environ = {
        "PATH": script_path_value(env),
        "HOME": str(env.home),
        "PROJECTS_ROOT": str(env.projects),
        "LC_ALL": "C",
    }
    environ.update(extra_env or {})
    return subprocess.run(  # noqa: S603 (fixed argv, no shell)
        [BASH, str(SCRIPT), *args],
        env=environ,
        cwd=env.root,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def describe(result: subprocess.CompletedProcess[str]) -> str:
    """Return exit code, stdout and stderr for an assertion message."""
    return f"exit {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def output_lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Return the non-blank lines of stdout and stderr together."""
    text = f"{result.stdout}\n{result.stderr}"
    return [line for line in text.splitlines() if line.strip()]


def host_dir(dest: Path) -> Path:
    """Return the one host directory the script made under `dest`."""
    children = [child for child in dest.iterdir() if child.is_dir()] if dest.is_dir() else []
    assert len(children) == 1, f"expected one host directory under --dest, got {children}"
    return children[0]


def relative_files(root: Path) -> set[str]:
    """Return every regular file under `root` as a POSIX path relative to it."""
    if not root.is_dir():
        return set()
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def shipped_files(run_id: str) -> set[str]:
    """Return the allowlisted files a project from `make_ship_project` ships."""
    return {template.format(run=run_id) for template in SHIPPED_TEMPLATE}


def planted_files(run_id: str) -> set[str]:
    """Return the files a project from `make_ship_project` holds that must not ship."""
    return {template.format(run=run_id) for template in PLANTED_TEMPLATE}


def make_ship_project(project: Path, *, delegate: bool = True, telemetry: bool = True) -> str:
    """Write a project with valid delegate evidence, telemetry and planted noise.

    The run ID is `run-<basename>`, so projects never share a run.

    Returns:
        The project's run ID.
    """
    run_id = f"run-{project.name}"
    project.mkdir(parents=True, exist_ok=True)
    skipped_prefixes = tuple(
        prefix
        for prefix, wanted in ((".agentic/delegate", delegate), (".agentic/telemetry", telemetry))
        if not wanted
    )
    for template in PLANTED_TEMPLATE:
        if skipped_prefixes and template.startswith(skipped_prefixes):
            continue
        path = project / template.format(run=run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic placeholder, never shipped\n", encoding="utf-8")
    if delegate:
        delegate_dir = project / ".agentic" / "delegate"
        record = run_record(run_id)
        write_ledger(delegate_dir, [record, review_record(run_id, "pass")])
        run_dir = write_result_dir(delegate_dir, run_id, result=record, extra_files=())
        write_state(run_dir, phase="implement", segments=[(0, 1)])
        write_session(
            run_dir,
            [session_header(f"pi-{run_id}"), assistant_message(tokens_in=40, tokens_out=8)],
            name=f"pi-{run_id}.jsonl",
        )
    if telemetry:
        write_jsonl(project / ".agentic" / "telemetry" / "tool-calls.jsonl", [{"type": "tool"}])
    return run_id


def write_claude_sessions(projects_dir: Path, session_id: str) -> set[str]:
    """Write a Claude Code session, its subagent and a non-JSONL file; return shipped files."""
    main = f"{CC_SLUG}/{session_id}.jsonl"
    agent = f"{CC_SLUG}/{session_id}/subagents/agent-{session_id}.jsonl"
    write_jsonl(
        projects_dir / main, [cc_user(session_id=session_id), cc_assistant(session_id=session_id)]
    )
    write_jsonl(projects_dir / agent, [cc_user(session_id=session_id, agent_id=f"a-{session_id}")])
    (projects_dir / CC_SLUG / "notes.txt").write_text("not a transcript\n", encoding="utf-8")
    return {main, agent}


def write_pi_sessions(sessions_dir: Path, session_id: str) -> set[str]:
    """Write a Pi session and a non-JSONL file; return the files that ship."""
    name = f"{PI_SLUG}/{pi_session_file_name(session_id)}"
    write_jsonl(sessions_dir / name, [pi_header(session_id), pi_assistant()])
    (sessions_dir / PI_SLUG / "scratch.md").write_text("not a transcript\n", encoding="utf-8")
    return {name}


def write_codex_sessions(sessions_dir: Path, plain_id: str, zst_id: str) -> set[str]:
    """Write a plain and a `.jsonl.zst` Codex rollout plus non-JSONL files; return shipped."""
    lines = [codex_meta(plain_id), codex_turn_context(), codex_token_count()]
    plain = f"{CODEX_DAY}/{codex_rollout_name(plain_id)}"
    write_jsonl(sessions_dir / plain, lines)
    zst = f"{CODEX_DAY}/{codex_rollout_name(zst_id)}.zst"
    write_jsonl_zst(sessions_dir / zst, [codex_meta(zst_id), codex_turn_context()])
    (sessions_dir / CODEX_DAY / "rollout-index.json").write_text("{}\n", encoding="utf-8")
    (sessions_dir / "history.txt").write_text("not a transcript\n", encoding="utf-8")
    return {plain, zst}
