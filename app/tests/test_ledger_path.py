"""The model-call ledger stays inside the board's own `--work` (verdict 14, section S).

Karl's spend record is `~/work/roughcut-ledger.jsonl`; a judge's server started with a
`--work` under the loop's dir appended ~21 rows to it, because `config.ledger_path()`
ignored `--work`. Now a board on any `--work` but the default logs to
`<work>/roughcut-ledger.jsonl`, and the pass tools it starts inherit that."""
import os
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

import server  # noqa: E402
from roughcut import config  # noqa: E402


def _home(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "home"
    (home / "work" / "app").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ROUGHCUT_LEDGER", raising=False)
    return home


def test_a_board_on_its_own_work_logs_there_and_its_tools_inherit_it(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    work = tmp_path / "judge" / "work"
    got = server.keep_ledger_in(work)
    assert got == work / "roughcut-ledger.jsonl"
    assert config.ledger_path() == got
    # a pass tool the server starts (a child process) logs to the same file
    out = subprocess.run([sys.executable, "-c",
                          "import sys; sys.path.insert(0, sys.argv[1]); "
                          "from roughcut import config; print(config.ledger_path())",
                          str(APP.parent)], capture_output=True, text=True, env=os.environ.copy())
    assert out.stdout.strip() == str(got), out.stderr
    assert not (home / "work" / "roughcut-ledger.jsonl").exists()


def test_karls_board_on_the_default_work_keeps_his_ledger(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert server.keep_ledger_in(home / "work" / "app") == home / "work" / "roughcut-ledger.jsonl"
    assert "ROUGHCUT_LEDGER" not in os.environ


def test_an_explicit_ledger_always_wins(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    mine = tmp_path / "mine.jsonl"
    monkeypatch.setenv("ROUGHCUT_LEDGER", str(mine))
    assert server.keep_ledger_in(tmp_path / "elsewhere") == mine
