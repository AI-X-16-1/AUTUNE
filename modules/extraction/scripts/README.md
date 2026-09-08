# Corpus analysis scripts

One-off analysis tools, not part of `autune_extraction`. They read an annotated
meeting corpus and report what it can contribute to the five-way utterance
classifier (#10) and to decision entities (#53).

The corpus itself is not in this repository — `dataset/` is gitignored, corpora
are large and carry their own licence terms. Download one and pass its path.

## AMI Meeting Corpus

`ami_public_manual_1.6.2`, the manual annotations only (~207MB). Licensed
CC BY 4.0, so it may be used here **with attribution**.

```bash
uv run python modules/extraction/scripts/ami_analyze.py <corpus-root>
uv run python modules/extraction/scripts/ami_layers.py  <corpus-root>
```

`ami_analyze.py` reports the dialogue-act distribution and resolves the
extractive `decision` layer to text. `ami_layers.py` reports the abstractive
summary sections and the adjacency-pair polarity types.

Both print JSON on stdout.

## AI Hub Korean meeting corpus

`002. 주요 영역별 회의 음성인식 데이터`. Transcription only — `annotation_level` is
`원시`, so there are no class labels in it. What it provides is Korean meeting
utterances, which AMI cannot.

```bash
uv run python modules/extraction/scripts/ko_reference_overlap.py <corpus-root>
```

Measures how often an unresolved reference shares an utterance with a class cue,
for the pipeline-order question in `docs/modules/extraction.md`. Read the output
as population size, not accuracy: both pattern sets are hand-written
approximations, and deciding which order classifies better needs a labelled set
and two trained classifiers.

Point it at a directory of label files; it reads `form`, the masked field, and
never `original_form`.

## Why two passes

AMI splits information across layers that have to be read together:

| Layer | Holds |
| --- | --- |
| `dialogueActs` | 15 act types, no polarity |
| `dialogueActs/*.adjacency-pairs` | Polarity: POS, NEG, UNC, PART, ELA |
| `abstractive` | Per meeting: abstract, decisions, problems, actions |
| `decision` | Extractive word spans for each decision |

The act inventory alone cannot separate a concern from an endorsement — that
distinction lives in the adjacency pairs. A decision's settled statement lives in
`abstractive`, while the utterances it came from live in `decision`. That pairing
is the same shape as `Decision(statement, source_utterance_ids)` in
`packages/contracts`.

## Reading the pointers

Annotations reference text as NITE pointers rather than carrying it:

```xml
<nite:child href="ES2002a.B.words.xml#id(ES2002a.B.words624)..id(ES2002a.B.words700)"/>
```

Both scripts resolve those ranges against the `words/` files, which is what makes
the output readable. Word files are cached per run; a full pass over the corpus
takes a few minutes.

Findings from these scripts are recorded in issue #20.
