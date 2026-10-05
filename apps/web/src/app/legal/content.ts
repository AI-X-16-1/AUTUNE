/**
 * The privacy policy, the terms of service and the information security
 * policy, as data -- in the form such documents take: articles (조), numbered
 * paragraphs (①), items, and a supplementary provision (부칙).
 *
 * DRAFT, not reviewed by a lawyer. Every `blank(...)` is a value only the
 * operator can give -- its name, the hosting provider, the privacy officer,
 * the security contact, the effective date. The page marks each as "확정 전".
 * Do not publish while one is left.
 *
 * The wording follows the article-form text the team keeps as a document
 * (2026-10-02). That text also makes choices that are assumptions until a
 * lawyer has read them, written here as plain statements because a legal
 * document cannot carry "maybe": the operator is called "회사"; nobody under 14
 * may use the service; the service is free; a change is announced 7 days ahead
 * and 30 when it is to the reader's disadvantage; disputes go to the court the
 * Civil Procedure Act names; processing rests on Article 15(1)1 and 15(1)4 of
 * the Personal Information Protection Act; a vulnerability report is answered
 * within three business days. Changing one of those is changing a promise.
 *
 * The text states what the code does today and nothing it does not do yet.
 * Where the document and the code disagreed the code won, and these are the
 * places, so that the next edit does not undo them:
 * - consent to a recording is the uploader's duty; the service does not take
 *   that confirmation at upload, and any member of the team can record it
 *   (privacy 제9조). Unconfirmed speech is stored and is not analysed.
 * - what goes to a language model is listed for EVERY caller on main -- B's
 *   classifier, resolver, meeting summary (#782: the whole meeting's
 *   consenting lines, in sections) and NLI (#828: only the utterances the
 *   classifier called ambiguous; local by default), C's verifier and
 *   relation assistant, D's
 *   judge, the agent's router, Research and the ask loop (#677: per item it
 *   sends the title and the start of the body, masked utterances among them)
 *   -- not for the one that sends least (privacy 제6조). A new caller means
 *   rereading that row.
 * - 제6조 ③ ("only where the operator turned it on") is true in two different
 *   ways. Module B's cloud models need a second switch as well
 *   (AUTUNE_EXTRACTION_LLM_ACKNOWLEDGED_392, #750); the agent's is on as soon
 *   as it has a key (router_impl defaults to gemini). OPERATING CONDITION,
 *   which the page cannot state as a promise: until #392 is decided no cloud
 *   model is to see a real meeting -- demo meetings only.
 * - what leaves to Slack, Notion and Jira is listed from the code that sends
 *   it, per recipient (privacy 제5조): Jira also gets the assignee's e-mail
 *   address, to find their account (jira_sync), and a confirmed decision's
 *   statement as an issue of its own (#796); a person's own due-date
 *   reminder (#751), Monday digest (#792) and Tuesday-to-Friday morning DM
 *   (#833) carry the item, its date, the meeting's title and a link, and are
 *   sent only where the operator turned them on -- the deployment, not the
 *   team; a project's minutes (#787) go to all three when a member presses
 *   send.
 * - one switch of the person's turns all three off (#771, #826, #833). Their
 *   own pause dates (#833, ext_notification_pauses) stop the two digests and
 *   not the reminders, are theirs alone, and are deleted once they have
 *   ended: 제2조, 제3조 and the Slack row say exactly that much.
 * - an invitation is mailed from the inviter's OWN Gmail, only when they
 *   choose it for that invitation (#760): the invited address, the inviter's
 *   name, the team's name and the link go to Google, under a grant that can
 *   send and cannot read (제2조, 제5조, 제7조). The row is worded for "chooses
 *   to send", which is true of the checkbox on main and of the button in #835.
 * - a name typed for a voice with no account (#836) is kept for that one
 *   meeting and goes with it; a pinned team (#747) is a mark on the person's
 *   own membership (제2조, 제3조).
 * - out-of-office time is READ from a connected calendar, where the operator
 *   turned it on (#838; off by default): the start and end of out-of-office
 *   events only, asked about the moment a digest would go, and not stored.
 *   That is a collection from Google, not a provision to it, so it is in
 *   제2조 and 제3조 and in the Slack row's account of when a digest does not go
 *   -- not in the Google Calendar row of 제5조, which lists what is SENT.
 *   "Not stored" holds on main since #841: no log line counts who was held
 *   back either.
 * - that a reminder or a digest went, and when, IS kept (#846 says so in
 *   privacy.md): a reminder's mark goes with its item, a digest's with the
 *   account or the team, and no code deletes them sooner (제2조, 제3조).
 *   Public holidays come from a public calendar with no credentials and are
 *   nobody's personal data; the Slack row says only that no digest goes then.
 * - copies outside are not all alike (privacy 제4조 ⑤, terms 제13조 ③): an
 *   item's or a decision's page stays as the team's record; a deleted item's
 *   page is trashed and its issue closed, retried (#764); a decision whose
 *   confirmation is taken back, or that is deleted, has its statement taken
 *   off its page and its issue before the page is trashed and the issue
 *   closed (#669, #796) -- Notion's page history and Jira's issue history
 *   are out of reach and may keep the earlier wording; a
 *   project's minutes
 *   are retracted with the meeting and rewritten when their content changes
 *   (#787); a withdrawn account's calendar and Gmail grants are revoked at
 *   Google (#766, #760).
 * - the application stores no IP address. What a server's own access log
 *   keeps is the hosting environment's, and nobody has checked its retention
 *   (제2조 says exactly that much).
 * - deleting one's own speech does not yet reach the decision statements kept
 *   for meeting linking (D has no hook). The report and its corrections no
 *   longer quote it (#725); a copy already posted outside stays there
 *   (privacy 제4조 ④).
 * - a pending invitation keeps the address of someone who has agreed to
 *   nothing (#739), and each sign-in keeps which document version was agreed
 *   to (#715): both are in 제2조 and 제3조.
 * - a per-participant spoke/did-not-speak value per topic IS stored and shown
 *   to the team; what is not stored is a per-person speech volume (제2조).
 * - voice data is stored only where the operator turned it on (제10조 ④).
 * - retention counts from the upload or the live start, not the meeting's
 *   date (제3조).
 * A sentence here is a promise to the person who signs in.
 *
 * Source of the facts: docs/architecture/privacy.md and the code it names.
 * User-facing copy is Korean; this file's comments stay English.
 */

export interface Blank {
  blank: string;
  proposed?: string;
}

export interface Strong {
  strong: string;
}

export type Inline = string | Blank | Strong;
export type Line = Inline | readonly Inline[];

export type Block =
  | { kind: "paragraph"; line: Line }
  | { kind: "list"; ordered?: boolean; items: readonly Line[] }
  | {
      kind: "table";
      head: readonly string[];
      rows: readonly (readonly Line[])[];
    };

export interface Section {
  /** The chapter (장) this article opens, shown above it. */
  chapter?: string;
  heading: string;
  blocks: readonly Block[];
}

export interface LegalDocument {
  id: "privacy" | "terms" | "security";
  title: string;
  /** The preamble, for a document that has one. */
  lead?: Line;
  sections: readonly Section[];
}

const blank = (label: string, proposed?: string): Blank => ({
  blank: label,
  proposed,
});
const p = (line: Line): Block => ({ kind: "paragraph", line });
const ol = (...items: Line[]): Block => ({
  kind: "list",
  ordered: true,
  items,
});
const table = (head: string[], ...rows: Line[][]): Block => ({
  kind: "table",
  head,
  rows,
});

const OPERATOR = blank("운영자 명칭");
const HOSTING = blank("호스팅 사업자");
const OFFICER_EMAIL = blank("전자우편 주소");
const EFFECTIVE = blank("시행일");

/**
 * Shown above the documents. Their text cannot be selected on the page (the
 * user, 2026-10-02), and a customer is owed a copy of the terms on request, so
 * the page says how to get one.
 */
