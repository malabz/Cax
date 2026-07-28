from datetime import datetime
from pathlib import Path

import pytest

from cax import planner, resources
from cax.models import Plan, PrepareHeader, Round, RunSettings, Step
from cax.resume import command_stable_key
from cax.ui import RunSettingsScreen


class _Memory:
    def __init__(self, available: int):
        self.available = available


def _mock_host(monkeypatch, *, cpu: int, memory: int) -> None:
    monkeypatch.setattr(resources.psutil, "cpu_count", lambda logical=True: cpu)
    monkeypatch.setattr(resources.psutil, "virtual_memory", lambda: _Memory(memory))
    monkeypatch.setattr(resources, "_affinity_cpu_count", lambda: None)


def test_detect_runtime_budget_uses_local_available_resources(tmp_path: Path, monkeypatch):
    _mock_host(monkeypatch, cpu=32, memory=120 * 2**30)

    budget = resources.detect_runtime_budget(env={}, cgroup_root=tmp_path)

    assert budget.cpu_cores == 32
    assert budget.memory_bytes == 120 * 2**30
    assert budget.source == "local system"


def test_detect_runtime_budget_applies_cgroup_cpu_and_remaining_memory(
    tmp_path: Path,
    monkeypatch,
):
    _mock_host(monkeypatch, cpu=64, memory=200 * 2**30)
    (tmp_path / "cpu.max").write_text("800000 100000", encoding="utf-8")
    (tmp_path / "cpuset.cpus.effective").write_text("0-15", encoding="utf-8")
    (tmp_path / "memory.max").write_text(str(20 * 2**30), encoding="utf-8")
    (tmp_path / "memory.current").write_text(str(4 * 2**30), encoding="utf-8")

    budget = resources.detect_runtime_budget(env={}, cgroup_root=tmp_path)

    assert budget.cpu_cores == 8
    assert budget.memory_bytes == 16 * 2**30
    assert "cgroup" in budget.source


def test_detect_runtime_budget_reads_current_cgroup_and_parent_limits(
    tmp_path: Path,
    monkeypatch,
):
    _mock_host(monkeypatch, cpu=64, memory=200 * 2**30)
    current = tmp_path / "user.slice" / "cax.scope"
    current.mkdir(parents=True)
    (tmp_path / "user.slice" / "cpu.max").write_text("1200000 100000", encoding="utf-8")
    (current / "cpu.max").write_text("max 100000", encoding="utf-8")
    (tmp_path / "user.slice" / "memory.max").write_text(str(40 * 2**30), encoding="utf-8")
    (tmp_path / "user.slice" / "memory.current").write_text(str(8 * 2**30), encoding="utf-8")
    proc_self_cgroup = tmp_path / "proc-self-cgroup"
    proc_self_cgroup.write_text("0::/user.slice/cax.scope\n", encoding="utf-8")

    budget = resources.detect_runtime_budget(
        env={},
        cgroup_root=tmp_path,
        proc_self_cgroup=proc_self_cgroup,
    )

    assert budget.cpu_cores == 12
    assert budget.memory_bytes == 32 * 2**30
    assert "cgroup" in budget.source


def test_detect_runtime_budget_uses_slurm_allocation(tmp_path: Path, monkeypatch):
    _mock_host(monkeypatch, cpu=64, memory=500 * 2**30)
    env = {
        "SLURM_JOB_ID": "7020317",
        "SLURM_CPUS_ON_NODE": "16",
        "SLURM_MEM_PER_NODE": "102400",
        "SLURMD_NODENAME": "cpu64-8",
    }

    budget = resources.detect_runtime_budget(env=env, cgroup_root=tmp_path)

    assert budget.cpu_cores == 16
    assert budget.memory_bytes == 100 * 2**30
    assert budget.source.startswith("Slurm allocation")
    assert "job 7020317 on cpu64-8" in budget.detail


def test_detect_runtime_budget_derives_slurm_memory_per_cpu(tmp_path: Path, monkeypatch):
    _mock_host(monkeypatch, cpu=64, memory=500 * 2**30)
    env = {
        "SLURM_JOB_ID": "99",
        "SLURM_CPUS_ON_NODE": "16",
        "SLURM_MEM_PER_CPU": "6400",
    }

    budget = resources.detect_runtime_budget(env=env, cgroup_root=tmp_path)

    assert budget.cpu_cores == 16
    assert budget.memory_bytes == 100 * 2**30


def test_detect_runtime_budget_uses_scontrol_when_slurm_env_is_incomplete(
    tmp_path: Path,
    monkeypatch,
):
    _mock_host(monkeypatch, cpu=64, memory=500 * 2**30)
    monkeypatch.setattr(
        resources,
        "_run_scontrol",
        lambda job_id: (
            "JobId=88 JobState=RUNNING NumCPUs=16 "
            "MinMemoryNode=102400M AllocTRES=cpu=16,mem=100G,node=1"
        ),
    )

    budget = resources.detect_runtime_budget(
        env={"SLURM_JOB_ID": "88"},
        cgroup_root=tmp_path,
    )

    assert budget.cpu_cores == 16
    assert budget.memory_bytes == 100 * 2**30


def test_prepare_resource_limits_add_defaults_and_preserve_lower_values():
    args = resources.apply_prepare_resource_limits(
        "input.txt --alignCores 8 --alignMemory 80Gi",
        cpu_limit=16,
        memory_limit_bytes=100 * 2**30,
    )

    assert "--alignCores 8" in args
    assert "--alignMemory 80Gi" in args
    assert "--defaultCores 16" in args
    assert "--defaultMemory 100Gi" in args


