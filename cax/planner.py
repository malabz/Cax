"""Translate plans into executable command sequences."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import List, Optional

from .models import Plan, Round, Step
from . import resources, tree_utils


@dataclass
class PlannedCommand:
    """Concrete command to be executed as part of a plan."""

    command: List[str]
    category: str
    display_name: str
    log_path: Optional[Path] = None
    round_name: Optional[str] = None
    step: Optional[Step] = None
    is_ramax: bool = False
    workdir: Optional[Path] = None

    def shell_preview(self) -> str:
        """Return a shell-friendly preview of the command."""

        return shlex.join(self.command)


def build_execution_plan(
    plan: Plan,
    base_dir: Optional[Path] = None,
    thread_count: Optional[int] = None,
    memory_limit_bytes: Optional[int] = None,
) -> list[PlannedCommand]:
    """Materialise the full list of commands that should be executed."""

    base_dir = base_dir or Path.cwd()
    commands: list[PlannedCommand] = []
    tree = tree_utils.build_alignment_tree(plan, base_dir=base_dir)

    for step in plan.preprocess:
        commands.append(
            _from_step(
                step,
                category="preprocess",
                base_dir=base_dir,
                thread_count=thread_count,
                memory_limit_bytes=memory_limit_bytes,
            )
        )

    for round_entry in plan.rounds:
        if _is_absorbed_by_subtree_ramax(round_entry, tree):
            # Ancestor subtree-mode RaMAx already covers this round; skip all steps here.
            continue
        if _is_descendant_ramax(round_entry, tree):
            # An ancestor already uses RaMAx; running it again here would be redundant.
            continue
        commands.extend(
            _round_commands(
                plan,
                round_entry,
                base_dir,
                thread_count,
                memory_limit_bytes,
            )
        )

    for step in plan.hal_merges:
        replacement = _ramax_halmerge_replacement(step, plan, base_dir)
        if replacement:
            commands.append(replacement)
            continue
        rewritten = _prune_covered_halmerge_inputs(step, plan, tree, base_dir)
        if rewritten:
            commands.append(
                _from_step(
                    rewritten,
                    category="halmerge",
                    base_dir=base_dir,
                    thread_count=thread_count,
                    memory_limit_bytes=memory_limit_bytes,
                )
            )
            continue
        if _skip_halmerge_for_ramax_parent(step, tree):
            continue
        commands.append(
            _from_step(
                step,
                category="halmerge",
                base_dir=base_dir,
                thread_count=thread_count,
                memory_limit_bytes=memory_limit_bytes,
            )
        )

    return commands


def _round_commands(
    plan: Plan,
    round_entry: Round,
    base_dir: Path,
    thread_count: Optional[int],
    memory_limit_bytes: Optional[int],
) -> list[PlannedCommand]:
    cmds: list[PlannedCommand] = []
    round_name = round_entry.name

    if round_entry.replace_with_ramax:
        cmds.append(
            _ramax_command(
                plan,
                round_entry,
                base_dir,
                thread_count,
            )
        )
    else:
        if round_entry.blast_step:
            cmds.append(
                _from_step(
                    round_entry.blast_step,
                    category="blast",
                    base_dir=base_dir,
                    round_name=round_name,
                    thread_count=thread_count,
                    memory_limit_bytes=memory_limit_bytes,
                )
            )
        if round_entry.align_step:
            cmds.append(
                _from_step(
                    round_entry.align_step,
                    category="align",
                    base_dir=base_dir,
                    round_name=round_name,
                    thread_count=thread_count,
                    memory_limit_bytes=memory_limit_bytes,
                )
            )

    for hal_step in round_entry.hal2fasta_steps:
        cmds.append(
            _from_step(
                hal_step,
                category="hal2fasta",
                base_dir=base_dir,
                round_name=round_name,
                thread_count=thread_count,
                memory_limit_bytes=memory_limit_bytes,
            )
        )

    return cmds


def _from_step(
    step: Step,
    category: str,
    base_dir: Path,
    round_name: Optional[str] = None,
    thread_count: Optional[int] = None,
    memory_limit_bytes: Optional[int] = None,
) -> PlannedCommand:
    command = _split_command(step.raw)
    if step.kind == "hal2fasta":
        command = _normalize_hal2fasta(command)
    command = _apply_cactus_resource_limits(
        command,
        thread_count=thread_count,
        memory_limit_bytes=memory_limit_bytes,
    )
    log_path = Path(step.log_file) if step.log_file else None
    display_name = step.short_label()
    return PlannedCommand(
        command=command,
        category=category,
        display_name=display_name,
        log_path=_resolve_path(log_path, base_dir) if log_path else None,
        round_name=round_name,
        step=step,
    )


SUBTREE_FLAG = "--subtree-mode"


def _ramax_command(
    plan: Plan,
    round_entry: Round,
    base_dir: Path,
    thread_count: Optional[int],
) -> PlannedCommand:
    workdir = round_entry.workdir
    if not workdir and plan.out_dir:
        workdir = str(Path(plan.out_dir) / "temps" / f"blast-{round_entry.root}")

    if round_entry.manual_ramax_command:
        command = _split_command(round_entry.manual_ramax_command)
    else:
        command = [
            "ramax",
            "-i",
            plan.out_seq_file,
            "-o",
            round_entry.target_hal,
            "--root",
            round_entry.root,
        ]
        if workdir:
            command.extend(["-w", workdir])
        command.extend(_filtered_ramax_opts(plan.global_ramax_opts))
        command.extend(_filtered_ramax_opts(round_entry.ramax_opts))
    command = _ensure_ramax_threads(command, thread_count)

    log_path = _guess_ramax_log_path(plan, round_entry, base_dir)

    workdir_path = Path(workdir).expanduser() if workdir else None
    if workdir_path and not workdir_path.is_absolute():
        workdir_path = (base_dir / workdir_path).resolve()

    # 为 RaMAx 命令补齐 out_files，使断点续跑能检查 HAL 产物是否存在。
    ramax_step = Step(
        raw=shlex.join(command),
        kind="ramax",
        out_files=[round_entry.target_hal],
        root=round_entry.root,
        log_file=str(log_path) if log_path else None,
    )
    return PlannedCommand(
        command=command,
        category="ramax",
        display_name=f"ramax-{round_entry.root}",
        log_path=log_path,
        round_name=round_entry.name,
        step=ramax_step,
        is_ramax=True,
        workdir=workdir_path,
    )


def _guess_ramax_log_path(plan: Plan, round_entry: Round, base_dir: Path) -> Optional[Path]:
    if round_entry.align_step and round_entry.align_step.log_file:
        align_log = Path(round_entry.align_step.log_file)
        ramax_name = align_log.name.replace("align", "ramax")
        return _resolve_path(align_log.with_name(ramax_name), base_dir)
    if plan.out_dir:
        return _resolve_path(Path(plan.out_dir) / "logs" / f"ramax-{round_entry.root}.log", base_dir)
    return None


def _resolve_path(path: Path, base_dir: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return (base_dir / expanded).resolve()


def _split_command(raw: str) -> List[str]:
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()


def _normalize_hal2fasta(command: List[str]) -> List[str]:
    """Normalize a hal2fasta invocation to avoid shell redirection.

    cactus-prepare emits commands like:
        hal2fasta in.hal Anc0 --hdf5InMemory > out.fa

    Since we execute with ``shell=False``, the '>' token is treated as an
    argument and hal2fasta fails. This helper converts the redirection to the
    explicit ``--outFaPath`` option that hal2fasta supports.
    """

    if ">" not in command and ">>" not in command:
        return command

    # Identify redirection token and the output path following it
    redirect_token = ">" if ">" in command else ">>"
    try:
        redirect_index = command.index(redirect_token)
    except ValueError:
        return command

    out_path = command[redirect_index + 1] if redirect_index + 1 < len(command) else None
    # Keep the main part of the command before redirection
    main = command[:redirect_index]

    # Remove any existing --outFaPath occurrences to avoid duplicates
    cleaned: List[str] = []
    skip_next = False
    for token in main:
        if skip_next:
            skip_next = False
            continue
        if token == "--outFaPath":
            skip_next = True
            continue
        cleaned.append(token)

    if out_path:
        cleaned.extend(["--outFaPath", out_path])
    return cleaned


def _apply_cactus_resource_limits(
    command: List[str],
    *,
    thread_count: Optional[int],
    memory_limit_bytes: Optional[int],
) -> List[str]:
    if not command:
        return command
    name = Path(command[0]).name
    if not name.startswith("cactus"):
        return command
    adjusted = list(command)
    if thread_count is not None:
        adjusted = _cap_integer_option(adjusted, "--maxCores", thread_count, ensure=True)
        adjusted = _cap_integer_option(adjusted, "--consCores", thread_count)
        adjusted = _cap_integer_option(adjusted, "--lastzCores", thread_count)
    if memory_limit_bytes is not None:
        adjusted = _cap_memory_option(
            adjusted,
            "--maxMemory",
            memory_limit_bytes,
            ensure=True,
        )
    return adjusted


def _filtered_ramax_opts(options: List[str]) -> List[str]:
    """Drop CAX-only sentinel flags before invoking ramax."""

    return [opt for opt in options if opt != SUBTREE_FLAG]


def _ensure_ramax_threads(command: List[str], thread_count: Optional[int]) -> List[str]:
    if thread_count is None or not command:
        return command
    name = Path(command[0]).name.lower()
    if name != "ramax":
        return command
    return _cap_integer_option(command, "--threads", thread_count, ensure=True)


def _cap_integer_option(
    command: List[str],
    flag: str,
    limit: int,
    *,
    ensure: bool = False,
) -> List[str]:
    adjusted = list(command)
    found = False
    index = 0
    while index < len(adjusted):
        token = adjusted[index]
        if token == flag:
            if index + 1 >= len(adjusted):
                raise resources.ResourceLimitError(f"{flag} requires a value.")
            found = True
            raw = adjusted[index + 1]
            try:
                value = int(raw)
            except ValueError as exc:
                raise resources.ResourceLimitError(
                    f"{flag} must be a positive integer, got {raw!r}."
                ) from exc
            if value <= 0:
                raise resources.ResourceLimitError(f"{flag} must be at least 1.")
            if value > limit:
                adjusted[index + 1] = str(limit)
            index += 2
            continue
        if token.startswith(flag + "="):
            found = True
            raw = token.split("=", 1)[1]
            try:
                value = int(raw)
            except ValueError as exc:
                raise resources.ResourceLimitError(
                    f"{flag} must be a positive integer, got {raw!r}."
                ) from exc
            if value <= 0:
                raise resources.ResourceLimitError(f"{flag} must be at least 1.")
            if value > limit:
                adjusted[index] = f"{flag}={limit}"
        index += 1
    if ensure and not found:
        adjusted.extend([flag, str(limit)])
    return adjusted


def _cap_memory_option(
    command: List[str],
    flag: str,
    limit: int,
    *,
    ensure: bool = False,
) -> List[str]:
    adjusted = list(command)
    found = False
    index = 0
    formatted_limit = resources.format_memory_size(limit)
    while index < len(adjusted):
        token = adjusted[index]
        if token == flag:
            if index + 1 >= len(adjusted):
                raise resources.ResourceLimitError(f"{flag} requires a value.")
            found = True
            raw = adjusted[index + 1]
            try:
                value = resources.parse_memory_size(raw)
            except ValueError as exc:
                raise resources.ResourceLimitError(
                    f"Invalid {flag} value {raw!r}."
                ) from exc
            if value > limit:
                adjusted[index + 1] = formatted_limit
            index += 2
            continue
        if token.startswith(flag + "="):
            found = True
            raw = token.split("=", 1)[1]
            try:
                value = resources.parse_memory_size(raw)
            except ValueError as exc:
                raise resources.ResourceLimitError(
                    f"Invalid {flag} value {raw!r}."
                ) from exc
            if value > limit:
                adjusted[index] = f"{flag}={formatted_limit}"
        index += 1
    if ensure and not found:
        adjusted.extend([flag, formatted_limit])
    return adjusted


def _has_flag(command: List[str], flag: str) -> bool:
    for token in command:
        if token == flag:
            return True
        if token.startswith(flag + "="):
            return True
    return False


def _ramax_halmerge_replacement(
    step: Step,
    plan: Plan,
    base_dir: Path,
) -> PlannedCommand | None:
    hal_paths = [path for path in step.out_files if path.endswith(".hal")]
    if len(hal_paths) < 2:
        return None

    source_hal = hal_paths[0]
    final_hal = hal_paths[-1]
    source_round = _round_for_hal(plan, source_hal, base_dir)
    if source_round is None or not source_round.replace_with_ramax:
        return None
    if _same_path(source_hal, final_hal, base_dir):
        return None

    command = ["cp", source_hal, final_hal]
    log_path = Path(step.log_file) if step.log_file else None
    copy_step = Step(
        raw=shlex.join(command),
        kind="halmerge",
        out_files=[final_hal],
        root=source_round.root,
        log_file=step.log_file,
    )
    return PlannedCommand(
        command=command,
        category="halmerge",
        display_name=f"halmerge-{source_round.root}",
        log_path=_resolve_path(log_path, base_dir) if log_path else None,
        round_name=source_round.name,
        step=copy_step,
    )


def _prune_covered_halmerge_inputs(
    step: Step,
    plan: Plan,
    tree: Optional[tree_utils.AlignmentTree],
    base_dir: Path,
) -> Step | None:
    if tree is None:
        return None

    command = _split_command(step.raw)
    hal_indices = [
        index
        for index, token in enumerate(command)
        if token.endswith(".hal")
    ]
    if len(hal_indices) < 3:
        return None

    input_hal_indices = hal_indices[:-1]
    input_hals = [command[index] for index in input_hal_indices]
    remove_indices: set[int] = set()
    for index in input_hal_indices[1:]:
        hal_path = command[index]
        if _is_hal_covered_by_ramax_input(hal_path, input_hals, plan, tree, base_dir):
            remove_indices.add(index)

    if not remove_indices:
        return None

    rewritten_command = [
        token
        for index, token in enumerate(command)
        if index not in remove_indices
    ]
    rewritten_out_files = [
        command[index]
        for index in hal_indices
        if index not in remove_indices
    ]
    return Step(
        raw=shlex.join(rewritten_command),
        kind=step.kind,
        jobstore=step.jobstore,
        out_files=rewritten_out_files,
        root=step.root,
        log_file=step.log_file,
        label=step.label,
    )


def _is_hal_covered_by_ramax_input(
    hal_path: str,
    input_hals: list[str],
    plan: Plan,
    tree: tree_utils.AlignmentTree,
    base_dir: Path,
) -> bool:
    round_entry = _round_for_hal(plan, hal_path, base_dir)
    if round_entry is None:
        return False
    if not (
        _is_descendant_ramax(round_entry, tree)
        or _is_absorbed_by_subtree_ramax(round_entry, tree)
    ):
        return False

    node = tree.find(round_entry.root)
    if node is None:
        return False
    ancestor = node.parent
    while ancestor:
        if ancestor.round and ancestor.round.replace_with_ramax:
            if any(
                _same_path(ancestor.round.target_hal, input_hal, base_dir)
                for input_hal in input_hals
            ):
                return True
        ancestor = ancestor.parent
    return False


def _round_for_hal(plan: Plan, hal_path: str, base_dir: Path) -> Round | None:
    for round_entry in plan.rounds:
        if _same_path(round_entry.target_hal, hal_path, base_dir):
            return round_entry
    return None


def _same_path(left: str, right: str, base_dir: Path) -> bool:
    if left == right:
        return True
    return _resolve_path(Path(left), base_dir) == _resolve_path(Path(right), base_dir)


def _is_descendant_ramax(round_entry: Round, tree: Optional[tree_utils.AlignmentTree]) -> bool:
    """Skip this round when any ancestor round already uses RaMAx and this round also requests RaMAx, avoiding duplicate alignments."""

    if tree is None or not round_entry.replace_with_ramax:
        return False
    node = tree.find(round_entry.root)
    if node is None:
        return False
    ancestor = node.parent
    while ancestor:
        if ancestor.round and ancestor.round.replace_with_ramax:
            return True
        ancestor = ancestor.parent
    return False


def _is_absorbed_by_subtree_ramax(
    round_entry: Round, tree: Optional[tree_utils.AlignmentTree]
) -> bool:
    """Return True when an ancestor round is in subtree-mode RaMAx, so this round should be skipped entirely.

    Subtree Mode represents "run RaMAx at this ancestor and absorb descendants". Descendants may have
    ``replace_with_ramax`` cleared to avoid mixed modes, but they should not execute their cactus steps
    either once the ancestor covers the subtree.
    """

    if tree is None:
        return False
    node = tree.find(round_entry.root)
    if node is None:
        return False
    ancestor = node.parent
    while ancestor:
        if ancestor.round and ancestor.round.replace_with_ramax:
            if SUBTREE_FLAG in ancestor.round.ramax_opts:
                return True
        ancestor = ancestor.parent
    return False


def _skip_halmerge_for_ramax_parent(step: Step, tree: Optional[tree_utils.AlignmentTree]) -> bool:
    """Skip halAppendSubtree when its parent round was produced by RaMAx to avoid writing HAL twice."""

    if tree is None or step.root is None:
        return _skip_halmerge_for_ramax_parent_fallback(step, tree)
    node = tree.find(step.root)
    if node is None:
        return False
    # Find the nearest ancestor with a round; that node is the halmerge target parent.
    parent = node.parent
    while parent and parent.round is None:
        parent = parent.parent
    if parent and parent.round and parent.round.replace_with_ramax:
        return True
    return False


def _skip_halmerge_for_ramax_parent_fallback(
    step: Step,
    tree: Optional[tree_utils.AlignmentTree],
) -> bool:
    if tree is None:
        return False
    first_hal = next((path for path in step.out_files if path.endswith(".hal")), None)
    if not first_hal:
        return False
    for round_entry in tree.iter_rounds():
        if round_entry.target_hal == first_hal:
            return round_entry.replace_with_ramax
        if Path(round_entry.target_hal).name == Path(first_hal).name:
            return round_entry.replace_with_ramax
    return False
