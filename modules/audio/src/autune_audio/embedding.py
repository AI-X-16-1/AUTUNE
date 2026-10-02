"""The width of a stored voice vector, in a module that imports nothing.

``models`` needs it for the ``Vector`` column and ``live.embedder`` needs it to
check the checkpoint at load (#363 item 3). It lives here so the embedder does
not pull SQLAlchemy and pgvector in for one integer (review of #611).
"""

from __future__ import annotations

EMBEDDING_DIM = 256
"""Width of ``pyannote/wespeaker-voxceleb-resnet34-LM``'s output -- the model
``live/embedder.py`` already runs, so a live vector and a stored one are
comparable."""
