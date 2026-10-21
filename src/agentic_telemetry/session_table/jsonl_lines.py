"""Stream the JSON objects of a transcript, plain `.jsonl` or zstandard `.jsonl.zst`.

Transcripts can run to ~100k lines, so the file is read one line at a time and
never held in memory. A line that cannot be used is skipped with a warning
naming `path:line`. The warning never repeats the line's text, which is
captured tool content.
"""

import io
import json
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import zstandard

from agentic_telemetry.run_table.json_values import JsonObject, as_object

COMPRESSED_SUFFIX = ".zst"


@contextmanager
def open_transcript(path: Path) -> Iterator[BinaryIO]:
    """Open a transcript for line-by-line binary reading, decompressing `.zst` files.

    Raises:
        OSError: The file cannot be opened.
    """
    with path.open("rb") as raw:
        if path.suffix != COMPRESSED_SUFFIX:
            yield raw
            return
        reader = zstandard.ZstdDecompressor().stream_reader(raw)
        with io.BufferedReader(reader) as buffered:
            yield buffered


class JsonlLines:
    """Iterate `(line_no, object)` pairs from one transcript.

    Line numbers are 1-based physical lines. Blank lines are skipped silently.
    After iteration, `warnings` holds one entry per skipped line and
    `line_count` the number of physical lines read.
    """

    def __init__(self, path: Path) -> None:
        """Remember the transcript to read; nothing is opened until iteration."""
        self.path = path
        self.warnings: list[str] = []
        self.line_count = 0

    def __iter__(self) -> Generator[tuple[int, JsonObject]]:
        """Yield each usable line as a JSON object with its line number.

        Raises:
            OSError: The file cannot be read.
            zstandard.ZstdError: A `.zst` file is not valid zstandard data.
        """
        self.warnings = []
        self.line_count = 0
        with open_transcript(self.path) as handle:
            for line_no, raw in enumerate(handle, start=1):
                self.line_count = line_no
                record = self._decode(line_no, raw)
                if record is not None:
                    yield line_no, record

    def _decode(self, line_no: int, raw: bytes) -> JsonObject | None:
        """Return one line as a JSON object, or None after recording why it was skipped."""
        where = f"{self.path}:{line_no}"
        try:
            line = raw.decode("utf-8")
        except UnicodeDecodeError:
            self.warnings.append(f"{where}: skipped a line that is not UTF-8")
            return None
        if not line.strip():
            return None
        try:
            decoded: object = json.loads(line)
        except json.JSONDecodeError as error:
            self.warnings.append(f"{where}: skipped malformed JSON ({error.msg})")
            return None
        except (ValueError, RecursionError) as error:
            # Valid JSON that Python refuses: an integer over the digit limit, or
            # nesting deeper than the recursion limit.
            self.warnings.append(f"{where}: skipped unparseable JSON ({type(error).__name__})")
            return None
        record = as_object(decoded)
        if record is None:
            self.warnings.append(f"{where}: skipped a line that is not a JSON object")
        return record
