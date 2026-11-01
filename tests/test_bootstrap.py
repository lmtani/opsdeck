"""Where the data lives, and the one-time move of a clone's data/ folder."""

from pathlib import Path

from opsdeck.bootstrap import adopt_legacy_data, database_url, resolve_data_dir
from opsdeck.config import Settings


def test_the_data_directory_is_the_users_unless_configured(tmp_path):
    default = resolve_data_dir(Settings())
    assert default.name == "opsdeck" and default.is_absolute()
    assert resolve_data_dir(Settings(data_dir=str(tmp_path))) == tmp_path
    assert resolve_data_dir(Settings(data_dir="~/od")) == Path.home() / "od"
    assert database_url(tmp_path, Settings()) == f"sqlite:///{tmp_path / 'opsdeck.db'}"


def test_a_clones_data_is_copied_once_into_a_new_data_directory(tmp_path):
    legacy = tmp_path / "data"
    (legacy / "images").mkdir(parents=True)
    (legacy / "opsdeck.db").write_bytes(b"db")
    (legacy / "images" / "a.png").write_bytes(b"png")
    (legacy / "links.json").write_text("[]")  # from the JSON storage, long gone: left behind
    target = tmp_path / "share" / "opsdeck"

    assert adopt_legacy_data(target, legacy) is True
    assert (target / "opsdeck.db").read_bytes() == b"db"
    assert (target / "images" / "a.png").read_bytes() == b"png"
    assert not (target / "links.json").exists()
    assert (legacy / "opsdeck.db").exists()  # the old folder stays

    (legacy / "opsdeck.db").write_bytes(b"newer")
    assert adopt_legacy_data(target, legacy) is False  # an existing directory is never touched
    assert (target / "opsdeck.db").read_bytes() == b"db"


def test_nothing_to_adopt(tmp_path):
    target = tmp_path / "share"
    assert adopt_legacy_data(target, tmp_path / "missing") is False
    assert not target.exists()
