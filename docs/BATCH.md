# Local batch processing and MCP review

These optional tools reuse namiyomi's AB1 trimming, assembly and review flags. The
GUI and command line leave the original AB1 files alone. These instructions
describe the source branch with MCP; the original v0.1.0 downloads predate it.

## From the namiyomi window

1. Import AB1 reads or open an existing project. To try assembly from scratch,
   import the four synthetic AB1 files in `demo/`.
2. Open **MCP > Settings**. Select **OpenAI (ChatGPT)**, **Anthropic (Claude)** or **Google (Gemini)**,
   enter the API key, and choose English or Japanese for the report.
   **Refresh Models** fetches current model IDs from the provider without sending project data.
   Pick one from the Model ID list or type an ID directly. Refreshing keeps your selected model.
   Providers can list models that do not support text reports; use the connection test to confirm.
   **Test Connection** sends a short test prompt and can incur an API charge.
   The API account needs access to the selected model. The ChatGPT and Claude
   web app subscriptions do not act as API credentials.
3. Click **Apply**, then **MCP > Full auto mode by LLM > OK**. Local assembly uses
   the same filename prefix rule as **Build Contig (AUTO)**. Each prefix must
   refer to one specimen and one locus. Previously assembled contigs keep their
   edits and flags. If all reads are assembled, this step reports on the current
   contigs. You can undo newly assembled contigs together with **Edit > Undo**.
4. Review the table and report. Select a contig and click **Open Contig** to
   inspect the traces. Save or export using the normal File menu. If the API
   call fails, the local contigs and quality results are still available.

**Offline preview** is a provider option that runs real local assembly and shows
a built-in report without calling an API. It uses the same dialogs as an online run.

For Gemini, create a key in [Google AI Studio](https://aistudio.google.com/apikey).
The default is `gemini-3.5-flash-lite`, which has a free tier for text generation
subject to your project's eligibility and quota. Check [Google's current pricing](https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite)
and data use terms; Google may use free-tier requests to improve its products.

Online providers receive quality metrics: read/contig counts, lengths, overlap
statistics, counts of flags and Ns, and numbered IDs for up to 50 contigs in review
priority order. The result table maps those IDs to names locally. No bases,
chromatogram images, sample labels, filenames, paths, or assembly error text are
sent. Only the provider, model and language are saved in app settings. A key
typed in the dialog lasts until the app closes; alternatively launch with
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` set in the environment. OpenAI calls use
`store: false`. The report helps prioritize review; it cannot resolve individual
base calls or mark them as reviewed.

## Command line

Install Biopython with `python -m pip install -r requirements/batch.txt`. Use
Python 3.10 or later. Neither command below needs Tk or an API key.

```sh
python src/batch.py --input demo --dry-run
python src/batch.py --input demo --output ./demo-results
```

Supply a **new** output directory. Results include `project.json` (open with
**File > Import Previous Project**), `summary.json`, `summary.tsv`, and FASTA
files grouped by marker. Summary rows distinguish no flags, needs review,
single read and failed assembly. Review flag coordinates in the CLI reports are
zero-based alignment columns. Single-read FASTA records are excluded unless
`--allow-single` is set. Keep the source AB1s in place so the project can reopen.

The default grouping uses filenames before the first underscore. With
`--sheet samples.csv`, groups are defined by the `file,sample,marker` columns
instead. Use the original filename, including `.ab1`, in the `file` column. Each
sample name must be a valid FASTA identifier. Files absent from the sheet are
skipped and reported. Optional flags include `--min-quality`, `--min-overlap`,
`--min-identity` (a fraction), and `--no-trim`. Use `--help` for the complete list.

## Read-only MCP server for external hosts

A local host supporting MCP over stdio can connect to the same quality tool.
For example, configure a host with these command and argument values, replacing
the paths with absolute paths for your machine:

```json
{
  "mcpServers": {
    "namiyomi": {
      "command": "/path/to/.venv/bin/python",
      "args": [
        "/path/to/namiyomi/src/mcp_server.py",
        "--project",
        "/path/to/demo-results/project.json"
      ]
    }
  }
}
```

The server reads the explicitly selected saved project at startup and exposes
one tool, `get_quality_summary`. Restart it to pick up a new save. The server
has no network listener and cannot accept a new project path, edit bases, trim
contigs or export files through MCP. The connected host may send the returned
quality metrics to its model. The GUI's MCP menu does not require setting up
an external host.

The batch and triage work is based on Joel Nitta's [PR #2](https://github.com/yoneoka-katsuhiro/namiyomi/pull/2).
