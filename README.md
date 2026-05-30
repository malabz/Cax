# Cactus-RaMAx

Cactus-RaMAx helps you remix alignment plans emitted by `cactus-prepare`. You can inspect every round, toggle RaMAx for any subtree, and then run or export the resulting command list. The current version (`0.6.1`) keeps the ASCII phylogenetic canvas with subtree/single-node toggle scopes, search, proportional branch spacing, and a bottom HUD that summarizes the current node, coverage, and live system metrics. Subtree Mode is a CAX-only toggle that disables descendant RaMAx automatically and gracefully reverts if you later edit a child node.

![CAX interactive UI demo](doc/assets/cax-ui-demo.gif)

## Installation

We recommend creating a fresh Conda environment and installing the project in editable mode:

```bash
git clone https://github.com/malabz/Cax.git
cd Cax

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

When RaMAx is enabled for a round or subtree, execution stops on the first failure—it does not fall back to cactus `blast`/`align` automatically.

## Templates and history

- Built-in templates are sourced from the packaged Evolver mammals/primates examples and any `.txt` files you add under `examples/`; user-defined templates live in `~/.cax/templates.json`.
- Command history is stored at `~/.cax/history.json`. It deduplicates consecutive runs, keeps up to 20 entries, and syncs with the Textual prompt so you can reuse or delete past commands.

## Logging and troubleshooting

- The raw output from `cactus-prepare` is stored at `<out_dir>/cax_prepare_debug.txt`. If you only passed `--outSeqFile`, the parent directory of that file becomes the inferred output directory.
- Runtime logs reuse the directories referenced by the original plan, for example `steps-output/logs/`.
- When enabled, Mash pairwise cache is stored under `<out_dir>/logs/mash_pair_cache_k31_s20000.json`.
- Command history and templates live under `~/.cax/` so you can reuse them across projects or machines.

## Feedback

Open an issue or pull request to help us iterate on the combined Cactus/RaMAx workflow.
