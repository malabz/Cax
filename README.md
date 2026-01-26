# Cactus-RaMAx

Cactus-RaMAx helps you remix alignment plans emitted by `cactus-prepare`. You can inspect every round, toggle RaMAx for any subtree, and then run or export the resulting command list. The current version (`0.6.1`) keeps the ASCII phylogenetic canvas with subtree/single-node toggle scopes, search, proportional branch spacing, and a bottom HUD that summarizes the current node, coverage, and live system metrics. Subtree Mode is a CAX-only toggle that disables descendant RaMAx automatically and gracefully reverts if you later edit a child node.

## Environment setup

We recommend creating a fresh Conda environment and installing the project in editable mode:

```bash
conda create -n cax python=3.10 -y
conda activate cax

pip install -e .
```

Alternatively, you can build a wheel and install it in a different environment:

```bash
python -m build
pip install dist/cactus_ramax-*.whl
```

### Optional but recommended: install Mash

Mash is used for automatic RaMAx preselection (Mash distance). If `mash` is not available on `PATH`, you can still use CAX normally, but Mash-based defaults and threshold recomputation will be skipped.

Common install options:

```bash
# Conda (recommended)
conda install -c bioconda mash

# Homebrew (macOS)
brew install mash
```

## Quick start

### 1. Launch the interactive UI

Run the entry point directly:

```bash
cax
```

- If you do not pass `--prepare-args` or `--from-file`, a Textual prompt opens so you can type or assemble a full `cactus-prepare` command.
  - Press **F2** (or type `:wizard`) to open the argument wizard and fill `--outDir`, `--outSeqFile`, `--outHal`, and `--jobStore` one field at a time.
  - Press **F3** (or type `:template`) to choose from Evolver examples bundled with the package or from your own `~/.cax/templates.json`.
  - Press **F4** or type `!N` (for example `!1`) to recall the Nth entry from `~/.cax/history.json`. The prompt keeps the 20 most recent commands and lets you delete entries from the history window.
- Before running `cactus-prepare`, CAX infers the effective output directory (from `--outDir` or the parent directory of `--outSeqFile`) and offers to delete existing `--outDir`/`--jobStore` paths so the run starts cleanly.
- If `logs/run_state.json` is present and you choose to keep existing outputs, the UI opens directly into a resume view (inside Run Settings) showing which steps can be skipped, which will rerun, and where execution resumes.
- After execution completes, the UI displays the parsed plan and lets you toggle RaMAx replacements before running or exporting.
- Scripted usage is still supported:
  ```bash
  cax --prepare-args "examples/evolverMammals.txt --outDir steps-output --outSeqFile ... --outHal ... --jobStore jobstore"
  ```
  or load an existing output:
  ```bash
  cax --from-file steps-output/prepare_output.txt
  ```
- Pass `--threads 32` to seed the run-settings prompt so cactus steps inherit `--maxCores 32` and RaMAx receives `--threads 32`; leave it unset to default to each command's original flag.
- If `mash` is available on `PATH`, CAX can preselect RaMAx rounds automatically using Mash distance.
  - Defaults: `mash dist -k 31 -s 20000` + threshold `0.02` (override with `--mash-threshold 0.01`).
  - Semantics: for each round, CAX checks **pairwise leaf distances inside that subtree** with early stop. If any pair exceeds the threshold, the round is *not* auto-enabled.
  - UX: by default, CAX will ask before computing Mash (`--no-ask-mash` to skip the prompt). Mash pair distances are cached under `<out_dir>/logs/` so repeated runs are fast. In the tree, `Mash:0.0145@cb` means the value comes from descendant node `cb` (a witness / max source), not necessarily the current node.
- If your inputs reference remote URLs (either directly in `--outSeqFile` or via the `cactus-preprocess` input seq file, as in the bundled Evolver examples), pass `--cache-seqs` to download them into a local cache and rewrite the plan to use the cached files. This avoids repeated downloads during cactus execution and enables Mash auto-selection to run on local inputs.
  - When Mash auto-selection is enabled (default), CAX will prompt you to cache remote URLs automatically before the UI opens.

### 1b. Run without UI (auto mode)

`cax auto` skips the UI and executes the plan immediately. Auto mode **requires Mash** (it will exit if `mash` is missing).

**Recommended short form (seqfile + threshold):**

```bash
cax auto --seqfile examples/evolverMammals.txt --mash-threshold 0.02
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

### 2. Work inside the UI

- The left pane renders an ASCII phylogenetic canvas with proportional branch spacing; use arrow keys or **h/j/k/l** to move, press **Space** to toggle RaMAx using the current scope, and press **b** to switch the scope between subtree and single node. Press **/** to search node names, then **n** / **Shift+N** to cycle through matches.
- The canvas paints cactus vs. RaMAx states inline, annotates branch lengths on dotted leaders, and shows a bottom HUD with identity, subtree/total RaMAx coverage, and live CPU/GPU/memory/disk metrics. Press **i** for a full detail modal of the current node.
- The right-hand detail pane shows the selected round's Mash distance (when available) and explains whether it is a subtree max (all pairs checked) or a witness (early-stop). Press `T` to adjust the threshold and recompute / reselect automatically when Mash is enabled.
- `E`: edit commands for the selected round or RaMAx replacement in a multi-line editor (press **Ctrl+S** to save).
- `R`: open the Run Settings screen to review verbose logging and the shared thread count, run the plan, or save the generated command list. The screen is fully keyboard-driven (`Tab` / `Shift+Tab`, **Ctrl+Enter**, **V**), and **F6** switches between the classic plan overview and a new flow view that renders the execution dependency tree in ASCII.
- `Q`: quit the UI.
- Verbose streaming is only controlled via the run-settings dialog so you can review the choice right before execution.

When RaMAx is enabled for a round or subtree, execution stops on the first failure—it does not fall back to cactus `blast`/`align` automatically.

### 3. Templates and history (optional)

- Built-in templates are sourced from the packaged Evolver mammals/primates examples and any `.txt` files you add under `examples/`; user-defined templates live in `~/.cax/templates.json`.
- Command history is stored at `~/.cax/history.json`. It deduplicates consecutive runs, keeps up to 20 entries, and syncs with the Textual prompt so you can reuse or delete past commands.

## Logging and troubleshooting

- The raw output from `cactus-prepare` is stored at `<out_dir>/cax_prepare_debug.txt`. If you only passed `--outSeqFile`, the parent directory of that file becomes the inferred output directory.
- Runtime logs reuse the directories referenced by the original plan, for example `steps-output/logs/`.
- When enabled, Mash pairwise cache is stored under `<out_dir>/logs/mash_pair_cache_k31_s20000.json`.
- Command history and templates live under `~/.cax/` so you can reuse them across projects or machines.

## Feedback

Open an issue or pull request to help us iterate on the combined Cactus/RaMAx workflow.
