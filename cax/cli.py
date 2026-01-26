"""Typer-powered command line interface for the streamlined CAX toolkit."""
from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
from typing import Optional

import typer
from rich import print
import shutil

from . import command_prompt, history, mash_auto as mash_auto_module, parser, seq_cache, templates, ui as ui_module
from .models import Plan, RunSettings
from .runner import PlanRunner

app = typer.Typer(help="Cactus-RaMAx interactive tools (ui only)")


def _load_prepare_text(
    prepare_args: Optional[str],
    from_file: Optional[Path],
    executable: str = "cactus-prepare",
) -> str:
    if prepare_args is not None:
        cmd = [executable, *shlex.split(prepare_args)]
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
            typer.echo(result.stdout)
            typer.echo(result.stderr, err=True)
            raise typer.Exit(code=result.returncode)
        output = result.stdout or ""
        history.add_command(shlex.join(cmd))
        tokens = cmd[1:]
        out_dir_path = _discover_out_dir(tokens)
        if out_dir_path is None:
            out_dir_path = Path("steps-output")
        out_dir_path.mkdir(exist_ok=True, parents=True)
        debug_path = out_dir_path / "cax_prepare_debug.txt"
        debug_path.write_text(output, encoding="utf-8")
        return output
    if from_file:
        return Path(from_file).read_text(encoding="utf-8")
    typer.echo("Either --prepare-args or --from-file must be provided.", err=True)
    raise typer.Exit(code=1)


def _build_prepare_args_from_seqfile(seqfile: Path) -> str:
    seqfile = seqfile.expanduser()
    stem = seqfile.stem or "run"
    out_dir = templates.default_output_dir(stem)
    out_seq = out_dir / f"{stem}.txt"
    out_hal = out_dir / f"{stem}.hal"
    tokens = [
        str(seqfile),
        "--outDir",
        str(out_dir),
        "--outSeqFile",
        str(out_seq),
        "--outHal",
        str(out_hal),
        "--jobStore",
        "jobstore",
    ]
    return shlex.join(tokens)


def _ensure_single_input(
    prepare_args: Optional[str],
    from_file: Optional[Path],
    seqfile: Optional[Path],
) -> str:
    provided = [prepare_args is not None, from_file is not None, seqfile is not None]
    if sum(provided) != 1:
        typer.echo("Provide exactly one of --prepare-args, --from-file, or --seqfile.", err=True)
        raise typer.Exit(code=1)
    if prepare_args is not None:
        return "prepare_args"
    if from_file is not None:
        return "from_file"
    return "seqfile"


