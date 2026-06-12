import asyncio
import shlex
from pathlib import Path

from cax import command_prompt, templates
from cax.command_prompt import PrepareCommandPrompt


def test_build_prepare_defaults_uses_input_stem():
    defaults = command_prompt.build_prepare_defaults("examples/evolverMammals.txt")

    stem = "evolverMammals"
    assert defaults.input_file == "examples/evolverMammals.txt"
    assert defaults.output_dir == str(templates.default_output_dir(stem))
    assert defaults.out_seq == str(templates.default_output_dir(stem) / f"{stem}.txt")
    assert defaults.out_hal == str(templates.default_output_dir(stem) / f"{stem}.hal")
    assert defaults.job_store == str(templates.default_output_dir(stem) / "jobstore")


def test_build_prepare_command_keeps_advanced_args():
    defaults = command_prompt.build_prepare_defaults("examples/evolverMammals.txt")
    defaults.advanced_args = "--maxCores 32"

    result = command_prompt.build_prepare_command(defaults)

    assert result is not None
    tokens = shlex.split(result.args)
    assert tokens[0] == "examples/evolverMammals.txt"
    assert "--outDir" in tokens
    assert "--jobStore" in tokens
    assert tokens[-2:] == ["--maxCores", "32"]


def test_parse_prepare_history_round_trips_form_state():
    command = (
        "cactus-prepare examples/evolverMammals.txt "
        "--outDir out --outSeqFile out/seq.txt --outHal out/seq.hal --jobStore out/jobstore --maxCores 16"
    )

    defaults = command_prompt.parse_prepare_history(command)

    assert defaults is not None
    assert defaults.input_file == "examples/evolverMammals.txt"
    assert defaults.output_dir == "out"
    assert defaults.out_seq == "out/seq.txt"
    assert defaults.out_hal == "out/seq.hal"
    assert defaults.job_store == "out/jobstore"
    assert defaults.advanced_args == "--maxCores 16"


def test_prepare_help_options_are_parsed_from_cactus_help():
    options = command_prompt._parse_prepare_help_options(
        """
        options:
          --outDir OUTDIR       generated elsewhere
          --defaultCores DEFAULTCORES
                                Number of cores for each job
          --gpu [GPU]           toggle on GPU-enabled lastz
          --script              print a bash script instead of list of commands
        """
    )

    flags = [option.flag for option in options]
    assert "--outDir" not in flags
    assert "--defaultCores" in flags
    assert "--gpu" in flags
    assert "--script" in flags
    assert flags[0] == "--defaultCores"
    default_cores = next(option for option in options if option.flag == "--defaultCores")
    assert default_cores.metavar == "DEFAULTCORES"
    assert "Number of cores" in default_cores.description


def test_prepare_prompt_starts_on_input_field():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            assert app.focused is app._input_file
            assert app._input_file is not None
            assert app._input_file.value
            assert app._output_dir is not None
            assert app._job_store is not None
            assert app._output_dir.value
            assert app._job_store.value.endswith("jobstore")

    asyncio.run(run_smoke())


def test_prepare_prompt_tab_opens_visible_file_candidates():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            assert app._input_file is not None
            app._input_file.value = "examples/evolver"
            app._input_file.cursor_position = len(app._input_file.value)
            await pilot.press("tab")
            await pilot.pause(0.1)
            assert any("evolverMammals.txt" in candidate.value for candidate in app._visible_candidates)
            assert app._candidate_panel is not None
            assert not app._candidate_panel.has_class("hidden")

    asyncio.run(run_smoke())


def test_prepare_prompt_candidates_exclude_prepare_output_logs():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            assert app._input_file is not None
            app._input_file.value = "examples/cactus"
            app._input_file.cursor_position = len(app._input_file.value)
            await pilot.press("tab")
            await pilot.pause(0.1)
            assert all("cactus-prepare_example.txt" not in candidate.value for candidate in app._visible_candidates)

    asyncio.run(run_smoke())


