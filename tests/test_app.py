from __future__ import annotations

from pathlib import Path

from dafeiyu.app import list_skills
from dafeiyu.config import Config
from dafeiyu.skills import SkillStore


def test_app_imports_and_lists_skills(tmp_path: Path, capsys) -> None:
    store = SkillStore(tmp_path / "skills")
    store.create(name="测试技能", content="# 测试\n内容")
    config = Config(skills_dir=tmp_path / "skills")
    assert list_skills(config) is True
    output = capsys.readouterr().out
    assert "测试技能" in output
