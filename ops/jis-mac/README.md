# JIS 정기 잡 — 자동화 맥 launchd (GitHub Actions 대체)

2026-11-24 「GitHub 호스티드 러너 0」 지시. 이 레포는 개인 레포라 조직 self-hosted 러너를 쓸 수 없어 GitHub Actions
워크플로 5개(firms·weekly·watchdog·notify·reports-notify)를 2호기(worker) launchd 잡 하나 `kr.impactflow.jis-jobs` 로 옮긴다.
설치·감시는 impactflow-agents 의 launchd 체계(`provision_runner.sh`·`config/host_roles.yaml`·`launchd_healthcheck`)를 쓴다.

| GitHub Actions | launchd(run.sh) | 일정 |
|---|---|---|
| firms.yml | firms | UTC :00/:15/:30/:45 |
| weekly.yml calendar | calendar | UTC 03·09·15·21시 :10 |
| weekly.yml summary | summary | UTC 일 11:00 |
| watchdog.yml | watchdog | UTC 00·06·12·18시 :23 |
| notify.yml · reports-notify.yml (push 트리거) | notify | 3분 틱마다 origin/main 의 새 커밋을 커밋 단위로 |

## 전환 안전장치
잡마다 대체한 GitHub 워크플로(firms → firms.yml · calendar·summary → weekly.yml · watchdog → watchdog.yml · notify → notify.yml·reports-notify.yml)가
**켜져 있거나 회차가 진행·대기 중이면 그 잡은 GitHub 몫**이다 — run.sh 는 돌리지 않고 「했다」로만 기록한다(notify 는 지점을 origin/main 끝으로).
gh 로 판정이 안 되면 그 틱 전체를 건너뛴다(rc 3). 그래서 설치 순서와 상관없이 이중 수집·이중 알림이 없고, **워크플로를 끄는 순간 그 잡이 넘어온다**
(끄는 순간 ≤3분 사이에 push 된 알림만 양쪽에서 한 번씩 갈 수 있다 — 누락보다 중복).
notify 는 커밋 × 채널(ntfy · 웹 푸시 · 보고서) 단위로 완료를 적어 실패한 단위만 다시 보낸다(10틱 연속 실패하면 포기 기록 후 다음 커밋).
push 6회 실패한 수집 커밋은 `refs/jis-unpushed/<시각>` 에 남는다. 잡 클론이 main 으로 옮겨지지 않으면 hard reset 하지 않고 멈춘다.

## 설치(2호기, 사람)
1. 시크릿: `jis-jobs.env.example` 을 `~/.config/impactflow/jis-jobs.env` 로 복사해 레포 시크릿과 같은 값을 채우고 `chmod 600`.
2. 잡 전용 클론: `cd ~/impactflow-agents-runner/agents/shared/workspace && scripts/provision_runner.sh --bootstrap`
   (REPOS 의 `korea-security-watch-jobbot` 을 받는다 — 원격 HaydenRyu/korea-security-watch). push·워크플로 상태 조회 자격 = 그 맥의 gh 신원.
3. 첫 지점 기록(옛 알림 재발송 방지): `bash ~/korea-security-watch-jobbot/ops/jis-mac/run.sh notify --init`
4. 설치: `scripts/provision_runner.sh --install-one kr.impactflow.jis-jobs` — 워크플로가 켜져 있는 동안 틱은 「[대기]」 기록만 한다.
5. 전환: 워크플로 5개를 끈다(`gh workflow disable <파일> -R HaydenRyu/korea-security-watch`) → `tail -f ~/Library/Logs/impactflow/jis-jobs.log` 에서
   firms 커밋 1회 확인.

롤백 = `launchctl bootout gui/$(id -u)/kr.impactflow.jis-jobs` 후 워크플로를 다시 켠다(켜기만 해도 그 잡은 다시 GitHub 몫으로 물러난다).
