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

## 설치(2호기, 사람)
1. 시크릿: `jis-jobs.env.example` 을 `~/.config/impactflow/jis-jobs.env` 로 복사해 레포 시크릿과 같은 값을 채우고 `chmod 600`.
2. 잡 전용 클론: `cd ~/impactflow-agents-runner/agents/shared/workspace && scripts/provision_runner.sh --bootstrap`
   (REPOS 의 `korea-security-watch-jobbot` 을 받는다 — 원격 HaydenRyu/korea-security-watch). push 자격 = 그 맥의 gh 신원.
3. 첫 지점 기록(옛 알림 재발송 방지): `bash ~/korea-security-watch-jobbot/ops/jis-mac/run.sh notify --init`
4. 설치: `scripts/provision_runner.sh --install-one kr.impactflow.jis-jobs`
5. 검증: `tail -f ~/Library/Logs/impactflow/jis-jobs.log` 에서 firms 커밋 1회·`notify` 무변화 확인 →
   GitHub Actions 의 같은 워크플로를 끈다(schedule·push 트리거 제거 — 둘 다 켜 두면 수집·알림이 두 번 돈다).

롤백 = `launchctl bootout gui/$(id -u)/kr.impactflow.jis-jobs` 후 GitHub Actions 워크플로를 다시 켠다.
