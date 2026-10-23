"""The long-running harvester loop: poll, report, sleep, never die of llama-swap.

Log lines carry counts and API paths only: no host, port, key, client address
or captured content, because they end up in `docker compose logs`.
"""

from collections.abc import Callable

from agentic_telemetry.captures.harvest import PollReport
from agentic_telemetry.captures.llama_swap_api import (
    LlamaSwapQueryError,
    LlamaSwapUnreachableError,
)

HEARTBEAT_POLLS = 40


def report_line(report: PollReport) -> str:
    """Return the one-line summary of a poll that saved or lost anything."""
    line = (
        f"poll: rows={report.rows} saved={report.saved} evicted={report.evicted} "
        f"no_capture={report.no_capture} id_reset={int(report.id_reset)}"
    )
    return line + (f" error={report.errors}" if report.errors else "")


def run_forever(
    *,
    poll: Callable[[], PollReport],
    interval: float,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
    max_polls: int | None = None,
) -> None:
    """Poll every `interval` seconds until `max_polls` polls have run (forever when None).

    A poll that saves or loses a row logs its counts. A failed poll logs one
    line naming the API path and is retried after the next sleep. After
    `HEARTBEAT_POLLS` quiet polls in a row, one heartbeat line shows the loop
    is alive.
    """
    polls = 0
    quiet = 0
    while max_polls is None or polls < max_polls:
        if polls:
            sleep(interval)
        polls += 1
        try:
            report = poll()
        except (LlamaSwapUnreachableError, LlamaSwapQueryError) as error:
            log(f"poll failed: {error}")
            quiet = 0
            continue
        if report.rows or report.id_reset:
            log(report_line(report))
            quiet = 0
            continue
        quiet += 1
        if quiet == HEARTBEAT_POLLS:
            log(
                f"heartbeat: {quiet} quiet polls; cursor epoch={report.cursor.epoch} "
                f"last_id={report.cursor.last_id}"
            )
            quiet = 0
