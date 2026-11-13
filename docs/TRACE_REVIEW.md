# How Review Start decides, and how that was measured

**AI Review > Review Start** settles flagged consensus columns in two steps.
Local rules read the chromatograms on your device first. An online model, if
you configured one, then looks at the columns the rules left open. This page
describes both steps and the measurements behind them.

Review considers open `N` calls (low quality, conflicting reads, or alignment gaps) and calls that one read won on quality alone. It keeps explicit manual edits and acknowledgements, as well as IUPAC codes recorded for double peaks.

## Step 1: local trace rules

The rules are in `src/trace_evidence.py`. Each one gives a base, removes the
column, or leaves it open.

| Rule | When it decides | What it does |
| --- | --- | --- |
| Clean read | One read is in a clean stretch here: the three calls on each side, and the call in the column, are all at Phred 40 or above with evenly spaced peaks. The other reads are not. If no read reaches 40, the same test is tried at 30 and then at 25. | Takes that read's version. If the clean read has a base, the base is set. If the clean read has no base in this column, the column is removed. |
| Resolved run | Two clean reads count a run of one base differently. One shows a separate summit for every base of the run and in the other the peaks have merged. | Takes the count from the read that resolves the run. |
| Clear peak | All reads call the same base at low Phred, and at least one has an isolated, centred peak with no summit of another channel under it. Limits are stricter where the neighbouring calls are also low quality. | Confirms the base. |
| Clear peak over unclear | The reads disagree, one has a clear peak, and the other has no distinct peak of its own. | Takes the base with the clear peak. |

The rules never use file names, never guess from sequence patterns or reading
frames, and never overrule a base you edited. A column where two clean reads
differ for any other reason stays open.

A single read is never enough to remove a call. See "What the measurements
showed" below.

## Step 2: the online model (optional)

For each column still open, the model receives the aligned calls of every read
that covers it and the four-channel signal sampled along the scan axis, in
consensus orientation. No file name, sample name, path or sequence position is
sent. The model answers with a base, `-` (no base in this column) or `N`, and
says whether it is confident.

An answer is applied only if all of these hold:

1. The model is confident.
2. The signal supports it. A base needs a real summit in its channel under a
   call in that column. Removal needs a read that already has a gap there.
3. In a column covered by one read, the answer confirms that read's own call,
   and either the column lies in the first 300 bases of the read or the peak is
   isolated from its neighbours.
4. Where one read has a base and another has a gap, the reasoning effort is
   High or above.

A site the model reviews and leaves unresolved remains flagged in red. If it suggested a call, the status bar shows the suggestion. Unresolved sites that have only been checked locally keep their black flags.

## How the rules were measured

The retrospective test compares a decision with a high-quality call from another read of the same specimen. This is a reference for comparison. Both reads share a specimen, and their alignment is computed together.

1. Group AB1 files as Build Contig (AUTO) does and assemble each group.
2. Hide one read from consensus calling. Keep the existing alignment and re-call the consensus from the reads that remain.
3. Judge every flagged column of that reduced contig.
4. Wherever the hidden read is in a clean stretch (Phred 40 or above for three
   calls on each side, even spacing), use its call as the comparison reference. In repeats, equivalent short sequences can agree despite a shifted gap.

`scripts/benchmark_trace_review.py` runs this on any folder:

```sh
python scripts/benchmark_trace_review.py --input /path/to/ab1-folder --jobs 8
```

The numbers below come from 3,795 rbcL reads of ferns, sequenced from 2019 to
2026 on Applied Biosystems 3100 and 3730 instruments with BigDye v3 and the KB
base caller. They form 1,214 groups of two or more reads. The rules were developed and evaluated on this cohort. The figures describe one locus and one laboratory, with repeated comparisons within groups. They are not an independent estimate for other loci, instruments, or laboratories.

### Calls the consensus already accepts

Calls in columns covered by a single read, counted as wrong when the held-out
clean read disagrees:

