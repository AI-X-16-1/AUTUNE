# Module C. Gap Detection

| | |
| --- | --- |
| **Package** | `autune_gap` |
| **Owner** | 박재경 |
| **Backend** | `modules/gap/` |
| **Frontend** | `apps/web/src/features/gap/` |
| **Table prefix** | `gap_` |
| **API prefix** | `/api/gap` |

## Responsibility

Find what the meeting should have covered and did not. Build a topic graph from
the transcript, measure who participated in which topic, compare against a
domain template, and score the risk of each missing item.

## Non-goals

- Extracting what *was* said into action items — that is B.
- Connecting to past meetings — that is D. C works within one meeting.
- Predicting future misalignment — that is E.

## Inputs

| Source | Contract |
| --- | --- |
| A | `TranscriptReady` via `autune.transcript.ready` |
| `packages/core` | `meetings`, `participants`, `utterances` (read-only) |

## Outputs

| Destination | Contract | Event |
| --- | --- | --- |
| E | `GapReport` | `autune.gap.completed` |
| Slack | Gap report and question cards | — |

## Pipeline

1. **NER** — spaCy extracts entities: features, systems, metrics, people, dates.
2. **Relation extraction** — build subject–relation–object triples. Marker
   rules today; LLM assistance for the hard cases is the seam, not the
   implementation.
3. **Topic graph** — persist nodes and edges as rows (`gap_topics`,
   `gap_topic_edges`), then load them into NetworkX.
4. **Centrality** — PageRank and betweenness identify which topics carried the
   meeting.
5. **Participation matrix** — per topic, who spoke and who was silent.
6. **Template comparison** — match the meeting against a domain template and
   find unfilled items.
7. **Risk scoring** — score each gap using topic centrality, participation
   imbalance (a topic no engineer spoke on is riskier), and template weight.
8. **Question generation** — produce a concrete question that would close each
   gap.
9. **Publish** — emit `GapReport`.

### Step 1 as built

**Measured first.** `ko_core_news_lg` 3.8.0 over the two shared fixtures finds
six entities in `transcript_ready.typical` — 오늘은, 한번, A, B,
다음 주 화요일까지, `010-****-5678` — and one in `transcript_ready.short`: 네,.
Everything those meetings are *about* — 실시간 개인화, 인기순 정렬, 콜드스타트,
검색 개인화 기능, 응답 시간 — is a plain noun the NER never sees, because
`feature` and `system` are this product's vocabulary and no general model has a
label for them. A gap report built on that graph would have been about 한번 and
A. `modules/gap/tests/unit/test_spacy_ner.py` (marked `model`) pins the
measurement.

Two things came out of it, both in `pipeline.spoken` as pure functions — the
same reason `graph` is pure: a judgement that can only be exercised by loading
a 500MB pipeline is a judgement nobody tests.

- **Noun terms.** A maximal run of content-noun tokens is the compound the
  speaker said, and that is what the graph needs a node for. The run is read
  from the morpheme tag (`ncn+jxt`) rather than the coarse part of speech,
  which calls 개인화로 an adverb; a particle, an ending or a stopword breaks the
  run, so the label is 개인화 and not 개인화로. Content noun means common,
  proper and foreign (`nc*`, `nq`, `f`) and **not** pronoun, numeral or bound
  noun: matching joins a run rather than breaking it, so letting 그거 or 두 in
  would give a node called 그거 검색 기능 and split the topic the meeting calls
  검색 기능 everywhere else. The tag rule is not enough on its own — this model
  tags 그거 as a common noun — so the demonstratives sit in the stoplist,
  measured rather than assumed.
- **A span the model found is claimed whether or not we keep it.** An
  implausible one-letter person, an `LC` meeting room, an `OG` vendor: the
  characters are spoken for, so a refusal cannot come back as a term under
  another name (강남 회의실, 카카오 API 연동). One character belongs to at most
  one thing, the rule `FakeNer` already follows. Whitespace tokens are skipped
  rather than passed through, or a double space — which ASR output carries —
  would split a compound the same meeting says as one topic elsewhere. Both
  raised in review of #222.
- **Two precision filters.** A one-character `person` is not a person: A/B 결과
  gives A and B as `PS`, and both became connected nodes. A `metric` with no
  digit in it is not a metric: `QT` on spoken Korean fires on 한번, 네, 좀.
  Precision is C's metric and a false topic is what a false gap is raised on.
- **Dates are not filtered, and that is a known hole.** 오늘은 is still a node,
  particle and all, while 오늘 sits in the noun-run stoplist — the same word
  refused on one path and taken on the other. A rule that drops it while
  keeping 다음 주 화요일까지, a deadline the meeting set and a value risk
  scoring will read, is not a one-liner. Issue #230, raised in review of #222.

**A term's kind stays undecided.** It carries the label `term`, the sixth in
`ENTITY_LABELS`, which says "a compound the meeting named" and not which of
`feature` or `system` it is. Telling those two apart is what #13's trained
model is for, and a label that guessed would be a guess template comparison
later reads as fact.

What this does **not** fix, and what #13 still carries:

