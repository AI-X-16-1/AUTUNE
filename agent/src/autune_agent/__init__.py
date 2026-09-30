"""The agent layer (ADR 0010, docs/architecture/agent-layer.md).

A main agent routes chat and triggers to five feature subagents; modules A-E
are the tools they read. Nothing imports this package except ``apps/``.
"""
