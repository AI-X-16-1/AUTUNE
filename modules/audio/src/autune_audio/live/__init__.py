"""Live transcription: browser microphone in, one row per utterance out.

**Invariant 11 on this path.** Audio exists here as frames in memory and
nowhere else. A segment is dropped the moment it has been transcribed; the
segmenter holds at most one open utterance and discards silent frames as they
arrive; no code in this package opens a file. The unmasked transcription is a
local variable in ``LiveSession`` and reaches no log, no exception and no row.
When the connection closes, the buffers go with the session. This path stores
nothing, so it writes no ``aud_masking_events`` either -- that is the final
pipeline's, after the upload.

Storing nothing is the simplification the design rests on. The live channel is
display; the truth is always made by the upload afterwards. That is also why
live row ids are ``utt_live_…`` and never equal the stored ``utt_…``: the two
are not claimed to be the same utterance.

Design: docs/modules/audio-live-transcription.md.

This package deliberately does not import ``.routes`` here: the router is
imported by ``autune_audio.router`` directly from ``.live.routes``, so that
importing ``autune_audio.service`` (as the Celery worker does, through
``autune_audio.tasks``) never drags FastAPI and the websocket stack into a
process that has no socket to serve.
"""
