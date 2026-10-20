"""재생 서버 세션 상한(10/20 맥부기 실측): 한 호스트가 짧은 창에 열 수 있는 재생 세션 5~6개, 그 뒤 모든 주소 403, 35~40초 뒤 회복.
연속 18대를 찍으면 앞 5~6대만 남던 원인 — 묶음(SESSION_CAP)·회복 확인·403 재시도·예산·회전을 네트워크 없이 검증한다."""
import subprocess, sys, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cctv_snap as cs


class FakeServer:
    """세션 상한 cap 개 — 넘기면 403. recover() 가 창을 비운다(실제 서버는 35~40초 뒤)."""
    def __init__(self, cap=5, bad=()):
        self.cap, self.bad, self.open, self.log, self.lock = cap, set(bad), 0, [], threading.Lock()
        self.concurrent, self.peak = 0, 0

    def grab(self, ff, url, out):
        with self.lock:
            self.concurrent += 1
            self.peak = max(self.peak, self.concurrent)
            self.log.append(url)
            if self.open >= self.cap:
                self.concurrent -= 1
                return False, "403"
            self.open += 1
        try:
            if url in self.bad:
                return False, "timeout"
            out.write_bytes(b"x" * 3000)
            return True, "ok"
        finally:
            with self.lock:
                self.concurrent -= 1

    def recover(self, url, deadline):
        self.open = 0
        self.log.append("WAIT")
        return True


def _jobs(n, tmp):
    return [(i, f"u{i}", tmp / f"{i}.jpg") for i in range(n)]


def test_grab_batches_covers_all_18_within_session_cap(tmp_path):
    srv = FakeServer(cap=5)
    res = cs.grab_batches("ff", _jobs(18, tmp_path), deadline=1e9, grab=srv.grab, wait=srv.recover, clock=lambda: 0)
    assert all(v == "ok" for v in res.values()) and len(res) == 18
    assert srv.log.count("WAIT") == 3                       # 묶음 4개 사이 회복 확인 3회
    assert srv.peak <= cs.SESSION_CAP                        # 동시에 연 세션이 상한을 넘지 않는다
    # 묶음 사이에 WAIT 가 있어 묶음마다 상한 이하로 연다
    groups, cur = [], 0
    for x in srv.log:
        if x == "WAIT":
            groups.append(cur); cur = 0
        else:
            cur += 1
    groups.append(cur)
    assert groups == [5, 5, 5, 3] and all(g <= cs.SESSION_CAP for g in groups)


def test_403_is_retried_once_after_recovery_then_reported(tmp_path):
    """상한에 걸린 403 은 회복 뒤 한 번 더 — 그래도 403 이면 사유로 남긴다(무한 재시도 없음)."""
    srv = FakeServer(cap=3)                                  # 묶음 5 중 뒤 2개가 403 으로 떨어지는 서버
    res = cs.grab_batches("ff", _jobs(5, tmp_path), deadline=1e9, grab=srv.grab, wait=srv.recover, clock=lambda: 0)
    assert sorted(res) == [0, 1, 2, 3, 4] and list(res.values()).count("ok") == 5
    assert srv.log.count("WAIT") == 1 and len([x for x in srv.log if x != "WAIT"]) == 7   # 5 + 재시도 2

    class Always403:
        def grab(self, ff, url, out): return False, "403"
    calls = []
    res = cs.grab_batches("ff", _jobs(2, tmp_path), deadline=1e9, grab=Always403().grab,
                          wait=lambda u, d: calls.append(u) or True, clock=lambda: 0)
    assert res == {0: "403", 1: "403"} and len(calls) == 1


