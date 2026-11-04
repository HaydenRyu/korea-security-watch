# korea-security-watch

공개정보(OSINT)로 한반도 군사 징후를 매시간 확인한다. 의미 있는 변화가 있을 때만 ntfy 푸시로 알린다. 개인용 비공식 분석이며, 공식 경보는 행정안전부·재난문자를 우선한다.

## 흐름
1. Claude 클라우드 루틴(매시간)이 `prompts/monitor.md` 를 따라 검색하고 `run.json` 을 만든다(등급 항목 10개 + 임박 신호·긴장 국면·완화 지표).
2. `scripts/diff_state.py` 가 등급을 계산하고 직전 회차와 비교한다. 결과는 `state/` 에 기록된다.
   - 배경 블록(세계 브리핑·배경 요인·연동 위기·전역·사건 기록)은 별도 루틴(하루 3회, `prompts/background.md` → `scripts/background.py`)이 쓴다. 아래 「루틴과 파일 소유」.
3. 변화가 있으면 루틴이 `state/outbox.json` 을 push 한다. 이 push 를 받은 Actions `notify` 가 ntfy 로 보낸다.
4. Actions `watchdog` 은 6시간마다 루틴이 정지했는지 감지해 본인 주제로 알린다.
5. 대시보드는 GitHub Pages 의 `index.html` 이다. 예시 화면은 `?sample=1` 로 연다.

## 등급
| 등급 | 조건 |
|---|---|
| 0 평시 | 징후 없음 |
| 1 주의 | 단일 징후, 또는 일상 활동(정례 훈련·미사일 시험·위협 발언)만 있음 |
| 2 경계 | 일상 활동이 아닌 징후가 2개 항목 이상, 또는 교차확인된 징후 1개 이상 |
| 3 위험 | 핵·병력 동원·정부 경고 중 하나가 교차확인되고, 일상 활동이 아닌 다른 항목 징후가 동시에 있음 |

- 알림 조건: 등급이 오르거나, 같은 등급에서 새 항목이 추가될 때
- 내림은 두 회차 연속 낮게 산정될 때만 적용한다(경계선 사건이 회차마다 뒤집히는 것 방지). 보류 중에는 직전 등급·항목을 유지하고 이력에 `held`·`raw_grade` 를 남기며, 해제 알림은 내림이 적용될 때 한 번 보낸다. 오를 때는 즉시 적용한다
- 감시 항목 9개(키는 `scripts/diff_state.py` `ITEMS`). 핵심 항목(위험 판정용)은 핵·병력 동원·정부 경고 3개이고, 전자전·사이버와 북 내부 전시태세는 일반 징후다
- 본인 주제(`NTFY_TOPIC_ME`): 주의 이상 알림·해제·감시 중단·루틴 정지
- 지인 주제(`NTFY_TOPIC`): 경계 이상 알림과 해제만
- 3회 연속 판정 불가면 「감시 중단」을 1회 알린다

## 루틴과 파일 소유
두 루틴은 서로 다른 파일만 쓴다 — 그래야 `git pull --rebase` 가 충돌 없이 합쳐진다(회귀: `tests/test_background.py`).

| 루틴 | 주기 | 지시문 → 코드 | 쓰는 파일 |
|---|---|---|---|
| 매시간 | 매시 :07 | `prompts/monitor.md` → `diff_state.py`·`ews.py` | `state/latest.json`·`events.jsonl`·`signals.json`·`ladder.json`·`scenarios.json`·`ews.json`·`outbox.json` |
| 배경 | KST 08:37·14:37·20:37 | `prompts/background.md` → `background.py` | `state/world.json`·`factors.json`·`linkage.json`·`theaters.json`·`incidents.json`·`background.json` |

- 서로의 파일은 읽기만 한다(`ews.py` 가 `theaters.json` 을, 화면이 전부를). 배경 파일의 시간 경과 만료도 배경 회차에만 반영된다(8시간 단위).
- 블록 수행 기록: 루틴이 `blocks: {블록: checked|no_findings|failed}` 를 내고, 키가 없으면 코드가 `skipped` 로 적는다. 매시간 블록(`signals`·`ladder`·`calm`)은 `events.jsonl` 의 `blocks`, 배경 블록은 `state/background.json` 의 `blocks{키: {status, last_ok}}`.
- 세계 브리핑은 2026-11-05 부터 `state/world.json` 이다(`latest.json` 에서 뺐다). 화면은 `world.json` 이 없거나 비면 `latest.world` 로 폴백한다.

## 조기경보 지수(EWS, 참고용)
`scripts/ews.py` 가 `state/ews.json` 을 쓴다(매시간 루틴 2-2단계, 등급·알림과 무관). 세계 5개 전역 판정은 `state/theaters.json`(배경 루틴이 쓴다 — 출처는 판정 시각 기준 72시간 안 `published_at` 만).
지금은 세계 연동을 합치지 않고(`data/ews_weights.json` alpha 1 — 과거 검정 6건 모두 평소 수준) 구간은 공식 단계가 정하고 값은 그 단계에서 D 가 가질 수 있는 최댓값 대비 위치다(`scripts/ews_backtest.py` 로 재생). 화면은 `public: false` 동안 `?sample=1` 에서만 보인다.

## 설정 (최초 1회)
1. 레포 시크릿을 넣는다. `FIRMS_KEY`(NASA FIRMS MAP_KEY, Actions `firms` 가 매시간 열점 갱신)와 ntfy 주제 두 개다. 값은 추측 불가한 무작위 문자열이다. 레포·공개 페이지에 적지 않는다.
   - `NTFY_TOPIC_ME`: 본인 주제
   - `NTFY_TOPIC`: 지인 주제
2. 휴대폰에 ntfy 앱을 설치하고 주제를 구독한다. 서버는 기본값 `ntfy.sh` 그대로 둔다.
3. Actions → notify → Run workflow 로 본인 주제 테스트 메시지를 보낸다.
4. GitHub Pages: Settings → Pages → Source = main / root.
5. CCTV 사진: 국가교통정보센터가 해외 IP 를 막아 GitHub 러너·Cloudflare 에서 직접 못 찍는다(2026-10-18 실측). 한국 IP 맥에서 `bash scripts/install_cctv_snap.sh` 로 5분 주기 설치 → `relay/`(Cloudflare Worker `jis-cctv`, `npx wrangler deploy`)로 올린다. 사진은 최대 6분 늦다.
6. Claude 루틴을 두 개 만든다(같은 환경·모델).
   - 매시간: 주기 매시간 · 지시문 `prompts/monitor.md 를 읽고 그대로 수행하라`
   - 배경: 이름 `korea-security-watch-background` · cron `37 23,5,11 * * *`(UTC = KST 08:37·14:37·20:37) · 지시문 `한국어로 작업한다. 레포의 prompts/background.md 를 읽고 그대로 수행하라. 마지막에 background.py 출력 한 줄과 push 결과(커밋 sha)를 출력한다.`
   - 레포: 이 레포 · 네트워크: Full. 출처 사이트가 다양해 Trusted 로는 막힐 수 있다.

## 테스트
`python3 -m pytest tests` (pytest 없으면 `tests/test_diff_state.py` 의 함수 직접 실행)
