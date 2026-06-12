"""Textual-based interactive UI for configuring CAX plans."""
from __future__ import annotations

import queue
import shlex
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import psutil
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.worker import Worker, WorkerState
from textual.widgets import Header, Input, RichLog, Static, TextArea
from textual.widgets import Tree as TextualTree
from textual.widgets._tree import TreeNode

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import mash_auto as mash_auto_module, planner, seq_cache, tree_utils
from .models import Plan, Round, RunSettings, Step
from .planner import PlannedCommand
from .runner import PlanRunner, RunnerEvent


SUBTREE_MODE_FLAG = "--subtree-mode"
TREE_STATUS_HELP = "E edit · R run · I details · / search · T mash · Space RaMAx · B scope · X fold/open · Q quit"
RUN_ITEM_STYLE = "white"
RUN_VALUE_STYLE = "#94a3b8"
RUN_HELP_STYLE = "italic dim #64748b"
RUN_SELECTED_STYLE = "bold white on #0e7490"


def _is_subtree_mode_round(round_entry: Round) -> bool:
    return round_entry.replace_with_ramax and SUBTREE_MODE_FLAG in round_entry.ramax_opts


def _is_effective_ramax_node(node: tree_utils.AlignmentNode) -> bool:
    """判断节点在执行层面是否等价于 RaMAx。

    说明：
    - `replace_with_ramax=True` 的 round 当然是 RaMAx。
    - 若任一祖先 round 处于 Subtree Mode（`--subtree-mode`），其子树内的 round 虽然会被标记成 Cactus
      以避免混合状态，但执行时会被祖先 RaMAx 吸收，因此 UI 需要按“有效模式”展示为 RaMAx。
    """

    if not node.round:
        return False
    if node.round.replace_with_ramax:
        return True
    current = getattr(node, "parent", None)
    while current:
        if current.round and _is_subtree_mode_round(current.round):
            return True
        current = getattr(current, "parent", None)
    return False


def _node_display_name(node: tree_utils.AlignmentNode) -> str:
    if node.round:
        return node.round.root
    if node.name:
        return node.name
    return "Root" if getattr(node, "parent", None) is None else "clade"


def _node_search_text(node: tree_utils.AlignmentNode) -> str:
    parts = [node.name]
    if node.round:
        parts.extend([node.round.root, node.round.name])
    return " ".join(part for part in parts if part).lower()


@dataclass
class UIResult:
    plan: Plan
    action: str
    payload: Optional[Path] = None
    run_settings: RunSettings | None = None


@dataclass
class CommandTarget:
    """Represents an editable command associated with a round."""

    key: str
    label: str
    command: str
    kind: str
    step: Step | None = None
    index: int | None = None


def plan_overview(plan: Plan, run_settings: Optional[RunSettings] = None, compact: bool = False) -> Panel:
    """Return a Rich Panel that summarizes the plan."""

    table = Table(
        title="Cactus → RaMAx Plan",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    if compact:
        table.add_column("Round", overflow="ellipsis", no_wrap=True, ratio=2)
        table.add_column("Root", overflow="ellipsis", no_wrap=True, ratio=2)
        table.add_column("Target HAL", overflow="ellipsis", no_wrap=True, ratio=3)
        table.add_column("RaMAx?", overflow="ellipsis", no_wrap=True, ratio=1)
    else:
        table.add_column("Round", overflow="fold")
        table.add_column("Root", overflow="fold")
        table.add_column("Target HAL", overflow="fold")
        table.add_column("RaMAx?", overflow="fold")
        table.add_column("Workdir", overflow="fold")

    for round_entry in plan.rounds:
        row = [
            round_entry.name,
            round_entry.root,
            round_entry.target_hal,
            "yes" if round_entry.replace_with_ramax else "no",
        ]
        if not compact:
            row.append(round_entry.workdir or "")
        table.add_row(*row)

    settings = run_settings or RunSettings()
    thread_label = (
        "auto (command defaults)"
        if settings.thread_count is None
        else f"{settings.thread_count} threads (--maxCores/--threads)"
    )
    footer_text = f"Verbose logging: {'on' if settings.verbose else 'off'} | Thread target: {thread_label}"
    footer = Text(footer_text, style="dim")
    content = Group(table, footer)
    return Panel(content, border_style="magenta", expand=True)


def environment_summary_card(environment: dict[str, Optional[str]], resources: dict[str, str]) -> Panel:
    """Build an environment summary card that adapts to the UI width."""

    def oneline(value: Optional[str]) -> str:
        if not value:
            return "Not detected"
        lines = value.splitlines()
        if not lines:
            return value
        return lines[0] if len(lines) == 1 else f"{lines[0]} (+{len(lines)-1} more)"

    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(ratio=1)

    def entry(name: str, value: Optional[str]) -> Text:
        text = Text()
        text.append(f"{name}: ", style="bold cyan")
        text.append(oneline(value))
        return text

    table.add_row(entry("RaMAx path", environment.get("ramax_path")))
    table.add_row(entry("RaMAx version", environment.get("ramax_version")))
    table.add_row(entry("Mash path", environment.get("mash_path")))
    table.add_row(entry("Mash version", environment.get("mash_version")))
    table.add_row(entry("cactus path", environment.get("cactus_path")))
    table.add_row(entry("cactus version", environment.get("cactus_version")))
    table.add_row(entry("GPU", environment.get("gpu")))
    table.add_row(entry("CPU cores", resources.get("cpu_count")))
    table.add_row(entry("Memory (GB)", resources.get("memory_gb")))
    table.add_row(entry("Disk free (GB)", resources.get("disk_free_gb")))

    panel = Panel(table, title="Environment summary", border_style="cyan", expand=True)
    return panel


def render_run_script(plan: Plan, commands: Iterable[PlannedCommand]) -> str:
    """Generate a bash script for the execution plan."""

    lines = ["#!/usr/bin/env bash", "set -euo pipefail", "", "# Generated from cactus-prepare plan"]
    for command in commands:
        lines.append(f"# {command.display_name}")
        lines.append(shlex.join(command.command))
        lines.append("")
    script = "\n".join(lines).rstrip() + "\n"
    return script




class CommandSelectionModal(ModalScreen[CommandTarget | None]):
    """Keyboard-first picker for editable commands on a round."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Edit"),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("j", "cursor_down", show=False),
    ]

    CSS = """
    CommandSelectionModal {
        align: center middle;
    }
    #picker-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 80;
        border: round $accent;
        background: $panel;
    }
    #picker-title {
        padding-bottom: 1;
    }
    #picker-list {
        height: auto;
        max-height: 20;
        overflow-y: auto;
    }
    #picker-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, targets: list[CommandTarget], initial_index: int = 0):
        super().__init__()
        self.targets = targets
        self._index = max(0, min(initial_index, len(targets) - 1)) if targets else 0
        self._list: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="picker-dialog"):
            yield Static("Choose a command to edit", id="picker-title")
            picker_list = Static(id="picker-list")
            self._list = picker_list
            yield picker_list
            yield Static("Up/Down choose | Enter edit | Esc back", id="picker-hint")

    def on_mount(self) -> None:
        self._refresh()

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(+1)

    def action_confirm(self) -> None:
        if 0 <= self._index < len(self.targets):
            self.dismiss(self.targets[self._index])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _move(self, delta: int) -> None:
        if not self.targets:
            return
        self._index = (self._index + delta) % len(self.targets)
        self._refresh()

    def _refresh(self) -> None:
        if not self._list:
            return
        text = Text()
        if not self.targets:
            text.append("No editable commands for this round.", style="dim")
            self._list.update(text)
            return
        for index, target in enumerate(self.targets):
            selected = index == self._index
            prefix = "> " if selected else "  "
            style = "bold white on #0e7490" if selected else "white"
            text.append(prefix, style=style)
            text.append(target.label, style=style)
            text.append("\n")
            text.append("  ")
            text.append(self._shorten(target.command), style="dim")
            if index < len(self.targets) - 1:
                text.append("\n")
        self._list.update(text)

    def _shorten(self, command: str) -> str:
        command = " ".join(command.split())
        width = max(40, min(110, self.size.width - 12))
        if len(command) <= width:
            return command
        return command[: width - 1] + "…"


class CommandEditModal(ModalScreen[str | None]):
    """Keyboard-first modal for editing a command string."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    CSS = """
    CommandEditModal {
        align: center middle;
    }
    #editor-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 90;
        border: round $accent;
        background: $panel;
    }
    #editor-title {
        padding-bottom: 1;
    }
    #editor-command {
        margin-bottom: 1;
    }
    #editor-status {
        color: $error;
    }
    #editor-hint {
        color: $text-muted;
        padding-top: 1;
    }
    """

    def __init__(self, title: str, initial_command: str):
        super().__init__()
        self.title = title
        self.initial_command = initial_command
        self._editor: TextArea | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="editor-dialog"):
            yield Static(self.title, id="editor-title")
            editor = TextArea(id="editor-command")
            editor.text = self.initial_command
            self._editor = editor
            yield editor
            status = Static("", id="editor-status")
            self._status = status
            yield status
            yield Static("Ctrl+S save | Esc back", id="editor-hint")

    def on_mount(self) -> None:
        if self._editor:
            self._editor.focus()

    def action_save(self) -> None:
        if not self._editor:
            self.dismiss(None)
            return
        value = self._editor.text.strip()
        if not value:
            if self._status:
                self._status.update("Command cannot be empty")
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class InfoModal(ModalScreen[None]):
    """Read-only modal for displaying multi-line text."""

    BINDINGS = [Binding("escape", "dismiss", "Close"), Binding("enter", "dismiss", show=False)]

    CSS = """
    InfoModal {
        align: center middle;
    }
    #info-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 90;
        height: 80%;
        border: round $accent;
        background: $panel;
        layout: vertical;
    }
    #info-title {
        padding-bottom: 1;
    }
    #info-body {
        height: 1fr;
        overflow-y: auto;
    }
    #info-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, title: str, body: str):
        super().__init__()
        self.title = title
        self.body = body or "(empty)"

    def compose(self) -> ComposeResult:
        with Container(id="info-dialog"):
            yield Static(self.title, id="info-title")
            yield Static(self.body, id="info-body")
            yield Static("Enter / Esc to close", id="info-hint")

    def action_dismiss(self) -> None:
        self.dismiss(None)


