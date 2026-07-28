"""Runtime resource-budget detection and normalization.

CAX executes workflows in several environments: directly on a host, inside a
container/cgroup, or inside a scheduler allocation such as Slurm.  This module
reduces the limits exposed by those environments to one stable CPU/memory
budget that can be applied consistently while a plan is prepared and run.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
from typing import Mapping, Optional

import psutil


_MEMORY_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?i?b?|b)?\s*$", re.IGNORECASE)
_BINARY_UNITS = {
    "": 1,
    "b": 1,
    "k": 2**10,
    "kb": 2**10,
    "ki": 2**10,
    "kib": 2**10,
    "m": 2**20,
    "mb": 2**20,
    "mi": 2**20,
    "mib": 2**20,
    "g": 2**30,
    "gb": 2**30,
    "gi": 2**30,
    "gib": 2**30,
    "t": 2**40,
    "tb": 2**40,
    "ti": 2**40,
    "tib": 2**40,
}
_PREPARE_CORE_FLAGS = (
    "--defaultCores",
    "--preprocessCores",
    "--blastCores",
    "--alignCores",
    "--lastzCores",
)
_PREPARE_MEMORY_FLAGS = (
    "--defaultMemory",
    "--preprocessMemory",
    "--blastMemory",
    "--alignMemory",
)


@dataclass(frozen=True)
class ResourceBudget:
    """CPU and memory available to the current CAX process."""

    cpu_cores: int
    memory_bytes: int
    source: str
    detail: str = ""


class ResourceLimitError(ValueError):
    """Raised when a requested resource value exceeds the runtime budget."""


def parse_memory_size(value: str | int) -> int:
    """Parse a Cactus/Slurm-style memory value into bytes.

    Bare integers are bytes.  Unit suffixes use binary multiples because both
    Cactus/Toil and Slurm resource values are expressed in byte/MiB-oriented
    units even when their user-facing spelling is ``G`` rather than ``Gi``.
    """

    if isinstance(value, int):
        if value <= 0:
            raise ValueError("Memory must be greater than zero.")
        return value
    match = _MEMORY_PATTERN.match(str(value))
    if not match:
        raise ValueError(f"Invalid memory value: {value!r}")
    number = float(match.group(1))
    unit = (match.group(2) or "").lower()
    multiplier = _BINARY_UNITS.get(unit)
    if multiplier is None:
        raise ValueError(f"Unsupported memory unit: {unit}")
    result = int(number * multiplier)
    if result <= 0:
        raise ValueError("Memory must be greater than zero.")
    return result


def format_memory_size(value: Optional[int]) -> str:
    """Return a compact exact Cactus-compatible memory value."""

    if value is None:
        return "auto"
    for suffix, unit in (("Ti", 2**40), ("Gi", 2**30), ("Mi", 2**20), ("Ki", 2**10)):
        if value >= unit and value % unit == 0:
            return f"{value // unit}{suffix}"
    return str(value)


def detect_runtime_budget(
    *,
    env: Optional[Mapping[str, str]] = None,
    cgroup_root: Path | str = Path("/sys/fs/cgroup"),
    proc_self_cgroup: Path | str = Path("/proc/self/cgroup"),
) -> ResourceBudget:
    """Detect a stable resource budget for the current process.

    Every independently known constraint participates in the result.  The
    effective budget is the minimum CPU and memory value, so a container or
    Slurm allocation cannot be mistaken for the larger physical host.
    """

    environment = dict(os.environ if env is None else env)
    root = Path(cgroup_root)
    host_cpu = max(1, int(psutil.cpu_count(logical=True) or 1))
    available_memory = max(1, int(psutil.virtual_memory().available))

    cpu_candidates: list[tuple[int, str]] = [(host_cpu, "host")]
    memory_candidates: list[tuple[int, str]] = [(available_memory, "available memory")]
    contexts: list[str] = []
    details: list[str] = []

    affinity_cpu = _affinity_cpu_count()
    if affinity_cpu is not None:
        cpu_candidates.append((affinity_cpu, "CPU affinity"))
        if affinity_cpu < host_cpu:
            contexts.append("CPU affinity")

    for cgroup_path in _current_cgroup_paths(root, Path(proc_self_cgroup)):
        cgroup_cpu = _cgroup_cpu_limit(cgroup_path)
        if cgroup_cpu is not None:
            cpu_candidates.append((cgroup_cpu, "cgroup"))
            if "cgroup" not in contexts:
                contexts.append("cgroup")

        cgroup_memory = _cgroup_memory_available(cgroup_path)
        if cgroup_memory is not None:
            memory_candidates.append((cgroup_memory, "cgroup"))
            if "cgroup" not in contexts:
                contexts.append("cgroup")

    slurm = _slurm_limits(environment)
    if slurm is not None:
        slurm_cpu, slurm_memory, slurm_detail = slurm
        if slurm_cpu is not None:
            cpu_candidates.append((slurm_cpu, "Slurm"))
        if slurm_memory is not None:
            memory_candidates.append((slurm_memory, "Slurm"))
        contexts.insert(0, "Slurm allocation")
        if slurm_detail:
            details.append(slurm_detail)

    cpu_limit, cpu_source = min(cpu_candidates, key=lambda item: item[0])
    memory_limit, memory_source = min(memory_candidates, key=lambda item: item[0])
    cpu_limit = max(1, cpu_limit)
    memory_limit = (memory_limit // 2**20) * 2**20 if memory_limit >= 2**20 else memory_limit

    if not contexts:
        contexts.append("local system")
    source = " + ".join(dict.fromkeys(contexts))
    details.append(f"CPU bound: {cpu_source}; memory bound: {memory_source}")
    return ResourceBudget(
        cpu_cores=cpu_limit,
        memory_bytes=memory_limit,
        source=source,
        detail="; ".join(details),
    )


def resolve_selected_limits(
    budget: ResourceBudget,
    *,
    threads: Optional[int] = None,
    memory: Optional[str | int] = None,
) -> tuple[int, int]:
    """Validate optional user limits against a detected runtime budget."""

    cpu_limit = budget.cpu_cores if threads is None else threads
    if cpu_limit <= 0:
        raise ResourceLimitError("CPU limit must be at least 1.")
    if cpu_limit > budget.cpu_cores:
        raise ResourceLimitError(
            f"Requested {cpu_limit} CPU cores, but the runtime budget allows at most "
            f"{budget.cpu_cores}."
        )

    memory_limit = budget.memory_bytes if memory is None else parse_memory_size(memory)
    if memory_limit > budget.memory_bytes:
        raise ResourceLimitError(
            f"Requested {format_memory_size(memory_limit)} memory, but the runtime budget "
            f"allows at most {format_memory_size(budget.memory_bytes)}."
        )
    return cpu_limit, memory_limit


def apply_prepare_resource_limits(
    prepare_args: str,
    *,
    cpu_limit: int,
    memory_limit_bytes: int,
) -> str:
    """Validate prepare resource options and add deterministic defaults."""

    tokens = shlex.split(prepare_args)
    for flag in _PREPARE_CORE_FLAGS:
        raw = _option_value(tokens, flag)
        if raw is None:
            continue
        try:
            value = int(raw)
        except ValueError as exc:
            raise ResourceLimitError(f"{flag} must be a positive integer, got {raw!r}.") from exc
        if value <= 0:
            raise ResourceLimitError(f"{flag} must be at least 1.")
        if value > cpu_limit:
            raise ResourceLimitError(
                f"{flag}={value} exceeds the effective CPU limit of {cpu_limit}."
            )

    for flag in _PREPARE_MEMORY_FLAGS:
        raw = _option_value(tokens, flag)
        if raw is None:
            continue
        try:
            value = parse_memory_size(raw)
        except ValueError as exc:
            raise ResourceLimitError(f"Invalid {flag} value {raw!r}.") from exc
        if value > memory_limit_bytes:
            raise ResourceLimitError(
                f"{flag}={raw} exceeds the effective memory limit of "
                f"{format_memory_size(memory_limit_bytes)}."
            )

    if _option_value(tokens, "--defaultCores") is None:
        tokens.extend(["--defaultCores", str(cpu_limit)])
    if _option_value(tokens, "--defaultMemory") is None:
        tokens.extend(["--defaultMemory", format_memory_size(memory_limit_bytes)])
    return shlex.join(tokens)


def _option_value(tokens: list[str], flag: str) -> Optional[str]:
    for index, token in enumerate(tokens):
        if token == flag:
            if index + 1 >= len(tokens):
                raise ResourceLimitError(f"{flag} requires a value.")
            return tokens[index + 1]
        if token.startswith(flag + "="):
            return token.split("=", 1)[1]
    return None


def _affinity_cpu_count() -> Optional[int]:
    getter = getattr(os, "sched_getaffinity", None)
    if getter is not None:
        try:
            count = len(getter(0))
        except (OSError, TypeError):
            count = 0
        if count > 0:
            return count
    try:
        count = len(psutil.Process().cpu_affinity())
    except (AttributeError, NotImplementedError, OSError, psutil.Error):
        return None
    return count if count > 0 else None


def _cgroup_cpu_limit(root: Path) -> Optional[int]:
    candidates: list[int] = []
    cpuset_text = _read_first(
        root / "cpuset.cpus.effective",
        root / "cpuset.cpus",
        root / "cpuset" / "cpuset.cpus",
    )
    if cpuset_text:
        count = _parse_cpu_set(cpuset_text)
        if count > 0:
            candidates.append(count)

    cpu_max = _read_first(root / "cpu.max")
    if cpu_max:
        parts = cpu_max.split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                quota = int(parts[0])
                period = int(parts[1])
                if quota > 0 and period > 0:
                    candidates.append(max(1, math.floor(quota / period)))
            except ValueError:
                pass
    else:
        quota_text = _read_first(
            root / "cpu.cfs_quota_us",
            root / "cpu" / "cpu.cfs_quota_us",
        )
        period_text = _read_first(
            root / "cpu.cfs_period_us",
            root / "cpu" / "cpu.cfs_period_us",
        )
        try:
            quota = int(quota_text) if quota_text else -1
            period = int(period_text) if period_text else -1
            if quota > 0 and period > 0:
                candidates.append(max(1, math.floor(quota / period)))
        except ValueError:
            pass

    return min(candidates) if candidates else None


def _cgroup_memory_available(root: Path) -> Optional[int]:
    limit_text = _read_first(root / "memory.max")
    usage_text = _read_first(root / "memory.current")
    if limit_text is None:
        limit_text = _read_first(
            root / "memory.limit_in_bytes",
            root / "memory" / "memory.limit_in_bytes",
        )
        usage_text = _read_first(
            root / "memory.usage_in_bytes",
            root / "memory" / "memory.usage_in_bytes",
        )
    if not limit_text or limit_text == "max":
        return None
    try:
        limit = int(limit_text)
        usage = int(usage_text) if usage_text else 0
    except ValueError:
        return None
    # Very large v1 values represent "unlimited".
    if limit <= 0 or limit >= 2**60:
        return None
    return max(1, limit - max(0, usage))


def _current_cgroup_paths(root: Path, proc_self_cgroup: Path) -> list[Path]:
    """Return the current process cgroup and its constrained ancestors."""

    paths: list[Path] = [root]
    try:
        lines = proc_self_cgroup.read_text(encoding="utf-8").splitlines()
    except OSError:
        return paths

    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        controllers, relative = parts[1], parts[2].lstrip("/")
        mount_roots: list[Path]
        if not controllers:
            mount_roots = [root]
        else:
            controller_names = controllers.split(",")
            mount_roots = [
                root / controllers,
                *(root / controller for controller in controller_names),
            ]
        for mount_root in mount_roots:
            current = mount_root / relative
            while current == mount_root or mount_root in current.parents:
                paths.append(current)
                if current == mount_root:
                    break
                current = current.parent

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _slurm_limits(
    env: Mapping[str, str],
) -> Optional[tuple[Optional[int], Optional[int], str]]:
    job_id = env.get("SLURM_JOB_ID") or env.get("SLURM_JOBID")
    if not job_id:
        return None

    cpu = _positive_int(env.get("SLURM_CPUS_ON_NODE"))
    if cpu is None:
        cpu = _positive_int(env.get("SLURM_CPUS_PER_TASK"))
    if cpu is None:
        cpu = _parse_slurm_cpu_list(env.get("SLURM_JOB_CPUS_PER_NODE", ""))

    memory = _slurm_memory_from_env(env, cpu)
    if cpu is None or memory is None:
        control_text = _run_scontrol(job_id)
        if control_text:
            if cpu is None:
                cpu = _extract_scontrol_cpu(control_text)
            if memory is None:
                memory = _extract_scontrol_memory(control_text, cpu)

    node = env.get("SLURMD_NODENAME") or env.get("HOSTNAME")
    detail = f"job {job_id}" + (f" on {node}" if node else "")
    return cpu, memory, detail


def _slurm_memory_from_env(env: Mapping[str, str], cpu: Optional[int]) -> Optional[int]:
    per_node = env.get("SLURM_MEM_PER_NODE")
    if per_node:
        return _parse_slurm_mebibytes(per_node)
    per_cpu = env.get("SLURM_MEM_PER_CPU")
    if per_cpu and cpu:
        value = _parse_slurm_mebibytes(per_cpu)
        return value * cpu if value is not None else None
    return None


def _run_scontrol(job_id: str) -> str:
    try:
        result = subprocess.run(
            ["scontrol", "show", "job", "-o", job_id],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _extract_scontrol_cpu(text: str) -> Optional[int]:
    for key in ("NumCPUs", "NumCPUsNode"):
        match = re.search(rf"(?:^|\s){key}=(\d+)", text)
        if match:
            return _positive_int(match.group(1))
    match = re.search(r"(?:AllocTRES|TRES)=[^\s]*\bcpu=(\d+)", text)
    return _positive_int(match.group(1)) if match else None


def _extract_scontrol_memory(text: str, cpu: Optional[int]) -> Optional[int]:
    node_match = re.search(r"(?:^|\s)MinMemoryNode=([^\s]+)", text)
    if node_match:
        return _parse_slurm_memory_token(node_match.group(1))
    cpu_match = re.search(r"(?:^|\s)MinMemoryCPU=([^\s]+)", text)
    if cpu_match and cpu:
        value = _parse_slurm_memory_token(cpu_match.group(1))
        return value * cpu if value is not None else None
    tres_match = re.search(r"(?:AllocTRES|TRES)=[^\s]*\bmem=([^,\s]+)", text)
    return _parse_slurm_memory_token(tres_match.group(1)) if tres_match else None


def _parse_slurm_memory_token(value: str) -> Optional[int]:
    token = value.strip()
    if token.isdigit():
        parsed = int(token)
        return parsed * 2**20 if parsed > 0 else None
    try:
        return parse_memory_size(token)
    except ValueError:
        return None


def _parse_slurm_mebibytes(value: str) -> Optional[int]:
    token = value.strip()
    try:
        if token.isdigit():
            parsed = int(token)
            return parsed * 2**20 if parsed > 0 else None
        return parse_memory_size(token)
    except ValueError:
        return None


def _parse_slurm_cpu_list(value: str) -> Optional[int]:
    match = re.match(r"\s*(\d+)", value)
    return _positive_int(match.group(1)) if match else None


def _positive_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _parse_cpu_set(value: str) -> int:
    cpus: set[int] = set()
    for raw_part in value.strip().split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError:
                continue
            if end >= start:
                cpus.update(range(start, end + 1))
            continue
        try:
            cpus.add(int(part))
        except ValueError:
            continue
    return len(cpus)


def _read_first(*paths: Path) -> Optional[str]:
    for path in paths:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return None
