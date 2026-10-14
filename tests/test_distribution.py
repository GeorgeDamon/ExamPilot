import sys
import tomllib
from pathlib import Path

from autoexam import __version__, cli


def test_version_matches_distribution_metadata():
    root = Path(__file__).resolve().parent.parent
    metadata = tomllib.loads((root / "pyproject.toml").read_text())
    assert metadata["project"]["version"] == __version__
    assert metadata["project"]["license"] == "GPL-3.0-only"
    assert metadata["project"]["scripts"]["exampilot"] == "autoexam.cli:main"


def test_init_preserves_private_files_and_sets_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["exampilot", "init"])
    assert cli.main() == 0
    assert (tmp_path / "config.yaml").exists()
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    (tmp_path / ".env").write_text("DO_NOT_OVERWRITE")
    assert cli.main() == 0
    assert (tmp_path / ".env").read_text() == "DO_NOT_OVERWRITE"


def test_missing_explicit_config_does_not_fall_back(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(
        sys, "argv", ["exampilot", "--config", str(tmp_path / "missing.yaml"), "demo"]
    )
    assert cli.main() == 1
    assert "指定的配置文件不存在" in capsys.readouterr().err
    assert not (tmp_path / "runtime").exists()
