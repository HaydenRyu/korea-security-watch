# korea-security-watch

공개정보(OSINT)로 한반도 군사 징후를 매시간 확인한다. 의미 있는 변화가 있을 때만 ntfy 푸시로 알린다. 개인용 비공식 분석이며, 공식 경보는 행정안전부·재난문자를 우선한다.

## 흐름
1. Claude 클라우드 루틴(매시간)이 `prompts/monitor.md` 를 따라 검색하고 `run.json` 을 만든다.
2. `scripts/diff_state.py` 가 등급을 계산하고 직전 회차와 비교한다. 결과는 `state/` 에 기록된다.
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
- 본인 주제(`NTFY_TOPIC_ME`): 주의 이상 알림·해제·감시 중단·루틴 정지
- 지인 주제(`NTFY_TOPIC`): 경계 이상 알림과 해제만
- 3회 연속 판정 불가면 「감시 중단」을 1회 알린다

## 설정 (최초 1회)
1. 레포 시크릿 두 개를 넣는다. 값은 추측 불가한 무작위 문자열이다. 레포·공개 페이지에 적지 않는다.
   - `NTFY_TOPIC_ME`: 본인 주제
   - `NTFY_TOPIC`: 지인 주제
2. 휴대폰에 ntfy 앱을 설치하고 주제를 구독한다. 서버는 기본값 `ntfy.sh` 그대로 둔다.
3. Actions → notify → Run workflow 로 본인 주제 테스트 메시지를 보낸다.
4. Settings → Pages 에서 source 를 `main` / root 로 지정한다.
5. Claude 루틴을 만든다.
   - 주기: 매시간
   - 레포: 이 레포
   - 지시문: `prompts/monitor.md 를 읽고 그대로 수행하라`
   - 환경 변수 `FIRMS_KEY`: NASA FIRMS MAP_KEY (https://firms.modaps.eosdis.nasa.gov/api/map_key/)
   - 네트워크: Full. 출처 사이트가 다양해 Trusted 로는 막힐 수 있다.

## 테스트
`python3 -m pytest tests` (pytest 없으면 `tests/test_diff_state.py` 의 함수 직접 실행)
