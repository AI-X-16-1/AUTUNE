# 0011. How an uploaded material is cut into pieces for search

**Status:** Proposed
**Date:** 2026-10-15
**Deciders:** 강민구 (proposer); 김민경, 박재경, 문민재, 이승환 to agree or object on the PR.

This is a draft for discussion. Nothing in it is built. The numbers under
"Evidence" are from one local run on invented text (2026-10-15); the floors
and the product's own search are not yet measured ("Evaluation").

## Context

A team uploads a file to its 자료 screen (#817). `materials.store_upload`
reads the file's text, masks the whole text, and only then cuts it:

| Step | Today | Where |
| --- | --- | --- |
| Read | Pure-Python readers, plain text out: no converter, no OCR, no layout. A page that is only a picture refuses the file. | `material_formats.py`, `material_reading.py` |
| Cut | `cut(masked, 500)`: a piece ends after a line break, else after a space in its second half, else at 500. No overlap; joined, the pieces are the masked text again. | `materials.cut` |
| Embed | One vector per piece, 1024 wide (KURE-v1), computed in the upload request. | `materials.embedded`, `MATERIAL_EMBEDDING_DIM` |
| Store | One `ext_material_chunks` row per piece: `(material_id, position, text, embedding)`. | `models.ExtMaterialChunk` |
| Search | Cosine over the asking team's pieces, a similarity floor, a word match for short questions, one excerpt per material, at most 5 materials. | `material_search.py` |

Three limits of this are what the question "should we apply RAG-style
chunking?" is really about:

1. **A cut that does not know the text.** A fixed 500-character window splits a
   sentence, a table row or a list item wherever it falls. The cut honours line
   breaks and spaces and nothing else.
2. **One granularity.** The unit that is embedded, the unit that is stored and
   the unit an excerpt is taken from are the same piece. A 500-character piece
   that holds two topics gets one vector in between them.
3. **No structure survives the readers.** A heading of a `.docx`, a slide title
   or a PDF's layout is gone before the cut sees the text, so a "split on
   headings" rule has nothing to split on except `.md` and `.txt`.

Constraints that bind any change (they are not up for this ADR):

- **Masking comes first.** Text is cut after it is masked, and every piece
  passes `assert_masked` before it is stored (invariant 11,
  `docs/architecture/privacy.md`). A new cutter works on masked text only.
- **What leaves a search is short** (#817, 10(b)): at most `EXCERPT_CHARS`
  (200) of one stored piece, one excerpt per material, never a whole document.
  The excerpt cap is deliberately "under half of a stored piece".
- **No model but the embedder reads the text.** `find_materials` hands nothing
  to a language model, and uploaded-material text goes to no outside API.
  Whatever runs here runs on our own host.
- **Retention and delete are per material and cascade** from `ext_materials`.
  Any new table or column keyed to a piece goes with its material.
- **A second upload of the same text is one material** (`_kept_already`
  compares stored pieces "cut the same way"). A new cut means an old upload and
  a re-upload of the same file are no longer equal; old rows are not re-cut.

## Options

**A. Keep the 500-character cut.** No change. Whether it fails on real
questions has not been measured, and every number in `material_search` (the floor, the
relative cut, the word-match floor) was tuned against this piece size.

**B. A structure-aware recursive cutter, no new dependency.** Replace
`cut` with a recursive splitter that tries separators from the coarsest down:
a markdown heading line, a blank line, a line break, a sentence end (`.`, `?`,
`!`, `다.`, `요.`), a space, and only then a hard cut at the size. A heading
starts a new piece in `.md`; a table row or a list item is not cut
in the middle when it fits. Pure Python, same readers, same schema. Size and
the boundary rules are constants next to `CHUNK_CHARS`.

**C. Small embedded pieces, larger parent pieces (parent-child).** Embed
children of about 200 characters; the parent is the larger piece that holds
them (a 500-character piece, or a Markdown section). The search ranks children and answers with the parent's material
and position. Schema: because children do not overlap, a parent's text is its children
joined, so one nullable `parent` column on `ext_material_chunks` is enough (see
"Proposed decision"); no second copy of the text and no new table.

**D. A document converter in front of the cut (Docling or similar).** Convert
PDF, DOCX, PPTX and XLSX to structured Markdown first, then cut on its headings
and tables. This is the only option that recovers structure the current readers
throw away, and the only one that adds a dependency. What it costs has to be
measured in a spike before anyone decides, not assumed here:
install size and import time on the release host, whether its layout models are
fetched at first use (the release host's egress and "no outside API" must hold),
memory per file next to the 10 MB / 300,000-character limits, and whether a
scanned page is still refused rather than OCR'd by default.

A local run of D against today's readers was made on 2026-10-15; see "Evidence".

## Evidence

One local run on 2026-10-15, on invented text only: Docling 2.139.0 with
CPU-only PyTorch and OCR off, KURE-v1 on CPU, and the 200-file shelf and 290
questions of the floor measurement of 2026-10-13 (labels fixed by hash). It is
not in the repository; the numbers are the proposer's to be re-run by anyone
who wants them. It is one run on a small shelf and is read as such.

### The marking check, run first

36 fixtures: the repository's own, plus eleven `.docx`/`.pptx` made for the
run, each with a marking in one place. "Existing" is today's reader and
`file_marking`; the other two give the check the Docling output.

| the check is given | match | missed | stricter | refusal needed | not read by Docling |
| --- | --- | --- | --- | --- | --- |
| the head of Docling's Markdown | 15 | **6** | 1 | 5 | 9 |
| the same, plus Docling's page header/footer items as the foot | 20 | **1** | 1 | 5 | 9 |

- **Missed** is a marked file the existing check stops and the Docling input
  does not: a confidential upload that would pass. With the Markdown alone it
  is a PDF's footer and five `.docx` (header, footer, property). With the
  furniture items as the foot one is left: a marking in the document's own
  properties, which Docling's export does not carry.
- **Stricter** is `marked_below.pdf`: Docling joins lines into paragraphs, so
  "the first five lines" of its Markdown reach into the body.
- **The refusal does not carry over.** Five files today's reader refuses whole
  (a page that is only a picture, no text layer, too little text) come out of
  Docling as ordinary Markdown. Docling's per-page counts of text and pictures
  cannot rebuild the rule: a refused file and an accepted one both have a
  second page of 0 characters and 0 pictures. A PDF today's reader accepts
  comes out with no body text at all.
- `.xls`, `.ppt` and password-protected files are not read by Docling.
- On the 200-file shelf no file is marked, so 200 of 200 match; that run says
  nothing about misses.

### Text kept

152 of the 200 files are `.txt`, which Docling does not read; 48 are converted
(21 `.xlsx`, 20 `.pptx`, 4 `.docx`, 3 `.pdf`). Letters and digits kept, Docling
against today's reader: 1.00 for slides, documents and PDFs; tables 0.90 at the
lowest, median 0.96.

### Retrieval

Each question against every piece exactly (cosine), a material scoring as its
nearest piece, no floor, 220 questions that a file answers, top-5 meaning the
right material is among the first five of 200.

| variant | pieces | mean chars | top-1 | top-5 | MRR |
| --- | --- | --- | --- | --- | --- |
| today: reader text, `cut` at 500 | 229 | 152 | 191 | 214 | 0.913 |
| same text, recursive split at 200 | 305 | 114 | 195 | 217 | 0.928 |
| Docling Markdown, sections, recursive 200 | 412 | 99 | 197 | 219 | 0.937 |
| the same, heading path embedded with the child | 412 | 99 | 198 | 219 | 0.941 |
| Docling Markdown, recursive 500 | 333 | 122 | 192 | 217 | 0.920 |

What it says, and what it does not:

- **Most of the gain is the cut, not the converter.** Recursive splitting at
  200 on the text the readers already give takes top-5 from 214 to 217 of 220
  and top-1 from 191 to 195. Docling adds 2 more at top-5 and 2 at top-1; the
  heading path adds 1 at top-1. Two to five questions of 220 is not a
  difference to build on.
- **Where Docling shows an effect**, slides: 40 of 40 against 37 of 40 for
  both text variants. On tables it is one question worse (39 of 40 against
  40 of 40 for the recursive split on the reader's text).
- **The piece size alone at 500 with Docling** (333 pieces, 217 at top-5)
  is no better than the recursive split on reader text at 200.
- **Not measured:** the floors, and the 70 questions nothing answers, which is
  where a smaller piece moves them; whether an excerpt holds the answer; upload
  time; the product's own candidate query (`CANDIDATES`) rather than exact
  scores; a shelf of long files (about 150 of the 200 files fit one piece at
  500, so the piece size matters only on the rest).

## Proposed decision

**Stage 1, the one proposed for approval: a recursive splitter at 200
characters on the text today's readers give.** It replaces the body of
`materials.cut` and its constant. Nothing else in the upload path changes:

1. The readers, the marking check and the refusal of an unread file stay as
   they are, in the same order, before the cut.
2. The text is masked as a whole, then cut. The cutter tries separators from
   the coarsest down: a blank line, a line break, a sentence end (`. `, `? `,
   `! `), a space, and only then a hard cut at 200. A Markdown heading line is
   one more break where a text has them. Pieces do not overlap; joined, they
   are the masked text again, as `_kept_already` relies on.
3. Every piece still passes `assert_masked` before it is stored.
4. No schema change, no dependency, no new place that holds text.

It is reverted by deploying the old `cut`. Stored rows of the old cut stay as
they are until they expire; `_kept_already` will not call an old and a new
upload of one file the same.

**Not proposed now, and why:**

- **Docling, or any converter.** The marking check and the refusal are the
  privacy rules of #817 and a plain swap of the reader fails both (see
  Evidence). Using it only for the body would mean reading every file twice,
  a 1.1 GB install on a Windows machine (third-party figures for models are
  about 1.4 GB more), a conversion in the upload request, and a different text
  for the same file across formats. The measured gain over the splitter alone
  is two questions of 220. Reopen it for a measured failure that the readers
  cause by dropping structure, such as slides, not for a general gain.
- **A parent grouping, and a heading path in the vector.** No model reads a
  material's text (#817 10(b)), so a parent has no consumer. The heading path
  added one question at top-1 and needs a `section` column and Markdown
  headings, which only `.md` and Docling output have. Revisit with Docling or
  with a reader that keeps headings.
- **Overlap between pieces**, which breaks "joined, the pieces are the masked
  text" and puts one sentence in two excerpts.

### What stage 1 changes in the code that exists

- **The excerpt has little left to choose.** A piece of at most 200 characters
  is within `EXCERPT_CHARS`, so `excerpt_of` returns the whole piece and its
  centring on the question's words does nothing. The docstrings that call the
  cap "under half of a stored piece" and the tests that assert it change.
- **Rows and vectors.** On the 200-file shelf 229 pieces become 305; at the
  300,000-character limit one file is about 600 pieces at 500 and about 1,500
  at 200, all embedded in the upload request (no text goes to a task).
- **The floors move.** The similarity floor, the relative cut and the
  word-match floor were measured at 500 characters. They are measured again at
  200 and set from that, not carried over.
- **Masking and the guard are unchanged**, and no new reader of the text is
  added: the same module, the same table, the same delete cascade.

## Open decisions for the owners

1. **Piece size.** 200 is the proposal; 500 stays the alternative. The numbers
   above are top-5 over exact scores, not the product's search with its
   floors.
2. **Upload time.** More pieces is a longer request. Is a limit on pieces per
   upload needed, or a different place to embed?
3. **Old uploads.** Leave old rows with their old cut (they expire with the
   team's window, or stay for teams that keep until deleted), or re-cut on a
   migration? Re-cutting means reading stored masked text back and embedding
   it again.
4. **The floors** are re-measured with the change (below), and the new values
   are part of the PR that changes the cut.

## Evaluation

Done: the run under Evidence.

To do before stage 1 is merged:

- **The floors at 200**, with the 70 questions nothing answers, on shelves of
  30 to 200 files, as the floors were first measured.
- **Through the product**: `store_upload` and `search_materials` on PostgreSQL
  with pgvector, not exact scores, so `CANDIDATES` and the word match are in.
- **A shelf with more long files**, so that the piece size is tested where it
  can matter.
- **Whether the excerpt holds the answer**, with answer words in the labels.
- **A boundary test**: a sentence, a table row and a heading that straddle the
  old 500 boundary stay whole.
- **Upload time** of a 300,000-character file on the release host's CPU.

## Consequences

- Stage 1 moves piece boundaries without touching a privacy rule: the cut runs
  after the readers, the marking check and the masking, and every piece still
  passes `assert_masked`.
- Stored rows of two generations live side by side until the old ones expire.
- Choosing not to adopt Docling leaves "readers are pure Python, no converter,
  no OCR" (`material_reading.py`, `pyproject.toml`) true. If it is reopened,
  those two statements change with it and the owners agree first.
