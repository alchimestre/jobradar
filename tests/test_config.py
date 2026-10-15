"""Config loading: environment expansion for source credentials."""

from __future__ import annotations

import pathlib

from jobradar import config


def write_config(tmp_path: pathlib.Path, body: str) -> pathlib.Path:
    path = tmp_path / "config.yaml"
    path.write_text(body)
    return path


def test_env_placeholder_is_expanded(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_TEST_KEY", "s3cret")
    path = write_config(
        tmp_path, "feeds:\n  - name: k\n    type: rss\n    url: https://example.test/${JOBRADAR_TEST_KEY}\n"
    )
    assert config.load_config(path)["feeds"][0]["url"] == "https://example.test/s3cret"


def test_unset_placeholder_is_kept_and_reported(tmp_path, monkeypatch) -> None:
    """A source with no key must be skipped with the variable named, not called with a blank."""
    monkeypatch.delenv("JOBRADAR_ABSENT", raising=False)
    path = write_config(
        tmp_path,
        "feeds:\n  - name: k\n    type: web3career\n    token: ${JOBRADAR_ABSENT}\n",
    )
    feed = config.load_config(path)["feeds"][0]
    assert feed["token"] == "${JOBRADAR_ABSENT}"
    assert config.unset_placeholders(feed) == ["JOBRADAR_ABSENT"]


def test_unset_placeholders_is_empty_for_a_configured_source() -> None:
    assert config.unset_placeholders({"name": "rss", "url": "https://example.test/feed"}) == []


def test_unset_placeholders_reads_list_values_too() -> None:
    assert config.unset_placeholders({"queries": ["ok", "${JOBRADAR_Q}"]}) == ["JOBRADAR_Q"]


def test_defaults_still_apply_when_the_key_is_absent(tmp_path) -> None:
    path = write_config(tmp_path, "timeout: 40\n")
    cfg = config.load_config(path)
    assert cfg["timeout"] == 40
    assert cfg["min_score"] == config.DEFAULTS["min_score"]


def test_env_file_beside_the_config_is_read(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("JOBRADAR_FROM_FILE", raising=False)
    (tmp_path / ".env").write_text("# comment\nJOBRADAR_FROM_FILE=from-file\n")
    path = write_config(
        tmp_path, "feeds:\n  - name: k\n    type: rss\n    url: https://x/${JOBRADAR_FROM_FILE}\n"
    )
    assert config.load_config(path)["feeds"][0]["url"] == "https://x/from-file"


def test_env_file_named_by_the_config_is_read(tmp_path, monkeypatch) -> None:
    """`env_file` comes from the config being loaded, so it has to be resolved before expansion."""
    monkeypatch.delenv("JOBRADAR_FROM_FILE", raising=False)
    (tmp_path / "custom.env").write_text("JOBRADAR_FROM_FILE=custom\n")
    path = write_config(
        tmp_path,
        "env_file: custom.env\nfeeds:\n  - name: k\n    type: rss\n    url: https://x/${JOBRADAR_FROM_FILE}\n",
    )
    assert config.load_config(path)["feeds"][0]["url"] == "https://x/custom"


def test_the_real_environment_beats_the_env_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JOBRADAR_FROM_FILE", "exported")
    (tmp_path / ".env").write_text("JOBRADAR_FROM_FILE=from-file\n")
    path = write_config(
        tmp_path, "feeds:\n  - name: k\n    type: rss\n    url: https://x/${JOBRADAR_FROM_FILE}\n"
    )
    assert config.load_config(path)["feeds"][0]["url"] == "https://x/exported"


def test_a_missing_env_file_is_not_an_error(tmp_path) -> None:
    path = write_config(tmp_path, "timeout: 40\n")
    assert config.load_config(path)["timeout"] == 40
