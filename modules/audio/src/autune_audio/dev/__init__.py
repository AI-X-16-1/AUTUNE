"""Development-only surface for trying the pipeline by hand.

Everything here is gated on ``AUTUNE_ENV=local`` and registered nowhere else.
It exists so a person can drop a recording on a page and read the transcript
back, which is the only way to judge Korean STT quality before an evaluation
set exists.

**This is not the production path.** The real flow is asynchronous: upload,
Celery task, PII masking, raw-audio deletion, then ``TranscriptReady``. This
endpoint transcribes synchronously and returns unmasked text to the caller, so
it must never be reachable outside a developer's own machine.
"""
