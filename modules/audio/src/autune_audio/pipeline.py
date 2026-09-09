"""AI pipeline for module A.

Model loading and inference only — no database writes, no HTTP. Pin model
versions explicitly and load once at worker startup, not per task.
"""

from __future__ import annotations
