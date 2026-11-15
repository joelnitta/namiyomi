# namiyomi ver 0.2.1

Released 2026-11-15.

namiyomi 0.2.1 adds an optional MCP review server for reviewing Sanger chromatograms and comparing end-trimming rules in a conversation with an external client. The desktop app's **AI Review** remains available from its existing menu.

## Download

- [Download for macOS — Apple Silicon, macOS 15+](https://github.com/yoneoka-katsuhiro/namiyomi/releases/download/v0.2.1/namiyomi-0.2.1-macOS-arm64-development.zip)
- [Download for Windows — Windows 10/11 x64, experimental](https://github.com/yoneoka-katsuhiro/namiyomi/releases/download/v0.2.1/namiyomi-0.2.1-Windows-x64.zip)
- [Source code for batch processing and the optional MCP review server](https://github.com/yoneoka-katsuhiro/namiyomi/archive/refs/tags/v0.2.1.zip)

The desktop ZIPs include Python and can be used without installing it separately. The optional MCP review server runs from the source code and requires its own Python environment and client configuration.

## Changes since 0.2.0

- **Optional MCP review server.** Joel Nitta's [PR #3](https://github.com/yoneoka-katsuhiro/namiyomi/pull/3) adds tools to process AB1 folders, inspect flagged columns and chromatogram images, search sequences, compare end-trimming rules, save review decisions, and export FASTA. It uses the same local trace rules by default and stores decisions in the same review fields displayed by the app. Thanks to Joel for this contribution and the earlier batch-processing work.
- **Clearer names for the two MCP services.** The existing `src/mcp_server.py` is described as the **read-only quality summary via MCP**. It returns anonymous counts through `get_quality_summary`. The new `src/mcp_review_server.py` is the **MCP review server**, with trace-inspection, editing and export tools. Both implement MCP; their capabilities differ.
- **Protected review outputs.** Reviewed projects require a new filename. FASTA exports always use `<marker>.reviewed.fasta`, including when a project has been renamed, and refuse existing destinations. Another review can use the latest reviewed project as input and export into a new folder.
- **Consistent MCP column numbers.** All MCP column arguments and returned flag positions start at 1. The command-line summary files retain their existing zero-based flag coordinates; `review.tsv` uses one-based positions.
- **Manual edits protected during trimming.** A proposed cut that includes a nonempty manual replacement or manually inserted bases is refused, so those edits can be inspected in the app first.
- **Updated instructions.** The English and Japanese READMEs explain the difference between in-app AI Review and client-driven MCP review, with source installation steps, batch examples and refreshed AI Review screenshots.

## Using the MCP review server

Extract the source code ZIP, open its folder in a terminal, and use Python 3.10 or later:

```sh
python -m pip install -r requirements/mcp.txt
```

Register `/path/to/python /path/to/namiyomi/src/mcp_review_server.py` with a trusted MCP client. See the [batch guide](https://github.com/yoneoka-katsuhiro/namiyomi/blob/v0.2.1/docs/BATCH.md#review-and-trim-from-an-mcp-client) for client configuration and the tool list. Open the resulting project in namiyomi with **File > Import Previous Project**.

In-app **AI Review** sends numeric evidence to an optional online model and checks the answer's confidence and support in the signal before applying it. The MCP client can request sequences, read names and trace images, then submit its own decisions. Those submitted edits do not go through AI Review's model-answer confidence and signal checks. The server itself makes no network calls; the connected client controls how the data it receives is used.

## 日本語での変更点

- NittaさんのPR #3を取り込み、外部MCPクライアントとの会話で波形の確認、末端トリムの比較、レビュー結果の保存・FASTA出力を行う機能を追加しました。
- 既存のMCP機能は「読み取り専用の品質サマリー（MCP接続）」、新機能は「MCPレビューサーバー」として役割を明確にしました。アプリ内の **AI Review** は従来どおり使えます。
- 既存の出力ファイルの上書き、MCPツール間の列番号のずれ、末端トリムによる手編集済み塩基の消失を防ぐ修正を加えました。
- MCPレビューサーバーはソースコードから別途導入するPythonツールです。デスクトップアプリの新しいメニューではありません。

## Package status and verification

| Package | Target and status |
| --- | --- |
| `namiyomi-0.2.1-macOS-arm64-development.zip` | Apple Silicon, macOS 15 or later; ad-hoc signed development package. Not an Intel or universal build. No Developer ID signature or Apple notarization. |
| `namiyomi-0.2.1-Windows-x64.zip` | Windows 10/11 x64; experimental and not Authenticode signed. |

Extract the complete ZIP before opening the app. On Windows, keep `_internal` beside `namiyomi.exe`. Operating-system security warnings may appear; do not disable system protections to install the packages.

The repository's [Actions workflow](https://github.com/yoneoka-katsuhiro/namiyomi/actions) runs automated tests and Windows package/startup checks. Consult the run for this release for its results. These checks do not establish full hands-on validation on Windows 10/11; that remains incomplete.

Keep the source AB1 files with their project, retain independent backups, and inspect exported sequences. The included `demo` uses synthetic sequences and signals. Projects with review decisions require namiyomi 0.2.0 or later; they cannot be opened in 0.1.0.

[Full changes from v0.2.0](https://github.com/yoneoka-katsuhiro/namiyomi/compare/v0.2.0...v0.2.1) · [English README](https://github.com/yoneoka-katsuhiro/namiyomi#readme) · [日本語の説明](https://github.com/yoneoka-katsuhiro/namiyomi/blob/main/README-ja.md)
