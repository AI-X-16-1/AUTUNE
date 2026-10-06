<!-- E's alignment heatmap passages. Text is Korean (user-facing); numbers are placeholders. -->

## alignment.overview | 정렬 히트맵이란
히트맵은 직무 두 개씩 짝을 지어 같은 결정에 얼마나 비슷한 입장이었는지를 보여 줍니다. 값이 1에 가까우면 두 직무가 같은 방향이고, 0이면 한쪽이 찬성하는 것에 다른 쪽이 완전히 반대한 것입니다.

## alignment.score | 정렬 점수 계산
직무의 입장은 (찬성 - 우려) / 확인된 사람 수로 구하고, 말하지 않은 사람은 중립으로 둡니다. 두 직무의 일치도는 1에서 입장 차이의 절반을 뺀 값입니다. 한 회의의 점수는 두 직무가 모두 입장을 낸 결정들의 평균입니다.

## alignment.silence | 침묵은 합의가 아닙니다
두 직무가 모두 입장을 내지 않은 결정은 그 짝의 계산에서 건너뜁니다. 그렇지 않으면 침묵이 완벽한 합의로 읽힙니다.

## alignment.floor | 칸이 비어 있는 이유
회의 {heatmap.min_meetings} 미만에서 점수가 나온 직무 짝은 히트맵에서 뺍니다. 표본이 적으면 특정 사람의 입장이 드러날 수 있어서 칸을 비워 둡니다.
