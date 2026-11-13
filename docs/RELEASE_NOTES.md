# namiyomi ver 0.2.0

namiyomi is a free desktop editor for routine Sanger sequencing work. It brings AB1 chromatograms, read trimming, selected-read assembly, consensus editing, and FASTA export into one workspace. Assembly and editing run locally. Online AI review is optional.

## Download namiyomi ver 0.2.0 here

- [Download for macOS — Apple Silicon, macOS 15+](https://github.com/yoneoka-katsuhiro/namiyomi/releases/download/v0.2.0/namiyomi-0.2.0-macOS-arm64-development.zip)
- [Download for Windows — Windows 10/11 x64, experimental](https://github.com/yoneoka-katsuhiro/namiyomi/releases/download/v0.2.0/namiyomi-0.2.0-Windows-x64.zip)

These are the ready-to-run applications. You do not need the repository's source-code ZIP or a separate Python installation.

![namiyomi's consensus and aligned chromatograms on macOS. The data are synthetic.](https://raw.githubusercontent.com/yoneoka-katsuhiro/namiyomi/main/docs/images/namiyomi-contig-macos.png)

*The consensus stays above its supporting chromatograms so each call can be checked against the original traces. This example uses the synthetic demonstration data supplied with namiyomi.*

## Changes since 0.1.0

- **Review Start checks flagged bases against the traces.** **AI Review > Review Start** assembles unused reads by filename prefix, then settles flagged columns with local trace rules: a base is set when one read is clean there or the peak is clear, and an extra base that the better read does not support is removed. This step runs on your device and needs no API key.
- **Optional online model for what remains.** OpenAI, Claude and Gemini are supported. Only the columns the local rules left open are sent, as sampled trace signal without file names. An answer is applied only when the model is confident and the signal supports it; otherwise the column stays flagged in red with the model's suggestion in the status bar.
- **Measured on held-out reads.** On 3,795 rbcL reads, the local rules decided 48% of flagged columns with 4 disagreements among 2,846 decisions (0.14%) against a high-quality held-out read used as a reference. The data came from one locus and one laboratory. `scripts/benchmark_trace_review.py` repeats the test on your own reads. See [How Review Start decides](https://github.com/yoneoka-katsuhiro/namiyomi/blob/main/docs/TRACE_REVIEW.md).
- **Batch command.** `src/batch.py` assembles a folder of AB1 files, optionally by a CSV sample sheet, and writes FASTA, a project and a triage summary. `--review` applies the same review and lists every decision in `review.tsv`.
- **Read-only MCP quality summary** for one saved project (`src/mcp_server.py`).
- **Double peaks as IUPAC codes** (Assembly Settings, off by default) and **smoothed chromatogram display** (Appearance).
- Projects saved in 0.2.0 keep their assembly status when the project and source AB1 files move together. Projects that contain review decisions cannot be opened in 0.1.0.

## Features

- AB1 import with configurable automatic end trimming and manual cuts.
- Manual contig assembly from two or more selected reads, plus optional filename-prefix AUTO assembly.
- Aligned source traces, original Phred bars, consensus Support Q, and review flags.
- Review Start: automatic trace check of flagged columns, with an optional online model.
- Base editing with linked consensus updates, Undo/Redo, and reverse complement.
- Both-strand sequence search, with exact or 80–100% identity matching.
- Project saving, local recovery snapshots, and FASTA export/copy.
- **Return Reads to AB1** to undo an assembly while retaining read edits and trimming.

![Orange highlights show a Find match in the synthetic consensus.](https://raw.githubusercontent.com/yoneoka-katsuhiro/namiyomi/main/docs/images/namiyomi-find-macos.png)

*Find checks the query and its reverse complement. The default is exact matching; the identity threshold can be changed in Settings.*

## Download

| File | Target |
| --- | --- |
| `namiyomi-0.2.0-macOS-arm64-development.zip` | Apple Silicon, macOS 15 or later. Not Intel or universal. |
| `namiyomi-0.2.0-Windows-x64.zip` | Windows 10/11 x64; experimental. |

Extract the ZIP before opening the app. A separate Python installation is not required. On Windows, open `namiyomi.exe` from the extracted folder and keep its `_internal` folder beside it.

**macOS signing:** this is an ad-hoc signed development package, not Developer ID signed or Apple notarized. macOS may warn about or block it. **Windows signing:** the executable is not Authenticode signed and may also trigger security warnings. Do not disable system security protections to install either package.

**Windows verification:** the Windows package is produced by the repository's [Actions workflow](https://github.com/yoneoka-katsuhiro/namiyomi/actions), which runs the tests and a GUI startup check on Windows Server 2022. Check the run for this release. Full hands-on testing on a physical Windows PC has not been completed, and errors may occur.

## Before using your own data

Version 0.2.0 is an early release. Keep independent backups and inspect exported sequences, including columns settled by Review Start. Project JSON files reference the original AB1 files; keep them together when moving a project. Support Q is supporting original Phred evidence, not a calibrated combined probability. Assembly is intended for small sets of overlapping reads from the same specimen and locus.

Both application ZIPs include a `demo` folder. For a first look, open its `Demo.json` through **File > Import Previous Project**. Keep the four AB1 files beside the project. These files contain synthetic demonstrations, not biological observations or instrument measurements. Use **Save Project As** if you want to keep your practice session separately.

The application and source code are free under the MIT License. See the [English README](https://github.com/yoneoka-katsuhiro/namiyomi#readme) or [日本語の説明](https://github.com/yoneoka-katsuhiro/namiyomi/blob/main/README-ja.md) for instructions, limitations, and a comparison with other Sanger tools.

Developed by Katsuhiro Yoneoka, Mishima, Shizuoka, Japan. [Report a problem](https://github.com/yoneoka-katsuhiro/namiyomi/issues).
