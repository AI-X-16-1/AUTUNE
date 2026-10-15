# 0011. How an uploaded material is cut into pieces for search

**Status:** Proposed
**Date:** 2026-10-15
**Deciders:** 강민구 (proposer); 김민경, 박재경, 문민재, 이승환 to agree or object on the PR.

This is a draft for discussion. Nothing in it is built. The numbers under
"Evidence" are from local runs on invented text (2026-10-15 and 2026-10-16),
on exact scores; the product's own search through pgvector and real reports
are not yet measured ("Evaluation").

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

**E. The structure the readers can already see, in the embedded text.** Keep
today's readers and B's cutter. Find a piece's heading path in the reader's
text (a `#` line of `.md`, a heading style of `.docx`, a short line that
starts `2.`, `2.1` or `2.1.1`) and embed the path with the piece, while the
stored piece stays as it is. No dependency, no column, no converter.

A local run of D against today's readers was made on 2026-10-15, and of B, D
and E on invented reports on 2026-10-16; see "Evidence".

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
- Of the 8 marked fixtures Docling reads, the Markdown alone catches 2 (25%) and
  with the furniture items 7 (88%); the ninth marked fixture is an `.xls`,
  which Docling does not read. Eight files is too few to give a rate, and the
  places not tried (a `.docx` watermark, a `.pptx` slide footer, an `.xlsx`
  print header, a PDF's properties, masters and layouts) are the ones a
  Markdown export is least likely to carry.

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

### The floors at 200 characters

`material_search.search_materials` replayed on exact cosine scores, with its own
constants (`CANDIDATES`, the floor, the cut below the best, the title and word
match), over the 200-file shelf and its 290 questions: 220 with an answer and
70 that nothing answers. At 500 characters the replay gives the 205 of 220 and
0.57 wrong per question that the original measurement gave. Floor 0.5 and cut
0.05, the settings now:

| Pieces | Answer among the hits | Asked what nothing answers, and given hits | Wrong per question |
| --- | --- | --- | --- |
| today, 500 characters | 93.2% (205) | 54.3% (38) | 0.57 |
| reader text, recursive 200 | 95.0% (209) | 58.6% (41) | 0.50 |
| Docling body (hybrid), recursive 200 | 96.8% (213) | 65.7% (46) | 0.51 |
| as above, heading path in the vector | 96.4% (212) | 60.0% (42) | 0.53 |

Shorter pieces score higher, so the same floor lets more through. Raising the
floor until the questions nothing answers get hits as often as today (about
54%):

| Pieces | Floor | Answer among the hits | Given hits, nothing answers | Wrong per question |
| --- | --- | --- | --- | --- |
| today, 500 characters | 0.50 | 93.2% (205) | 54.3% (38) | 0.57 |
| reader text, recursive 200 | 0.51 | 94.5% (208) | 52.9% (37) | 0.47 |
| Docling body, recursive 200 | 0.52 | 95.5% (210) | 52.9% (37) | 0.41 |

- **The cut below the best stays at 0.05.** At 0.03 four answers are lost; at
  0.07 the wrong hits per question rise to about 0.8.
- **The heading path in the vector bought nothing on this shelf** (212 against
  213 at floor 0.5, 210 against 210 at the matched floors): its documents do
  not repeat a shape. On reports that do, it is the largest gain ("Reports
  whose sections repeat"). The path is embedded, not stored, so no parent or
  section column is needed either way.
- **The word match and the title match never fired**, at any floor, in any
  variant: every hit came from the vector. Their lower bound is not measured
  by this shelf, and the questions, written as sentences, rarely have all
  their words in one piece.
- **Shelf size.** At 30 files the three 200-character variants find 88 of 90
  against 87; the gain grows with the shelf (209 to 213 against 205 of 220 at
  151 and 200 files).
- **Limits.** One person wrote the files and the questions, the text is
  invented, the scores are exact (not pgvector's index and its 50
  candidates), and the three long files are too few to show where 200
  characters should matter most. A difference of three to five questions of
  220 is a direction, not a ranking.

### Does the excerpt hold the answer

The search says which material; the person reads one excerpt of at most 200
characters of its nearest piece. 38 questions have a key, a string only the
answering sentence holds: the 18 on the three long files (keys of the earlier
measurement) and the 20 on the five documents of two or more sections (keys
chosen from the files). Searched with no floor; the nearest piece of the
material cut to the excerpt with `excerpt_of`; text and key compared with
whitespace and Markdown table bars removed. At 500 characters the long files
give 9 of 18 pieces and 8 of 18 excerpts holding the answer, as the product's
own run did.

| Pieces | Material among the hits | The excerpt shown holds the answer | Any of the 2 nearest pieces' excerpts | Any of the 3 nearest |
| --- | --- | --- | --- | --- |
| today, 500 characters | 94.7% (36/38) | 63.2% (24) | 81.6% (31) | 86.8% (33) |
| reader text, recursive 200 | 100% (38) | 78.9% (30) | 94.7% (36) | 100% (38) |
| Docling body, recursive 200 | 100% (38) | 89.5% (34) | 97.4% (37) | 97.4% (37) |

On the 18 long-file questions alone the excerpt shown holds the answer 44%
(8), 67% (12) and 83% (15) of the time.

- **What is left is not a reading problem.** The four misses of the Docling
  variant are all reworded questions where the material was found and its
  nearest piece was another piece. A model given that piece would not have the
  answer; one given the whole file would, and that is what #817 10(b) rules
  out.
- **Showing more than one piece of the same material closes most of it
  without a model**: 2 or 3 pieces of 200 characters are 400 to 600
  characters, still never the document.
- **Limits.** 38 questions, 8 files, text written by the proposer; the 20
  two-section keys were chosen after reading the files. The Docling variant
  makes 410 pieces against 305, because a Markdown table is padded to column
  width; why it does better on the long tables was not examined.

### What Docling's Markdown changed on slides

On the 40 slide questions at floor 0.5 the answer is among the hits for 35
(reader, 500 or 200 characters) and 40 (Docling Markdown). The words in the
twenty decks are the same in both, to the word: the difference is layout
marks (`#` before a title, `-` before a bullet, blank lines). A deck is
shorter than 200 characters in both, so the cut is not what changed; the
embedding of the marked text is. The cause was not examined. If it is the
marks, the `.pptx` reader could write them without a converter; this has not
been tried. The slides are invented, a title and a few lines each, and OCR
being off, a picture or a chart is read by neither.

### Reports whose sections repeat

Ten invented reports (six `.docx` with heading styles, four `.pdf` with
larger bold lines), about 4,600 characters and three heading levels each, in
the 200-file shelf. Each has eight subjects (branch, system, supplier...)
treated in the same shape. The subject's name is in the heading; the numbers
under it do not repeat it, as a report often reads ("이탈률은 3.2%로 집계됐다"
under "2.2 해운대 지사"). Seven questions each, 70 in all, with a key: three
metrics asked with the subject's name, a plan date, a date in the sentence
that names the subject, a metric asked in other words, a team from the plan
table. Searched with no floor; the excerpt of the nearest piece of the report
is checked for the key.

| Pieces | Report among the hits | The excerpt holds the answer | Any of the 2 nearest | Any of the 3 nearest |
| --- | --- | --- | --- | --- |
| A. today, 500 characters | 100% | 23% (16/70) | 61% | 63% |
| B. recursive 200, reader text | 100% | 43% (30) | 56% | 60% |
| D. split on numbered headings, piece only | 100% | 27% (19) | 47% | 53% |
| **E. as D, heading path put in the embedded text** | 100% | **71%** (50) | 94% | **100%** |
| C. `.docx` heading styles, with path | 100% | 73% (51) | 81% | 84% |
| F. Docling Markdown, 200, piece only | 100% | 30% (21) | 43% | 50% |
| G. Docling, the path Docling gave | 100% | 53% (37) | 74% | 79% |
| **H. Docling, level taken from the heading's number** | 100% | **81%** (57) | 97% | **100%** |

By kind of question (A, E, H): a metric under a subject's heading 3%, 83%,
97%; a reworded question 20%, 70%, 90%; a plan date 60%, 90%, 100%; a date in
a sentence that names the subject 50%, 60%, 80%; the team from a table row
20%, 30%, 10%. By format, E gives 74% on `.docx` and 68% on `.pdf`; C gives
86% and 54% (a PDF has no styles to read); H gives 81% and 82%.

- **Splitting on headings does nothing by itself** (D, 27%, against 23% to
  43% for A and B). What helps is the heading path in the text that is
  embedded, because the subject's name is only there (E, C, G, H).
- **The reader's text is enough for most of it.** E needs no converter: a
  short line that starts `2.`, `2.1` or `2.1.1` is a heading. Docling's
  addition over E is 10 points on the excerpt shown and 3 points on two
  excerpts (94% to 97%).
- **Docling's own levels lose the subject in a PDF** (G, 53%): every heading
  of a PDF comes out as one level, so the path of "2.2.1 지표" is "지표".
  Taking the level from the number (H) repairs it, and is the same idea as E.
- **The table row stays hard** (10% to 40% in every variant): rows are
  alike, and the nearest row is often another. Showing two or three pieces
  answers it (90% to 100% at two or three nearest).
- **Limits.** A stress test by construction: the subject's name sits only in
  the heading, and the eight sections are of one shape, which is where a path
  helps most. A real report will gain less. Headings are numbered in all ten;
  an unnumbered heading in a PDF is found by nothing here. One generator wrote
  the ten, one person wrote the questions, and 70 questions are not
  independent. On the 200-file shelf, where documents do not repeat, the path
  did not help (212 against 213 at floor 0.5).

### Where a security label sits

Nine invented files (`.pdf` and `.docx`) with the label "대외비" in one place
each, run through today's marking check and through Docling's output:

| Place of the label | Today's check | Docling Markdown alone | Markdown + page header and footer items |
| --- | --- | --- | --- |
| cover page, top right (a body paragraph) | stops | stops | stops |
| every page, bottom (pdf) | stops | **misses** | stops |
| every page, top (pdf) | stops | **misses** | stops |
| cover and every page bottom (pdf) | stops | stops (the cover) | stops |
| cover, top right (docx body) | stops | stops | stops |
| header (docx) | stops | **misses** | stops |
| footer (docx) | stops | **misses** | stops |
| two unmarked files | passes | passes | passes |

Docling files a repeated header or footer under its page furniture and leaves
it out of the Markdown; the label on a cover is body text and is kept. Not
tried: a text box, a watermark, a label inside a picture, a document property
on these two formats, a label on a middle page only.

### Memory and disk

A conversion of a byte stream (`DocumentStream` over `BytesIO`, never a path),
four invented files, on the proposer's Windows machine:

- A Python audit hook saw 0 writes and no new file in the temp directory; the
  hook was checked against a write it must see. A C extension that writes a
  file itself is not seen by it, so this is not proof for the server's
  container.
- The process's peak working set was 330 MB after import and about 1.05 GB
  after the first PDF conversion (the models load then).
- A small PDF took 1.6 to 7 s on CPU and a `.docx` or `.pptx` 0.1 s; the first
  conversion of a process, with its model load, 36 s. A 10 MB file was not
  tried.

### The hybrid, over the same files

This is what option D would look like behind today's check, measured in case
it is taken later. Today's reader decides refusal and marking, unchanged;
Docling gives the body only when it read the file and kept at least 95% of the
letters and digits the reader read.

| | 36 fixtures | 200-file shelf |
| --- | --- | --- |
| refused, as today | 7 | 0 |
| stopped as marked, as today | 9 | 0 |
| body from Docling Markdown | 8 | 41 |
| body from the reader (Docling does not read the file) | 6 | 152 |
| body from the reader (Docling kept too little) | 6 | 7 |

Why a file falls back: a PDF whose text Docling files as a page header (0.00),
cell notes and speaker notes it drops (0.17 to 0.81), and a worksheet's name,
which the reader keeps as its first line and Docling's table does not (about
0.90 to 0.95 on the shelf's `.xlsx`). The 95% line is a proposal read off these
48 files, not a tuned value. The marking check and the refusal give the same
verdict as today by construction, since the same code decides them.

## Proposed decision

**Cut the masked text into pieces of 200 characters with a recursive
splitter (B), and embed each piece with the heading path the reader's text
already shows (E). Keep today's readers and today's marking check as they
are, and run them first. Do not add a document converter (D) and do not add
parent-child pieces (C).**

In the order the upload runs:

1. **The reader** (`read_file`) reads the file. A file it cannot read is
   refused whole, as now.
2. **The marking check** (`file_marking` over `FileText.edges()`) runs on the
   reader's head, foot and label. A marked file is stopped, an alarm row
   written, nothing stored, as now.
3. **The whole text is masked** as one string, as now.
4. **The headings are found in the masked text**: a `#` line of `.md`, a
   heading style of `.docx` (the reader learns to mark them), and in every
   other format a short line that starts `2.`, `2.1` or `2.1.1`. A heading
   path is its ancestors' lines, by level. A file with none has an empty path
   and is cut as in B.
5. **Each section is split recursively**, coarsest separator first (a blank
   line, a line break, a sentence end, a space, a hard cut), into pieces of at
   most **200 characters**, without overlap. Joined, the pieces are the masked
   text again.
6. **Each piece is embedded as `path` + line break + `piece`** (the piece
   alone when the path is empty) and **stored as the piece alone**. The
   embedded string and the stored piece both pass `assert_masked`. The path is
   cut to a fixed length so that it cannot outweigh the piece.
7. **The search** applies a similarity floor of 0.51, and a hit may carry the
   excerpts of the two or three nearest pieces of its material.

Stage it, so each step can be measured and undone alone:

- **Stage 1: the cutter and the floor** (5 and the 0.51). No dependency, no
  schema change, no change to the readers.
- **Stage 2: the heading path in the embedded text** (4 and 6). The `.docx`
  reader learns heading styles; nothing else about a reader changes.
- **Stage 3: several excerpts per hit** (7). A change to `MaterialHit`, which
  is module B's own type in `schemas.py`, not a contract; the agent tool and
  the web page that read it are looked at first.

### The four named options, and why not

| Option | Decision | Why, from the evidence |
| --- | --- | --- |
| Recursive text splitter, 200 characters | **use** | The excerpt shown holds the answer 63% to 79% on the long files and two-section documents (89% with Docling's body). No dependency, no privacy rule moves. |
| Document converter (Docling) and Markdown | **not now** | It cannot make the marking check or the refusals: Markdown alone misses a header or footer label (4 of 4 placements). Behind today's check it adds about 1 GB to each worker that converts, a second read of each file and a change to `privacy.md`, for 10 points of the excerpt shown on the long files and on the reports (89% against 79%, and 81% against 71%; why it does better on the long tables was not examined) and about 1 point of answers found on the 200-file shelf. |
| Markdown chunking (split on headings) | **only as the path** | A split on headings alone gained nothing (27% against 23% to 43%). The gain is the heading path in the embedded text, and that comes from the reader's text without a conversion. |
| Parent-child chunking | **no** | The parent has no reader: no model is given a material's text (#817 10(b)), an answer is at most 200 characters, and a child of 200 characters is already the excerpt cap. The path in the embedded text does what a parent's context was for, with no column. |

### When to look at a converter again

The case that was not tried and that nothing here handles is a PDF whose
headings carry no number. A converter's layout model finds them where a text
rule cannot, but its levels come flat from a PDF and have to be repaired, as
H was. It would run behind today's marking check and only for the body, and
it would be a new decision with its own measurement on real reports, on the
release host's memory limit.

### What this changes in the code that exists

- **No dependency, no migration, no column.** The embedded string is not the
  stored text; the embedding of a stored row says nothing more.
- **`cut` is replaced** by the splitter of 5; `CHUNK_CHARS` goes from 500 to
  200. The docstrings that call the excerpt cap "under half of a stored
  piece" and the tests that assert it change: with pieces of at most 200
  characters, `excerpt_of` returns the whole piece.
- **The `.docx` reader** marks heading lines. Its text for the marking check
  (`edges()`) is not changed, and a test shows that the same files are stopped.
- **A piece and its path are masked text.** The headings are found after the
  masking, so the path holds no unmasked value. A heading that held a masked
  run stays a masked run.
- **`_kept_already` compares pieces "cut the same way".** An old upload and a
  re-upload of one file are no longer one material, as the ADR already said of
  any new cut.
- **Old rows are not re-cut** unless the owners decide it (open decision 3).
  Two generations are searched side by side; their scores are not on one
  scale, which the floor of 0.51 has to be read against.
- **Upload time grows with the number of pieces** (305 against 229 on the
  200-file shelf, about 1.3 times). It is measured through the product below.
- **`privacy.md` is not changed**: no reader of unmasked text is added, and
  the masked text still goes to the embedder only.

## Open decisions for the owners

1. **Piece size.** 200 is the proposal; 500 stays the alternative, and the
   floor goes with it.
2. **The similarity floor** (`material_search_min_similarity`): 0.51 for
   pieces of 200 (0.52 if the body were Docling's), changed with the cut in
   the same pull request, and measured again through the product.
3. **Old uploads.** Leave the old rows with their old cut, or re-cut them on a
   migration. The stored pieces joined are the masked text again, so a re-cut
   is possible; the heading styles of a `.docx` are not in them, only its
   numbered lines.
4. **One excerpt or several** (stage 3): up to two or three pieces of one
   material per hit. Whether a longer hit reads well on the page and in the
   chatbot's answer is a product question.
5. **Headings without numbers** in a PDF: accept an empty path (cut as in B),
   or look at a converter later, under "When to look at a converter again".
6. **The length of a path** that is embedded with a piece.
7. **A model that reads a material** is not proposed and not needed by this
   evidence; it would change #817 10(b) and `privacy.md` and would need a
   local model, since material text goes to no outside API.

## Evaluation

Done, on invented text, on one machine: the marking check against Docling's
output, the text kept, retrieval over 220 questions, the floors at 200
characters with the 70 questions nothing answers on shelves of 30 to 200
files (replayed, exact scores), whether the excerpt holds the answer on 38
keyed questions, ten repeating-structure reports, and nine label placements.

To do before stage 1 is merged:

- **Through the product**: `store_upload` and `search_materials` on PostgreSQL
  with pgvector, not exact scores, so `CANDIDATES` is in and the floor of 0.51
  is confirmed. A shelf in which the word match fires is needed to measure it;
  on this shelf it never did.
- **A shelf with more long files**, so that the piece size is tested where it
  can matter.
- **A boundary test**: a sentence, a table row and a heading that straddle the
  old 500 boundary stay whole.
- **Upload time**: the request's time for a 10 MB file at 200 characters.

To do before stage 2 is merged:

- **Real reports**: five to ten that are not confidential, run through the
  same ten-report measurement, and a set with unnumbered headings. The ten
  invented ones are a stress test and say how much a path can gain, not how
  much it will.
- **A marked file through the whole path**, for each place in each format, to
  show that the `.docx` reader's heading marks change nothing in the check.
- **Keys for the other 182 questions**, so that "the excerpt holds the answer"
  is measured on tables, slides and prose too.

## Consequences

- Stage 1 moves piece boundaries without touching a privacy rule: the cut runs
  after the reader, the marking check and the masking, and every piece still
  passes `assert_masked`.
- Stage 2 adds text to what is embedded but not to what is stored or shown;
  the path comes from the masked text, so it carries nothing that was not
  already cleared.
- Stage 3 shows up to three excerpts of one material, 600 characters at most,
  never the document.
- Stored rows of two generations live side by side until the old ones expire.
- "Readers are pure Python, no converter, no OCR" (`material_reading.py`,
  `pyproject.toml`) stays true.
- A converter is not closed off: the evidence says what it would have to beat
  and where (unnumbered headings in a PDF), and that it would run behind the
  marking check, not instead of it.
