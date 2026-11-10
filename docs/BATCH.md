# Batch mode and Claude (MCP) integration

For large numbers of AB1 files. The GUI is unchanged; these tools reuse its code (`core.py`, `assembly.py`)
and write only to an output folder you choose. **Original AB1 files are never modified or moved.**
Everything runs locally; no sequence data is uploaded by this software.

## Install

Python 3.10 or later is required (the `mcp` package needs it). The `/usr/bin/python3` that ships with macOS is
3.9; install a newer Python from python.org or Homebrew. On macOS the command is `python3`, not `python`.

```
python3 -m venv .venv                                # Windows: py -m venv .venv
.venv/bin/pip install -r requirements/batch.txt      # Windows: .venv\Scripts\pip
```

`requirements/batch.txt` does not need tkinter, tkinterdnd2 or PyInstaller. To also open projects in the window
from source, add `requirements/runtime.txt` (it provides `tkinterdnd2`) and run
`.venv/bin/python src/app.py --project OUT/project.json`.

## Command line

```
python src/batch.py --input ~/projects/run42/reads --output ~/projects/run42/results [--sheet sheet.csv] [--allow-single] [--dry-run]
```

- `--output` is required and must be a different folder from `--input`.
- `--dry-run` lists the planned groups and writes nothing.
- Optional: `--min-quality`, `--min-overlap`, `--min-identity`, `--no-trim` (same meaning as in the app).

### Grouping

- **Default (no sheet):** like *Build Contig (AUTO)* in the app: files are grouped by the text before the first
  underscore (`Demo01_F.ab1`, `Demo01_R.ab1` -> `Demo01`). Each prefix must be one specimen and one locus.
  All FASTA records go to `auto.fasta`.
- **Sample sheet:** a CSV with the columns `file,sample,marker`:

  ```
  file,sample,marker
  S07_rbcL_F.ab1,S07,rbcL
  S07_rbcL_R.ab1,S07,rbcL
  S07_matK_F.ab1,S07,matK
  ```

  `file` is the file name inside the input folder. One FASTA per marker is written (`rbcL.fasta`, `matK.fasta`),
  with the sample as the sequence name. Files missing from the sheet are skipped and reported; sheet rows with no
  file are reported. Sample names cannot contain whitespace, `>` or `|`.

### Triage

| Category | Meaning |
|---|---|
| `clean` | Two or more reads assembled, not stale, no flagged columns. |
| `needs_review` | Assembled, but one or more columns are flagged (conflicting high-quality calls, internal gaps, `N`). |
| `single_read` | One usable read only. This includes pairs where the other read was trimmed to nothing (reported). Not exported unless `--allow-single`. |
| `failed` | Assembly raised (e.g. no sufficient overlap); the error is reported. One failing group never stops the batch. |

Also reported: AB1 files that could not be loaded, reads trimmed to nothing (these are left out of assembly
so the rest of the group can still assemble), and contigs not exported because the sample name is not a valid
FASTA identifier.

### Outputs (in `--output`)

`project.json` (open in the app with *File > Import Previous Project*), `summary.json`, `summary.tsv`, and the
FASTA files. `project.json` refers to the AB1 files by relative path, so keep the originals where they are (or
move both folders together). Rerunning into the same folder overwrites these files. FASTA and summary contents
are deterministic; `project.json` contains random read IDs, so it differs byte-for-byte between runs.

## Claude Desktop / Claude Code (MCP)

