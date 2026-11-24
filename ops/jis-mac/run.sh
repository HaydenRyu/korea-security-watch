#!/bin/bash
# JIS 정기 잡을 자동화 맥 launchd 에서 돌리는 진입점(GitHub Actions 대체, 2026-11-24 「호스티드 러너 0」).
# 이 레포는 개인 레포라 조직 self-hosted 러너를 못 쓴다 → impactflow-agents 의 launchd 체계(provision_runner)로 설치한다.
# launchd 라벨 하나(kr.impactflow.jis-jobs, 3분 주기)가 매 틱마다 할 일을 고른다 — 워크플로 5개와 같은 일정:
#
#   notify   매 틱   origin/main 에서 state/outbox.json·reports/*.md 를 바꾼 커밋을 커밋마다 보낸다(notify·reports-notify 의 push 트리거 대체)
#   firms    UTC :00/:15/:30/:45   FIRMS·대기·항행경보·뉴스·훈련·관측 수집 → state/* 커밋·push   (firms.yml)
#   calendar UTC 03·09·15·21시 :10  위험 달력·UCDP(매달 21~28일)·위성영상 글 → data/* 커밋·push  (weekly.yml calendar)
#   watchdog UTC 00·06·12·18시 :23  state/events.jsonl 정체(3~9h) 경보                            (watchdog.yml)
#   summary  UTC 일 11:00           주간 요약 ntfy                                                  (weekly.yml summary)
#
# 수동: run.sh <job> — 그 잡 하나만 즉시(일정 무시). run.sh notify --init = 현재 origin 을 '보낸 지점'으로만 기록.
# 일정 판정은 「마지막 예정 시각 ≤ 지금」과 기록(stamp)을 비교한다 — 맥이 잠들어 지나친 회차는 깨어난 틱에서 한 번 돈다.
# 처음 설치(기록 없음)에는 notify·calendar·watchdog·summary 를 돌리지 않고 현재 지점만 기록한다(옛 알림 재발송 방지).
#
# 전환 안전장치(이중 실행 방지): 잡마다 대체한 GitHub 워크플로가 켜져 있거나 회차가 진행·대기 중이면 그 잡은 GitHub 몫이다 —
# 돌리지 않고 「했다」로만 기록한다(notify 는 지점을 origin/main 끝으로, 나머지는 stamp). 워크플로를 끄는 순간 그 잡이 넘어온다.
# gh 로 판정이 안 되면 이번 틱 전체를 건너뛴다(rc 3). 전환 순간 ≤3분(한 틱) 사이에 push 된 알림은 양쪽에서 한 번씩 갈 수 있다(누락보다 중복).
set -uo pipefail

