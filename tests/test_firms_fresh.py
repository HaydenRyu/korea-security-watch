import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import firms as f  # noqa: E402
import firms_fresh as ff  # noqa: E402

NOW = "2026-11-11T06:00:00+00:00"


def _run(tmp_path, doc, now=NOW):
    p = tmp_path / "firms.json"
    if doc is not None:
        p.write_text(json.dumps(doc))
    env = {**os.environ, "FIRMS_FRESH_NOW": now}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "firms_fresh.py"), "--file", str(p)],
                       env=env, capture_output=True, text=True)
    return r.returncode, r.stdout


def test_fixtures_five_cases(tmp_path):
    # 티켓 4057e099 AC: 고정 시각 픽스처 5종(2h59m·3h00m·3h01m·누락·미래 15분)
    assert _run(tmp_path, {"fetched_at": "2026-11-11T03:01:00+00:00"})[0] == 0   # 2h59m
    assert _run(tmp_path, {"fetched_at": "2026-11-11T03:00:00+00:00"})[0] == 0   # 정확히 3h 는 통과
    assert _run(tmp_path, {"fetched_at": "2026-11-11T02:59:00+00:00"})[0] == 1   # 3h01m
    assert _run(tmp_path, {"count": 0})[0] == 1                                  # 누락
    assert _run(tmp_path, {"fetched_at": "2026-11-11T06:15:00+00:00"})[0] == 1   # 미래 15분


def test_edge_cases(tmp_path):
    assert _run(tmp_path, {"fetched_at": "2026-11-11T06:10:00+00:00"})[0] == 0   # 미래 정확히 10분은 허용
    assert _run(tmp_path, {"fetched_at": "어제"})[0] == 1                         # 파싱 실패
    assert _run(tmp_path, None)[0] == 1                                          # 파일 없음
    assert _run(tmp_path, [1, 2])[0] == 1                                        # 객체 아님
    assert _run(tmp_path, {"fetched_at": "2026-11-11T12:00:00+09:00"})[0] == 0   # 다른 시간대도 UTC 로 비교(= 03:00Z)
    assert _run(tmp_path, {"fetched_at": "2026-11-11T05:00:00Z"})[0] == 0


def test_fetch_logs_each_attempt_and_retries_only_oserror(capsys):
    calls = {"n": 0}

    class Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"latitude,longitude\n37.0,127.0\n"

    def flaky(url, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError(101, "Network is unreachable")
        return Resp()

    clock = iter([0.0, 40.0, 90.0, 95.5]).__next__
    rows = f.fetch("SECRETKEY", "VIIRS_SNPP_NRT", _open=flaky, _sleep=lambda s: None, _clock=clock)
    out = capsys.readouterr().out
    assert rows == [{"latitude": "37.0", "longitude": "127.0"}]
    assert "시도 1/3 실패 · 경과 40.0s · OSError" in out and "시도 2/3 실패 · 경과 90.0s" in out
    assert "시도 3/3 성공 · 경과 95.5s" in out
    assert "SECRETKEY" not in out                                                # 키 비노출

    def dead(url, timeout):
        raise OSError(101, "Network is unreachable")
    try:
        f.fetch("K", "VIIRS_SNPP_NRT", _open=dead, _sleep=lambda s: None)
        raise AssertionError("should raise")
    except OSError:
        pass
    out = capsys.readouterr().out
    assert out.count("실패") == 3 and "시도 3/3 실패" in out and "재시도" in out.split("시도 3/3")[0]

    def boom(url, timeout):
        raise ValueError("bad")
    try:
        f.fetch("K", "VIIRS_SNPP_NRT", _open=boom, _sleep=lambda s: None)
        raise AssertionError("should raise")
    except ValueError:
        pass
    out = capsys.readouterr().out
    assert "시도 1/3 실패" in out and "재시도" not in out                       # 통신 오류 밖은 재시도 안 함(현행 유지)
