# namiyomi ver 0.0.1

A focused desktop editor for Sanger sequencing: inspect AB1 chromatograms, trim read ends, assemble selected reads, review the consensus, and export FASTA.

namiyomi keeps the sequence and its supporting traces in one workspace. It is designed for routine, small-scale Sanger work rather than as an integrated bioinformatics suite. All sequence processing takes place locally. The application and source code are free under the MIT License.

[Download](https://github.com/yoneoka-katsuhiro/namiyomi/releases) · [日本語](README-ja.md) · [Build and distribution guide](DISTRIBUTION.md)

![The consensus sequence above its aligned source chromatograms in namiyomi on macOS. The displayed data are synthetic.](docs/images/namiyomi-contig-macos.png)

*Review the consensus alongside its supporting chromatograms. Base calls, quality bars, and review flags share the same sequence coordinates. This screenshot uses the synthetic demonstration data supplied with the project.*

## Download and open

| Package | System | Release status |
| --- | --- | --- |
| `namiyomi-0.0.1-macOS-arm64-development.zip` | Apple Silicon, macOS 15 or later | Tested on the developer's Mac. Ad-hoc signed; not Developer ID signed or Apple notarized. Not an Intel or universal build. |
| `namiyomi-0.0.1-Windows-x64.zip` | Windows 10/11, x64 | Experimental. Full hands-on testing on a physical Windows PC has not been completed. Errors may occur. |

Download the appropriate ZIP from [Releases](https://github.com/yoneoka-katsuhiro/namiyomi/releases). Standalone packages include Python and their dependencies; a separate Python installation is not required.

The compressed downloads are approximately **41 MiB for macOS** and **26 MiB for Windows**, including the runtime and synthetic demo. These are package sizes, not memory-use or performance benchmarks.

**macOS:** extract the ZIP, open its `namiyomi` folder, then open `namiyomi.app`. You can move the application to an Applications folder if desired; keep the separate `demo` folder if you want to try the example project. This is an unnotarized development release. macOS may warn about or block the application; the package has not received Apple's notarization. See [Apple's explanation of Developer ID](https://developer.apple.com/developer-id/) before deciding whether to run it. Do not disable system security protections to install namiyomi.

**Windows:** extract the complete `namiyomi` folder, then open `namiyomi.exe`. Keep the `_internal` folder beside the executable; do not copy the executable out on its own. The executable is not Authenticode signed, so Windows may display a security warning. The Windows build workflow uses Windows Server 2022 for automated build and startup checks; consult [Actions](https://github.com/yoneoka-katsuhiro/namiyomi/actions) for the outcome of each run. Those checks do not replace hands-on testing of the complete workflow on Windows 10/11.

Version 0.0.1 is an early release. Back up your AB1 files and projects, and check exported sequences before using them in an analysis.

## What namiyomi does

- Imports AB1 files without assuming a particular filename pattern.
- Automatically trims low-quality ends on import, with adjustable settings and manual left/right cuts.
- Builds a contig from two or more selected reads and displays their aligned chromatograms beneath the consensus.
- Recalculates the linked consensus and its supporting-quality display when a source read is edited.
- Provides review flags, base editing, reverse complement, Undo/Redo, and sequence/FASTA copying.
- Finds consensus subsequences on either strand, with exact matching by default and an adjustable 80–100% identity threshold.
- Saves editable projects, writes local recovery snapshots, and exports FASTA without modifying the original AB1 files.

The interface deliberately omits cloning design, restriction mapping, BLAST, phylogenetics, and genome-scale assembly. Its scope is the work between opening Sanger reads and obtaining a reviewed sequence. No account or cloud service is needed to use the application, and sequence data are not uploaded.

## Try the demonstration

Both application ZIPs include a `demo` folder with four synthetic AB1 files and a prepared `Demo.json` project. The same files are available in the repository's [`demo`](demo) directory. The sequences and trace signals were generated for demonstration; they are **not biological observations or instrument measurements**.

Keep the files in `demo` together, then choose **File > Import Previous Project** and open `demo/Demo.json` from the extracted application folder. You can inspect the prepared contigs, try Find, edit a base, and Undo the change. Use **Save Project As** to save your practice session separately. To practise assembly from scratch, start a new project and import the four demonstration AB1 files instead.

## A typical session

1. **Import the reads.** Choose **File > Import AB1** or drag AB1 files into the window. Reads remain separate in **AB1 files**; importing does not group them or build contigs. Automatic end trimming is enabled by default. Adjust **Settings > Trimming Settings** before importing if needed.
2. **Inspect the chromatograms.** Check trimmed ends and ambiguous calls. Gray regions are excluded from the retained sequence. Adjust **Base spacing** and **Trace height** at the bottom right. Use **Edit > Cut Left / Cut Right** for additional cuts.
3. **Select reads from one specimen and one locus.** Shift-click the relevant entries, then choose **Assembly > Build Contig (manual)**. Three or more reads are supported. The assembler checks both orientations; names and primer labels do not determine the alignment.
4. **Review the consensus.** Examine flagged positions against the traces. Click a base to edit it; Backspace removes selected calls. Source-read edits recalculate the linked consensus. Use **Review > Mark Reviewed** after checking a flagged call. A review acknowledgement does not improve its quality score.
5. **Save and export.** **Save Project** first asks for a filename, then updates that same file. **Export Project Copy** leaves the current save destination unchanged. Select a read or contig and choose **Export FASTA**, or use **Copy FASTA** from its right-click menu.

If an assembly needs to be redone, right-click its contig and choose **Return Reads to AB1**. The reads keep their edits and trimming. The removed contig's consensus-only edits and review flags are not transferred to them; Undo restores the whole operation.

### Optional filename-based assembly

**Assembly > Build Contig (AUTO)** groups unused reads by the part of the original filename before its first underscore. For example, `sample07_forward.ab1` and `sample07_reverse.ab1` form the group `sample07`. A successful group receives that name as its contig identifier.

Use AUTO only when each prefix identifies one specimen **and one locus**. Reads already used by an existing manual or AUTO contig are excluded, and existing contigs are not rebuilt. Use the manual command when filenames do not follow this convention. A single-read group can form a contig, but it does not provide independent multi-read confirmation.

### Find a sequence on either strand

![Orange highlighting marks a sequence match in a synthetic consensus, with its source chromatograms below.](docs/images/namiyomi-find-macos.png)

*Enter a DNA sequence in Find to highlight matching consensus sites. The search also checks the reverse complement. These demonstration traces are synthetic.*

The default is 100% identity. **Settings > Find Settings** permits 80–100% identity for substitution-only, ungapped matching. IUPAC letters match literally, not as wildcard sets. Search includes edited consensus calls and insertions, excludes deleted calls, and never changes the sequence. Use the arrow buttons or Enter / Shift+Enter to move between matches; Esc clears the query.

### Useful shortcuts

| Action | macOS | Windows |
| --- | --- | --- |
| Save project | ⌘S | Ctrl+S |
| Undo / Redo | ⌘Z / ⇧⌘Z | Ctrl+Z / Ctrl+Shift+Z |
| Build Contig (manual) | ⌘B | Ctrl+B |
| Copy sequence / Copy FASTA | ⌘C / ⇧⌘C | Ctrl+C / Ctrl+Shift+C |
| Find | ⌘F | Ctrl+F |
| Next / previous review flag | ⌘E / ⇧⌘E | Ctrl+E / Ctrl+Shift+E |

Right-click the sidebar for common actions. Double-click a sequence name to rename it. Double-click **Sequences** or **Length (bp)** to sort reads and contigs, then repeat to reverse the order. Delete, return-to-AB1, and export-with-flags confirmations offer **Don't ask again for this action**; **Settings > Confirmations** restores each prompt independently.

## Choosing a tool

Several established applications also handle Sanger data. The difference is the workflow and scope, not that namiyomi is the only free or cross-platform option. See the [detailed comparison](docs/COMPARISON.md) for ChromasPro, Geneious Prime, and related GitHub projects, including SeqTrace, sangeranalyseR, and Tracy. The comparison separates documented features from interface preferences; no speed or accuracy advantage is claimed.

| Application | License / cost model | Platforms listed by the developer | Main scope |
| --- | --- | --- | --- |
| **namiyomi** | Free; MIT source code | macOS; experimental Windows package | AB1 review, selected-read contigs, consensus editing, and FASTA export in one focused workspace. |
| [Chromas](https://technelysium.com.au/wp/chromas/) | Free application; optional PeakTrace service is separate | Windows | Single-trace viewing/editing, trimming, searching, and export. Its developer directs multi-read assembly users to ChromasPro. |
| [ChromasPro](https://technelysium.com.au/wp/chromaspro/) | Commercial; trial available | Windows | Read assembly and graphical consensus editing, plus broader sequence-analysis tools. |
| [UGENE](https://ugene.net/) | Free and open source | Windows, macOS, Linux | An integrated bioinformatics toolkit, including a Sanger editor and [CAP3 assembly integration](https://ugene.net/docs/extensions/cap3/). |

Choose namiyomi when the task is to review a small set of Sanger reads and produce a consensus without opening a larger analysis environment. Choose another tool when you need its broader analyses, supported formats, established workflows, or platform support. No performance or accuracy benchmark against these applications is claimed. Descriptions were checked against the developers' official documentation on 4 November 2026.

## Important limits and data handling

- **Assembly requires related, overlapping reads.** The greedy overlap assembler is for small Sanger contigs, not genome assembly or repeat resolution. Defaults are a minimum overlap of 100 comparable bases and 90% identity. A filename or overlap cannot establish specimen identity, locus identity, or absence of contamination.
- **Review flags identify positions to inspect.** Conflicting high-quality calls and internal alignment gaps can remain `N`. **Support Q** is the highest usable original Phred score supporting an automatic call, not a calibrated combined confidence probability. Manually edited source calls do not acquire instrument Phred scores.
- **Relative orientation is not biological annotation.** Orientation unification uses earlier sufficiently similar contigs as references. The first contig in each matching locus sets the relative direction; namiyomi does not determine a gene's biological forward strand.
- **A project is not a self-contained AB1 archive.** Project JSON stores source-file paths and checksums, not the chromatograms themselves. Keep the original AB1 files available. When moving a project, include the referenced AB1 files and preserve their relative folder structure. Changed or missing sources can prevent reopening.
- **Recovery is a safeguard, not a backup.** Unsaved edits receive local recovery snapshots approximately every 30 seconds. A crash can still lose the most recent edits and Undo history. Save regularly and keep independent backups.

## License, citation, and feedback

namiyomi is released under the [MIT License](LICENSE.txt). Bundled dependencies retain their own licenses and are accompanied by third-party notices.

Suggested citation:

> Yoneoka, Katsuhiro (2026). *namiyomi* (version 0.0.1) [Computer software]. https://github.com/yoneoka-katsuhiro/namiyomi

Machine-readable metadata is available in [CITATION.cff](CITATION.cff). Record the software version, processing settings, and manual review decisions when describing your workflow. No DOI has been assigned.

Report a problem through [GitHub Issues](https://github.com/yoneoka-katsuhiro/namiyomi/issues). Include the operating system, namiyomi version, steps to reproduce, and the error message or a screenshot. Do not upload confidential or unpublished sequence data unless you intend to make it public.

Developed by Katsuhiro Yoneoka, Mishima, Shizuoka, Japan.
