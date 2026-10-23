"""Structure checks on `collector/README.md` (spec 0003, criteria 2, 6 and 8).

Headings match case-insensitively; only the tokens the spec names are pinned.
"""

from __future__ import annotations

import re

from collector_files import (
    README,
    dockerfile_image_references,
    image_services,
    markdown_section,
    markdown_tables,
    read_text,
    relative,
)

RUN_LOCALLY = r"\brun locally on the mac\b"
IMAGES = r"\bimages\b"
ENDPOINTS = r"\binference\b.*\bendpoints?\b"
CAPTURES = r"\blocal-model captures\b"
PLACEHOLDER_CELLS = {"", "-", "?", "tbd", "todo", "unknown", "n/a"}
# Runtime users stated in the spec's Notes (images checked 2026-10-21).
SPEC_IMAGE_USERS = {
    "otel/opentelemetry-collector-contrib": "10001",
    "grafana/loki": "10001",
    "grafana/tempo": "10001",
    "victoriametrics/victoria-metrics": "root",
}


def readme_section(title_pattern: str) -> str:
    """Return a README section body, asserting the heading exists."""
    body = markdown_section(read_text(README), title_pattern)
    assert body is not None, f"expected a heading matching /{title_pattern}/i in {relative(README)}"
    return body


def has_word(text: str, word: str) -> bool:
    """True when `word` appears with no word character or hyphen on either side."""
    return re.search(rf"(?<![\w-]){re.escape(word)}(?![\w-])", text) is not None


def image_rows() -> dict[str, tuple[list[str], list[str]]]:
    """Map each pulled image to `(header, row)` from the README Images table.

    Pulled images are the compose `image:` references plus the harvester
    Dockerfile's `FROM` and `COPY --from` images (spec 0008); a `build:` service
    has no image of its own.
    """
    tables = markdown_tables(readme_section(IMAGES))
    assert tables, "expected a table under the README Images heading"
    found: dict[str, tuple[list[str], list[str]]] = {}
    pulled = [str(service.get("image", "")) for service in image_services().values()]
    for reference in [*pulled, *dockerfile_image_references()]:
        name_tag, _, digest = reference.partition("@sha256:")
        name, _, tag = name_tag.rpartition(":")
        for table in tables:
            header, *rows = table
            for row in rows:
                text = " ".join(row)
                if name in text and tag in text and digest and digest in text:
                    found[reference] = (header, row)
        assert reference in found, (
            f"expected an Images row carrying {name}, tag {tag} and its full sha256 digest"
        )
    return found


def user_cell(header: list[str], row: list[str]) -> str:
    """Return the row's cell under the header column that names the user."""
    columns = [i for i, title in enumerate(header) if "user" in title.lower()]
    assert columns, f"expected an Images table column titled with 'user', header {header}"
    return row[columns[0]].strip("` ") if columns[0] < len(row) else ""


def test_run_locally_section_has_setup_health_and_teardown_steps() -> None:
    body = readme_section(RUN_LOCALLY)
    for token in ("cp env.example .env", "docker compose up -d", "13133", "docker compose down"):
        assert token in body, f"expected {token!r} in the 'Run locally on the Mac' section"


def test_run_locally_section_creates_data_root_owned_by_uid_10001() -> None:
    body = readme_section(RUN_LOCALLY)
    assert has_word(body, "10001"), "expected the run steps to chown to UID 10001"
    for directory in ("archive", "collector-state", "loki", "tempo"):
        assert has_word(body, directory), f"expected the run steps to name {directory!r}"


def test_bring_up_no_longer_copies_dotted_env_example() -> None:
    text = read_text(README)
    assert "cp .env.example" not in text, (
        "expected README steps to say `cp env.example .env`, not the untracked .env.example"
    )


def test_images_table_lists_every_compose_image_with_both_arches_and_a_user() -> None:
    for reference, (header, row) in image_rows().items():
        text = " ".join(row)
        for platform in ("linux/amd64", "linux/arm64"):
            assert platform in text, f"expected {platform} in the Images row for {reference}"
        user = user_cell(header, row)
        assert user.lower() not in PLACEHOLDER_CELLS, (
            f"expected a runtime user in the Images row for {reference}, got {user!r}"
        )


def test_images_table_records_the_runtime_users_named_in_the_spec() -> None:
    rows = image_rows()
    for name, expected in SPEC_IMAGE_USERS.items():
        matches = [hr for ref, hr in rows.items() if ref.rpartition(":")[0].startswith(name)]
        assert matches, f"expected an Images row for {name}"
        header, row = matches[0]
        assert has_word(user_cell(header, row), expected), (
            f"expected user {expected!r} for {name}, got {user_cell(header, row)!r}"
        )


def test_endpoints_table_has_one_row_per_served_build() -> None:
    body = readme_section(ENDPOINTS)
    rows = [" ".join(row).lower() for table in markdown_tables(body) for row in table[1:]]
    assert rows, "expected a table under the README inference-endpoints heading"
    builds = {
        "llama.cpp upstream": lambda r: "llama.cpp" in r and "upstream" in r,
        "Strata": lambda r: "strata" in r,
        "Project Maya": lambda r: "project maya" in r,
    }
    matched: dict[str, set[int]] = {
        build: {i for i, row in enumerate(rows) if predicate(row)}
        for build, predicate in builds.items()
    }
    for build, indexes in matched.items():
        assert indexes, f"expected an endpoints-table row for {build}"
    solo = {
        build: {i for i in idx if sum(i in o for o in matched.values()) == 1}
        for build, idx in matched.items()
    }
    for build, indexes in solo.items():
        assert indexes, f"expected a row for {build} that names no other build"


def test_endpoints_section_records_metrics_and_slots_exposure() -> None:
    body = readme_section(ENDPOINTS)
    for endpoint in ("/metrics", "/slots"):
        assert endpoint in body, f"expected the endpoints section to record {endpoint}"


def test_images_table_has_rows_for_the_harvester_base_images() -> None:
    rows = image_rows()
    for name in ("python", "ghcr.io/astral-sh/uv"):
        assert [ref for ref in rows if ref.split(":")[0] == name], (
            f"expected an Images row for the harvester's {name} image"
        )


def test_captures_section_records_the_llama_swap_settings_and_loss_counts() -> None:
    body = readme_section(CAPTURES)
    for token in ("captureBuffer", "1024", "store.path", "evicted", "no_capture"):
        assert has_word(body, token), f"expected {token!r} in the local-model captures section"


def test_captures_section_names_the_session_header_setting() -> None:
    body = readme_section(CAPTURES)
    assert "sendSessionAffinityHeaders" in body, "expected Pi's optional header setting"
