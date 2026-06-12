"""Textual setup screen for collecting minimal cactus-prepare inputs."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Literal

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.events import Key
from textual.screen import Screen
from textual.widgets import Header, Input, Static

from . import history, templates


SUPPORTED_INPUT_SUFFIXES = {".txt", ".seq", ".fa", ".fasta", ".fna", ".nwk", ".newick"}
MAX_CANDIDATES = 12
CANDIDATE_PAGE_SIZE = 6
GENERATED_PREPARE_FLAGS = {"--help", "--outDir", "--outSeqFile", "--outHal", "--jobStore"}
PATH_CANDIDATE_SOURCES = {"dir", "file", "example", "template", "history"}
ADVANCED_OPTION_PRIORITY = (
    "--defaultCores",
    "--preprocessCores",
    "--blastCores",
    "--alignCores",
    "--defaultMemory",
    "--preprocessMemory",
    "--blastMemory",
    "--alignMemory",
    "--defaultDisk",
    "--gpu",
    "--lastzCores",
    "--maskMode",
    "--branchScale",
    "--configFile",
    "--hdf5Codec",
    "--includeRoot",
    "--script",
    "--seqFileOnly",
)
OPTION_PATTERN = re.compile(
    r"^\s+(?:-[A-Za-z0-9],\s*)?(--[A-Za-z][A-Za-z0-9-]*)"
    r"(?:\s+(\[[^\]]+\]|\{[^}]+\}|[A-Z][A-Z0-9_]*))?"
    r"(?:\s{2,}(.*))?$"
)


@dataclass
class PromptResult:
    """Result returned from the prepare setup UI."""

    executable: str
    args: str
    action: Literal["submit", "quit"]


@dataclass
class PrepareDefaults:
    """Generated cactus-prepare paths for an input species/seq file."""

    input_file: str
    output_dir: str
    out_seq: str
    out_hal: str
    job_store: str
    advanced_args: str = ""


@dataclass
class PathCandidate:
    """Visible path candidate used by the shell-like input file field."""

    value: str
    label: str
    source: str
    is_dir: bool = False


@dataclass(frozen=True)
class PrepareOption:
    """A cactus-prepare option parsed from its own help text."""

    flag: str
    metavar: str = ""
    description: str = ""

    @property
    def label(self) -> str:
        suffix = f" {self.metavar}" if self.metavar else ""
        if self.description:
            description = " ".join(self.description.split())
            if len(description) > 72:
                description = description[:69].rstrip() + "..."
            return f"{self.flag}{suffix}  {description}"
        return f"{self.flag}{suffix}"


def prompt_prepare_command() -> PromptResult:
    """Launch the Textual setup UI and return the user's command selection."""

    app = PrepareCommandPrompt()
    result = app.run()
    if isinstance(result, PromptResult):
        return result
    return PromptResult(executable="", args="", action="quit")


def build_prepare_defaults(input_file: str) -> PrepareDefaults:
    """Build deterministic output paths from a species/seq input path."""

    cleaned = input_file.strip()
    stem = Path(cleaned).expanduser().stem or "run"
    output_dir = templates.default_output_dir(stem)
    return PrepareDefaults(
        input_file=cleaned,
        output_dir=str(output_dir),
        out_seq=str(output_dir / f"{stem}.txt"),
        out_hal=str(output_dir / f"{stem}.hal"),
        job_store=str(output_dir / "jobstore"),
    )


def build_prepare_command(state: PrepareDefaults) -> PromptResult | None:
    """Convert form state into the cactus-prepare command expected by the CLI."""

    if not state.input_file.strip():
        return None
    tokens = [
        "cactus-prepare",
        state.input_file.strip(),
        "--outDir",
        state.output_dir.strip(),
        "--outSeqFile",
        state.out_seq.strip(),
        "--outHal",
        state.out_hal.strip(),
        "--jobStore",
        state.job_store.strip(),
    ]
    advanced = state.advanced_args.strip()
    if advanced:
        try:
            tokens.extend(shlex.split(advanced))
        except ValueError:
            return None
    return PromptResult(executable="cactus-prepare", args=shlex.join(tokens[1:]), action="submit")


def parse_prepare_history(command: str) -> PrepareDefaults | None:
    """Parse a saved cactus-prepare command into setup fields."""

    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens:
        return None
    if Path(tokens[0]).name == "cactus-prepare":
        tokens = tokens[1:]
    defaults = _tokens_to_defaults(tokens)
    input_file = defaults.get("input_file", "").strip()
    if not input_file:
        return None
    generated = build_prepare_defaults(input_file)
    output_dir = defaults.get("output_dir") or generated.output_dir
    return PrepareDefaults(
        input_file=input_file,
        output_dir=output_dir,
        out_seq=defaults.get("out_seq") or _out_seq_for(output_dir, input_file),
        out_hal=defaults.get("out_hal") or _out_hal_for(output_dir, input_file),
        job_store=defaults.get("job_store") or str(Path(output_dir).expanduser() / "jobstore"),
        advanced_args=defaults.get("advanced_args", ""),
    )


def _out_seq_for(output_dir: str, input_file: str) -> str:
    stem = Path(input_file).expanduser().stem or "run"
    return str(Path(output_dir).expanduser() / f"{stem}.txt")


