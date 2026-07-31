from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from cax import cli
from cax.models import Plan, PrepareHeader, Round, RunSettings, Step


runner = CliRunner()


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


def test_version_reports_installed_package_version(monkeypatch):
    monkeypatch.setattr(
        cli.metadata,
        "version",
        lambda package: "9.8.7" if package == "cactus-ramax" else None,
    )
    monkeypatch.setattr(
        cli,
        "ui",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("UI must not launch")),
    )

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == "CAX 9.8.7"


def test_auto_export_writes_final_commands_without_cleaning_or_running(
    tmp_path: Path,
    monkeypatch,
):
    plan = Plan(
        header=PrepareHeader(
            generated_by="cactus-prepare --outSeqFile seq.fa",
            date=datetime.now(),
        ),
        preprocess=[
            Step(
                raw=(
                    "cactus-preprocess js in.txt out.txt "
                    "--maxCores 32 --maxMemory 64Gi"
                ),
                kind="preprocess",
            )
        ],
        rounds=[
            Round(
                name="round",
                root="Anc0",
                target_hal="out.hal",
                replace_with_ramax=True,
                manual_ramax_command=(
                    "ramax -i seq.fa -o out.hal --root Anc0 --threads 32"
                ),
            )
        ],
        hal_merges=[],
        out_seq_file="seq.fa",
        out_dir=str(tmp_path / "steps-output"),
    )
    settings = RunSettings(
        thread_count=8,
        memory_limit_bytes=16 * 2**30,
        mash_auto=True,
        mash_distance_threshold=0.02,
    )
    output_path = tmp_path / "exports" / "commands.txt"

    monkeypatch.setattr(cli, "_build_runtime_settings", lambda **kwargs: settings)
    monkeypatch.setattr(cli.shutil, "which", lambda executable: f"/usr/bin/{executable}")
    monkeypatch.setattr(
        cli,
        "_prepare_plan_preview",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("export mode must not prepare cleanup")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_ensure_clean_environment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("export mode must not clean outputs")
        ),
    )
    monkeypatch.setattr(cli, "_load_prepare_text", lambda *args, **kwargs: "prepare output")
    monkeypatch.setattr(cli.parser, "parse_prepare_script", lambda text: plan)
    monkeypatch.setattr(
        cli.seq_cache,
        "find_preprocess_input_seq_file",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(cli.seq_cache, "count_remote_sources", lambda path: 0)
    monkeypatch.setattr(
        cli.mash_auto_module,
        "apply_mash_distance_defaults",
        lambda *args, **kwargs: SimpleNamespace(
            computed=1,
            enabled_ramax=1,
            pairwise_computed=1,
            pairwise_cached=0,
        ),
    )
    monkeypatch.setattr(
        cli,
        "PlanRunner",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("export mode must not run the plan")
        ),
    )

    cli.auto(
        prepare_args=None,
        from_file=tmp_path / "prepare.txt",
        seqfile=None,
        threads=None,
        memory_limit=None,
        export_commands=output_path,
        mash_auto=True,
        mash_threshold=0.02,
        ask_mash=False,
        cache_seqs=False,
    )

    assert settings.resume is False
    assert output_path.read_text(encoding="utf-8").splitlines() == [
        (
            "cactus-preprocess js in.txt out.txt "
            "--maxCores 8 --maxMemory 16Gi"
        ),
        "ramax -i seq.fa -o out.hal --root Anc0 --threads 8",
    ]