Add the server (use absolute paths to the venv's Python and to `src/mcp_server.py`).

**Claude Code**: run from the namiyomi folder.

```
# macOS
claude mcp add -s user namiyomi -- "$(pwd)/.venv/bin/python" "$(pwd)/src/mcp_server.py"
# Windows (PowerShell)
claude mcp add -s user namiyomi -- "$PWD\.venv\Scripts\python.exe" "$PWD\src\mcp_server.py"
```

Use `-s user`. Without it Claude Code uses the *local* scope, which only applies in the folder where the command
was run (the namiyomi folder), so the tools would be missing when you start Claude in your data folder.
Check with `claude mcp list` (expect `namiyomi: … ✔ Connected`), then start a new session: MCP tools are loaded
only when a session starts. In a terminal, quit and run `claude` again; in VS Code, start a new conversation or
run *Developer: Reload Window*.

**Claude Desktop**: edit the config file, then quit the app fully and reopen it.

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "namiyomi": {
      "command": "/path/to/namiyomi/.venv/bin/python",
      "args": ["/path/to/namiyomi/src/mcp_server.py"]
    }
  }
}
```

On Windows use `C:\\path\\to\\namiyomi\\.venv\\Scripts\\python.exe` and `C:\\path\\to\\namiyomi\\src\\mcp_server.py`.

### Tools

| Tool | Purpose |
|---|---|
| `scan_folder` | Dry run: AB1 count and proposed groups, plus sheet problems. |
| `run_batch` | Run the batch (same as the CLI). `review` = `human` (default) or `claude`. |
| `list_contigs` | Contigs of a saved `project.json`, filtered by `clean` / `needs_review` / `single_read`. |
| `get_contig_details` | Flagged columns, each source read's call, Phred score and A/C/G/T peak heights. |
| `get_column_qualities` | Every column of a contig (or a range) with each read's base and Phred, for choosing a trim point. |
| `describe_contig_ends` | Per contig: read layout, the two-read region, each single-read end (length, mean Phred, `N`s, longest `N` run), and what candidate trimming rules would keep. |
| `suggest_quality_trim` | Dry run of one trimming rule (rolling-mean Phred and/or cut before a run of `N`s; single-read ends only; isolated `N`s kept); returns ready `trim` edits. |
| `find_sequence` | Search a contig's consensus (both strands). |
| `render_trace` | PNG of the chromatograms around a column, so Claude can look at the peaks. |
| `apply_review_edits` | Apply review verdicts (set a base, confirm a flag, or trim the ends) to a **new** project file and log reasons in `review_report.md`, plus the flags left unresolved (with optional reasons). |
| `export_fasta` | FASTA per marker from a saved project. From `*.reviewed.json` the files are `<marker>.reviewed.fasta`, so the unedited FASTA is kept. |

### Choosing who reviews the flags

- **`review=human` (default).** Claude runs the batch and reports the triage. It makes no edits. You open
  `project.json` in the app and check every flagged contig against the traces.
- **`review=claude`.** For each `needs_review` contig Claude reads the per-read calls and Phred scores, looks at
  `render_trace` images, and applies only the changes it is confident about (a changed call, or "confirm
  as is") to a copy, `project.reviewed.json`. Each edit and its reason is written to `review_report.md`.
  Anything uncertain is left unedited and listed under *Left unresolved* in the report. You then spot-check
  the report and the reviewed project in the app.

Most flags in typical runs are single-read positions: low-Phred bases at the 3′ end of one read, beyond the
other read's coverage. Such contigs are longer than the clean ones. These positions cannot be confirmed by a
second read. How far to keep them is a judgement call: trimming strictly to the region both reads cover can
discard many usable bases, so trimming the single-read end by quality and accepting an occasional `N` is often a
better balance. Do it in the app, or ask Claude for help.

### End trimming with Claude

1. **Compare.** `describe_contig_ends` lists each contig's single-read ends and what these rules would keep
   (length and `N` count): keep everything; rolling mean Q20 over 10 columns; rolling mean Q30 over 20;
   cut before a run of 4, 3 or 1 `N`; the region covered by two reads. Pass `options` to compare other rules.
2. **Preview.** `suggest_quality_trim` applies one rule and returns the cut per contig. Rules:
   - `min_mean_phred` with `window`: column quality is the highest Phred of the covering reads; each end is
     trimmed while the mean of the next `window` columns is below the threshold. With `keep_isolated_n`
     (default), a single `N` is left out of the mean.
   - `max_n_run`: walking outward from the two-read region, cut just before the first run of that many `N`s.
   - Both together: the stricter cut wins at each end. `single_read_ends_only` (default) never cuts into the
     two-read region.
3. **Apply.** After you confirm, Claude passes the returned `trim` edits to `apply_review_edits`. This deletes
   the consensus columns outside the kept range (the same as deleting them in the app) and logs the rule in
   `review_report.md`. Then `export_fasta` writes `<marker>.reviewed.fasta`.

The MCP prompt `trim_contig_ends` (in Claude Code: `/mcp__namiyomi__trim_contig_ends`) walks Claude through
these steps. Claude trims only when asked. Batch reads are already Mott-trimmed at Q20, so a rolling-mean
rule often removes little; on reads with long single-read tails, the number of `N`s you accept usually
decides the length.

Claude's reading of a chromatogram is a second opinion, not ground truth. As the README says, check exported
sequences before using them in analysis. In either mode `project.json` stays the untouched machine output, so
you can always compare, or switch to human review of the same batch.

### Example

> Run the batch on ~/projects/run42/reads with sheet.csv, output to ~/projects/run42/results.

Claude calls `scan_folder`, then `run_batch`, and reports e.g. "52 clean, 5 needs review, 2 single-read, 1 failed".
Add "and review the flags yourself" to switch to `review=claude`.