class SearchModal(ModalScreen[str | None]):
    """Single-line search input modal."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    SearchModal {
        align: center middle;
    }
    #search-dialog {
        padding: 1 2;
        min-width: 40;
        border: round $accent;
        background: $panel;
    }
    #search-title {
        padding-bottom: 1;
    }
    #search-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, initial: str = ""):
        super().__init__()
        self.initial = initial
        self._input: Input | None = None

    def compose(self) -> ComposeResult:
        with Container(id="search-dialog"):
            yield Static("Enter a node keyword", id="search-title")
            self._input = Input(value=self.initial, placeholder="e.g. human / panTro")
            yield self._input
            yield Static("Enter to confirm, Esc to cancel", id="search-hint")

    def on_mount(self) -> None:
        if self._input:
            self.set_focus(self._input)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


THREAD_AUTO = "auto"


class ThreadCountModal(ModalScreen[int | str | None]):
    """Keyboard-first thread-count editor."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    ThreadCountModal {
        align: center middle;
    }
    #threads-dialog {
        padding: 1 2;
        min-width: 50;
        border: round $accent;
        background: $panel;
        layout: vertical;
    }
    #threads-title {
        padding-bottom: 1;
    }
    #threads-input {
        width: 100%;
    }
    #threads-hint {
        padding-top: 1;
        color: $text-muted;
    }
    #threads-status {
        padding-top: 1;
        color: $error;
    }
    """

    def __init__(self, current: Optional[int]):
        super().__init__()
        self.current = current
        self._input: Input | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="threads-dialog"):
            yield Static("Thread count", id="threads-title")
            input_widget = Input(
                value="" if self.current is None else str(self.current),
                placeholder="auto or a positive integer",
                id="threads-input",
            )
            self._input = input_widget
            yield input_widget
            yield Static("Enter apply | empty/auto for auto | Esc back", id="threads-hint")
            status = Static("", id="threads-status")
            self._status = status
            yield status

    def on_mount(self) -> None:
        if self._input:
            self.set_focus(self._input)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "threads-input":
            self._apply(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _apply(self, raw: str) -> None:
        text = raw.strip().lower()
        if not text or text in {"a", "auto"}:
            self.dismiss(THREAD_AUTO)
            return
        try:
            value = int(text)
        except ValueError:
            self._update_status("Thread count must be a positive integer, or auto.")
            return
        if value <= 0:
            self._update_status("Thread count must be at least 1.")
            return
        self.dismiss(value)

    def _update_status(self, message: str | None) -> None:
        if self._status:
            self._status.update(message or "")


class MashThresholdModal(ModalScreen[float | None]):
    """Modal dialog to adjust Mash distance threshold and reapply auto-selection."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    MashThresholdModal {
        align: center middle;
    }
    #mash-dialog {
        padding: 1 2;
        min-width: 50;
        border: round $accent;
        background: $panel;
        layout: vertical;
    }
    #mash-title {
        padding-bottom: 1;
    }
    #mash-threshold {
        width: 100%;
    }
    #mash-hint {
        padding-top: 1;
        color: $text-muted;
    }
    #mash-status {
        padding-top: 1;
        color: $error;
    }
    """

    def __init__(self, initial: float):
        super().__init__()
        self.initial = initial
        self._input: Input | None = None
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="mash-dialog"):
            yield Static("Mash distance threshold (distance ≤ threshold enables RaMAx)", id="mash-title")
            threshold_input = Input(
                value=f"{self.initial:.4f}",
                placeholder="0.02",
                id="mash-threshold",
            )
            self._input = threshold_input
            yield threshold_input
            yield Static("Example: 0.04 | Enter apply | Esc cancel", id="mash-hint")
            status = Static("", id="mash-status")
            self._status = status
            yield status

    def on_mount(self) -> None:
        if self._input:
            self.set_focus(self._input)

    def _update_status(self, message: str | None) -> None:
        if self._status is not None:
            self._status.update(message or "")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _apply(self) -> None:
        if not self._input:
            self.dismiss(None)
            return
        raw = self._input.value.strip()
        if not raw:
            value = self.initial
        else:
            try:
                value = float(raw)
            except ValueError:
                self._update_status("Threshold must be a number (for example 0.02).")
                return
        if value < 0.0 or value > 1.0:
            self._update_status("Threshold must be between 0.0 and 1.0.")
            return
        self._update_status("")
        self.dismiss(value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "mash-threshold":
            self._apply()


@dataclass
class _DetailCallback:
    """Wrapper to forward focused-node updates to the host app."""

    handler: Optional[Callable[[tree_utils.AlignmentNode, Optional[str]], None]] = None

    def __call__(self, node: tree_utils.AlignmentNode, status: Optional[str] = None) -> None:
        if self.handler:
            self.handler(node, status=status)


class RoundPickerModal(ModalScreen[int | None]):
    """Keyboard-first picker for choosing a round."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Select"),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("j", "cursor_down", show=False),
    ]

    CSS = """
    RoundPickerModal {
        align: center middle;
    }
    #round-picker {
        padding: 1 2;
        width: 70%;
        max-width: 80;
        border: round $accent;
        background: $panel;
    }
    #round-picker-list {
        height: auto;
        max-height: 24;
        overflow-y: auto;
    }
    #round-picker-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, rounds: list[Round]):
        super().__init__()
        self.rounds = rounds
        self._index = 0
        self._list: Static | None = None

    def compose(self) -> ComposeResult:
        with Container(id="round-picker"):
            yield Static("Choose a round", id="round-picker-title")
            round_list = Static(id="round-picker-list")
            self._list = round_list
            yield round_list
            yield Static("Up/Down choose | Enter edit | Esc back", id="round-picker-hint")

    def on_mount(self) -> None:
        self._refresh()

    def action_cursor_up(self) -> None:
        self._move(-1)

    def action_cursor_down(self) -> None:
        self._move(+1)

    def action_confirm(self) -> None:
        self.dismiss(self._index if self.rounds else None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _move(self, delta: int) -> None:
        if not self.rounds:
            return
        self._index = (self._index + delta) % len(self.rounds)
        self._refresh()

    def _refresh(self) -> None:
        if not self._list:
            return
        text = Text()
        if not self.rounds:
            text.append("No rounds found.", style="dim")
            self._list.update(text)
            return
        width = max(40, min(100, self.size.width - 12))
        for index, round_entry in enumerate(self.rounds):
            selected = index == self._index
            prefix = "> " if selected else "  "
            style = "bold white on #0e7490" if selected else "white"
            text.append(prefix, style=style)
            text.append(f"{round_entry.name} ({round_entry.root})", style=style)
            text.append("\n")
            target = round_entry.target_hal
            if len(target) > width:
                target = target[: width - 1] + "…"
            text.append(f"  {target}", style="dim")
            if index < len(self.rounds) - 1:
                text.append("\n")
        self._list.update(text)


class _PlanTextualTree(TextualTree[tree_utils.AlignmentNode]):
    """Textual tree with CAX-owned selection and folding keys."""

    BINDINGS = [
        Binding("enter", "edit_round", show=False, priority=True),
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("shift+left", "cursor_parent", show=False),
        Binding("shift+right", "cursor_parent_next_sibling", show=False),
        Binding("shift+up", "cursor_previous_sibling", show=False),
        Binding("shift+down", "cursor_next_sibling", show=False),
    ]

    async def _on_click(self, event: events.Click) -> None:
        line = event.style.meta.get("line")
        if line is None:
            return
        event.prevent_default()
        event.stop()
        self.cursor_line = line
        tree_node = self.get_node_at_line(line)
        parent = self.parent
        if tree_node is not None and parent and hasattr(parent, "_sync_focused_tree_node"):
            parent._sync_focused_tree_node(tree_node)

    def action_toggle_node(self) -> None:
        parent = self.parent
        if parent and hasattr(parent, "action_toggle_apply"):
            parent.action_toggle_apply()

    def action_select_cursor(self) -> None:
        parent = self.parent
        if parent and hasattr(parent, "_sync_focused_tree_node") and self.cursor_node:
            parent._sync_focused_tree_node(self.cursor_node)

    def action_edit_round(self) -> None:
        parent = self.parent
        if parent and hasattr(parent, "action_edit_round"):
            parent.action_edit_round()


class PlanTreeBrowser(Static):
    """Scrollable and collapsible tree browser backed by Textual's native Tree."""

    BINDINGS = [
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("left", "cursor_left", show=False),
        Binding("right", "cursor_right", show=False),
        Binding("b", "toggle_scope", "Scope"),
        Binding("space", "toggle_apply", "Toggle RaMAx"),
        Binding("enter", "edit_round", "Edit"),
        Binding("/", "open_search", "Search"),
        Binding("n", "search_next", "Next match"),
        Binding("shift+n", "search_prev", "Prev match"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("h", "cursor_left", "Parent", show=False),
        Binding("l", "cursor_right", "Open", show=False),
        Binding("x", "toggle_fold", "Fold/Open"),
    ]

    DEFAULT_CSS = """
    PlanTreeBrowser {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        min-height: 0;
    }
    PlanTreeBrowser > Tree {
        height: 1fr;
        width: 1fr;
        min-height: 0;
        overflow: auto;
    }
    """

    def __init__(self, root: tree_utils.AlignmentNode, *, id: str = "plan-tree-browser"):
        super().__init__("", id=id)
        self.root_node = root
        self.scope = "subtree"
        self._tree: TextualTree[tree_utils.AlignmentNode] | None = None
        self._node_to_tree: dict[tree_utils.AlignmentNode, TreeNode[tree_utils.AlignmentNode]] = {}
        self._focused_node = root
        self._search_term: str | None = None
        self._hits: list[tree_utils.AlignmentNode] = []
        self._hit_index = 0
        self._detail_callback = _DetailCallback()

    def compose(self) -> ComposeResult:
        tree = _PlanTextualTree(self._label_for(self.root_node), data=self.root_node, id="plan-tree")
        tree.show_root = True
        tree.auto_expand = False
        tree.root.expand()
        self._tree = tree
        self._node_to_tree[self.root_node] = tree.root
        self._populate(tree.root, self.root_node)
        yield tree

    def on_mount(self) -> None:
        if self._tree:
            self._tree.focus()
            self.call_after_refresh(self._initialize_tree_view)

    def _initialize_tree_view(self, *, preserve_focus: bool = False) -> None:
        if not self._tree:
            return
        target = self._focused_node if preserve_focus else self.root_node
        self._expand_every_tree_node()
        self._focus_node(target)
        self._notify("Tree ready. Expanded all nodes. Scope: Subtree.")

    def _expand_every_tree_node(self) -> None:
        if self._tree:
            self._tree.root.expand_all()

    def set_detail_callback(
        self,
        callback: Optional[Callable[[tree_utils.AlignmentNode, Optional[str]], None]],
    ) -> None:
        self._detail_callback.handler = callback

    def current_node(self) -> tree_utils.AlignmentNode:
        return self._focused_node

    def current_scope(self) -> str:
        return self.scope

    def rebuild_labels(self) -> None:
        for node, tree_node in self._node_to_tree.items():
            tree_node.set_label(self._label_for(node))
        if self._tree:
            self._tree.refresh()

    def _populate(
        self,
        tree_node: TreeNode[tree_utils.AlignmentNode],
        alignment_node: tree_utils.AlignmentNode,
    ) -> None:
        for child in alignment_node.children:
            allow_expand = bool(child.children)
            child_tree = tree_node.add(
                self._label_for(child),
                data=child,
                expand=True,
                allow_expand=allow_expand,
            )
            self._node_to_tree[child] = child_tree
            self._populate(child_tree, child)

    def _label_for(self, node: tree_utils.AlignmentNode) -> Text:
        kind = self._node_kind(node)
        marker = {"ramax": "R", "cactus": "C", "covered": "R*", "clade": "◇", "leaf": "L"}[kind]
        style = {
            "ramax": "bold #f59e0b",
            "covered": "bold #fbbf24",
            "cactus": "bold #22d3ee",
            "clade": "dim #7dd3fc",
            "leaf": "#86efac",
        }[kind]
        label = Text()
        label.append(f"{marker} ", style=style)
        label.append(_node_display_name(node), style=style)
        if node.round and node.round.mash_distance is not None:
            label.append(f" mash {node.round.mash_distance:.4f}", style="dim #94a3b8")
        if node.round and _is_subtree_mode_round(node.round):
            label.append(" subtree", style="dim #fbbf24")
        return label

    def _node_kind(self, node: tree_utils.AlignmentNode) -> str:
        if node.round:
            if node.round.replace_with_ramax:
                return "ramax"
            if _is_effective_ramax_node(node):
                return "covered"
            return "cactus"
        if node.children:
            return "clade"
        return "leaf"

    def _is_actionable_node(self, node: tree_utils.AlignmentNode) -> bool:
        return bool(node.round or node.children)

    def _visible_nodes(self) -> list[tree_utils.AlignmentNode]:
        if not self._tree:
            return []
        visible: list[tree_utils.AlignmentNode] = []

        def walk(tree_node: TreeNode[tree_utils.AlignmentNode]) -> None:
            node = tree_node.data
            if isinstance(node, tree_utils.AlignmentNode):
                visible.append(node)
            if not tree_node.is_expanded:
                return
            for child in tree_node.children:
                walk(child)

        walk(self._tree.root)
        return visible

    def _move_focus(self, delta: int) -> None:
        visible = self._visible_nodes()
        if not visible:
            return
        try:
            start = visible.index(self._focused_node)
        except ValueError:
            start = 0
        index = start + delta
        while 0 <= index < len(visible):
            node = visible[index]
            if self._is_actionable_node(node):
                self._focus_node(node)
                self._notify()
                return
            index += delta

    def _sync_focused_tree_node(self, tree_node: TreeNode[tree_utils.AlignmentNode]) -> None:
        node = tree_node.data
        if isinstance(node, tree_utils.AlignmentNode):
            self._focused_node = node
            if self._tree:
                self._tree.scroll_to_node(tree_node, animate=False)
            self._notify()

    def on_tree_node_highlighted(self, event: TextualTree.NodeHighlighted) -> None:
        node = event.node.data
        if isinstance(node, tree_utils.AlignmentNode):
            self._sync_focused_tree_node(event.node)

    def on_tree_node_selected(self, event: TextualTree.NodeSelected) -> None:
        node = event.node.data
        if isinstance(node, tree_utils.AlignmentNode):
            self._sync_focused_tree_node(event.node)

    def action_toggle_scope(self) -> None:
        self.scope = "node" if self.scope == "subtree" else "subtree"
        self._notify(f"Scope switched to: {'Single node' if self.scope == 'node' else 'Subtree'}")

    def action_toggle_apply(self) -> None:
        if self.scope == "node":
            self._toggle_single()
        else:
            self._toggle_subtree()

    def _toggle_single(self) -> None:
        node = self._focused_node
        if not node.round:
            self._notify("No round on this node; nothing to toggle.")
            return
        if self._maybe_revert_subtree_ancestor(node):
            return
        if SUBTREE_MODE_FLAG in node.round.ramax_opts:
            node.round.ramax_opts.remove(SUBTREE_MODE_FLAG)
        node.round.replace_with_ramax = not node.round.replace_with_ramax
        self.rebuild_labels()
        state = "RaMAx" if node.round.replace_with_ramax else "Cactus"
        self._notify(f"Current node switched to {state}.")

    def _toggle_subtree(self) -> None:
        node = self._focused_node
        if not node.round:
            self._notify("No round on this node to apply subtree mode.")
            return
        active = _is_subtree_mode_round(node.round)
        if active:
            node.round.replace_with_ramax = False
            if SUBTREE_MODE_FLAG in node.round.ramax_opts:
                node.round.ramax_opts.remove(SUBTREE_MODE_FLAG)
            message = "Disabled subtree RaMAx."
        else:
            node.round.replace_with_ramax = True
            if SUBTREE_MODE_FLAG not in node.round.ramax_opts:
                node.round.ramax_opts.append(SUBTREE_MODE_FLAG)
            disabled = 0
            for child in self._collect_round_nodes(node):
                if child is node:
                    continue
                if child.round and child.round.replace_with_ramax:
                    child.round.replace_with_ramax = False
                    if SUBTREE_MODE_FLAG in child.round.ramax_opts:
                        child.round.ramax_opts.remove(SUBTREE_MODE_FLAG)
                    disabled += 1
            message = f"Enabled subtree RaMAx. Overridden {disabled} descendant(s)."
        self.rebuild_labels()
        self._notify(message)

    def _maybe_revert_subtree_ancestor(self, node: tree_utils.AlignmentNode) -> bool:
        current = getattr(node, "parent", None)
        while current:
            if current.round and _is_subtree_mode_round(current.round):
                current.round.replace_with_ramax = False
                current.round.ramax_opts.remove(SUBTREE_MODE_FLAG)
                self.rebuild_labels()
                self._notify(
                    f"Ancestor subtree mode on '{_node_display_name(current)}' was disabled before node-level edit."
                )
                return True
            current = getattr(current, "parent", None)
        return False

    def _collect_round_nodes(self, node: tree_utils.AlignmentNode) -> list[tree_utils.AlignmentNode]:
        return [candidate for candidate in node.walk() if candidate.round]

    def action_open_search(self) -> None:
        self.app.push_screen(SearchModal(self._search_term or ""), self._apply_search_term)

    def _apply_search_term(self, term: str | None) -> None:
        if term is None:
            return
        cleaned = term.strip().lower()
        if not cleaned:
            self._search_term = None
            self._hits = []
            self._notify("Search cleared.")
            return
        self._search_term = cleaned
        self._hits = [node for node in self.root_node.walk() if cleaned in _node_search_text(node)]
        self._hit_index = 0
        if not self._hits:
            self._notify("No matching nodes found.")
            return
        self._focus_node(self._hits[0])
        self._notify(f"Found {len(self._hits)} match(es).")

    def action_search_next(self) -> None:
        self._jump_hit(+1)

    def action_search_prev(self) -> None:
        self._jump_hit(-1)

    def _jump_hit(self, delta: int) -> None:
        if not self._hits:
            return
        self._hit_index = (self._hit_index + delta) % len(self._hits)
        self._focus_node(self._hits[self._hit_index])
        self._notify(f"Match {self._hit_index + 1}/{len(self._hits)}.")

    def _focus_node(self, node: tree_utils.AlignmentNode) -> None:
        self._focused_node = node
        tree_node = self._node_to_tree.get(node)
        if not self._tree or not tree_node:
            return
        current = tree_node.parent
        while current:
            current.expand()
            current = current.parent
        self._tree.move_cursor(tree_node, animate=False)
        self._tree.scroll_to_node(tree_node, animate=False)

    def _sync_focused_from_tree_cursor(self) -> None:
        if not self._tree or not self._tree.cursor_node:
            return
        node = self._tree.cursor_node.data
        if isinstance(node, tree_utils.AlignmentNode):
            self._focused_node = node
            self._notify()

    def action_toggle_fold(self) -> None:
        tree_node = self._node_to_tree.get(self._focused_node)
        if not tree_node or not tree_node.allow_expand:
            self._notify("Current node has no child branch to fold.")
            return
        if tree_node.is_expanded:
            tree_node.collapse()
            self._notify("Folded current branch.")
        else:
            tree_node.expand()
            self._notify("Opened current branch.")

    def action_cursor_down(self) -> None:
        self._move_focus(+1)

    def action_cursor_up(self) -> None:
        self._move_focus(-1)

    def action_cursor_left(self) -> None:
        if not self._tree:
            return
        self._tree.action_cursor_parent()
        self._sync_focused_from_tree_cursor()

    def action_cursor_right(self) -> None:
        if not self._tree:
            return
        tree_node = self._node_to_tree.get(self._focused_node)
        if tree_node and tree_node.allow_expand and not tree_node.is_expanded:
            tree_node.expand()
            self._notify("Expanded current subtree.")
            return
        if tree_node and tree_node.children:
            child = next(
                (
                    candidate
                    for candidate in tree_node.children
                    if isinstance(candidate.data, tree_utils.AlignmentNode)
                    and self._is_actionable_node(candidate.data)
                ),
                None,
            )
            if child and isinstance(child.data, tree_utils.AlignmentNode):
                self._focus_node(child.data)
                self._notify()

    def action_edit_round(self) -> None:
        self.app.action_edit_round()

    def _notify(self, status: Optional[str] = None) -> None:
        self._detail_callback(self._focused_node, status=status)


class DecisionPanel(Static):
    """Right-side decision panel for the focused tree node."""

    DEFAULT_CSS = """
    DecisionPanel {
        height: 1fr;
        width: 1fr;
        min-height: 0;
        overflow-y: auto;
        padding: 1 2;
        border-left: solid #30363d;
    }
    """

    def update_node(
        self,
        node: tree_utils.AlignmentNode,
        *,
        scope: str,
        run_settings: RunSettings,
        status: str | None = None,
    ) -> None:
        self.update(self._render_decision(node, scope=scope, run_settings=run_settings, status=status))

    def _render_decision(
        self,
        node: tree_utils.AlignmentNode,
        *,
        scope: str,
        run_settings: RunSettings,
        status: str | None,
    ) -> RenderableType:
        header = Text("Decision", style="bold cyan")
        title = Text.assemble(("Node: ", "dim"), (_node_display_name(node), "bold white"))
        scope_text = "Single node" if scope == "node" else "Subtree"
        body = Table.grid(expand=True, padding=(0, 1))
        body.add_column(ratio=1)
        body.add_column(ratio=2)
        body.add_row("Scope", Text(scope_text, style="bold yellow" if scope == "subtree" else "bold cyan"))
        if node.round:
            mode = "RaMAx" if node.round.replace_with_ramax else "Cactus"
            if _is_effective_ramax_node(node) and not node.round.replace_with_ramax:
                mode = "RaMAx (covered by ancestor subtree)"
            body.add_row("Decision", Text(mode, style="bold green" if "RaMAx" in mode else "bold cyan"))
            body.add_row("Round", node.round.name)
            if node.round.mash_distance is not None:
                mash = f"{node.round.mash_distance:.4f}"
                if node.round.mash_source and node.round.mash_source != node.round.root:
                    mash += f" @ {node.round.mash_source}"
                body.add_row("Mash", mash)
                body.add_row("Threshold", f"{run_settings.mash_distance_threshold:.4f}")
            else:
                body.add_row("Mash", "(not computed)")
            subtree_rounds = list(node.iter_rounds())
        else:
            body.add_row("Decision", "No round on this node")
            subtree_rounds = list(node.iter_rounds())
        if subtree_rounds:
            subtree_nodes = [candidate for candidate in node.walk() if candidate.round]
            effective = sum(1 for candidate in subtree_nodes if _is_effective_ramax_node(candidate))
            body.add_row("Rounds", f"{effective}/{len(subtree_rounds)} RaMAx")
        parts: list[RenderableType] = [header, title, Text(""), body]
        if status:
            parts.insert(2, Text(status, style="green"))
        return Group(*parts)


class RunSettingsScreen(Screen[RunSettings | None]):
    """Keyboard-first confirmation screen for starting execution."""

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("enter", "activate_selected", "Select"),
        Binding("r", "save", "Run"),
        Binding("e", "edit_commands", "Edit commands"),
        Binding("s", "save_commands", "Save commands"),
        Binding("v", "toggle_verbose", "Toggle verbose"),
        Binding("space", "toggle_selected", "Toggle field"),
        Binding("up", "field_up", show=False),
        Binding("down", "field_down", show=False),
        Binding("k", "field_up", show=False),
        Binding("j", "field_down", show=False),
        Binding("left", "decrement_threads", show=False),
        Binding("right", "increment_threads", show=False),
        Binding("f6", "toggle_view", "Commands"),
    ]

    CSS = """
    #run-root {
        padding: 1 2;
        height: 1fr;
        width: 100%;
        layout: vertical;
        min-height: 0;
    }
    #run-scroll {
        height: 1fr;
        min-height: 0;
        overflow-y: auto;
    }
    #run-status {
        dock: bottom;
        height: 1;
        width: 100%;
        background: $panel;
        padding: 0 1;
        color: $text;
    }
    """

    def __init__(
        self,
        plan: Plan,
        current: RunSettings,
        compact: bool,
        resume_available: bool = False,
    ):
        super().__init__()
        self.plan = plan
        self.current = current
        self.compact = compact
        self.resume_available = resume_available
        self._content: Static | None = None
        self._status: Static | None = None
        self._view_mode: str = "summary"
        self._field_index = 0
        self._thread_text = "" if current.thread_count is None else str(current.thread_count)
        self._verbose_enabled = current.verbose
        self._resume_enabled = current.resume
        self._previous_sub_title: str | None = None
        self._leaving_for_run = False
        self._command_cache: dict[Optional[int], list[PlannedCommand]] = {}

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="run-root"):
            with VerticalScroll(id="run-scroll"):
                content = Static(id="run-content")
                self._content = content
                yield content
        status = Static("", id="run-status")
        self._status = status
        yield status

    def on_mount(self) -> None:
        self._previous_sub_title = getattr(self.app, "sub_title", None)
        if hasattr(self.app, "sub_title"):
            self.app.sub_title = "Run settings"
        self._refresh()

    def on_unmount(self) -> None:
        if not self._leaving_for_run and self._previous_sub_title and hasattr(self.app, "sub_title"):
            self.app.sub_title = self._previous_sub_title

    def on_key(self, event: events.Key) -> None:
        if self._current_item_id() != "threads":
            return
        if event.character and event.character.isdigit():
            event.prevent_default()
            self._thread_text = (self._thread_text + event.character).lstrip("0") or "0"
            self._refresh()
            return
        if event.key == "backspace":
            event.prevent_default()
            self._thread_text = self._thread_text[:-1]
            self._refresh()
            return
        if event.character and event.character.lower() == "a":
            event.prevent_default()
            self._thread_text = ""
            self._refresh("Threads set to auto.")

    def _validate_threads(self) -> tuple[bool, Optional[int], Optional[str]]:
        text = self._thread_text.strip()
        if not text:
            return True, None, None
        try:
            value = int(text)
        except ValueError:
            return False, None, "Thread count must be a positive integer."
        if value <= 0:
            return False, None, "Thread count must be at least 1."
        return True, value, None

    def _refresh(self, message: str | None = None) -> None:
        if self._content is not None:
            self._content.update(self._render_run_content())
        self._update_status(message)

    def _update_status(self, message: str | None = None) -> None:
        if self._status is not None:
            if message:
                self._status.update(message)
                return
            if self._view_mode == "commands":
                self._status.update("Enter/R run | E edit | S save list | F6 summary | Esc back")
            else:
                self._status.update("Enter selected | R run | E edit | V verbose | S save | F6 preview | Esc back")

    def action_save(self) -> None:
        ok, threads, error = self._validate_threads()
        if not ok:
            self._refresh(error)
            return
        settings = self._current_settings_preview()
        settings.thread_count = threads
        self._leaving_for_run = True
        self.dismiss(settings)

    def action_cancel(self) -> None:
        if self._view_mode == "commands":
            self._view_mode = "summary"
            self._refresh("Run summary.")
            return
        self.dismiss(None)

    def action_save_commands(self) -> None:
        ok, _, error = self._validate_threads()
        if not ok:
            self._refresh(error)
            return
        app = self.app
        if not isinstance(app, PlanUIApp):
            self._refresh("Cannot save commands from this host.")
            return
        path = app.export_commands(self._current_settings_preview(), notify_detail=False)
        self._refresh(f"Commands saved to {path}" if path else "Failed to save commands.")

    def action_edit_commands(self) -> None:
        app = self.app
        if not isinstance(app, PlanUIApp):
            self._refresh("Cannot edit commands from this host.")
            return
        if not self.plan.rounds:
            self._refresh("No rounds found in this plan.")
            return
        picker = RoundPickerModal(self.plan.rounds)
        app.push_screen(picker, self._handle_edit_round_pick)

    def _handle_edit_round_pick(self, index: int | None) -> None:
        if index is None:
            self._refresh("Edit cancelled.")
            return
        app = self.app
        if not isinstance(app, PlanUIApp) or index >= len(self.plan.rounds):
            self._refresh("Cannot edit this round.")
            return
        app._start_round_edit(index, on_done=self._handle_command_edited)

    def _handle_command_edited(self) -> None:
        self._command_cache.clear()
        self._refresh("Command updated.")

    def action_toggle_verbose(self) -> None:
        self._verbose_enabled = not self._verbose_enabled
        self._refresh(f"Verbose logging {'on' if self._verbose_enabled else 'off'}.")

    def action_toggle_view(self) -> None:
        self._view_mode = "commands" if self._view_mode == "summary" else "summary"
        self._refresh("Command preview." if self._view_mode == "commands" else "Run summary.")

    def action_field_up(self) -> None:
        items = self._items()
        self._field_index = (self._field_index - 1) % len(items)
        self._refresh()

    def action_field_down(self) -> None:
        items = self._items()
        self._field_index = (self._field_index + 1) % len(items)
        self._refresh()

    def action_activate_selected(self) -> None:
        if self._view_mode == "commands":
            self.action_save()
            return
        item = self._current_item_id()
        if item == "run":
            self.action_save()
            return
        if item == "edit":
            self.action_edit_commands()
            return
        if item == "preview":
            self.action_toggle_view()
            return
        if item == "save_commands":
            self.action_save_commands()
            return
        self.action_toggle_selected()

    def action_toggle_selected(self) -> None:
        item = self._current_item_id()
        if item == "threads":
            self.action_edit_threads()
            return
        if item == "verbose":
            self.action_toggle_verbose()
            return
        if item == "resume":
            self._resume_enabled = not self._resume_enabled
            self._refresh(f"Resume {'on' if self._resume_enabled else 'off'}.")

    def action_increment_threads(self) -> None:
        if self._current_item_id() != "threads":
            return
        ok, threads, _ = self._validate_threads()
        next_value = (threads or 0) + 1 if ok else 1
        self._thread_text = str(next_value)
        self._refresh()

    def action_decrement_threads(self) -> None:
        if self._current_item_id() != "threads":
            return
        ok, threads, _ = self._validate_threads()
        if not ok or threads is None or threads <= 1:
            self._thread_text = ""
        else:
            self._thread_text = str(threads - 1)
        self._refresh()

    def action_edit_threads(self) -> None:
        ok, threads, _ = self._validate_threads()
        current = threads if ok else None
        self.app.push_screen(ThreadCountModal(current), self._handle_thread_count)

    def _handle_thread_count(self, result: int | str | None) -> None:
        if result is None:
            self._refresh("Thread edit cancelled.")
            return
        if result == THREAD_AUTO:
            self._thread_text = ""
            self._refresh("Threads set to auto.")
            return
        self._thread_text = str(result)
        self._refresh(f"Threads set to {result}.")

    def _items(self) -> list[tuple[str, str, str, str, str]]:
        settings = self._current_settings_preview()
        command_count = len(self._commands(settings))
        items = [
            (
                "run",
                "Run plan",
                "Enter/R",
                "action",
                "Start with the settings below.",
            ),
            (
                "edit",
                "Edit commands",
                "E",
                "action",
                "Choose a round and edit its command.",
            ),
            (
                "preview",
                "Command preview",
                f"F6 / {command_count}",
                "action",
                "Open the generated command list.",
            ),
            (
                "save_commands",
                "Save command list",
                "S",
                "action",
                "Write ramax_commands.txt without running.",
            ),
            (
                "threads",
                "Threads",
                "auto" if settings.thread_count is None else str(settings.thread_count),
                "setting",
                "Enter/Space edits; blank or auto keeps command defaults.",
            ),
            (
                "verbose",
                "Verbose",
                "on" if self._verbose_enabled else "off",
                "setting",
                "Streams every command output into the execution log.",
            ),
        ]
        if self.resume_available:
            items.append(
                (
                    "resume",
                    "Resume",
                    "on" if self._resume_enabled else "off",
                    "setting",
                    "Skips the already completed contiguous command prefix when possible.",
                )
            )
        if self._field_index >= len(items):
            self._field_index = len(items) - 1
        return items

    def _fields(self) -> list[tuple[str, str, str, str]]:
        return [(item_id, label, value, help_text) for item_id, label, value, _, help_text in self._items()]

    def _current_item_id(self) -> str:
        return self._items()[self._field_index][0]

    def _current_field_id(self) -> str:
        return self._current_item_id()

    def _current_settings_preview(self) -> RunSettings:
        ok, threads, _ = self._validate_threads()
        thread_val = threads if ok else self.current.thread_count
        return RunSettings(
            verbose=self._verbose_enabled,
            thread_count=thread_val,
            resume=self._resume_enabled,
            mash_auto=self.current.mash_auto,
            mash_distance_threshold=self.current.mash_distance_threshold,
        )

    def _render_run_content(self) -> RenderableType:
        settings = self._current_settings_preview()
        if self._view_mode == "commands":
            return self._render_commands(settings)
        return self._render_summary(settings)

    def _render_summary(self, settings: RunSettings) -> RenderableType:
        commands = self._commands(settings)
        ramax_rounds = sum(1 for round_entry in self.plan.rounds if round_entry.replace_with_ramax)
        cactus_rounds = len(self.plan.rounds) - ramax_rounds
        text = Text()
        text.append("Ready to run\n", style="bold cyan")
        text.append("Enter runs now. Use E to edit commands first.\n\n", style="dim")
        text.append(f"Steps: {len(commands)}", style="bold white")
        text.append(f"    Rounds: {len(self.plan.rounds)}", style="bold white")
        text.append(f"    RaMAx: {ramax_rounds}", style="bold #f59e0b")
        text.append(f"    Cactus: {cactus_rounds}\n", style="bold #22d3ee")
        preview_width = self._preview_width()
        text.append("Output: ", style="dim")
        text.append(f"{self._shorten(self.plan.out_dir or '-', preview_width)}\n")
        text.append("outSeqFile: ", style="dim")
        text.append(f"{self._shorten(self.plan.out_seq_file or '-', preview_width)}\n")
        jobstore = self._first_jobstore()
        if jobstore:
            text.append("JobStore: ", style="dim")
            text.append(f"{self._shorten(jobstore, preview_width)}\n")
        if self.resume_available:
            text.append("Resume state: ", style="dim")
            text.append("available\n" if self._resume_enabled else "available, disabled\n")

        current_section: str | None = None
        for item_id, label, value, section, help_text in self._items():
            if section != current_section:
                current_section = section
                heading = "\nActions\n" if section == "action" else "\nSettings\n"
                text.append(heading, style="bold cyan")
            selected = item_id == self._current_item_id()
            prefix = "> " if selected else "  "
            row_style = RUN_SELECTED_STYLE if selected else RUN_ITEM_STYLE
            value_style = RUN_SELECTED_STYLE if selected else RUN_VALUE_STYLE
            text.append(prefix, style=row_style)
            text.append(label, style=row_style)
            text.append("  ", style=row_style)
            text.append(f"[{value}]", style=value_style)
            text.append("\n")
            if selected:
                text.append(f"    - {help_text}\n", style=RUN_HELP_STYLE)
        return text

    def _render_commands(self, settings: RunSettings) -> RenderableType:
        commands = self._commands(settings)
        text = Text()
        text.append("Command preview\n", style="bold cyan")
        text.append("Enter runs. E edits commands. S saves this list. F6 returns to summary.\n\n", style="dim")
        if not commands:
            text.append("(no commands)\n", style="dim")
            return text
        limit = 200
        for index, command in enumerate(commands[:limit], start=1):
            text.append(f"{index:>3}. ", style="dim")
            text.append(command.shell_preview())
            text.append("\n")
        if len(commands) > limit:
            text.append(f"... {len(commands) - limit} more command(s) not shown.\n", style="dim")
        return text

    def _commands(self, settings: RunSettings) -> list[PlannedCommand]:
        key = settings.thread_count
        if key not in self._command_cache:
            self._command_cache[key] = planner.build_execution_plan(
                self.plan,
                self._base_dir(),
                thread_count=settings.thread_count,
            )
        return self._command_cache[key]

    def _base_dir(self) -> Path:
        app = self.app
        return app.base_dir if isinstance(app, PlanUIApp) else Path.cwd()

    def _first_jobstore(self) -> str | None:
        for round_entry in self.plan.rounds:
            for step in (round_entry.blast_step, round_entry.align_step, *round_entry.hal2fasta_steps):
                if step and step.jobstore:
                    return step.jobstore
        return None

    def _preview_width(self) -> int:
        try:
            width = self.size.width
        except Exception:
            width = 80
        return max(40, min(120, width - 12))

    def _shorten(self, text: str, width: int) -> str:
        if len(text) <= width:
            return text
        return text[: width - 1] + "…"