@app.command()
def ui(
    prepare_args: Optional[str] = typer.Option(None, help="Arguments passed through to cactus-prepare"),
    from_file: Optional[Path] = typer.Option(None, help="Parse prepare output from an existing file"),
    run_after: bool = typer.Option(False, help="Run the plan after exiting the UI"),
    threads: Optional[int] = typer.Option(
        None,
        min=1,
        help="Override cactus/RaMAx thread count for all steps (leave unset for command defaults)",
    ),
    mash_auto: bool = typer.Option(
        True,
        "--mash-auto/--no-mash-auto",
        help=(
            "Preselect RaMAx rounds using Mash distance (requires mash on PATH; uses -k 31 -s 20000). "
            "Uses tree-aware pairwise checks with early-stop and caches distances under the plan output directory."
        ),
    ),
    mash_threshold: float = typer.Option(
        0.02,
        min=0.0,
        max=1.0,
        help="Enable RaMAx when Mash distance <= threshold (default: 0.02).",
    ),
    ask_mash: bool = typer.Option(
        True,
        "--ask-mash/--no-ask-mash",
        help="Prompt before computing Mash distances (recommended for large trees).",
    ),
    cache_seqs: bool = typer.Option(
        False,
        "--cache-seqs/--no-cache-seqs",
        help=(
            "Download remote URLs in the cactus seq file(s) into a local cache and rewrite the plan to use cached inputs "
            "(covers both --outSeqFile and cactus-preprocess input seq files)."
        ),
    ),
) -> None:
    """Launch the interactive Textual UI for plan editing."""

    executable = "cactus-prepare"
    if prepare_args is None and from_file is None:
        prompt_result = command_prompt.prompt_prepare_command()
        if prompt_result.action == "quit":
            typer.echo("[cax] Cancelled.")
            return
        prepare_args = prompt_result.args
        executable = prompt_result.executable or executable

    out_dir_preview, job_store_preview = _prepare_plan_preview(executable, prepare_args, from_file)
    resume_preselected = _ensure_clean_environment(out_dir_preview, job_store_preview)
    text = _load_prepare_text(prepare_args, from_file, executable=executable)
    plan = parser.parse_prepare_script(text)

    base_dir = Path.cwd()
    out_seq_path = _resolve_path(plan.out_seq_file)
    preprocess_seq_file = seq_cache.find_preprocess_input_seq_file(plan, base_dir=base_dir)
    out_remote = seq_cache.count_remote_sources(out_seq_path)
    pre_remote = seq_cache.count_remote_sources(preprocess_seq_file) if preprocess_seq_file else 0

    remote_source = None
    remote_count = 0
    if out_remote:
        remote_source = "outSeqFile"
        remote_count = out_remote
    elif pre_remote:
        remote_source = "cactus-preprocess input seq file"
        remote_count = pre_remote

    if mash_auto and remote_count and not cache_seqs:
        cache_seqs = typer.confirm(
            f"[cax] Detected {remote_count} remote sequence URL(s) in {remote_source}. "
            "Cache/download them now to enable Mash distance and avoid repeated downloads?",
            default=True,
        )

    if cache_seqs:
        try:
            before_out_seq = plan.out_seq_file
            before_pre_seq = preprocess_seq_file
            summary = seq_cache.apply_sequence_cache(plan, base_dir=base_dir)
            if summary.rewritten:
                after_pre_seq = seq_cache.find_preprocess_input_seq_file(plan, base_dir=base_dir)
                detail = ""
                if before_out_seq != plan.out_seq_file:
                    detail = f"Using cached outSeqFile: {plan.out_seq_file}"
                elif before_pre_seq and after_pre_seq and before_pre_seq != after_pre_seq:
                    detail = f"Using cached cactus-preprocess input seq file: {after_pre_seq}"
                typer.echo(
                    f"[cax] Cached sequences: downloaded {summary.downloaded} file(s). "
                    f"Cache dir: {seq_cache.default_cache_dir()} | "
                    f"{detail or 'Cached seq file applied.'}"
                )
        except Exception as exc:
            typer.echo(f"[cax] Sequence cache failed: {exc}", err=True)

    if mash_auto:
        mash_path = shutil.which("mash")
        if not mash_path:
            typer.echo(
                "[cax] WARNING: Mash auto-selection is enabled, but `mash` was not found on PATH.\n"
                "  - Install Mash (e.g. conda/brew/apt) or make sure it's on PATH.\n"
                "  - Or re-run with `--no-mash-auto` to skip Mash defaults.",
                err=True,
            )
        else:
            preprocess_seq_file = seq_cache.find_preprocess_input_seq_file(plan, base_dir=base_dir)
            out_seq_path = _resolve_path(plan.out_seq_file)
            out_remote = seq_cache.count_remote_sources(out_seq_path)
            pre_remote = seq_cache.count_remote_sources(preprocess_seq_file) if preprocess_seq_file else 0
            remote_source = None
            remote_count = 0
            if out_remote:
                remote_source = "outSeqFile"
                remote_count = out_remote
            elif pre_remote:
                remote_source = "cactus-preprocess input seq file"
                remote_count = pre_remote

            do_mash = True
            if ask_mash:
                seq_for_estimate = preprocess_seq_file or out_seq_path
                leaf_count = _estimate_leaf_count(seq_for_estimate)
                pair_count = leaf_count * (leaf_count - 1) // 2 if leaf_count >= 2 else 0
                cache_hint = Path(plan.out_dir or base_dir) / "logs"
                do_mash = typer.confirm(
                    f"[cax] Compute Mash distances now? (leaves={leaf_count}, max_pairs={pair_count}) "
                    f"Results are cached under: {cache_hint}",
                    default=True,
                )

            if do_mash:
                typer.echo(
                    f"[cax] Computing Mash distances (k=31, s=20000, threshold={mash_threshold:.4f})..."
                )
                summary = mash_auto_module.apply_mash_distance_defaults(
                    plan,
                    base_dir=base_dir,
                    threshold=mash_threshold,
                    sequence_file=preprocess_seq_file,
                )
                if summary.computed:
                    typer.echo(
                        f"[cax] Mash complete: computed {summary.computed} round(s), "
                        f"auto-enabled RaMAx for {summary.enabled_ramax}. "
                        f"(pairs: +{summary.pairwise_computed}, cached {summary.pairwise_cached})"
                    )
                else:
                    if remote_count and not cache_seqs:
                        typer.echo(
                            f"[cax] Mash skipped: {remote_source} references remote URLs. "
                            "Re-run with `--cache-seqs` to download/cache sequences first.",
                            err=True,
                        )
                    else:
                        typer.echo(
                            "[cax] Mash skipped: no eligible local sequences were found to compute distances.",
                            err=True,
                        )
            else:
                typer.echo("[cax] Skipped Mash distance computation. (You can still edit RaMAx selections manually.)")
    run_settings = RunSettings(
        verbose=False,
        thread_count=threads,
        resume=resume_preselected,
        mash_auto=mash_auto,
        mash_distance_threshold=mash_threshold,
    )

    # 若用户在启动时选择保留 run_state，UI 会自动进入续跑专属界面（可查看已完成/待执行并微调后续命令）。
    result = ui_module.launch(plan, run_settings=run_settings)
    plan = result.plan
    run_settings = result.run_settings or run_settings
    if result.action == "run" or run_after:
        if result.action != "run":
            run_settings = _prompt_run_settings(run_settings, plan)
        runner = PlanRunner(plan, run_settings=run_settings)
        runner.run()
    else:
        print(ui_module.plan_overview(plan, run_settings=run_settings))


