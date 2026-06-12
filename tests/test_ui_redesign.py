import asyncio
from datetime import datetime
from pathlib import Path

from textual.app import App

from cax import templates, tree_utils, ui as ui_module
from cax.models import Plan, PrepareHeader, Round, RunSettings, Step
from cax.runner import PlanRunner
from cax.ui import (
    CommandEditModal,
    CommandSelectionModal,
    CommandTarget,
    MashThresholdModal,
    PlanTreeBrowser,
    PlanUIApp,
    RUN_HELP_STYLE,
    RamaxOptionsModal,
    RoundPickerModal,
    RunSettingsScreen,
    ThreadCountModal,
    UIResult,
)


class _FakeClick:
    def __init__(self, meta: dict[str, object]):
        self.style = type("Style", (), {"meta": meta})()
        self.prevented = False
        self.stopped = False

    def prevent_default(self) -> None:
        self.prevented = True

    def stop(self) -> None:
        self.stopped = True


def _minimal_plan(tmp_path: Path) -> Plan:
    header = PrepareHeader(generated_by="cactus-prepare --outSeqFile seq.fa --outDir out", date=datetime.now())
    step = Step(
        raw="python -c \"from pathlib import Path; Path('done.txt').write_text('ok')\"",
        kind="blast",
        out_files=["done.txt"],
        root="Anc0",
    )
    round_entry = Round(
        name="Round 1",
        root="Anc0",
        target_hal="target.hal",
        blast_step=step,
        align_step=step,
    )
    return Plan(
        header=header,
        preprocess=[],
        rounds=[round_entry],
        hal_merges=[],
        out_seq_file=str(tmp_path / "seq.fa"),
        out_dir=str(tmp_path),
    )


def _simple_tree() -> tuple[tree_utils.AlignmentNode, tree_utils.AlignmentNode]:
    root_round = Round(
        name="root",
        root="Anc0",
        target_hal="root.hal",
        blast_step=Step(raw="echo blast", kind="blast", root="Anc0"),
        align_step=Step(raw="echo align", kind="align", root="Anc0"),
    )
    child_round = Round(
        name="child",
        root="Anc1",
        target_hal="child.hal",
        blast_step=Step(raw="echo blast", kind="blast", root="Anc1"),
        align_step=Step(raw="echo align", kind="align", root="Anc1"),
        replace_with_ramax=True,
    )
    root = tree_utils.AlignmentNode(name="Anc0", round=root_round)
    child = tree_utils.AlignmentNode(name="Anc1", round=child_round, parent=root)
    root.children.append(child)
    return root, child


def _large_newick(leaf_count: int) -> str:
    counter = 0

    def build(names: list[str]) -> str:
        nonlocal counter
        if len(names) == 1:
            return names[0]
        midpoint = len(names) // 2
        left = build(names[:midpoint])
        right = build(names[midpoint:])
        name = f"Anc{counter}"
        counter += 1
        return f"({left},{right}){name}"

    return build([f"leaf{i}" for i in range(leaf_count)])


def _large_tree_template(tmp_path: Path, leaf_count: int = 128) -> templates.Template:
    seq = tmp_path / "large.seq"
    lines = [f"{_large_newick(leaf_count)};"]
    lines.extend(f"leaf{i} leaf{i}.fa" for i in range(leaf_count))
    seq.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out_dir = tmp_path / "large-out"
    return templates.Template(
        name="Test large tree",
        spec=str(seq),
        params={
            "out_dir": str(out_dir),
            "out_seq": str(out_dir / "large.txt"),
            "out_hal": str(out_dir / "large.hal"),
            "job_store": str(out_dir / "jobstore"),
        },
        source="test",
    )


def _branching_plan(tmp_path: Path) -> Plan:
    plan = _minimal_plan(tmp_path)
    child_step = Step(
        raw="python -c \"from pathlib import Path; Path('child.txt').write_text('ok')\"",
        kind="blast",
        out_files=["child.txt"],
        root="Anc1",
    )
    child_round = Round(
        name="Round 2",
        root="Anc1",
        target_hal="child.hal",
        blast_step=child_step,
        align_step=child_step,
    )
    seq = tmp_path / "branching.seq"
    seq.write_text(
        "(leaf0,(leaf1,leaf2)Anc1)Anc0;\nleaf0 a.fa\nleaf1 b.fa\nleaf2 c.fa\n",
        encoding="utf-8",
    )
    plan.rounds.append(child_round)
    plan.out_seq_file = str(seq)
    return plan