export const COPY_NOTICE: Line = [
  "이 문서는 인쇄하여 보관할 수 있으며, 개인정보 보호책임자의 연락처(",
  OFFICER_EMAIL,
  ")로 요청하면 사본을 보내 드립니다.",
];

const PRIVACY: LegalDocument = {
  id: "privacy",
  title: "개인정보 처리방침",
  lead: [
    OPERATOR,
    '(이하 "회사")는 「개인정보 보호법」 제30조에 따라 정보주체의 개인정보를 보호하고 이와 관련한 고충을 신속하고 원활하게 처리할 수 있도록 다음과 같이 개인정보 처리방침을 수립·공개합니다.',
  ],
  sections: [
    {
      heading: "제1조(개인정보의 처리 목적)",
      blocks: [
        p(
          "회사는 다음의 목적을 위하여 개인정보를 처리합니다. 처리하는 개인정보는 다음의 목적 이외의 용도로는 이용되지 않으며, 이용 목적이 변경되는 경우에는 「개인정보 보호법」 제18조에 따라 별도의 동의를 받는 등 필요한 조치를 이행합니다.",
        ),
        ol(
          "회원 가입 및 관리: Google 계정을 통한 본인 식별·인증, 회원 자격의 유지·관리, 각종 고지·통지",
          "회의 기록 서비스의 제공: 회의 음성의 전사, 화자 구분, 전사문에 포함된 개인정보의 가림 처리, 회의 기록의 열람 제공",
          "회의 분석 서비스의 제공: 액션 아이템 및 결정 사항의 추출, 논의되지 않은 항목의 탐지, 이전 회의와의 연결, 팀 단위 지표의 산출",
          "외부 서비스 연동: 이용자 또는 이용자가 속한 팀이 연결한 외부 서비스로의 결과 전달",
          "고충 처리: 민원인의 신원 확인, 민원 사항의 확인, 처리 결과의 통보",
        ),
        p(
          "회사는 「개인정보 보호법」 제15조 제1항 제1호(정보주체의 동의) 및 제4호(정보주체와 체결한 계약의 이행)에 근거하여 개인정보를 처리합니다.",
        ),
      ],
    },
    {
      heading: "제2조(처리하는 개인정보의 항목)",
      blocks: [
        p("① 회사는 다음의 개인정보 항목을 처리합니다."),
        table(
          ["구분", "처리 항목", "수집 방법"],
          [
            "회원 정보",
            "전자우편 주소, 이름, Google 계정 식별자, 최종 로그인 일시",
            "Google 로그인 시 Google로부터 제공받음",
          ],
          [
            "팀 정보",
            "소속 팀, 팀 내 역할, 본인의 팀 목록에서 위에 고정한 팀",
            "이용자의 입력",
          ],
          [
            "회의 정보",
            "회의 제목, 시작 일시, 길이, 참석자 표시(화자 이름표, 역할), 계정이 없는 참석자에 대하여 팀 구성원이 해당 회의에 한하여 직접 입력한 이름",
            "이용자의 입력 및 서비스 이용 과정에서 생성",
          ],
          [
            "음성 녹음",
            "이용자가 업로드하거나 실시간 회의 기능으로 전송한 음성",
            "이용자의 업로드 또는 전송",
          ],
          [
            "전사문",
            "가림 처리된 발화 내용, 화자, 발화의 시작 및 종료 시각",
            "서비스 이용 과정에서 생성",
          ],
          [
            "음성 특징정보",
            "음성에서 추출한 특징값(제10조 제4항에 따라 회사가 해당 기능을 활성화한 경우에 한함)",
            "서비스 이용 과정에서 생성",
          ],
          [
            "분석 결과",
            "액션 아이템(담당자 및 기한 포함), 결정 사항, 논의되지 않은 항목, 회의에서 추출한 주제의 명칭(발화에서 추출하므로 성명이 포함될 수 있음), 참석자별 주제 발언 여부, 회의 간 연결 정보, 팀 단위 지표",
            "서비스 이용 과정에서 생성",
          ],
          ["동의 확인 기록", "회의별 동의 확인자, 확인 일시", "이용자의 입력"],
          [
            "약관 동의 기록",
            "동의한 문서(이용약관, 개인정보 처리방침), 문서의 판, 동의 일시",
            "이용자의 동의",
          ],
          [
            "초대 대상자 정보",
            "팀 구성원이 팀에 초대하기 위하여 입력한 전자우편 주소(아직 가입하지 않은 사람의 주소를 포함)",
            "팀 구성원의 입력",
          ],
          [
            "알림 설정",
            "본인에게 오는 알림의 수신 여부, 본인이 정한 알림 중지 기간(시작일 및 종료일)",
            "이용자의 입력",
          ],
          [
            "알림 발송 기록",
            "본인에게 마감 알림, 월요일 요약 및 아침 요약을 보냈다는 사실과 그 일시. 메시지의 내용은 보관하지 않습니다",
            "서비스 이용 과정에서 생성",
          ],
          [
            "부재중 일정의 시각",
            "이용자가 연결한 Google Calendar의 부재중 일정의 시작 및 종료 시각(회사가 해당 기능을 활성화한 경우에 한함). 일정의 제목, 내용, 참석자 및 그 밖의 일정은 조회하지 않습니다",
            "이용자가 연결한 Google Calendar로부터 조회",
          ],
          [
            "연동 정보",
            "Slack, Notion, Jira, Google Calendar의 접근 토큰 및 연동 설정, 초대 메일을 보내기 위하여 이용자가 연결한 Gmail의 접근 토큰(메일 보내기 권한에 한하며 메일함을 읽는 권한은 받지 않습니다)",
            "이용자가 외부 서비스를 연결할 때 해당 서비스로부터 제공받음",
          ],
          [
            "자동 생성 정보",
            [
              "쿠키(세션 식별자). 접속 일시 및 접속 IP 주소는 서비스의 데이터베이스에 저장하지 않으나, 서버 운영 환경(",
              HOSTING,
              ")의 접속 기록에 남을 수 있습니다",
            ],
            "서비스 이용 과정에서 자동 생성",
          ],
        ),
        p(
          "② 회사는 전사문에 포함된 전화번호, 전자우편 주소, 주민등록번호, 계좌번호 및 신용카드번호를 저장하기 전에 가림 처리하며, 가림 처리 전의 원문은 저장하지 않습니다. 성명은 가림 처리 대상에 포함되지 않습니다.",
        ),
        p(
          "③ 회사는 회의에서 각 참석자가 발화한 비율을 산출하여 해당 참석자 본인에게만 제공하며, 참석자별 발화량을 저장하지 않습니다. 주제별 발언 여부는 발언의 유무만을 기록하며, 해당 회의가 속한 팀의 구성원이 리포트에서 열람할 수 있습니다.",
        ),
        p(
          "④ 회사는 음성 녹음 및 전사문을 인공지능 모델의 학습 목적으로 보관하거나 이용하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제3조(개인정보의 처리 및 보유 기간)",
      blocks: [
        p(
          "① 회사는 법령에 따른 개인정보 보유·이용 기간 또는 정보주체로부터 개인정보를 수집할 때 동의받은 개인정보 보유·이용 기간 내에서 개인정보를 처리·보유합니다.",
        ),
        p("② 각 개인정보의 처리 및 보유 기간은 다음과 같습니다."),
        table(
          ["대상", "보유 기간"],
          [
            "음성 녹음",
            "전사가 종료될 때까지. 전사의 성공, 실패, 취소를 불문하고 종료 즉시 파기합니다.",
          ],
          [
            "회의 정보, 전사문, 분석 결과 등 해당 회의에 부속된 자료",
            "음성을 업로드하거나 실시간 회의를 시작한 날부터 90일. 팀은 보유 기간을 30일, 90일, 180일, 365일 중에서 정할 수 있습니다.",
          ],
          ["회의별 음성 특징정보", "해당 회의의 보유 기간과 같습니다."],
          [
            "본인 확인을 거친 음성 프로필",
            "해당 이용자가 참석한 회의가 보유되는 동안. 마지막 회의의 보유 기간이 만료되면 파기합니다.",
          ],
          [
            "회원 정보, 연동 정보, 알림의 수신 여부, 약관 동의 기록",
            "회원 탈퇴 시까지",
          ],
          [
            "본인이 정한 알림 중지 기간",
            "정한 종료일이 지날 때까지. 종료일이 지나면 파기하며, 이용자가 해제하거나 탈퇴하는 때에도 파기합니다.",
          ],
          [
            "알림 발송 기록",
            "마감 알림의 발송 기록은 해당 액션 아이템이 삭제될 때까지(회의의 보유 기간이 만료되어 삭제되는 경우를 포함합니다). 월요일 요약 및 아침 요약의 발송 기록은 회원 탈퇴 또는 팀의 삭제 시까지. 발송 일시가 남으므로 평소보다 늦게 발송된 날은 그 사유를 짐작할 여지가 있으나, 사유는 기록하지 않습니다.",
          ],
          [
            "부재중 일정의 시각",
            "저장하지 않습니다. 본인에게 월요일 요약 또는 아침 요약을 보낼지 판단하는 때에 조회하여 그 판단에만 쓰고 보관하지 않습니다.",
          ],
          [
            "계정이 없는 참석자에 대하여 직접 입력한 이름",
            "해당 회의의 보유 기간과 같습니다. 다른 회의에는 쓰이지 않습니다.",
          ],
          [
            "초대 대상자의 전자우편 주소",
            "초대가 수락되거나, 7일이 지나 만료되거나, 같은 주소로 다시 초대하여 교체되거나, 팀 또는 초대한 회원이 삭제되거나, 해당 주소의 회원이 탈퇴하는 때 중 먼저 오는 때까지",
          ],
        ),
        p(
          "③ 과거에 진행된 회의의 음성을 나중에 업로드하는 경우 보유 기간은 업로드한 날부터 기산합니다.",
        ),
      ],
    },
    {
      heading: "제4조(개인정보의 파기 절차 및 방법)",
      blocks: [
        p(
          "① 회사는 개인정보 보유 기간의 경과, 처리 목적의 달성 등으로 개인정보가 불필요하게 되었을 때에는 지체 없이 해당 개인정보를 파기합니다.",
        ),
        p("② 개인정보의 파기 절차는 다음과 같습니다."),
        ol(
          "음성 녹음은 전사가 종료되는 즉시 삭제합니다. 삭제되지 않고 남은 임시 파일이 있는지 1시간마다 점검하여 삭제합니다.",
          "보유 기간이 만료된 회의는 1시간마다 확인하여 해당 회의에 부속된 자료 일체와 함께 삭제합니다.",
          "회원이 탈퇴하면 해당 회원의 발화, 음성 특징정보 및 회원 정보를 삭제합니다.",
        ),
        p(
          "③ 회사는 삭제 표시만 하고 정보를 남겨 두는 방식을 사용하지 않으며, 전자적 파일 형태로 기록·저장된 개인정보를 데이터베이스에서 삭제합니다.",
        ),
        p(
          "④ 이용자가 자신의 발화를 삭제한 경우, 그 발화에서 생성된 액션 아이템, 결정 사항 및 논의 누락 분석에서는 해당 이용자의 발화 내용을 삭제하고, 팀의 업무 항목은 삭제된 발화에서 생성된 항목임을 표시하여 유지합니다. 다만, 회의 간 연결을 위하여 보관하는 결정 문장에는 삭제가 아직 반영되지 않아 해당 내용이 남을 수 있으며, 이미 Slack 등 외부 도구에 게시된 리포트 사본은 해당 도구에 남습니다.",
        ),
        p(
          "⑤ 이용자 또는 팀이 연결한 외부 서비스에 전달된 사본의 처리는 다음과 같습니다.",
        ),
        ol(
          "Notion 및 Jira에 전달된 액션 아이템 및 결정 사항은 해당 팀의 기록으로서 보유 기간의 만료 또는 회원 탈퇴로 삭제되지 않습니다. 이용자가 자신의 발화를 삭제하거나 내용을 정정한 경우에는 그 사본의 문장에 이를 반영합니다.",
          "이용자가 액션 아이템을 삭제한 경우 해당 Notion 페이지는 휴지통으로 옮기고 Jira 항목은 종료 처리합니다. 이용자가 결정 사항의 확정을 취소하거나 결정 사항을 삭제한 경우에는 해당 Notion 페이지와 Jira 항목에서 결정 사항의 문장을 지운 뒤 Notion 페이지는 휴지통으로 옮기고 Jira 항목은 종료 처리합니다(Jira 항목은 삭제하지 않습니다). 외부 서비스의 응답이 없어 처리하지 못한 사본은 기록해 두었다가 다시 시도하며, 거듭 실패한 사본은 해당 서비스에 남을 수 있습니다. Notion의 페이지 변경 이력과 Jira의 항목 변경 이력에는, 각 서비스가 자체적으로 보관하는 범위에서 지우기 전의 문장이 남을 수 있습니다.",
          "Slack, Notion 및 Jira에 보낸 프로젝트별 회의록은 해당 회의가 삭제되거나 보유 기간이 만료된 경우 회수(내용을 비우고 삭제 또는 종료)를 요청하고, 그 회의록에 포함된 내용이 삭제·정정·확정 취소된 경우 사본을 다시 작성합니다. 외부 서비스의 응답이 없으면 일정 기간 다시 시도하며, 그래도 처리하지 못한 사본은 해당 서비스에 남을 수 있습니다.",
          "담당자 본인의 Google Calendar에 등록된 일정은 삭제를 요청하며, Google의 응답이 없는 경우 일정이 남을 수 있습니다.",
          "회원이 탈퇴하는 경우 해당 회원이 Google Calendar 연결 및 Gmail을 통한 초대 메일 발송을 위하여 부여한 권한의 해지를 Google에 요청합니다. Google의 응답이 없는 경우 그 권한은 이용자가 Google 계정에서 직접 해제할 때까지 남을 수 있으며, 회사는 탈퇴 후 해당 권한의 사본을 보관하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제5조(개인정보의 제3자 제공)",
      blocks: [
        p(
          "① 회사는 정보주체의 개인정보를 제1조에서 명시한 범위 내에서만 처리하며, 정보주체의 동의, 법률의 특별한 규정 등 「개인정보 보호법」 제17조 및 제18조에 해당하는 경우에만 개인정보를 제3자에게 제공합니다.",
        ),
        p(
          "② 회사는 이용자 또는 이용자가 속한 팀이 직접 연결한 외부 서비스에 한하여 다음과 같이 개인정보를 제공합니다. 연결하지 않은 외부 서비스에는 제공하지 않습니다.",
        ),
        table(
          [
            "제공받는 자",
            "제공 항목",
            "제공 목적",
            "제공 시기",
            "보유 및 이용 기간",
          ],
          [
            "Slack Technologies, LLC (Slack)",
            "본인에게 보내는 확인 요청 메시지(본인의 발화 인용 포함), 본인의 발화 비율 및 승인을 기다리는 제안의 건수. 회사가 해당 기능을 활성화한 경우 본인에게 보내는 마감 알림, 매주 월요일의 본인 할 일 요약 및 화요일부터 금요일까지 아침의 본인 업무 요약(본인이 담당하는 액션 아이템의 내용, 기한, 회의 제목 및 서비스 화면 링크. 아침 요약에는 지난 요약 이후 본인이 완료하였거나 새로 맡은 항목이 포함됩니다. 이용자는 본인에게 오는 이 알림을 끌 수 있으며, 끄면 마감 알림, 월요일 요약 및 아침 요약이 모두 중지됩니다. 기간을 정하여 월요일 요약 및 아침 요약만 받지 않을 수도 있습니다. 월요일 요약 및 아침 요약은 공휴일에는 보내지 않으며, 회사가 해당 기능을 활성화한 경우 본인이 연결한 Google Calendar에 부재중 일정이 있는 시간에도 보내지 않습니다). 팀 채널에 보내는 회의 리포트 및 주간 팀 리포트(팀 단위로 집계한 지표, 반복되는 논의 누락의 유형, 액션 아이템의 건수), 이전 회의와 연결된 주제의 명칭, 변경된 결정 사항의 문장 일부와 주제의 명칭, 회의 전 브리핑(이전 회의의 요약 및 예정 안건), 팀 구성원이 보내기를 선택한 프로젝트별 회의록(팀 및 프로젝트의 명칭, 회의 일자, 확정된 결정 사항과 액션 아이템의 내용·담당자·기한)",
            "확인 요청 및 알림의 전달",
            "팀이 Slack을 연결한 때부터",
            "해당 서비스의 약관 및 팀의 설정에 따름",
          ],
          [
            "Notion Labs, Inc. (Notion)",
            "확정된 액션 아이템 및 결정 사항(내용, 담당자, 기한, 상태, 회의 제목). 팀 구성원이 보내기를 선택한 프로젝트별 회의록(팀 및 프로젝트의 명칭, 회의 일자, 확정된 결정 사항과 액션 아이템의 내용·담당자·기한)",
            "팀의 업무 기록 작성",
            "팀이 Notion을 연결하고 이용자가 항목을 확정한 때. 프로젝트별 회의록은 팀 구성원이 보내기를 선택한 때",
            "해당 서비스의 약관 및 팀의 설정에 따름",
          ],
          [
            "Atlassian Pty Ltd (Jira)",
            "확정된 액션 아이템(내용, 기한, 담당자의 Jira 계정) 및 담당자의 Jira 계정을 찾기 위한 담당자의 전자우편 주소. 확정된 결정 사항의 문장. 팀 구성원이 보내기를 선택한 프로젝트별 회의록(팀 및 프로젝트의 명칭, 회의 일자, 확정된 결정 사항과 액션 아이템의 내용·담당자·기한)",
            "팀의 업무 항목 등록",
            "팀이 Jira를 연결하고 이용자가 항목을 확정한 때. 프로젝트별 회의록은 팀 구성원이 보내기를 선택한 때",
            "해당 서비스의 약관 및 팀의 설정에 따름",
          ],
          [
            "Google LLC (Google Calendar)",
            "액션 아이템의 내용 및 기한. 참석자와 전사 내용은 포함하지 않습니다.",
            "담당자 본인의 일정 등록",
            "담당자가 본인의 캘린더를 연결한 때부터",
            "해당 서비스의 약관 및 이용자의 설정에 따름",
          ],
          [
            "Google LLC (Gmail)",
            "초대 대상자의 전자우편 주소, 초대한 회원의 이름, 팀의 명칭, 초대 링크 및 그 만료일",
            "초대한 회원 본인의 Gmail 주소로 팀 초대 메일 발송",
            "회원이 본인의 Gmail을 연결하고 초대 메일 보내기를 선택한 때. 선택하지 않은 초대는 Google에 전송하지 않습니다.",
            "해당 서비스의 약관 및 이용자의 설정에 따름. 보낸 메일은 초대한 회원의 Gmail 보낸편지함에 남습니다.",
          ],
        ),
        p("③ 음성 녹음은 어떠한 외부 서비스에도 제공하지 않습니다."),
        p(
          "④ 회사는 개인정보를 판매하거나 광고 목적으로 제3자에게 제공하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제6조(개인정보 처리업무의 위탁)",
      blocks: [
        p(
          "① 회사는 원활한 개인정보 업무 처리를 위하여 다음과 같이 개인정보 처리업무를 위탁하고 있습니다.",
        ),
        table(
          ["수탁자", "위탁하는 업무의 내용", "위탁하는 개인정보의 항목"],
          [
            HOSTING,
            "서비스 제공을 위한 서버 운영 및 데이터 보관",
            "서비스가 보관하는 자료 전부. 음성 녹음은 전사하는 동안에만 처리되며 보관되지 않습니다.",
          ],
          [
            "Google LLC (Gemini API)",
            "언어 모델을 이용한 발화 분류, 모호한 동의 발화의 판정, 발화의 맥락 해석, 회의 요약 작성 및 논의 누락 확인",
            "가림 처리된 회의 문장(해당 발화와 그 전후 문장). 모호한 동의 발화의 판정의 경우 분류에서 모호하다고 판단된 발화에 한합니다. 회의 요약 작성의 경우 분석에 동의한 화자의 가림 처리된 회의 문장 전체와 그 회의에서 추출된 결정 사항 및 액션 아이템",
          ],
          [
            blank("회의 간 연결 판단에 쓰는 언어 모델 제공자"),
            "언어 모델을 이용한 회의 간 연결 판단",
            "가림 처리된 회의 문장(해당 발화와 그 전후 문장)",
          ],
          [
            "Google LLC (Gemini API)",
            "언어 모델을 이용한 AI 비서 응답 생성",
            "이용자의 질문, 조회 결과의 요약, 조회된 항목의 제목과 본문의 앞부분(가림 처리된 발화 문장, 결정 사항 및 액션 아이템을 포함하며, 담당자 성명 및 기한이 포함될 수 있음), 인용한 회의의 제목 및 일시",
          ],
        ),
        p(
          "② 언어 모델을 이용한 발화 분류, 모호한 동의 발화의 판정, 발화의 맥락 해석 및 회의 요약 작성에서는 팀 구성원 명단에 등록된 성명을 전송 전에 식별할 수 없는 표지로 치환합니다. 명단에 등록되지 않은 성명은 치환되지 않으며, 그 밖의 기능(논의 누락 확인, 회의 간 연결 판단, AI 비서)에서는 문장 및 항목에 포함된 성명을 치환하지 않습니다.",
        ),
        p(
          "③ 언어 모델을 이용한 기능은 회사가 해당 기능을 활성화한 경우에 한하여 제공됩니다. 음성 녹음은 언어 모델에 전송하지 않습니다.",
        ),
        p(
          "④ 회사는 위탁계약을 체결할 때 「개인정보 보호법」 제26조에 따라 위탁업무 수행 목적 외 개인정보 처리 금지, 기술적·관리적 보호조치, 재위탁 제한, 수탁자에 대한 관리·감독, 손해배상 등 책임에 관한 사항을 계약서 등 문서에 명시하고, 수탁자가 개인정보를 안전하게 처리하는지를 감독합니다.",
        ),
        p(
          "⑤ 위탁업무의 내용이나 수탁자가 변경되는 경우에는 지체 없이 이 개인정보 처리방침을 통하여 공개합니다.",
        ),
      ],
    },
    {
      heading: "제7조(개인정보의 국외 이전)",
      blocks: [
        p(
          "① 회사는 서비스 제공을 위하여 다음과 같이 개인정보를 국외로 이전합니다.",
        ),
        table(
          [
            "이전받는 자",
            "이전되는 국가",
            "이전 일시 및 방법",
            "이전 항목",
            "이용 목적",
            "보유 및 이용 기간",
          ],
          [
            "Google LLC",
            "미국",
            "로그인하는 때, 캘린더에 일정을 등록하는 때, Gmail로 초대 메일을 보내는 때, 언어 모델을 이용한 기능을 사용하는 때에 정보통신망을 통하여 전송",
            "제5조 및 제6조에 기재한 항목",
            "본인 인증, 일정 등록, 팀 초대 메일의 발송, 언어 모델을 이용한 분석 및 AI 비서 응답 생성",
            "제5조 및 제6조에 기재한 기간",
          ],
          [
            "Slack Technologies, LLC",
            "미국",
            "확인 요청 또는 알림을 보내는 때에 정보통신망을 통하여 전송",
            "제5조에 기재한 항목",
            "확인 요청 및 알림의 전달",
            "제5조에 기재한 기간",
          ],
          [
            "Notion Labs, Inc.",
            "미국",
            "이용자가 항목을 확정하는 때에 정보통신망을 통하여 전송",
            "제5조에 기재한 항목",
            "팀의 업무 기록 작성",
            "제5조에 기재한 기간",
          ],
          [
            "Atlassian Pty Ltd",
            "호주, 미국",
            "이용자가 항목을 확정하는 때에 정보통신망을 통하여 전송",
            "제5조에 기재한 항목",
            "팀의 업무 항목 등록",
            "제5조에 기재한 기간",
          ],
        ),
        p(
          "② 이용자는 외부 서비스를 연결하지 않는 방법으로 해당 서비스로의 국외 이전을 거부할 수 있습니다. Google 로그인에 따른 이전을 거부하는 경우에는 서비스를 이용할 수 없습니다.",
        ),
      ],
    },
    {
      heading: "제8조(정보주체와 법정대리인의 권리·의무 및 행사 방법)",
      blocks: [
        p(
          "① 정보주체는 회사에 대하여 언제든지 개인정보의 열람, 정정, 삭제, 처리정지 요구 등의 권리를 행사할 수 있습니다.",
        ),
        p("② 정보주체는 다음의 권리를 서비스 내에서 직접 행사할 수 있습니다."),
        table(
          ["권리", "행사 방법 및 효과"],
          [
            "본인 발화 및 음성 특징정보의 삭제",
            "서비스 내에서 직접 삭제합니다. 회원 자격은 유지됩니다.",
          ],
          [
            "회원 탈퇴",
            "서비스 내에서 직접 탈퇴합니다. 발화, 음성 특징정보 및 회원 정보가 삭제됩니다.",
          ],
          [
            "가림 처리되지 않은 개인정보의 신고",
            "전사문에서 해당 부분을 선택하여 신고합니다. 즉시 가림 처리되며 분석 결과에도 반영됩니다.",
          ],
          [
            "본인 발화 비율의 열람",
            "본인만 열람할 수 있습니다. 팀 관리자를 포함한 다른 사람은 열람할 수 없습니다.",
          ],
        ),
        p(
          "③ 제2항 외의 권리 행사는 「개인정보 보호법 시행령」 제41조 제1항에 따라 서면, 전자우편 등을 통하여 할 수 있으며, 회사는 이에 대하여 지체 없이 조치합니다.",
        ),
        p(
          "④ 제1항에 따른 권리 행사는 정보주체의 법정대리인이나 위임을 받은 자 등 대리인을 통하여 할 수 있습니다. 이 경우 「개인정보 처리 방법에 관한 고시」 별지 제11호 서식에 따른 위임장을 제출하여야 합니다.",
        ),
        p(
          "⑤ 개인정보의 열람 및 처리정지 요구는 「개인정보 보호법」 제35조 제4항 및 제37조 제2항에 따라 정보주체의 권리가 제한될 수 있습니다.",
        ),
        p(
          "⑥ 개인정보의 정정 및 삭제 요구는 다른 법령에서 그 개인정보가 수집 대상으로 명시되어 있는 경우에는 그 삭제를 요구할 수 없습니다.",
        ),
        p(
          "⑦ 회사는 정보주체의 권리에 따른 열람, 정정·삭제, 처리정지의 요구를 받은 경우 요구를 한 자가 본인이거나 정당한 대리인인지를 확인합니다.",
        ),
        p(
          "⑧ 서비스에 가입하지 않은 회의 참석자는 제13조의 연락처를 통하여 본인 발화의 삭제 등 이 조의 권리를 행사할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "제9조(회의 녹음에 대한 동의)",
      blocks: [
        p(
          "① 음성을 업로드하거나 실시간 회의를 시작하는 이용자는 회의 참석자 전원에게 녹음 및 분석 사실을 알리고 동의를 받아야 합니다. 회사는 음성을 업로드하는 때에 그 확인을 별도로 받지 않습니다.",
        ),
        p(
          "② 해당 회의가 속한 팀의 구성원은 회의별로 참석자 전원이 동의하였음을 확인하여 기록할 수 있으며, 회사는 확인자와 확인 일시를 기록합니다. 확인은 음성을 업로드한 이용자가 아닌 팀 구성원도 할 수 있습니다.",
        ),
        p(
          "③ 회사는 동의가 확인되지 아니한 회의의 발화를 액션 아이템 및 결정 사항의 추출, 논의 누락의 탐지, 회의 간 연결에 이용하지 않습니다. 이 경우에도 해당 발화의 전사문은 가림 처리된 상태로 저장됩니다.",
        ),
      ],
    },
    {
      heading: "제10조(음성 특징정보의 처리)",
      blocks: [
        p(
          "① 회사는 회의의 화자를 구분하고, 본인이 확인한 음성을 이후의 회의에서 식별하기 위하여 음성 특징정보를 처리합니다.",
        ),
        p(
          "② 음성 특징정보는 음성에서 추출한 수치 정보이며, 회사는 음성 녹음 자체를 보관하지 않습니다.",
        ),
        p(
          "③ 정보주체는 제8조에 따라 언제든지 본인의 음성 특징정보를 삭제할 수 있습니다.",
        ),
        p(
          "④ 음성 특징정보는 회사가 해당 기능을 활성화한 환경에서만 저장하며, 기본값은 비활성입니다. 비활성인 환경에서는 회의별 음성 특징정보와 음성 프로필을 저장하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제11조(개인정보의 안전성 확보조치)",
      blocks: [
        p(
          "회사는 개인정보의 안전성 확보를 위하여 다음과 같은 조치를 하고 있습니다.",
        ),
        ol(
          "저장 전 가림 처리: 전사문에 포함된 전화번호, 전자우편 주소, 주민등록번호, 계좌번호 및 신용카드번호를 데이터베이스에 저장하기 전에 가림 처리합니다.",
          "음성 녹음의 미보관: 음성 녹음은 전사하는 동안에만 임시로 처리하고 전사 종료 즉시 삭제합니다.",
          "접근 통제: 회의 기록, 액션 아이템, 결정 사항, 논의 누락 리포트 및 회의 간 연결 정보는 해당 회의가 속한 팀의 구성원만 열람할 수 있도록 합니다.",
          "연동 정보의 암호화: 외부 서비스의 접근 토큰은 암호화하여 저장합니다.",
          "외부 전송의 검사: 외부로 전송되는 요청은 전송 직전에 검사하여, 가림 처리되지 않은 전화번호, 전자우편 주소 또는 계좌번호가 포함된 경우 전송하지 않습니다.",
          "기록의 최소화: 전사 내용은 로그 및 오류 메시지에 기록하지 않으며 식별자만 기록합니다.",
          "전송 구간의 암호화: 서비스는 암호화된 통신(HTTPS)으로 제공합니다.",
          "개발 절차: 모든 변경은 코드 검토와 자동 검사를 거치며, 개인정보 보호 규칙에 어긋나는 변경은 반영하지 않습니다.",
        ),
      ],
    },
    {
      heading:
        "제12조(개인정보 자동 수집 장치의 설치·운영 및 거부에 관한 사항)",
      blocks: [
        p(
          "① 회사는 로그인 절차를 진행하고 로그인 상태를 유지하기 위하여 쿠키(cookie)를 사용합니다.",
        ),
        p(
          "② 쿠키는 로그인 요청의 확인 및 세션의 식별에만 사용하며, 웹브라우저의 스크립트가 읽을 수 없도록 설정합니다.",
        ),
        p(
          "③ 이용자는 웹브라우저의 설정을 통하여 쿠키의 저장을 거부할 수 있습니다. 쿠키의 저장을 거부하는 경우 로그인이 필요한 서비스를 이용할 수 없습니다.",
        ),
        p(
          "④ 회사는 온라인 맞춤형 광고 등을 위한 행태정보를 수집·이용·제공하지 않으며, 이용 행태를 분석하는 외부 도구를 사용하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제13조(개인정보 보호책임자)",
      blocks: [
        p(
          "① 회사는 개인정보 처리에 관한 업무를 총괄해서 책임지고, 개인정보 처리와 관련한 정보주체의 불만 처리 및 피해 구제 등을 위하여 다음과 같이 개인정보 보호책임자를 지정하고 있습니다.",
        ),
        table(
          ["구분", "내용"],
          ["성명", blank("성명")],
          ["직책", blank("직책")],
          ["연락처", OFFICER_EMAIL],
        ),
        p(
          "② 정보주체는 회사의 서비스를 이용하면서 발생한 모든 개인정보 보호 관련 문의, 불만 처리, 피해 구제 등에 관한 사항을 개인정보 보호책임자에게 문의할 수 있으며, 회사는 지체 없이 답변하고 처리합니다.",
        ),
        p(
          "③ 정보주체는 「개인정보 보호법」 제35조에 따른 개인정보의 열람 청구를 제1항의 연락처로 할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "제14조(권익침해 구제 방법)",
      blocks: [
        p(
          "정보주체는 개인정보 침해로 인한 구제를 받기 위하여 아래의 기관에 분쟁 해결이나 상담 등을 신청할 수 있습니다.",
        ),
        table(
          ["기관", "전화", "누리집"],
          [
            "개인정보분쟁조정위원회",
            "(국번 없이) 1833-6972",
            "www.kopico.go.kr",
          ],
          ["개인정보침해신고센터", "(국번 없이) 118", "privacy.kisa.or.kr"],
          ["대검찰청", "(국번 없이) 1301", "www.spo.go.kr"],
          ["경찰청", "(국번 없이) 182", "ecrm.police.go.kr"],
        ),
      ],
    },
    {
      heading: "제15조(개인정보 처리방침의 변경)",
      blocks: [
        p(["① 이 개인정보 처리방침은 ", EFFECTIVE, "부터 적용됩니다."]),
        p(
          "② 회사는 이 개인정보 처리방침을 변경하는 경우 변경 내용과 시행일을 시행일 7일 전부터 서비스 화면에 공지합니다. 다만, 정보주체의 권리에 중대한 변경이 있는 경우에는 시행일 30일 전부터 공지합니다.",
        ),
      ],
    },
  ],
};

const TERMS: LegalDocument = {
  id: "terms",
  title: "서비스 이용약관",
  sections: [
    {
      chapter: "제1장 총칙",
      heading: "제1조(목적)",
      blocks: [
        p([
          "이 약관은 ",
          OPERATOR,
          '(이하 "회사")가 제공하는 회의 분석 서비스 Autune(이하 "서비스")의 이용과 관련하여 회사와 이용자 사이의 권리, 의무 및 책임 사항, 그 밖에 필요한 사항을 규정함을 목적으로 합니다.',
        ]),
      ],
    },
    {
      heading: "제2조(정의)",
      blocks: [
        p("이 약관에서 사용하는 용어의 뜻은 다음과 같습니다."),
        ol(
          '"서비스"란 회의 음성을 전사하고, 전사문에 포함된 개인정보를 가림 처리하며, 액션 아이템과 결정 사항을 추출하고, 논의되지 않은 항목과 이전 회의와의 연결을 제시하는 Autune의 기능 일체를 말합니다.',
          '"이용자"란 이 약관에 동의하고 서비스를 이용하는 자를 말합니다.',
          '"팀"이란 회의 기록을 공유하는 이용자의 단위를 말합니다.',
          '"콘텐츠"란 이용자가 업로드하거나 실시간 회의 기능으로 전송한 음성과, 그로부터 생성된 전사문 및 분석 결과를 말합니다.',
          '"외부 서비스"란 Slack, Notion, Jira, Google Calendar, Gmail 등 회사가 아닌 자가 제공하는 서비스로서 이용자 또는 팀이 서비스에 연결한 것을 말합니다.',
        ),
      ],
    },
    {
      heading: "제3조(약관의 게시와 개정)",
      blocks: [
        p(
          "① 회사는 이 약관의 내용을 이용자가 쉽게 알 수 있도록 서비스 화면에 게시합니다.",
        ),
        p(
          "② 회사는 「약관의 규제에 관한 법률」, 「개인정보 보호법」 등 관계 법령을 위배하지 않는 범위에서 이 약관을 개정할 수 있습니다.",
        ),
        p(
          "③ 회사가 약관을 개정하는 경우에는 적용일자 및 개정 사유를 명시하여 현행 약관과 함께 적용일자 7일 전부터 서비스 화면에 공지합니다. 다만, 이용자에게 불리한 내용으로 개정하는 경우에는 적용일자 30일 전부터 공지합니다.",
        ),
        p(
          "④ 회사가 제3항에 따라 공지하면서 적용일자까지 거부 의사를 표시하지 않으면 동의한 것으로 본다는 뜻을 명확히 알렸음에도 이용자가 거부 의사를 표시하지 않은 경우, 이용자는 개정 약관에 동의한 것으로 봅니다.",
        ),
        p(
          "⑤ 이용자는 개정 약관에 동의하지 않는 경우 제17조에 따라 이용계약을 해지할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "제4조(약관 외 준칙)",
      blocks: [
        p(
          "이 약관에서 정하지 아니한 사항은 관계 법령 및 회사의 개인정보 처리방침에 따릅니다.",
        ),
      ],
    },
    {
      chapter: "제2장 이용계약",
      heading: "제5조(이용계약의 성립)",
      blocks: [
        p(
          "① 이용계약은 이용자가 이 약관 및 개인정보 처리방침의 내용에 동의하고 Google 계정으로 로그인함으로써 성립합니다.",
        ),
        p("② 만 14세 미만인 자는 서비스를 이용할 수 없습니다."),
      ],
    },
    {
      heading: "제6조(계정의 관리)",
      blocks: [
        p(
          "① 서비스는 Google 계정으로 로그인하여 이용하며, 회사는 이용자의 비밀번호를 보관하지 않습니다.",
        ),
        p(
          "② 이용자는 자신의 계정을 선량한 관리자의 주의로 관리하여야 하며, 이를 제3자가 이용하게 하여서는 안 됩니다.",
        ),
        p(
          "③ 이용자는 자신의 계정이 도용되거나 제3자가 이용하고 있음을 알게 된 경우 즉시 회사에 알려야 합니다.",
        ),
      ],
    },
    {
      heading: "제7조(팀)",
      blocks: [
        p("① 이용자는 팀을 생성하여 서비스를 이용합니다."),
        p(
          "② 회의 기록, 액션 아이템, 결정 사항, 논의 누락 리포트 및 회의 간 연결 정보는 해당 회의가 속한 팀의 구성원만 열람할 수 있습니다.",
        ),
      ],
    },
    {
      chapter: "제3장 서비스의 이용",
      heading: "제8조(서비스의 내용)",
      blocks: [
        p("회사가 제공하는 서비스의 내용은 다음과 같습니다."),
        ol(
          "회의 음성의 전사 및 화자 구분",
          "전사문에 포함된 개인정보의 가림 처리",
          "액션 아이템 및 결정 사항의 추출",
          "회의에서 논의되지 않은 항목의 탐지",
          "이전 회의와의 연결 및 팀 단위 지표의 제공",
          "외부 서비스로의 결과 전달",
          "그 밖에 회사가 추가로 개발하여 제공하는 기능",
        ),
      ],
    },
    {
      heading: "제9조(자동 생성 결과의 성격)",
      blocks: [
        p(
          "① 전사문 및 분석 결과는 자동화된 방법으로 생성되며, 오류나 누락이 있을 수 있습니다.",
        ),
        p(
          "② 액션 아이템 및 결정 사항은 이용자가 확인하여 확정한 경우에 한하여 확정되며, 확정된 항목만 외부 서비스로 전달됩니다.",
        ),
        p(
          "③ 개인정보의 가림 처리는 자동화된 방법으로 이루어지며, 가림 처리되지 않은 부분이 있을 수 있습니다. 이용자는 해당 부분을 신고하여 즉시 가림 처리되도록 할 수 있습니다.",
        ),
        p(
          "④ 이용자는 전사문 및 분석 결과를 이용하기 전에 그 내용을 확인하여야 합니다.",
        ),
      ],
    },
    {
      heading: "제10조(콘텐츠의 보관 및 삭제)",
      blocks: [
        p(
          "① 음성 녹음은 전사가 종료되는 즉시 삭제되며, 회사는 음성 녹음의 원본을 다시 제공할 수 없습니다.",
        ),
        p(
          "② 회의 기록 및 분석 결과는 음성을 업로드하거나 실시간 회의를 시작한 날부터 보유 기간이 지나면 삭제되며, 삭제된 자료는 복구할 수 없습니다. 보유 기간은 90일을 기본으로 하며, 팀은 30일, 90일, 180일, 365일 중에서 정할 수 있습니다.",
        ),
        p(
          "③ 이용자는 언제든지 서비스 내에서 자신의 발화 및 음성 특징정보를 삭제할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "제11조(서비스의 변경 및 중단)",
      blocks: [
        p(
          "① 회사는 설비의 점검, 장애의 발생, 그 밖의 운영상 또는 기술상의 필요가 있는 경우 서비스의 전부 또는 일부를 변경하거나 중단할 수 있습니다.",
        ),
        p(
          "② 회사는 제1항의 경우 그 내용을 사전에 서비스 화면에 공지합니다. 다만, 사전에 공지할 수 없는 부득이한 사유가 있는 경우에는 사후에 공지할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "제12조(이용 요금)",
      blocks: [
        p("① 서비스는 무료로 제공됩니다."),
        p(
          "② 회사가 서비스의 전부 또는 일부를 유료로 전환하는 경우에는 제3조에 따라 사전에 공지하고 이용자의 동의를 받습니다.",
        ),
      ],
    },
    {
      heading: "제13조(외부 서비스 연동)",
      blocks: [
        p(
          "① 이용자 또는 팀이 외부 서비스를 연결한 경우, 확정된 액션 아이템 및 결정 사항, 팀 구성원이 보내기를 선택한 프로젝트별 회의록과 알림이 해당 외부 서비스로 전달됩니다. 이용자가 본인의 Gmail을 연결하고 초대할 때 메일 보내기를 선택한 경우, 초대 메일은 해당 이용자의 Gmail 주소로 발송됩니다.",
        ),
        p(
          "② 외부 서비스에 전달된 사본에는 해당 외부 서비스의 약관 및 정책이 적용됩니다.",
        ),
        p(
          "③ Notion 및 Jira에 전달된 액션 아이템 및 결정 사항의 사본은 해당 팀의 기록으로서, 서비스에서 회의가 삭제되거나 이용자가 탈퇴한 후에도 남습니다. 프로젝트별 회의록의 사본은 회의가 삭제되거나 보유 기간이 만료되면 회사가 해당 외부 서비스에 회수를 요청하며, 외부 서비스의 응답이 없는 경우 남을 수 있습니다.",
        ),
        p(
          "④ 회사는 외부 서비스의 장애 또는 정책 변경으로 연동이 중단된 경우 이에 대한 책임을 지지 않습니다. 다만, 회사의 고의 또는 중대한 과실이 있는 경우에는 그러하지 아니합니다.",
        ),
      ],
    },
    {
      chapter: "제4장 의무와 권리",
      heading: "제14조(회사의 의무)",
      blocks: [
        p(
          "① 회사는 관계 법령과 이 약관을 준수하며, 서비스를 안정적으로 제공하기 위하여 노력합니다.",
        ),
        p(
          "② 회사는 이용자의 개인정보를 보호하기 위하여 개인정보 처리방침을 공개하고 준수합니다.",
        ),
        p(
          "③ 회사는 콘텐츠를 서비스의 제공에 필요한 범위에서만 처리하며, 인공지능 모델의 학습에 이용하지 않습니다.",
        ),
        p(
          "④ 회사는 참석자별 발화 비율을 해당 참석자 본인 외의 자에게 제공하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제15조(이용자의 의무)",
      blocks: [
        p(
          "① 음성을 업로드하거나 실시간 회의를 시작하는 이용자는 회의 참석자 전원에게 녹음 및 분석 사실을 알리고 동의를 받아야 하며, 「통신비밀보호법」 등 관계 법령을 준수하여야 합니다.",
        ),
        p("② 이용자는 다음 각 호의 행위를 하여서는 안 됩니다."),
        ol(
          "업로드할 권한이 없는 음성 또는 법령이나 소속 조직의 규정이 금지하는 음성을 업로드하는 행위",
          "다른 사람의 발화량이나 발언 성향을 파악하거나 평가할 목적으로 서비스를 이용하는 행위",
          "자신이 속하지 않은 팀의 기록에 접근하거나 접근을 시도하는 행위",
          "다른 사람의 계정을 도용하는 행위",
          "서비스의 정상적인 운영을 방해하는 행위",
        ),
        p(
          "③ 이용자가 제1항을 위반하여 동의 없이 업로드한 음성으로 인하여 발생한 책임은 해당 이용자에게 있습니다.",
        ),
      ],
    },
    {
      heading: "제16조(콘텐츠에 대한 권리)",
      blocks: [
        p("① 콘텐츠에 대한 권리는 이용자 및 이용자가 속한 팀에 있습니다."),
        p(
          "② 이용자는 회사가 서비스를 제공하는 데 필요한 범위에서 콘텐츠를 처리하는 것을 허락합니다.",
        ),
        p("③ 서비스에 대한 저작권 및 그 밖의 지식재산권은 회사에 있습니다."),
      ],
    },
    {
      chapter: "제5장 계약의 종료 및 책임",
      heading: "제17조(이용계약의 해지)",
      blocks: [
        p(
          "① 이용자는 언제든지 서비스 내에서 탈퇴함으로써 이용계약을 해지할 수 있습니다.",
        ),
        p(
          "② 이용자가 탈퇴하면 해당 이용자의 발화, 음성 특징정보 및 회원 정보가 삭제됩니다. 외부 서비스에 전달된 사본은 제13조에 따릅니다.",
        ),
      ],
    },
    {
      heading: "제18조(이용의 제한)",
      blocks: [
        p(
          "① 회사는 이용자가 제15조를 위반한 경우 서비스의 이용을 제한하거나 이용계약을 해지할 수 있습니다.",
        ),
        p(
          "② 회사는 제1항에 따라 이용을 제한하거나 이용계약을 해지하는 경우 그 사유를 이용자에게 알립니다.",
        ),
      ],
    },
    {
      heading: "제19조(책임의 제한)",
      blocks: [
        p(
          "① 회사는 천재지변 또는 이에 준하는 불가항력으로 인하여 서비스를 제공할 수 없는 경우 서비스 제공에 관한 책임을 지지 않습니다.",
        ),
        p(
          "② 회사는 이용자의 귀책 사유로 인한 서비스 이용의 장애에 대하여 책임을 지지 않습니다.",
        ),
        p(
          "③ 회사는 전사문 및 분석 결과의 정확성이나 완전성을 보증하지 않으며, 이용자가 그 내용을 확인하지 않고 내린 판단에 대하여 책임을 지지 않습니다.",
        ),
        p(
          "④ 제1항부터 제3항까지의 규정은 회사의 고의 또는 중대한 과실로 인하여 발생한 손해에는 적용하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제20조(손해배상)",
      blocks: [
        p(
          "이용자가 이 약관을 위반하여 회사 또는 제3자에게 손해를 입힌 경우, 해당 이용자는 그 손해를 배상하여야 합니다.",
        ),
      ],
    },
    {
      heading: "제21조(준거법 및 재판관할)",
      blocks: [
        p(
          "① 이 약관의 해석 및 회사와 이용자 사이의 분쟁에 대하여는 대한민국 법을 적용합니다.",
        ),
        p(
          "② 서비스 이용과 관련하여 회사와 이용자 사이에 발생한 분쟁에 관한 소송은 「민사소송법」에 따른 관할 법원에 제기합니다.",
        ),
      ],
    },
    {
      heading: "부칙",
      blocks: [p(["이 약관은 ", EFFECTIVE, "부터 시행합니다."])],
    },
  ],
};

const SECURITY: LegalDocument = {
  id: "security",
  title: "정보보호 정책",
  sections: [
    {
      heading: "제1조(목적)",
      blocks: [
        p([
          "이 정책은 ",
          OPERATOR,
          '(이하 "회사")가 제공하는 회의 분석 서비스 Autune(이하 "서비스")에서 처리되는 정보를 보호하기 위하여 회사가 적용하는 기술적·관리적 보호조치를 정함을 목적으로 합니다.',
        ]),
      ],
    },
    {
      heading: "제2조(적용 범위)",
      blocks: [
        p(
          "이 정책은 서비스를 구성하는 정보시스템과 서비스에서 처리되는 음성 녹음, 전사문, 분석 결과 및 회원 정보에 적용합니다.",
        ),
      ],
    },
    {
      heading: "제3조(기본 원칙)",
      blocks: [
        p(
          "① 회사는 다음 각 호의 원칙을 서비스의 설계 및 구현 단계에서 적용합니다.",
        ),
        ol(
          "음성 녹음의 미보관: 음성 녹음은 전사가 종료되는 즉시 삭제하며 보관하지 않습니다.",
          "저장 전 가림 처리: 전사문에 포함된 개인정보는 데이터베이스에 저장하기 전에 가림 처리합니다.",
          "발화 비율의 본인 한정 제공: 참석자별 발화 비율은 해당 참석자 본인에게만 제공합니다.",
          "보유 기간 경과 시 삭제: 회의 기록 및 분석 결과는 보유 기간이 지나면 삭제합니다.",
        ),
        p(
          "② 회사는 제1항의 원칙을 약화하는 변경을 서비스에 반영하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제4조(인증 및 세션 관리)",
      blocks: [
        p(
          "① 서비스의 로그인은 Google 계정을 통한 인증으로만 이루어지며, 회사는 이용자의 비밀번호를 보관하지 않습니다.",
        ),
        p(
          "② 세션 정보는 웹브라우저의 스크립트가 읽을 수 없는 쿠키에 저장합니다.",
        ),
      ],
    },
    {
      heading: "제5조(접근 통제)",
      blocks: [
        p(
          "① 회의 기록, 액션 아이템, 결정 사항, 논의 누락 리포트 및 회의 간 연결 정보는 해당 회의가 속한 팀의 구성원만 열람할 수 있습니다.",
        ),
        p(
          "② 참석자별 발화 비율은 해당 참석자 본인에게만 제공하며, 팀 관리자를 포함한 다른 사람에게는 제공하지 않습니다. 회사는 참석자별 발화량을 저장하지 않습니다.",
        ),
        p("③ 결정 사항에 대한 개인별 찬반 정보에도 제2항을 준용합니다."),
      ],
    },
    {
      heading: "제6조(암호화)",
      blocks: [
        p("① 서비스는 암호화된 통신(HTTPS)으로 제공합니다."),
        p(
          "② Slack, Notion, Jira 및 Google Calendar의 접근 토큰은 암호화하여 저장합니다.",
        ),
      ],
    },
    {
      heading: "제7조(음성 녹음의 처리)",
      blocks: [
        p(
          "① 음성 녹음은 전사하는 동안에만 임시 경로에 두며, 전사의 성공, 실패, 취소를 불문하고 종료 즉시 삭제합니다.",
        ),
        p(
          "② 음성 녹음은 영구 저장소, 데이터베이스 및 로그에 기록하지 않습니다.",
        ),
        p(
          "③ 임시 파일은 소유자만 읽을 수 있는 권한으로 생성하며, 파일의 경로를 메시지나 로그에 기록하지 않습니다.",
        ),
        p(
          "④ 삭제되지 않고 남은 임시 파일이 있는지 1시간마다 점검하여 삭제합니다.",
        ),
      ],
    },
    {
      heading: "제8조(개인정보의 가림 처리 및 외부 전송의 통제)",
      blocks: [
        p(
          "① 전사문에 포함된 전화번호, 전자우편 주소, 주민등록번호, 계좌번호 및 신용카드번호는 데이터베이스에 저장하기 전에 가림 처리합니다. 가림 처리 전의 원문은 저장소, 로그 및 외부 서비스 어느 곳에도 전달하지 않습니다.",
        ),
        p(
          "② 이용자가 가림 처리되지 않은 개인정보를 신고한 경우 해당 부분을 즉시 가림 처리하고 분석 결과에 반영합니다.",
        ),
        p(
          "③ 외부로 전송되는 요청은 전송 직전에 검사하며, 가림 처리되지 않은 전화번호, 전자우편 주소 또는 계좌번호가 포함된 요청은 전송하지 않습니다.",
        ),
        p("④ 음성 녹음은 어떠한 외부 서비스에도 전송하지 않습니다."),
      ],
    },
    {
      heading: "제9조(기록의 관리)",
      blocks: [
        p(
          "① 전사 내용은 어떠한 수준의 로그에도 기록하지 않으며, 로그에는 식별자만 기록합니다.",
        ),
        p("② 오류 메시지에도 전사 내용을 포함하지 않습니다."),
      ],
    },
    {
      heading: "제10조(자동화된 처리에 대한 확인)",
      blocks: [
        p(
          "① 액션 아이템 및 결정 사항은 이용자가 확정한 경우에 한하여 외부 서비스로 전달합니다.",
        ),
        p(
          "② AI 비서가 제안한 변경은 승인 권한이 있는 이용자의 승인을 거쳐 실행합니다.",
        ),
      ],
    },
    {
      heading: "제11조(개발 보안)",
      blocks: [
        p(
          "① 서비스에 대한 모든 변경은 코드 검토와 자동 검사를 거쳐 반영합니다.",
        ),
        p(
          "② 코드 검토는 개인정보 보호 규칙에 관한 점검표에 따라 수행하며, 점검표에 어긋나는 변경은 반영하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제12조(취약점의 신고)",
      blocks: [
        p([
          "① 서비스의 보안 취약점을 발견한 자는 다음 연락처로 신고할 수 있습니다: ",
          blank("보안 문의 전자우편 주소"),
        ]),
        p(
          "② 회사는 신고를 접수한 날부터 3영업일 이내에 접수 사실을 회신합니다.",
        ),
      ],
    },
    {
      heading: "제13조(침해사고의 대응)",
      blocks: [
        p(
          '① 회사는 개인정보의 분실·도난·유출(이하 "유출 등")을 알게 된 때에는 「개인정보 보호법」 제34조에 따라 72시간 이내에 해당 정보주체에게 다음 각 호의 사항을 알립니다.',
        ),
        ol(
          "유출 등이 된 개인정보의 항목",
          "유출 등이 된 시점과 그 경위",
          "유출 등으로 인하여 발생할 수 있는 피해를 최소화하기 위하여 정보주체가 할 수 있는 방법 등에 관한 정보",
          "회사의 대응 조치 및 피해 구제 절차",
          "정보주체에게 피해가 발생한 경우 신고 등을 접수할 수 있는 담당 부서 및 연락처",
        ),
        p(
          "② 회사는 유출 등이 「개인정보 보호법」 제34조 제3항에서 정한 요건에 해당하는 경우 개인정보보호위원회 또는 한국인터넷진흥원에 신고합니다.",
        ),
        p(
          "③ 회사는 유출 등으로 인한 피해를 최소화하기 위한 대책을 마련하고 필요한 조치를 합니다.",
        ),
      ],
    },
    {
      heading: "제14조(정책의 개정)",
      blocks: [
        p(
          "회사는 이 정책을 개정하는 경우 개정 내용과 시행일을 서비스 화면에 공지합니다.",
        ),
      ],
    },
    {
      heading: "부칙",
      blocks: [p(["이 정책은 ", EFFECTIVE, "부터 시행합니다."])],
    },
  ],
};

export const LEGAL_DOCUMENTS: readonly LegalDocument[] = [
  PRIVACY,
  TERMS,
  SECURITY,
];
