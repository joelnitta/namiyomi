"""stdio MCP server exposing namiyomi's batch tool and review helpers to Claude.

Local only: no network access, sequence data never leaves the machine except as the tool
results Claude reads in the chat. Run with ``python src/mcp_server.py`` (see docs/BATCH.md).
"""
from pathlib import Path

from mcp.server.fastmcp import FastMCP, Image

import batch
import review

mcp = FastMCP('namiyomi')

HUMAN_NEXT = ('Review mode is "human": do NOT edit anything. Report the triage to the user and give them '
              'the project path to open in namiyomi (File > Import Previous Project), listing the needs_review, '
              'single_read and failed contigs for them to check against the traces.')
CLAUDE_NEXT = ('Review mode is "claude": for each needs_review contig call get_contig_details, then render_trace '
               'for every flagged column and judge the peaks. Apply only confident changes with '
               'apply_review_edits (writes a NEW reviewed project plus review_report.md); leave anything '
               'uncertain unedited and list it for the user. Your reading of traces is a second opinion; say so.')


def _path(value):
    return Path(value).expanduser()


@mcp.tool()
def scan_folder(input_dir: str, sheet: str | None = None) -> dict:
    """Dry run: count the AB1 files in a folder and show the contig groups that would be built.

    Without ``sheet``, files are grouped by the text before the first underscore (like the app's
    Build Contig AUTO). With ``sheet`` (CSV with columns file,sample,marker) groups come from the sheet;
    files not in the sheet and sheet rows with no file are reported. Nothing is written.
    """
    return batch.scan_folder(_path(input_dir), _path(sheet) if sheet else None)


@mcp.tool()
def run_batch(input_dir: str, output_dir: str, sheet: str | None = None, allow_single: bool = False,
              review: str = 'human', min_quality: int | None = None, min_overlap: int | None = None,
              min_identity: float | None = None) -> dict:
    """Import, trim, group, assemble and triage every AB1 in input_dir; write results to output_dir.

    output_dir must be a different folder (originals are never modified). Writes project.json (open it
    in the app), summary.json/tsv and one FASTA per marker. Single-read contigs are exported only with
    allow_single. ``review`` is "human" (default: stop after triage, the user checks flags in the app) or
    "claude" (Claude reviews flags with render_trace and apply_review_edits). Follow ``next_steps``.
    """
    if review not in ('human', 'claude'):
        raise ValueError('review must be "human" or "claude".')
    settings = {k: v for k, v in (('min_quality', min_quality), ('min_overlap', min_overlap),
                                  ('min_identity', min_identity)) if v is not None}
    summary = batch.run_batch(_path(input_dir), _path(output_dir), sheet=_path(sheet) if sheet else None,
                              settings=settings, allow_single=allow_single)
    summary['review_mode'] = review
    summary['next_steps'] = HUMAN_NEXT if review == 'human' else CLAUDE_NEXT
    return summary


@mcp.tool()
def list_contigs(project_path: str, filter: str | None = None) -> list[dict]:
    """List contigs in a saved project.json, optionally filtered: clean, needs_review or single_read."""
    return review.list_contigs(review.load_project(_path(project_path)), filter)


@mcp.tool()
def get_contig_details(project_path: str, contig: str) -> dict:
    """Flagged columns of one contig (0-based alignment columns), with each source read's base call and Phred."""
    return review.contig_details(review.load_project(_path(project_path)), contig)


@mcp.tool()
def find_sequence(project_path: str, contig: str, query: str, identity: int = 100) -> list[dict]:
    """Search a contig's consensus (both strands) for a DNA/IUPAC query; identity is 80-100 (%)."""
    return review.find_sequence(review.load_project(_path(project_path)), contig, query, identity)


@mcp.tool()
def render_trace(project_path: str, contig: str, column: int, window: int = 15) -> Image:
    """Image of every source read's chromatogram around one alignment column (highlighted).

    Base calls and Phred scores are shown above the peaks; reverse reads are shown complemented so
    all reads line up. Use it to judge a flagged column. ``window`` = columns shown on each side.
    """
    return Image(data=review.render_trace(review.load_project(_path(project_path)), contig, column, window),
                 format='png')


@mcp.tool()
def apply_review_edits(project_path: str, output_path: str, edits: list[dict]) -> dict:
    """Apply review verdicts to a NEW copy of the project (the input is never overwritten).

    Each edit: {"contig": name, "column": int, "action": "set" or "confirm", "value": "A" (for set),
    "reason": "why, citing the peaks"}. "set" changes the consensus call; "confirm" marks the flag
    reviewed without changing it. Every edit is logged with its reason in review_report.md next to
    output_path. Do not edit columns you are unsure about; leave them for the user.
    """
    return review.apply_review_edits(_path(project_path), _path(output_path), edits)


@mcp.tool()
def export_fasta(project_path: str, output_dir: str, allow_single: bool = False) -> dict:
    """Write one FASTA per marker from a saved project (use the reviewed project after review)."""
    return review.export_fasta(_path(project_path), _path(output_dir), allow_single)


if __name__ == '__main__':
    mcp.run()