ONLY="${1:-}"; [ $# -gt 0 ] && shift
REPO="${JIS_REPO:-$HOME/korea-security-watch-jobbot}"   # 잡 전용 클론(사람 편집 없음 — hard reset 대상)
GH_REPO="HaydenRyu/korea-security-watch"
VENV="${JIS_VENV:-$HOME/.venvs/jis-jobs}"
STATE="${JIS_STATE:-$HOME/.local/state/jis-jobs}"
LOGDIR="$HOME/Library/Logs/impactflow"
LOG="$LOGDIR/jis-jobs.log"
mkdir -p "$LOGDIR" "$STATE"
exec >>"$LOG" 2>&1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# 동시 실행 방지(수동 실행과 launchd 틱이 겹칠 때). 잠금은 소유 pid 로 판정한다 — 살아 있으면 건너뛰고, 죽었으면 넘겨받는다.
# 지우는 것도 소유자만(다른 프로세스가 넘겨받은 잠금을 EXIT trap 이 지우지 않게).
LOCK="$STATE/lock"
take_lock() {
  if mkdir "$LOCK" 2>/dev/null; then echo $$ > "$LOCK/pid"; return 0; fi
  local owner; owner=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then return 1; fi
  rm -rf "$LOCK" && mkdir "$LOCK" 2>/dev/null && echo $$ > "$LOCK/pid"
}
take_lock || { echo "$(date '+%F %T') 이전 틱(pid $(cat "$LOCK/pid" 2>/dev/null)) 진행 중 — 건너뜀"; exit 0; }
trap '[ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK"' EXIT

# 시크릿: GitHub 시크릿은 읽어 올 수 없어 사람이 이 파일에 옮긴다(README). chmod 600.
ENVF="$HOME/.config/impactflow/jis-jobs.env"
if [ -f "$ENVF" ]; then
  [ "$(stat -f %Lp "$ENVF")" = 600 ] || echo "[경고] $ENVF 권한이 600 이 아님 — chmod 600"
  set -a; . "$ENVF"; set +a
fi

# 정본 인터프리터(provision_runner.sh PY) — 없으면 PATH 의 python3(≥3.11)
BASEPY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
[ -x "$BASEPY" ] || BASEPY="$(command -v python3)"
if [ ! -x "$VENV/bin/python" ]; then "$BASEPY" -m venv "$VENV" || { echo "[실패] venv 생성"; exit 1; }; fi
PY="$VENV/bin/python"
# drills_kr 첨부 해석기(firms.yml 과 같은 고정 버전). 한 번만.
[ -f "$VENV/.drills-deps" ] || { "$PY" -m pip install -q olefile==0.47 pypdf==6.14.2 && touch "$VENV/.drills-deps"; } \
  || echo "[경고] 첨부 해석기 설치 실패 — drills 는 본문만"

cd "$REPO" 2>/dev/null || { echo "[실패] 클론 없음: $REPO (provision_runner.sh --bootstrap)"; exit 1; }
# 잡 전용 클론만 hard reset 한다 — 최상위·origin 이 맞지 않으면 멈춘다
[ "$(git rev-parse --show-toplevel 2>/dev/null)" = "$(pwd -P)" ] || { echo "[실패] $REPO 가 레포 최상위가 아님"; exit 1; }
case "$(git remote get-url origin 2>/dev/null)" in
  *github.com[:/]"$GH_REPO"|*github.com[:/]"$GH_REPO".git) ;;
  *) echo "[실패] $REPO 의 origin 이 $GH_REPO 가 아님 — 잡 전용 클론만 쓴다"; exit 1 ;;
esac

sync_main() {  # 잡 클론을 origin/main 으로 맞춘다(사람 편집 없음)
  git fetch -q origin main || { sleep $((RANDOM % 5 + 3)); git fetch -q origin main; } || return 1
  # main 으로 못 옮기면 멈춘다 — 다른 브랜치를 origin/main 으로 hard reset 하지 않는다
  git checkout -q main 2>/dev/null || git checkout -q -B main origin/main || return 1
  [ "$(git branch --show-current)" = main ] || return 1
  git reset -q --hard origin/main && git clean -qfd -- state data
}

