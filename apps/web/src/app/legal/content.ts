/**
 * The privacy policy, the terms of use and the security notice, as data.
 *
 * DRAFT, not reviewed by a lawyer. Every `blank(...)` is a value the operator
 * has not settled: with a second argument it is a proposal, without one it is
 * unknown. The page marks each as "확정 전", so nothing unsettled reads as a
 * commitment. Do not publish while one is left.
 *
 * The text states what the code does today and nothing it does not do yet. It
 * deliberately does not say that an attendee can withdraw consent (only a
 * per-meeting confirmation exists), that the team dashboard is limited to team
 * members (its routes do not check the caller yet), or that an outside language
 * model is used on real meetings (undecided). The language-model row lists what
 * leaves for EVERY caller on main -- B's classifier and resolver, C's verifier
 * and relation assistant, D's judge, the agent's router and Research -- not for
 * the one that sends least; a new caller means rereading that row. Keep it that
 * way when editing:
 * a sentence here is a promise to the person who signs in.
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
  heading: string;
  blocks: readonly Block[];
}

export interface LegalDocument {
  id: "privacy" | "terms" | "security" | "voice_features" | "overseas_transfer";
  title: string;
  lead: Line;
  sections: readonly Section[];
  /** Shown under the last section, e.g. the effective date. */
  closing?: Line;
}

const blank = (label: string, proposed?: string): Blank => ({
  blank: label,
  proposed,
});
const b = (text: string): Strong => ({ strong: text });
const p = (line: Line): Block => ({ kind: "paragraph", line });
const ul = (...items: Line[]): Block => ({ kind: "list", items });
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
const EFFECTIVE = blank("시행일");

