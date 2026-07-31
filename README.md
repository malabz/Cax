# Cactus-RaMAx

Cactus-RaMAx helps you remix alignment plans emitted by `cactus-prepare`. You can inspect every round, toggle RaMAx for any subtree, and then run or export the resulting command list. Version `0.7.1` adds non-UI final-command export and `cax --version`, building on the runtime CPU and memory budgets for local hosts, containers/cgroups, and Slurm allocations introduced in `0.7.0`. The ASCII phylogenetic canvas, subtree/single-node toggle scopes, search, proportional branch spacing, and bottom HUD remain unchanged. Subtree Mode is a CAX-only toggle that disables descendant RaMAx automatically and gracefully reverts if you later edit a child node.

![CAX interactive UI demo](doc/assets/cax-ui-demo.gif)

## Installation

We recommend creating a fresh Conda environment and installing the project in editable mode:

```bash
# clone cax
git clone https://github.com/malabz/Cax.git
cd Cax

# create conda enviroment
conda create -n cax python=3.10 -y
conda activate cax

# Install the recommended stable Cactus release, v3.2.1.
# You can request another Cactus version, but v3.2.1 is recommended. Other
# versions may introduce compatibility issues with this workflow.
bash cactus-install.sh

# Or, if GitHub downloads are slow, download the Cactus tarball yourself and
# pass the local path.
bash cactus-install.sh /path/to/cactus-bin-v3.2.1.tar.gz

# install ramax
conda install -c conda-forge -c malab ramax
# install mash
conda install -c bioconda mash
# install cax
pip install -e .
```

Alternatively, you can build a wheel and install it in a different environment:

```bash
python -m build
pip install dist/cactus_ramax-*.whl
```

## Docker Installation

Docker images are published under `pingluzhang/cax`. The default image is for Linux x86_64 CPUs with AVX2:

```bash
docker pull pingluzhang/cax:latest
```

For older x86_64 CPUs without AVX2, use the legacy Cactus image:

```bash
docker pull pingluzhang/cax:legacy
```

Run CAX from the image by mounting your working directory at `/data`. The container uses `/data` as both its working directory and home directory, so CAX outputs, logs, history, templates, and caches are written back to the mounted directory.

```bash
docker run --rm -it \
  --user "$(id -u):$(id -g)" \
  -v "$PWD:/data" \
  pingluzhang/cax:latest \
  auto --seqfile /data/examples/evolverPrimates.txt \
  --mash-threshold 0.02 \
```

For the interactive Textual UI:

```bash
docker run --rm -it \
  --user "$(id -u):$(id -g)" \
  -v "$PWD:/data" \
  pingluzhang/cax:latest
```

Use `pingluzhang/cax:legacy` in the same commands on older CPUs.


## Quick Start: Run the Primate Example

Run this first to verify the installation:

```bash
cax auto --seqfile examples/evolverPrimates.txt --mash-threshold 0.02
```

CAX replaced 2 of 3 Cactus alignment tasks with RaMAx in this example. In our test, runtime dropped from 14 min 33.09 sec to 6 min 13.18 sec, and peak memory dropped from 279.1 MB to 203.7 MB.

| Run | Time | Maximum resident set size |
| --- | ---: | ---: |
| Pure Cactus | 14 min 33.09 sec | 279.1 MB |
| CAX with RaMAx | 6 min 13.18 sec | 203.7 MB |

## Run Without UI

`cax auto` skips the UI and executes the plan immediately. Auto mode **requires Mash** (it will exit if `mash` is missing).

**Recommended short form (seqfile + threshold):**

```bash
cax auto --seqfile examples/evolverPrimates.txt --mash-threshold 0.02
```

This auto-generates output paths using the seqfile stem:
`~/.cax/outputs/<stem>/<stem>.txt` (outSeqFile), `<stem>.hal` (outHal), and `jobstore` (jobStore).

**Full control (optional):**

```bash
cax auto --prepare-args "examples/evolverMammals.txt --outDir steps-output --outSeqFile ... --outHal ... --jobStore jobstore"
```

Or parse an existing prepare output:

```bash
cax auto --from-file steps-output/prepare_output.txt
```

Use `--no-ask-mash` to skip the Mash confirmation prompt. Use `--cache-seqs` to download remote URLs before Mash computation.

To generate the final Mash-selected, resource-limited command list without
executing it, provide an explicit output path:

```bash
cax auto --seqfile examples/evolverPrimates.txt --no-ask-mash \
  --export-commands commands.txt
```

The export contains the same commands that auto mode would execute, one command
per line. Export mode does not clean existing output/job-store directories and
does not start the plan.

CAX detects the CPU and memory available to the current process before it runs
`cactus-prepare`. On a normal host it uses the process-visible CPU set and
available memory; inside a container or Slurm allocation it also honors those
harder limits. The detected budget is applied to Cactus `--maxCores` /
`--maxMemory` and RaMAx `--threads`. Use `--threads N` or
`--memory-limit 80Gi` to select a lower limit; values above the detected budget
are rejected.

Show the installed CAX version with:

```bash
cax --version
```

## Run With UI

Run CAX without arguments to open the interactive Textual UI:

```bash
cax
```

You can also pass a prepared command directly or reopen a saved `cactus-prepare` output:

```bash
cax --prepare-args "examples/evolverMammals.txt --outDir steps-output --outSeqFile ... --outHal ... --jobStore jobstore"
cax --from-file steps-output/prepare_output.txt
```

Inside the UI, CAX renders the alignment tree, shows cactus vs. RaMAx state for each round, lets you toggle RaMAx replacements, and can run or export the generated command list. For the full UI workflow, keyboard shortcuts, Mash behavior, resume mode, templates, and history, see [Run with UI](doc/run-with-ui.md).

The Run Settings screen shows the detected resource source and effective CPU
and memory limits. Both values can be lowered before previewing, exporting, or
running the final command list.

When RaMAx is enabled for a round or subtree, execution stops on the first failure—it does not fall back to cactus `blast`/`align` automatically.

## Templates and history

- Built-in templates are sourced from the packaged Evolver mammals/primates examples and any `.txt` files you add under `examples/`; user-defined templates live in `~/.cax/templates.json`.
- Command history is stored at `~/.cax/history.json`. It deduplicates consecutive runs, keeps up to 20 entries, and syncs with the Textual setup screen so you can reuse or delete past inputs.

## Logging and troubleshooting

- The raw output from `cactus-prepare` is stored at `<out_dir>/cax_prepare_debug.txt`. If you only passed `--outSeqFile`, the parent directory of that file becomes the inferred output directory.
- Runtime logs reuse the directories referenced by the original plan, for example `steps-output/logs/`.
- When enabled, Mash pairwise cache is stored under `<out_dir>/logs/mash_pair_cache_k31_s20000.json`.
- Command history and templates live under `~/.cax/` so you can reuse them across projects or machines.

## Feedback

Open an issue or pull request to help us iterate on the combined Cactus/RaMAx workflow.