def _out_hal_for(output_dir: str, input_file: str) -> str:
    stem = Path(input_file).expanduser().stem or "run"
    return str(Path(output_dir).expanduser() / f"{stem}.hal")


def _parse_prepare_command(command: str) -> PromptResult | None:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    if not tokens or Path(tokens[0]).name != "cactus-prepare":
        return None
    return PromptResult(executable=tokens[0], args=shlex.join(tokens[1:]), action="submit")


def _tokens_to_defaults(tokens: list[str]) -> dict[str, str]:
    """Infer setup defaults from cactus-prepare arguments."""

    defaults: dict[str, str] = {}
    if not tokens:
        return defaults

    flag_map = {
        "--outDir": "output_dir",
        "--outSeqFile": "out_seq",
        "--outHal": "out_hal",
        "--jobStore": "job_store",
        "--jobstore": "job_store",
    }
    extra: list[str] = []
    input_seen = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token.startswith("--") and "=" in token:
            flag, value = token.split("=", 1)
            field = flag_map.get(flag)
            if field:
                defaults[field] = value
            else:
                extra.append(token)
            i += 1
            continue
        if token in flag_map:
            field = flag_map[token]
            if i + 1 < len(tokens):
                defaults[field] = tokens[i + 1]
                i += 2
                continue
            extra.append(token)
            i += 1
            continue
        if not token.startswith("-") and not input_seen:
            defaults["input_file"] = token
            input_seen = True
            i += 1
            continue
        if token.startswith("-"):
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                extra.extend([token, tokens[i + 1]])
                i += 2
            else:
                extra.append(token)
                i += 1
            continue
        i += 1
    if extra:
        defaults["advanced_args"] = shlex.join(extra)
    return defaults


def _display_candidate_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _looks_like_input_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES and _is_valid_seqfile(path)


def _is_valid_seqfile(path_like: str | Path) -> bool:
    path = Path(path_like).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            first = next((line.strip() for line in handle if line.strip()), "")
    except OSError:
        return False
    return bool(first.endswith(";") and "(" in first)


def _candidate_matches(candidate: str, query: str) -> bool:
    cleaned = query.strip().lower()
    if not cleaned:
        return True
    value = candidate.lower()
    name = Path(candidate).name.lower()
    return value.startswith(cleaned) or name.startswith(cleaned)


def _path_query_parts(raw: str) -> tuple[Path, str, str]:
    if not raw:
        return Path("."), "", ""
    expanded = Path(raw).expanduser()
    if raw.endswith(("/", "\\")):
        return expanded, "", raw
    parent = expanded.parent if str(expanded.parent) not in {"", "."} else Path(".")
    base = expanded.name
    prefix = raw[: max(0, len(raw) - len(base))]
    return parent, base, prefix


def _common_prefix(values: list[str]) -> str:
    if not values:
        return ""
    return os.path.commonprefix(values)