@app.command("auto")
def auto(
    prepare_args: Optional[str] = typer.Option(None, help="Arguments passed through to cactus-prepare"),
    from_file: Optional[Path] = typer.Option(None, help="Parse prepare output from an existing file"),
    seqfile: Optional[Path] = typer.Option(
        None,
        "--seqfile",
        help="Shortcut: supply cactus-prepare spec/seq file and auto-generate output paths",
    ),
    threads: Optional[int] = typer.Option(
        None,
        min=1,
        help="Override cactus/RaMAx thread count for all steps (leave unset for command defaults)",
    ),
    mash_auto: bool = typer.Option(
        True,
        "--mash-auto/--no-mash-auto",
        help="Auto-select RaMAx using Mash distance (required in auto mode).",
    ),
    mash_threshold: float = typer.Option(
        0.02,
        min=0.0,
        max=1.0,
        help="Enable RaMAx when Mash distance <= threshold (default: 0.02).",
    ),
    ask_mash: bool = typer.Option(
        True,
        "--ask-mash/--no-ask-mash",
        help="Prompt before computing Mash distances (recommended for large trees).",
    ),
    cache_seqs: bool = typer.Option(
        False,
        "--cache-seqs/--no-cache-seqs",
        help=(
            "Download remote URLs in the cactus seq file(s) into a local cache and rewrite the plan to use cached inputs "
            "(covers both --outSeqFile and cactus-preprocess input seq files)."
        ),
    ),
) -> None:
    """Run cactus->RaMAx plan without launching the UI (Mash auto-selection required)."""

    input_kind = _ensure_single_input(prepare_args, from_file, seqfile)
    executable = "cactus-prepare"

    if input_kind == "seqfile":
        seqfile_path = Path(seqfile).expanduser() if seqfile else None
        if not seqfile_path or not seqfile_path.exists():
            typer.echo("[cax] --seqfile must point to an existing file.", err=True)
            raise typer.Exit(code=1)
        prepare_args = _build_prepare_args_from_seqfile(seqfile_path)

    if not mash_auto:
        typer.echo("[cax] Auto mode requires Mash auto-selection. Remove --no-mash-auto.", err=True)
        raise typer.Exit(code=1)

    mash_path = shutil.which("mash")
    if not mash_path:
        typer.echo(
            "[cax] Mash is required for auto mode, but `mash` was not found on PATH.\n"
            "  - Install Mash (e.g. conda/brew/apt) or make sure it's on PATH.",
            err=True,
        )
        raise typer.Exit(code=1)

    out_dir_preview, job_store_preview = _prepare_plan_preview(executable, prepare_args, from_file)
    resume_preselected = _ensure_clean_environment(out_dir_preview, job_store_preview)
    text = _load_prepare_text(prepare_args, from_file, executable=executable)
    plan = parser.parse_prepare_script(text)

    base_dir = Path.cwd()
    out_seq_path = _resolve_path(plan.out_seq_file)
    preprocess_seq_file = seq_cache.find_preprocess_input_seq_file(plan, base_dir=base_dir)
    out_remote = seq_cache.count_remote_sources(out_seq_path)
    pre_remote = seq_cache.count_remote_sources(preprocess_seq_file) if preprocess_seq_file else 0

    remote_source = None
    remote_count = 0
    if out_remote:
        remote_source = "outSeqFile"
        remote_count = out_remote
    elif pre_remote:
        remote_source = "cactus-preprocess input seq file"
        remote_count = pre_remote

    if remote_count and not cache_seqs:
        cache_seqs = typer.confirm(
            f"[cax] Detected {remote_count} remote sequence URL(s) in {remote_source}. "
            "Cache/download them now to enable Mash distance and avoid repeated downloads?",
            default=True,
        )
        if not cache_seqs:
            typer.echo("[cax] Auto mode requires Mash inputs. Re-run with --cache-seqs.", err=True)
            raise typer.Exit(code=1)

    if cache_seqs:
        try:
            before_out_seq = plan.out_seq_file
            before_pre_seq = preprocess_seq_file
            summary = seq_cache.apply_sequence_cache(plan, base_dir=base_dir)
            if summary.rewritten:
                after_pre_seq = seq_cache.find_preprocess_input_seq_file(plan, base_dir=base_dir)
                detail = ""
                if before_out_seq != plan.out_seq_file:
                    detail = f"Using cached outSeqFile: {plan.out_seq_file}"
                elif before_pre_seq and after_pre_seq and before_pre_seq != after_pre_seq:
                    detail = f"Using cached cactus-preprocess input seq file: {after_pre_seq}"
                typer.echo(
                    f"[cax] Cached sequences: downloaded {summary.downloaded} file(s). "
                    f"Cache dir: {seq_cache.default_cache_dir()} | "
                    f"{detail or 'Cached seq file applied.'}"
                )
        except Exception as exc:
            typer.echo(f"[cax] Sequence cache failed: {exc}", err=True)
        preprocess_seq_file = seq_cache.find_preprocess_input_seq_file(plan, base_dir=base_dir)
        out_seq_path = _resolve_path(plan.out_seq_file)

    do_mash = True
    if ask_mash:
        seq_for_estimate = preprocess_seq_file or out_seq_path
        leaf_count = _estimate_leaf_count(seq_for_estimate)
        pair_count = leaf_count * (leaf_count - 1) // 2 if leaf_count >= 2 else 0
        cache_hint = Path(plan.out_dir or base_dir) / "logs"
        do_mash = typer.confirm(
            f"[cax] Compute Mash distances now? (leaves={leaf_count}, max_pairs={pair_count}) "
            f"Results are cached under: {cache_hint}",
            default=True,
        )
        if not do_mash:
            typer.echo("[cax] Auto mode requires Mash distances. Re-run with --no-ask-mash.", err=True)
            raise typer.Exit(code=1)

    typer.echo(f"[cax] Computing Mash distances (k=31, s=20000, threshold={mash_threshold:.4f})...")
    summary = mash_auto_module.apply_mash_distance_defaults(
        plan,
        base_dir=base_dir,
        threshold=mash_threshold,
        sequence_file=preprocess_seq_file,
    )
    if summary.computed:
        typer.echo(
            f"[cax] Mash complete: computed {summary.computed} round(s), "
            f"auto-enabled RaMAx for {summary.enabled_ramax}. "
            f"(pairs: +{summary.pairwise_computed}, cached {summary.pairwise_cached})"
        )
    else:
        typer.echo(
            "[cax] Mash skipped: no eligible local sequences were found to compute distances.\n"
            "Auto mode requires Mash distances; re-run with local sequences or --cache-seqs.",
            err=True,
        )
        raise typer.Exit(code=1)

    run_settings = RunSettings(
        verbose=False,
        thread_count=threads,
        resume=resume_preselected,
        mash_auto=True,
        mash_distance_threshold=mash_threshold,
    )
    runner = PlanRunner(plan, run_settings=run_settings)
    runner.run()