def test_budget_and_blocked_leave_reasons_not_silence(tmp_path):
    srv = FakeServer(cap=5)
    # 첫 묶음 뒤 예산 소진 → 나머지는 'budget'(시도 안 함), 회복 대기도 안 한다
    res = cs.grab_batches("ff", _jobs(8, tmp_path), deadline=10, grab=srv.grab, wait=srv.recover, clock=lambda: 11)
    assert [res[i] for i in range(5)] == ["ok"] * 5 and [res[i] for i in range(5, 8)] == ["budget"] * 3
    assert "WAIT" not in srv.log
    # 회복이 안 되면(주소 만료 의심) 나머지는 'blocked'
    srv = FakeServer(cap=5)
    res = cs.grab_batches("ff", _jobs(8, tmp_path), deadline=1e9, grab=srv.grab, wait=lambda u, d: False, clock=lambda: 0)
    assert [res[i] for i in range(5, 8)] == ["blocked"] * 3
    # 다른 사유(timeout)는 재시도 없이 그대로
    srv = FakeServer(cap=5, bad={"u1"})
    res = cs.grab_batches("ff", _jobs(3, tmp_path), deadline=1e9, grab=srv.grab, wait=srv.recover, clock=lambda: 0)
    assert res == {0: "ok", 1: "timeout", 2: "ok"}


def test_wait_recover_polls_until_200_or_deadline():
    t = {"now": 0.0}
    def clock(): return t["now"]
    def sleep(s): t["now"] += s
    answers = iter([False, False, True])
    assert cs.wait_recover("u", deadline=1e9, probe=lambda u: next(answers), sleep=sleep, clock=clock) is True
    assert t["now"] == 3 * cs.RECOVER_POLL
    t["now"] = 0
    assert cs.wait_recover("u", deadline=1e9, probe=lambda u: False, sleep=sleep, clock=clock) is False
    assert t["now"] >= cs.RECOVER_MAX                        # 상한까지만 기다린다
    t["now"] = 0
    assert cs.wait_recover("u", deadline=12, probe=lambda u: False, sleep=sleep, clock=clock) is False
    assert t["now"] < cs.RECOVER_MAX                         # 회차 예산이 더 이르면 거기서 멈춘다


def test_grab_reason_comes_from_ffmpeg_stderr_and_file_size(tmp_path, monkeypatch):
    class R:
        def __init__(self, rc, err=b""): self.returncode, self.stderr = rc, err
    out = tmp_path / "a.jpg"
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R(8, b"Server returned 403 Forbidden (access denied)"))
    assert cs.grab("ff", "u", out) == (False, "403")
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R(1, b"Connection refused"))
    assert cs.grab("ff", "u", out) == (False, "error")
    def boom(*a, **k): raise subprocess.TimeoutExpired("ff", cs.GRAB_TIMEOUT)
    monkeypatch.setattr(cs.subprocess, "run", boom)
    assert cs.grab("ff", "u", out) == (False, "timeout")
    monkeypatch.setattr(cs.subprocess, "run", lambda *a, **k: R(0))
    assert cs.grab("ff", "u", out) == (False, "small")       # rc 0 이어도 파일이 없거나 작으면 실패
    out.write_bytes(b"x" * 3000)
    assert cs.grab("ff", "u", out) == (True, "ok")


def test_rotation_and_budget_fit_the_5_minute_cycle():
    assert cs.rotated([0, 1, 2, 3], 1) == [1, 2, 3, 0] and cs.rotated([0, 1, 2, 3], 5) == [1, 2, 3, 0]
    assert cs.rotated([], 3) == [] and sorted(cs.rotated(list(range(18)), 7)) == list(range(18))
    # 18대 = 묶음 4개: 통상 회복 40초 × 3 + 묶음당 ffmpeg 몇 초 ≈ 120~140초가 예산 안, 예산 + 마지막 묶음 상한 + 공역·업로드 여유가 300초 안
    batches = -(-18 // cs.SESSION_CAP)
    assert (batches - 1) * 40 + batches * 5 < cs.BUDGET
    assert cs.BUDGET + cs.GRAB_TIMEOUT + 60 < 300
    assert cs.RECOVER_MAX >= 40 and cs.RECOVER_POLL <= 10
