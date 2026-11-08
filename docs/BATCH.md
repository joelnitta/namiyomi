# Batch mode and Claude (MCP) integration

For large numbers of AB1 files. The GUI is unchanged; these tools reuse its code (`core.py`, `assembly.py`)
and write only to an output folder you choose. **Original AB1 files are never modified or moved.**
Everything runs locally; no sequence data is uploaded by this software.

## Install

```
python -m venv .venv
.venv/bin/pip install -r requirements/batch.txt      # Windows: .venv\Scripts\pip
```

`requirements/batch.txt` does not need tkinter, tkinterdnd2 or PyInstaller.

## Command line

```
python src/batch.py --input ~/seq/run42 --output ~/seq/run42_out [--sheet sheet.csv] [--allow-single] [--dry-run]
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
| `single_read` | One read only. Not exported unless `--allow-single`. |
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

**Claude Code**

```
claude mcp add namiyomi -- /path/to/namiyomi/.venv/bin/python /path/to/namiyomi/src/mcp_server.py
```

**Claude Desktop**: edit the config file and restart the app.

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
| `get_contig_details` | Flagged columns, each source read's call and Phred score. |
| `find_sequence` | Search a contig's consensus (both strands). |
| `render_trace` | PNG of the chromatograms around a column, so Claude can look at the peaks. |
| `apply_review_edits` | Apply review verdicts to a **new** project file and log reasons in `review_report.md`. |
| `export_fasta` | FASTA per marker from a saved project (e.g. the reviewed one). |

### Choosing who reviews the flags

- **`review=human` (default).** Claude runs the batch and reports the triage. It makes no edits. You open
  `project.json` in the app and check every flagged contig against the traces.
- **`review=claude`.** For each `needs_review` contig Claude reads the per-read calls and Phred scores, looks at
  `render_trace` images, and applies only the changes it is confident about (a changed call, or "confirm
  as is") to a copy, `project.reviewed.json`. Each edit and its reason is written to `review_report.md`.
  Anything uncertain is left unedited and listed for you. You then spot-check the report and the reviewed
  project in the app.

Claude's reading of a chromatogram is a second opinion, not ground truth. As the README says, check exported
sequences before using them in analysis. In either mode `project.json` stays the untouched machine output, so
you can always compare, or switch to human review of the same batch.

### Example

> Run the batch on ~/seq/run42 with sheet.csv, output to ~/seq/run42_out.

Claude calls `scan_folder`, then `run_batch`, and reports e.g. "52 clean, 5 needs review, 2 single-read, 1 failed".
Add "and review the flags yourself" to switch to `review=claude`.
