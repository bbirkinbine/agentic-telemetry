"""Local-model request and response captures from llama-swap (spec 0008).

`captures-pull` copies every call llama-swap records, with its capture, into
daily `captures/<host>/<YYYY-MM-DD>.jsonl.zst` files before llama-swap's
in-memory cache evicts it. `captures show` joins those calls to session-table
sessions and prints them turn by turn.
"""