# push_commit "<msg>" <paths…> — firms.yml·weekly.yml commit 스텝과 같은 규칙(-X theirs 재시도 6회).
#   -X theirs = 충돌 칸은 이번 회차 값(rebase 에서 theirs = 다시 얹는 이 회차 커밋). 이 경로들은 그 잡만 쓰고(firms.yml 75행 근거와 같다)
#   GitHub 쪽은 전환 안전장치가 막으므로 늦게 push 하는 쪽이 늦게 수집한 쪽이다. 6회 모두 실패하면 커밋을 refs/jis-unpushed/ 에 남긴다.
push_commit() {
  local msg="$1"; shift
  [ -z "$(git status --porcelain -- "$@")" ] && { echo "unchanged"; return 0; }
  local add=() p
  for p in "$@"; do [ -e "$p" ] && add+=("$p"); done
  [ ${#add[@]} -gt 0 ] && git add ${add[@]+"${add[@]}"}
  git add -u -- "$@"   # 지워진 경로도 반영
  git -c user.name="jis-jobs[bot]" -c user.email="jis-jobs@users.noreply.github.com" commit -q -m "$msg" || return 1
  local left own i
  left=$(git status --porcelain --untracked-files=no)
  [ -n "$left" ] && { echo "커밋 밖 변경 버림:"; echo "$left"; git reset -q --hard HEAD; }
  own=$(git rev-parse HEAD)
  for i in 1 2 3 4 5 6; do
    git pull --rebase -X theirs -q origin main && git push -q origin HEAD:main && return 0
    git rebase --abort 2>/dev/null || true
    git reset -q --hard "$own"
    sleep $((RANDOM % 15 + 5))
  done
  git update-ref "refs/jis-unpushed/$(date -u +%Y%m%dT%H%M%SZ)" "$own"
  echo "[실패] push 6회 실패 — 커밋 $own 을 refs/jis-unpushed/ 에 보존(다음 동기화가 작업 트리를 되돌린다)"; return 1
}

# due <job> — 이번 틱에 돌 차례인가. 마지막 예정 시각(UTC) 키를 출력하고 rc 0(돌 차례)/1(아님)/2(첫 실행: 기록만)
due() {
  "$PY" - "$1" "$STATE/$1.stamp" <<'PY'
import datetime as dt, pathlib, sys
job, stamp = sys.argv[1], pathlib.Path(sys.argv[2])
now = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0)
def last(hours, minute, weekday=None):
    t = now.replace(minute=minute)
    for _ in range(24 * 8):
        if t <= now and t.hour in hours and (weekday is None or t.weekday() == weekday):
            return t
        t -= dt.timedelta(hours=1)
if job == "firms":
    key = now.replace(minute=now.minute // 15 * 15)
elif job == "calendar":
    key = last({3, 9, 15, 21}, 10)
elif job == "watchdog":
    key = last({0, 6, 12, 18}, 23)
elif job == "summary":
    key = last({11}, 0, weekday=6)
else:
    sys.exit(3)
k = key.strftime("%Y-%m-%dT%H:%MZ")
print(k)
prev = stamp.read_text().strip() if stamp.exists() else ""
sys.exit(2 if (not prev and job != "firms") else (0 if prev != k else 1))
PY
}

run_firms() {
  "$PY" scripts/firms.py; local fetch=$?
  "$PY" scripts/air_count.py
  timeout_run 300 "$PY" scripts/navwarn_kr.py || echo "[경고] navwarn_kr 실패"
  timeout_run 180 "$PY" scripts/news_kr.py --public || echo "[경고] news_kr 실패"
  timeout_run 300 "$PY" scripts/drills_kr.py || echo "[경고] drills_kr 실패"
  "$PY" scripts/obs_link.py || echo "[경고] obs_link 실패"
  [ $fetch -ne 0 ] && echo "FIRMS 수집 실패 — state/firms.json 미갱신(직전 값 유지)"
  push_commit "firms: $(date -u +%FT%H:%MZ)" state/firms.json state/firms_cells.json state/air.json state/air.jsonl \
    state/navwarn_kr.json state/news_kr.json state/drills_kr.json state/obs_candidates.json
}

run_calendar() {
  "$PY" scripts/risk_calendar.py || return 1
  local d; d=$(TZ=Asia/Seoul date +%-d)
  if { [ "$d" -ge 21 ] && [ "$d" -le 28 ]; } || [ ! -f data/conflicts.json ] || [ "$ONLY" = calendar ]; then
    "$PY" scripts/ucdp_month.py || echo "[경고] ucdp_month 실패"
  else
    echo "conflicts skip — 매달 21~28일만(오늘 ${d}일)"
  fi
  "$PY" scripts/imagery_feeds.py || echo "[경고] imagery_feeds 실패"
  push_commit "calendar: $(date -u +%FT%H:%MZ)" data/risk_calendar.json data/forecast_prob.json \
    data/forecast_ledger.jsonl data/conflicts.json data/imagery_reports.json
}

run_watchdog() {  # watchdog.yml stale check 와 같은 식
  local out stale hours
  out=$("$PY" - <<'PY'
import json, datetime as dt, pathlib
p = pathlib.Path("state/events.jsonl")
lines = [l for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
if not lines:
    print("0 0"); raise SystemExit
last = dt.datetime.fromisoformat(json.loads(lines[-1])["checked_at"])
h = (dt.datetime.now(dt.timezone.utc) - last).total_seconds() / 3600
print(f"{int(3 <= h < 9)} {h:.1f}")
PY
) || return 1
  stale=${out%% *}; hours=${out#* }
  echo "watchdog stale=$stale hours=$hours"
  # 보내기 실패 = 잡 실패 → stamp 를 남기지 않아 다음 틱에 다시 보낸다
  if [ "$stale" = 1 ]; then "$PY" scripts/ntfy_send.py me "안보 감시 정지" "마지막 확인 ${hours}시간 전. 루틴 상태를 확인하세요." 4 || return 1; fi
  return 0
}

run_summary() {
  local w; w="$("$PY" scripts/weekly_summary.py)" || return 1
  "$PY" scripts/ntfy_send.py me "$(printf '%s' "$w" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["title"])')" \
    "$(printf '%s' "$w" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["message"])')" 2
}

# notify — 보낸 지점(notify.sha) 이후 origin/main 커밋 중 outbox·보고서를 바꾼 것을 커밋 순서대로 보낸다.
# push 트리거(notify.yml·reports-notify.yml)는 push 마다 그 커밋의 outbox 를 보냈다 — 틱 사이에 여러 push 가 있어도
# 커밋마다 보내 같은 결과를 낸다. 발송 단위(커밋 × 채널: ntfy · 웹 푸시 · 보고서 파일)마다 완료를 notify.done 에 적어
# 재시도 때 이미 간 것은 다시 보내지 않는다. 실패한 단위가 있으면 지점은 그 커밋에서 멈추고 다음 틱에 그 단위만 다시 보낸다.
# 같은 커밋이 NOTIFY_MAX_TRIES 틱 연속 실패하면 남은 단위를 포기 기록하고 넘어간다(한 커밋이 뒤 알림을 영영 막지 않게).
NOTIFY_MAX_TRIES=10
notify_unit() {  # notify_unit <commit> <unit> cmd… — 이미 끝난 단위는 건너뛰고, 성공하면 완료 기록
  local c="$1" u="$2"; shift 2
  grep -qxF "$c $u" "$STATE/notify.done" 2>/dev/null && return 0
  "$@" || return 1
  echo "$c $u" >> "$STATE/notify.done"
}
run_notify() {
  local f="$STATE/notify.sha" from head c tmp rc=0 r title tries crc
  head=$(git rev-parse origin/main) || return 1
  if [ ! -s "$f" ] || [ "${1:-}" = "--init" ]; then echo "$head" > "$f"; : > "$STATE/notify.done"; : > "$STATE/notify.tries"; echo "notify 지점 초기화 $head"; return 0; fi
  from=$(cat "$f")
  git merge-base --is-ancestor "$from" "$head" 2>/dev/null || { echo "[경고] notify 지점 $from 이 origin/main 조상이 아님 — 재초기화"; echo "$head" > "$f"; return 0; }
  tmp=$(mktemp -d "$STATE/notify.XXXXXX")
  for c in $(git rev-list --reverse "$from..$head" -- state/outbox.json 'reports/*.md'); do
    crc=0
    if git diff-tree --no-commit-id --name-only -r "$c" -- state/outbox.json | grep -q .; then
      git show "$c:state/outbox.json" > "$tmp/outbox.json" || { rc=1; break; }
      notify_unit "$c" ntfy "$PY" scripts/ntfy_send.py outbox "$tmp/outbox.json" || { echo "[경고] ntfy 실패($c)"; crc=1; }
      notify_unit "$c" webpush "$PY" scripts/push_send.py outbox "$tmp/outbox.json" || { echo "[경고] 웹 푸시 실패($c)"; crc=1; }   # 두 채널은 독립(notify.yml 과 같다)
    fi
    for r in $(git diff-tree --no-commit-id --name-only --diff-filter=AM -r "$c" -- 'reports/*.md'); do
      title=$(git show "$c:$r" | head -1 | sed 's/^# *//')
      notify_unit "$c" "report:$r" "$PY" scripts/ntfy_send.py me "JIS 보고서" "${title} — https://github.com/HaydenRyu/korea-security-watch/blob/main/${r}" 3 \
        || { echo "[경고] 보고서 알림 실패($c $r)"; crc=1; }
    done
    if [ $crc -ne 0 ]; then
      tries=$(grep -cxF "$c" "$STATE/notify.tries" 2>/dev/null); tries=$(( ${tries:-0} + 1 ))
      echo "$c" >> "$STATE/notify.tries"
      if [ "$tries" -lt "$NOTIFY_MAX_TRIES" ]; then rc=1; break; fi
      echo "[실패] notify $c ${tries}틱 연속 실패 — 남은 발송 포기하고 다음 커밋으로"; rc=1
    fi
    echo "$c" > "$f"
  done
  [ $rc -eq 0 ] && { echo "$head" > "$f"; : > "$STATE/notify.done"; : > "$STATE/notify.tries"; }
  rm -rf "$tmp"
  return $rc
}

# GitHub 몫 판정 — gha_owned <job>: 0 = 아직 GitHub 몫(워크플로 켜짐 또는 회차 진행·대기), 1 = launchd 몫.
# 틱마다 gh 로 한 번 읽는다(load_gha). 판정 불가는 load_gha 가 rc≠0 로 틱 전체를 멈춘다.
GHA_ACTIVE=""; GHA_BUSY=""
load_gha() {
  GHA_ACTIVE=$(gh api "repos/$GH_REPO/actions/workflows?per_page=100" \
      --jq '.workflows[] | select(.state == "active") | .path | split("/") | last' 2>/dev/null) || return 1
  GHA_BUSY=$( { gh api "repos/$GH_REPO/actions/runs?status=in_progress&per_page=100" --jq '.workflow_runs[].path' &&
               gh api "repos/$GH_REPO/actions/runs?status=queued&per_page=100" --jq '.workflow_runs[].path'; } 2>/dev/null \
             | sed 's#.*/##; s#@.*##') || return 1
}
gha_owned() {
  local wf
  case "$1" in
    firms) set -- firms.yml ;;
    calendar|summary) set -- weekly.yml ;;
    watchdog) set -- watchdog.yml ;;
    notify) set -- notify.yml reports-notify.yml ;;
  esac
  for wf in "$@"; do
    printf '%s\n' "$GHA_ACTIVE" "$GHA_BUSY" | grep -qxF "$wf" && return 0
  done
  return 1
}

