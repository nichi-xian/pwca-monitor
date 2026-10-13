from conftest import ROOT

from pwca_monitor.cli import main


def test_test_mode_runs(capsys):
    assert main(["test"]) == 0
    assert "test: OK" in capsys.readouterr().out


def test_missing_config():
    assert main(["--config", "/nonexistent.yaml", "check"]) == 2


def test_unknown_mode():
    import pytest
    with pytest.raises(SystemExit):
        main(["nope"])


def test_monitor_without_secrets_fails(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert main(["monitor", "--state", str(tmp_path / "s.json")]) == 1
    assert not (tmp_path / "s.json").exists()


def test_monitor_broken_state_not_overwritten(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{broken")
    assert main(["monitor", "--console", "--state", str(p)]) == 1
    assert p.read_text() == "{broken"
