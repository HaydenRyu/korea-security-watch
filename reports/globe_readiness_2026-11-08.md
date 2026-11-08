# 지구본 공개 준비 판정 2026-11-08 — 표본 부족 (3/9)

## 1. 수집 안정성 (2026-11-05T03:30Z 이후 `bg:` 커밋)

| 커밋 | 시각(KST) | theaters 상태 | 관측 수(raw/kept) | failed 블록 |
|---|---|---|---|---|
| 9c7bb97 | 2026-11-07 14:40 | checked | 5/5 | 없음 |
| 397261b | 2026-11-07 20:39 | checked | 5/5 | 없음 |
| f81baf9 | 2026-11-08 08:40 | checked | 5/5 | 없음 |

(note: Rappler 피드 빈 응답 — scs_ecs 는 Philstar·BBC 아시아로 확인)

**판정: 표본 부족 3/9.** 기준 위반(failed·kept<3)은 없으나 연속 9회에 못 미침.

## 2. 전역별 검수표 (최종 검수는 사람이 한다. 아래 기사 판단은 참고용)

state/theaters.json judged_at: 2026-11-08 08:40 KST

| 전역 | 상태 | summary | sources (KST) | 기사 제목 | 참고 판단 |
|---|---|---|---|---|---|
| ukraine | 1 긴장 | 백악관에서 러·우 대표단이 만나 헤르손 휴전 이후 지속 가능한 휴전을 논의했다. | BBC 2026-11-06 16:56 · https://www.bbc.co.uk/news/articles/c9p8g085mz07o | 확인 불가(제목 추출 실패) | 확인 불가 — 협상 국면이라 긴장(1)은 개연성 있으나 기사 미확인 |
| mideast | 3 교전 | 미 중부사령부가 7일 오만만에서 이란 봉쇄 위반 화물선을 무력화, 사우디 공습으로 후티 25명 이상 사망 주장. | DVIDS 2026-11-08 05:31 · https://www.dvidshub.net/news/578872/us-forces-disable-cargo-vessel-attempting-violate-nav ; BBC 2026-11-08 02:09 · https://www.bbc.co.uk/news/articles/c65yn1v99ypmo | DVIDS: "U.S. Forces Disable Cargo Vessel Attempting to Violate Naval Blockade" / BBC: 확인 불가 | 맞음 — 미군의 선박 무력화(실제 교전 행위)로 3 뒷받침 |
| taiwan | 1 긴장 | ISW·AEI가 중국의 비공표 대규모 군연 대만 포위 모의 가능성 지적, 국안회의는 서태평양 100여 척 전개 발표. | 중앙통신사 2026-11-07 21:17 · https://www.cna.com.tw/news/aipl/202611070199.aspx | 확인 불가(제목 추출 실패) | 확인 불가 — 요약 내용이면 긴장(1) 타당해 보이나 기사 미확인 |
| scs_ecs | 0 평온(피드 확인) | Philstar·BBC 아시아 피드에 72시간 안 남·동중국해 관련 기사 없음 | Philstar 2026-11-08 00:00 · https://www.philstar.com/rss/nation ; BBC 아시아 2026-11-08 08:02 · https://feeds.bbci.co.uk/news/world/asia/rss.xml | (피드, 개별 기사 없음) | 확인 불가 — 부재 증명이라 사람 확인 필요(Rappler 피드 결측) |
| us_posture | 0 평온(피드 확인) | Stars and Stripes·DVIDS 피드에 72시간 안 새 전략자산 전개 기사 없음 | Stars and Stripes 2026-11-06 09:00 · https://www.stripes.com/theaters/asia_pacific/ ; DVIDS 2026-11-08 07:01 · https://www.dvidshub.net/rss/news | (피드, 개별 기사 없음) | 확인 불가 — 부재 증명 |

## 공개 방법

공개하려면 `data/ews_weights.json` 의 `public_globe` 를 true 로 바꾸는 커밋 1건, `public_index` 는 false 유지.
(지금은 표본 부족이므로 공개 보류 권장.)

_gh 인증 실패로 이슈 대신 이 파일로 기록._
