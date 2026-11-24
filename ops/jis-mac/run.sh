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
set -uo pipefail

ONLY="${1:-}"; [ $# -gt 0 ] && shift
REPO="${JIS_REPO:-$HOME/korea-security-watch-jobbot}"   # 잡 전용 클론(사람 편집 없음 — hard reset 대상)
VENV="${JIS_VENV:-$HOME/.venvs/jis-jobs}"
STATE="${JIS_STATE:-$HOME/.local/state/jis-jobs}"
LOGDIR="$HOME/Library/Logs/impactflow"
LOG="$LOGDIR/jis-jobs.log"
mkdir -p "$LOGDIR" "$STATE"
exec >>"$LOG" 2>&1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# 동시 실행 방지(수동 실행과 launchd 틱이 겹칠 때). 잡히면 이번 틱은 건너뛴다.
LOCK="$STATE/lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +30 2>/dev/null)" ]; then rm -rf "$LOCK"; mkdir "$LOCK" || exit 0
  else echo "$(date '+%F %T') 이전 틱 진행 중 — 건너뜀"; exit 0; fi
fi
trap 'rm -rf "$LOCK"' EXIT

# 시크릿: GitHub 시크릿은 읽어 올 수 없어 사람이 이 파일에 옮긴다(README). chmod 600.
set -a
[ -f "$HOME/.config/impactflow/jis-jobs.env" ] && . "$HOME/.config/impactflow/jis-jobs.env"
set +a

# 정본 인터프리터(provision_runner.sh PY) — 없으면 PATH 의 python3(≥3.11)
BASEPY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
[ -x "$BASEPY" ] || BASEPY="$(command -v python3)"
if [ ! -x "$VENV/bin/python" ]; then "$BASEPY" -m venv "$VENV" || { echo "[실패] venv 생성"; exit 1; }; fi
PY="$VENV/bin/python"
# drills_kr 첨부 해석기(firms.yml 과 같은 고정 버전). 한 번만.
[ -f "$VENV/.drills-deps" ] || { "$PY" -m pip install -q olefile==0.47 pypdf==6.14.2 && touch "$VENV/.drills-deps"; } \
  || echo "[경고] 첨부 해석기 설치 실패 — drills 는 본문만"

cd "$REPO" || { echo "[실패] 클론 없음: $REPO (provision_runner.sh --bootstrap)"; exit 1; }

sync_main() {  # 잡 클론을 origin/main 으로 맞춘다(사람 편집 없음)
  git fetch -q origin main || { sleep $((RANDOM % 5 + 3)); git fetch -q origin main; } || return 1
  git checkout -q main 2>/dev/null || git checkout -q -B main origin/main
  git reset -q --hard origin/main && git clean -qfd -- state data
}

push_commit() {  # push_commit "<msg>" <paths…> — firms.yml·weekly.yml commit 스텝과 같은 규칙(-X theirs 재시도 6회)
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
  echo "[실패] push 6회 실패"; return 1
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
  [ "$stale" = 1 ] && "$PY" scripts/ntfy_send.py me "안보 감시 정지" "마지막 확인 ${hours}시간 전. 루틴 상태를 확인하세요." 4
  return 0
}

run_summary() {
  local w; w="$("$PY" scripts/weekly_summary.py)" || return 1
  "$PY" scripts/ntfy_send.py me "$(printf '%s' "$w" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["title"])')" \
    "$(printf '%s' "$w" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["message"])')" 2
}

# notify — 보낸 지점(notify.sha) 이후 origin/main 커밋 중 outbox·보고서를 바꾼 것을 커밋 순서대로 보낸다.
# push 트리거(notify.yml·reports-notify.yml)는 push 마다 그 커밋의 outbox 를 보냈다 — 틱 사이에 여러 push 가 있어도
# 커밋마다 보내 같은 결과를 낸다. 지점은 커밋 하나를 끝낼 때마다 전진한다(실패하면 그 커밋부터 다음 틱에 재시도).
run_notify() {
  local f="$STATE/notify.sha" from head c tmp rc=0
  head=$(git rev-parse origin/main) || return 1
  if [ ! -s "$f" ] || [ "${1:-}" = "--init" ]; then echo "$head" > "$f"; echo "notify 지점 초기화 $head"; return 0; fi
  from=$(cat "$f")
  git merge-base --is-ancestor "$from" "$head" 2>/dev/null || { echo "[경고] notify 지점 $from 이 origin/main 조상이 아님 — 재초기화"; echo "$head" > "$f"; return 0; }
  tmp=$(mktemp -d "$STATE/notify.XXXXXX")
  for c in $(git rev-list --reverse "$from..$head" -- state/outbox.json 'reports/*.md'); do
    if git diff-tree --no-commit-id --name-only -r "$c" -- state/outbox.json | grep -q .; then
      git show "$c:state/outbox.json" > "$tmp/outbox.json" || { rc=1; break; }
      "$PY" scripts/ntfy_send.py outbox "$tmp/outbox.json" || rc=1
      "$PY" scripts/push_send.py outbox "$tmp/outbox.json" || echo "[경고] 웹 푸시 실패($c)"   # notify.yml 은 ntfy 실패와 무관하게 돌았다
      [ $rc -ne 0 ] && break
    fi
    for r in $(git diff-tree --no-commit-id --name-only --diff-filter=AM -r "$c" -- 'reports/*.md'); do
      title=$(git show "$c:$r" | head -1 | sed 's/^# *//')
      "$PY" scripts/ntfy_send.py me "JIS 보고서" "${title} — https://github.com/HaydenRyu/korea-security-watch/blob/main/${r}" 3 || { rc=1; break; }
    done
    [ $rc -ne 0 ] && break
    echo "$c" > "$f"
  done
  [ $rc -eq 0 ] && echo "$head" > "$f"
  rm -rf "$tmp"
  return $rc
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
sync_main || { echo "[실패] origin 동기화 실패"; exit 1; }

rc=0
if [ -n "$ONLY" ]; then
  case "$ONLY" in
    notify) run_notify "$@" || rc=1 ;;
    firms|calendar|watchdog|summary) "run_$ONLY" || rc=1 ;;
    *) echo "[!] 알 수 없는 job '$ONLY' (notify|firms|calendar|watchdog|summary)"; exit 2 ;;
  esac
  echo "=== $(date '+%F %T') 종료 rc=$rc"; exit $rc
fi

run_notify || { echo "[실패] notify"; rc=1; }
for job in firms calendar watchdog summary; do
  key=$(due "$job"); d=$?
  case $d in
    0) echo "--- $job ($key)"
       if "run_$job"; then echo "$key" > "$STATE/$job.stamp"; else echo "[실패] $job"; rc=1; fi
       sync_main || true ;;
    2) echo "$key" > "$STATE/$job.stamp"; echo "$job 첫 실행 — 지점 $key 만 기록" ;;
  esac
done
echo "=== $(date '+%F %T') 종료 rc=$rc"
exit $rc