timeout_run() {  # timeout_run <초> cmd… — GNU timeout 없이(macOS) 상한
  local s="$1"; shift
  "$@" & local p=$!
  ( sleep "$s"; kill "$p" 2>/dev/null ) & local w=$!
  wait "$p"; local r=$?
  kill "$w" 2>/dev/null; wait "$w" 2>/dev/null
  return $r
}

echo "=== $(date '+%F %T') 틱 only=[$ONLY]"
command -v gh >/dev/null || { echo "[실패] gh CLI 없음"; exit 3; }
load_gha || { echo "[보류] GitHub 워크플로 상태 판정 불가(gh) — 이중 실행 방지로 이번 틱 건너뜀"; exit 3; }
sync_main || { echo "[실패] origin 동기화 실패"; exit 1; }

rc=0
if [ -n "$ONLY" ]; then
  case "$ONLY" in
    notify) if [ "${1:-}" != "--init" ] && gha_owned notify; then echo "[대기] notify 는 아직 GitHub 몫 — 건너뜀"; exit 0; fi
            run_notify "$@" || rc=1 ;;
    firms|calendar|watchdog|summary)
            if gha_owned "$ONLY"; then echo "[대기] $ONLY 는 아직 GitHub 몫 — 건너뜀"; exit 0; fi
            "run_$ONLY" || rc=1 ;;
    *) echo "[!] 알 수 없는 job '$ONLY' (notify|firms|calendar|watchdog|summary)"; exit 2 ;;
  esac
  echo "=== $(date '+%F %T') 종료 rc=$rc"; exit $rc
fi

if gha_owned notify; then
  git rev-parse origin/main > "$STATE/notify.sha"; : > "$STATE/notify.done"; : > "$STATE/notify.tries"
  echo "[대기] notify 는 GitHub 몫 — 지점만 origin/main 끝으로"
else
  run_notify || { echo "[실패] notify"; rc=1; }
fi
for job in firms calendar watchdog summary; do
  key=$(due "$job"); d=$?
  if [ $d -eq 0 ] && gha_owned "$job"; then echo "$key" > "$STATE/$job.stamp"; echo "[대기] $job ($key) 은 GitHub 몫 — 기록만"; continue; fi
  case $d in
    0) echo "--- $job ($key)"
       if "run_$job"; then echo "$key" > "$STATE/$job.stamp"; else echo "[실패] $job"; rc=1; fi
       sync_main || { echo "[실패] origin 동기화 실패 — 남은 잡은 다음 틱에"; rc=1; break; } ;;
    2) echo "$key" > "$STATE/$job.stamp"; echo "$job 첫 실행 — 지점 $key 만 기록" ;;
  esac
done
echo "=== $(date '+%F %T') 종료 rc=$rc"
exit $rc