class RamaxOptionsModal(ModalScreen[tuple[list[str], list[str]] | None]):
    """Keyboard-first editor for global and per-round RaMAx options."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("f6", "toggle_section", "Switch section"),
    ]

    CSS = """
    RamaxOptionsModal {
        align: center middle;
    }
    #options-dialog {
        padding: 1 2;
        width: 80%;
        max-width: 90;
        height: 80%;
        border: round $accent;
        background: $panel;
        layout: vertical;
    }
    #options-title {
        padding-bottom: 1;
    }
    .section-label {
        padding-top: 1;
        padding-bottom: 0;
    }
    .option-editor {
        width: 100%;
        height: 1fr;
        min-height: 5;
    }
    #options-status {
        padding-top: 1;
        color: $error;
    }
    #options-hint {
        padding-top: 1;
        color: $text-muted;
    }
    """

    def __init__(self, global_opts: list[str], round_opts: list[str]):
        super().__init__()
        self._global_values = list(global_opts)
        self._round_values = list(round_opts)
        self._global_editor: TextArea | None = None
        self._round_editor: TextArea | None = None
        self._status: Static | None = None
        self._active_section = "global"

    def compose(self) -> ComposeResult:
        with Container(id="options-dialog"):
            yield Static("Edit RaMAx options", id="options-title")
            yield Static("Global options (one option per line)", classes="section-label")
            global_editor = TextArea(id="global-options-editor", classes="option-editor")
            global_editor.text = "\n".join(self._global_values)
            self._global_editor = global_editor
            yield global_editor
            yield Static("Current round options (one option per line)", classes="section-label")
            round_editor = TextArea(id="round-options-editor", classes="option-editor")
            round_editor.text = "\n".join(self._round_values)
            self._round_editor = round_editor
            yield round_editor
            status = Static("", id="options-status")
            self._status = status
            yield status
            yield Static("F6 switch section | Ctrl+S save | Esc back", id="options-hint")

    def on_mount(self) -> None:
        if self._global_editor:
            self._global_editor.focus()

    def action_save(self) -> None:
        global_values = self._collect_editor_values(self._global_editor)
        round_values = self._collect_editor_values(self._round_editor)
        self.dismiss((global_values, round_values))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_toggle_section(self) -> None:
        if self._active_section == "global":
            self._active_section = "round"
            if self._round_editor:
                self._round_editor.focus()
        else:
            self._active_section = "global"
            if self._global_editor:
                self._global_editor.focus()
        self._update_status(f"Editing {self._active_section} options.")

    def _collect_editor_values(self, editor: TextArea | None) -> list[str]:
        if editor is None:
            return []
        return [line.strip() for line in editor.text.splitlines() if line.strip()]

    def _update_status(self, message: str | None) -> None:
        if self._status is not None:
            self._status.update(message or "")


class ExecutionScreen(Screen[str]):
    """Run the plan inside Textual with progress, logs, and resource snapshots."""

    BINDINGS = [
        Binding("escape", "close_after_done", "Back when done"),
        Binding("q", "close_after_done", "Back when done"),
    ]

    CSS = """
    ExecutionScreen {
        layout: vertical;
        min-height: 0;
    }
    #exec-root {
        layout: vertical;
        height: 1fr;
        min-height: 0;
        padding: 1 2;
    }
    #exec-header {
        height: auto;
        padding-bottom: 0;
    }
    #exec-progress {
        height: auto;
        padding-bottom: 1;
        color: $text-muted;
    }
    #exec-body {
        layout: horizontal;
        height: 1fr;
        min-height: 0;
    }
    #exec-log {
        width: 4fr;
        height: 1fr;
        border: round $accent;
    }
    #exec-side {
        width: 1fr;
        height: 1fr;
        margin-left: 2;
        padding: 1;
        border: round $accent;
        overflow-y: auto;
    }
    #exec-status {
        height: 1;
        width: 100%;
        background: $panel;
        padding: 0 1;
        color: $text;
    }
    """

    def __init__(self, plan: Plan, base_dir: Path, run_settings: RunSettings):
        super().__init__()
        self.plan = plan
        self.base_dir = base_dir
        self.run_settings = run_settings
        self._events: queue.Queue[RunnerEvent] = queue.Queue()
        self._done = False
        self._failed = False
        self._error: Exception | None = None
        self._thread: threading.Thread | None = None
        self._title: Static | None = None
        self._progress: Static | None = None
        self._log: RichLog | None = None
        self._side: Static | None = None
        self._status: Static | None = None
        self._completed = 0
        self._total = 0
        self._current = ""
        self._last_log_path: Path | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="exec-root"):
            title = Static("Execution is starting...", id="exec-header")
            self._title = title
            yield title
            progress = Static("Progress: 0/0 (0%)", id="exec-progress")
            self._progress = progress
            yield progress
            with Container(id="exec-body"):
                log = RichLog(id="exec-log", wrap=True, markup=True, max_lines=500)
                self._log = log
                yield log
                side = Static("", id="exec-side")
                self._side = side
                yield side
        status = Static("Running. Wait for completion; Q/Esc returns when done.", id="exec-status")
        self._status = status
        yield status

    def on_mount(self) -> None:
        if hasattr(self.app, "sub_title"):
            self.app.sub_title = "Execution"
        self.set_interval(0.2, self._drain_events)
        self.set_interval(1.0, self._refresh_side)
        self._thread = threading.Thread(target=self._run_plan, daemon=True)
        self._thread.start()

    def _run_plan(self) -> None:
        try:
            runner = PlanRunner(
                self.plan,
                base_dir=self.base_dir,
                mirror_stdout=False,
                run_settings=self.run_settings,
                event_sink=self._events.put,
            )
            runner.run()
        except Exception as exc:
            self._error = exc
            self._events.put(RunnerEvent(kind="ui_runner_error", message=str(exc)))

    def _drain_events(self) -> None:
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            self._handle_event(event)

    def _handle_event(self, event: RunnerEvent) -> None:
        if event.total:
            self._total = event.total
        if event.log_path:
            self._last_log_path = event.log_path
        if event.kind == "plan_started":
            self._write_log(f"[bold cyan]{event.message}[/]")
        elif event.kind == "resume_notice":
            self._write_log(f"[yellow]{event.message}[/]")
        elif event.kind == "command_started":
            self._current = event.display_name or ""
            index = "" if event.command_index is None else f"{event.command_index + 1}/{event.total}"
            self._write_log(f"[cyan]▶ {index} {self._current}[/]")
            if event.message:
                self._write_log(f"[dim]{event.message}[/]")
        elif event.kind == "command_log":
            if event.message:
                self._write_log(event.message)
        elif event.kind == "command_skipped":
            self._completed += 1
            self._write_log(f"[yellow]⏭ {event.message}[/]")
        elif event.kind == "command_succeeded":
            self._completed += 1
            self._write_log(f"[green]✔ {event.message}[/]")
        elif event.kind == "command_failed":
            self._failed = True
            self._write_log(f"[red]✖ {event.message}[/]")
        elif event.kind == "plan_failed":
            self._done = True
            self._failed = True
            self._write_log(f"[red]{event.message}[/]")
        elif event.kind == "plan_completed":
            self._done = True
            self._write_log(f"[green]{event.message}[/]")
        elif event.kind == "ui_runner_error":
            self._done = True
            if not self._failed:
                self._failed = True
                self._write_log(f"[red]{event.message}[/]")
        if self._title:
            state = "failed" if self._failed else "complete" if self._done else "running"
            self._title.update(f"Execution {state}: {self._completed}/{self._total or '?'} complete")
        self._refresh_progress()
        if self._status:
            if self._done:
                self._status.update("Q/Esc back to planner")
            else:
                self._status.update("Running. Wait for completion; Q/Esc returns when done.")
        self._refresh_side()

    def _write_log(self, message: str) -> None:
        if self._log:
            self._log.write(message)

    def _refresh_progress(self) -> None:
        if not self._progress:
            return
        total = max(0, self._total)
        completed = min(self._completed, total) if total else self._completed
        percent = 0 if total == 0 else int((completed / total) * 100)
        current = self._current or "-"
        self._progress.update(f"Progress: {completed}/{total or '?'} ({percent}%) | Current: {current}")

    def _refresh_side(self) -> None:
        if not self._side:
            return
        metrics = self._collect_metrics()
        lines = [
            "[bold cyan]Status[/bold cyan]",
            f"Result: {'failed' if self._failed else 'done' if self._done else 'running'}",
            f"Completed: {self._completed}/{self._total or '?'}",
            "",
            "[bold cyan]Latest log[/bold cyan]",
            f"Latest: {self._last_log_path or '-'}",
            "",
            "[bold cyan]Resources[/bold cyan]",
            metrics,
        ]
        if self._done:
            lines.extend(["", "Press Q or Esc to return to the planner."])
        self._side.update("\n".join(lines))

    def _collect_metrics(self) -> str:
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage(self.base_dir)
            return (
                f"CPU: {cpu:.0f}%\n"
                f"Memory: {mem.percent:.0f}% {mem.used / (1024**3):.1f}/{mem.total / (1024**3):.1f} GB\n"
                f"Disk: {disk.percent:.0f}% {disk.used / (1024**3):.1f}/{disk.total / (1024**3):.1f} GB"
            )
        except Exception:
            return "Resource snapshot unavailable"

    def action_close_after_done(self) -> None:
        if self._done:
            self.dismiss("failed" if self._failed else "completed")
        else:
            self._write_log("[yellow]Execution is still running; wait for completion before leaving this screen.[/]")

class PlanUIApp(App[UIResult]):
    CSS = """
    /* Deep Space HUD theme */
    $bg-deep: #0f111a;
    $bg-panel: #1a1d2e;
    $border-bright: #444b6a;
    $text-main: #e0def4;
    $accent-gold: #f6c177;
    $accent-blue: #9ccfd8;
    $accent-green: #31748f;

    Screen {
        layout: vertical;
        min-height: 0;
        background: $bg-deep;
        color: $text-main;
    }
    #tree-container {
        height: 1fr;
        width: 100%;
        background: $bg-deep;
        overflow: hidden;
        layout: horizontal;
        min-height: 0;
    }
    PlanTreeBrowser {
        width: 74%;
        height: 100%;
        background: $bg-deep;
        padding: 0 1;
    }
    DecisionPanel {
        width: 26%;
        background: $bg-panel;
    }
    #plan-tree-empty {
        align: center middle;
        color: #6b768f;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        width: 100%;
        background: $bg-panel;
        padding: 0 1;
        color: $text-main;
    }
    #editor-command { height: 10; }

    ModalScreen {
        background: rgba(0, 0, 0, 0.6);
        align: center middle;
    }
    #picker-dialog, #editor-dialog, #info-dialog, #search-dialog, #round-picker, #options-dialog, #run-form {
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    Input {
        background: $bg-panel;
        border: none;
        color: $text-main;
    }
    Input:focus {
        border: tall $accent-blue;
    }
    """

    BINDINGS = [
        Binding("enter", "edit_round", "Edit command"),
        Binding("e", "edit_round", "Edit command"),
        Binding("r", "run_plan", "Run"),
        Binding("t", "mash_threshold", "Mash threshold"),
        Binding("q", "quit", "Quit"),
        Binding("i", "show_info", "Info"),
    ]

    def __init__(self, plan: Plan, base_dir: Optional[Path] = None, run_settings: Optional[RunSettings] = None):
        super().__init__()
        self.title = "CAX"
        self.sub_title = "Plan"
        self.plan = plan
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.alignment_tree = tree_utils.build_alignment_tree(plan, base_dir=self.base_dir)
        self._run_state_path = self._resolve_run_state_path()
        self.resume_available = self._run_state_path.exists()
        self.canvas: PlanTreeBrowser | None = None
        self.run_settings = run_settings or RunSettings()
        self.decision_panel: DecisionPanel | None = None
        self.status_bar: Static | None = None
        self._last_detail_text: str = ""

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="tree-container"):
            if self.alignment_tree:
                canvas = PlanTreeBrowser(self.alignment_tree.root)
                canvas.set_detail_callback(self._on_node_selected)
                self.canvas = canvas
                yield canvas
                decision_panel = DecisionPanel()
                self.decision_panel = decision_panel
                yield decision_panel
            else:
                yield Static("Alignment tree not found; nothing to render.", id="plan-tree-empty")
        status_bar = Static("", id="status-bar")
        self.status_bar = status_bar
        yield status_bar

    def on_mount(self) -> None:
        if self.canvas:
            self.canvas.focus()
            self._on_node_selected(self.canvas.current_node())
        else:
            self.sub_title = "Plan overview"
            self._last_detail_text = "Alignment tree not found; nothing to render."
        
        if self.run_settings.resume and self.resume_available:
            # 断点续跑专属入口：直接进入运行设置/续跑摘要界面。
            self.set_timer(0.05, self.action_run_plan)
        else:
            self._update_status_bar(
                TREE_STATUS_HELP
            )

    def _resolve_run_state_path(self) -> Path:
        if self.plan.out_dir:
            out_dir = Path(self.plan.out_dir).expanduser()
            if not out_dir.is_absolute():
                out_dir = (self.base_dir / out_dir).resolve()
            return (out_dir / "logs" / "run_state.json").resolve()
        return (self.base_dir / "logs" / "run_state.json").resolve()

    def _show_welcome_guide(self) -> None:
        welcome_text = (
            "Welcome to the Cactus-RaMAx Planner!\n\n"
            "This interactive UI allows you to inspect and configure the phylogenetic alignment plan.\n\n"
            "• [bold]Navigation[/]: Use Arrow keys or h/j/k/l to browse the tree.\n"
            "• [bold]Toggle RaMAx[/]: Press [bold]SPACE[/] on a node to enable/disable acceleration.\n"
            "• [bold]Edit[/]: Press [bold]Enter[/] or [bold]E[/] to customize commands and options.\n"
            "• [bold]Search[/]: Press [bold]/[/] to find species or nodes.\n"
            "• [bold]Run[/]: Press [bold]R[/] to review settings and start execution.\n"
        )
        self.push_screen(InfoModal("Quick Start Guide", welcome_text))

    def _is_compact(self) -> bool:
        return self.size.width <= 100

    def _set_header_for_node(self, node: tree_utils.AlignmentNode) -> None:
        label = _node_display_name(node)
        if node.round:
            self.sub_title = f"{node.round.name} ({label})"
        elif node.children:
            self.sub_title = f"Clade {label}"
        else:
            self.sub_title = f"Leaf {label}"

    def _update_status_bar(self, message: str) -> None:
        if self.status_bar:
            self.status_bar.update(message)

    def action_show_info(self) -> None:
        content = self._last_detail_text or "(empty)"
        self.push_screen(InfoModal("Current node details", content))

    def action_edit_round(self) -> None:
        if not self.plan.rounds:
            self._last_detail_text = "No rounds found in this plan."
            self._update_status_bar(self._last_detail_text)
            return
        node_round = None
        if self.canvas:
            node = self.canvas.current_node()
            node_round = node.round
        if node_round and node_round in self.plan.rounds:
            round_index = self.plan.rounds.index(node_round)
            self._start_round_edit(round_index)
            return
        picker = RoundPickerModal(self.plan.rounds)
        self.push_screen(picker, self._handle_round_pick)

    def _handle_round_pick(self, index: int | None) -> None:
        if index is None:
            return
        if index >= len(self.plan.rounds):
            return
        self._start_round_edit(index)

    def _start_round_edit(self, round_index: int, on_done: Optional[Callable[[], None]] = None) -> None:
        round_entry = self.plan.rounds[round_index]
        targets = self._gather_command_targets(round_entry)
        if not targets:
            self._last_detail_text = "No editable commands for this round."
            self._update_status_bar(self._last_detail_text)
            return
        if len(targets) == 1:
            self._open_command_editor(round_index, targets[0], on_done=on_done)
        else:
            self._open_command_target_picker(round_index, targets, on_done=on_done)
        self._show_round(round_index)

    def _open_command_target_picker(
        self,
        round_index: int,
        targets: list[CommandTarget],
        *,
        on_done: Optional[Callable[[], None]] = None,
        initial_index: int = 0,
    ) -> None:
        self.push_screen(
            CommandSelectionModal(targets, initial_index=initial_index),
            lambda target: self._handle_command_selection(round_index, targets, target, on_done=on_done),
        )

    def action_run_plan(self) -> None:
        screen = RunSettingsScreen(
            self.plan,
            self.run_settings,
            compact=self._is_compact(),
            resume_available=self.resume_available,
        )
        self.push_screen(screen, self._finalize_run_settings)

    def action_mash_threshold(self) -> None:
        self.push_screen(
            MashThresholdModal(self.run_settings.mash_distance_threshold),
            self._apply_mash_threshold,
        )

    def export_commands(self, settings: RunSettings | None = None, *, notify_detail: bool = True) -> Path | None:
        output_dir = Path(self.plan.out_dir or self.base_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "ramax_commands.txt"
        commands = planner.build_execution_plan(
            self.plan,
            self.base_dir,
            thread_count=(settings.thread_count if settings else self.run_settings.thread_count),
        )
        lines = [cmd.shell_preview() for cmd in commands]
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if notify_detail:
            self._last_detail_text = f"[green]Commands saved to {output_path}[/green]"
            self._update_status_bar(f"Commands saved to {output_path}")
        return output_path

    def action_quit(self) -> None:
        self.exit(UIResult(plan=self.plan, action="quit", run_settings=self.run_settings))

    def _apply_mash_threshold(self, threshold: float | None) -> None:
        if threshold is None:
            return

        self.run_settings.mash_distance_threshold = threshold

        # If Mash auto-selection is enabled and mash is available, recompute as needed (supports early-stop + 补算).
        if self.run_settings.mash_auto and shutil.which("mash"):
            if self.is_running:
                self._pending_mash_threshold = threshold
                self._update_status_bar(f"Computing Mash distances for threshold={threshold:.4f}...")

                def work() -> mash_auto_module.MashAutoSummary:
                    preprocess_seq_file = seq_cache.find_preprocess_input_seq_file(self.plan, base_dir=self.base_dir)
                    return mash_auto_module.apply_mash_distance_defaults(
                        self.plan,
                        base_dir=self.base_dir,
                        threshold=threshold,
                        sequence_file=preprocess_seq_file,
                    )

                self.run_worker(
                    work,
                    name="mash-threshold",
                    group="mash",
                    description="Compute Mash distances for threshold changes",
                    exit_on_error=False,
                    exclusive=True,
                    thread=True,
                )
                return

            preprocess_seq_file = seq_cache.find_preprocess_input_seq_file(self.plan, base_dir=self.base_dir)
            summary = mash_auto_module.apply_mash_distance_defaults(
                self.plan,
                base_dir=self.base_dir,
                threshold=threshold,
                sequence_file=preprocess_seq_file,
            )
            status = (
                f"Applied Mash threshold {threshold:.4f}: enabled {summary.enabled_ramax}/{summary.computed} rounds "
                f"(pairs: +{summary.pairwise_computed}, cached {summary.pairwise_cached})."
            )
        else:
            summary = mash_auto_module.apply_mash_threshold(self.plan, threshold=threshold)
            status = (
                f"Applied Mash threshold {threshold:.4f}: enabled {summary.enabled_ramax}/{summary.considered} rounds "
                f"(changed {summary.changed})."
            )

        if self.canvas:
            self.canvas.rebuild_labels()
            self._on_node_selected(self.canvas.current_node(), status=status)
        else:
            self._update_status_bar(status)

    def on_worker_state_changed(self, message: Worker.StateChanged) -> None:
        if message.worker.name != "mash-threshold":
            return
        if message.state == WorkerState.ERROR:
            error = message.worker.error
            self._update_status_bar(f"Mash computation failed: {error}")
            return
        if message.state != WorkerState.SUCCESS:
            return
        threshold = getattr(self, "_pending_mash_threshold", self.run_settings.mash_distance_threshold)
        summary = message.worker.result
        if summary is None:
            return
        status = (
            f"Applied Mash threshold {threshold:.4f}: enabled {summary.enabled_ramax}/{summary.computed} rounds "
            f"(pairs: +{summary.pairwise_computed}, cached {summary.pairwise_cached})."
        )
        if self.canvas:
            self.canvas.rebuild_labels()
            self._on_node_selected(self.canvas.current_node(), status=status)
        else:
            self._update_status_bar(status)

    def _round_details(self, round_entry: Round) -> list[str]:
        details = [f"[bold]{round_entry.name}[/bold] root={round_entry.root}"]
        if round_entry.mash_distance is not None:
            prefix = "Mash distance"
            if round_entry.mash_reference and round_entry.mash_query:
                prefix = f"{prefix} ({round_entry.mash_reference} vs {round_entry.mash_query})"
            details.append(f"{prefix}: {round_entry.mash_distance:.4f}")
            threshold = self.run_settings.mash_distance_threshold
            details.append(
                f"[dim]Mash note: distance ≤ {threshold:.4f} means subtree max (all pairs checked); "
                f"distance > {threshold:.4f} means a witness pair was found (early-stop).[/dim]"
            )
            if round_entry.mash_source and round_entry.mash_source != round_entry.root:
                if round_entry.mash_distance > threshold:
                    details.append(
                        f"[dim]Mash source: {round_entry.mash_source} (descendant witness; this node inherits the failure).[/dim]"
                    )
                else:
                    details.append(
                        f"[dim]Mash source: {round_entry.mash_source} (subtree max observed in descendant).[/dim]"
                    )
        elif self.run_settings.mash_auto:
            if shutil.which("mash") is None:
                details.append("[dim]Mash distance: (not computed — `mash` not found on PATH)[/dim]")
            else:
                threshold = self.run_settings.mash_distance_threshold
                details.append("[dim]Mash distance: (not computed for this round)[/dim]")
                details.append(
                    f"[dim]Hint: press [bold]T[/bold] to compute Mash distances (threshold={threshold:.4f}); "
                    "ensure sequences are local/cached.[/dim]"
                )
        if round_entry.replace_with_ramax:
            ramax_preview = self._ramax_command_preview(round_entry)
            if ramax_preview:
                details.extend(["", "[green]RaMAx command[/green]", ramax_preview])
        else:
            if round_entry.blast_step:
                details.extend(["", "[cyan]cactus-blast[/cyan]", round_entry.blast_step.raw])
            if round_entry.align_step:
                details.extend(["", "[cyan]cactus-align[/cyan]", round_entry.align_step.raw])
        if round_entry.hal2fasta_steps:
            details.append("")
            details.append("[magenta]hal2fasta[/magenta]")
            details.extend(step.raw for step in round_entry.hal2fasta_steps)
        details.append("")
        details.append("[yellow]RaMAx options[/yellow]")
        details.append(f"Global: {self._format_option_list(self.plan.global_ramax_opts)}")
        details.append(f"Round: {self._format_option_list(round_entry.ramax_opts)}")
        return details

    def _format_option_list(self, options: list[str]) -> str:
        return ", ".join(options) if options else "(empty)"

    def _ramax_options_summary(self, round_entry: Round) -> str:
        global_summary = self._format_option_list(self.plan.global_ramax_opts)
        round_summary = self._format_option_list(round_entry.ramax_opts)
        return f"Global: {global_summary}\nRound: {round_summary}"

    def _show_round(self, index: int, status: str | None = None) -> None:
        if index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[index]
        details = self._round_details(round_entry)
        if status:
            details.extend(["", f"[green]{status}[/green]"])
        self._last_detail_text = "\n".join(details)
        if self.canvas and self.canvas.current_node().round is round_entry:
            self._set_header_for_node(self.canvas.current_node())
        else:
            self.sub_title = f"{round_entry.name} ({round_entry.root})"
        self._update_status_bar(status or f"Selected round {round_entry.name}")

    def _gather_command_targets(self, round_entry: Round) -> list[CommandTarget]:
        targets: list[CommandTarget] = [
            CommandTarget(
                key="ramax-options",
                label="RaMAx options",
                command=self._ramax_options_summary(round_entry),
                kind="ramax-options",
            )
        ]
        if round_entry.replace_with_ramax:
            ramax_preview = self._ramax_command_preview(round_entry)
            targets.append(
                CommandTarget(
                    key="ramax",
                    label="RaMAx",
                    command=ramax_preview,
                    kind="ramax",
                )
            )
        else:
            if round_entry.blast_step:
                targets.append(
                    CommandTarget(
                        key="blast",
                        label="cactus-blast",
                        command=round_entry.blast_step.raw,
                        kind="blast",
                        step=round_entry.blast_step,
                    )
                )
            if round_entry.align_step:
                targets.append(
                    CommandTarget(
                        key="align",
                        label="cactus-align",
                        command=round_entry.align_step.raw,
                        kind="align",
                        step=round_entry.align_step,
                    )
                )
        for idx, step in enumerate(round_entry.hal2fasta_steps):
            label = "hal2fasta" if len(round_entry.hal2fasta_steps) == 1 else f"hal2fasta #{idx + 1}"
            targets.append(
                CommandTarget(
                    key=f"hal2fasta-{idx}",
                    label=label,
                    command=step.raw,
                    kind="hal2fasta",
                    step=step,
                    index=idx,
                )
            )
        return targets

    def _show_alignment_node(
        self,
        node: tree_utils.AlignmentNode,
        status: str | None = None,
    ) -> None:
        details: list[str] = []
        if node.round:
            details.extend(self._round_details(node.round))
        else:
            title = _node_display_name(node)
            details.append(f"[bold]{title}[/bold]")
        subtree_rounds = list(node.iter_rounds())
        if subtree_rounds:
            replaced = sum(1 for round_entry in subtree_rounds if round_entry.replace_with_ramax)
            details.extend(
                [
                    "",
                    f"Subtree summary: RaMAx {replaced}/{len(subtree_rounds)} rounds",
                ]
            )
            mash_values = [
                round_entry.mash_distance
                for round_entry in subtree_rounds
                if round_entry.mash_distance is not None
            ]
            if mash_values:
                details.append(
                    f"Subtree Mash max: {max(mash_values):.4f} (computed {len(mash_values)}/{len(subtree_rounds)} rounds)"
                )
        else:
            details.extend(["", "No cactus rounds in this subtree (leaf node)."])
        if status:
            details.extend(["", f"[green]{status}[/green]"])
        self._last_detail_text = "\n".join(details)
        self._set_header_for_node(node)
        if self.decision_panel:
            scope = self.canvas.current_scope() if self.canvas else "subtree"
            self.decision_panel.update_node(
                node,
                scope=scope,
                run_settings=self.run_settings,
                status=status,
            )
        self._update_status_bar(status or TREE_STATUS_HELP)

    def _handle_command_selection(
        self,
        round_index: int,
        targets: list[CommandTarget],
        target: CommandTarget | None,
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        if target is None:
            return
        try:
            target_index = next(index for index, candidate in enumerate(targets) if candidate is target)
        except StopIteration:
            target_index = 0
        self._open_command_editor(
            round_index,
            target,
            on_done=on_done,
            on_cancel=lambda: self._open_command_target_picker(
                round_index,
                targets,
                on_done=on_done,
                initial_index=target_index,
            ),
        )

    def _on_node_selected(
        self, node: tree_utils.AlignmentNode, status: str | None = None
    ) -> None:
        """Tree navigation callback that drives the decision panel."""
        self._show_alignment_node(node, status=status)

    def _open_command_editor(
        self,
        round_index: int,
        target: CommandTarget,
        on_done: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> None:
        if target.kind == "ramax-options":
            if round_index >= len(self.plan.rounds):
                return
            options_modal = RamaxOptionsModal(
                self.plan.global_ramax_opts,
                self.plan.rounds[round_index].ramax_opts,
            )
            self.push_screen(
                options_modal,
                lambda result: self._apply_ramax_options(
                    round_index,
                    result,
                    on_done=on_done,
                    on_cancel=on_cancel,
                ),
            )
            return
        editor = CommandEditModal(f"Edit {target.label} command", target.command)
        self.push_screen(
            editor,
            lambda new_command: self._apply_command_edit(
                round_index,
                target,
                new_command,
                on_done=on_done,
                on_cancel=on_cancel,
            ),
        )

    def _apply_command_edit(
        self,
        round_index: int,
        target: CommandTarget,
        new_command: str | None,
        on_done: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> None:
        if new_command is None:
            if on_cancel:
                on_cancel()
            return
        if round_index >= len(self.plan.rounds):
            return
        round_entry = self.plan.rounds[round_index]
        if target.kind == "ramax":
            round_entry.manual_ramax_command = new_command
        elif target.step is not None:
            target.step.raw = new_command
        status = f"Updated {target.label} command"
        if self.canvas:
            self.canvas.rebuild_labels()
        self._show_round(round_index, status=status)
        if on_done:
            on_done()

    def _apply_ramax_options(
        self,
        round_index: int,
        result: tuple[list[str], list[str]] | None,
        on_done: Optional[Callable[[], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ) -> None:
        if result is None:
            if on_cancel:
                on_cancel()
            return
        if round_index >= len(self.plan.rounds):
            return
        global_opts, round_opts = result
        self.plan.global_ramax_opts = global_opts
        round_entry = self.plan.rounds[round_index]
        round_entry.ramax_opts = round_opts
        status = "RaMAx options updated"
        if self.canvas:
            self.canvas.rebuild_labels()
        self._show_round(round_index, status=status)
        if on_done:
            on_done()

    def _finalize_run_settings(self, result: RunSettings | None) -> None:
        if result is None:
            return
        self.run_settings = result
        self.push_screen(
            ExecutionScreen(self.plan, self.base_dir, self.run_settings),
            self._handle_execution_finished,
        )

    def _handle_execution_finished(self, result: str | None) -> None:
        if result == "completed":
            self.exit(UIResult(plan=self.plan, action="run_completed", run_settings=self.run_settings))
            return
        if result == "failed":
            self.exit(UIResult(plan=self.plan, action="run_failed", run_settings=self.run_settings))
            return
        if result:
            self._update_status_bar(f"Execution {result}.")

    def _ramax_command_preview(self, round_entry: Round) -> str:
        if round_entry.manual_ramax_command:
            return round_entry.manual_ramax_command
        commands = planner.build_execution_plan(
            self.plan,
            self.base_dir,
            thread_count=self.run_settings.thread_count,
        )
        for command in commands:
            if command.is_ramax and command.round_name == round_entry.name:
                return command.shell_preview()
        return ""


def launch(
    plan: Plan,
    base_dir: Optional[Path] = None,
    run_settings: Optional[RunSettings] = None,
) -> UIResult:
    """Run the Textual UI and return the resulting plan/action."""

    app = PlanUIApp(plan, base_dir=base_dir, run_settings=run_settings)
    result = app.run()
    if isinstance(result, UIResult):
        return result
    return UIResult(plan=plan, action="quit", run_settings=run_settings)