| Phred of the call | Calls | Wrong | Of the wrong calls, spurious extra bases |
| --- | --- | --- | --- |
| below 10 | 309 | 6.8% | 12 of 21 |
| 10 to 19 | 5,398 | 1.3% | 44 of 70 |
| 20 to 29 | 20,167 | 0.27% | 41 of 54 |
| 30 to 39 | 40,573 | 0.09% | 27 of 35 |
| 40 and above | 279,657 | 0.012% | 29 of 33 |

Phred 20 to 29 is the lowest band the default settings accept without a flag,
so 0.27% is the yardstick: a rule that decides flagged columns should not be
worse.

### Local rules on flagged columns

5,872 flagged columns had a held-out answer.

| Rule | Decided | Wrong |
| --- | --- | --- |
| Clean read | 115 | 1 (0.87%) |
| Resolved run | 5 | 0 |
| Clear peak, including clear over unclear | 2,726 | 3 (0.11%) |
| All rules | 2,846 (48%) | 4 (0.14%) |

The 115 clean-read decisions are few because three reads must overlap for the
test to apply. In ordinary two-read contigs this rule does most of the work,
because the degraded tail of one read usually lies under the clean middle of
the other.

### What the measurements showed

- **Most wrong low-quality calls are extra bases, not wrong bases.** Below
  Phred 20, 56 of 91 errors are a call that should not exist. A
  review that can only answer A, C, G, T or N cannot fix those. It either
  leaves an `N` in place of nothing or confirms the extra base, and either one
  shifts the reading frame.
- **Two calls on one peak are usually both real.** A rule that removed the
  weaker of two same-base calls sharing a peak was wrong in 42 of 45 cases, so
  it is not in the program. The base caller counts merged peaks better than
  peak spacing suggests.
- **One trace cannot show that its own call is spurious.** Late in a read,
  about 3% of the low-quality calls the rules leave open are wrong, mostly
  extra calls on merged peaks. They look like their correct neighbours. This is
  why removal needs a second read and why a single-read answer from the model
  is limited as described above.
- **Reads with the same prefix must be the same specimen.** Merging groups
  whose names differ only by a set suffix produced hundreds of columns where
  two clean reads disagree. The rules leave such columns open, which is also
  how a mixed-up group shows itself.

### The online model on the same test

277 columns with held-out answers were sampled by category and sent to
`gpt-6-luna` at high reasoning effort. Columns where the read's call is wrong
were deliberately over-sampled, so the counts describe this sample, not
ordinary data.

| Review | Answers applied | Wrong |
| --- | --- | --- |
| 0.1.0 AI Review preview: peak measurements as text, replies limited to A, C, G, T or N, every reply other than N applied | 173 | 32 |
| 0.2.0 model step: sampled signal, removal allowed, acceptance checks above | 108 | 0 |

Where the earlier protocol went wrong:

- At the 53 length disagreements it answered 26 times and 12 answers were
  wrong. It had no way to say that a column holds no base.
- In the 40 single-read columns where the read's call is wrong, it answered 20
  times and confirmed the wrong call every time.

The acceptance checks matter as much as the prompt. In those same 40 columns
the 0.2.0 prompt still produced 14 confident wrong answers. None was applied,
because none met the single-read condition. At the 53 length disagreements the
0.2.0 step applied 25 answers and all 25 were right.

Reasoning effort matters at length disagreements. At medium effort the same
model applied 26 answers there and 3 were wrong, which is why condition 4
exists. With that condition the medium run applied 71 answers in the whole
sample, none wrong. The sample describes the tested model and settings.

In the program the model only sees what the local rules leave open. On a real
project of 44 reads (18 contigs, 49 flagged columns), the local rules settled
36 columns in under two seconds, `gpt-6-luna` settled 7 of the other 13, and 6
stayed flagged. The whole review took about 40 seconds.

## Limits

- The rates above are for one locus and one laboratory's reads.
- A column covered by one low-quality read, late in the read, with merged
  peaks usually stays open. Trim the end or sequence the other strand.
- Heterozygous or mixed templates are outside these rules. With **Call clear
  double peaks as IUPAC codes** enabled, such columns keep their IUPAC code and
  their flag.
- A contig with no flags is not a certified sequence. Calls at Phred 20 and
  above are accepted as before, and a few of them are wrong.