- **Recall is unmeasured.** There is no annotated set and no eval harness for
  step 1, so "better than six junk entities" is the whole claim. The number
  that matters is gap precision, which cannot be read until something writes
  `gap_gaps` (#35).
- **A compound the model mis-analyses still splits.** 실시간 개인화로 is tagged
  실시간 + 개인화로, so the graph gets 실시간 and loses 개인화. Rejoining it
  means trusting a lemma split that is wrong as often as it is right here.
- **The stoplist is a judgement.** Every entry is a topic the graph can no
  longer raise a gap about, so it is short, and dismissals are what tune it
  (#35) rather than taste.

### Step 2 as built

Rules only, no model, no network: `pipeline/relations.py`. Issue #32 puts the
non-LLM share at about 70% and says to exhaust the rules first, so they are
written and measured before anything is sent anywhere. Today the share is 100%
— there is no assisted implementation — and what the rules cannot read is a
named list below rather than a shrug.

**Four relations** are in the vocabulary, each one a thing risk scoring (#35)
should treat differently: `depends_on`, `blocked_by`, `part_of`,
`alternative_to`. **Three of them have a rule.** Every rule keys on a **marker**
the speaker actually said, and fires only with two topics around it in one
utterance. Proximity alone stays `co_occurs`, which the graph writes without
asking the extractor.

| Relation | Marker | Reads |
| --- | --- | --- |
| `depends_on` | 필요, 있어야, 되어야, 선행, 전제, 없이는, 없으면 | "정렬 로직은 인덱스가 필요합니다" |
| `blocked_by` | a blocker word (안 잡, 미정, 막혀, 무리, 이슈, …) **and** a causal connective in the same clause | "실시간은 콜드스타트가 안 잡혀 있어서 무리입니다" |
| `alternative_to` | 대신, 말고, 보다는, 아니라, 반면, `vs` | "인기순 정렬 대신 실시간 개인화로" |
| `part_of` | — **no rule** | |

`part_of` has no rule because 의 marks possession and composition with the same
character: "검색의 정렬 로직" is a part of a thing, "검색 기능의 담당자 일정" is
somebody's calendar, and the rule read both. It is the same problem as `는데`
and gets the same answer — a case for the assisted implementation rather than a
marker list. The label stays in the vocabulary: the graph can carry it and #35
weights it, and what a rule can read today is a different question from what an
edge may say. Raised in review of #249.

**Measured.** Over `transcript_ready.typical` the rules assert exactly one
relation — `실시간 blocked_by 콜드스타트` — against six co-occurrence edges.
`transcript_ready.short` asserts none: it names two topics in two utterances and
never says how they stand to each other. Both numbers are pinned in
`modules/gap/tests/unit/test_spacy_ner.py` (marked `model`).

Three things came out of that measurement, and each one changed the design:

- **The rules read every name the meeting used, not this utterance's own
  entities.** Entity extraction claims a bare noun run and stops at a particle,
  so the utterance that *states* a relation is the one where the topic wears
  one: 실시간 개인화 is claimed in utterance 1, and 실시간은 in utterance 2 is
  what says it is blocked. Keyed per utterance the rules found **nothing at all**
  on either fixture. It widens which utterances can state a relation and not
  what a topic is — `graph.relation_edges` drops any relation whose ends are not
  both topics, so nothing enters the graph this way.
- **A marker swallowed by a label is a relation nobody can see.**
  `ko_core_news_lg` tags 대신 and 말고 as ordinary common nouns, so a noun run
  joined them: "인기순 정렬 대신 실시간 개인화로" came back as one topic called
  인기순 정렬 대신 실시간. The three contrast markers are now in
  `spoken.STOP_TERMS`, which fixes a junk node and an invisible relation at once.
- **`는데` and `지만` are not contrast markers here.** "실시간 개인화로
  합의했는데 오늘은 인기순 정렬 얘기가 나왔네요" is a real contrast and "자료
  공유드리는데 확인 부탁드려요" is not, and no surface string tells them apart.
  Spoken Korean uses `는데` as sentence glue, so taking it would make
  `alternative_to` the most common relation in the graph and every one of them a
  coin flip. This is the clearest case for the LLM assistance step 2 is promised.

**A marker is a string, and the clause decides whether the speaker meant it.**
Three guards, each one a sentence that produced an edge before it existed
(raised in review of #249, found by running the extractor rather than reading
it):

- **Negation and questions.** 필요 with 없 or 않 after it in the same clause is
  the opposite of a need; 필요한가요 is a question about one. "캐시 이슈는
  없어서 검색 기능은 바로 진행합니다" is a blocker word, a causal connective and
  no blocker — and it read as `검색 기능 blocked_by 캐시`, the reverse of what
  the speaker said, in the one relation the report treats as a finding. 안 is
  deliberately not a negation marker: "캐시 없이는 안 됩니다" is a need.
- **One clause, both ways.** The causal connective a `blocked_by` needs has to
  be in the blocker's own clause. Searching to the end of the utterance paired
  이슈 with a 없어서 two clauses away and put a date topic on the blocked end.
  The guard window stops at the boundary for the same reason in reverse:
  "인덱스가 필요하고 캐시는 문제 없습니다" must not cancel a need the speaker
  did state.
- **The source is what the sentence is about.** Korean starts a new subject
  after a connective ending, so the nearest mention after the marker is usually
  the next sentence — "정렬 로직은 인덱스가 필요하고 캐시는 다음 주에 봅시다"
  read as `캐시 depends_on 인덱스`. The far end is taken only inside the same
  clause; otherwise the rule looks back for a mention wearing 은/는, which is
  how Korean marks the thing a sentence is about, and only then falls back to
  the mention before the target.

**A marker has to be a word, and a mention has to start one.** Two more from
the same review:

- `vs` sits inside `devs`, and "API devs 검색 기능" made the two topics
  alternatives to each other on the strength of a plural. The Latin markers now
  need word boundaries; the Korean ones are still substrings, because a particle
  attaches directly and there is no boundary to anchor to.
- `실시간` sits inside `비실시간`, and "비실시간 처리가 필요해서 검색 기능은
  미뤘습니다" asserted that 검색 기능 depends on 실시간 — a topic whose name the
  utterance contains and whose meaning it negates. A mention now has to begin a
  word. Only the left side is guarded: Korean attaches particles directly, so
  실시간은 and 실시간으로 have to stay mentions, and telling 실시간성 from those
  needs the tagger rather than a boundary.

**The cue words themselves are not topics.** 필요 and 이슈 join 대신, 말고 and
반면 in `spoken.STOP_TERMS`: the model tags all of them as ordinary nouns, so a
noun run welds them into a label, and "인덱스가 필요 없습니다" produced a topic
called 필요.

**A pair the rules typed gets no `co_occurs` row.** The typed relation says
everything co-occurrence would and more. A pair they said nothing about keeps
it — that is most pairs, and dropping them would leave a meeting nobody spoke
carefully in with no edges at all.

**Every extracted edge weighs 1.** An assertion is not a frequency: a speaker
who says it twice has not made it twice as true, and the table is unique on
`(source, target, relation)` so a repetition could not reach a second row
anyway. Co-occurrence is still counted, because frequency is the only evidence
it has. If #35 wants to know how often a relation was restated, that is a column
and not a number folded into the weight.

What this does **not** do:

- **A wrong edge costs the pair its co-occurrence too**, because a typed pair
  gets no `co_occurs` row. The guards above are why that trade is acceptable;
  it is also why the marker lists are short and every addition needs a sentence
  that fires it. Raised in review of #249.
- **Recall is unmeasured, and low.** One relation out of a five-utterance
  meeting is the whole claim. There is no annotated set for step 2 either — the
  number that matters is gap precision, which cannot be read until something
  writes `gap_gaps` (#35).
- **Nothing crosses an utterance.** "응답 시간 목표는 정해진 게 있나요" is about
  the feature named in the utterance before it, and no rule here reaches back.
- **A relation needs both topics to exist.** A topic only exists if the meeting
  said it bare at least once, because that is what entity extraction claims. A
  thing referred to only as 그거 is in no relation.
- **No LLM path exists.** The seam is `RelationExtractor` and
  `AUTUNE_GAP_RELATION_IMPL`; when one lands it goes through
  `autune_integrations` so `check_outbound` sees the request body. Unlike step 1
  an assisted implementation here is *allowed* — a relation needs the clause,
  not the transcript.

`gap_topic_edges.extractor_version` records which extractor asserted an edge,
and is NULL exactly when none did — that is the `co_occurs` row.

### Steps 3 to 5 as built

`autune_gap.graph` holds the decisions as pure functions; `service` feeds it
and stores what comes back.

- **A topic is a name, normalised.** Mentions whose text matches after
  collapsing whitespace and folding case are one topic, labelled the way the
  meeting first said it. Nothing merges "검색" into "검색 기능": that is a
  judgement about meaning, and a wrong merge hides one topic inside another.
- **An edge is what step 2 asserted, or co-occurrence.** A pair the rules typed
  carries that relation, directed, weight 1. Every other pair named in the same
  utterance gets `relation = "co_occurs"`, weighted by how many utterances named
  both and scaled so the strongest pair is 1, written both ways because
  co-occurrence itself is symmetric. See "Step 2 as built".
- **PageRank is personalised by mention count**, then divided by the top score
  so the topic that carried the meeting is 1. Without the personalisation a
  meeting whose topics share no utterance ranks every topic level.
  Betweenness is unweighted — NetworkX reads a weight there as a distance, and
  ours is a strength.
- **Only a consenting participant's speech is analysed**, and only they appear
  in the participation matrix (`../architecture/privacy.md` section 5). Speech
  with no participant behind it is left out too: unknown consent is not
  consent.
- **Masked spans are never topics.** An entity containing `*` is dropped;
  `010-****-5678` keeps its last four digits by design, and as a node it would
  carry them into a report the whole team reads.
- **Participation is keyed by `participants.id`**, not `users.id`. A
  participant id exists for an unidentified speaker too, and it is scoped to one
  meeting, so what C publishes carries no cross-meeting identity on its own.
  That is not the same as the join being impossible: `participants` is a shared
  table every module may read, so any consumer can resolve a participant id to
  its `user_id`. Accumulating one person's silences across meetings is a
  `../architecture/privacy.md` section 3 violation on the consumer's side — the
  id format does not prevent it, it only declines to hand it over.

`contracts.md` shows `user_…` ids in its `GapReport` example, and the contract
field has no pattern. The example predates this choice; it is flagged rather
than edited here, because `contracts.md` is shared.

A re-run deletes the meeting's topics and rebuilds them in one transaction;
edges, evidence and participation cascade. So would the `gap_related_topics`
rows of a gap already raised — gap generation (#35) has to rebuild those in the
same run, and decide what a re-run does to a gap somebody dismissed.

### Steps 6 and 7 as built

`autune_gap.template` loads the checklists, `autune_gap.detect` compares a
meeting against one and scores what it finds, and `service.detect_gaps` writes
the rows. The comparison is pure — no database, no model, no network — so what
counts as a gap can be argued with without starting Postgres.

**Two templates, per #22.** `general` is five items any cross-functional
decision meeting has to settle; `feature_planning` extends it with five more a
feature meeting has to settle on top. The reason for two rather than five is the
metric: precision 0.70+ measured over five to ten real meetings in W5, and more
templates split that sample one or two meetings deep, where precision cannot be
measured at all. `general` is applied to every meeting unless one is overridden
— a template inferred from the title or the topics makes its whole checklist
false when it guesses wrong.

**Three states, and only two of them raise a gap.** An item is *covered* when a
matched topic carried real weight, *partial* when it came up and was not
settled, and *missing* when nobody said anything of the kind. S20 shows the
three side by side.

- **Matching is containment either way**, over `graph.topic_key`'s
  normalisation: a keyword inside a longer label, and a label inside a longer
  keyword. Deliberately dumb, and the rule v1 measures precision against — what
  replaces it (embeddings over the items) is then a change with a number
  attached rather than a better idea.
- **Two sources of evidence, ranked: the graph, then the speech.** A topic match
  carries a centrality, so it decides between covered and partial. A keyword
  that appears in an utterance with no topic behind it is weaker — the words
  were said and the extractor never raised them to a topic — so it is *partial*
  and never covered.

  Without the second source the comparison could only ever be as good as entity
  extraction, and NER recall was silently deciding gap precision. A meeting that
  settles an owner and a deadline in plain Korean — "API 업그레이드는 한개발님이
  10월 2일까지 맡아주시고요" — yields no topic carrying the word 담당 or 기한, so
  the item came back `missing` and put a full-weight gap on the screen about
  something the meeting had done. Measured over the three labelled fixtures, the
  change cut `high`-severity gaps from 8 to 5 without losing a true one at
  `high`.

  Speech alone never covers an item, because one passing "다음에 얘기해요" would
  otherwise close an item the meeting never settled. Only consenting speech is
  read, filtered by the same join `build_topic_graph` uses — unknown consent is
  not consent, and a gap resting on a person who declined is the failure that
  matters here.

  **A question counts as having raised the subject.** "소셜 로그인 API가
  필요한가요?" makes the dependency item partial rather than missing, which
  demotes a gap somebody might have wanted at `high`. Interrogatives and
  negations are not detected, and detecting them is its own judgement rather
  than a one-liner; the fixture labels disagree with the code on exactly this
  case and it is the open question of the rule.
- **A missing item scores exactly its template weight.** There is no topic to
  read a centrality off and none to read a silence off, so the weight is the
  only measured input and the score is it. Charging it a full 1.0 for "no
  coverage" instead added the same constant to every missing item and pushed the
  whole checklist into `high` — a score that looks measured and is not.
- **A signal that cannot be measured is dropped and the weights renormalised.**
  Module E does the same with `_decision_density` when a meeting reached no
  decisions.
- **A partial finding is damped** (`AUTUNE_GAP_PARTIAL_DAMPING`). "Named but
  thin" is a weaker claim than "never came up", and the metric is precision.
- **A meeting with no topics raises no gaps at all.** Every item would be
  missing and the report would be a whole checklist about a meeting the pipeline
  failed to read. An empty graph says extraction found nothing, not that the
  meeting discussed nothing — and with NER recall on spoken Korean where "Step 1
  as built" measures it, that case happens.

**There is no rule about which job roles spoke, and that is deliberate.** #14's
headline signal is "a topic no engineer said anything on is riskier", and it is
absent rather than half-built, because **two** things are missing and the first
one arriving does not make the second appear:

- `participants.role` is written by no production code (#22). Module A creates
  every participant with the column unset.
- A topic every consenting participant was silent on **cannot occur.** The graph
  builds topics from entities found in consenting speech, so whoever said the
  utterance a topic came from is recorded as having spoken on it — the share can
  never reach 1.

An earlier draft carried a `roles` field on the template item and a
`roles_known` flag through `detect.classify`. It read only whether the field was
*empty*, so `roles: [Dev]` and `roles: [Design]` behaved identically, and the
condition it gated was the unreachable one above. A rule that looks implemented
is worse than one that is missing: it invites a template author to state
something nothing enforces, and it sends whoever fills the column later looking
for the bug in the wrong half. Raised in review of #266 by the person who will
fill it.

Participation still feeds the **risk score** as a continuous share, which is
measurable: a topic most of the room stayed silent on scores higher than one
they all spoke on.

**A re-run keeps the gaps it already raised.** They are recognised by
`(meeting_id, template_key, template_item_key)` and updated in place, so a gap's
id survives — a link somebody sent still opens it — and so does `dismissed_at`,
which is a person's judgement and the input threshold tuning reads (ADR 0006). A
row this run did not produce is deleted: the meeting covers that item now. Only
template rows are touched, so a gap found from the graph alone would be left
alone. `gap_related_topics` is rewritten every run, because `build_topic_graph`
deletes the meeting's topics first and the cascade takes those rows with them.

Every threshold and weight is in `config.py` (#35): the two severity bands, the
centrality below which a match is partial, the damping, and the three risk
weights.

### Step 8 as built

A gap carries the question that would close it, and a template item holds two
wordings for it (#35).

- **A partial finding names its topic.** "검색 개인화 기능의 성공 기준은 무엇으로
  측정합니까?" can be answered; the generic wording has to be decoded first, and
  a reader opening the report a week later no longer knows which "일" it meant.
  The topic named is `matched[0]` — the one the risk score was computed against,
  so the number and the sentence describe the same thing.
- **A missing finding keeps the generic wording.** There is no topic to name.
  Naming the meeting's most central topic instead would be a guess, and with
  step 1's recall where it is that guess is as likely to be "다음 주" as the
  thing the meeting was about — a question about the wrong subject reads worse
  than a general one. Same rule as the risk score: what was not measured is not
  substituted for.
- **`{topic}` must be followed by an invariant particle** — 의, 에, 에서, 에 대해.
  은/는, 이/가 and 을/를 change form with the last syllable of the noun before
  them, and a topic label is a noun read out of a meeting, so the right form is
  not knowable when the copy is written. The loader refuses the variable ones;
  the failure it prevents is a screen showing "캐시은".

Putting a topic label in a question is safe for the same reason it is safe as a
node: `graph.build_topics` drops any entity carrying the mask character, so no
topic label has ever contained a masked span. #250 is about what that costs in
recall, and confirms the guarantee itself holds through both extraction paths.

**What is still not built:** a question that reads the *relation* between topics
rather than naming one — "정렬 로직이 인덱스 재색인에 의존한다면, 재색인은 언제
끝납니까?" needs #32's triples.

### Step 9 as built

`GapReport` is assembled from the stored rows after their transaction commits,
then published with `autune_core.publish(GAP_COMPLETED, …)` — C names the event,
never E's task. Topics come most central first, ties in the order the meeting
reached them, each with its evidence utterance ids in meeting order;
participation is the `spoke` and `silent` id lists and nothing else. A
dismissed gap is left out: its row stays for threshold tuning, but E should not
score a meeting on a gap the team rejected.

**One person is one entry in the report**, however many voices diarization
split them into. `gap_participation` stays per participant row — the speaker
track is C's unit of analysis — and the report merges rows that share a
`user_id`, represented by the smallest of their participant ids. Having spoken
as any of them puts the person in `spoke`: recording speech as silence would
raise a gap that is a false statement about somebody. The representative is a
participant id rather than the user id so the report carries no cross-meeting
identity on its own — `participants` is a shared table every module may read,
so resolving one back to a person stays possible, and accumulating a person's
silences across meetings is a `../architecture/privacy.md` section 3 violation
on the consumer's side rather than something the id format prevents. Raised in
review of #164; it cannot happen until identification (#6) fills `user_id`,
which is why it is fixed now rather than found then.

`gaps` carries whatever `gap_gaps` holds, minus the ones somebody dismissed.
Template comparison now writes those rows (see "Steps 6 and 7 as built"), so the
list is what the meeting was held to and did not settle. It is empty for a
meeting whose graph came out empty, which is a statement about extraction rather
than about the meeting — the comparison declines to raise a checklist's worth of
gaps off a transcript nothing was read out of.

## Storage

| Store | Contents |
| --- | --- |
| PostgreSQL `gap_topics` | Topic nodes with PageRank and betweenness, per meeting |
| PostgreSQL `gap_topic_utterances` | Which utterances a topic was built from, in order |
| PostgreSQL `gap_topic_edges` | Relations between topics, directed, per meeting, with which extractor asserted each |
| PostgreSQL `gap_participation` | Topic × participant speech presence |
| PostgreSQL `gap_gaps` | Detected gaps, category, coverage, severity, risk score, question |
| PostgreSQL `gap_related_topics` | Which topics a gap was inferred from |
| PostgreSQL `gap_meeting_template` | Which template one meeting is compared against, when somebody chose one |
| PostgreSQL `gap_templates` | Domain templates and their items — **not built, and not needed**, see below |

Everything that exists cascades from `meetings.id`, so no deletion hook is
needed.

`gap_topic_utterances` and `gap_related_topics` are link tables rather than
JSONB lists on their parents. The report joins both back — to `utterances` for
the quotation behind a topic, to `gap_topics` for why a gap was raised — and
`../architecture/data-model.md` rules JSONB out for anything you join on. They
store ids and never the text: a copy of an utterance here would leave transcript
content behind a cascade that no longer reaches it.

`gap_topics` keeps PageRank and betweenness in separate columns rather than one
blended score. Risk scoring weights them differently, and a single number could
not be re-weighted afterwards without rebuilding the graph.

`gap_gaps.dismissed_at` marks a false positive without hiding the row —
threshold tuning has to read what was dismissed, and soft deletes are forbidden
(`../architecture/data-model.md`). No dismisser is recorded: which teammate
pressed the button is not something tuning needs, and storing it would be a
per-person record of conduct that ADR 0003 refuses.

### Templates are files, so `gap_templates` never had to be built

A domain template is reference data — it is not derived from any meeting, so it
is the one thing this module owns that cannot cascade from `meetings.id`. What a
table would *have* hung off, a team or nothing at all, follows from who writes
templates and how many there are, which is issue #22. Creating it meant guessing
an anchor and migrating away from it later.

So the templates live in the package instead —
`modules/gap/src/autune_gap/templates/*.yaml` — and the question does not arise.
Git holds their history, an edit goes through PR review, which is the right
control for content that decides gap precision, and nothing has to be anchored
anywhere. `gap_templates` gets built when a team writes its own template
(Phase 2): a real user flow names the anchor then.

What is stored per meeting is only the **exception**. `gap_meeting_template`
holds one row for a meeting somebody pointed at a non-default template, so
changing `AUTUNE_GAP_DEFAULT_TEMPLATE` reaches every meeting that never
expressed a preference. It cascades from `meetings.id` like everything else
here, so the no-deletion-hook sentence above still holds.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/reports/{meeting_id}` | Full gap report |
| GET | `/topics/{meeting_id}` | Topic graph for visualization |
| POST | `/gaps/{id}/dismiss` | Mark a gap as a false positive (feeds threshold tuning) |
| GET | `/templates` | Available domain templates |
| GET | `/templates/{meeting_id}` | Which template this meeting is held to, and how far it got with each item |
| PUT | `/templates/{meeting_id}` | Point this meeting at a template and re-compare |

### The read API as built

Everything above is built except `POST /gaps/{id}/dismiss`.
`/reports/{meeting_id}` and `/topics/{meeting_id}` read the stored rows; nothing
was added to `apps/` to mount them.

- **The report is read, not replayed.** It is assembled from `gap_*` rows by the
  same `service.build_report` the publish path uses, so a report reopened a week
  later shows the dismissals made since, and E and the screen never disagree
  about what the meeting produced.
- **An unanalysed meeting answers empty, and only an unknown id is a 404.** A
  screen polling while the pipeline runs has to tell those apart. Module B draws
  the same line on `/results/{meeting_id}`.
- **The topic graph is not a contract.** `schemas.TopicGraphRead` is this
  module's own shape: it carries `betweenness`, which `autune_contracts.Topic`
  does not, and E neither calls an endpoint nor draws a graph. A visualization
  shape in `packages/contracts` would be four modules' business for no reason.
- **Nodes come in the report's order** — most central first, ties to the topic
  the meeting reached first — so S20 can show the picture beside the list
  without reconciling two orderings. Edges come strongest first, ties by where
  their endpoints sit in that order, never by `gap_topic_edges.id`: that is an
  autoincrement a re-run reassigns, and the same graph would redraw differently
  every time the meeting was reprocessed.
- **Both directions of a co-occurrence edge come back.** Collapsing the pair
  here would assert that `co_occurs` is undirected, and #32 replaces it with
  triples where the same collapse loses which topic acted on which. A renderer
  that wants one line per pair drops the direction it does not need.
- **The graph carries no participation matrix.** Who spoke is in the report,
  keyed by topic id. A node is the one place a per-person number could arrive
  attached to a picture, and the report is already read along a topic rather
  than along a person (see "Privacy notes").

The three template routes are built. `GET /templates` lists what a meeting can
be held to — key, name, version and item count, and deliberately not the items:
choosing a template is choosing a name, and shipping every checklist to a screen
that shows one of them is a payload nobody reads. `GET /templates/{meeting_id}`
answers with the configured default rather than an empty body when nobody has
chosen, because there is always a template in force and a rail showing nothing
selected would misreport that.

`GET /templates/{meeting_id}` also carries the comparison itself — every item of
the template beside `covered`, `partial` or `missing`, and the id of the gap it
raised. That is the rail on the right of S20, and it is the gap list read from
the other end: the list says what is missing, the rail says what the missing
items were measured against, which is what makes a gap a claim rather than an
opinion. The response is a superset of the selection, so a caller that only
wanted the key still reads it off the same field; `PUT` keeps taking and
returning the selection alone, because a request body carrying a read-only
comparison invites a caller to send one back.

Two things keep it honest, and both are failures it would otherwise make
silently:

- **Coverage is stored, not recomputed.** `gap_gaps.coverage` records what
  `detect.classify` decided when the pipeline ran. A rail that re-ran the
  comparison at read time would disagree with the gap rows beside it the moment
  `AUTUNE_GAP_PARTIAL_CENTRALITY` moved — and the rows are what E was published
  and what a dismissal was made against. The report is read, not replayed; so is
  the checklist behind it. `covered` is never stored, because a covered item
  raises no gap: the server reads the *absence* of a row back as covered.
- **An unanalysed meeting reports no coverage at all.** `analysed` is false and
  every item's coverage is null. Without it, "no gap row" would read as
  "covered" for a meeting nobody has processed — a full checklist of green dots
  for a meeting the pipeline never reached, which is the same false statement
  `compare` refuses to make when it declines to raise a checklist of gaps
  against an empty graph.

A dismissed item keeps its coverage and is marked dismissed rather than promoted
to covered. Somebody pressing "해당 없음" is a judgement about the gap, not
evidence the meeting covered the item, and the row is what threshold tuning
reads.

`PUT /templates/{meeting_id}` stores the choice **and re-runs detection**, so the
gaps on `/reports/{meeting_id}` reflect the new template as soon as it returns —
the alternative is a control that appears to do nothing until the meeting is
reprocessed. It does not republish `autune.gap.completed`: E scores the meeting
the pipeline produced, and a template somebody is trying out on S20 should not
silently rewrite that. A key no template file defines is a 422, not a 404 — what
is wrong is the value, not the address.

`POST /gaps/{id}/dismiss` is still not built. It now has rows to act on, and
what it needs is the screen that calls it (#48).

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.gap.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |

## Slack surface

- Gap report thread in the meeting channel, `high` severity only by default
- Generated question cards teams can act on

## AI stack

| Component | Model or algorithm |
| --- | --- |
| Entity extraction | spaCy NER (Korean model) |
| Relation extraction | Rule-based patterns plus LLM assistance |
| Graph | NetworkX, in memory |
| Topic importance | PageRank, betweenness centrality |
| Risk scoring | Weighted heuristic; thresholds in `config.py` |

### Model abstraction layer

Every model sits behind a Protocol in `autune_gap.pipeline.base`, and the
implementation is chosen by `AUTUNE_GAP_*_IMPL` and reached through
`registry`. Nothing outside that package names a model class, so swapping a
model does not touch a caller. Modules B and D settled on the same shape.

`FakeNer` is what the tests run. It is deterministic, needs no weights and no
network, and it exists so the topic graph, the centrality pass and the
participation matrix can be built and tested before the real extractor lands
(#13). It is **not** an approximation of the model's accuracy and must not be
used to estimate it.

There is no `external` implementation and adding one is a privacy decision
rather than a config string — see `../engineering/environments.md`, "The entity
extractor has no external option".

Every row a topic produces records `extractor_version` — the pipeline name and
its version, `ko_core_news_lg-3.8.0`. The name alone is not a version: the
pipeline ships a new release with every spaCy minor, so a graph built with 3.7
and one built with 3.8 would carry the same string. Gap precision is measured
over time and dismissals feed threshold tuning; both read across model
versions, so a row that cannot name its extractor takes part in neither. The
version comes from the pipeline's own `meta`, and the wheel is pinned in the
`local-models` extra so `uv.lock` decides it rather than the day somebody ran
`spacy download`.

### `ko_core_news_lg` is CC BY-SA 4.0

The pipeline and both of its annotated sources — UD Korean Kaist v2.8 and
KLUE v1.1.0 — are CC BY-SA 4.0. (Its vectors are CC0.)

Running it inside our own infrastructure is unencumbered. **ShareAlike bites if
a model derived from it is distributed**, which is the shape #13 takes: a
pipeline fine-tuned from these weights, published or shipped to a customer,
carries the same licence onward. Read the meta before assuming otherwise:

```bash
uv run --package autune-gap --extra local-models python -c \
  "import spacy; print(spacy.load('ko_core_news_lg').meta['license'])"
```

The same question module B has open for its classifier checkpoint and AMI
(#112). If #13 trains from a differently licensed base instead, this note is
what says why that mattered.

Entities are normalised onto six labels — `feature`, `system`, `metric`,
`person`, `date`, `term` — rather than spaCy's own inventory. A model trained on
news text emits `ORG` and `LOC`, and a meeting about search ranking has no
organisations in it worth graphing. `feature` and `system` have no spaCy
equivalent at all: they are this product's vocabulary, so a general model
cannot supply them and #13 is where they come from. `term` is what the graph
uses in the meantime — the compound noun without the judgement about which of
the two it is — and the mapping in `pipeline.ner` still shows the limit rather
than hiding it behind an empty class. See "Step 1 as built".

There is deliberately **no `worker_process_init` warm-up hook**. `apps/worker`
imports every module's `tasks.py` into one Celery app, so a hook registered
here would run in every worker process regardless of `-Q` — including workers
that never run a gap task. Module D shipped one and had to gate it behind a
setting (PR #90). The cost of not having one is that the first task of a
process pays the model load.

## Metric

Gap detection precision — 0.70+ at six weeks, 0.82+ at three months.

Precision, not recall: a false gap costs user trust, a missed gap costs
nothing they did not already have. Only `high` severity is shown by default,
and dismissals feed threshold tuning.

```bash
uv run --package autune-gap python -m autune_gap.eval
```

Precision is measured over the `high` band, because that is what a reader
actually sees — a `medium` false positive is not a false statement to anybody
until something surfaces it. The all-severity figure is printed beside it; the
two moving apart means the bands are doing the work rather than the comparison.
Recall is printed and is not a target.

Each false positive is also attributed to one of four causes, because the
headline says the pipeline is overshooting and only the split says where to go:
`partial` (the centrality threshold), `extraction` (the meeting said a noun the
item's keywords do match and step 1 never turned it into a topic), `keyword`
(the topic is in the graph and the keywords do not name it), and `no-noun` (the
meeting settled the item with a verb or a date and said no noun that could name
it). The last one is counted apart from the other three: matching keywords
against topic labels is lexical and what settled the item is grammatical, so
neither a keyword list nor a better extractor reaches it. The report prints how
many of the false positives are reachable from this module at all.

Attribution reads the case's hand-labeled `evidence` — the nouns a reader would
point at as settling each item — against the topic labels the run produced. "In
the graph" means a label contains the whole expected term, deliberately not
`detect.match`'s containment-either-way: a label carrying half the noun is a
step-1 truncation, and reading it as a match sends somebody to widen a keyword
list over an extraction bug.

**Nothing is scored against a number nobody measured.** Precision over a run
that raised no gaps is reported as "not measured", not as 0.0 or 1.0 — both
would be a claim about a pipeline that said nothing. A case that labels no
`evidence` has its false positives reported as `unclassified` rather than
guessed into a cause. Cases whose graph came out
empty are listed separately for the same reason: `detect.compare` raises nothing
for them on purpose, so they pull recall down for a reason that belongs to step
1.

**Point it at a disposable database.** The harness creates a team per case and
deletes it when the case is scored, so every synthetic row it writes reaches
deletion through `meetings.id` — but it writes to whatever
`AUTUNE_DATABASE_URL` names, which on a shared development database is somebody
else's. The docker-compose database in
`../engineering/environments.md` is the intended target.

The split between `missing` and `partial` is read off `gap_related_topics`, and
that reading has a failure mode shaped exactly like a result: an empty link
table says "every gap is missing". The harness cross-checks it against the
stored `gap_gaps.title`, which `detect` composes from the coverage state, and
stops with exit 2 if the two disagree rather than printing a cause split built
on one of them.

The committed set (`eval/fixtures/gap_detection_v1.json`) is **four authored
meetings, and is not the PRD figure** — that one comes from five to ten real
team meetings in W5, and four cases cannot carry a statistical claim. It is a
regression gate: a template keyword that starts matching everything, or a
threshold that moves a band, fails it visibly. The set is closed-world (every
template item is labeled settled or genuinely missing) and the loader refuses a
case where it is not, because otherwise a precision figure measures the
labeler's diligence rather than the pipeline. No real meeting content is
committed.

## Privacy notes

- The participation matrix records **whether** a participant spoke on a topic,
  not how much. It is topic coverage, not speech volume — do not let it drift
  into a per-person talk-time metric. See `../architecture/privacy.md` section 3.
  `gap_participation.spoke` is a boolean and a test asserts the whole column set
  so it stays one.
- **The boolean is not the whole guarantee — how the report reads it matters.**
  Summing the matrix *along a person* ("spoke on 1 of 12 topics") rebuilds the
  speaking-ratio metric the column shape was chosen to prevent, out of data that
  is individually harmless. The report reads it along a *topic* instead
  ("검색 랭킹 — 백엔드 쪽 발언 없음"), which is what a gap is and what the whole
  team may see. Raised in review of #134; the surface it constrains is #36.
- Topic labels derived from transcript text are already masked upstream. Do not
  re-derive anything from an unmasked source; there is not one.

## The topic graph is per meeting

Decided 2026-09-08 (issue #23): C builds a fresh subgraph for each meeting and
does not accumulate across meetings.

Nothing C produces needs accumulation. Centrality answers "which topics carried
*this* meeting", the participation matrix and template comparison are
within-meeting by definition, and risk scoring feeds on all three. C's metric is
gap detection precision, which accumulation does not help.

What it avoids:

- **Two mechanisms answering the same question.** D already matches topics
  across meetings with SBERT, BM25 and cross-encoder re-ranking. A second,
  graph-based matcher would disagree with it, and users would see the
  contradiction.
- **Ambiguous centrality.** A topic central to today's meeting and a topic
  central to the quarter are different things; an accumulated PageRank measures
  the second while the gap report needs the first.
- **Surgical deletion.** Retention has to remove one meeting's data
  (`../architecture/privacy.md` section 4). Dropping a per-meeting subgraph is
  trivial; unpicking one meeting's contribution from a shared graph, where edges
  may be shared, is error-prone.

The dashboard's topic-recurrence figure (S26) does not need it either: D's
`topic_links` already say a topic came up before, so E counts those.

## Open questions

- Whether `participants.role` will be filled by identification (#6) from
  `team_members.role`, or by some other path. Nothing writes it today, so the
  participation half of the partial-coverage rule is inert — see "Steps 6 and 7
  as built". Asked of module A in #22.
- Whether a meeting with low consent coverage may be told it did not discuss
  something (#248). Detection today compares the topics of whoever consented
  against the whole checklist, and says nothing about how much of the meeting
  that was.