def test_plan_runner_emits_ui_events(tmp_path: Path):
    plan = _minimal_plan(tmp_path)
    events = []
    runner = PlanRunner(
        plan,
        base_dir=tmp_path,
        mirror_stdout=False,
        run_settings=RunSettings(verbose=False),
        event_sink=events.append,
    )

    runner.run()

    kinds = [event.kind for event in events]
    assert kinds[0] == "plan_started"
    assert "command_started" in kinds
    assert "command_succeeded" in kinds
    assert kinds[-1] == "plan_completed"


def test_tree_browser_scope_and_subtree_toggle():
    root, child = _simple_tree()
    widget = PlanTreeBrowser(root)

    assert widget.current_scope() == "subtree"
    widget.action_toggle_scope()
    assert widget.current_scope() == "node"

    widget._focused_node = root
    widget.action_toggle_scope()
    assert widget.current_scope() == "subtree"
    widget.action_toggle_apply()

    assert root.round is not None
    assert child.round is not None
    assert root.round.replace_with_ramax is True
    assert "--subtree-mode" in root.round.ramax_opts
    assert child.round.replace_with_ramax is False


def test_plan_ui_mounts_native_tree(tmp_path: Path):
    seq = tmp_path / "seq.fa"
    seq.write_text("(leaf1,leaf2)Anc0;\nleaf1 a.fa\nleaf2 b.fa\n", encoding="utf-8")
    plan = _minimal_plan(tmp_path)
    plan.out_seq_file = str(seq)

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            assert app.canvas is not None
            assert app.decision_panel is not None
            assert len(list(app.query("Footer"))) == 0
            assert "Actions" not in str(app.decision_panel.render())
            tree = app.canvas._tree
            assert tree is not None
            assert tree.root.is_expanded
            await pilot.press("space")
            assert plan.rounds[0].replace_with_ramax is True
            assert tree.root.is_expanded
            await pilot.press("b")
            await pilot.press("x")
            assert not tree.root.is_expanded
            await pilot.press("x")
            assert tree.root.is_expanded

    asyncio.run(run_smoke())


def test_tree_keyboard_skips_species_leaf_siblings(tmp_path: Path):
    plan = _branching_plan(tmp_path)

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            assert app.canvas is not None
            root = app.alignment_tree.root
            actionable_child = root.children[1]

            await pilot.press("right")
            assert app.canvas.current_node() is actionable_child

            await pilot.press("h")
            assert app.canvas.current_node() is root
            await pilot.press("j")
            assert app.canvas.current_node() is actionable_child

    asyncio.run(run_smoke())


def test_header_tracks_tree_focus_after_round_edit(tmp_path: Path):
    plan = _branching_plan(tmp_path)

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            assert app.canvas is not None
            assert app.sub_title == "Round 1 (Anc0)"

            await pilot.press("enter")
            await pilot.pause(0.1)
            assert "Round 1" in app.sub_title
            await pilot.press("escape")
            await pilot.pause(0.1)

            await pilot.press("right")
            await pilot.pause(0.1)
            assert app.canvas.current_node() is app.alignment_tree.root.children[1]
            assert app.sub_title == "Round 2 (Anc1)"

    asyncio.run(run_smoke())


def test_tree_mouse_click_only_focuses_node(tmp_path: Path):
    template = _large_tree_template(tmp_path, leaf_count=8)
    plan = _minimal_plan(tmp_path)
    plan.out_seq_file = template.spec
    plan.out_dir = template.params["out_dir"]

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            assert app.canvas is not None
            tree = app.canvas._tree
            assert tree is not None
            child = app.alignment_tree.root.children[0]
            child_line = next(
                index
                for index, line in enumerate(tree._tree_lines)
                if line.path[-1].data is child
            )
            await tree._on_click(_FakeClick({"line": child_line}))
            await pilot.pause(0.1)
            assert app.canvas.current_node() is child
            assert type(app.screen).__name__ == "Screen"

            assert tree.root.is_expanded
            await tree._on_click(_FakeClick({"line": 0, "toggle": True}))
            await pilot.pause(0.1)
            assert app.canvas.current_node() is app.alignment_tree.root
            assert tree.root.is_expanded
            assert type(app.screen).__name__ == "Screen"

    asyncio.run(run_smoke())