if __name__ == "__main__":
    app()


def _prompt_run_settings(defaults: RunSettings, plan: Plan | None = None) -> RunSettings:
    """Collect run-time settings from the user just before execution."""

    typer.echo("[cax] Configure run settings before execution:")
    verbose = typer.confirm(
        "Enable verbose logging (stream every command output)?",
        default=defaults.verbose,
    )

    resume = typer.confirm(
        "Enable resume mode (record run state and skip successful steps next time)?",
        default=defaults.resume,
    )

    thread_count = defaults.thread_count
    while True:
        default_display = "" if thread_count is None else str(thread_count)
        prompt = typer.prompt(
            "Thread count for cactus/RaMAx (blank = auto)",
            default=default_display,
            show_default=bool(default_display),
        )
        stripped = prompt.strip()
        if not stripped:
            thread_count = None
            break
        try:
            value = int(stripped)
        except ValueError:
            typer.echo("[cax] Please enter a positive integer or leave blank.")
            continue
        if value <= 0:
            typer.echo("[cax] Thread count must be at least 1.")
            continue
        thread_count = value
        break

    settings = RunSettings(
        verbose=verbose,
        thread_count=thread_count,
        resume=resume,
        mash_auto=defaults.mash_auto,
        mash_distance_threshold=defaults.mash_distance_threshold,
    )

    return settings


