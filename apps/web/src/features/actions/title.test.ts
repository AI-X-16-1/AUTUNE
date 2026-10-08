import { describe, expect, it } from "vitest";

import { TITLE_MAX, shortTitle } from "./title";

const length = (text: string) => [...text].length;

/** What fits before the "…". */
const ROOM = TITLE_MAX - 1;

/** The form sits where the limit falls: its first word fits and all of it does not. */
function straddles(sentence: string, form: string): boolean {
  const start = sentence.indexOf(form);
  const firstWord = sentence.slice(0, start + form.indexOf(" "));
  const all = sentence.slice(0, start + form.length);
  return start > 0 && form.includes(" ") && length(firstWord) <= ROOM && length(all) > ROOM;
}

/** The sentence up to the form, as a cut that keeps the form whole shows it. */
function before(sentence: string, form: string): string {
  return `${sentence.slice(0, sentence.indexOf(form)).trimEnd()}…`;
}

describe("shortTitle", () => {
  it("leaves a sentence of twenty characters or fewer as it is", () => {
    const twenty = "다음 주 화요일에 출시하기로 했습니다";

    expect(length(twenty)).toBe(TITLE_MAX);
    expect(shortTitle(twenty)).toEqual({ shown: twenty, cut: false });
    expect(shortTitle("시안은 제가 챙겨 볼게요.")).toEqual({
      shown: "시안은 제가 챙겨 볼게요.",
      cut: false,
    });
  });

  it("cuts a longer one between words, the mark inside the twenty", () => {
    const title = shortTitle("마케팅 메일은 출시 다음 날인 수요일 오전에 보내기로 했습니다");

    expect(title).toEqual({ shown: "마케팅 메일은 출시 다음 날인…", cut: true });
    expect(length(title.shown)).toBeLessThanOrEqual(TITLE_MAX);
  });

  it("counts a space, a digit and a mark as one each", () => {
    // 22 characters with its spaces, 17 without: it is cut.
    const sentence = "로그 보관 기간은 90일로 늘리기로 정함";

    expect(length(sentence)).toBe(22);
    expect(shortTitle(sentence).cut).toBe(true);
  });

  it("never shows more than the limit, whatever it is given", () => {
    const sentences = [
      "환불 정책 안내 문구 변경은 법무 검토가 끝난 뒤에 하기로 하고 이번 출시에는 넣지 않기로 했습니다",
      "베타 사용자 설문은 200명한테 보내고 응답이 50개 넘으면 마감하는 걸로 정했습니다",
      "a".repeat(80),
      "가 ".repeat(40),
      "😀".repeat(30),
    ];

    for (const sentence of sentences) {
      const title = shortTitle(sentence);
      expect(title.cut).toBe(true);
      expect(length(title.shown)).toBeLessThanOrEqual(TITLE_MAX);
      expect(title.shown.endsWith("…")).toBe(true);
    }
  });

  it("does not cut inside a date and a time", () => {
    // The last gap that fits is between "15일" and "오후".
    const title = shortTitle("릴리스 공지는 반드시 10월 15일 오후 3시에 올립니다");

    expect(title.shown).toBe("릴리스 공지는 반드시…");
  });

  it("does not leave a week without its day", () => {
    const title = shortTitle("회의록 정리는 제가 다음 주 화요일까지 올리겠습니다");

    expect(title.shown).toBe("회의록 정리는 제가…");
  });

  it("keeps a whole date that fits", () => {
    const title = shortTitle("공지는 10월 15일 오후 3시에 올리기로 했습니다");

    expect(title.shown).toBe("공지는 10월 15일 오후 3시에…");
  });

  it("does not leave a count in words without what it counts", () => {
    const title = shortTitle("타임아웃은 5초로 늘리고 재시도 한 번 넣기로 했습니다");

    expect(title.shown).toBe("타임아웃은 5초로 늘리고 재시도…");
  });

  it("does not take a word that only ends like a count for one", () => {
    // The last gap that fits is between "중요한" and "번역은".
    const title = shortTitle("이번 릴리스 노트에서 가장 중요한 번역은 다시 봅니다");

    expect(title.shown).toBe("이번 릴리스 노트에서 가장 중요한…");
  });

  it("does not leave a number without its unit", () => {
    const title = shortTitle("설문은 베타 사용자 가운데 200 명한테 먼저 보냅니다");

    expect(title.shown).toBe("설문은 베타 사용자 가운데…");
  });

  // The forms the first list left out (#1072's "not done"). In each sentence
  // the form sits where the limit falls -- `straddles` says so -- so a cut
  // that does not know the form lands inside it.
  it.each([
    ["베타 설문은 사용자 가운데 스무 명한테 먼저 보냅니다", "스무 명한테"],
    ["온보딩 문서 검토는 팀에서 열두 명이 나눠서 합니다", "열두 명이"],
    ["장애 대응 훈련은 올해 안에도 몇 번 더 하기로 했습니다", "몇 번"],
    ["릴리스 후보 빌드는 이번에도 두세 개 만들어 봅니다", "두세 개"],
    ["회의록 초안은 지금까지 밀렸던 석 달 치를 올립니다", "석 달"],
    ["같은 문구가 설정 화면에도 여러 군데 남아 있습니다", "여러 군데"],
    ["온보딩 메일은 가입하고 나서 첫 번째 주에 보냅니다", "첫 번째"],
    ["이번 배포 뒤에는 응답 속도가 두 배로 빨라집니다", "두 배로"],
  ])("does not leave a count in other words without what it counts: %s", (sentence, form) => {
    expect(straddles(sentence, form)).toBe(true);
    expect(shortTitle(sentence).shown).toBe(before(sentence, form));
  });

  it.each([
    ...["한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟", "아홉"],
    ...["스무", "석", "넉", "몇", "여러", "수십", "수백", "첫"],
    ...["한두", "두세", "서너", "두어", "네댓"],
    ...["열", "스물", "서른", "마흔", "쉰", "예순", "일흔", "여든", "아흔"],
    ...["열한", "열두", "스물세", "서른네", "아흔두"],
  ])("takes %s for a number that counts", (word) => {
    const sentence = `릴리스 후보 빌드는 이번에도 ${word} 번째 돌렸습니다`;

    expect(straddles(sentence, `${word} 번째`)).toBe(true);
    expect(shortTitle(sentence).shown).toBe("릴리스 후보 빌드는 이번에도…");
  });

  it.each([
    ...["분기", "사분기", "년", "곳", "쪽", "페이지", "사람"],
    ...["일", "분", "초", "회", "배", "장", "대", "팀", "해", "프로"],
  ])("takes %s for a unit", (unit) => {
    const sentence = `이번 스프린트에서는 반드시 용량 2 ${unit}까지 봅니다`;

    expect(straddles(sentence, `2 ${unit}까지`)).toBe(true);
    expect(shortTitle(sentence).shown).toBe("이번 스프린트에서는 반드시 용량…");
  });

  it.each([
    ...["간", "동안", "째", "씩", "쯤", "마다", "까지", "부터", "에"],
    ...["은", "는", "이", "가", "을", "를", "도", "만", "으로", "로", "밖에"],
  ])("takes a unit that everyday words start with when %s follows it", (particle) => {
    const sentence = `이번 스프린트에서는 반드시 용량 2 배${particle} 봅니다`;

    expect(shortTitle(sentence).shown).toBe("이번 스프린트에서는 반드시 용량…");
  });

  it.each([
    ["결제 모듈 교체 작업은 앞으로 2 년 걸립니다", "2 년"],
    ["배포 창은 매번 새벽에만 딱 30 분 엽니다", "30 분"],
    ["QA 결과 정리에는 넉넉하게 꼭 3 일 주기로 했습니다", "3 일"],
    ["이번 스프린트에서는 반드시 용량 2 배까지 봅니다", "2 배까지"],
    ["성능 목표는 아무리 늦어도 올해 3 분기에 맞춥니다", "올해 3 분기에"],
  ])("does not leave a number without a unit set apart from it: %s", (sentence, form) => {
    expect(straddles(sentence, form)).toBe(true);
    expect(shortTitle(sentence).shown).toBe(before(sentence, form));
  });

  it.each(["배포", "초안", "일정", "회의", "장애", "프로젝트"])(
    "does not take a word that only starts like a unit for one: %s",
    (word) => {
      // "배" is a unit and "배포" is not: the number may end the line.
      const title = shortTitle(`이번 스프린트에서는 반드시 버전 2 ${word}까지 봅니다`);

      expect(title.shown).toBe("이번 스프린트에서는 반드시 버전 2…");
    },
  );

  it.each([
    ["점검 공지는 실제 배포하기 꼭 3일 전에 올립니다", "3일 전에"],
    ["회고 정리는 릴리스하고 나서 2주 뒤에 하기로 했습니다", "2주 뒤에"],
    ["보안 패치 적용은 늦어도 이번 주 안에 끝냅니다", "이번 주 안에"],
    ["디자인 시안 검토는 늦어도 금요일 전까지 끝냅니다", "금요일 전까지"],
    ["어제 올라온 장애 보고서는 오늘 안으로 정리합니다", "오늘 안으로"],
    ["고객 문의 답변은 접수하고 이틀 안에 보내기로 함", "이틀 안에"],
    ["초대 메일은 절대로 동시에 50명 이상 보내지 않습니다", "50명 이상"],
    // A particle on the unit does not part it from the word after.
    ["신규 기능 공개는 지금부터 한 달쯤 뒤에 합니다", "한 달쯤 뒤에"],
    ["신규 기능 공개는 지금부터 3 달쯤 뒤에 합니다", "3 달쯤 뒤에"],
    ["지난번 장애 회고는 이미 저번 주 금요일에 끝냈습니다", "저번 주 금요일에"],
  ])("does not leave a date or a count without the word that bounds it: %s", (sentence, form) => {
    expect(straddles(sentence, form)).toBe(true);
    expect(shortTitle(sentence).shown).toBe(before(sentence, form));
  });

  it.each([
    ...["전", "후", "뒤", "안", "내", "이내", "중", "이상", "이하", "미만", "초과", "동안"],
    ...["전에", "전까지", "전부터", "안으로", "내로", "뒤는", "이상은", "이상이", "전의"],
    ...["전쯤", "동안만", "이하도", "전까지는"],
  ])("takes %s for a word that bounds the count before it", (word) => {
    const sentence = `초대 메일은 절대로 동시에 50명 ${word} 보내지 않습니다`;

    expect(straddles(sentence, `50명 ${word}`)).toBe(true);
    expect(shortTitle(sentence).shown).toBe("초대 메일은 절대로 동시에…");
  });

  it.each([
    ...["어제", "올해", "내년", "작년", "주말", "월말", "연말", "당일"],
    ...["하루", "이틀", "사흘", "나흘", "닷새", "열흘", "보름"],
  ])("takes %s for a day or a number of days", (word) => {
    const sentence = `디자인 시안 검토는 늦어도 ${word} 안에 끝냅니다`;

    expect(straddles(sentence, `${word} 안에`)).toBe(true);
    expect(shortTitle(sentence).shown).toBe("디자인 시안 검토는 늦어도…");
  });

  it.each([
    ["리뷰 승인 기준은 리뷰어 3명 중 2명 찬성으로 함", "3명 중"],
    ["리뷰 승인 기준은 리뷰어 3명 중에 2명 찬성으로 함", "3명 중에"],
    ["승인 기준은 꼭 리뷰어 3명 중에서 2명 찬성으로 함", "3명 중에서"],
  ])("keeps both numbers of a count out of a count: %s", (sentence, first) => {
    // "3명 중" fits and "3명 중 2명" does not: cut there, it would say three.
    const start = sentence.indexOf(first);
    expect(length(sentence.slice(0, start + first.length))).toBeLessThanOrEqual(ROOM);
    expect(length(sentence.slice(0, start + first.length + " 2명".length))).toBeGreaterThan(ROOM);

    expect(shortTitle(sentence).shown).toBe(before(sentence, first));
  });

  it("does not take a word that only starts like one of those for one", () => {
    // "전체" is not "전", and "안건만" not "안": the date and the count may end the line.
    expect(shortTitle("분기 계획 공유는 다음 달 3일 전체 회의에서 합니다").shown).toBe(
      "분기 계획 공유는 다음 달 3일…",
    );
    expect(shortTitle("오늘 회의에서는 새로 올라온 5개 안건만 봅니다").shown).toBe(
      "오늘 회의에서는 새로 올라온 5개…",
    );
  });

  it("keeps such a word with what is before it, not with what is after", () => {
    // "뒤" belongs to "공지": the cut may fall between it and "5일 안에".
    const title = shortTitle("보안 패치는 정식 공지 뒤 5일 안에 넣습니다");

    expect(title.shown).toBe("보안 패치는 정식 공지 뒤…");
  });

  it("ends the date there: what comes next is not part of it", () => {
    // "올해 안에도" is whole, and "3 번" after it is another count.
    const title = shortTitle("장애 대응 훈련은 올해 안에도 3 번 더 하기로 했습니다");

    expect(title.shown).toBe("장애 대응 훈련은 올해 안에도…");
  });

  it("cuts a first word too long to fit at the limit", () => {
    const title = shortTitle("가나다라마바사아자차카타파하거너더러머버서어저처");

    expect(title.shown).toBe("가나다라마바사아자차카타파하거너더러머…");
    expect(length(title.shown)).toBe(TITLE_MAX);
  });

  it("moves that cut back out of a number", () => {
    const title = shortTitle("가나다라마바사아자차카타파하12,345,678원입니다");

    expect(title.shown).toBe("가나다라마바사아자차카타파하…");
  });

  // What module A's masker writes (`autune_audio.masking`): the shape kept,
  // "*" for the content. Each shape below is one it produces.
  it("does not cut inside a masked phone number said with spaces", () => {
    // The last gap that fits is between "****" and "5678로".
    const title = shortTitle("담당자 연락처는 010 **** 5678로 정리했습니다");

    expect(title.shown).toBe("담당자 연락처는…");
  });

  it("does not cut inside a masked card number", () => {
    const title = shortTitle("결제는 **** **** **** 3456 카드로 하기로 했습니다");

    expect(title.shown).toBe("결제는…");
  });

  it("does not cut inside a span a person reported, masked word by word", () => {
    const title = shortTitle("새 배송지는 ** *** **** *** 쪽으로 정했습니다");

    expect(title.shown).toBe("새 배송지는…");
  });

  it("keeps a masked span whole when it fits", () => {
    const title = shortTitle("연락처는 010-****-5678로 정리했습니다");

    expect(title.shown).toBe("연락처는 010-****-5678로…");
    expect(length(title.shown)).toBe(TITLE_MAX);
  });

  it("moves a cut with no gap to fall in back out of a masked number", () => {
    const title = shortTitle("가나다라마바사아자차카타010-****-5678로연락합니다");

    expect(title.shown).toBe("가나다라마바사아자차카타…");
  });

  it("keeps a masked number that ends exactly where that cut falls", () => {
    const title = shortTitle("가나다라마바010-****-5678로연락드리겠습니다");

    expect(title.shown).toBe("가나다라마바010-****-5678…");
    expect(length(title.shown)).toBe(TITLE_MAX);
  });

  it("moves that cut back out of a masked address, its first letter kept", () => {
    const title = shortTitle("가나다라마바사아자차카타파하k***@example.com으로보냅니다");

    expect(title.shown).toBe("가나다라마바사아자차카타파하…");
  });

  it("moves that cut back out of a masked name, its first character kept", () => {
    const title = shortTitle("가나다라마바사아자차카타파하거너더김**님께전달합니다");

    expect(title.shown).toBe("가나다라마바사아자차카타파하거너더…");
  });

  it("leaves no comma or stop hanging before the mark", () => {
    expect(shortTitle("첫째, 둘째, 셋째, 넷째, 다섯째, 여섯째, 일곱째").shown).toBe(
      "첫째, 둘째, 셋째, 넷째, 다섯째…",
    );
  });

  it("adds no word and changes none: what is shown is the start of the sentence", () => {
    const sentence = "타임아웃은 5초로 늘리고 재시도 한 번 넣기로 했습니다";
    const title = shortTitle(sentence);

    expect(sentence.startsWith(title.shown.slice(0, -1))).toBe(true);
  });

  it("shows nothing for nothing", () => {
    expect(shortTitle("   ")).toEqual({ shown: "", cut: false });
  });
});
