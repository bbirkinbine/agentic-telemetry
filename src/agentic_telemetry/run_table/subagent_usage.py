"""A Pi `subagent` tool result's usage, child runs and model names (spec 0009).

Pi records the tokens a subagent spent on the parent transcript's `subagent` tool
result: `usage` holds the totals over the subagent's turns, `details.results` has
one entry per child run, and `details.totalChildUsage.turns` counts its turns. The
subagent's own transcripts (under `subagent-artifacts/` and
`<parent-session>/<id>/`) repeat those turns, so the run table never reads them.
"""

from collections.abc import Collection
from dataclasses import dataclass

from agentic_telemetry.run_table.json_values import (
    JsonObject,
    as_object,
    object_list,
    opt_int,
    opt_str,
)


@dataclass(frozen=True)
class SubagentCall:
    """What one usage-bearing `subagent` tool result adds to its run.

    `models` are the `details.results[].model` names as Pi wrote them,
    `<provider>/<model>`; `strip_provider` removes the prefix once the run's
    providers are known. `turns` is None when the result has no integer
    `totalChildUsage.turns`.
    """

    usage: JsonObject
    runs: int
    turns: int | None
    models: tuple[str, ...]


def read_subagent_call(message: JsonObject) -> SubagentCall | None:
    """Return the subagent call a transcript message records, if it records one.

    A `subagent` tool result without a `usage` object (a management call that
    lists agents) ran no child and returns None. `isError` is not checked: a child
    that failed or timed out still spent the tokens its `usage` reports.

    Args:
        message: The `message` object of a transcript line.

    Returns:
        The call's usage, child runs, turns and model names, or None for any other
        message.
    """
    if opt_str(message.get("role")) != "toolResult":
        return None
    if opt_str(message.get("toolName")) != "subagent":
        return None
    usage = as_object(message.get("usage"))
    if usage is None:
        return None
    details = as_object(message.get("details")) or {}
    results = object_list(details.get("results"))
    child_usage = as_object(details.get("totalChildUsage")) or {}
    names = (opt_str(result.get("model")) for result in results)
    return SubagentCall(
        usage=usage,
        runs=len(results),
        turns=opt_int(child_usage.get("turns")),
        models=tuple(name for name in names if name is not None),
    )


def strip_provider(name: str, providers: Collection[str]) -> str:
    """Remove a `<provider>/` prefix from `name` when the run used that provider.

    Only the part up to the first `/` is compared, so a model name that itself
    contains `/` keeps the rest. A prefix that is not one of `providers` is kept:
    it may be part of the model's own name.

    Args:
        name: A `details.results[].model` value.
        providers: The `provider` values of the run's assistant messages.

    Returns:
        The name without its provider prefix, or `name` unchanged.
    """
    prefix, slash, rest = name.partition("/")
    if slash and prefix in providers:
        return rest
    return name
