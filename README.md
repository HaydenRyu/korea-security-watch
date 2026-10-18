# korea-security-watch

공개정보(OSINT)로 한반도 군사 징후를 매시간 확인한다. 의미 있는 변화가 있을 때만 카카오톡 「나에게 보내기」로 알린다. 개인용 비공식 분석이며, 공식 경보는 행정안전부·재난문자를 우선한다.

## 흐름
1. Claude 클라우드 루틴(매시간)이 `prompts/monitor.md` 를 따라 검색하고 `run.json` 을 만든다.
2. `scripts/diff_state.py` 가 등급을 계산하고 직전 회차와 비교한다. 결과는 `state/` 에 기록된다.
3. 변화가 있으면 루틴이 `state/outbox.json` 을 push 한다. 이 push 를 받은 Actions `notify` 가 카톡을 보낸다.
4. Actions `watchdog` 은 6시간마다 두 가지를 한다.
   - 카카오 토큰을 유지하고 회전한다.
   - 루틴이 정지했는지 감지한다.
5. 대시보드는 GitHub Pages 의 `index.html` 이다. 예시 화면은 `?sample=1` 로 연다.

## 등급
| 등급 | 조건 |
|---|---|
| 0 평시 | 징후 없음 |
| 1 주의 | 단일 징후, 또는 일상 활동(정례 훈련·예고된 시험·위협 발언)만 있음 |
| 2 경계 | 일상 활동이 아닌 징후가 2개 항목 이상, 또는 교차확인된 징후 1개 이상 |
| 3 위험 | 핵·병력 동원·정부 경고 중 하나가 교차확인됨 |

- 알림 조건: 등급이 오르거나, 같은 등급에서 새 항목이 추가될 때
- 등급 2 이상이면 메시지 첫 줄에 「지인 전달 권고」가 붙는다
- 3회 연속 판정 불가면 「감시 중단」을 1회 알린다

## 설정 (최초 1회)
1. **Kakao Developers** 에서 앱을 만든다.
   - 카카오 로그인 ON
   - Redirect URI: `http://localhost:8766/callback`
   - 동의항목 「카카오톡 메시지 전송(talk_message)」 ON
   - 플랫폼 Web 사이트 도메인: `https://haydenryu.github.io`
2. 로컬에서 `KAKAO_REST_KEY=<REST API 키> python3 scripts/kakao.py auth` 를 실행한다. 출력된 refresh_token 을 확인한다.
3. 레포 시크릿 네 개를 넣는다.
   - `KAKAO_REST_KEY`
   - `KAKAO_REFRESH_TOKEN`
   - `KAKAO_CLIENT_SECRET`: 클라이언트 시크릿을 켠 경우만
   - `SECRETS_PAT`: fine-grained PAT. 이 레포만 대상으로, Secrets 권한은 Read and write
4. Actions → notify → Run workflow 로 테스트 메시지를 보낸다.
5. Settings → Pages 에서 source 를 `main` / root 로 지정한다.
6. Claude 루틴을 만든다.
   - 주기: 매시간
   - 레포: 이 레포
   - 지시문: `prompts/monitor.md 를 읽고 그대로 수행하라`
   - 네트워크: Full. 출처 사이트가 다양해 Trusted 로는 막힐 수 있다.

## 테스트
`python3 -m pytest tests` (pytest 없으면 `tests/test_diff_state.py` 의 함수 직접 실행)