def test_large_tree_example_is_available_as_template():
    loaded = templates.load_templates()

    assert any(template.spec.endswith("examples/largeTreeUiTest.txt") for template in loaded)


def test_prepare_prompt_candidate_enter_accepts_path():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            assert app._input_file is not None
            app._input_file.value = "examples/evolverM"
            app._input_file.cursor_position = len(app._input_file.value)
            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app._input_file.value.endswith("evolverMammals.txt")
            assert not app._visible_candidates

    asyncio.run(run_smoke())


def test_prepare_prompt_updates_jobstore_when_output_changes():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            assert app._output_dir is not None
            assert app._job_store is not None
            app._output_dir.value = "custom-out"
            await pilot.pause(0.1)
            assert app._job_store.value == str(Path("custom-out") / "jobstore")

    asyncio.run(run_smoke())


def test_prepare_prompt_keyboard_can_reach_editable_fields():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            assert app.focused is app._input_file
            await pilot.press("tab")
            assert app.focused is app._output_dir
            await pilot.press("tab")
            assert app.focused is app._job_store
            await pilot.press("tab")
            assert app.focused is app._advanced_args
            await pilot.press("shift+tab")
            assert app.focused is app._job_store
            await pilot.press("ctrl+n")
            assert app.focused is app._advanced_args
            await pilot.press("ctrl+p")
            assert app.focused is app._job_store

    asyncio.run(run_smoke())


def test_prepare_prompt_arrow_keys_select_fields_spatially():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            assert app.focused is app._input_file
            await pilot.press("down")
            assert app.focused is app._output_dir
            await pilot.press("right")
            assert app.focused is app._job_store
            await pilot.press("down")
            assert app.focused is app._advanced_args
            await pilot.press("up")
            assert app.focused is app._output_dir
            await pilot.press("left")
            assert app.focused is app._input_file
            await pilot.press("right")
            assert app.focused is app._output_dir
            await pilot.press("left")
            assert app.focused is app._input_file

    asyncio.run(run_smoke())


def test_prepare_prompt_shows_advanced_field_and_compact_help():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            assert app._advanced_args is not None
            assert app._advanced_args.region.y < app.size.height - 1
            assert len(list(app.query("#prepare-help"))) == 1
            assert len(list(app.query("#setup-title"))) == 0
            assert len(list(app.query("#actions"))) == 0
            assert len(list(app.query("Footer"))) == 0

    asyncio.run(run_smoke())


def test_prepare_prompt_help_exposes_templates_and_history_when_idle():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(0.1)
            assert app._help is not None
            help_text = str(app._help.render())
            assert "Enter start" in help_text
            assert "F3 templates" in help_text
            assert "F4 history" in help_text
            assert "Ctrl+Enter" not in help_text
            assert "PgUp/PgDn" not in help_text
            assert "Arrows" not in help_text
            assert "next" not in help_text.lower()

    asyncio.run(run_smoke())


def test_prepare_prompt_help_switches_only_when_candidates_are_visible():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(0.1)
            assert app._input_file is not None
            assert app._help is not None
            app._input_file.value = "examples/evolver"
            app._input_file.cursor_position = len(app._input_file.value)
            await pilot.press("tab")
            await pilot.pause(0.1)
            help_text = str(app._help.render())
            assert "Up/Down choose" in help_text
            assert "F4 history" not in help_text
            await pilot.press("enter")
            await pilot.pause(0.1)
            help_text = str(app._help.render())
            assert "F4 history" in help_text
            assert "Up/Down choose" not in help_text

    asyncio.run(run_smoke())


