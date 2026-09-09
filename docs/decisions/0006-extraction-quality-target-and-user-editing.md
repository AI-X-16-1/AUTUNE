# 0006. Extraction aims at the published ceiling, and the user finishes the list

**Status:** Accepted
**Date:** 2026-09-09
**Deciders:** 강민구 (module B owner). The PRD metric row needs the team.

## Context

`../product/prd.md` and this module's Metric section both ask for action item
extraction F1 of 0.80 at six weeks and 0.88 at three months. Those numbers were
set before anyone checked what the task scores in the literature.

The best published action item detection F1 on the AMI Meeting Corpus is **43.12**
[1]. That is the positive-class F1 of the best configuration in the paper that
introduced the task's current strongest results; the plain sentence-level baseline
in the same table scores 38.67, and no configuration reaches 44.

Human agreement on the task sits in the same region. The same paper reports κ 0.46
for the English data and κ 0.47 between annotator pairs on its Chinese corpus [1];
the earliest annotation study on ICSI reports agreement as low as κ 0.373 on an
individual meeting [2]. κ and F1 do not measure the same thing, so this is not a
like-for-like comparison — but two trained humans marking the same meeting land
nowhere near 0.80, and that is the figure we set for a model in six weeks.

A target above the ceiling is not ambition. At the six-week review it reads as a
miss, and it says nothing about whether the module works.

The corpus gap makes it worse. AMI is English design roleplay. The AI Hub Korean
set is committee and broadcast discussion, with a chair and a formal register.
AUTUNE is for team meetings that have no chair. A model trained on those two and
measured on real team meetings has no reason to land above the AMI number.

Lowering the target is the obvious fix and it is not enough on its own. An action
item list that misses a third of the commitments is not usable at 0.43, and it is
not usable at 0.65 either. The number was never the product question. The product
question is what happens when the model is wrong.

## Decision

**The target is the published ceiling, and it is reported next to it.** Six
weeks: match 0.43 action item F1 on our own held-out Korean evaluation set. Three
months: beat it. Every report of our number carries the reference number beside
it, so the figure can be read by someone who does not know the task.

The metric we train against is the classifier's **five-way macro F1**, because
that is what the evaluation harness scores and what a training run can move.
Action item F1 is derived from it and exists to compare against the literature.

**Extraction output is a draft the user completes.** The action item screen is a
worksheet, not a report. Every item can be edited, deleted, or added by hand, and
each one shows the utterances it came from so the user can check it without
replaying the meeting.

Recall is ranked above precision. A wrong item costs one click; a missing item
costs re-reading a 45-minute meeting. Items below the confidence threshold are
shown as candidates rather than dropped.

The product metric is therefore **edit cost**: the share of items accepted with
no edit, and the number of edits it takes to reach a list the user accepts. It is
aggregated per meeting. ADR 0003 forbids per-person metrics and that holds here.

**Corrections never become training data.** ADR 0003 keeps customer meeting
content out of training, on the free plan too. A correction updates that
meeting's rows and increments the edit-cost counters; the text does not leave the
meeting. Training labels come from AI Hub public data only.

## Alternatives considered

**Lower the target to something reachable and leave the output read-only.**
Rejected: honest about the number, still unusable, and it leaves the real
question — what the user does with a wrong item — unanswered.

**Keep 0.80 as an aspiration.** Rejected: it is a figure on the metric table, not
a slogan, and the table is what the six-week review reads.

**Editing but no adding.** Rejected: recall is where the model is weakest, so the
missing item is the common case, and it is the one case editing cannot fix.

**Feed corrections back as labels.** The obvious flywheel, and it would close the
corpus gap this ADR opens with. Rejected by ADR 0003: customer meeting content is
not training data, and a flywheel that needs an exception to the privacy rule is
not available to us.

## Consequences

**Easier.** The six-week demo has a number it can defend and a product that works
whatever the number turns out to be. Per-item source utterances were already
wanted for 근거 발화 보기; this makes them load-bearing, and AMI's own
`summaryLinks` annotation is the precedent for the shape.

**Harder.** The API grows: creating an item by hand, soft-deleting one, and
confidence plus source utterances on every row. Edit cost needs instrumentation
that counts without identifying anyone. The candidate section needs a confidence
threshold, and the threshold needs the evaluation set before it can be set.

**Accepted cost.** Edit cost has no published baseline, so the first six weeks
establish ours and the first report has nothing to compare against.

**Revisit if** an evaluation set drawn from the team's own meetings scores well
above the AMI ceiling, which would mean Korean team meetings are an easier task
than AMI rather than a harder one.

## References

[1] Liu, Deng, Zhang, Chen and Wang, *Meeting Action Item Detection with
Regularized Context Modeling*, ICASSP 2023. https://arxiv.org/abs/2303.16763 —
source of the 43.12 AMI figure, the 38.67 baseline, and both κ values.

[2] Morgan, Chang, Gupta and Purver, *Automatically Detecting Action Items in
Audio Meeting Recordings*, SIGdial 2006.
https://nlp.stanford.edu/pubs/sigdial06.pdf — the ICSI annotation study, F
measure between 13.81 and 31.92 and per-meeting κ as low as 0.373.

Note that AMI has no dedicated action item annotation layer — its own
documentation places actions inside the abstractive summaries — so an AMI action
item benchmark is derived rather than annotated directly. Read 43.12 as the best
published number on the task, not as a figure from a canonical labelled split.
