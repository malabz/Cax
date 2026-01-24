"""Mash-based heuristics for pre-selecting RaMAx rounds.

Key behavior:
- For each internal alignment node/round we want a *tree-aware* Mash distance that
  is safe to use for defaults: if *any* leaf-pair in the subtree exceeds the
  threshold, we must NOT auto-enable RaMAx for that node.
- We therefore perform pairwise checks within each subtree with **early stop**:
  as soon as we find a leaf-pair distance > threshold, the subtree is proven
  ineligible and we stop computing more pairs.
- Results are cached on disk (under the plan output directory) so repeating the
  same dataset/command reuses prior distances and "补算" (compute more pairs as
  needed) when the user increases the threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Optional
from concurrent.futures import ALL_COMPLETED, FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

from .models import Plan, Round
from . import tree_utils


DEFAULT_KMER = 31
DEFAULT_SKETCH_SIZE = 20000


@dataclass(frozen=True)
class MashAutoSummary:
    computed: int
    skipped: int
    enabled_ramax: int
    pairwise_computed: int = 0
    pairwise_cached: int = 0


SUBTREE_MODE_FLAG = "--subtree-mode"


@dataclass(frozen=True)
class MashThresholdSummary:
    threshold: float
    considered: int
    enabled_ramax: int
    changed: int
    cleared_subtree_mode: int


def apply_mash_threshold(
    plan: Plan,
    *,
    threshold: float,
    clear_subtree_mode: bool = True,
) -> MashThresholdSummary:
    """Re-apply Mash threshold to an existing plan without recomputing distances.

    This is intended for UI workflows where Mash distances were already computed
    (``Round.mash_distance`` is populated) and the user wants to tweak the
    threshold and immediately re-select RaMAx rounds.
    """

    if threshold < 0.0 or threshold > 1.0:
        raise ValueError("threshold must be within [0.0, 1.0]")

    considered = 0
    enabled = 0
    changed = 0
    cleared = 0

    for round_entry in plan.rounds:
        if clear_subtree_mode and SUBTREE_MODE_FLAG in round_entry.ramax_opts:
            round_entry.ramax_opts = [opt for opt in round_entry.ramax_opts if opt != SUBTREE_MODE_FLAG]
            cleared += 1

        if round_entry.mash_distance is None:
            continue
        considered += 1
        new_state = round_entry.mash_distance <= threshold
        if round_entry.replace_with_ramax != new_state:
            round_entry.replace_with_ramax = new_state
            changed += 1
        if new_state:
            enabled += 1

    return MashThresholdSummary(
        threshold=threshold,
        considered=considered,
        enabled_ramax=enabled,
        changed=changed,
        cleared_subtree_mode=cleared,
    )


def apply_mash_distance_defaults(
    plan: Plan,
    *,
    base_dir: Optional[Path] = None,
    threshold: float = 0.02,
    sequence_file: str | Path | None = None,
    k: int = DEFAULT_KMER,
    sketch_size: int = DEFAULT_SKETCH_SIZE,
    max_workers: int | None = None,
    mash_exe: str = "mash",
) -> MashAutoSummary:
    """Compute tree-aware Mash distances and enable RaMAx when distance <= threshold.

    Unlike the initial lightweight heuristic, this implementation performs
    subtree pairwise checks with early stop and caching so that:

    - If any leaf pair within a subtree has Mash distance > threshold, that
      subtree is considered **ineligible** for automatic RaMAx.
    - If all leaf pairs are <= threshold, the subtree is eligible and the
      stored ``Round.mash_distance`` becomes the *maximum* observed distance
      within the subtree.
    - If the user later raises the threshold, we may need to compute additional
      pairs (补算). Previously computed distances are reused from a persistent
      on-disk cache.

    This mutates *plan* in place by updating:
    - ``round.mash_distance``
    - ``round.mash_reference`` / ``round.mash_query`` (leaf names used)
    - ``round.replace_with_ramax`` based on *threshold*.
    """

    base_dir = Path(base_dir) if base_dir else Path.cwd()
    mash_path = shutil.which(mash_exe)
    if not mash_path:
        return MashAutoSummary(computed=0, skipped=len(plan.rounds), enabled_ramax=0)

    out_seq_path = _resolve_path(plan.out_seq_file, base_dir)
    leaf_seq_path = out_seq_path
    if sequence_file is not None:
        leaf_seq_path = _resolve_path(str(sequence_file), base_dir)
    leaf_map = _parse_out_seq_file_map(leaf_seq_path)
    if not leaf_map and leaf_seq_path != out_seq_path:
        # Fallback to plan.out_seq_file mapping when the requested seq file is missing/unreadable.
        leaf_seq_path = out_seq_path
        leaf_map = _parse_out_seq_file_map(leaf_seq_path)
    if not leaf_map:
        return MashAutoSummary(computed=0, skipped=len(plan.rounds), enabled_ramax=0)

    tree = tree_utils.build_alignment_tree(plan, base_dir=base_dir)
    if tree is None:
        return MashAutoSummary(computed=0, skipped=len(plan.rounds), enabled_ramax=0)

    leaf_paths: dict[str, Path] = {}
    for leaf, source in leaf_map.items():
        path = _resolve_sequence_source(leaf, source, leaf_seq_path, base_dir)
        if path is not None:
            leaf_paths[leaf] = path

    cache_path = _default_pair_cache_path(plan, base_dir, k=k, sketch_size=sketch_size)
    pair_cache = _load_pair_cache(cache_path, k=k, sketch_size=sketch_size)
    pairwise_computed = 0
    pairwise_cached = 0

    if max_workers is None:
        env_workers = os.environ.get("CAX_MASH_WORKERS")
        if env_workers:
            try:
                max_workers = max(1, int(env_workers))
            except ValueError:
                max_workers = None
        if max_workers is None:
            max_workers = max(1, min(4, os.cpu_count() or 1))
    parallel_min_pairs = 64

    executor: ThreadPoolExecutor | None = None
    if max_workers > 1:
        executor = ThreadPoolExecutor(max_workers=max_workers)

    @dataclass
    class _Eval:
        status: str  # ok|fail|skip
        leaves: tuple[str, ...]
        max_distance: float | None = None
        max_pair: tuple[str, str] | None = None
        source_node: str | None = None

    eval_cache: dict[int, _Eval] = {}

    def _distance(a: str, b: str) -> float:
        nonlocal pairwise_computed, pairwise_cached
        key = tuple(sorted((a, b)))
        if key in pair_cache:
            pairwise_cached += 1
            return pair_cache[key]
        a_path = leaf_paths.get(a)
        b_path = leaf_paths.get(b)
        if a_path is None or b_path is None:
            raise FileNotFoundError("missing leaf sequence")
        dist = _mash_distance(
            mash_path,
            a_path,
            b_path,
            k=k,
            sketch_size=sketch_size,
            cwd=base_dir,
        )
        pair_cache[key] = dist
        pairwise_computed += 1
        return dist

    def _compute_distance(a: str, b: str) -> float:
        a_path = leaf_paths.get(a)
        b_path = leaf_paths.get(b)
        if a_path is None or b_path is None:
            raise FileNotFoundError("missing leaf sequence")
        return _mash_distance(
            mash_path,
            a_path,
            b_path,
            k=k,
            sketch_size=sketch_size,
            cwd=base_dir,
        )

    def _check_cross_pairs(
        left: tuple[str, ...],
        right: tuple[str, ...],
        *,
        threshold: float,
        current_max: float,
        current_max_pair: tuple[str, str] | None,
    ) -> tuple[str, float, tuple[str, str] | None, tuple[str, str] | None]:
        """Evaluate left×right leaf pairs.

        Returns a tuple:
        - status: ok|fail|skip
        - max_distance: updated max over all observed pairs
        - max_pair: leaf pair that produced max_distance (may be None)
        - witness_pair: leaf pair that exceeded threshold when failing (may be None)
        """

        nonlocal pairwise_computed, pairwise_cached

        max_distance = current_max
        max_pair = current_max_pair

        total_pairs = len(left) * len(right)
        if executor is None or total_pairs < parallel_min_pairs:
            for a in left:
                for b in right:
                    try:
                        dist = _distance(a, b)
                    except Exception:
                        return ("skip", max_distance, max_pair, None)
                    if dist > max_distance:
                        max_distance = dist
                        max_pair = (a, b)
                    if dist > threshold:
                        return ("fail", max_distance, max_pair, (a, b))
            return ("ok", max_distance, max_pair, None)

        # Parallel path: limited in-flight execution to bound wasted work.
        iterator = ((a, b) for a in left for b in right)
        pending: dict[Future[float], tuple[str, str]] = {}

        def cancel_and_wait() -> None:
            for fut in pending:
                fut.cancel()
            wait(list(pending.keys()), return_when=ALL_COMPLETED)

        def fill_queue() -> tuple[str, tuple[str, str] | None]:
            """Fill pending futures up to max_workers.

            Returns:
            - ("ok", None) when queue filled or iterator exhausted without finding a cached witness.
            - ("fail", (a,b)) when a cached distance already exceeds threshold.
            """

            nonlocal pairwise_cached, max_distance, max_pair
            while len(pending) < max_workers:
                try:
                    a, b = next(iterator)
                except StopIteration:
                    break
                key = tuple(sorted((a, b)))
                if key in pair_cache:
                    pairwise_cached += 1
                    dist = pair_cache[key]
                    if dist > max_distance:
                        max_distance = dist
                        max_pair = (a, b)
                    if dist > threshold:
                        return ("fail", (a, b))
                    continue
                pending[executor.submit(_compute_distance, a, b)] = (a, b)
            return ("ok", None)

        status, witness = fill_queue()
        if status == "fail" and witness is not None:
            cancel_and_wait()
            return ("fail", max_distance, max_pair, witness)

        while pending:
            done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
            for fut in done:
                a, b = pending.pop(fut)
                try:
                    dist = fut.result()
                except Exception:
                    cancel_and_wait()
                    return ("skip", max_distance, max_pair, None)

                key = tuple(sorted((a, b)))
                pair_cache[key] = dist
                pairwise_computed += 1
                if dist > max_distance:
                    max_distance = dist
                    max_pair = (a, b)
                if dist > threshold:
                    cancel_and_wait()
                    return ("fail", max_distance, max_pair, (a, b))

            status, witness = fill_queue()
            if status == "fail" and witness is not None:
                cancel_and_wait()
                return ("fail", max_distance, max_pair, witness)

        return ("ok", max_distance, max_pair, None)

    def _evaluate(node: tree_utils.AlignmentNode) -> _Eval:
        cached = eval_cache.get(id(node))
        if cached is not None:
            return cached

        node_label = (node.round.root if node.round else node.name) or None

        if not node.children:
            if node.name and node.name in leaf_map and node.name not in leaf_paths:
                result = _Eval(status="skip", leaves=(node.name,))
            else:
                result = _Eval(
                    status="ok",
                    leaves=(node.name,) if node.name else tuple(),
                    max_distance=0.0,
                    source_node=node_label,
                )
            eval_cache[id(node)] = result
            return result

        child_results = [_evaluate(child) for child in node.children]
        leaves = tuple(sorted({leaf for res in child_results for leaf in res.leaves}))
        if any(res.status == "skip" for res in child_results):
            result = _Eval(status="skip", leaves=leaves)
            eval_cache[id(node)] = result
            return result

        failing_children = [res for res in child_results if res.status == "fail"]
        if failing_children:
            # Proven failing: no need to compute cross pairs.
            best = max(
                (res for res in failing_children if res.max_distance is not None),
                key=lambda res: res.max_distance,  # type: ignore[arg-type]
                default=failing_children[0],
            )
            result = _Eval(
                status="fail",
                leaves=leaves,
                max_distance=best.max_distance,
                max_pair=best.max_pair,
                source_node=best.source_node,
            )
            eval_cache[id(node)] = result
            return result

        # All children ok: check cross-child pairs.
        max_distance = -1.0
        max_pair: tuple[str, str] | None = None
        max_source: str | None = None
        for res in child_results:
            candidate = res.max_distance if res.max_distance is not None else 0.0
            if candidate >= max_distance:
                max_distance = candidate
                max_pair = res.max_pair
                max_source = res.source_node

        child_leaf_lists = [tuple(sorted(res.leaves)) for res in child_results]
        # Evaluate each pair of child subtrees.
        for i in range(len(child_leaf_lists)):
            for j in range(i + 1, len(child_leaf_lists)):
                left = child_leaf_lists[i]
                right = child_leaf_lists[j]
                before_pair = max_pair
                status, max_distance, max_pair, witness = _check_cross_pairs(
                    left,
                    right,
                    threshold=threshold,
                    current_max=max_distance,
                    current_max_pair=max_pair,
                )
                if status == "skip":
                    result = _Eval(status="skip", leaves=leaves)
                    eval_cache[id(node)] = result
                    return result
                if status == "fail" and witness is not None:
                    result = _Eval(
                        status="fail",
                        leaves=leaves,
                        max_distance=max_distance,
                        max_pair=witness,
                        source_node=node_label,
                    )
                    eval_cache[id(node)] = result
                    return result
                if max_pair is not None and max_pair != before_pair:
                    # New max comes from cross-pair comparisons at this node.
                    max_source = node_label

        result = _Eval(
            status="ok",
            leaves=leaves,
            max_distance=max_distance,
            max_pair=max_pair,
            source_node=max_source,
        )
        eval_cache[id(node)] = result
        return result

    computed = 0
    skipped = 0
    enabled = 0

    try:
        for round_entry in plan.rounds:
            if SUBTREE_MODE_FLAG in round_entry.ramax_opts:
                round_entry.ramax_opts = [opt for opt in round_entry.ramax_opts if opt != SUBTREE_MODE_FLAG]
            node = tree.find(round_entry.root)
            if node is None:
                skipped += 1
                continue
            outcome = _evaluate(node)
            if outcome.status == "skip" or outcome.max_distance is None:
                skipped += 1
                round_entry.mash_distance = None
                round_entry.mash_reference = None
                round_entry.mash_query = None
                round_entry.mash_source = None
                round_entry.replace_with_ramax = False
                continue

            round_entry.mash_distance = outcome.max_distance
            if outcome.max_pair:
                round_entry.mash_reference = outcome.max_pair[0]
                round_entry.mash_query = outcome.max_pair[1]
            round_entry.mash_source = outcome.source_node
            computed += 1
            round_entry.replace_with_ramax = outcome.max_distance <= threshold
            if round_entry.replace_with_ramax:
                enabled += 1
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    # Persist cache only if we computed something new.
    if pairwise_computed:
        _save_pair_cache(cache_path, pair_cache, k=k, sketch_size=sketch_size)

    return MashAutoSummary(
        computed=computed,
        skipped=skipped,
        enabled_ramax=enabled,
        pairwise_computed=pairwise_computed,
        pairwise_cached=pairwise_cached,
    )


def _resolve_path(path_like: str, base_dir: Path) -> Path:
    if path_like.startswith("file:"):
        path_like = path_like.split(":", 1)[1]
    path = Path(path_like).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _parse_out_seq_file_map(path: Path) -> dict[str, str]:
    """Parse a cactus outSeqFile style file into a leaf->source map."""

    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    # First non-empty line is Newick; remaining non-empty lines map leaf->source.
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return {}
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
    return mapping


def _default_pair_cache_path(plan: Plan, base_dir: Path, *, k: int, sketch_size: int) -> Path:
    out_root = Path(plan.out_dir).expanduser() if plan.out_dir else base_dir
    if not out_root.is_absolute():
        out_root = (base_dir / out_root).resolve()
    log_dir = out_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"mash_pair_cache_k{k}_s{sketch_size}.json"


def _load_pair_cache(path: Path, *, k: int, sketch_size: int) -> dict[tuple[str, str], float]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if data.get("k") != k or data.get("sketch_size") != sketch_size:
        return {}
    pairs = data.get("pairs")
    if not isinstance(pairs, dict):
        return {}
    cache: dict[tuple[str, str], float] = {}
    for key, value in pairs.items():
        if not isinstance(key, str):
            continue
        parts = key.split("\t")
        if len(parts) != 2:
            continue
        a, b = parts
        try:
            dist = float(value)
        except Exception:
            continue
        cache[tuple(sorted((a, b)))] = dist
    return cache


def _save_pair_cache(path: Path, cache: dict[tuple[str, str], float], *, k: int, sketch_size: int) -> None:
    pairs: dict[str, float] = {"\t".join(pair): dist for pair, dist in sorted(cache.items())}
    payload = {
        "version": 1,
        "k": k,
        "sketch_size": sketch_size,
        "pairs": pairs,
    }
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def _resolve_sequence_source(
    leaf_name: str,
    source: str,
    seq_file_path: Path,
    base_dir: Path,
) -> Path | None:
    """Resolve a leaf's sequence source to a local filesystem path.

    Currently supports local paths and ``file:`` URIs. Remote URLs are skipped.
    """

    if source.startswith(("http://", "https://")):
        return None
    if source.startswith("file:"):
        source = source.split(":", 1)[1]
    path = Path(source).expanduser()
    if path.is_absolute():
        return path if path.exists() else None
    # Prefer the seq file directory for relative paths.
    candidate = (seq_file_path.parent / path).resolve()
    if candidate.exists():
        return candidate
    candidate2 = (base_dir / path).resolve()
    return candidate2 if candidate2.exists() else None


def _mash_distance(
    mash_path: str,
    ref_path: Path,
    query_path: Path,
    *,
    k: int,
    sketch_size: int,
    cwd: Path,
) -> float:
    cmd = [
        mash_path,
        "dist",
        "-k",
        str(k),
        "-s",
        str(sketch_size),
        str(ref_path),
        str(query_path),
    ]
    result = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=cwd)
    output = (result.stdout or "").strip()
    if result.returncode != 0:
        raise RuntimeError(f"mash dist failed ({result.returncode}): {result.stderr.strip()}")
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 3:
            continue
        try:
            return float(parts[2])
        except ValueError:
            continue
    raise RuntimeError("Unable to parse mash dist output")