@lru_cache(maxsize=4)
def _load_prepare_options(executable: str = "cactus-prepare") -> tuple[PrepareOption, ...]:
    """Read advanced argument candidates from `cactus-prepare --help`."""

    try:
        result = subprocess.run(
            [executable, "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ()
    return tuple(_parse_prepare_help_options((result.stdout or "") + "\n" + (result.stderr or "")))


def _parse_prepare_help_options(help_text: str) -> list[PrepareOption]:
    options: list[PrepareOption] = []
    seen: set[str] = set()
    current: PrepareOption | None = None
    for line in help_text.splitlines():
        match = OPTION_PATTERN.match(line)
        if match:
            flag = match.group(1)
            if flag in GENERATED_PREPARE_FLAGS or flag in seen:
                current = None
                continue
            option = PrepareOption(
                flag=flag,
                metavar=match.group(2) or "",
                description=(match.group(3) or "").strip(),
            )
            seen.add(flag)
            options.append(option)
            current = option
            continue
        if current and line.startswith("                        "):
            extra = line.strip()
            if extra:
                updated = PrepareOption(
                    flag=current.flag,
                    metavar=current.metavar,
                    description=f"{current.description} {extra}".strip(),
                )
                options[-1] = updated
                current = updated
    priority = {flag: index for index, flag in enumerate(ADVANCED_OPTION_PRIORITY)}
    return sorted(options, key=lambda option: priority.get(option.flag, len(priority)))


def _advanced_token_parts(raw: str) -> tuple[str, str]:
    start = len(raw)
    while start > 0 and not raw[start - 1].isspace():
        start -= 1
    return raw[:start], raw[start:]


class KeyboardPickerList(Static):
    """Focusable picker surface that lets the parent screen own selection."""

    can_focus = True

    async def handle_key(self, event: Key) -> bool:  # type: ignore[override]
        screen = self.screen
        if event.key in {"up", "k"}:
            event.prevent_default()
            event.stop()
            if hasattr(screen, "action_cursor_up"):
                screen.action_cursor_up()
            return True
        if event.key in {"down", "j"}:
            event.prevent_default()
            event.stop()
            if hasattr(screen, "action_cursor_down"):
                screen.action_cursor_down()
            return True
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            if hasattr(screen, "action_select_current"):
                screen.action_select_current()
            return True
        if event.key == "escape":
            event.prevent_default()
            event.stop()
            if hasattr(screen, "action_cancel"):
                screen.action_cancel()
            return True
        if event.key in {"d", "delete"} and hasattr(screen, "action_delete_entry"):
            event.prevent_default()
            event.stop()
            screen.action_delete_entry()
            return True
        return await super().handle_key(event)


class TemplateSelector(Screen[templates.Template | None]):
    """Keyboard-first template picker."""

    BINDINGS = [
        Binding("escape", "cancel", "Back", priority=True, show=False),
        Binding("enter", "select_current", "Load", priority=True, show=False),
        Binding("up", "cursor_up", "Up", priority=True, show=False),
        Binding("down", "cursor_down", "Down", priority=True, show=False),
        Binding("k", "cursor_up", "Up", priority=True, show=False),
        Binding("j", "cursor_down", "Down", priority=True, show=False),
    ]

    def __init__(self, template_list: list[templates.Template]) -> None:
        super().__init__()
        self._templates = template_list
        self._list_view: KeyboardPickerList | None = None
        self._index = 0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="template-layout"):
            yield Static("Templates", id="template-title")
            self._list_view = KeyboardPickerList("", id="template-list")
            yield self._list_view
        yield Static("Up/Down choose | Enter load | Esc", id="template-help")

    def on_mount(self) -> None:  # type: ignore[override]
        if self._list_view:
            self._index = 0
            self._refresh_list()
            self._list_view.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_up(self) -> None:
        self._move_selection(-1)

    def action_cursor_down(self) -> None:
        self._move_selection(+1)

    def action_select_current(self) -> None:
        if not self._templates:
            self.dismiss(None)
            return
        template = self._templates[self._index] if 0 <= self._index < len(self._templates) else None
        self.dismiss(template)

    def _move_selection(self, delta: int) -> None:
        if not self._templates:
            return
        self._index = (self._index + delta) % len(self._templates)
        self._refresh_list()

    def _refresh_list(self) -> None:
        if not self._list_view:
            return
        text = Text()
        if not self._templates:
            text.append("No templates available.", style="dim")
        for index, template in enumerate(self._templates):
            selected = index == self._index
            name_style = "reverse" if selected else "bold"
            detail_style = "reverse" if selected else "dim"
            text.append(template.name, style=name_style)
            text.append("\n")
            text.append(template.spec, style=detail_style)
            if index < len(self._templates) - 1:
                text.append("\n")
        self._list_view.update(text)


class HistoryViewer(Screen[str | None]):
    """Keyboard-first history picker."""

    BINDINGS = [
        Binding("escape", "cancel", "Back", priority=True, show=False),
        Binding("enter", "select_current", "Load", priority=True, show=False),
        Binding("up", "cursor_up", "Up", priority=True, show=False),
        Binding("down", "cursor_down", "Down", priority=True, show=False),
        Binding("k", "cursor_up", "Up", priority=True, show=False),
        Binding("j", "cursor_down", "Down", priority=True, show=False),
        Binding("delete", "delete_entry", "Delete", priority=True, show=False),
        Binding("d", "delete_entry", "Delete", priority=True, show=False),
    ]

    def __init__(self, entries: list[history.HistoryEntry]) -> None:
        super().__init__()
        self._entries = entries
        self._list_view: KeyboardPickerList | None = None
        self._index = 0
        self._status: Static | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="history-layout"):
            yield Static("History", id="history-title")
            self._list_view = KeyboardPickerList("", id="history-list")
            yield self._list_view
            self._status = Static("", id="history-status")
            yield self._status
        yield Static("Up/Down choose | Enter load | D delete | Esc", id="history-help")

    def on_mount(self) -> None:  # type: ignore[override]
        if self._list_view:
            self._index = 0
            self._refresh_history_content()
            self._list_view.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_up(self) -> None:
        self._move_selection(-1)

    def action_cursor_down(self) -> None:
        self._move_selection(+1)

    def action_select_current(self) -> None:
        if not self._entries:
            self.dismiss(None)
            return
        if 0 <= self._index < len(self._entries):
            self.dismiss(self._entries[self._index].command)
        else:
            self.dismiss(None)

    def _move_selection(self, delta: int) -> None:
        if not self._entries:
            return
        self._index = (self._index + delta) % len(self._entries)
        self._refresh_history_content()

    def action_delete_entry(self) -> None:
        if not self._entries:
            self._update_status("No entries to delete.")
            return
        index = self._index
        if not history.delete_entry(index):
            self._update_status("Delete failed.")
            return
        del self._entries[index]
        if self._entries:
            self._index = min(index, len(self._entries) - 1)
        else:
            self._index = 0
        self._refresh_history_content()
        self._update_status("Deleted." if self._entries else "History cleared.")

    def _refresh_history_content(self) -> None:
        if not self._list_view:
            return
        text = Text()
        if not self._entries:
            text.append("No history yet.", style="dim")
            self._list_view.update(text)
            return
        for idx, entry in enumerate(self._entries, start=1):
            parsed = parse_prepare_history(entry.command)
            selected = (idx - 1) == self._index
            name_style = "reverse" if selected else "bold"
            detail_style = "reverse" if selected else "dim"
            if parsed:
                text.append(f"{idx}. {parsed.input_file}", style=name_style)
            else:
                text.append(f"{idx}. {entry.command}", style=name_style)
            text.append("\n")
            text.append(entry.command, style=detail_style)
            if idx < len(self._entries):
                text.append("\n")
        self._list_view.update(text)

    def _update_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)


