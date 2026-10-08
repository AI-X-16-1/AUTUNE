import { describe, expect, it } from "vitest";

import { TITLE_MAX, shortTitle } from "./title";

const length = (text: string) => [...text].length;

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
