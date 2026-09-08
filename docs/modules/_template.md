# Module <Letter>. <Name>

> Template for a module document. Copy, fill in, delete this line.

| | |
| --- | --- |
| **Package** | `autune_<name>` |
| **Owner** | <name> |
| **Backend** | `modules/<name>/` |
| **Frontend** | `apps/web/src/features/<feature>/` |
| **Table prefix** | `<prefix>_` |
| **API prefix** | `/api/<name>` |

## Responsibility

Two or three sentences. What this module is accountable for, stated so that a
reader can tell whether a given piece of work belongs here.

## Non-goals

What this module explicitly does not do, and which module does it instead. This
section prevents scope drift more than any other.

## Inputs

| Source | Contract or table | Notes |
| --- | --- | --- |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |

## Pipeline

The processing steps, in order, with the model or algorithm used at each.

## Tables

| Table | Purpose | Key columns |
| --- | --- | --- |

Neo4j labels and Chroma collections, if any.

## API

| Method | Path | Purpose |
| --- | --- | --- |

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |

## Slack surface

What this module sends to Slack, to whom, and when.

## AI stack

| Component | Model or algorithm | Version pin |
| --- | --- | --- |

## Metric

The KPI this owner reports, its target, and how to run the evaluation.

## Privacy notes

Anything in this module that touches personal data, and the constraint that
applies. Link to `../architecture/privacy.md`.

## Open questions

Decisions not yet made. Remove entries as they are resolved.
