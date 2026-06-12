# Run with UI

The interactive UI is the default way to inspect a `cactus-prepare` plan before running it. It is useful when you want to review every round, toggle RaMAx for selected subtrees, adjust run settings, and export or execute the resulting command list.

## Launch the UI

Run the entry point directly:

```bash
cax
```

If you do not pass `--prepare-args` or `--from-file`, a Textual setup screen opens for the smallest required input: the species/seq file.

- Use the arrow keys to select fields directly. When a completion list is open, **Up/Down** select candidates instead, and **PgUp/PgDn** pages through long candidate lists.
- Type an input path and press **Tab** to complete it. In the advanced-args field, **Tab** completes options parsed from `cactus-prepare --help`. If the current field is already complete, **Tab** moves to the next field. If multiple candidates match, use **Up/Down** and **Enter** to choose.
- CAX generates `--outDir`, `--outSeqFile`, `--outHal`, and `--jobStore` from the input filename. You can edit the output and temporary/jobStore directories before starting.
- Use the other prepare advanced args field only for extra `cactus-prepare` flags such as `--defaultCores 32`.
- Press **Enter** to start from any field when no candidate list is open.
- Press **F3** to choose from Evolver examples bundled with the package or from your own `~/.cax/templates.json`.
- Press **F4** to recall an entry from `~/.cax/history.json`. The setup screen keeps the 20 most recent commands and lets you delete entries from the history window.

Scripted usage is also supported:

```bash
cax --prepare-args "examples/evolverMammals.txt --outDir steps-output --outSeqFile ... --outHal ... --jobStore jobstore"
```

Or load an existing `cactus-prepare` output:

```bash
cax --from-file steps-output/prepare_output.txt
```

Pass `--threads 32` to seed the run-settings prompt so cactus steps inherit `--maxCores 32` and RaMAx receives `--threads 32`. Leave it unset to default to each command's original flag.

## Prepare and resume behavior

Before running `cactus-prepare`, CAX infers the effective output directory from the generated or supplied `--outDir`. It then offers to delete existing `--outDir` and `--jobStore` paths so the run starts cleanly.

If `logs/run_state.json` is present and you choose to keep existing outputs, the UI opens directly into a resume view inside Run Settings. That view shows which steps can be skipped, which will rerun, and where execution resumes.

After `cactus-prepare` completes, the UI displays the parsed plan and lets you toggle RaMAx replacements before running or exporting.

## Mash and cached sequences

If `mash` is available on `PATH`, CAX can preselect RaMAx rounds automatically using Mash distance.

- Defaults: `mash dist -k 31 -s 20000` and threshold `0.02`.
- Override the threshold with `--mash-threshold 0.01`.
- For each round, CAX checks pairwise leaf distances inside that subtree with early stop. If any pair exceeds the threshold, the round is not auto-enabled.
- By default, CAX asks before computing Mash. Use `--no-ask-mash` to skip the prompt.
- Mash pair distances are cached under `<out_dir>/logs/` so repeated runs are fast.
- In the tree, `Mash:0.0145@cb` means the value comes from descendant node `cb`, as a witness or max source, not necessarily from the current node.

If your inputs reference remote URLs, either directly in `--outSeqFile` or through the `cactus-preprocess` input seq file as in the bundled Evolver examples, pass `--cache-seqs`. CAX downloads the remote files into a local cache and rewrites the plan to use cached files. This avoids repeated downloads during cactus execution and enables Mash auto-selection to run on local inputs.

When Mash auto-selection is enabled by default, CAX will prompt you to cache remote URLs automatically before the UI opens.

## Navigate the plan

The left pane renders an ASCII phylogenetic canvas with proportional branch spacing.

- Use arrow keys or **h/j/k/l** to move.
- Press **Space** to toggle RaMAx using the current scope.
- Press **b** to switch the scope between subtree and single node.
- Press **/** to search node names, then **n** or **Shift+N** to cycle through matches.
- Press **i** for a full detail modal of the current node.

The canvas paints cactus vs. RaMAx states inline, annotates branch lengths on dotted leaders, and shows a bottom HUD with identity, subtree/total RaMAx coverage, and live CPU/GPU/memory/disk metrics.

The right-hand detail pane shows the selected round's Mash distance when available. It also explains whether that value is a subtree max, where all pairs were checked, or a witness from early stop. Press **T** to adjust the threshold and recompute or reselect automatically when Mash is enabled.

## Edit, run, and export

- Press **E** to edit commands for the selected round or RaMAx replacement in a multi-line editor. Press **Ctrl+S** to save.
- Press **R** to open the Run Settings screen to review verbose logging and the shared thread count, run the plan, or save the generated command list.
- The Run Settings screen is keyboard-driven with **Up/Down**, **Enter/R**, **E**, **V**, and **S**.
- Press **F6** in Run Settings to switch between the run summary and the generated command preview.
- Press **Q** to quit the UI.

Verbose streaming is controlled only through the run-settings dialog, so you can review the choice right before execution.

When RaMAx is enabled for a round or subtree, execution stops on the first failure. It does not fall back to cactus `blast` or `align` automatically.

## Templates and history

Built-in templates are sourced from the packaged Evolver mammals/primates examples and any `.txt` files you add under `examples/`. User-defined templates live in `~/.cax/templates.json`.

Command history is stored at `~/.cax/history.json`. It deduplicates consecutive runs, keeps up to 20 entries, and syncs with the Textual setup screen so you can reuse or delete past inputs.
