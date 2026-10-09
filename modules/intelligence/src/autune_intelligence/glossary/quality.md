<!-- E's quality score passages. Text is Korean (user-facing); numbers are placeholders. -->

## quality.overview | 회의 품질 점수란
회의 품질 점수는 결정 밀도, 고위험 갭 부담, 할 일 확정률, 참여 균형 네 가지를 합친 0~1 사이 점수입니다. 측정하지 못한 항목은 0점으로 치지 않고 빼고 계산합니다. 측정된 항목이 하나도 없으면 중간값으로 둡니다.

## quality.weights | 구성 요소별 가중치
결정 밀도 {weight.decision_density}, 고위험 갭 부담 {weight.gap_burden}, 할 일 확정률 {weight.action_item_completion_rate}, 참여 균형 {weight.participation_balance}의 비중으로 합칩니다. 이 비중은 사람이 정한 초기값이고, 아직 검증하지 않았습니다.

## quality.grades | 등급 기준
점수가 {grade.cutoffs}이면 각 등급이고, 그보다 낮으면 F입니다.

## quality.decision_density | 결정 밀도
회의에서 나온 결정 수를 회의 길이에서 기대하는 결정 수로 나눈 값이고, 최대 1입니다. 기대하는 결정 수는 {quality.decision_cadence}에 하나로 셉니다. 결정이 하나도 없으면 0점이 아니라 측정하지 못한 것으로 보고 점수에서 뺍니다.

## quality.gap_burden | 고위험 갭 부담
고위험 갭이 많을수록 이 항목이 내려가고, {gap.high_ceiling} 이상이면 0점입니다. 심각도가 높지 않은 갭은 이 항목에 들어가지 않습니다. 동의한 발화가 없어 갭을 재지 못한 회의는 갭이 없는 회의로 치지 않고 이 항목을 빼고 계산합니다.

## quality.action_confirmation | 회의 점수의 할 일 확정률
그 회의에서 나온 할 일 중 확인이 필요한 상태를 벗어난 항목의 비율입니다. 일을 끝냈는지가 아니라 확정되었는지를 봅니다. 회의 점수를 매길 때의 값으로 고정됩니다.

## quality.participation | 참여 균형
주제마다 참석자 중 그 주제에 발언한 사람의 비율을 구해 평균한 값입니다. 얼마나 오래 말했는지는 계산에 쓰지 않습니다. 개인별 발언 수치는 팀에 공개되지 않습니다.

## quality.pending | 점수가 아직 없는 회의
회의 점수는 할 일, 갭, 이전 회의 연결의 분석 결과가 모두 오거나, 첫 결과가 온 뒤 {quality.aggregate_wait}이 지나면 매깁니다. 그 전에는 점수가 없습니다. 오지 않은 결과가 있으면 그것 없이 매기고, 빠진 결과가 무엇인지 함께 남깁니다.

## quality.speaking_ratio | 발언 비율은 누가 보나
발언 비율은 회의가 끝나면 본인에게만 슬랙 DM으로 보냅니다. 다른 팀원이나 관리자는 남의 비율을 볼 수 없고, 채팅에서도 알려 주지 않습니다. 발언한 사람 중 화자로 확인된 사람이 {speaking.min_people} 미만인 회의는 보내지 않습니다. 비율로 남의 몫을 거꾸로 알 수 있어서입니다. 비율은 어디에도 저장하지 않습니다.
