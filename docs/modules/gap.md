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
   rules, and — opt-in — an LLM for the pairs the rules decline to read.
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
  which calls 개인화로 an adverb. A noun carrying a particle joins the run
  **as its stem** and ends it — 로직은 joins as 로직, so "정렬 로직은" is
  정렬 로직 and the label is 개인화 and not 개인화로; an ending, the copula or
  a stopword breaks the run. Content noun means common,
  proper and foreign (`nc*`, `nq`, `f`) and **not** pronoun, numeral or bound
  noun: matching joins a run rather than breaking it, so letting 그거 or 두 in
  would give a node called 그거 검색 기능 and split the topic the meeting calls
  검색 기능 everywhere else. The tag rule is not enough on its own — this model
  tags 그거 as a common noun — so the demonstratives sit in the stoplist,
  measured rather than assumed.
- **The particle is cut on the surface, not along the model's morphemes.**
  Until #278 a noun with a particle on it was refused outright, and Korean puts
  a particle on nearly every noun that is not the first half of a compound: "가장
  큰 리스크는 콜드스타트입니다" gave no topic at all, and 정렬 로직은 came back
  as 정렬. `spoken.noun_stem` now reads through it. The tag decides *whether* a
  particle is attached and how many (noun parts, then only `j*` parts); the
  standard particle inventory decides *what* is cut off the end of the word.
  `lemma_` would have been the obvious source and is wrong on this vocabulary —
  it splits 개인화로 as 개인 + 화로 and 콜드스타트입니다 as 콜드 + 스타트입니다.
  Stacked particles are cut one at a time, no more times than the tag has `j`
  morphemes: 서버에서부터 (`jca+jxc`) and 서버와는 (`jct+jxt`) are 서버, and
  결과도 (`jxc`) stays 결과. A stack the model tags as one `j` (모듈까지도) is
  on the list whole. A cut is only taken where the particle's spelling can
  follow the syllable before it — 차이랑 is 차이 + 랑, not 차 + 이랑. The list
  is load-bearing: an ending missing from it is cut wherever a shorter listed
  ending matches, which is how the first version turned 계획대로 into 계획대
  (#345). Four things are deliberately not read through: the copula (`jp`),
  because 붙입니다 is tagged `ncn+jp+etm` and would give a topic called 붙; an
  ending a particle and a noun's own last syllable spell alike when the tag
  gives it one `j` — 경로는 and 개인화로는, 성과도 and 서버와도, 인프라도 and
  캐시라도 — where either cut invents a topic on the other half, except for a
  short measured list of nouns (경로, 결과, 불만, …) kept by name; a stem a
  particle is still on (서버에서), which means the list missed a stack; and the
  two nouns the model splits before their last syllable, 재시도 and 난이도,
  which containment matching would otherwise count as a template's 재시도
  covered by
  a topic called 재시. Reading through the particle surfaced words it used to
  refuse by accident, so 회의, 회의실 and the positional bound nouns the model
  tags `ncn` (중, 쪽, 안) joined the stoplist in the same change.
- **A span the model found is claimed whether or not we keep it.** An
  implausible one-letter person, an `LC` meeting room, an `OG` vendor: the
  characters are spoken for, so a refusal cannot come back as a term under
  another name (강남 회의실, 카카오 API 연동). One character belongs to at most
  one thing, the rule `FakeNer` already follows. Whitespace tokens are skipped
  rather than passed through, or a double space — which ASR output carries —
  would split a compound the same meeting says as one topic elsewhere. Both
  raised in review of #222.
- **What module A masked is claimed too.** A masked value is not a topic and
  `graph.build_topics` has always refused one, but that check fires on the
  whole run the mask ended up in, so 고객 연락처 010-****-5678 확인 lost
  고객 연락처 along with the number. Whether it did was decided by whether a
  particle happened to sit between the noun and the mask — 연락처는
  010-****-5678 kept its topic, 연락처 010-****-5678 did not — which is a
  property of how somebody spoke, not of what the meeting covered. Since C
  reports on what is *absent*, each loss is a gap raised about something that
  was discussed. `spoken.masked_spans` claims the chunk so the run breaks on it
  instead, in both phrasings. The `MASK_CHAR` it looks for is imported from
  `autune_integrations.privacy`, the masker's own: a second copy of the
  character here is a guard that stops matching when the notation changes and
  says nothing about it. #250.
- **Three precision filters.** A one-character `person` is not a person: A/B
  결과 gives A and B as `PS`, and both became connected nodes. A `metric` with
  no digit in it is not a metric: `QT` on spoken Korean fires on 한번, 네, 좀.
  And a stopword is not a topic on either path. Precision is C's metric and a
  false topic is what a false gap is raised on.
- **A person is named without the honorific.** The model reads 이건우님이
  as one `npp+jcs` token and one `PS` span, and 이 is an ending `noun_stem`
  will not cut on a noun (차이, 아이). After 님 it cannot be anything else, so
  `spoken.person_name` cuts 님/씨 and the particle after it. Before this one
  colleague was 이건우님이, 이건우님 and 이건우 — three topics.
- **A date or a quantity is read by its ending, and a bare one is refused**
  (#315). An entity span ending in the copula kept it — 0건입니다, 90일이고 —
  because `noun_stem` does not read through `jp`; and 다음 주 금요일까지 kept
  its 까지 whenever the model tagged it as a noun. `spoken.quantity_text` cuts
  the copula and a particle from a `date` or `metric` span when what is left
  ends in a number, a unit or a day. What is left, if it is only a number and
  one unit (15%, 30초, 0건, 90일), is not a topic: the noun beside it is. 응답
  3초, 다음 주 금요일 and 10월 1일 stay. 의미 joined `STOP_TERMS`, the most
  central node of `deploy-retro`; 진행 and 공유 did not, because 진행 is a
  `next_step` keyword and 공유 is the only topic of a status meeting.

  | Labels over the two shared fixtures and the four authored meetings | before | after |
  | --- | --- | --- |
  | topics | 57 | 52 |
  | with a copula or particle on the end | 0건입니다 · 90일이고 · 다음 주 화요일까지 · 다음 주 금요일까지 | none |
  | a bare quantity | 15% · 30초 | none |

  Precision and recall on the authored set are unchanged in every
  configuration, measured before and after on the same run.
- **Both paths cut the particle and ask the same stoplist.** 오늘은 used to be
  a `DT` node, particle and all, while 오늘 was refused as a term — one word,
  two answers (#230). An entity span now loses the particle on its last word
  by the same `noun_stem`, and the stoplist is asked about the whole span:
  오늘은 is 오늘 and is refused, and 다음 주 화요일까지, a deadline the meeting
  set, survives because the span is not 다음. The noun runs ask token by token
  instead, so 오늘 배포 keeps 배포 — a span-level check would let 오늘 into the
  label. Both halves of this closed with #278.

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
written and measured before anything is sent anywhere. What the rules cannot
read is a named list below rather than a shrug, and that list is exactly what
the opt-in assisted implementation asks about ("Relation assistance", below).
By default the share is 100%: nothing is sent.

**Four relations** are in the vocabulary, each one a thing risk scoring (#35)
should treat differently: `depends_on`, `blocked_by`, `part_of`,
`alternative_to`. **Three of them have a rule.** Every rule keys on a **marker**
the speaker actually said, and fires only with two topics around it in one
utterance. Proximity alone stays `co_occurs`, which the graph writes without
asking the extractor.

| Relation | Marker | Reads |
| --- | --- | --- |
| `depends_on` | 필요, 있어야, 되어야, 선행, 전제, 없이는, 없으면; and 끝나야, 끝내야, 나와야, 마쳐야 **only when another clause follows** | "정렬 로직은 인덱스가 필요합니다", "인덱스 재색인이 먼저 **끝나야** 정렬 로직을 붙일 수 있습니다" |
| `blocked_by` | a blocker word (안 잡, 미정, 막혀, 무리, 이슈, …) **and** a causal connective in the same clause, on either side of it | "실시간은 콜드스타트가 **안 잡혀 있어서** 무리입니다", "검색 기능은 캐시 **때문에** 막혀 있습니다" |
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
relation — `실시간 blocked_by 콜드스타트 처리` — against its co-occurrence edges.
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

**A reason can be denied, and a window can hold two of them.** Two guards the
backward reading needed and did not have, both found by running it (raised in
review of #254):

- **`때문이 아니라`** names a reason in order to refuse it. Without a guard
  "검색 기능은 캐시 때문이 아니라 그냥 막혀 있습니다" asserted
  `검색 기능 blocked_by 캐시` — the reverse of the sentence. `아니` is read
  between the connective and the cue and nowhere else: `_ALTERNATIVES` reads
  `아니라` as a contrast marker, and that reading is still the right one for
  "A가 아니라 B". A reason stated after the denied one is still found.
- **Two reasons in one window.** "캐시 처리 때문에 인증 탓에 막혀 있습니다"
  states two, and the resolution check was reading the whole window, so 처리
  from the first cancelled the second and 인증 — the blocker actually standing
  — was dropped. The check now sees only what its own connective heads.

**What the reason guard costs.** `_RESOLVED` refuses a blocker whose reason
clause says the thing is gone — "캐시 이슈가 해결됐기 때문에 …" asserts the
reverse of a blocker, and `blocked_by` is the one relation the report treats as
a finding on its own. 처리 is on that list and is also the ordinary noun for
the work, so "캐시 처리 때문에 막혀 있습니다" is refused too, and the relation
the meeting did state is lost. The ambiguity was already here in the forward
direction; reading backwards means it now costs a relation in two places. Kept
in the losing direction because precision is C's metric, and pinned by
`test_a_resolution_word_used_as_a_noun_costs_the_relation` so it is a known
price rather than a surprise. Telling the two readings apart is the assisted
implementation's job, not a longer list.

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

  **The clause has two sides, and at first only one was read.** `어서`/`아서`/
  `라서` are verb endings and attach to the predicate, so they follow the
  blocker word; `때문`/`탓에`/`으로 인해` head the reason and Korean puts the
  reason first, so they precede it. Looking only forwards from the cue read
  three of the six connectives and dropped every relation a meeting stated the
  other way — "검색 기능은 캐시 때문에 막혀 있습니다" asserted nothing. The
  window now runs backwards as well, bounded by the same clause break and the
  same `MAX_MARKER_DISTANCE`, and when the connective is the one behind, it
  rather than the cue is what the ends are read from: in "캐시 때문에 정렬
  로직이 막혀 있습니다" the mention before the *cue* is 정렬 로직, the thing
  being blocked. Recall only — no false edge was produced by the narrow
  window. #254, follow-up to #249.

  **Only three of the six run backwards.** `어서`/`아서`/`라서` are verb endings
  that close the clause they sit in, and the clause-break list has no entry for
  them, so a backward window that looked for all six read straight past one:
  "결제 모듈은 시간이 없어서 로그인 모듈 이슈는 못 봤습니다" paired the blocker
  word with the previous clause's reason and asserted `로그인 모듈 blocked_by
  결제 모듈`. The backward window takes `때문`/`탓에`/`으로 인해` only.
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

**A finish is a condition only when something follows it.** "인덱스 재색인이
먼저 끝나야 정렬 로직을 붙일 수 있습니다" is the commonest way a meeting says one
piece of work waits on another, and none of the plain need words is in it —
gap_detection_v1's search-personalisation case lost its dependency to exactly
this. The same ending also closes an obligation: "정렬 로직이 금요일까지
끝나야 합니다" is a deadline, and read as a need it would assert that 정렬 로직
depends on whatever was named before it. So 끝나야, 끝내야, 나와야 and 마쳐야
count only when a clause follows; 합니다, 해요, 돼요, 겠-, 할 and the end of the
utterance refuse them. 있어야 and 되어야 take no such guard, because "캐시가
있어야 합니다" is a need either way — what is obliged there is the thing
existing, not a date somebody promised. `rules-3`.

**A date or a quantity is never an end.** Entities labelled `date` or `metric`
are left out of the names the rules search for. They sit exactly where the
thing needed is looked for — "인덱스가 금요일까지 있어야 정렬 로직을 붙입니다"
read as `정렬 로직 depends_on 금요일까지` — and neither is a thing another thing
waits on. They are still topics; only the relation step stops seeing them.
`rules-3`.

With #456 reading `depends_on` as evidence for the dependency item, the two
together close search-personalisation's dependency false positive:
gap_detection_v1 `high` precision 0.842 → 0.889, recall 1.0.

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
- **The LLM path is opt-in.** `AUTUNE_GAP_RELATION_IMPL=gemini`, below. Off,
  nothing here reaches past the rules.

`gap_topic_edges.extractor_version` records which extractor asserted an edge,
and is NULL exactly when none did — that is the `co_occurs` row. Under relation
assistance it names the half that asserted it: `rules-3` or `gemini:<model>`.

### Relation assistance

`AUTUNE_GAP_RELATION_IMPL=gemini` (off by default) runs the marker rules, then
asks Gemini about the pairs they decline. It is the LLM assistance #32 asked
for, kept to the cases this section already names as the rules' limits
(`pipeline/relation_assist.py`, `relations.hard_pairs`).

**What is asked.** Adjacent mentions in one utterance, no further apart than
`MAX_MARKER_DISTANCE`, that the rules did not type in either direction, with
one of these between them:

| Shape | Why the rules decline it |
| --- | --- |
| `는데` / `지만` | A contrast as often as not, and sentence glue the rest of the time |
| nothing but `의` | Composition or possession; no `part_of` rule |
| a causal connective, in an utterance with a blocker word and a resolution word | "캐시 처리 때문에 막혀" — 처리 names the work, and `_resolved` reads it as the blocker being gone |

**What may come back.** Only a pair the line offered, in either direction, with
one of the four `RELATION_LABELS`. Anything else in the answer is dropped. The
rules' relations stand and are listed first, so a triple both found is
attributed to the rule; the model cannot remove or replace one. A symmetric
answer is written both ways, as the rules write theirs.

**When it cannot answer, the rules stand.** A failed request, an unparseable
answer, a line too long to send, or one past
`AUTUNE_GAP_RELATION_ASSIST_MAX_UTTERANCES` keeps the rules-only result for
that utterance. `PrivacyViolationError` from `check_outbound` is raised, not
answered, for the reason the verifier gives (review of #484).

**What leaves.** Per request, under the 4,000-character outbound cap and
through `autune_integrations.HttpClient`: the fixed instruction text, and each
asked utterance on its own numbered line as module A stored it, with the
mentions of its pairs lettered beside it. The mentions are substrings of the
line. **Names and numbers read out as words are not masked**, as with the
verifier. No speaker, time, meeting or utterance id, and no neighbouring line.
It shares the verifier's provider settings (`AUTUNE_GAP_VERIFIER_API_KEY`,
`_MODEL`, `_FALLBACK_MODEL`, `_BASE_URL`, `_TIMEOUT_SEC`) and its standing:
opt-in, never the default, dummy meetings only until the team decides. That
decision is #392, open. Both of C's callers send names unmasked until then. #500
replaces a team's names before module B's classifier sends; once that moves to
`packages/integrations`, the verifier and this path take it too. Raised in
review of #499.

**The model answers with names, not letters.** Asked to answer with the
letters, `gemini-3.5-flash` and `gemini-3.8-flash` both wrote the topic names
instead, and a letters-only parser dropped every answer — indistinguishable
from a model that found nothing. A name is accepted when it is one of that
line's own mentions, so this widens nothing the letters did not offer.

**Nor always with bare line numbers.** The template verifier's answers came
back keyed `"발화 1"`, the label the request shows, and a digits-only key
reader dropped every one. Both callers now read keys through
`gemini.answers_by_line`, which takes a key for its number and refuses an
answer that names no line asked, so the batch falls back rather than reading
as "nothing stated".

**Measured** on 2026-09-30, one request per model:

- **The authored eval set sends nothing.** Over `gap_detection_v1` with spaCy,
  no utterance holds a hard pair, so `gemini` asks nothing and the graph is
  the rules' graph. The set cannot say whether assistance helps gap precision;
  the W5 meetings can.
- **Eight probes, each a case this section names**, three with a relation
  and five without. Both models got all eight: `alternative_to` across
  "합의했는데", `정렬 로직 part_of 검색` from "검색의 정렬 로직", and
  `정렬 로직 blocked_by 캐시` from "캐시 처리 때문에"; nothing for plain `는데`
  glue, a `지만` that contrasts qualities rather than options, "검색 기능의
  담당자 일정", or "이슈가 해결되어서". The probes were written alongside the
  prompt, so 8/8 says the mechanism works on the cases it was built for and
  nothing about real meetings.

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
  replaces it is then a change with a number attached rather than a better idea.
- **A stated relation matches too.** An item may name step-2 relations
  (`relations` in the template file), and a topic at either end of such an edge
  in `gap_topic_edges` matches the item the way a keyword hit does — ranked by
  centrality, so it can cover the item or leave it partial. `general`'s
  `dependency` names `depends_on` and `blocked_by` (version 3). A dependency is
  how two things stand to each other, and no topic label says it: "마이그레이션
  검증 스크립트가 먼저 있어야 롤백 절차가 의미가 있습니다" already came out of
  step 2 as `롤백 절차 depends_on 마이그레이션 검증 스크립트`, and the item
  was still reported missing because neither label contains 의존 or 선행. On
  gap_detection_v1 the change closed that false positive — `high` precision
  0.800 → 0.842, recall unchanged at 1.0. `co_occurs` is refused at load: two
  topics said together say nothing about how they relate. The two dependency
  false positives left are a marker the rules do not know yet ("먼저 끝나야")
  and a sentence with only one topic in it, which no relation can reach.
- **Embeddings over the topic labels were measured and not built.** KURE-v1
  between each item's display name and each topic label, over the same set:
  of the five settled items keyword matching missed, it placed no correct topic
  nearest to any — they were settled with a verb or by a relation, and the
  nearest label was an unrelated one ("성공 기준" for `dependency`). It also
  scored real gaps above true matches (`ownership` against "보관 기간" at
  0.546; `performance` against "응답 시간", a true match, at 0.500), so no
  floor separates them. A label match can cover an item, which makes every
  such error a real gap hidden. Reading the *speech* by meaning is a different
  mechanism with a different ceiling, and is where sentence embeddings go.
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
- **The speech can also be read by meaning** (`AUTUNE_GAP_EMBEDDER_IMPL=local`,
  off by default). See "Speech read by meaning" below.
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
which is a person's judgement and the input threshold tuning reads (see Storage). A
row this run did not produce is deleted: the meeting covers that item now. Only
template rows are touched, so a gap found from the graph alone would be left
alone. `gap_related_topics` is rewritten every run, because `build_topic_graph`
deletes the meeting's topics first and the cascade takes those rows with them.

Every threshold and weight is in `config.py` (#35): the two severity bands, the
centrality below which a match is partial, the damping, and the three risk
weights.

### Speech read by meaning

A keyword is a noun, and the eval set's false positives were all `no-noun`: the
meeting settled the item with a verb and a date — "정렬 로직은 이건우님이 맡고
다음 주 금요일까지 초안을 봅니다" — and said no noun a keyword list or a better
extractor could reach. `autune_gap.semantic` reads the same consenting speech a
second way, through a sentence embedder (KURE-v1, in process, the model B and D
already run).

- **Every item carries example sentences** (`examples` in the template files) —
  what settling it sounds like in a meeting. An utterance is compared against
  every item's examples and against `semantic.BACKGROUND`, sentences that
  settle nothing ("네 좋습니다", "오늘은 진행 상황만 공유드릴게요"), and counts
  for the class it is nearest to if that is an item and the cosine reaches
  `AUTUNE_GAP_SEMANTIC_FLOOR`.
- **Nearest class, because a cutoff does not separate.** Item names and
  example sentences were both measured against the eval set with a per-item
  cutoff first, and the distributions overlapped: "네 알겠습니다. 그럼 여기서
  마치겠습니다" is 0.65 from "그 작업은 제가 맡겠습니다" on the shared ending
  alone, above the 0.61 of the utterance that really did settle an owner in
  `search-personalisation`. Against the background class the same sentence
  lands at 0.86 and counts for nothing.
- **A heard item is exactly as strong as a spoken keyword.** It makes a missing
  item *partial*, never covered, and `detect.classify`'s ranking is unchanged.
  So the change closes a `no-noun` false positive by moving it below `high`
  rather than by claiming the item was settled — the row stays, a reader who
  opens `medium` sees it, and precision over every severity does not move.
- **Examples are never taken from the eval set**, and a test compares the two.
  The harness would otherwise be grading its own answer key.
- **Nothing leaves the process and nothing is stored.** The embedder is
  `local` or `fake`; there is no external option, for the reason the entity
  extractor has none. The vectors decide which items were said and are dropped.

`python -m autune_gap.eval --compare` runs the set twice, embedder off and then
on, and prints what became of every baseline false positive. On the four
authored cases, with spaCy:

| | off | local |
| --- | --- | --- |
| precision (`high`) | 0.80 | 0.88 |
| recall (`high`) | 1.00 | 0.94 |
| false positives (all `no-noun`) | 4 | 2 |
| true positives | 16 | 15 |

Closed: `deploy-retro:ownership` and `outbound-privacy:dependency`. Still
raised: `search-personalisation:dependency` — "인덱스 재색인이 먼저 끝나야
정렬 로직을 붙일 수 있습니다" reaches only 0.51 with the dependency examples,
under the floor — and `deploy-retro:dependency`, whose utterance is nearer the
`risk` examples, and one utterance counts for one item. Lost: `search-personalisation:cold_start`, a real gap, because "인기순
정렬 대신 실시간 개인화로 가는 거죠" is nearest the cold-start examples.

**Off by default, and that is the finding rather than caution.** The floor
(0.55) and margin (0) were chosen by looking at these four meetings, so the
table says the mechanism does what it claims on them and nothing about whether
it holds. The W5 meetings decide; `--compare` is the command to run on them.
LLM verification of what the embedder heard is the next section, and a
privacy decision of its own (`../architecture/privacy.md` section 6).

### Verifying what the embedder was unsure of

`AUTUNE_GAP_VERIFIER_IMPL=gemini` (off by default, and needing the embedder on)
adds one step between the embedding and `detect`. It does not re-judge the
meeting; it asks about the utterances the embedding could not decide.

**Triage** (`autune_gap.verification`) files every utterance by its ranking:

| | when | what happens |
| --- | --- | --- |
| confident | an item wins with score >= `VERIFY_CONFIDENT_SCORE` and lead >= `VERIFY_CONFIDENT_LEAD` | heard, nothing asked |
| ignored | the background class wins by >= `VERIFY_CONFIDENT_LEAD`, or no item reaches `VERIFY_CANDIDATE_SCORE` | not heard, nothing asked |
| ambiguous | anything else | asked, with the `VERIFY_CANDIDATES` nearest items at or above `VERIFY_CANDIDATE_SCORE` |

**The verifier checks candidates and nothing else.** For each ambiguous
utterance it sees the utterance and its lettered candidates — each item's name,
question and `VERIFY_EXAMPLES` example sentences — and answers which letters
the utterance actually discussed. A letter the line was not offered is dropped,
so it cannot add an item or reach past the embedder's shortlist. What it
confirms joins `heard`, and `heard` enters `detect.compare` exactly as the
embedding's answer did: partial at most. Coverage, severity and risk are
`detect`'s and did not change.

**When it cannot answer, the embedding stands.** A failed request, an
unparseable answer, an utterance too long to send, or one past
`VERIFY_MAX_UTTERANCES` keeps the embedding-only decision
(`semantic.nearest_item`), and `gap_detection_complete` logs how many were
asked and how many went unanswered.

**A privacy refusal is not "cannot answer".** When `check_outbound` finds an
unmasked value in a request, `PrivacyViolationError` propagates and the task
fails. It means a stored transcript holds what module A should have masked;
falling back would keep the product running while hiding that. Raised in
review of #484.

**What leaves, with `gemini`.** Per request, under the 4,000-character outbound
cap and through `autune_integrations.HttpClient` (`check_outbound` scans the
body):

- the fixed instruction text;
- the candidate items offered in that request — template-file content;
- the ambiguous utterances, numbered, **as module A stored them**. Module A masks
  resident registration, card, phone and account numbers and e-mail addresses
  written in digits. **It does not mask names, and on the batch path it does
  not mask numbers read out as words** ("공일공 일이삼사…" — the spoken-number
  recogniser runs on the live path only; module A's to fix, raised in review
  of #484). Either goes to Google with its line. No speaker, time, meeting or
  utterance id, and no neighbouring line.

A meeting with no topics sends nothing, since it raises no gaps. At most
`VERIFY_MAX_UTTERANCES` utterances of one meeting leave per run. This is the
exposure module B's `llm` classifier has (#392) at a smaller size, and it
takes the same answer: opt-in, never the default, dummy meetings only until
the team decides otherwise. A free-tier key may let the provider keep what it
is sent.

**Another provider** is another class behind `pipeline.base.TemplateVerifier`
and an entry in `registry._VERIFIERS`; nothing outside `pipeline` names one.
Tests use `FakeVerifier`, which takes a decision function or confirms the
embedder's nearest candidate. The verifier is for template matching only;
relation extraction's own assistance is "Relation assistance" above, and shares
only the Gemini client (`pipeline/gemini.py`).

```bash
uv run --package autune-gap python -m autune_gap.eval --compare --verifier gemini  # off / local / +gemini
uv run --package autune-gap python -m autune_gap.eval --probe --verifier gemini    # single utterances
```

The probes (`eval/fixtures/verification_probes_v1.json`) are the utterances the
embedding got wrong or nearly wrong: "인덱스 재색인이 먼저 끝나야…" (dependency),
"인기순 정렬 대신 실시간 개인화로…" (not cold start), an owner and a date with no
noun, and "네 알겠습니다, 마치겠습니다" (nothing).

Measured on 2026-09-30 with `gemini-3.5-flash` (fallback never used), spaCy,
the four authored meetings, templates `general.4` / `feature_planning.2`, after
the key-reading fix below:

| | off | local | local + gemini |
| --- | --- | --- | --- |
| precision (`high`) | 0.89 | 1.00 | 1.00 |
| recall (`high`) | 1.00 | 0.94 | 1.00 |
| false positives (all `no-noun`) | 2 | 0 | 0 |
| verifier requests / utterances sent | 0 / 0 | 0 / 0 | 4 / 10 |

**On this set the verifier takes recall back without giving up precision.** The
embedding alone closed both `no-noun` false positives and lost the cold-start
gap ("인기순 정렬 대신 실시간 개인화로…" is not a cold-start plan); with the
verifier checking the ambiguous lines, the gap is back and neither false
positive returns. Four authored meetings and ten asked utterances say the
mechanism does what it was built for, and nothing about real meetings: the W5
set decides whether it is worth its request. `--relations gemini` changes
nothing here, because no utterance in the set holds a pair the rules decline
(see "Relation assistance").

**The 2026-09-29 run read 0.94 / 1.00 and was a parse bug.** The model keyed its
answers by the line label it was shown (`"발화 1"`), the parser accepted only a
bare digit, and every line came back answered empty — "not this item" on every
ambiguous utterance, overriding the embedding. That run concluded the verifier
traded precision for recall; it was measuring the parse. Keys are now read by
`gemini.answers_by_line`.

Probes: 3/5 on the embedding alone, 5/5 with Gemini (2 requests). Requests ran
765-1,543 characters; no body carried the key, a speaker, an id or a line that
triage had not marked ambiguous.

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
topic label has ever contained a masked span. That guarantee never moved, and
**the drop is still the only thing enforcing it.** #250 stopped a masked value
taking its neighbour down with it (see "Step 1 as built"), but that works on the
noun-run path only — a masked value the model tags as an entity in its own right
reaches `build_topics` untouched, and the mask check is what refuses it there.
Do not read the one as making the other redundant. Raised in review of #250.

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
| PostgreSQL `gap_scorings` | A digest of who counted as one person when a meeting's gaps were last scored |
| PostgreSQL `gap_agenda_events` | Which event on whose own Google Calendar holds a gap's line (S20 "다음 회의 잡기", #824), so the line can be taken out again, and the day that event starts. Read by the cleanup, and by `gap.next_meeting_days` for each picked day and the display name of who picked it |
| PostgreSQL `gap_agenda_cleanup` | Lines still to take off their owners' calendars, drained by the worker: those of a deleted or expired meeting, and those of an owner who left the meeting's team (#937). Keyed by the owner, not the meeting |
| PostgreSQL `gap_templates` | Domain templates and their items — **not built, and not needed**, see below |

Everything that exists cascades from `meetings.id` (or, for the agenda tables,
from `users.id` as well). The one thing outside the database is a gap's line
on a person's own calendar, so C registers a meeting hook and a user hook that
take those lines out (`calendar_writes`, privacy.md section 6).

**A person deleting their own speech is the exception (#587).** A topic's
`label` is a span cut from an utterance and a gap's `suggested_question` names
it; only `gap_topic_utterances` cascades from `utterances.id`. So
`service.forget_deleted_speech` runs on `autune_core.deletion.on_speech_deleted`,
before the utterances go:

- A topic goes only when every utterance it was built from is being deleted. A
  topic somebody else also named stays, label and all: it is still the
  meeting's topic, in their words too.
- A gap stays — it is the team's finding. If its question names the label of
  a topic that goes, the question falls back to the template item's general
  `question`, or to none if the template is no longer shipped. Every gap of
  the meeting is checked, not only those linked to the topic: a missing item's
  question can name the meeting's subject without a link to it (#598).
- Each meeting that changed is queued for `autune.gap.publish_report`, so E
  stops quoting the label. A meeting left with no topic at all is not
  republished (`republish_report` skips an unanalysed meeting); E clears its
  own copy on the same signal.

The hook lives in `service.py` because the API process, where module A's
deletion runs, imports `router` (and through it `service`) but never `tasks`.

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

`gap_gaps.carried_at` marks a gap somebody sent on to the next meeting
("다음 회의 어젠다로" on S20, #824), with `dismissed_at`'s rules: no actor
column, a second press keeps the first moment, and a re-run that updates the
row in place keeps it. It names no meeting. A team's next meeting has no agenda
to hold the gap yet (#756), so the mark is what the next meeting's picture reads
— the agent tool `gap.carried_gaps`, which lists the team's carried,
undismissed gaps, most recently sent first. Nothing is scheduled and nobody is
invited; that is S25 (P2). A mark stays until somebody takes it back or
dismisses the gap.

`gap_agenda_events.event_day` keeps the day the event a line went onto
starts, in Korea, as Google gave it at the write. A person who picks an event
for the next meeting has chosen that day, so the Follow-up approval card offers
it beside the day its own rule suggests: the agent tool `gap.next_meeting_days`
lists a meeting's picked days, each once, from today on, with the display
name of who picked each -- so the approver knows whose day it is (the owner,
2026-10-08). Picking is an act a member took for the team, and where a Slack
channel is connected C's team notice posts the same name for the same press;
the tool serves the approvals card only, not the chat model, and gives nothing else of the calendar or the event, and only
members still on the team are named. Two people who picked different days
give two, and the approver chooses. An event moved later keeps its old day
until somebody presses again; the event's attendees, all on the team, already
see its day on the event itself.

`gap_gaps.question_edited_at` marks a 해소용 질문 a member rewrote by hand
("편집" on S20, `PUT /gaps/{gap_id}/question`, #824), with the same rules: no
actor column. A re-run and `refresh_questions` keep an edited question rather
than recompute it; deleted speech still wins, and a question naming a label
that is gone is reset to the item's general one with the mark cleared (#587).
What a member types goes where C's own question goes — the team's Slack
question, the next meeting's line, E's report — so text that reads as personal
data (`find_unmasked`) is refused with a 422 and nothing changes.

### A speaker confirmed after scoring — `gap_scorings`

A gap's risk reads how much of the room was silent on a topic, and who is one
person comes from `participants.user_id`: two diarization labels that share a
user are one person, and having spoken as either counts as having spoken.
Module A fills `user_id` when somebody confirms a speaker (#370), which can be
long after the gaps were scored. `build_report` recomputes participation on
every read, but the stored `risk_score` and `severity` do not, so the report
could show a person who spoke next to a gap scored on their silence (#415).

A confirmation publishes no event (#360 settled on consumers re-reading), so
`detect_gaps` records a digest of the participant → person grouping it scored
against, and `autune.gap.periodic.rescore_changed_people` compares it against
the participants every ten minutes. A meeting whose grouping moved is detected
again and its `GapReport` republished. Gap ids and dismissals survive, as on any
re-run. A consent withdrawal moves the grouping too and is picked up the same
way.

**A change of consent also rebuilds the graph** (#515). The topics, their
evidence and the participation matrix were read from whoever consented when
the transcript arrived, so rescoring alone left a label taken from a withdrawn
line on S20 and a newly allowed line out of it. When the consenting
participants differ from those in `gap_participation` — which holds every
consenting participant on every topic, so its participant set is who consented
at build time — `service.rebuild_topic_graph` reads the meeting's stored
utterances (masked by module A, the same text the pipeline held) through the
same consent filter before detection runs. A confirmed speaker changes who is
one person, not whose speech may be read, and does not rebuild. A meeting with
no stored utterances is left as it is: there is nothing to rebuild from. Module
D does the same for its links with `autune.context.rederive_topics` (#472).

- **A digest, not the grouping.** A comparison needs nothing more, and a digest
  cannot be read back into who was merged with whom.
- **Only a meeting with a topic graph gets a row**, so the rescore never sends E
  a first report for a meeting the pipeline has not published. A re-run that
  leaves no graph deletes the row, so it is not rescored every ten minutes
  against a grouping it can never record.
- **A privacy violation fails the sweep.** A meeting that fails otherwise is
  logged by id and retried on the next run. A `PrivacyViolationError` from the
  verifier is a broken invariant (`pipeline/base.py`). The sweep finishes the
  other meetings, then raises it with the meeting ids and never the value.
- **Retries are capped per grouping (#516).** A failed rescore is counted on
  the row (`rescore_failures`, `failed_people_key`, `last_failed_at`). After
  `AUTUNE_GAP_RESCORE_MAX_ATTEMPTS` (5) failures in a row at the same grouping,
  the sweep skips the meeting until its people move again. That is a new
  question, so the count starts over. A successful detection clears all three.
  Before the cap, a meeting that always failed was retried every ten minutes
  for good, spending a hosted verifier's quota each time. A privacy violation is
  not counted, because `check_outbound` refused it before anything was sent. The
  sweep logs `held` for the meetings it skipped, and each failure logs its
  attempt number and whether that was the last.
- **No backfill.** A meeting scored before the table existed has no row and is
  left alone until its detection runs again. Speaker confirmation landed days
  earlier, so few meetings have a `user_id` to be stale about.
- **Naming a speaker who merges with nobody does not rescore.** It changes no
  silent share.

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
| GET | `/topics/{meeting_id}` | Topic graph, for debugging the pipeline (S20 does not draw it) |
| GET | `/explanations/{meeting_id}` | Why each gap was raised: its basis, the utterances it rests on, its score breakdown |
| GET | `/gaps?team_id=` | Every open gap across a team's meetings, newest meeting first (`severity` repeats, default `high`) |
| POST | `/gaps/{id}/dismiss` | Mark a gap as a false positive (feeds threshold tuning) |
| DELETE | `/gaps/{id}/dismiss` | Take a dismissal back |
| POST | `/gaps/{id}/carry` | Mark a gap as sent on to the next meeting — "다음 회의 어젠다로" (#824) |
| DELETE | `/gaps/{id}/carry` | Take that back |
| GET | `/templates` | Available domain templates |
| GET | `/templates/{meeting_id}` | Which template this meeting is held to, and how far it got with each item |
| PUT | `/templates/{meeting_id}` | Point this meeting at a template and re-compare |

### The read API as built

`/explanations/{meeting_id}` is what S20 shows beside a verdict. It reads the
stored coverage and score and never re-classifies: a gap rests on a thin topic
(its first utterances are quoted), on a keyword said without becoming a topic
(the consenting utterances that say it), on meaning (nothing to quote), or, when
missing, on nothing (the item's keywords are what was searched for). The score
breakdown is `detect.score_breakdown` over the same inputs and is sent only
while it adds up to the stored `risk_score`. Module C's own response, not a
contract, mirrored by hand in `features/gap/types.ts`.

A missing item's question names the meeting's subject (`detect.subject_of`).
Gaps stored before that read the template's generic question until detection
runs again, so `python -m autune_gap.refresh_questions [--team ID] [--dry-run]`
recomputes the stored `suggested_question` with `detect.question_for` and
queues `autune.gap.publish_report` for each meeting it changed, so the report,
E's stored copy and the agent's `gap.open_gaps` keep reading one question.
Coverage, score and severity are not touched; a second run changes nothing.

Two rules keep a question from naming the wrong thing (`detect.question_for`).
A topic label made only of the template's own keywords -- "성공", "필요", "다음
주" -- names nothing (`detect.nameable`): a matched topic like that gets the
template's question, and the subject skips it for the next topic. And the
subject is named only on items whose template says `ask_about_subject: true`;
who owns the work and what happens next are about the meeting, so `ownership`
and `next_step` keep their own questions. Neither rule moves a verdict or a
score. On the 48 gaps of the local database it changed 24 questions; see the PR
that introduced it for the comparison.

Everything above is built.
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
- **S20 does not draw the graph (#913).** The list of topic pairs and how they
  relate was a developer's view, not something a team acts on, so the 토픽 tab
  shows the ranking from the report only. The endpoint stays for anyone
  debugging the pipeline, and the graph still feeds risk scoring and the agent
  tools.
- **Nodes come in the report's order** — most central first, ties to the topic
  the meeting reached first — so the graph and the report never need two
  orderings reconciled. Edges come strongest first, ties by where
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
reprocessed. It then queues `autune.gap.publish_report`, so E scores the
meeting against the template in force rather than the one the pipeline first
compared it to (#316). A key no template file defines is a 422, not a 404 — what
is wrong is the value, not the address.

`POST /gaps/{id}/carry` and its `DELETE` are the same shape for `carried_at`
(`schemas.GapCarry`), and do not republish: `GapReport` carries no such mark,
so E's copy has nothing to change. S20 reads the mark from `/explanations`
(`carried`).

`POST /gaps/{id}/dismiss` sets `dismissed_at` and nothing else; `DELETE` on the
same path clears it, because a button pressed by mistake has to be undoable from
the screen or the mistake sits in the data tuning reads. Both return the state
the server settled on (`schemas.GapDismissal`). Dismissing twice keeps the first
timestamp. The routes are named by the gap, so the membership check is the
service's own: an unknown gap and a gap on another team's meeting are the same
404, and neither names the meeting. Both commit the mark and queue
`autune.gap.publish_report`, so E stops quoting and scoring a gap the team
called wrong (#471).

`GET /gaps?team_id=` is the sidebar's "갭 리포트" (#550): every undismissed
gap of the team's meetings, newest meeting first (started, else registered) and
by risk within one, capped at `service.TEAM_GAPS_LIMIT` rows. `severity`
repeats and defaults to `high`, the precision rule S20 keeps; the screen asks
for the rest behind a toggle. It names a team, so the check is membership of
it, and an unknown team is the same 404 as another team's. A row is the gap and
its meeting's title and date — **no topics and no participation**: a list
across every meeting is where reading silence along a person would be easiest
(privacy.md section 3), and the why is one click away on the meeting's report.

### Sending E the report again

`autune.gap.publish_report` re-sends `GapReport` built from the rows as they are
when it runs. E takes the latest payload for a meeting, reopens it and
re-aggregates without re-sending the personal DM, so a second
`autune.gap.completed` needs no change on E's side and no contract change.

- **Queued, never published in the request.** The API process is a Celery
  client, not a worker (#258), and the route returns without waiting on the
  broker round trip to E.
- **It never performs a first publish.** A meeting with no topic graph is
  skipped: a template chosen while the pipeline is still running, or for a
  meeting whose transcript never arrived, would otherwise hand E an empty
  report and start its timeout countdown before B and D have reported.
- **Two changes in a row end with the second, on one worker.** Each run reads
  committed rows, so the later run sends the later state. Two workers taking
  the pair out of order could leave E with the older one; `GapReport` carries
  nothing E could order payloads by, and adding that is a contract change.
  `autune.context.republish` has the same exposure.

A dismissal made under one template survives a switch to another. Switching
drops the old checklist's gaps, but a dismissed one stays — marked, out of the
report, and not on the rail, which reads only the template in force — so tuning
keeps its input and switching back finds the judgement where it was left.

S20 calls all of this (#48): "해당 없음" on a HIGH gap, "되돌리기" on the rail
item it leaves behind, and the rail's template picker. The screen re-reads the
report, graph and rail after each write rather than patching its own copy, and
polls them every five seconds while the rail says `analysed: false`.

## Celery tasks

| Task | Trigger | Queue |
| --- | --- | --- |
| `autune.gap.on_transcript_ready` | `autune.transcript.ready` | `cpu_heavy` |
| `autune.gap.publish_report` | `PUT /templates/{meeting_id}`, `POST`/`DELETE /gaps/{id}/dismiss` | `cpu_heavy` |
| `autune.gap.periodic.rescore_changed_people` | every 10 minutes | `cpu_heavy` |
| `autune.gap.periodic.drain_agenda_cleanup` | every 10 minutes: queues the lines of an owner no longer on the meeting's team, then takes queued lines out with each owner's grant | `cpu_heavy` |

## Slack surface

- Gap report thread in the meeting channel, `high` severity only by default
- Generated question cards teams can act on
- S20's two buttons post once on the team's channel (`team_notice`, #824).
  "담당자 지정해 질문" mentions the member with the gap's question. "다음 회의
  잡기" lists the gaps it put on the next meeting's event and says when that
  event starts (`10월 15일(목) 14:00`, in the event's own time zone). Only the
  event's date and time are read for it, never its title, which is Google's
  unmasked text.

## AI stack

| Component | Model or algorithm |
| --- | --- |
| Entity extraction | spaCy NER (Korean model) |
| Relation extraction | Rule-based patterns plus LLM assistance |
| Spoken evidence by meaning | KURE-v1 sentence embeddings, in process, off by default |
| Verifying ambiguous matches | Gemini, **external**, opt-in and off by default |
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

Every row a topic produces records `extractor_version` — the pipeline name, its
version and the version of `pipeline.spoken`'s rules,
`ko_core_news_lg-3.8.0+spoken-4`. The rules are half the extractor: the same
parse gives a different graph once a rule there changes, so `spoken.RULES_VERSION`
is bumped with any change to what it keeps. The name alone is not a version: the
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
uv run --package autune-gap python -m autune_gap.eval --compare   # embedder off vs local
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

The split between `missing` and `partial` is read off `gap_gaps.coverage`. It
used to be derived from `gap_related_topics` — a gap linking to no topic was a
missing one — and that reading had two problems. One was a failure mode shaped
exactly like a result: an empty link table says "every gap is missing". The
other retired the derivation outright: an item the meeting only said out loud
is partial and links to nothing, so every such gap read as missing. The harness
cross-checks the stored state against the stored `gap_gaps.title`, which
`detect` composes from the same coverage state, and stops with exit 2 if the
two disagree — or if a row carries no coverage at all — rather than printing a
cause split built on one of them.

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
