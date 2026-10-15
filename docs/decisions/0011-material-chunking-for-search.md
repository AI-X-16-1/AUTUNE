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

Today's reader decides refusal and marking, unchanged; Docling gives the body
only when it read the file and kept at least 95% of the letters and digits the
reader read.

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

The proposer's decision of 2026-10-15: **the marking check stays as it is, and
the body is made as Markdown.** In the order the upload runs:

1. **Today's reader** (`read_file`) reads the file. A file it cannot read is
   refused whole, as now.
2. **Today's marking check** (`file_marking` over `FileText.edges()`) runs on
   the reader's head, foot and label. A marked file is stopped, an alarm row
   written, nothing stored, as now. Docling has not run yet.
3. **The body** is Docling's Markdown for `.pdf`, `.docx`, `.pptx` and `.xlsx`
   when Docling read the file and kept at least 95% of the reader's letters
   and digits; otherwise the reader's text. `.txt`, `.md`, `.csv`, `.xls` and
   `.ppt` are always the reader's text. Docling runs with OCR off, no remote
   service and no network, on the byte stream in the request's memory.
4. **The whole body is masked** as one string, as now, before it is cut: not
   item by item, so a value that runs over a table cell is still one value.
5. **Markdown chunking**: a heading line starts a section, and the section is
   the parent; its heading path is kept with it.
6. **Recursive splitting** inside a section, coarsest separator first (a blank
   line, a line break, a sentence end, a space, a hard cut) into children of
   **200 characters**, without overlap.
7. **Each child passes `assert_masked`**, then the duplicate check, the
   embedding of the child (with its heading path, if stage 3 is taken) and the
   store.

Stage it, so each step can be measured and undone alone:

- **Stage 1: the cutter** (6) on the reader's text. No dependency, no schema
  change. This is the step the numbers support most firmly (214 to 217 of 220
  at top-5).
- **Stage 2: the Docling body** (3, 4, 5 without the parent columns), behind
  its own setting so it can be switched off without a deploy of old code.
- **Stage 3: `parent` and `section` columns** and the heading path in the
  vector, only if stage 2 stays.

Not proposed: Docling deciding marking or refusal (Evidence: it fails both);
OCR; overlap between pieces.

### What this changes in the code that exists

- **The upload reads a file twice** (the reader, then Docling) and holds
  about 1 GB more in each API worker process that converts. The release
  host's container limit and the concurrency of uploads are not known here.
- **The conversion runs in the upload request**, as the reader does, because
  the file is not kept and its bytes go to no task (#817).
- **The refusal and the marking check do not move**, which is the point of the
  order: they are the privacy rules of #817 and they run before Docling.
- **`privacy.md` names Docling.** The sentence that the masked text goes to
  the embedder only is about masked text; Docling sees the file before it is
  masked, in the same process, as the readers do. It is named there and the
  owners agree to it.
- **The same text is no longer one material across formats.** `_kept_already`
  documents that the same text from a `.docx` and from a `.txt` is one
  material; a `.docx` body is now Markdown and the `.txt` is not.
- **The excerpt has little left to choose.** A child of at most 200 characters
  is within `EXCERPT_CHARS`, so `excerpt_of` returns the whole child. The
  docstrings that call the cap "under half of a stored piece" and the tests
  that assert it change.
- **The parent has no reader** (#817 10(b): no model reads a material, an
  answer is at most 200 characters), so the section is a grouping and the
  heading path in the vector is what it buys.
- **The floors move** (similarity, relative cut, word match): measured at 500
  characters, measured again at 200, not carried over.
- **Dependency** (invariant 8): Docling and CPU PyTorch in
  `modules/extraction/pyproject.toml`; a 1.1 GB virtual environment on the
  proposer's machine, and model weights fetched once from Hugging Face that
  have to be in the image, since nothing is fetched at request time.
- **Masking on Markdown** needs tests with a number in a table row, in a
  link's text and in an emphasised run.

## Open decisions for the owners

1. **Docling as a dependency** (invariant 8), with the memory and time it
   costs per upload request, and `privacy.md` naming it.
2. **Piece size.** 200 is the proposal; 500 stays the alternative.
3. **Upload time and memory.** More pieces and a conversion make a longer
   request; is a limit on pieces per upload, or on concurrent conversions,
   needed?
4. **Old uploads.** Leave old rows with their old cut, or re-cut on a
   migration (read the stored masked text back and embed it again).
5. **The 95% rule** of step 3: kept as it is, or a different line, or the
   worksheet's name added back so that tables stop falling back.

## Evaluation

Done: the runs under Evidence, on invented text, on one machine.

To do before stage 1 is merged:

- **The floors at 200**, with the 70 questions nothing answers, on shelves of
  30 to 200 files, as the floors were first measured.
- **Through the product**: `store_upload` and `search_materials` on PostgreSQL
  with pgvector, not exact scores, so `CANDIDATES` and the word match are in.
- **A shelf with more long files**, so that the piece size is tested where it
  can matter, and more `.pdf`, `.docx`, `.pptx` for the Docling body (41 of
  the 200 files are what Docling's Markdown was used on).
- **Whether the excerpt holds the answer**, with answer words in the labels.
- **A boundary test**: a sentence, a table row and a heading that straddle the
  old 500 boundary stay whole.

To do before stage 2 is merged:

- **A 10 MB file**: time and memory in the server's container, and file
  access traced there, not only in Python.
- **A marked file through the whole path**, for each of the five places in
  each format, to show that Docling never runs on a stopped file.
- **Masking on Markdown** with the shapes above, and a value broken across a
  line in a PDF, which Docling's joining of lines may or may not help.

## Consequences

- Stage 1 moves piece boundaries without touching a privacy rule: the cut runs
  after the reader, the marking check and the masking, and every piece still
  passes `assert_masked`.
- Stage 2 keeps the marking check and the refusal as they are, and adds a
  reader of unmasked text in the same process, with a cost in memory and time
  that is paid on every PDF, `.docx`, `.pptx` and `.xlsx` upload.
- Stored rows of two generations live side by side until the old ones expire;
  `_kept_already` will not call an old and a new upload of one file the same.
- Adopting Docling makes "readers are pure Python, no converter, no OCR"
  (`material_reading.py`, `pyproject.toml`) untrue for the body, and those two
  statements change with it.