def _prepare_plan_preview(
    executable: str,
    prepare_args: Optional[str],
    from_file: Optional[Path],
) -> tuple[Optional[str], Optional[str]]:
    """Return the prospective --outDir and --jobStore before running cactus-prepare."""

    if from_file:
        try:
            text = Path(from_file).read_text(encoding="utf-8")
            plan = parser.parse_prepare_script(text)
            return plan.out_dir, None
        except OSError:
            return None, None
    if prepare_args is None:
        return None, None
    tokens = shlex.split(prepare_args)
    out_dir_path = _discover_out_dir(tokens)
    out_dir = str(out_dir_path) if out_dir_path else None
    job_store = _extract_flag(tokens, "--jobStore") or _extract_flag(tokens, "--jobstore")
    # Some users may pass --jobStore=file:/path or jobstore=...; leave as-is for now.
    return out_dir, job_store


def _extract_flag(tokens: list[str], flag: str) -> Optional[str]:
    for idx, tok in enumerate(tokens):
        if tok == flag and idx + 1 < len(tokens):
            return tokens[idx + 1]
        if tok.startswith(flag + "="):
            return tok.split("=", 1)[1]
    return None


def _discover_out_dir(tokens: list[str]) -> Optional[Path]:
    """Infer the output directory from cactus-prepare style tokens."""

    out_dir = _extract_flag(tokens, "--outDir")
    if out_dir:
        return Path(out_dir).expanduser()
    out_seq = _extract_flag(tokens, "--outSeqFile")
    if out_seq:
        seq_path = Path(out_seq).expanduser()
        try:
            parent = seq_path.resolve().parent
        except OSError:
            parent = seq_path.parent
        return parent
    return None


def _ensure_clean_environment(out_dir: Optional[str], job_store: Optional[str]) -> bool:
    """Before running cactus-prepare, optionally clean existing output directories.

    返回值：True 表示用户选择保留现有目录以便断点续跑；False 表示已清理或无需保留。
    """

    candidates: list[Path] = []
    run_state_path: Optional[Path] = None
    if out_dir:
        out_path = _resolve_path(out_dir)
        run_state_path = out_path / "logs" / "run_state.json"
        candidates.append(out_path)
    if job_store:
        job_path = _resolve_path(job_store)
        candidates.append(job_path)
    # When no explicit jobStore is supplied, Toil uses subdirectories jobstore/0, etc.
    candidates.append(Path.cwd() / "jobstore")

    existing: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            existing.append(resolved)

    if not existing:
        return False

    resume_available = run_state_path is not None and run_state_path.exists()

    typer.echo("[cax] Detected existing paths:")
    for path in existing:
        try:
            relative = path.relative_to(Path.cwd())
            typer.echo(f"  - {relative}")
        except ValueError:
            typer.echo(f"  - {path}")

    if resume_available:
        if typer.confirm(
            "Found logs/run_state.json. Keep existing outputs to resume?",
            default=True,
        ):
            typer.echo("[cax] Keeping existing outputs; continuing.")
            return True
        typer.echo("[cax] Cleaning outputs and restarting.")
    else:
        typer.echo("[cax] No run_state.json found; cleaning old outputs and jobStore before starting.")

    for path in existing:
        if not path.exists():
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            typer.echo(f"[cax] Failed to remove {path}: {exc}")
    typer.echo("[cax] Cleanup complete.")
    return False


def _resolve_path(path_like: str) -> Path:
    if path_like.startswith("file:"):
        path_like = path_like.split(":", 1)[1]
    path = Path(path_like)
    if path.is_absolute():
        return path
    return (Path.cwd() / path).resolve()


def _estimate_leaf_count(seq_file: Path) -> int:
    """Best-effort leaf count estimator for an outSeqFile-style file."""

    try:
        lines = seq_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return 0
    idx += 1  # skip Newick line
    count = 0
    for line in lines[idx:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        count += 1
    return count