const PRIVACY: LegalDocument = {
  id: "privacy",
  title: "개인정보 처리방침",
  lead: [
    OPERATOR,
    '(이하 "운영자")는 회의 녹음을 전사하고 분석하는 서비스 Autune을 제공하며, 아래와 같이 개인정보를 처리합니다.',
  ],
  sections: [
    {
      heading: "1. 처리하는 개인정보와 목적",
      blocks: [
        table(
          ["구분", "항목", "처리 목적"],
          [
            "계정",
            "이메일, 표시 이름, Google 계정 식별자, 마지막 로그인 시각",
            "Google 로그인, 본인 확인",
          ],
          ["팀", "소속 팀, 팀 안 역할", "회의 기록의 열람 범위 결정"],
          [
            "회의",
            "제목, 시작 시각, 길이, 참석자 표시(화자 이름표, 역할)",
            "회의 기록 제공",
          ],
          [
            "녹음 파일",
            "업로드한 음성",
            "전사. 전사가 끝나면 삭제하며 저장하지 않음",
          ],
          [
            "전사문",
            "발화 내용(마스킹된 텍스트), 화자, 발화 시작·끝 시각",
            "회의 기록, 분석의 근거",
          ],
          [
            "음성 특징",
            "목소리를 수치로 바꾼 벡터",
            "화자 구분, 본인이 확인한 목소리의 재식별. 운영자가 이 기능을 켠 환경에서만 저장하며 기본은 꺼져 있음",
          ],
          [
            "분석 결과",
            "액션 아이템(담당자, 기한 포함), 결정, 논의되지 않은 항목, 회의에서 나온 주제 이름(발화에서 잘라 낸 말이라 사람 이름이 들어갈 수 있음), 참석자별로 각 주제에 발언했는지 여부(했다·안 했다만 기록하며 팀이 리포트에서 봄), 이전 회의와의 연결, 팀 단위 지표",
            "서비스의 핵심 기능 제공",
          ],
          [
            "동의 기록",
            "회의별 동의 확인자와 확인 시각",
            "녹음 동의 사실의 증빙",
          ],
          [
            "연동",
            "Slack, Notion, Jira, Google Calendar 연결 토큰과 설정",
            "이용자가 연결한 도구로 결과 전달",
          ],
        ),
        ul(
          [
            b("발화 비율:"),
            " 회의에서 본인이 말한 비중은 계산해 본인에게만 전달하고, 사람별 발화량은 저장하지 않습니다. 주제별 발언 여부는 양이 아니라 했다·안 했다만 남깁니다.",
          ],
          [
            b("민감한 식별정보:"),
            " 전사문 속 전화번호, 이메일, 주민등록번호, 계좌번호, 카드번호는 저장 전에 가립니다. 사람 이름은 가리지 않습니다.",
          ],
          [
            b("학습에 쓰지 않음:"),
            " 녹음과 전사문을 모델 학습용으로 보관하거나 사용하지 않습니다.",
          ],
        ),
      ],
    },
    {
      heading: "2. 보유 기간과 파기",
      blocks: [
        p(
          "녹음 파일은 전사가 끝나는 즉시 삭제하고, 회의 기록과 분석 결과는 녹음을 올리거나 실시간 회의를 시작한 날로부터 90일이 지나면 삭제합니다. 회의가 열린 날이 아니라 기록이 만들어진 날부터 셉니다.",
        ),
        table(
          ["대상", "보유 기간", "파기 방법"],
          [
            "녹음 파일",
            "전사가 끝날 때까지",
            "성공, 실패, 취소 어느 경우에도 즉시 삭제. 남은 파일은 1시간마다 점검해 삭제",
          ],
          [
            "회의 기록, 전사문, 분석 결과",
            "녹음을 올리거나 실시간 회의를 시작한 날로부터 90일 (팀별로 조정 가능)",
            "1시간마다 만료된 회의를 삭제. 회의에 딸린 모든 자료가 함께 삭제됨",
          ],
          ["회의별 음성 특징", "해당 회의와 같음", "회의와 함께 삭제"],
          [
            "본인이 확인한 음성 프로필",
            "그 사람이 나온 회의가 남아 있는 동안",
            "마지막 회의가 만료되면 삭제",
          ],
          ["계정, 연동 토큰", "탈퇴할 때까지", "탈퇴 시 삭제"],
        ),
        ul(
          [
            b("음성 특징:"),
            " 회의별 음성 특징과 음성 프로필은 운영자가 이 기능을 켠 환경에서만 저장합니다. 기본은 꺼져 있고, 꺼진 환경에서는 둘 다 저장하지 않습니다.",
          ],
          [
            b("삭제는 실제 삭제입니다."),
            " 숨김 처리나 내용이 남는 표시를 두지 않습니다.",
          ],
          [
            b("본인 발화 삭제:"),
            ' 이용자는 언제든지 자신의 발화와 음성 특징을 지울 수 있습니다. 그 발화에서 나온 액션 아이템과 결정, 논의 누락 분석에서는 본인의 말이 지워지고, 팀의 업무 항목 자체는 "삭제된 발화에서 만든 항목"으로 남습니다. 회의 간 연결에 남은 결정 문장과 팀 리포트의 글에는 아직 반영되지 않아 본인의 말이 남을 수 있습니다.',
          ],
          [b("탈퇴:"), " 발화, 음성 특징, 계정을 삭제합니다."],
          [
            b("외부 도구에 보낸 사본:"),
            " 팀이 연결한 Notion과 Jira에 보낸 항목은 그 팀의 기록이므로 보유 기간 만료나 탈퇴로 삭제되지 않습니다. 본인 발화를 삭제하거나 내용을 정정하면 그 사본의 문장도 따라 바뀝니다. 본인 Google 캘린더에 넣은 일정은 삭제를 시도하지만, Google이 응답하지 않으면 남을 수 있습니다.",
          ],
        ),
      ],
    },
    {
      heading: "3. 외부로 나가는 정보",
      blocks: [
        p(
          "Autune 밖으로 나가는 것은 마스킹된 텍스트뿐이며, 기능에 필요한 만큼만 보냅니다. 녹음 파일은 어디에도 보내지 않습니다.",
        ),
        table(
          ["받는 곳", "나가는 정보", "나가는 때"],
          [
            "Google (로그인)",
            "로그인 요청. Google에서 이메일과 이름을 받음",
            "로그인할 때",
          ],
          [
            "Slack",
            "본인에게: 확인 요청 메시지(본인이 한 말 한 줄), 본인의 발화 비율, 승인을 기다리는 제안의 건수. 팀 채널에: 회의 리포트, 이전 회의와 이어진 주제 이름, 바뀐 결정의 문장 일부와 주제 이름, 회의 전 브리핑(이전 회의 요약과 다룰 안건)",
            "팀이 Slack을 연결한 경우",
          ],
          [
            "Notion",
            "확정된 액션 아이템과 결정(내용, 담당자, 기한, 상태, 회의 제목)",
            "팀이 Notion을 연결하고, 사람이 항목을 확정한 뒤",
          ],
          [
            "Jira",
            "확정된 액션 아이템(내용, 기한, 담당자의 Jira 계정)",
            "팀이 Jira를 연결하고, 사람이 항목을 확정한 뒤",
          ],
          [
            "Google Calendar",
            "액션 아이템의 내용과 날짜. 참석자와 전사 내용은 넣지 않음",
            "담당자가 본인 캘린더를 연결한 경우",
          ],
          [
            "언어 모델 제공자 (Google Gemini 등)",
            "마스킹된 회의 문장(발화와 그 앞뒤 문장), AI 비서에 입력한 질문, 비서가 답을 만들 때 쓰는 항목의 요약과 제목(담당자 이름과 기한이 들어갈 수 있음), 인용한 회의의 제목과 날짜. 녹음 파일은 보내지 않음",
            "운영자가 언어 모델 연결을 설정한 환경에서만",
          ],
          [blank("호스팅 사업자"), "서비스가 보관하는 모든 자료", "상시"],
        ),
        ul(
          [
            b("연동 도구로는 이용자가 연결했을 때만 나갑니다."),
            " Slack, Notion, Jira, Google Calendar로 나가는 정보는 팀이나 본인이 직접 연결했을 때만 전송됩니다.",
          ],
          [
            b("이름:"),
            " 언어 모델로 발화를 분류하거나 약속의 맥락을 풀 때는 팀 명단에 있는 이름을 보내기 전에 자리표시자로 바꿉니다. 명단에 없는 이름은 그대로 나갈 수 있습니다. 그 밖의 기능(논의 누락 확인, 회의 간 연결, AI 비서)에서는 문장과 항목에 담긴 이름이 바뀌지 않고 나갑니다.",
          ],
          [b("오류 추적·분석 도구:"), " 현재 사용하지 않습니다."],
          [b("판매·광고 목적 제공:"), " 하지 않습니다."],
        ),
      ],
    },
    {
      heading: "4. 이용자의 권리와 행사 방법",
      blocks: [
        p(
          "이용자는 자신의 개인정보를 열람, 정정, 삭제하거나 처리를 멈춰 달라고 언제든지 요구할 수 있습니다.",
        ),
        table(
          ["하고 싶은 것", "방법"],
          [
            "내 발화와 음성 특징 삭제",
            "서비스 안에서 직접 삭제. 계정은 유지됨",
          ],
          ["탈퇴", "서비스 안에서 직접 탈퇴. 발화, 음성 특징, 계정이 삭제됨"],
          [
            "가려지지 않은 개인정보 신고",
            "전사문에서 해당 부분을 선택해 신고. 즉시 가려지고 분석 결과에도 반영됨",
          ],
          [
            "내 발화 비율 확인",
            "본인만 볼 수 있음. 다른 사람은 관리자를 포함해 볼 수 없음",
          ],
          [
            "그 밖의 열람, 정정, 처리 정지 요구",
            [
              blank("문의 이메일"),
              "로 요청. ",
              blank("답변 기한", "10일"),
              " 안에 답변",
            ],
          ],
        ),
      ],
    },
    {
      heading: "5. 녹음 동의",
      blocks: [
        ul(
          "참석자 모두에게 녹음과 분석을 알리고 동의를 받는 것은 녹음을 올리는 이용자의 의무입니다(이용약관 제5조). 서비스는 녹음을 올릴 때 그 확인을 따로 받지 않습니다.",
          "회의가 속한 팀의 구성원은 회의마다 참석자 모두가 동의했음을 확인해 둘 수 있고, 운영자는 확인한 사람과 시각을 기록합니다. 확인은 올린 사람이 아닌 팀 구성원도 할 수 있습니다.",
          "동의가 확인되지 않은 회의의 발화는 전사문으로 저장되지만, 액션 아이템과 결정 추출, 논의 누락 확인, 회의 간 연결에는 쓰지 않습니다.",
        ),
      ],
    },
    {
      heading: "6. 안전성 확보 조치",
      blocks: [
        p(
          '저장 전 마스킹, 녹음 미보관, 연동 토큰 암호화, 전사 내용을 로그에 남기지 않는 것이 핵심입니다. 자세한 내용은 아래 "보안" 장에 적었습니다.',
        ),
      ],
    },
    {
      heading: "7. 개인정보 보호책임자와 문의처",
      blocks: [
        ul(
          ["개인정보 보호책임자: ", blank("성명, 직책"), ", ", blank("이메일")],
          "권리 침해에 대한 구제는 개인정보침해신고센터(국번 없이 118), 개인정보분쟁조정위원회(1833-6972), 대검찰청(1301), 경찰청(182)에 신청할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "8. 방침의 변경",
      blocks: [
        p([
          "이 방침을 바꾸면 시행 ",
          blank("고지 기간", "7일"),
          " 전에 서비스 안에서 알립니다.",
        ]),
      ],
    },
  ],
  closing: ["시행일: ", EFFECTIVE],
};

const TERMS: LegalDocument = {
  id: "terms",
  title: "이용약관",
  lead: [
    "이 약관은 ",
    OPERATOR,
    '(이하 "운영자")가 제공하는 Autune의 이용 조건을 정합니다. 로그인해 서비스를 이용하면 이 약관과 개인정보 처리방침에 동의한 것으로 봅니다.',
  ],
  sections: [
    {
      heading: "제1조 용어",
      blocks: [
        ul(
          [
            b("서비스:"),
            " 회의 녹음을 전사하고, 개인정보를 가리고, 액션 아이템과 결정을 뽑고, 논의되지 않은 항목과 이전 회의와의 연결을 보여 주는 Autune의 기능 전체.",
          ],
          [b("이용자:"), " 이 약관에 동의하고 서비스를 쓰는 사람."],
          [
            b("팀:"),
            ' 회의 기록을 함께 보는 이용자의 묶음. 무엇을 팀 구성원만 열 수 있는지는 아래 "보안" 장의 열람 범위에 적었습니다.',
          ],
          [
            b("콘텐츠:"),
            " 이용자가 올린 녹음과, 그로부터 만들어진 전사문과 분석 결과.",
          ],
        ),
      ],
    },
    {
      heading: "제2조 약관의 효력과 변경",
      blocks: [
        ol(
          [
            "운영자는 약관을 바꿀 수 있으며, 바꾸는 경우 시행 ",
            blank("고지 기간", "7일"),
            " 전에 서비스 안에서 알립니다. 이용자에게 불리한 변경은 ",
            blank("불리한 변경의 고지 기간", "30일"),
            " 전에 알립니다.",
          ],
          "바뀐 약관에 동의하지 않는 이용자는 탈퇴할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "제3조 계정",
      blocks: [
        ol(
          "서비스는 Google 계정으로 로그인해 이용합니다.",
          "이용자는 자신의 계정을 스스로 관리하며, 다른 사람에게 쓰게 해서는 안 됩니다.",
          [blank("이용 연령 제한", "만 14세 미만은 이용할 수 없습니다.")],
        ),
      ],
    },
    {
      heading: "제4조 서비스의 내용과 한계",
      blocks: [
        ol(
          "전사문과 분석 결과는 자동으로 만들어지며 틀리거나 빠질 수 있습니다. 액션 아이템과 결정은 사람이 확인한 뒤에만 확정되고 외부 도구로 전달됩니다.",
          "개인정보 가림은 자동으로 이루어지며 놓칠 수 있습니다. 이용자는 놓친 부분을 신고해 즉시 가릴 수 있습니다.",
          "녹음 파일은 전사 후 삭제되므로 운영자는 원본 녹음을 다시 제공할 수 없습니다.",
          "회의 기록과 분석 결과는 보유 기간(기본 90일)이 지나면 삭제되며 복구할 수 없습니다.",
          [
            "서비스는 현재 ",
            blank("제공 형태", "무료 / 시범 운영"),
            "으로 제공됩니다.",
          ],
        ),
      ],
    },
    {
      heading: "제5조 이용자의 의무",
      blocks: [
        ol(
          [
            b("참석자 동의."),
            " 녹음을 올리는 이용자는 참석자 모두에게 녹음과 분석 사실을 알리고 동의를 받아야 합니다. 동의 없이 올린 녹음으로 생긴 책임은 올린 이용자에게 있습니다.",
          ],
          "올릴 권한이 없는 녹음, 법령이나 소속 조직의 규정이 금지하는 녹음을 올려서는 안 됩니다.",
          "다른 사람의 발화량이나 발언 성향을 알아내거나 평가하려는 목적으로 서비스를 쓰지 않습니다. 서비스는 그런 수치를 본인 외에는 제공하지 않습니다.",
          "서비스의 정상 운영을 방해하거나, 다른 팀의 기록에 접근하려 해서는 안 됩니다.",
        ),
      ],
    },
    {
      heading: "제6조 콘텐츠에 대한 권리",
      blocks: [
        ol(
          "콘텐츠에 대한 권리는 이용자와 그 팀에 있습니다.",
          "운영자는 서비스를 제공하는 데 필요한 범위에서만 콘텐츠를 처리하며, 모델 학습에 쓰지 않습니다.",
        ),
      ],
    },
    {
      heading: "제7조 외부 도구 연동",
      blocks: [
        ol(
          "이용자나 팀이 Slack, Notion, Jira, Google Calendar를 연결하면 확정된 항목이 그 도구로 전달됩니다.",
          "연결한 도구에 전달된 사본은 그 도구의 약관과 정책을 따르며, Notion과 Jira의 사본은 Autune에서 회의가 삭제되어도 팀의 기록으로 남습니다.",
          "외부 도구의 장애나 정책 변경으로 연동이 멈춘 경우 운영자는 책임지지 않습니다.",
        ),
      ],
    },
    {
      heading: "제8조 서비스의 변경과 중단",
      blocks: [
        p(
          "운영자는 점검, 장애, 운영상 필요로 서비스를 바꾸거나 멈출 수 있으며, 미리 알 수 있는 경우 사전에 알립니다.",
        ),
      ],
    },
    {
      heading: "제9조 책임의 제한",
      blocks: [
        ol(
          "운영자는 분석 결과의 정확성이나 완전성을 보증하지 않으며, 이용자가 결과를 확인하지 않고 내린 판단에 책임지지 않습니다.",
          "운영자의 고의나 중대한 과실로 생긴 손해에는 이 조항을 적용하지 않습니다.",
        ),
      ],
    },
    {
      heading: "제10조 이용 종료",
      blocks: [
        ol(
          "이용자는 언제든지 서비스 안에서 탈퇴할 수 있습니다.",
          "이용자가 제5조를 어기면 운영자는 이용을 제한하거나 종료할 수 있습니다.",
        ),
      ],
    },
    {
      heading: "제11조 준거법과 분쟁",
      blocks: [
        p([
          "이 약관은 대한민국 법을 따르며, 분쟁은 ",
          blank("관할 법원"),
          "에서 다룹니다.",
        ]),
      ],
    },
  ],
  closing: ["시행일: ", EFFECTIVE],
};

const SECURITY: LegalDocument = {
  id: "security",
  title: "보안",
  lead: "Autune은 회의 녹음이 조직에서 가장 민감한 자료라는 전제로, 아래 네 가지를 정책이 아니라 코드의 제약으로 지킵니다.",
  sections: [
    {
      heading: "데이터 처리 원칙",
      blocks: [
        table(
          ["원칙", "어떻게 지키는가"],
          [
            "녹음은 보관하지 않는다",
            "녹음 파일은 전사하는 동안만 임시 경로에 있고, 성공·실패·취소 어느 경우에도 즉시 삭제됩니다. 저장소, 데이터베이스, 로그 어디에도 남기지 않으며 남은 파일은 1시간마다 점검해 지웁니다.",
          ],
          [
            "저장 전에 가린다",
            "전화번호, 이메일, 주민등록번호, 계좌번호, 카드번호를 데이터베이스에 쓰기 전에 가립니다. 가리지 않은 원문은 어떤 저장소에도, 로그에도, 외부 서비스에도 가지 않습니다.",
          ],
          [
            "발화 비율은 본인만 본다",
            "한 사람이 회의에서 말한 비중은 그 사람에게만 전달합니다. 팀 관리자를 포함해 다른 누구에게도 보여 주지 않으며, 사람별 발화량을 저장하지 않습니다. 결정에 대한 개인별 찬반도 같은 규칙을 따릅니다.",
          ],
          [
            "기한이 지나면 지운다",
            "회의 기록과 분석 결과는 기본 90일 뒤 삭제됩니다. 삭제는 실제 삭제이며, 이용자는 그 전에도 자신의 자료를 지울 수 있습니다.",
          ],
        ),
        p(
          "이 원칙을 약하게 만드는 변경은 그것이 어떤 기능을 가능하게 하든 코드 리뷰에서 거절됩니다.",
        ),
      ],
    },
    {
      heading: "보호 조치",
      blocks: [
        table(
          ["영역", "조치"],
          [
            "로그인",
            "Google 계정 로그인만 사용하며 비밀번호를 보관하지 않습니다. 세션은 스크립트가 읽을 수 없는 쿠키에 담습니다.",
          ],
          [
            "열람 범위",
            "회의 기록, 액션 아이템, 논의 누락 리포트, 회의 간 연결은 그 회의가 속한 팀의 구성원만 열 수 있습니다.",
          ],
          [
            "연동 토큰",
            "Slack, Notion, Jira, Google Calendar의 연결 토큰은 암호화해 저장합니다.",
          ],
          [
            "외부 전송",
            "밖으로 나가는 모든 요청은 전송 직전에 검사해, 가려지지 않은 전화번호·이메일·계좌번호가 있으면 보내지 않습니다.",
          ],
          [
            "로그",
            "전사 내용은 어떤 수준의 로그에도 남기지 않고 식별자만 기록합니다. 오류 메시지에도 전사 내용을 넣지 않습니다.",
          ],
          [
            "녹음 임시 파일",
            "소유자만 읽을 수 있는 권한으로 만들고, 파일 경로를 메시지나 로그에 싣지 않습니다.",
          ],
          [
            "사람의 확인",
            "액션 아이템과 결정은 사람이 확정한 뒤에만 외부 도구로 나갑니다. AI 비서가 제안한 변경도 승인을 거쳐야 실행됩니다.",
          ],
          [
            "개발 절차",
            "모든 변경은 코드 리뷰와 자동 검사를 거칩니다. 개인정보 규칙을 어기는 변경은 거절하는 점검표가 있습니다.",
          ],
        ),
        ul([
          b("전송 구간:"),
          " ",
          blank("서비스 주소"),
          "는 HTTPS로만 제공합니다. 저장 구간(데이터베이스, 백업)의 암호화: ",
          blank("호스팅 환경에 따라 기재"),
          ".",
        ]),
      ],
    },
    {
      heading: "취약점 신고와 사고 대응",
      blocks: [
        ul(
          [
            "취약점을 발견하면 ",
            blank("보안 문의 이메일"),
            "로 알려 주세요. ",
            blank("답변 기한", "3영업일"),
            " 안에 답변합니다.",
          ],
          [
            "개인정보 유출을 확인하면 지체 없이 해당 이용자에게 알리고, 법령이 정한 기한 안에 관계 기관에 신고합니다. ",
            blank("통지 기한과 절차"),
          ],
        ),
      ],
    },
  ],
};

const REFUSAL = blank(
  "동의 거부 시 처리",
  "현재는 이 항목만 따로 끄는 설정이 없어, 동의하지 않으면 서비스를 이용할 수 없습니다",
);

/**
 * The two consents asked for apart from the policy (decided with the user,
 * 2026-10-02). Each repeats facts the policy already states -- sections 1, 2
 * and 3 above -- in the shape a separate consent needs: what, why, how long,
 * and the right to refuse. Change a fact there, change it here.
 *
 * What happens on refusal is unsettled and marked so: the service has no
 * per-person switch for either, which is a fact about the code, and whether a
 * consent may be required on that ground is a question for the legal review.
 */
const VOICE_CONSENT: LegalDocument = {
  id: "voice_features",
  title: "음성 특징정보 수집·이용 동의",
  lead: "목소리를 수치로 바꾼 정보는 사람을 알아볼 수 있는 정보여서, 개인정보 처리방침과 따로 동의를 받습니다.",
  sections: [
    {
      heading: "수집·이용 내용",
      blocks: [
        table(
          ["구분", "내용"],
          [
            "항목",
            "목소리를 수치로 바꾼 벡터. 회의마다 만드는 음성 특징과, 본인이 확인한 음성 프로필",
          ],
          [
            "목적",
            "한 회의 안에서 화자를 구분하고, 본인이 확인한 목소리를 다음 회의에서 다시 알아보는 것",
          ],
          [
            "보유 기간",
            "회의별 음성 특징은 그 회의와 함께 삭제합니다. 음성 프로필은 그 사람이 나온 마지막 회의가 만료되면 삭제합니다. 본인은 그 전에도 언제든지 지울 수 있습니다",
          ],
          [
            "현재 상태",
            "운영자가 이 기능을 켠 환경에서만 저장하며 기본은 꺼져 있습니다. 꺼진 환경에서는 저장하지 않습니다",
          ],
        ),
      ],
    },
    {
      heading: "동의를 거부할 권리",
      blocks: [p(["이 동의를 거부할 수 있습니다. ", REFUSAL, "."])],
    },
  ],
};

const ABROAD = blank("이전 국가", "미국");

const OVERSEAS_CONSENT: LegalDocument = {
  id: "overseas_transfer",
  title: "개인정보 국외 이전 동의",
  lead: "아래 서비스는 국외 사업자가 운영하므로, 그쪽으로 나가는 정보는 국외로 이전됩니다. 무엇이 언제 나가는지는 개인정보 처리방침의 \"외부로 나가는 정보\"와 같습니다.",
  sections: [
    {
      heading: "이전 내용",
      blocks: [
        table(
          ["이전받는 자", "이전되는 정보", "이전 시점과 방법", "이용 목적"],
          [
            ["언어 모델 제공자 (Google Gemini 등), ", ABROAD],
            "마스킹된 회의 문장, AI 비서에 입력한 질문, 답을 만들 때 쓰는 항목의 요약과 제목, 인용한 회의의 제목과 날짜",
            "운영자가 언어 모델 연결을 설정한 환경에서, 분석하거나 질문할 때 네트워크로 전송",
            "발화 분류, 맥락 해석, 질문에 대한 답 작성",
          ],
          [
            ["Slack, ", ABROAD],
            "확인 요청 메시지, 본인의 발화 비율, 승인을 기다리는 제안의 건수, 팀 채널 알림",
            "팀이 Slack을 연결한 경우, 알림을 보낼 때 네트워크로 전송",
            "알림 전달",
          ],
          [
            ["Notion, ", ABROAD],
            "확정된 액션 아이템과 결정",
            "팀이 Notion을 연결하고 사람이 항목을 확정한 뒤 네트워크로 전송",
            "팀의 작업 도구에 기록",
          ],
          [
            ["Atlassian (Jira), ", ABROAD],
            "확정된 액션 아이템",
            "팀이 Jira를 연결하고 사람이 항목을 확정한 뒤 네트워크로 전송",
            "팀의 작업 도구에 기록",
          ],
          [
            ["Google (Calendar), ", ABROAD],
            "액션 아이템의 내용과 날짜",
            "담당자가 본인 캘린더를 연결한 경우 네트워크로 전송",
            "본인 일정에 기한 표시",
          ],
          [
            [blank("호스팅 사업자"), ", ", blank("이전 국가")],
            "서비스가 보관하는 모든 자료",
            "상시",
            "서비스 운영",
          ],
        ),
        ul(
          [
            "이전받는 자의 보유 기간: ",
            blank("이전받는 자의 보유 기간", "각 사업자의 약관에 따름"),
          ],
          "녹음 파일은 어디에도 보내지 않습니다.",
        ),
      ],
    },
    {
      heading: "동의를 거부할 권리",
      blocks: [p(["이 동의를 거부할 수 있습니다. ", REFUSAL, "."])],
    },
  ],
};

export const LEGAL_DOCUMENTS: readonly LegalDocument[] = [
  PRIVACY,
  TERMS,
  SECURITY,
  VOICE_CONSENT,
  OVERSEAS_CONSENT,
];

/** The document the consent page opens for one required consent. */
export function legalDocument(id: LegalDocument["id"]): LegalDocument {
  const found = LEGAL_DOCUMENTS.find((doc) => doc.id === id);
  if (found === undefined) throw new Error(`no legal document "${id}"`);
  return found;
}