def test_run_settings_enter_runs_from_keyboard(tmp_path: Path):
    plan = _minimal_plan(tmp_path)
    results: list[RunSettings | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(plan, RunSettings(), compact=False)
            app.push_screen(screen, callback=results.append)
            await pilot.pause(0.1)
            assert len(list(app.query("Footer"))) == 0
            assert screen._current_field_id() == "run"
            assert screen._content is not None
            assert "1. cactus" not in str(screen._content.render())
            await pilot.press("enter")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert len(results) == 1
    assert isinstance(results[0], RunSettings)


def test_run_settings_help_text_is_visually_secondary(tmp_path: Path):
    plan = _minimal_plan(tmp_path)

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(plan, RunSettings(), compact=False)
            app.push_screen(screen)
            await pilot.pause(0.1)

            rendered = screen._render_summary(screen._current_settings_preview())
            assert "Run plan  [Enter/R]" in rendered.plain
            assert "    - Start with the settings below." in rendered.plain
            assert any(str(span.style) == RUN_HELP_STYLE for span in rendered.spans)

    asyncio.run(run_smoke())


def test_run_settings_keyboard_fields_update_settings(tmp_path: Path):
    plan = _minimal_plan(tmp_path)
    results: list[RunSettings | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(plan, RunSettings(), compact=False)
            app.push_screen(screen, callback=results.append)
            await pilot.pause(0.1)
            assert app.sub_title == "Run settings"

            for _ in range(4):
                await pilot.press("down")
            assert screen._current_field_id() == "threads"

            await pilot.press("2")
            await pilot.press("4")
            assert screen._thread_text == "24"

            await pilot.press("down")
            assert screen._current_field_id() == "verbose"
            await pilot.press("space")
            assert screen._verbose_enabled is True

            await pilot.press("r")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert len(results) == 1
    assert isinstance(results[0], RunSettings)
    assert results[0].thread_count == 24
    assert results[0].verbose is True


def test_run_settings_preserves_resume_from_keyboard(tmp_path: Path):
    plan = _minimal_plan(tmp_path)
    results: list[RunSettings | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(
                plan,
                RunSettings(resume=True),
                compact=False,
                resume_available=True,
            )
            app.push_screen(screen, callback=results.append)
            await pilot.pause(0.1)

            assert screen._resume_enabled is True
            assert screen._current_settings_preview().resume is True
            assert screen._content is not None
            rendered = str(screen._content.render())
            assert "Resume state: available" in rendered
            assert "Resume  [on]" in rendered

            await pilot.press("enter")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert len(results) == 1
    assert isinstance(results[0], RunSettings)
    assert results[0].resume is True


def test_run_settings_can_disable_resume_with_keyboard(tmp_path: Path):
    plan = _minimal_plan(tmp_path)
    results: list[RunSettings | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(
                plan,
                RunSettings(resume=True),
                compact=False,
                resume_available=True,
            )
            app.push_screen(screen, callback=results.append)
            await pilot.pause(0.1)

            for _ in range(10):
                if screen._current_field_id() == "resume":
                    break
                await pilot.press("down")
            assert screen._current_field_id() == "resume"

            await pilot.press("space")
            await pilot.pause(0.1)
            assert screen._resume_enabled is False
            assert screen._current_settings_preview().resume is False

            await pilot.press("r")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert len(results) == 1
    assert isinstance(results[0], RunSettings)
    assert results[0].resume is False


def test_run_settings_command_preview_escape_returns_to_summary(tmp_path: Path):
    plan = _minimal_plan(tmp_path)

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(plan, RunSettings(), compact=False)
            app.push_screen(screen)
            await pilot.pause(0.1)

            await pilot.press("f6")
            await pilot.pause(0.1)
            assert screen._view_mode == "commands"

            await pilot.press("escape")
            await pilot.pause(0.1)
            assert type(app.screen).__name__ == "RunSettingsScreen"
            assert screen._view_mode == "summary"

    asyncio.run(run_smoke())


def test_run_settings_threads_enter_and_space_open_editor(tmp_path: Path):
    plan = _minimal_plan(tmp_path)

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(plan, RunSettings(), compact=False)
            app.push_screen(screen)
            await pilot.pause(0.1)

            for _ in range(4):
                await pilot.press("down")
            assert screen._current_field_id() == "threads"

            await pilot.press("enter")
            await pilot.pause(0.1)
            assert isinstance(app.screen, ThreadCountModal)
            assert app.screen._input is not None
            app.screen._input.value = "12"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert type(app.screen).__name__ == "RunSettingsScreen"
            assert screen._thread_text == "12"

            await pilot.press("space")
            await pilot.pause(0.1)
            assert isinstance(app.screen, ThreadCountModal)
            assert app.screen._input is not None
            app.screen._input.value = "auto"
            await pilot.press("enter")
            await pilot.pause(0.1)
            assert type(app.screen).__name__ == "RunSettingsScreen"
            assert screen._thread_text == ""

    asyncio.run(run_smoke())


def test_run_settings_reuses_command_plan_while_navigating(tmp_path: Path, monkeypatch):
    plan = _minimal_plan(tmp_path)
    calls: list[int | None] = []

    def fake_build_execution_plan(plan_arg, base_dir, thread_count=None):
        calls.append(thread_count)
        return []

    monkeypatch.setattr(ui_module.planner, "build_execution_plan", fake_build_execution_plan)

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = RunSettingsScreen(plan, RunSettings(), compact=False)
            app.push_screen(screen)
            await pilot.pause(0.1)
            assert calls == [None]

            for _ in range(6):
                await pilot.press("down")
                await pilot.pause(0.01)
            assert calls == [None]

            for _ in range(4):
                await pilot.press("down")
            await pilot.press("2")
            await pilot.pause(0.1)
            assert calls == [None, 2]

    asyncio.run(run_smoke())


def test_run_settings_save_commands_is_keyboard_only(tmp_path: Path):
    plan = _minimal_plan(tmp_path)

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("r")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "RunSettingsScreen"
            assert len(list(app.query("Footer"))) == 0
            assert len(list(app.query("Button"))) == 0

            await pilot.press("s")
            await pilot.pause(0.2)
            assert (tmp_path / "ramax_commands.txt").exists()

            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())


def test_run_settings_exposes_command_editing_from_keyboard(tmp_path: Path):
    plan = _minimal_plan(tmp_path)

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("r")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "RunSettingsScreen"
            assert app.screen._current_field_id() == "run"
            assert app.screen._content is not None
            assert "Edit commands" in str(app.screen._content.render())

            await pilot.press("e")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "RoundPickerModal"

            await pilot.press("enter")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "CommandSelectionModal"

            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, CommandEditModal)
            assert app.screen._editor is not None
            app.screen._editor.text = "echo edited"

            await pilot.press("ctrl+s")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "RunSettingsScreen"

            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert plan.rounds[0].blast_step is not None
    assert plan.rounds[0].blast_step.raw == "echo edited"


def test_command_edit_escape_returns_to_command_picker(tmp_path: Path):
    plan = _minimal_plan(tmp_path)
    original = plan.rounds[0].blast_step.raw if plan.rounds[0].blast_step else ""

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("r")
            await pilot.pause(0.2)
            await pilot.press("e")
            await pilot.pause(0.2)
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "CommandSelectionModal"

            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert isinstance(app.screen, CommandEditModal)
            assert app.screen._editor is not None
            app.screen._editor.text = "echo should-not-save"

            await pilot.press("escape")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "CommandSelectionModal"
            assert app.screen._index == 1

            await pilot.press("escape")
            await pilot.pause(0.2)
            assert type(app.screen).__name__ == "RunSettingsScreen"

            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert plan.rounds[0].blast_step is not None
    assert plan.rounds[0].blast_step.raw == original


def test_command_selection_modal_is_keyboard_only():
    targets = [
        CommandTarget("blast", "Blast", "cactus-blast --foo one", "blast"),
        CommandTarget("align", "Align", "cactus-align --bar two", "align"),
    ]
    results: list[CommandTarget | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(CommandSelectionModal(targets), callback=results.append)
            await pilot.pause(0.1)
            assert len(list(app.query("Button"))) == 0
            assert len(list(app.query("ListView"))) == 0

            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert results == [targets[1]]


def test_command_edit_modal_saves_with_keyboard():
    results: list[str | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = CommandEditModal("Edit command", "echo old")
            app.push_screen(screen, callback=results.append)
            await pilot.pause(0.1)
            assert len(list(app.query("Button"))) == 0
            assert screen._editor is not None

            screen._editor.text = "echo new"
            await pilot.press("ctrl+s")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert results == ["echo new"]


def test_round_picker_modal_is_keyboard_only(tmp_path: Path):
    plan = _branching_plan(tmp_path)
    results: list[int | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(RoundPickerModal(plan.rounds), callback=results.append)
            await pilot.pause(0.1)
            assert len(list(app.query("Button"))) == 0
            assert len(list(app.query("ListView"))) == 0

            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert results == [1]


def test_ramax_options_modal_saves_with_keyboard():
    results: list[tuple[list[str], list[str]] | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(110, 36)) as pilot:
            screen = RamaxOptionsModal(["--global-old"], ["--round-old"])
            app.push_screen(screen, callback=results.append)
            await pilot.pause(0.1)
            assert len(list(app.query("Button"))) == 0
            assert screen._global_editor is not None
            assert screen._round_editor is not None

            screen._global_editor.text = "--global-new\n\n"
            screen._round_editor.text = "--round-new\n--flag value"
            await pilot.press("ctrl+s")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert results == [(["--global-new"], ["--round-new", "--flag value"])]


def test_mash_threshold_modal_applies_with_enter():
    results: list[float | None] = []

    async def run_smoke() -> None:
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            screen = MashThresholdModal(0.02)
            app.push_screen(screen, callback=results.append)
            await pilot.pause(0.1)
            assert len(list(app.query("Button"))) == 0
            assert screen._input is not None

            screen._input.value = "0.04"
            await pilot.press("enter")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())

    assert results == [0.04]


def test_plan_ui_handles_large_tree_keyboard_navigation(tmp_path: Path):
    template = _large_tree_template(tmp_path, leaf_count=128)
    plan = _minimal_plan(tmp_path)
    plan.out_seq_file = template.spec
    plan.out_dir = template.params["out_dir"]

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause(0.3)
            assert app.canvas is not None
            assert app.alignment_tree is not None
            tree = app.canvas._tree
            assert tree is not None
            assert tree.root.is_expanded
            assert app.canvas.current_node() is app.alignment_tree.root
            assert tree.cursor_node is not None
            assert tree.cursor_node.data is app.canvas.current_node()
            assert all(
                (not tree_node.allow_expand) or tree_node.is_expanded
                for tree_node in app.canvas._node_to_tree.values()
            )
            await pilot.press("right")
            assert app.canvas.current_node() is app.alignment_tree.root.children[0]
            assert tree.cursor_node is not None
            assert tree.cursor_node.data is app.canvas.current_node()
            await pilot.press("h")
            assert app.canvas.current_node() is app.alignment_tree.root
            await pilot.press("x")
            assert not tree.root.is_expanded
            await pilot.press("x")
            assert tree.root.is_expanded
            root_name = app.canvas.current_node().name
            await pilot.press("j")
            assert app.canvas.current_node().name != root_name
            assert tree.cursor_node is not None
            assert tree.cursor_node.data is app.canvas.current_node()
            assert app.status_bar is not None
            assert "E edit" in str(app.status_bar.render())
            assert "I details" in str(app.status_bar.render())
            assert "/ search" in str(app.status_bar.render())
            assert "T mash" in str(app.status_bar.render())
            assert "X fold/open" in str(app.status_bar.render())
            assert "Move:" not in str(app.status_bar.render())
            assert "Z expand" not in str(app.status_bar.render())
            await pilot.press("k")
            assert app.canvas.current_node().name == root_name
            await pilot.press("h")
            await pilot.press("l")
            await pilot.press("b")
            assert app.canvas.current_scope() == "node"
            assert app.canvas.current_node().name

    asyncio.run(run_smoke())


def test_plan_ui_resume_entry_opens_run_settings(tmp_path: Path):
    plan = _minimal_plan(tmp_path)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "run_state.json").write_text("{}", encoding="utf-8")

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path, run_settings=RunSettings(resume=True))
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.3)

            assert app.resume_available is True
            assert type(app.screen).__name__ == "RunSettingsScreen"
            assert app.screen._resume_enabled is True
            assert app.screen._current_settings_preview().resume is True

            await pilot.press("escape")
            await pilot.pause(0.1)

    asyncio.run(run_smoke())


def test_plan_ui_execution_returns_completed_action(tmp_path: Path):
    plan = _minimal_plan(tmp_path)

    async def run_smoke() -> None:
        app = PlanUIApp(plan, base_dir=tmp_path)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause(0.2)
            await pilot.press("r")
            await pilot.pause(0.1)
            await pilot.press("enter")
            for _ in range(30):
                await pilot.pause(0.2)
                if type(app.screen).__name__ == "ExecutionScreen" and getattr(app.screen, "_done", False):
                    break
            assert type(app.screen).__name__ == "ExecutionScreen"
            assert getattr(app.screen, "_done", False) is True
            assert len(list(app.query("Footer"))) == 0
            assert len(list(app.query("ProgressBar"))) == 0
            assert app.screen._progress is not None
            assert "Progress:" in str(app.screen._progress.render())
            await pilot.press("q")
            await pilot.pause(0.2)
        result = app._return_value
        assert isinstance(result, UIResult)
        assert result.action == "run_completed"

    asyncio.run(run_smoke())