class PrepareCommandPrompt(App[PromptResult]):
    """Minimal, keyboard-first cactus-prepare setup UI."""

    CSS = """
    Screen {
        layout: vertical;
        min-height: 0;
    }
    #prepare-layout {
        layout: vertical;
        padding: 1 2;
        min-height: 0;
        width: 1fr;
        height: 1fr;
    }
    .field-label {
        padding-top: 1;
        color: $text-muted;
    }
    Input {
        width: 1fr;
    }
    #candidate-panel, #advanced-candidate-panel {
        min-height: 0;
        max-height: 8;
        padding: 0 1;
        border-left: solid $accent;
    }
    #defaults-row {
        layout: horizontal;
        height: auto;
        width: 1fr;
        min-height: 0;
    }
    #output-block, #jobstore-block {
        layout: vertical;
        width: 1fr;
        height: auto;
        min-height: 0;
    }
    #output-block {
        padding-right: 1;
    }
    #jobstore-block {
        padding-left: 1;
    }
    #command-preview {
        padding-top: 1;
        height: 2;
        overflow: hidden;
        color: $text-muted;
    }
    #status {
        padding-top: 1;
        min-height: 1;
    }
    #prepare-help {
        dock: bottom;
        height: 1;
        width: 100%;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }
    .hidden {
        display: none;
    }
    #template-layout, #history-layout {
        layout: vertical;
        height: 1fr;
        padding: 1 2;
        min-height: 0;
    }
    #template-title, #history-title {
        text-style: bold;
        color: $accent;
        padding-bottom: 1;
    }
    #template-list, #history-list {
        height: 1fr;
        min-height: 0;
    }
    #history-status {
        padding-top: 1;
    }
    #template-help, #history-help {
        dock: bottom;
        height: 1;
        width: 100%;
        padding: 0 2;
        background: $panel;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "quit", "Quit", show=False),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("ctrl+n", "focus_next_field", "Next field", priority=True, show=False),
        Binding("ctrl+p", "focus_previous_field", "Previous field", priority=True, show=False),
        Binding("up", "arrow_focus_up", "Field up", priority=True, show=False),
        Binding("down", "arrow_focus_down", "Field down", priority=True, show=False),
        Binding("left", "arrow_focus_left", "Field left", priority=True, show=False),
        Binding("right", "arrow_focus_right", "Field right", priority=True, show=False),
        Binding("pageup", "candidate_page_up", "Candidate page up", priority=True, show=False),
        Binding("pagedown", "candidate_page_down", "Candidate page down", priority=True, show=False),
        Binding("home", "candidate_first", "First candidate", priority=True, show=False),
        Binding("end", "candidate_last", "Last candidate", priority=True, show=False),
        Binding("f3", "choose_template", "Templates", priority=True, show=False),
        Binding("f4", "show_history", "History", priority=True, show=False),
        Binding("ctrl+space", "show_path_candidates", "Files", show=False),
        Binding("ctrl+r", "submit_raw_command", "Raw command", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.title = "CAX"
        self.sub_title = "Prepare"
        self._templates = templates.load_templates()
        self._history_entries = history.load_history()
        self._prepare_options = _load_prepare_options()
        self._input_file: Input | None = None
        self._output_dir: Input | None = None
        self._job_store: Input | None = None
        self._advanced_args: Input | None = None
        self._candidate_panel: Static | None = None
        self._advanced_candidate_panel: Static | None = None
        self._preview: Static | None = None
        self._status: Static | None = None
        self._help: Static | None = None
        self._visible_candidates: list[PathCandidate] = []
        self._candidate_index = 0
        self._candidate_offset = 0
        self._previous_defaults: PrepareDefaults | None = None
        self._output_touched = False
        self._jobstore_touched = False
        self._advanced_touched = False
        self._applying_defaults = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="prepare-layout"):
            yield Static("Input file", classes="field-label")
            self._input_file = Input(placeholder="examples/evolverMammals.txt", id="input-file")
            yield self._input_file
            self._candidate_panel = Static("", id="candidate-panel", classes="hidden")
            yield self._candidate_panel
            with Container(id="defaults-row"):
                with Container(id="output-block"):
                    yield Static("Output directory", classes="field-label")
                    self._output_dir = Input(id="output-dir")
                    yield self._output_dir
                with Container(id="jobstore-block"):
                    yield Static("Temporary/jobStore directory", classes="field-label")
                    self._job_store = Input(id="job-store")
                    yield self._job_store
            yield Static("Other prepare advanced args", classes="field-label")
            self._advanced_args = Input(placeholder="Tab for options from cactus-prepare --help", id="advanced-args")
            yield self._advanced_args
            self._advanced_candidate_panel = Static("", id="advanced-candidate-panel", classes="hidden")
            yield self._advanced_candidate_panel
            self._preview = Static("", id="command-preview")
            yield self._preview
            self._status = Static("", id="status")
            yield self._status
        self._help = Static(self._idle_help_text(), id="prepare-help")
        yield self._help

    def on_mount(self) -> None:  # type: ignore[override]
        default_input = self._default_input_file()
        if self._input_file:
            self._input_file.value = default_input
            self._input_file.focus()
            self._input_file.cursor_position = len(default_input)
        self._apply_input_defaults(force=True)
        self._hide_candidates()

    def on_key(self, event: Key) -> None:  # type: ignore[override]
        if event.key == "tab":
            event.prevent_default()
            event.stop()
            if self.focused is self._input_file:
                self.action_complete_path()
            elif self.focused is self._advanced_args:
                self.action_complete_advanced_args()
            else:
                self.action_focus_next_field()
            return
        if event.key in {"shift+tab", "backtab"}:
            event.prevent_default()
            event.stop()
            self.action_focus_previous_field()
            return
        if event.key == "enter" and self._candidates_visible():
            event.prevent_default()
            event.stop()
            self._accept_candidate()
            return
        if event.key in {"pageup", "page_up"} and self._candidates_visible():
            event.prevent_default()
            event.stop()
            self.action_candidate_page_up()
            return
        if event.key in {"pagedown", "page_down"} and self._candidates_visible():
            event.prevent_default()
            event.stop()
            self.action_candidate_page_down()
            return
        if event.key == "home" and self._candidates_visible():
            event.prevent_default()
            event.stop()
            self.action_candidate_first()
            return
        if event.key == "end" and self._candidates_visible():
            event.prevent_default()
            event.stop()
            self.action_candidate_last()
            return

    def on_input_changed(self, event: Input.Changed) -> None:  # type: ignore[override]
        if self._applying_defaults:
            return
        if event.input is self._input_file:
            self._apply_input_defaults()
            self._refresh_candidates(only_if_visible=True)
        elif event.input is self._output_dir:
            self._output_touched = True
            self._sync_output_dependent_defaults()
        elif event.input is self._job_store:
            self._jobstore_touched = True
        elif event.input is self._advanced_args:
            self._advanced_touched = True
            self._refresh_advanced_candidates(only_if_visible=True)
        self._refresh_preview()

    def on_input_submitted(self, event: Input.Submitted) -> None:  # type: ignore[override]
        event.stop()
        if self._candidates_visible():
            self._accept_candidate()
            return
        self.action_submit()

    def action_quit(self) -> None:
        self.exit(PromptResult(executable="", args="", action="quit"))

    def action_show_path_candidates(self) -> None:
        self._refresh_candidates(force=True)

    def action_focus_next_field(self) -> None:
        self._focus_relative_field(+1)

    def action_focus_previous_field(self) -> None:
        self._focus_relative_field(-1)

    def action_arrow_focus_up(self) -> None:
        picker = self._active_picker_screen()
        if picker:
            picker.action_cursor_up()
            return
        if self._candidates_visible():
            self._move_candidate(-1)
            return
        self._focus_arrow_field("up")

    def action_arrow_focus_down(self) -> None:
        picker = self._active_picker_screen()
        if picker:
            picker.action_cursor_down()
            return
        if self._candidates_visible():
            self._move_candidate(+1)
            return
        self._focus_arrow_field("down")

    def action_arrow_focus_left(self) -> None:
        self._focus_arrow_field("left")

    def action_arrow_focus_right(self) -> None:
        self._focus_arrow_field("right")

    def _active_picker_screen(self) -> TemplateSelector | HistoryViewer | None:
        screen = self.screen
        if isinstance(screen, (TemplateSelector, HistoryViewer)):
            return screen
        return None

    def action_candidate_page_up(self) -> None:
        self._move_candidate_page(-1)

    def action_candidate_page_down(self) -> None:
        self._move_candidate_page(+1)

    def action_candidate_first(self) -> None:
        if self._visible_candidates:
            self._set_candidate_index(0)

    def action_candidate_last(self) -> None:
        if self._visible_candidates:
            self._set_candidate_index(len(self._visible_candidates) - 1)

    def action_complete_path(self) -> None:
        if not self._input_file:
            return
        current = self._input_file.value.strip()
        if current and _is_valid_seqfile(current):
            self.action_focus_next_field()
            return
        candidates = self._build_path_candidates(self._input_file.value)
        if not candidates:
            self._hide_candidates()
            self._update_status("No matching input files.")
            return
        values = [candidate.value for candidate in candidates]
        prefix = _common_prefix(values)
        current = self._input_file.value
        if prefix and len(prefix) > len(current):
            self._set_input_value(prefix)
            self._visible_candidates = self._build_path_candidates(prefix)
            self._candidate_index = 0
            self._candidate_offset = 0
            self._render_candidates()
            return
        self._visible_candidates = candidates
        self._candidate_index = 0
        self._candidate_offset = 0
        self._render_candidates()

    def action_complete_advanced_args(self) -> None:
        if not self._advanced_args:
            return
        candidates = self._build_advanced_candidates(self._advanced_args.value)
        if not candidates:
            self._hide_candidates()
            self._update_status("No matching cactus-prepare options.")
            return
        self._visible_candidates = candidates
        self._candidate_index = 0
        self._candidate_offset = 0
        self._render_candidates()

    def action_choose_template(self) -> None:
        if not self._templates:
            self._update_status("No templates available.")
            return
        self.push_screen(TemplateSelector(self._templates), self._template_chosen)

    def action_show_history(self) -> None:
        self._history_entries = history.load_history()
        if not self._history_entries:
            self._update_status("No history available.")
            return
        self.push_screen(HistoryViewer(self._history_entries), self._history_selected)

    def action_submit_raw_command(self) -> None:
        if not self._advanced_args:
            return
        result = _parse_prepare_command(self._advanced_args.value.strip())
        if result is None:
            self._update_status("Raw command must start with cactus-prepare.")
            return
        self.exit(result)

    def action_submit(self) -> None:
        state = self._current_state()
        if not state.input_file:
            self._update_status("Input file is required.")
            if self._input_file:
                self._input_file.focus()
            return
        if not _is_valid_seqfile(state.input_file):
            self._update_status("Input file must be a species/seq file whose first non-empty line is Newick.")
            if self._input_file:
                self._input_file.focus()
            return
        result = build_prepare_command(state)
        if result is None:
            self._update_status("Advanced arguments could not be parsed.")
            if self._advanced_args:
                self._advanced_args.focus()
            return
        self.exit(result)

    def _default_input_file(self) -> str:
        for entry in self._history_entries:
            parsed = parse_prepare_history(entry.command)
            if parsed and _is_valid_seqfile(parsed.input_file):
                return parsed.input_file
        if self._templates:
            for template in self._templates:
                if _is_valid_seqfile(template.spec):
                    return template.spec
        candidates = self._build_path_candidates("")
        return candidates[0].value if candidates else ""

    def _current_state(self) -> PrepareDefaults:
        input_file = self._input_file.value.strip() if self._input_file else ""
        defaults = build_prepare_defaults(input_file) if input_file else build_prepare_defaults("run")
        return PrepareDefaults(
            input_file=input_file,
            output_dir=(self._output_dir.value.strip() if self._output_dir else "") or defaults.output_dir,
            out_seq=_out_seq_for(
                (self._output_dir.value.strip() if self._output_dir else "") or defaults.output_dir,
                input_file or defaults.input_file,
            ),
            out_hal=_out_hal_for(
                (self._output_dir.value.strip() if self._output_dir else "") or defaults.output_dir,
                input_file or defaults.input_file,
            ),
            job_store=(self._job_store.value.strip() if self._job_store else "") or defaults.job_store,
            advanced_args=self._advanced_args.value.strip() if self._advanced_args else "",
        )

    def _focusable_fields(self) -> list[Input]:
        return [
            field
            for field in (self._input_file, self._output_dir, self._job_store, self._advanced_args)
            if field is not None
        ]

    def _focus_relative_field(self, delta: int) -> None:
        fields = self._focusable_fields()
        if not fields:
            return
        try:
            index = fields.index(self.focused)  # type: ignore[arg-type]
        except ValueError:
            index = 0
        target = fields[(index + delta) % len(fields)]
        target.focus()
        target.cursor_position = len(target.value)
        self._hide_candidates()

    def _focus_arrow_field(self, direction: str) -> None:
        current = self.focused
        target: Input | None = None
        if direction == "down":
            if current is self._input_file:
                target = self._output_dir
            elif current in {self._output_dir, self._job_store}:
                target = self._advanced_args
        elif direction == "up":
            if current is self._advanced_args:
                target = self._output_dir
            elif current in {self._output_dir, self._job_store}:
                target = self._input_file
        elif direction == "right":
            if current is self._input_file:
                target = self._output_dir
            elif current is self._output_dir:
                target = self._job_store
            elif current is self._job_store:
                target = self._advanced_args
        elif direction == "left":
            if current is self._advanced_args:
                target = self._job_store
            elif current is self._job_store:
                target = self._output_dir
            elif current is self._output_dir:
                target = self._input_file
        if target is None:
            return
        target.focus()
        target.cursor_position = len(target.value)
        self._hide_candidates()

    def _apply_input_defaults(self, *, force: bool = False) -> None:
        if not self._input_file or not self._input_file.value.strip():
            self._refresh_preview()
            return
        current = build_prepare_defaults(self._input_file.value.strip())
        previous = self._previous_defaults
        self._applying_defaults = True
        try:
            if self._output_dir and (
                force
                or not self._output_touched
                or (previous is not None and self._output_dir.value.strip() == previous.output_dir)
            ):
                self._output_dir.value = current.output_dir
                self._output_touched = False
            if self._job_store and (
                force
                or not self._jobstore_touched
                or (previous is not None and self._job_store.value.strip() == previous.job_store)
            ):
                output_dir = self._output_dir.value.strip() if self._output_dir else current.output_dir
                self._job_store.value = str(Path(output_dir).expanduser() / "jobstore")
                self._jobstore_touched = False
        finally:
            self._applying_defaults = False
        self._previous_defaults = self._current_state()
        self._refresh_preview()

    def _sync_output_dependent_defaults(self) -> None:
        if not self._output_dir or not self._job_store:
            return
        previous = self._previous_defaults
        if not self._jobstore_touched or (previous is not None and self._job_store.value.strip() == previous.job_store):
            self._applying_defaults = True
            try:
                self._job_store.value = str(Path(self._output_dir.value.strip()).expanduser() / "jobstore")
                self._jobstore_touched = False
            finally:
                self._applying_defaults = False
        self._previous_defaults = self._current_state()

    def _template_chosen(self, template: templates.Template | None) -> None:
        if not template:
            return
        defaults = _defaults_from_template(template)
        self._apply_state(defaults)
        self._update_status(f"Loaded template: {template.name}")

    def _history_selected(self, command: str | None) -> None:
        if not command:
            return
        defaults = parse_prepare_history(command)
        if defaults is None:
            self._update_status("History command could not be parsed.")
            return
        if not _is_valid_seqfile(defaults.input_file):
            self._update_status("History input is not a valid species/seq file.")
            return
        self._apply_state(defaults)
        self._update_status("Loaded history command.")

    def _apply_state(self, state: PrepareDefaults) -> None:
        self._output_touched = True
        self._jobstore_touched = True
        self._advanced_touched = bool(state.advanced_args)
        self._applying_defaults = True
        try:
            if self._input_file:
                self._input_file.value = state.input_file
                self._input_file.cursor_position = len(state.input_file)
            if self._output_dir:
                self._output_dir.value = state.output_dir
            if self._job_store:
                self._job_store.value = state.job_store
            if self._advanced_args:
                self._advanced_args.value = state.advanced_args
        finally:
            self._applying_defaults = False
        self._previous_defaults = None
        self._hide_candidates()
        self._refresh_preview()
        if self._input_file:
            self._input_file.focus()

    def _set_input_value(self, value: str) -> None:
        if not self._input_file:
            return
        self._applying_defaults = True
        try:
            self._input_file.value = value
            self._input_file.cursor_position = len(value)
        finally:
            self._applying_defaults = False
        self._apply_input_defaults()

    def _build_path_candidates(self, query: str) -> list[PathCandidate]:
        seen: set[str] = set()
        candidates: list[PathCandidate] = []

        def add(candidate: PathCandidate) -> None:
            if candidate.value in seen:
                return
            if not _candidate_matches(candidate.value, query):
                return
            seen.add(candidate.value)
            candidates.append(candidate)

        parent, base, prefix = _path_query_parts(query.strip())
        try:
            children = sorted(parent.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError:
            children = []
        for child in children:
            if base and not child.name.lower().startswith(base.lower()):
                continue
            if child.is_dir():
                value = f"{prefix}{child.name}/"
                add(PathCandidate(value=value, label=value, source="dir", is_dir=True))
            elif _looks_like_input_file(child):
                value = f"{prefix}{child.name}"
                add(PathCandidate(value=value, label=value, source="file"))

        for path in _example_input_paths():
            value = _display_candidate_path(path)
            add(PathCandidate(value=value, label=value, source="example"))

        for template in self._templates:
            if _is_valid_seqfile(template.spec):
                add(PathCandidate(value=template.spec, label=template.name, source="template"))

        for entry in self._history_entries:
            parsed = parse_prepare_history(entry.command)
            if parsed and _is_valid_seqfile(parsed.input_file):
                add(PathCandidate(value=parsed.input_file, label=parsed.input_file, source="history"))

        return candidates[:MAX_CANDIDATES]

    def _build_advanced_candidates(self, raw: str) -> list[PathCandidate]:
        prefix, token = _advanced_token_parts(raw)
        query = token.strip()
        if query and not query.startswith("-"):
            return []
        candidates: list[PathCandidate] = []
        for option in self._prepare_options:
            if query and not option.flag.startswith(query):
                continue
            value = f"{prefix}{option.flag} "
            candidates.append(PathCandidate(value=value, label=option.label, source="arg"))
        return candidates

    def _refresh_advanced_candidates(self, *, only_if_visible: bool = False) -> None:
        if only_if_visible and not self._candidates_visible(source="arg"):
            return
        if not self._advanced_args:
            return
        candidates = self._build_advanced_candidates(self._advanced_args.value)
        if not candidates:
            self._hide_candidates()
            return
        self._visible_candidates = candidates
        self._candidate_index = min(self._candidate_index, max(0, len(candidates) - 1))
        self._ensure_candidate_visible()
        self._render_candidates()

    def _refresh_candidates(self, *, force: bool = False, only_if_visible: bool = False) -> None:
        if only_if_visible and not self._candidates_visible(source="path"):
            return
        if not self._input_file:
            return
        candidates = self._build_path_candidates(self._input_file.value)
        if not candidates and not force:
            self._hide_candidates()
            return
        self._visible_candidates = candidates
        self._candidate_index = min(self._candidate_index, max(0, len(candidates) - 1))
        self._ensure_candidate_visible()
        self._render_candidates()

    def _render_candidates(self) -> None:
        panel = self._candidate_panel_for_current_source()
        inactive_panel = self._advanced_candidate_panel if panel is self._candidate_panel else self._candidate_panel
        if inactive_panel:
            inactive_panel.update("")
            inactive_panel.add_class("hidden")
        if not panel:
            return
        if not self._visible_candidates:
            panel.update("")
            panel.add_class("hidden")
            return
        self._ensure_candidate_visible()
        text = Text()
        start = self._candidate_offset
        end = min(len(self._visible_candidates), start + CANDIDATE_PAGE_SIZE)
        for idx in range(start, end):
            candidate = self._visible_candidates[idx]
            marker = "> " if idx == self._candidate_index else "  "
            style = "reverse" if idx == self._candidate_index else ""
            text.append(marker + candidate.label, style=style)
            text.append(f"  {candidate.source}", style="dim" if not style else style)
            if idx < end - 1:
                text.append("\n")
        if len(self._visible_candidates) > CANDIDATE_PAGE_SIZE:
            text.append("\n")
            text.append(
                f"{start + 1}-{end}/{len(self._visible_candidates)}  PgUp/PgDn page  Home/End jump",
                style="dim",
            )
        panel.update(text)
        panel.remove_class("hidden")
        self._refresh_help()

    def _hide_candidates(self) -> None:
        self._visible_candidates = []
        self._candidate_index = 0
        self._candidate_offset = 0
        if self._candidate_panel:
            self._candidate_panel.update("")
            self._candidate_panel.add_class("hidden")
        if self._advanced_candidate_panel:
            self._advanced_candidate_panel.update("")
            self._advanced_candidate_panel.add_class("hidden")
        self._refresh_help()

    def _idle_help_text(self) -> str:
        return "Tab complete | Enter start | F3 templates | F4 history | Esc"

    def _candidate_help_text(self) -> str:
        parts = ["Up/Down choose", "Enter accept", "Tab complete"]
        if len(self._visible_candidates) > CANDIDATE_PAGE_SIZE:
            parts.extend(["PgUp/PgDn page", "Home/End jump"])
        parts.append("Esc quit")
        return " | ".join(parts)

    def _refresh_help(self) -> None:
        if self._help:
            self._help.update(self._candidate_help_text() if self._visible_candidates else self._idle_help_text())

    def _candidate_panel_for_current_source(self) -> Static | None:
        if self._visible_candidates and self._visible_candidates[0].source == "arg":
            return self._advanced_candidate_panel
        return self._candidate_panel

    def _candidates_visible(self, source: str | None = None) -> bool:
        if not self._visible_candidates:
            return False
        if source is None:
            return True
        if source == "path":
            return self._visible_candidates[0].source in PATH_CANDIDATE_SOURCES
        return self._visible_candidates[0].source == source

    def _move_candidate(self, delta: int) -> None:
        if not self._visible_candidates:
            return
        self._set_candidate_index((self._candidate_index + delta) % len(self._visible_candidates))

    def _move_candidate_page(self, delta: int) -> None:
        if not self._visible_candidates:
            return
        target = self._candidate_index + (delta * CANDIDATE_PAGE_SIZE)
        self._set_candidate_index(max(0, min(len(self._visible_candidates) - 1, target)))

    def _set_candidate_index(self, index: int) -> None:
        self._candidate_index = max(0, min(len(self._visible_candidates) - 1, index))
        self._ensure_candidate_visible()
        self._render_candidates()

    def _ensure_candidate_visible(self) -> None:
        if not self._visible_candidates:
            self._candidate_offset = 0
            return
        if self._candidate_index < self._candidate_offset:
            self._candidate_offset = self._candidate_index
        elif self._candidate_index >= self._candidate_offset + CANDIDATE_PAGE_SIZE:
            self._candidate_offset = self._candidate_index - CANDIDATE_PAGE_SIZE + 1
        max_offset = max(0, len(self._visible_candidates) - CANDIDATE_PAGE_SIZE)
        self._candidate_offset = max(0, min(self._candidate_offset, max_offset))

    def _accept_candidate(self) -> None:
        if not self._visible_candidates:
            return
        candidate = self._visible_candidates[self._candidate_index]
        if candidate.source == "arg":
            if self._advanced_args:
                self._applying_defaults = True
                try:
                    self._advanced_args.value = candidate.value
                    self._advanced_args.cursor_position = len(candidate.value)
                finally:
                    self._applying_defaults = False
                self._advanced_args.focus()
                self._refresh_preview()
            self._hide_candidates()
            return
        self._set_input_value(candidate.value)
        if candidate.is_dir:
            self._refresh_candidates(force=True)
        else:
            self._hide_candidates()

    def _refresh_preview(self) -> None:
        if not self._preview:
            return
        state = self._current_state()
        input_label = state.input_file or "<input>"
        output_label = state.output_dir or "<out>"
        advanced_label = f" + {state.advanced_args}" if state.advanced_args else ""
        self._preview.update(f"Preview: cactus-prepare {input_label} -> {output_label}{advanced_label}")

    def _update_status(self, message: str) -> None:
        if self._status:
            self._status.update(message)


def _defaults_from_template(template: templates.Template) -> PrepareDefaults:
    params = template.params
    generated = build_prepare_defaults(template.spec)
    output_dir = params.get("out_dir") or generated.output_dir
    return PrepareDefaults(
        input_file=template.spec,
        output_dir=output_dir,
        out_seq=params.get("out_seq") or _out_seq_for(output_dir, template.spec),
        out_hal=params.get("out_hal") or _out_hal_for(output_dir, template.spec),
        job_store=params.get("job_store") or str(Path(output_dir).expanduser() / "jobstore"),
        advanced_args=params.get("extra", ""),
    )


def _example_input_paths() -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for base_dir in (Path("examples"), templates.PACKAGE_EXAMPLE_DIR):
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.glob("*.txt")):
            key = path.name
            if key in seen:
                continue
            if not _is_valid_seqfile(path):
                continue
            seen.add(key)
            paths.append(path)
    return paths
