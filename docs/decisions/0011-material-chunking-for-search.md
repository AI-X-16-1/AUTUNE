# 0011. How an uploaded material is cut into pieces for search

**Status:** Proposed
**Date:** 2026-10-15
**Deciders:** 강민구 (proposer); 김민경, 박재경, 문민재, 이승환 to agree or object on the PR.

This is a draft for discussion. Nothing in it is built, and no number in the
"Evaluation" section has been measured yet.

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

## Proposed decision

The configuration the proposer asked to have written down (2026-10-15), to be
evaluated before it is built. It is options D, B and C together:

1. **Docling converts the uploaded file to Markdown.** It replaces the
   `material_formats` readers as the source of the body text.
2. **Markdown chunking.** A heading line starts a section; the section is the
   parent, and its heading path travels with it.
3. **Recursive splitting inside a section**, coarsest separator first: a blank
   line, a line break, a sentence end, a space, a hard cut.
4. **Parent-child.** The children are what is embedded and searched; a child
   belongs to one parent section.
5. **Children of 200 characters.**

Stage it, so that each step can be measured and reverted alone: the cutter
(2-3, 5) first, on the text the readers give today; the converter (1) only
after a spike; the parent grouping (4) last. Overlap between pieces is not
proposed: it breaks "joined, the pieces are the masked text" (which
`_kept_already` relies on) and puts one sentence in two excerpts.

### What this configuration changes in the code that exists

- **Where the conversion runs.** The file is not kept and its bytes go to no
  task (#817), so the conversion happens in the upload request, as the readers
  do. A Docling conversion is much slower than a pure-Python read. How slow, on
  the release host, for a 10 MB file, is a number the spike has to produce;
  moving it to a task would mean keeping the original, which is refused.
- **The marking check keeps its own reading.** It looks at the head, the page
  footers and the file's label (`FileText.edges`), not the body, and a file
  whose text cannot be read is refused whole. A Markdown export of the body is
  not that. Whatever replaces the readers must give the marking check the same
  head, footers and label, or the readers stay for that check. It is not
  weakened to fit the converter (`docs/architecture/privacy.md`, #817 point 9).
- **OCR stays off.** Today a page that is only a picture refuses the file,
  because text that was not read can be neither checked nor masked. A converter
  that reads such a page by OCR changes that rule; that is a separate decision,
  not a default of a library.
- **No network in the conversion.** Models are in the image, nothing is
  fetched at request time, no remote service is enabled, and nothing of the
  file leaves the host.
- **Masking runs on Markdown.** A number inside a table row, a link's text or
  an emphasised run must still be found by the masker, and every child still
  passes `assert_masked` before it is stored. Tests with those shapes come with
  the change.
- **The same text is no longer one material across formats.** `_kept_already`
  documents that the same text from a `.docx` and from a `.txt` is one
  material. After conversion the `.docx` carries Markdown syntax the `.txt`
  does not; the two differ.
- **The excerpt has nothing left to choose.** A child of 200 characters is the
  excerpt cap (`EXCERPT_CHARS`), so `excerpt_of` returns the whole child and
  its centring on the question's words does nothing. The docstrings that say
  the cap is "under half of a stored piece" and the tests that assert it change
  with the piece size.
- **The parent has no reader.** Nothing returns a parent's text: no model reads
  a material (#817 10(b)) and an answer is at most 200 characters. With a 200
  character child the parent is only a grouping. What does help a short child
  is its heading path: embedding the heading path together with the child's
  masked text gives the vector the context a bare 200 characters lack, with no
  parent read anywhere. This is the part of C that is kept; a parent that is
  read by a local model is a privacy decision of its own, not made here.
- **Schema.** Additive, on module B's own branch: `ext_material_chunks` gets a
  nullable `parent` (an integer, the section's index) and a nullable `section`
  (the heading path, masked text, so it passes `assert_masked` too). Children
  do not overlap, so a parent's text is its children joined in `position`
  order: no second copy of the text and no parent table. Old rows have both
  columns null.
- **Rows and vectors.** A file at the 300,000-character limit is about 600
  pieces at 500 and about 1,500 at 200, all embedded in the request.
- **The floors move.** The similarity floor, the relative cut and the
  word-match floor were measured with KURE-v1 on made-up text at 500
  characters. They are measured again at 200, not carried over.
- **The dependency.** Docling goes in `modules/extraction/pyproject.toml`
  (invariant 8) and the lock is regenerated. Third-party documentation reports
  about 1.8 GB for the install with CPU-only PyTorch and about 1.4 GB of model
  weights fetched from Hugging Face on first use (MIT licence). These are not
  measured here; the spike measures them on the release host's image, its
  150 GB volume and its CI time.

## What parent-child can and cannot buy

C's usual reason is to retrieve on a small piece and hand a larger one to a
language model. **Here no language model reads a material's text** (#817 10(b),
`material_search`'s docstring), so the larger piece has no consumer. What the
configuration above does buy is a more exact match on a short child, and,
where the child is the excerpt, an excerpt that is a whole sentence or two
rather than a window cut out of 500 characters.

## Open decisions for the owners

1. **Piece size.** 200 is what is asked for here; 500 stays the alternative.
   Every step down is more rows and more vectors per upload: about 600 pieces
   at 500, about 1,000 at 300, about 1,500 at 200 for a file at the
   300,000-character limit.
2. **Upload time.** Vectors are computed in the upload request (no text goes to
   a task). More pieces is a longer request. Is a limit on pieces per upload
   needed, or a different place to embed?
3. **Old uploads.** Leave old rows with their old cut (they expire with the
   team's window, or stay for teams that keep until deleted), or re-cut on a
   migration? Re-cutting means reading stored masked text back and embedding
   it again.
4. **The similarity floor and the word-match floor** were measured for KURE-v1
   on made-up text with 500-character pieces. A different piece size moves
   them. They have to be re-measured with the change, not carried over.
5. **A converter dependency (D)**: acceptable at all, given the readers are
   pure Python on purpose? If yes, is OCR for scanned pages off (as above), and
   is a request-time conversion of a 10 MB file acceptable on the release host?

## Evaluation (to do, before stage 1 is merged)

- **A labelled set that is not a team's material.** Made-up documents in the
  formats the screen takes, with question → piece labels, the way the floors
  were first measured. No real upload, no AI Hub sheet, and nothing masked or
  unmasked is sent to an outside service to build it.
- **Metrics:** whether the right material is in the top 5, its rank, and
  whether the excerpt of that hit contains the answer's words; the number of
  pieces and the upload time per file.
- **Compare** A, B (and C if reached) on the same questions with the floors
  re-measured for each, so a better cut is not judged by a floor tuned for the
  old one.
- **A boundary test:** a sentence, a table row and a heading that straddle the
  old 500 boundary stay whole under B.

## Consequences

- Stage 1 makes the piece boundaries better without touching privacy rules:
  the cut runs after masking and every piece still passes `assert_masked`.
- Stored rows of two generations live side by side until the old ones expire;
  `_kept_already` will not call an old and a new upload of one file the same.
- C adds two nullable columns to `ext_material_chunks` (`parent`, `section`),
  both masked text or an index. It needs a line in
  `docs/architecture/privacy.md` and `docs/modules/extraction.md` in the same
  change, and the `section` text passes `assert_masked` like a piece does.
- D, if ever, changes what "readers are pure Python, no converter, no OCR"
  means in `material_reading.py`'s docstring and in `pyproject.toml`'s comment,
  and needs the owners' agreement before it is a dependency.
