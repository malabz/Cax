from cax import cli


class _NoCommandContext:
    invoked_subcommand = None


class _SubcommandContext:
    invoked_subcommand = "ui"


def test_bare_cax_invokes_ui_defaults(monkeypatch):
    calls = []

    def fake_ui(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(cli, "ui", fake_ui)

    cli.main(_NoCommandContext())

    assert calls == [
        {
            "prepare_args": None,
            "from_file": None,
            "run_after": False,
            "threads": None,
            "memory_limit": None,
            "mash_auto": True,
            "mash_threshold": 0.02,
            "ask_mash": True,
            "cache_seqs": False,
        }
    ]


def test_subcommand_does_not_double_invoke_ui(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "ui", lambda **kwargs: calls.append(kwargs))

    cli.main(_SubcommandContext())

    assert calls == []