def test_template_selector_loads_selected_template_with_keyboard(tmp_path: Path):
    first_seq = tmp_path / "first.seq"
    second_seq = tmp_path / "second.seq"
    first_seq.write_text("(leaf1,leaf2)Anc0;\nleaf1 a.fa\nleaf2 b.fa\n", encoding="utf-8")
    second_seq.write_text("(leaf1,leaf2)Anc0;\nleaf1 a.fa\nleaf2 b.fa\n", encoding="utf-8")
    first_out = tmp_path / "first-out"
    second_out = tmp_path / "second-out"
    app = PrepareCommandPrompt()
    app._templates = [
        templates.Template(
            name="First template",
            spec=str(first_seq),
            params={
                "out_dir": str(first_out),
                "out_seq": str(first_out / "seq.txt"),
                "out_hal": str(first_out / "seq.hal"),
                "job_store": str(first_out / "jobstore"),
                "extra": "--defaultCores 4",
            },
            source="test",
        ),
        templates.Template(
            name="Second template",
            spec=str(second_seq),
            params={
                "out_dir": str(second_out),
                "out_seq": str(second_out / "seq.txt"),
                "out_hal": str(second_out / "seq.hal"),
                "job_store": str(second_out / "jobstore"),
                "extra": "--defaultCores 8",
            },
            source="test",
        )
    ]

    async def run_smoke() -> None:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("f3")
            await pilot.pause(0.1)
            assert isinstance(app.screen, command_prompt.TemplateSelector)
            assert len(list(app.screen.query("#template-help"))) == 1
            assert len(list(app.screen.query("Footer"))) == 0
            assert app.screen._index == 0
            await pilot.press("down")
            assert app.screen._index == 1
            await pilot.press("up")
            assert app.screen._index == 0
            await pilot.press("down")
            assert app.screen._index == 1
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app._input_file is not None
            assert app._output_dir is not None
            assert app._advanced_args is not None
            assert app._input_file.value == str(second_seq)
            assert app._output_dir.value == str(second_out)
            assert app._advanced_args.value == "--defaultCores 8"

    asyncio.run(run_smoke())


def test_history_viewer_loads_selected_command_with_keyboard(tmp_path: Path, monkeypatch):
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(command_prompt.history, "HISTORY_FILE", history_file)
    seq = tmp_path / "history.seq"
    seq.write_text("(leaf1,leaf2)Anc0;\nleaf1 a.fa\nleaf2 b.fa\n", encoding="utf-8")
    command_prompt.history.save_history(
        [
            "cactus-prepare examples/evolverMammals.txt --outDir first-out",
            (
                f"cactus-prepare {shlex.quote(str(seq))} --outDir hist-out "
                "--outSeqFile hist-out/seq.txt --outHal hist-out/seq.hal "
                "--jobStore hist-out/jobstore --defaultCores 16"
            )
        ]
    )
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("f4")
            await pilot.pause(0.1)
            assert isinstance(app.screen, command_prompt.HistoryViewer)
            assert len(list(app.screen.query("#history-help"))) == 1
            assert len(list(app.screen.query("Footer"))) == 0
            assert app.screen._index == 0
            await pilot.press("down")
            assert app.screen._index == 1
            await pilot.press("up")
            assert app.screen._index == 0
            await pilot.press("down")
            assert app.screen._index == 1
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app._input_file is not None
            assert app._output_dir is not None
            assert app._advanced_args is not None
            assert app._input_file.value == str(seq)
            assert app._output_dir.value == "hist-out"
            assert app._advanced_args.value == "--defaultCores 16"

    asyncio.run(run_smoke())


def test_history_viewer_deletes_selected_command_with_keyboard(tmp_path: Path, monkeypatch):
    history_file = tmp_path / "history.json"
    monkeypatch.setattr(command_prompt.history, "HISTORY_FILE", history_file)
    seq_a = tmp_path / "a.seq"
    seq_b = tmp_path / "b.seq"
    seq_a.write_text("(leaf1,leaf2)Anc0;\nleaf1 a.fa\nleaf2 b.fa\n", encoding="utf-8")
    seq_b.write_text("(leaf1,leaf2)Anc0;\nleaf1 a.fa\nleaf2 b.fa\n", encoding="utf-8")
    first = f"cactus-prepare {shlex.quote(str(seq_a))} --outDir a-out"
    second = f"cactus-prepare {shlex.quote(str(seq_b))} --outDir b-out"
    command_prompt.history.save_history([first, second])
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test(size=(140, 30)) as pilot:
            await pilot.pause(0.1)
            await pilot.press("f4")
            await pilot.pause(0.1)
            await pilot.press("d")
            await pilot.pause(0.1)
            entries = command_prompt.history.load_history()
            assert [entry.command for entry in entries] == [second]

    asyncio.run(run_smoke())


