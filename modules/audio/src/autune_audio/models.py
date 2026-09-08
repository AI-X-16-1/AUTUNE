"""Tables owned by module A.

Every table name starts with ``aud_``. Foreign keys may reference shared
entities (``meetings.id``, ``utterances.id``) but never another module's tables.
Each table needs a path to deletion by meeting_id or user_id.

See docs/architecture/data-model.md.
"""

from __future__ import annotations

from autune_core import Base  # noqa: F401  (re-exported for migrations)
