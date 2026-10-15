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
children of about 200 characters; the parent is the 500-character piece that
holds them. The search ranks children and answers with the parent's material
and position. Schema: the existing `ext_material_chunks` row stays the parent
and a new table `ext_material_child_vectors (material_id, position, start,
end, embedding)` holds one vector per child, with offsets into the parent's
text, so the masked text is stored once. Deleting a material cascades to both.

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

Stage it, and let the evaluation below decide each step.

1. **Stage 1: B.** It changes one function and its constants, adds no table,
   no dependency and no new place that holds text. It can be reverted by
   re-deploying `cut`.
2. **Stage 2: C only if** stage 1 does not close the gap on the evaluation set
   and a question of the form "the answer is in one sentence of a long piece"
   is where it fails.
3. **Stage 3: D only after a spike**, and only with the owners' answer to the
   dependency question below.

Not proposed: overlap between pieces. It breaks "joined, the pieces are the
masked text" (which `_kept_already` and the excerpt rely on), duplicates masked
text in the database, and leaves a sentence in two excerpts. Boundary-aware
cuts are the cheaper way to the same goal.

## What parent-child can and cannot buy

C's usual reason is to retrieve on a small piece and hand a larger one to a
language model. **Here no language model reads a material's text** (#817 10(b),
`material_search`'s docstring). So today the parent is never read by anything;
the gain is only a more exact match on a short child. Where the child is
itself the excerpt (at most 200 characters, which is the existing cap), the
excerpt gets better too. If the owners later allow a local model to read a
parent, that is a privacy decision of its own (a new reader of the text) and is
not made by this ADR.

## Open decisions for the owners

1. **Piece size.** Stay at 500, or move to about 300? Every step down is more
   rows and more vectors per upload: a file at the 300,000-character limit is
   about 600 pieces at 500, about 1,000 at 300, about 1,500 at 200.
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
   pure Python on purpose?

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
- C adds a table that is a second place a piece is addressed (vectors only; the
  text is not copied). It needs a line in `docs/architecture/privacy.md` and
  `docs/modules/extraction.md` in the same change.
- D, if ever, changes what "readers are pure Python, no converter, no OCR"
  means in `material_reading.py`'s docstring and in `pyproject.toml`'s comment,
  and needs the owners' agreement before it is a dependency.
