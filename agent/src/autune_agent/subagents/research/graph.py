"""The Research subgraph (spec section 3): read, terms, search, write, save.

Reads through its Toolbox only and calls no write a person sees: the document is
kept by the layer's own L0 tool, and sharing it leaves as one L2 proposal whose
only argument is the document id, so ``agent_runs`` keeps no text.
"""

from __future__ import annotations

from typing import Any, cast

from langgraph.graph import END, START, StateGraph

from autune_agent.main.registry import NO_MEETING, Toolbox
from autune_agent.main.subagents import CompiledSubagent, SubagentState
from autune_agent.results import ProposedAction, SubagentResult, ToolResult
from autune_core.errors import PrivacyViolationError

from .writer import Match, Writer, WriterError

OVERVIEW = "audio.meeting_overview"
RECENT = "audio.recent_meetings"
QUESTIONS = "extraction.unresolved_questions"
SEARCH = "audio.search_team_meetings"
SAVE = "agent.save_research_document"
SHARE = "agent.share_research_document"

TOOLS = (OVERVIEW, RECENT, QUESTIONS, SEARCH, SAVE)
ANALYSED = ("awaiting_confirmation", "complete", "delivered")


class ResearchState(SubagentState, total=False):
    meeting_id: str
    meeting_title: str
    questions: list[str]
    question_ids: list[str]
    terms: list[str]
    matches: list[Match]
    body: str


def _stop(reason: str, summary: str) -> dict[str, Any]:
    return {"outcome": SubagentResult(result=ToolResult.failure(reason, summary))}


def _done(summary: str) -> dict[str, Any]:
    return {"outcome": SubagentResult(result=ToolResult(ok=True, summary=summary))}


def build_with(writer: Writer) -> Any:
    def build(toolbox: Toolbox) -> CompiledSubagent:
        def read(state: ResearchState) -> dict[str, Any]:
            overview = toolbox.call(OVERVIEW)
            if not overview.ok and overview.reason == NO_MEETING:
                recent = toolbox.call(RECENT)
                picked = next(
                    (i for i in recent.items if getattr(i, "status", None) in ANALYSED), None
                )
                if picked is None:
                    return _stop("no analysed meeting", "조사할 회의가 없습니다.")
                overview = toolbox.call(
                    OVERVIEW, meeting_id=(picked.model_extra or {}).get("meeting_id")
                )
            if not overview.ok or not overview.evidence:
                return _stop(overview.reason or "meeting unreadable", "회의를 읽지 못했습니다.")
            meeting_id = overview.evidence[0]
            asked = toolbox.call(QUESTIONS, meeting_id=meeting_id)
            if not asked.ok:
                return _stop(asked.reason or "questions unreadable", "질문을 읽지 못했습니다.")
            kept = [(i.body.strip(), getattr(i, "id", None)) for i in asked.items if i.body.strip()]
            if not kept:
                return _done("이 회의에서 조사할 질문이 없습니다.")
            return {
                "meeting_id": meeting_id,
                "meeting_title": overview.summary,
                "questions": [q for q, _ in kept],
                "question_ids": [i for _, i in kept if i],
            }

        def terms(state: ResearchState) -> dict[str, Any]:
            try:
                return {"terms": writer.terms(state["questions"])}
            except PrivacyViolationError:
                raise
            except Exception:  # noqa: BLE001 - no terms is a document without past quotes
                return {"terms": []}

        def search(state: ResearchState) -> dict[str, Any]:
            seen: set[str] = set()
            matches: list[Match] = []
            for term in state["terms"]:
                found = toolbox.call(SEARCH, query=term, exclude_meeting_id=state["meeting_id"])
                for item in found.items if found.ok else []:
                    uid = getattr(item, "id", None)
                    if uid and uid not in seen:
                        seen.add(uid)
                        matches.append(
                            Match(
                                utterance_id=uid,
                                meeting_id=getattr(item, "meeting_id", ""),
                                title=item.title,
                                body=item.body,
                            )
                        )
            return {"matches": matches}

        def write(state: ResearchState) -> dict[str, Any]:
            try:
                body = writer.write(
                    meeting_title=state["meeting_title"],
                    questions=state["questions"],
                    matches=state["matches"],
                )
            except WriterError:
                return _stop("document not written", "리서치 문서를 쓰지 못했습니다.")
            except PrivacyViolationError:
                raise
            except Exception:  # noqa: BLE001 - an outbound refusal or a timeout
                return _stop("document not written", "리서치 문서를 쓰지 못했습니다.")
            return {"body": body}

        def save(state: ResearchState) -> dict[str, Any]:
            ids = state["question_ids"] + [m.utterance_id for m in state["matches"]]
            saved = toolbox.call(
                SAVE, meeting_id=state["meeting_id"], body=state["body"], utterance_ids=ids
            )
            if not saved.ok or not saved.evidence:
                return _stop(saved.reason or "not saved", "리서치 문서를 저장하지 못했습니다.")
            doc_id = saved.evidence[0]
            result = ToolResult(
                ok=True,
                summary=(
                    f"질문 {len(state['questions'])}건과 과거 회의 발언 "
                    f"{len(state['matches'])}건으로 리서치 문서를 만들어 공유를 제안했습니다."
                ),
                evidence=[doc_id],
            )
            proposal = ProposedAction(
                kind="research_share",
                title="리서치 문서 공유",
                tool=SHARE,
                arguments={"document_id": doc_id},
                level="L2",
                rationale="회의에서 제기된 질문에 대해 팀이 가진 자료를 모았습니다.",
                evidence=[doc_id],
            )
            return {"outcome": SubagentResult(result=result, proposed=[proposal])}

        def next_after(node: str) -> Any:
            return lambda s: END if "outcome" in s else node

        graph = StateGraph(ResearchState)
        for name, fn in (
            ("read", read),
            ("terms", terms),
            ("search", search),
            ("write", write),
            ("save", save),
        ):
            graph.add_node(name, fn)
        graph.add_edge(START, "read")
        graph.add_conditional_edges("read", next_after("terms"), ["terms", END])
        graph.add_edge("terms", "search")
        graph.add_edge("search", "write")
        graph.add_conditional_edges("write", next_after("save"), ["save", END])
        graph.add_edge("save", END)
        return cast(CompiledSubagent, graph.compile())

    return build
