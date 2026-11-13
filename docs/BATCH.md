# Batch processing and Review Start

These optional tools reuse namiyomi's AB1 trimming, assembly and review flags. The
GUI and command line leave the original AB1 files alone.
[How Review Start decides](TRACE_REVIEW.md) explains the rules and how they were measured.

## From the namiyomi window

1. Import AB1 reads or open an existing project. To try assembly from scratch,
   import the four synthetic AB1 files in `demo/`.
2. Optional: open **AI Review > Settings** to add an online model. Select
   **OpenAI (ChatGPT)**, **Anthropic (Claude)** or **Google (Gemini)**, enter the
   API key, and choose English or Japanese for the report. **This device only**
   runs the local trace rules without any API request.
   **Refresh Models** fetches current model IDs from the provider without sending project data.
   Pick one from the Model ID list or type an ID directly.
   Choose **Reasoning effort** or leave it at **Default**. The supported levels vary by model.
   **Test Connection** sends a short test prompt and can incur an API charge.
   The API account needs access to the selected model. The ChatGPT and Claude
   web app subscriptions do not act as API credentials.
3. Open **AI Review > Review Start**. Local assembly uses the same rule as
   **Build Contig (AUTO)**: group by the filename before the first underscore.
   For rbcL files, rename them before importing, for example
   `64_rbcL_af.ab1` and `64_rbcL_cr.ab1`. Use IDs without underscores and
   make each prefix refer to one specimen and one locus across the project.
4. The review runs in two steps. The local trace rules check eligible flagged columns first. They set a base when one read is clean there or the peak is
   clear, and they remove an extra base that the better read does not support.
   With an online provider, the columns still open are then sent as sampled
   trace signal, nearby aligned calls and Phred scores. An answer is applied
   only when the model is confident and the signal supports it. Manual
   consensus edits and columns marked reviewed are never changed. Rebuild stale
   contigs before reviewing.
   **Edit > Undo** reverses the assembly and all decisions together.
5. The **AI Review Report** gives the assembly counts, how many flags the
   local rules and the model settled, and what is still open.
   Click a result row to jump to its first red flag. Repeat
   to visit each remaining red flag and then return to the first. A contig with
   only open black flags opens at the first one. Red flags are columns left
   open after the online model reviewed them; the status bar shows the model's suggestion when it had one. A settled
   column has a teal underline, keeps its black history flag and no longer
   counts as an open issue. The results window stays open while you inspect
   and edit the project. Save or export using the normal File menu.

For nuclear loci, **Assembly Settings > Call clear double peaks as IUPAC codes**
is an opt-in setting. The smaller peak must be at least 65% of the taller peak
at the same position, with clear neighboring peaks. A detected pair such as A/G
is represented as R and flagged for review. Rebuild existing contigs after
changing the setting.

**Settings > Appearance > Smooth chromatogram display** interpolates between
the displayed samples. It preserves peak heights and leaves base calling and
quality calculations on the original AB1 measurements.

**This device only** is a provider option that runs the assembly and the local
trace rules without calling an API. It uses the same dialogs as an online run.

For Gemini, create a key in [Google AI Studio](https://aistudio.google.com/apikey).
The default is `gemini-3.5-flash-lite`, which has a free tier for text generation
subject to your project's eligibility and quota. Check [Google's current pricing](https://aistudio.google.com/docs/pricing#gemini-3.5-flash-lite)
and data use terms; Google may use free-tier requests to improve its products.

Online providers receive, for each open column, the sampled four-channel signal
around it, nearby base calls, Phred scores and anonymous read labels. Images,
sample names, filenames, paths and absolute sequence positions stay on the
device. The app sorts contigs and formats the report locally. When the local
rules settle every flag, no review request is sent. Provider,
model, effort and language are saved in app settings. A key
typed in the dialog lasts until the app closes; alternatively launch with
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY` set in the environment. OpenAI calls use
`store: false`. A report with no warnings does not certify sequence accuracy.

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

Add `--review` to settle flagged columns with the local trace rules before the
FASTA files are written. The decisions are listed in `review.tsv`, one row per
reviewed column, and saved in `project.json`. This needs no API key.

```sh
python src/batch.py --input demo --output ./demo-reviewed --review
```

To send the columns that remain to an online model as well, name the provider.
The key is read from `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`.

```sh
python src/batch.py --input reads --output ./reviewed --review \
    --ai-provider openai --ai-model gpt-6-luna --ai-effort high
```

`scripts/benchmark_trace_review.py --input FOLDER` measures the local rules on
your own reads by holding one read out of each group.

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
one tool, `get_quality_summary`. Its counts include the columns that the review
settled or removed. Restart it to pick up a new save. The server
has no network listener and cannot accept a new project path, edit bases, trim
contigs or export files through MCP. The connected host may send the returned
quality metrics to its model. The GUI's AI Review menu does not require setting up
an external host.

The batch and triage work is based on Joel Nitta's [PR #2](https://github.com/yoneoka-katsuhiro/namiyomi/pull/2).
