# namiyomi ver 0.0.1

namiyomi is a free desktop editor for routine Sanger sequencing work. It brings AB1 chromatograms, read trimming, selected-read assembly, consensus editing, and FASTA export into one workspace. All sequence processing takes place locally.

![namiyomi's consensus and aligned chromatograms on macOS. The data are synthetic.](https://raw.githubusercontent.com/yoneoka-katsuhiro/namiyomi/main/docs/images/namiyomi-contig-macos.png)

*The consensus stays above its supporting chromatograms so each call can be checked against the original traces. This example uses the synthetic demonstration data supplied with namiyomi.*

## In this release

- AB1 import with configurable automatic end trimming and manual cuts.
- Manual contig assembly from two or more selected reads, plus optional filename-prefix AUTO assembly.
- Aligned source traces, original Phred bars, consensus Support Q, and review flags.
- Base editing with linked consensus updates, Undo/Redo, and reverse complement.
- Both-strand sequence search, with exact or 80–100% identity matching.
- Project saving, local recovery snapshots, and FASTA export/copy.
- **Return Reads to AB1** to undo an assembly while retaining read edits and trimming.

![Orange highlights show a Find match in the synthetic consensus.](https://raw.githubusercontent.com/yoneoka-katsuhiro/namiyomi/main/docs/images/namiyomi-find-macos.png)

*Find checks the query and its reverse complement. The default is exact matching; the identity threshold can be changed in Settings.*

## Download

| File | Target |
| --- | --- |
| `namiyomi-0.0.1-macOS-arm64-development.zip` | Apple Silicon, macOS 15 or later. Not Intel or universal. |
| `namiyomi-0.0.1-Windows-x64.zip` | Windows 10/11 x64; experimental. |

Extract the ZIP before opening the app. A separate Python installation is not required. On Windows, open `namiyomi.exe` from the extracted folder and keep its `_internal` folder beside it.

**macOS signing:** this is an ad-hoc signed development package, not Developer ID signed or Apple notarized. macOS may warn about or block it. **Windows signing:** the executable is not Authenticode signed and may also trigger security warnings. Do not disable system security protections to install either package.

**Windows verification:** full hands-on testing on a physical Windows PC has not been completed, and errors may occur. The automated workflow uses Windows Server 2022 for build and startup checks; see [Actions](https://github.com/yoneoka-katsuhiro/namiyomi/actions) for the outcome of the relevant run. These checks do not establish that the full interactive workflow has been verified on Windows 10/11.

## Before using your own data

Version 0.0.1 is an early release. Keep independent backups and inspect exported sequences. Project JSON files reference the original AB1 files; keep them together when moving a project. Support Q is supporting original Phred evidence, not a calibrated combined probability. Assembly is intended for small sets of overlapping reads from the same specimen and locus.

Both application ZIPs include a `demo` folder. For a first look, open its `Demo.json` through **File > Import Previous Project**. Keep the four AB1 files beside the project. These files contain synthetic demonstrations, not biological observations or instrument measurements. Use **Save Project As** if you want to keep your practice session separately.

The application and source code are free under the MIT License. See the [English README](https://github.com/yoneoka-katsuhiro/namiyomi#readme) or [日本語の説明](https://github.com/yoneoka-katsuhiro/namiyomi/blob/main/README-ja.md) for instructions, limitations, and a comparison with other Sanger tools.

Developed by Katsuhiro Yoneoka, Mishima, Shizuoka, Japan. [Report a problem](https://github.com/yoneoka-katsuhiro/namiyomi/issues).
