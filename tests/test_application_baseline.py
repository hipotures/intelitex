"""Frozen adapter contracts captured before the application-layer extraction."""
from __future__ import annotations

from bookpipe.cli import parser


COMMANDS = {
    "import", "analyze", "review", "reader", "approve", "translate", "status", "export", "publish",
    "profiles", "doctor", "attempts", "usage", "catalog-import", "discover", "smoke",
}


def _parse(*arguments: str):
    return parser().parse_args(list(arguments))


def test_cli_command_inventory_and_shared_flags(tmp_path):
    project = str(tmp_path / "project")
    choices = next(action for action in parser()._actions if action.dest == "command").choices
    assert set(choices) == COMMANDS
    for command in COMMANDS - {"import", "catalog-import"}:
        args = [command, "--project", project, "--quiet"]
        if command == "smoke":
            args.append("--live")
        parsed = _parse(*args)
        assert parsed.project == tmp_path / "project"
        assert parsed.quiet is True


def test_cli_defaults_that_are_application_semantics(tmp_path):
    project = str(tmp_path / "project")
    source = str(tmp_path / "source")
    imported = _parse("import", source, "--project", project)
    assert imported.chapter_mode == "auto"
    assert imported.whole_section_limit is None
    assert imported.pass_profile == []

    translated = _parse("translate", "--project", project)
    assert translated.chunk_limit == 5
    assert translated.pass_profile == []

    published = _parse("publish", "--project", project)
    assert published.target_language == "pl"

    reviewed = _parse("review", "--project", project)
    assert (reviewed.bind, reviewed.review_port, reviewed.no_browser) == ("127.0.0.1", 8765, False)

    reader = _parse("reader", "--project", project)
    assert (reader.bind, reader.reader_port, reader.no_browser) == ("127.0.0.1", 8766, False)

    exported = _parse("export", "--project", project)
    assert exported.encoding == "utf-8" and exported.output is None

    for command in ("discover", "smoke"):
        parsed = _parse(command, "--project", project)
        assert parsed.pass_no == 1
