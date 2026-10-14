import { describe, expect, it } from "vitest";

import { REQUIRED_CONSENTS } from "./consents";
import { COPY_NOTICE, LEGAL_DOCUMENTS } from "./content";

// The documents are published only with every value the operator gives filled
// (2026-10-11). A `blank(...)` left anywhere -- a paragraph, a list, a table
// cell -- would show on the page as "확정 전".
function blanks(value: unknown, found: string[] = []): string[] {
  if (Array.isArray(value)) value.forEach((v) => blanks(v, found));
  else if (value && typeof value === "object") {
    if ("blank" in value) found.push(String((value as { blank: unknown }).blank));
    else Object.values(value).forEach((v) => blanks(v, found));
  }
  return found;
}

describe("legal documents", () => {
  it("leave no value to be filled", () => {
    expect(blanks(LEGAL_DOCUMENTS)).toEqual([]);
    expect(blanks(COPY_NOTICE)).toEqual([]);
  });

  // The sentences about a team's uploaded document (#817) came into the
  // two documents a person agrees to after `2026-10-12` had already been
  // on main with the consent page. Whoever agreed to that version agreed
  // to a text without them, so the text that has them is not `2026-10-12`
  // (the owner, 2026-10-12).
  it("are not asked for under the version that lacked the materials sentences", () => {
    for (const { document, version } of REQUIRED_CONSENTS) {
      const doc = LEGAL_DOCUMENTS.find((d) => d.id === document);
      expect(JSON.stringify(doc), document).toContain("팀이 올린 문서 자료");
      expect(version, document).not.toBe("2026-10-12");
    }
  });

  // A refused upload is told to the team's approvers by Slack (#1201). A new
  // message that leaves the service is in the notice with what it carries
  // and what it leaves out, and the record says nobody is written down.
  it("say what the approvers' message about a refused upload carries, and that nobody is recorded", () => {
    const privacy = JSON.stringify(LEGAL_DOCUMENTS.find((d) => d.id === "privacy"));
    expect(privacy).toContain("확인을 기다리는 받지 않은 파일의 건수 및 서비스 화면 링크");
    expect(privacy).toContain("파일의 이름, 일시, 표시의 종류 및 올린 사람은 싣지 않습니다");
    expect(privacy).toContain("알림을 받은 사람과 이 기록을 확인한 사람도 기록하지 않습니다");
    expect(privacy).toContain("알림을 보낸 일시는 그 기록과 함께 삭제합니다");
  });

  // Searching a team's uploaded documents (#817) is a purpose of its own: a
  // person who agreed to keeping them had not agreed to searching them. Both
  // documents name it, and the privacy policy says what happens to the
  // question a person types (the owner, 2026-10-12).
  it("name the search of a team's uploaded documents as a purpose", () => {
    const text = (id: string) => JSON.stringify(LEGAL_DOCUMENTS.find((d) => d.id === id));
    expect(text("terms")).toContain("팀이 올린 문서 자료의 보관 및 검색");
    expect(text("privacy")).toContain("팀 자료의 보관 및 검색");
    expect(text("privacy")).toContain(
      "입력한 질문은 같은 방법으로 가림 처리한 뒤 자료를 찾는 데에만 이용하며, 저장하거나 로그에 기록하지 않습니다.",
    );
    // Whoever agreed to either document without these sentences, or without
    // the ones on the approvers' message, is asked again: neither version is
    // the one `main` asked under before them.
    const before: Record<string, string> = { terms: "2026-10-12.2", privacy: "2026-10-12.4" };
    for (const { document, version } of REQUIRED_CONSENTS) {
      expect(version, document).not.toBe(before[document]);
    }
  });

  // A team nobody is on keeps no materials (#817; the owner, 2026-10-13):
  // module B's hourly task deletes them whatever the team chose. The policy
  // had said a person's withdrawal does not delete a team's documents, which
  // is no longer the whole of it for a team's only member -- so it says the
  // exception where it says the rule, and wherever it says how long.
  it("say a team with no member left loses its uploaded documents", () => {
    const text = (id: string) => JSON.stringify(LEGAL_DOCUMENTS.find((d) => d.id === id));
    const privacy = text("privacy");
    expect(privacy).toContain(
      "회원의 탈퇴 또는 본인 발화의 삭제로는 팀의 문서 자료가 삭제되지 않습니다. 다만 팀에 구성원이 한 명도 남지 않게 된 경우(마지막 구성원이 탈퇴한 경우를 포함합니다)에는 1시간마다 확인하여, 팀이 지울 때까지 보관하도록 정한 경우에도, 그 팀의 문서 자료와 등록한 링크, 받지 않은 문서 파일의 기록을 모두 삭제하며 복구할 수 없습니다.",
    );
    expect(privacy).toContain(
      "팀 구성원이 삭제하거나 팀이 삭제되거나 팀에 구성원이 한 명도 남지 않게 될 때까지 보관합니다.",
    );
    expect(privacy).toContain(
      "알림을 보낸 일시는 그 기록과 함께 삭제합니다. 팀에 구성원이 한 명도 남지 않게 된 경우에는 그 전이라도 삭제합니다",
    );
    expect(text("terms")).toContain(
      "팀 구성원이 삭제하거나 팀이 삭제되거나 팀에 구성원이 한 명도 남지 않게 될 때까지 보관됩니다.",
    );
    // Neither document still ends the kept-until-deleted case at the team's
    // deletion alone.
    for (const id of ["privacy", "terms"]) {
      expect(text(id), id).not.toContain("팀이 삭제될 때까지 보관");
    }
    // Whoever agreed to either document as it read before is asked again
    // (the owner, 2026-10-13): the policy they agreed to said a withdrawal
    // deletes no team's documents.
    const before: Record<string, string> = { terms: "2026-10-12.3", privacy: "2026-10-12.5" };
    for (const { document, version } of REQUIRED_CONSENTS) {
      expect(version, document).not.toBe(before[document]);
    }
  });

  // What the documents said of a team's uploaded documents and what the code
  // does (the owner, 2026-10-13): vectors are kept beside the masked text;
  // the masker misses a value split by a line or page break and the check
  // before storing is the same detector (`document_masking.py`); a material
  // has no report path, only its delete; and a server's access record holds
  // the address asked for, which can carry a team's id.
  it("say what is kept of an uploaded document and what the masking does not do", () => {
    const text = (id: string) => JSON.stringify(LEGAL_DOCUMENTS.find((d) => d.id === id));
    const privacy = text("privacy");
    expect(privacy).toContain(
      "그 글을 찾는 데 쓰기 위하여 가림 처리된 글로부터 계산한 수치(벡터)와 계산에 쓴 모델의 이름",
    );
    expect(privacy).toContain(
      "가림 처리는 자동화된 방법으로 이루어지므로, 줄이나 쪽이 바뀌는 곳에서 나뉘어 적힌 번호나 전자우편 주소는 가려지지 않은 채 보관될 수 있습니다. 문서 자료에는 가려지지 않은 부분만 골라 가리는 기능이 없으며, 팀 구성원은 그 자료를 삭제할 수 있습니다.",
    );
    expect(privacy).toContain(
      "접속 일시, 접속 IP 주소 및 요청한 주소(팀을 가리키는 번호가 포함될 수 있습니다)는 서비스의 데이터베이스에 저장하지 않으나",
    );
    expect(text("terms")).toContain(
      "이용자는 전사문의 해당 부분을 신고하여 즉시 가림 처리되도록 할 수 있으며, 팀의 문서 자료에 그런 부분이 있는 경우에는 그 자료를 삭제할 수 있습니다.",
    );
    const security = text("security");
    expect(security).toContain(
      "저장하기 전에 같은 방법으로 한 번 더 검사합니다. 이 검사는 처음의 가림 처리가 찾지 못한 값을 찾아내지 못합니다.",
    );
    expect(security).not.toContain("가림 처리되었는지 다시 검사합니다");
  });

  // The server refuses any other shape (`autune_core.consents._VERSION`),
  // and a version it refuses leaves every person at the consent page.
  it("are asked for under a version the server takes", () => {
    for (const { version } of REQUIRED_CONSENTS) {
      expect(version).toMatch(/^[0-9A-Za-z][0-9A-Za-z._-]{0,63}$/);
    }
  });
  // The service runs on AWS's Seoul region (#1342). The policy named a tunnel
  // through Cloudflare and a server the team runs itself; neither is true of
  // this host, and a sentence that says what the code does not do is the
  // thing this file is careful not to have.
  it("name AWS as the one processor of the hosting row, and no longer a relay", () => {
    const privacy = JSON.stringify(LEGAL_DOCUMENTS.find((d) => d.id === "privacy"));
    expect(privacy).toContain("Amazon Web Services, Inc. (서울 리전)");
    expect(privacy).toContain("서울 리전 밖으로 옮기지 않습니다");
    expect(privacy).toContain("서버의 임시 메모리 영역에서만 처리하도록 구성되어 있습니다");
    // Audio is still given to no outside service, and now says where it is
    // processed while it is.
    expect(privacy).toContain("음성 녹음은 어떠한 외부 서비스에도 제공하지 않습니다");
    expect(privacy).toContain("서비스가 가동되는 서버(제6조의 수탁자)에서 전사하는 동안에만 임시로 처리");
    expect(privacy).not.toContain("Cloudflare");
    expect(privacy).not.toContain("접속을 중계");
    expect(privacy).not.toContain("그 보관은 위탁하지 않습니다");
  });

  // A person who agreed to `2026-10-12.6` agreed to a text that said the
  // keeping was not entrusted and that a Cloudflare tunnel carried the
  // connection. They are asked again.
  it("ask again those who agreed before the hosting moved to AWS", () => {
    const privacy = REQUIRED_CONSENTS.find((c) => c.document === "privacy");
    expect(privacy?.version).not.toBe("2026-10-12.6");
  });

});
