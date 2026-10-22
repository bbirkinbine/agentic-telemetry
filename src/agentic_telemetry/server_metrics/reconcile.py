"""How well the server's token counts match the run's own transcript (spec 0005).

The join's only evidence that a row is about the traffic this run caused: one run
at a time on the server keeps a window unambiguous, and this is what detects a
violation even when the other traffic is not in the table.
"""

MATCH_EXACT = "exact"
MATCH_SERVER_MORE = "server_more"
MATCH_SERVER_LESS = "server_less"
MATCH_NOT_COMPARED = "not_compared"


def token_match(
    tokens_in: int | None,
    tokens_out: int | None,
    server_prompt: int | None,
    server_predicted: int | None,
) -> str:
    """Compare the run's transcript tokens with the server's increases.

    Prompt and predicted are compared separately. A mixed result — more on one
    side, less on the other — is `server_more` (spec 0005, D3): the run's window
    held traffic it did not cause, which is the finding that matters.

    Args:
        tokens_in: The run's transcript input tokens, or None with no transcript.
        tokens_out: The run's transcript output tokens, or None.
        server_prompt: Increase of the served model's prompt-token counter, or
            None when the server recorded no series for the window.
        server_predicted: Increase of its predicted-token counter, or None.

    Returns:
        `exact`, `server_more`, `server_less` or `not_compared`.
    """
    if tokens_in is None or tokens_out is None:
        return MATCH_NOT_COMPARED
    if server_prompt is None or server_predicted is None:
        return MATCH_NOT_COMPARED
    if server_prompt > tokens_in or server_predicted > tokens_out:
        return MATCH_SERVER_MORE
    if server_prompt < tokens_in or server_predicted < tokens_out:
        return MATCH_SERVER_LESS
    return MATCH_EXACT
