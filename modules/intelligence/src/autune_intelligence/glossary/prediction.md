<!-- E's misalignment prediction passages. Text is Korean (user-facing); numbers are placeholders. -->

## prediction.overview | 불일치 위험 예측이란
이 회의에서 내린 결정이 {prediction.horizon} 안에 뒤집힐 예상 확률입니다. 뒤집힌 결정은 맥락 모듈이 기록한 번복으로 정합니다.

## prediction.inputs | 예측에 쓰는 정보
회의 품질 점수, 갭 개수, 가장 낮은 직무 정렬, 결정 번복 흐름, 핵심 이해관계자가 빠진 채 바뀐 결정이 있었는지, 불확실한 합의, 확정되지 않은 액션 아이템, 빠진 입력을 씁니다. 이해관계자는 누구인지가 아니라 있었는지만 봅니다.

## prediction.gate | 예측이 보이는 조건
점수가 매겨진 회의가 {prediction.min_meetings} 이상일 때부터 예측을 보여 줍니다. 그 전에는 값 대신 이유가 표시됩니다.

## prediction.accuracy | 예측의 한계
기본 예측기는 사람이 정한 가중치를 쓰며 아직 검증하지 않았습니다. 학습한 모델은 설정으로 켜야 쓰고, 라벨이 충분히 쌓이기 전에는 기본 예측기를 유지합니다. 정확도는 보정 평가로 따로 확인하며, 라벨이 너무 적으면 점수를 내지 않습니다.