def test_selected_limits_can_be_lower_but_not_higher_than_runtime_budget():
    budget = resources.ResourceBudget(
        cpu_cores=16,
        memory_bytes=100 * 2**30,
        source="Slurm allocation",
    )

    assert resources.resolve_selected_limits(
        budget,
        threads=8,
        memory="80Gi",
    ) == (8, 80 * 2**30)

    with pytest.raises(resources.ResourceLimitError, match="at most 16"):
        resources.resolve_selected_limits(budget, threads=32)
    with pytest.raises(resources.ResourceLimitError, match="at most 100Gi"):
        resources.resolve_selected_limits(budget, memory="120Gi")


@pytest.mark.parametrize(
    "args, message",
    [
        ("input.txt --alignCores 32", "--alignCores=32"),
        ("input.txt --alignMemory 120Gi", "--alignMemory=120Gi"),
    ],
)
def test_prepare_resource_limits_reject_values_above_budget(args: str, message: str):
    with pytest.raises(resources.ResourceLimitError, match=message):
        resources.apply_prepare_resource_limits(
            args,
            cpu_limit=16,
            memory_limit_bytes=100 * 2**30,
        )


def test_planner_caps_existing_cactus_and_ramax_resource_options(tmp_path: Path):
    header = PrepareHeader(
        generated_by="cactus-prepare --outSeqFile seq.fa",
        date=datetime.now(),
    )
    preprocess = Step(
        raw=(
            "cactus-preprocess js in.txt out.txt "
            "--maxCores 32 --lastzCores=24 --maxMemory 120Gi"
        ),
        kind="preprocess",
    )
    round_entry = Round(
        name="round",
        root="Anc0",
        target_hal="out.hal",
        replace_with_ramax=True,
        manual_ramax_command="ramax -i seq.fa -o out.hal --root Anc0 --threads 32",
    )
    plan = Plan(
        header=header,
        preprocess=[preprocess],
        rounds=[round_entry],
        hal_merges=[],
        out_seq_file="seq.fa",
        out_dir=str(tmp_path),
    )

    commands = planner.build_execution_plan(
        plan,
        base_dir=tmp_path,
        thread_count=16,
        memory_limit_bytes=100 * 2**30,
    )

    cactus = commands[0].command
    assert cactus[cactus.index("--maxCores") + 1] == "16"
    assert "--lastzCores=16" in cactus
    assert cactus[cactus.index("--maxMemory") + 1] == "100Gi"

    ramax = next(command.command for command in commands if command.is_ramax)
    assert ramax[ramax.index("--threads") + 1] == "16"


def test_planner_adds_missing_cactus_memory_limit(tmp_path: Path):
    header = PrepareHeader(
        generated_by="cactus-prepare --outSeqFile seq.fa",
        date=datetime.now(),
    )
    plan = Plan(
        header=header,
        preprocess=[Step(raw="cactus-preprocess js in.txt out.txt", kind="preprocess")],
        rounds=[],
        hal_merges=[],
        out_seq_file="seq.fa",
        out_dir=str(tmp_path),
    )

    command = planner.build_execution_plan(
        plan,
        base_dir=tmp_path,
        thread_count=8,
        memory_limit_bytes=20 * 2**30,
    )[0].command

    assert command[-4:] == ["--maxCores", "8", "--maxMemory", "20Gi"]


def test_resume_stable_key_ignores_runtime_memory_limit():
    command_a = planner.PlannedCommand(
        command=["cactus-preprocess", "js", "in", "out", "--maxMemory", "100Gi"],
        category="preprocess",
        display_name="preprocess",
    )
    command_b = planner.PlannedCommand(
        command=["cactus-preprocess", "js", "in", "out", "--maxMemory", "80Gi"],
        category="preprocess",
        display_name="preprocess",
    )

    assert command_stable_key(command_a) == command_stable_key(command_b)


def test_run_settings_enforces_detected_cpu_and_memory_budget(tmp_path: Path):
    budget = resources.ResourceBudget(
        cpu_cores=16,
        memory_bytes=100 * 2**30,
        source="Slurm allocation",
    )
    header = PrepareHeader(
        generated_by="cactus-prepare --outSeqFile seq.fa",
        date=datetime.now(),
    )
    plan = Plan(
        header=header,
        preprocess=[],
        rounds=[],
        hal_merges=[],
        out_seq_file="seq.fa",
        out_dir=str(tmp_path),
    )
    screen = RunSettingsScreen(
        plan,
        RunSettings(
            thread_count=16,
            memory_limit_bytes=100 * 2**30,
            resource_budget=budget,
        ),
        compact=False,
    )

    screen._thread_text = "32"
    ok, _, message = screen._validate_threads()
    assert ok is False
    assert message == "Thread count cannot exceed the runtime budget (16)."

    screen._thread_text = ""
    ok, cpu, _ = screen._validate_threads()
    assert ok is True
    assert cpu == 16

    screen._memory_text = "120Gi"
    ok, _, message = screen._validate_memory()
    assert ok is False
    assert message == "Memory limit cannot exceed the runtime budget (100Gi)."

    screen._memory_text = ""
    ok, memory, _ = screen._validate_memory()
    assert ok is True
    assert memory == 100 * 2**30
