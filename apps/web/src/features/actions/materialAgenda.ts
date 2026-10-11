import { getMaterialUploadRules, listMaterials, searchMaterials } from "./api";
import { MAX_QUESTION_CHARS } from "./materialSearch";
import type { MaterialUploadRules } from "./types";

/**
 * The team's uploaded materials as a source of the agenda draft (#1147 B2).
 * Two shapes, as module B's other sources have (`agendaSources.ts`): one for
 * the new-meeting form's row, one for the draft in the pre-meeting brief.
 * Each is named by the feature that draws it; the routes hand these over.
 *
 * **Computed on every read, stored nowhere.** No draft is kept, so nothing
 * of a material outlives it: a material a member deleted is not in the next
 * search's answer and is simply absent from the draft, as a gap somebody
 * dismissed is absent from C's group. (The owner, 2026-10-10: a draft made
 * from a material is the meeting's and a delete does not reach it — kept
 * trivially, because there is no made draft to reach.)
 *
 * **Off where the server's switch is off.** Uploads and the search over them
 * are one deployment switch (`AUTUNE_EXTRACTION_MATERIAL_UPLOAD`, reported as
 * `enabled` and `search` by the upload rules). Off, or on a server whose
 * rules do not mention search, this source has no line and asks nothing more.
 */

/** Whether this server keeps uploads and answers a question over them. */
const searchable = (rules: MaterialUploadRules) => rules.enabled && rules.search === true;

/**
 * The question the brief asks the team's materials: the meeting's title, as
 * the brief already carries it, with its spaces folded and cut to what the
 * search takes. Nothing else of the meeting is added. The server masks it
 * before it is embedded and keeps it nowhere (`material_search`).
 */
export function agendaQuestion(title: string): string {
  return title.split(/\s+/).filter(Boolean).join(" ").slice(0, MAX_QUESTION_CHARS).trim();
}

/**
 * For the new-meeting form's row: the team's uploaded materials, by title.
 * The form has no meeting and so no title to search with yet; the row only
 * asks whether a draft could have something, and a team with an upload
 * could. Like the row's other sources it is an estimate — the brief's search
 * may still find nothing near its meeting's title. Drive links are not
 * counted: Autune reads nothing of them, so the search never returns one.
 */
export const MATERIALS_AGENDA = {
  label: "올린 자료",
  lines: async (teamId: string) => {
    if (!searchable(await getMaterialUploadRules(teamId))) return [];
    return (await listMaterials(teamId))
      .filter((material) => material.source === "upload")
      .map((material) => ({ title: material.title }));
  },
};

/**
 * For the draft in the pre-meeting brief: the team's uploaded materials
 * nearest the meeting's title, best first, five at most — the search's own
 * order and cap. Asked by the brief's meeting, which is all the brief knows
 * of its team; the server checks the reader is on that meeting's team.
 *
 * A line is the material's title and nothing else. The draft's other carried
 * groups show a title and no excerpt, so this one shows none either; the
 * group's name says where the lines came from.
 *
 * The search has no distance floor yet (`material_search`): a team with
 * uploads gets its nearest materials whatever they are about. The group's
 * name says "가까운", not "관련된", for that reason.
 */
export const MATERIALS_BRIEF_AGENDA = {
  label: "회의 제목과 가까운 올린 자료",
  lines: async (_earlierMeetingId: string, about: { meetingId: string; title: string }) => {
    const question = agendaQuestion(about.title);
    if (question === "") return [];
    const meeting = { meetingId: about.meetingId };
    if (!searchable(await getMaterialUploadRules(meeting))) return [];
    const answer = await searchMaterials(meeting, question);
    return answer.hits.map((hit) => ({ title: hit.title }));
  },
};
