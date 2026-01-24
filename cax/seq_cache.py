"""Utilities for caching remote sequences referenced by cactus seq files.

Some cactus outSeqFiles reference remote FASTA/FASTQ URLs (e.g. GitHub raw links).
Downloading them ahead of time provides two benefits:

- Mash auto-selection can run locally without needing network-aware inputs.
- Subsequent cactus steps can reuse local files instead of repeatedly fetching URLs.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import shlex
from pathlib import Path
import urllib.parse
import urllib.request
from typing import Iterable, Optional

from .models import Plan, Step


@dataclass(frozen=True)
class SeqCacheSummary:
    downloaded: int
    rewritten: bool


def apply_sequence_cache(
    plan: Plan,
    *,
    base_dir: Optional[Path] = None,
    cache_dir: Optional[Path] = None,
) -> SeqCacheSummary:
    """Materialize remote sequences and rewrite the plan to use cached local files.

    This supports two common cases:
    1) ``plan.out_seq_file`` itself contains remote URLs (direct usage by blast/align steps).
       In that case we rewrite ``plan.out_seq_file`` to point at the cached version.
    2) ``plan.out_seq_file`` points at local paths that do not exist yet because they will be
       produced by ``cactus-preprocess``, while the preprocess input seq file contains URLs.
       In that case we cache the preprocess input seq file and rewrite the preprocess step
       to use the cached input, leaving ``plan.out_seq_file`` unchanged.
    """

    base_dir = Path(base_dir) if base_dir else Path.cwd()
    cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()

    original_seq = plan.out_seq_file
    original_seq_path = _resolve_path(original_seq, base_dir)
    preprocess_input = _find_preprocess_input_seq_file(plan, base_dir)
    output_dir = _resolve_output_dir(plan, base_dir)

    # Prefer caching plan.out_seq_file when it directly references URLs.
    out_remote = count_remote_sources(original_seq_path) if original_seq_path.exists() else 0
    if out_remote:
        source_seq_path = _locate_seq_file(plan, base_dir) or original_seq_path
        cached_seq_path, materialize_summary = materialize_out_seq_file(
            source_seq_path,
            output_dir=output_dir,
            cache_dir=cache_dir,
        )
        if not materialize_summary.rewritten:
            return SeqCacheSummary(downloaded=0, rewritten=False)

        plan.out_seq_file = str(cached_seq_path)
        _rewrite_plan_commands(
            plan,
            old_seq=original_seq,
            old_seq_path=original_seq_path,
            source_seq_path=source_seq_path,
            new_seq=str(cached_seq_path),
        )
        return SeqCacheSummary(downloaded=materialize_summary.downloaded, rewritten=True)

    # Otherwise, cache the preprocess input seq file if it contains URLs.
    if preprocess_input is None:
        return SeqCacheSummary(downloaded=0, rewritten=False)
    in_remote = count_remote_sources(preprocess_input)
    if not in_remote:
        return SeqCacheSummary(downloaded=0, rewritten=False)

    cached_in_path, materialize_summary = materialize_out_seq_file(
        preprocess_input,
        output_dir=output_dir,
        cache_dir=cache_dir,
    )
    if not materialize_summary.rewritten:
        return SeqCacheSummary(downloaded=0, rewritten=False)

    _rewrite_preprocess_input_seq_file(
        plan,
        base_dir=base_dir,
        old_path=preprocess_input,
        new_path=cached_in_path,
    )
    return SeqCacheSummary(downloaded=materialize_summary.downloaded, rewritten=True)

def find_seq_file(plan: Plan, *, base_dir: Optional[Path] = None) -> Path | None:
    """Locate the seq file (Newick + leaf mapping) for *plan*."""

    base = Path(base_dir) if base_dir else Path.cwd()
    return _locate_seq_file(plan, base)


def find_preprocess_input_seq_file(plan: Plan, *, base_dir: Optional[Path] = None) -> Path | None:
    """Locate the input seq file used by the first cactus-preprocess step (if any)."""

    base = Path(base_dir) if base_dir else Path.cwd()
    return _find_preprocess_input_seq_file(plan, base)


def count_remote_sources(seq_file: Path) -> int:
    """Return number of leaf entries that reference http(s) URLs."""

    parsed = _parse_out_seq_file(seq_file)
    if parsed is None:
        return 0
    _, mapping = parsed
    return sum(1 for src in mapping.values() if _is_remote_url(src))


@dataclass(frozen=True)
class MaterializeSummary:
    downloaded: int
    rewritten: bool


def materialize_out_seq_file(
    seq_file: Path,
    *,
    output_dir: Path,
    cache_dir: Path,
) -> tuple[Path, MaterializeSummary]:
    """Create a cached outSeqFile with URLs replaced by local cached paths."""

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    parsed = _parse_out_seq_file(seq_file)
    if parsed is None:
        return seq_file, MaterializeSummary(downloaded=0, rewritten=False)

    newick, mapping = parsed
    remote = {name: src for name, src in mapping.items() if _is_remote_url(src)}
    if not remote:
        return seq_file, MaterializeSummary(downloaded=0, rewritten=False)

    downloaded = 0
    rewritten_mapping: dict[str, str] = dict(mapping)
    for name, url in remote.items():
        dest = _cached_filename(cache_dir, name, url)
        if not dest.exists() or dest.stat().st_size == 0:
            _download(url, dest)
            downloaded += 1
        rewritten_mapping[name] = str(dest)

    cached_seq_path = output_dir / f"{seq_file.stem}.cached{seq_file.suffix or '.txt'}"
    _write_out_seq_file(cached_seq_path, newick, rewritten_mapping)
    return cached_seq_path, MaterializeSummary(downloaded=downloaded, rewritten=True)


def default_cache_dir() -> Path:
    return Path.home() / ".cax" / "cache" / "sequences"


def _resolve_output_dir(plan: Plan, base_dir: Path) -> Path:
    if plan.out_dir:
        out = Path(plan.out_dir).expanduser()
        if not out.is_absolute():
            out = (base_dir / out).resolve()
        return out
    return base_dir


def _resolve_path(path_like: str, base_dir: Path) -> Path:
    if path_like.startswith("file:"):
        path_like = path_like.split(":", 1)[1]
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()

def _locate_seq_file(plan: Plan, base_dir: Path) -> Path | None:
    """Find an existing seq file that contains the Newick + leaf mapping."""

    candidate = _resolve_path(plan.out_seq_file, base_dir)
    if candidate.exists():
        return candidate

    # Fallback: cactus-preprocess often includes the seq file as a positional arg.
    for step in plan.preprocess[:1]:
        tokens = _safe_split(step.raw)
        for tok in tokens:
            if tok.startswith("-"):
                continue
            path = _resolve_path(tok, base_dir)
            if path.exists() and path.is_file():
                parsed = _parse_out_seq_file(path)
                if parsed and parsed[1]:
                    return path

    # Fallback: derive from header command (first positional arg after cactus-prepare).
    tokens = _safe_split(plan.header.generated_by)
    if tokens:
        try:
            idx = next(i for i, t in enumerate(tokens) if Path(t).name == "cactus-prepare")
        except StopIteration:
            idx = 0
        for tok in tokens[idx + 1 :]:
            if tok.startswith("-"):
                continue
            path = _resolve_path(tok, base_dir)
            if path.exists() and path.is_file():
                parsed = _parse_out_seq_file(path)
                if parsed and parsed[1]:
                    return path
    return None


def _find_preprocess_input_seq_file(plan: Plan, base_dir: Path) -> Path | None:
    for step in plan.preprocess[:1]:
        tokens = _safe_split(step.raw)
        if not tokens:
            continue
        if Path(tokens[0]).name != "cactus-preprocess":
            continue
        in_tok: str | None = None
        if len(tokens) >= 4:
            # cactus-preprocess JOBSTORE IN_SEQ OUT_SEQ [opts...]
            in_tok = tokens[2]
        elif len(tokens) >= 2:
            # Simplified form used in some tests/scripts: cactus-preprocess IN_SEQ
            in_tok = tokens[1]
        if not in_tok:
            continue
        candidate = _resolve_path(in_tok, base_dir)
        if candidate.exists() and candidate.is_file():
            parsed = _parse_out_seq_file(candidate)
            if parsed and parsed[1]:
                return candidate
    return None


def _rewrite_preprocess_input_seq_file(
    plan: Plan,
    *,
    base_dir: Path,
    old_path: Path,
    new_path: Path,
) -> None:
    for step in plan.preprocess[:1]:
        tokens = _safe_split(step.raw)
        if not tokens:
            continue
        if Path(tokens[0]).name != "cactus-preprocess":
            continue

        idx = 2 if len(tokens) >= 4 else 1 if len(tokens) >= 2 else None
        if idx is None:
            continue
        try:
            resolved = _resolve_path(tokens[idx], base_dir)
        except Exception:
            resolved = None
        if resolved is None or resolved != old_path:
            continue
        tokens[idx] = str(new_path)
        step.raw = shlex.join(tokens)


def _parse_out_seq_file(path: Path) -> tuple[str, dict[str, str]] | None:
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return None
    newick = lines[idx].strip()
    idx += 1
    mapping: dict[str, str] = {}
    for line in lines[idx:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        name = parts[0]
        source = " ".join(parts[1:])
        mapping[name] = source
    return newick, mapping


def _write_out_seq_file(path: Path, newick: str, mapping: dict[str, str]) -> None:
    lines = [newick, ""]
    for name, source in mapping.items():
        lines.append(f"{name} {source}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _is_remote_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _cached_filename(cache_dir: Path, leaf_name: str, url: str) -> Path:
    parsed = urllib.parse.urlparse(url)
    suffix = Path(parsed.path).suffix or ".fa"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    safe_leaf = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in leaf_name)
    filename = f"{safe_leaf}__{digest}{suffix}"
    return (cache_dir / filename).resolve()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    with urllib.request.urlopen(url) as resp:  # nosec - user-controlled URL in an interactive tool
        data = resp.read()
    tmp_path.write_bytes(data)
    tmp_path.replace(dest)


def _rewrite_plan_commands(
    plan: Plan,
    *,
    old_seq: str,
    old_seq_path: Path,
    source_seq_path: Path,
    new_seq: str,
) -> None:
    old_candidates = {
        old_seq,
        str(old_seq_path),
        f"file:{old_seq}",
        f"file:{old_seq_path}",
        str(source_seq_path),
        f"file:{source_seq_path}",
    }

    def rewrite_step(step: Step) -> None:
        tokens = _safe_split(step.raw)
        if not tokens:
            return
        changed = False
        updated: list[str] = []
        for tok in tokens:
            if tok in old_candidates:
                updated.append(new_seq)
                changed = True
            else:
                updated.append(tok)
        if changed:
            step.raw = shlex.join(updated)

    for step in plan.preprocess:
        rewrite_step(step)
    for step in plan.hal_merges:
        rewrite_step(step)
    for round_entry in plan.rounds:
        if round_entry.blast_step:
            rewrite_step(round_entry.blast_step)
        if round_entry.align_step:
            rewrite_step(round_entry.align_step)
        for step in round_entry.hal2fasta_steps:
            rewrite_step(step)


def _safe_split(raw: str) -> list[str]:
    try:
        return shlex.split(raw)
    except ValueError:
        return raw.split()