def test_prepare_prompt_advanced_args_complete_from_help_options():
    app = PrepareCommandPrompt()
    app._prepare_options = (
        command_prompt.PrepareOption("--defaultCores", "DEFAULTCORES", "Number of cores"),
        command_prompt.PrepareOption("--gpu", "[GPU]", "toggle GPU"),
    )

    async def run_smoke() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            assert app._advanced_args is not None
            app._advanced_args.focus()
            app._advanced_args.value = "--def"
            app._advanced_args.cursor_position = len(app._advanced_args.value)
            await pilot.press("tab")
            await pilot.pause(0.1)
            assert app._visible_candidates
            assert app._visible_candidates[0].label.startswith("--defaultCores DEFAULTCORES")
            assert app._advanced_candidate_panel is not None
            assert not app._advanced_candidate_panel.has_class("hidden")
            assert app._candidate_panel is not None
            assert app._candidate_panel.has_class("hidden")
            await pilot.press("enter")
            assert app._advanced_args.value == "--defaultCores "

    asyncio.run(run_smoke())


def test_prepare_prompt_advanced_candidates_can_page():
    app = PrepareCommandPrompt()
    app._prepare_options = tuple(
        command_prompt.PrepareOption(f"--option{index}", f"VALUE{index}", f"option {index}")
        for index in range(14)
    )

    async def run_smoke() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            assert app._advanced_args is not None
            app._advanced_args.focus()
            await pilot.press("tab")
            await pilot.pause(0.1)
            assert len(app._visible_candidates) == 14
            assert app._candidate_index == 0
            await pilot.press("pagedown")
            assert app._candidate_index == command_prompt.CANDIDATE_PAGE_SIZE
            assert app._candidate_offset == 1
            await pilot.press("end")
            assert app._candidate_index == 13
            await pilot.press("pageup")
            assert app._candidate_index == 13 - command_prompt.CANDIDATE_PAGE_SIZE
            await pilot.press("home")
            assert app._candidate_index == 0

    asyncio.run(run_smoke())


def test_prepare_prompt_preview_stays_compact():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause(0.1)
            assert app._preview is not None
            assert app._preview.region.height <= 2

    asyncio.run(run_smoke())


def test_prepare_prompt_enter_submits_without_mouse():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            await pilot.press("enter")
            result = pilot.app._return_value
            assert result.action == "submit"
            assert result.executable == "cactus-prepare"
            assert "--jobStore" in shlex.split(result.args)

    asyncio.run(run_smoke())


def test_prepare_prompt_rejects_prepare_output_log_file():
    app = PrepareCommandPrompt()

    async def run_smoke() -> None:
        async with app.run_test() as pilot:
            assert app._input_file is not None
            assert app._status is not None
            app._input_file.value = "examples/cactus-prepare_example.txt"
            await pilot.pause(0.1)
            app.action_submit()
            await pilot.pause(0.1)
            assert app.focused is app._input_file
            assert pilot.app._return_value is None

    asyncio.run(run_smoke())


def test_parse_prepare_command_returns_prompt_result():
    command = "cactus-prepare examples/evolverMammals.txt --outDir out --outSeqFile out/seq.txt"
    result = command_prompt._parse_prepare_command(command)

    assert result is not None
    assert result.action == "submit"
    assert result.executable == "cactus-prepare"
    assert shlex.split(result.args)[0] == "examples/evolverMammals.txt"
