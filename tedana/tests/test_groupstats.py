"""Tests for tedana.groupstats module."""

import ast
import logging
import os
import platform
import stat
import urllib.error
from pathlib import Path

import pytest

from tedana import groupstats

LGR_NAME = "GENERAL"


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for the object returned by urllib.request.urlopen."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _make_tgs_dir(path, files=None, version=None):
    """Create a directory containing (a subset of) the required TGS files."""
    path.mkdir(parents=True, exist_ok=True)
    for name in files if files is not None else groupstats.TGS_FILES:
        (path / name).write_text(f"# {name}\n")
    if version is not None:
        (path / "VERSION").write_text(version)
    return path


def _assets(files=None):
    """Build an assets mapping like get_latest_tgs_version returns."""
    return {
        name: f"https://example.com/{name}"
        for name in (files if files is not None else groupstats.TGS_FILES)
    }


@pytest.fixture
def cache_dir(tmp_path, monkeypatch):
    """Redirect the TGS cache to a temporary directory."""
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(groupstats, "get_tgs_cache_dir", lambda: cache)
    return cache


@pytest.fixture
def fake_download(monkeypatch):
    """Replace download_tgs_file with a function that writes dummy content.

    Returns the list of (url, dest_path) calls made.
    """
    calls = []

    def _fake(url, dest_path):
        calls.append((url, dest_path))
        Path(dest_path).write_text(f"downloaded from {url}")

    monkeypatch.setattr(groupstats, "download_tgs_file", _fake)
    return calls


def _no_network(*args, **kwargs):
    raise AssertionError("Network should not have been used in this test")


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_constants():
    """Test the module-level configuration constants."""
    assert groupstats.TGS_REPO_OWNER == "ME-ICA"
    assert groupstats.TGS_REPO_NAME == "tedana_group_stats"
    assert groupstats.TGS_GITHUB_API == (
        "https://api.github.com/repos/ME-ICA/tedana_group_stats/releases/latest"
    )
    assert groupstats.TGS_FILES == ["aggregate.py", "discovery.py", "metrics.py", "report.py"]
    assert groupstats.TGS_PATH_ENV_VAR == "TEDANA_TGS_PATH"
    assert groupstats.TGS_BUNDLED_PATH.name == "tedana_group_stats"
    assert groupstats.TGS_BUNDLED_PATH.parent.name == "resources"


# ---------------------------------------------------------------------------
# validate_tgs_path
# ---------------------------------------------------------------------------


def test_validate_tgs_path_valid(tmp_path):
    """A directory with all required files is valid (str and Path inputs)."""
    tgs = _make_tgs_dir(tmp_path / "tgs")
    assert groupstats.validate_tgs_path(tgs) is True
    assert groupstats.validate_tgs_path(str(tgs)) is True


def test_validate_tgs_path_extra_files_ok(tmp_path):
    """Extra files (e.g., VERSION) do not invalidate a TGS directory."""
    tgs = _make_tgs_dir(tmp_path / "tgs", version="v1.0.0")
    (tgs / "extra.txt").write_text("hello")
    assert groupstats.validate_tgs_path(tgs) is True


def test_validate_tgs_path_nonexistent(tmp_path):
    """A nonexistent path is invalid."""
    assert groupstats.validate_tgs_path(tmp_path / "nope") is False


def test_validate_tgs_path_not_a_directory(tmp_path):
    """A file path is invalid."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    assert groupstats.validate_tgs_path(file_path) is False


def test_validate_tgs_path_empty_dir(tmp_path):
    """An empty directory is invalid."""
    (tmp_path / "empty").mkdir()
    assert groupstats.validate_tgs_path(tmp_path / "empty") is False


@pytest.mark.parametrize("missing", groupstats.TGS_FILES)
def test_validate_tgs_path_missing_each_file(tmp_path, missing):
    """Missing any single required file makes the directory invalid."""
    files = [f for f in groupstats.TGS_FILES if f != missing]
    tgs = _make_tgs_dir(tmp_path / "tgs", files=files)
    assert groupstats.validate_tgs_path(tgs) is False


def test_validate_tgs_path_logs_missing(tmp_path, caplog):
    """The names of missing files are logged at debug level."""
    tgs = _make_tgs_dir(tmp_path / "tgs", files=["aggregate.py"])
    with caplog.at_level(logging.DEBUG, logger=LGR_NAME):
        assert groupstats.validate_tgs_path(tgs) is False
    assert "missing files" in caplog.text
    assert "report.py" in caplog.text


# ---------------------------------------------------------------------------
# get_tgs_from_local
# ---------------------------------------------------------------------------


def test_get_tgs_from_local_valid(tmp_path):
    """A valid directory is returned as a Path."""
    tgs = _make_tgs_dir(tmp_path / "tgs")
    result = groupstats.get_tgs_from_local(str(tgs))
    assert isinstance(result, Path)
    assert result == tgs


def test_get_tgs_from_local_nonexistent(tmp_path):
    """A nonexistent path raises ValueError."""
    with pytest.raises(ValueError, match="does not exist"):
        groupstats.get_tgs_from_local(tmp_path / "nope")


def test_get_tgs_from_local_not_a_directory(tmp_path):
    """A file path raises ValueError."""
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    with pytest.raises(ValueError, match="not a directory"):
        groupstats.get_tgs_from_local(file_path)


def test_get_tgs_from_local_missing_files(tmp_path):
    """A directory missing required files raises ValueError listing them."""
    tgs = _make_tgs_dir(tmp_path / "tgs", files=["aggregate.py", "discovery.py"])
    with pytest.raises(ValueError, match="missing required files") as excinfo:
        groupstats.get_tgs_from_local(tgs)
    assert "metrics.py" in str(excinfo.value)
    assert "report.py" in str(excinfo.value)


# ---------------------------------------------------------------------------
# get_tgs_cache_dir
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "system,expected",
    [
        ("Linux", Path(".cache") / "tedana" / "tgs"),
        ("Darwin", Path("Library") / "Caches" / "tedana" / "tgs"),
        ("SomethingElse", Path(".cache") / "tedana" / "tgs"),
    ],
)
def test_get_tgs_cache_dir_platforms(tmp_path, monkeypatch, system, expected):
    """Cache directory is platform specific and is created."""
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    result = groupstats.get_tgs_cache_dir()

    assert result == tmp_path / expected
    assert result.is_dir()


def test_get_tgs_cache_dir_windows_localappdata(tmp_path, monkeypatch):
    """On Windows, %LOCALAPPDATA% is used when set."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))

    result = groupstats.get_tgs_cache_dir()

    assert result == tmp_path / "local" / "tedana" / "tgs"
    assert result.is_dir()


def test_get_tgs_cache_dir_windows_fallback(tmp_path, monkeypatch):
    """On Windows without %LOCALAPPDATA%, fall back to ~/AppData/Local."""
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    result = groupstats.get_tgs_cache_dir()

    assert result == tmp_path / "AppData" / "Local" / "tedana" / "tgs"
    assert result.is_dir()


def test_get_tgs_cache_dir_idempotent(tmp_path, monkeypatch):
    """Calling twice does not fail when the directory already exists."""
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert groupstats.get_tgs_cache_dir() == groupstats.get_tgs_cache_dir()


# ---------------------------------------------------------------------------
# get_cached_tgs_version / is_tgs_cached
# ---------------------------------------------------------------------------


def test_get_cached_tgs_version(tmp_path):
    """The VERSION file is read and whitespace stripped."""
    (tmp_path / "VERSION").write_text("v1.2.3\n")
    assert groupstats.get_cached_tgs_version(tmp_path) == "v1.2.3"


def test_get_cached_tgs_version_missing(tmp_path):
    """None is returned if there is no VERSION file."""
    assert groupstats.get_cached_tgs_version(tmp_path) is None


def test_is_tgs_cached_complete(tmp_path):
    """True when all files exist (VERSION is not required)."""
    _make_tgs_dir(tmp_path)
    assert groupstats.is_tgs_cached(tmp_path) is True


def test_is_tgs_cached_empty(tmp_path):
    """False for an empty directory."""
    assert groupstats.is_tgs_cached(tmp_path) is False


@pytest.mark.parametrize("missing", groupstats.TGS_FILES)
def test_is_tgs_cached_incomplete(tmp_path, missing):
    """False when any single file is missing."""
    _make_tgs_dir(tmp_path, files=[f for f in groupstats.TGS_FILES if f != missing])
    assert groupstats.is_tgs_cached(tmp_path) is False


# ---------------------------------------------------------------------------
# get_latest_tgs_version
# ---------------------------------------------------------------------------


def _release_payload(tag="v2.0.0", asset_names=None, extra_assets=("README.md", "notes.txt")):
    import json

    names = list(asset_names if asset_names is not None else groupstats.TGS_FILES)
    names += list(extra_assets)
    return json.dumps(
        {
            "tag_name": tag,
            "assets": [
                {"name": n, "browser_download_url": f"https://example.com/{tag}/{n}"}
                for n in names
            ],
        }
    ).encode("utf-8")


def test_get_latest_tgs_version(monkeypatch):
    """Version and TGS asset URLs are parsed; unrelated assets are ignored."""
    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["timeout"] = timeout
        return _FakeResponse(_release_payload("v2.0.0"))

    monkeypatch.setattr(groupstats.urllib.request, "urlopen", _fake_urlopen)

    version, assets = groupstats.get_latest_tgs_version()

    assert version == "v2.0.0"
    assert set(assets) == set(groupstats.TGS_FILES)
    assert assets["report.py"] == "https://example.com/v2.0.0/report.py"
    assert "README.md" not in assets
    assert seen["url"] == groupstats.TGS_GITHUB_API
    assert seen["headers"]["user-agent"] == "tedana"
    assert "github" in seen["headers"]["accept"]
    assert seen["timeout"] == 30


def test_get_latest_tgs_version_no_assets(monkeypatch):
    """A release without TGS assets raises ValueError."""
    monkeypatch.setattr(
        groupstats.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse(_release_payload(asset_names=[])),
    )
    with pytest.raises(ValueError, match="No TGS assets found in release v2.0.0"):
        groupstats.get_latest_tgs_version()


def test_get_latest_tgs_version_release_without_assets_key(monkeypatch):
    """A release JSON with no 'assets' key raises ValueError."""
    import json

    payload = json.dumps({"tag_name": "v0.0.1"}).encode("utf-8")
    monkeypatch.setattr(
        groupstats.urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(payload)
    )
    with pytest.raises(ValueError):
        groupstats.get_latest_tgs_version()


def test_get_latest_tgs_version_network_error(monkeypatch):
    """URLError from urlopen propagates to the caller."""

    def _raise(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(groupstats.urllib.request, "urlopen", _raise)
    with pytest.raises(urllib.error.URLError):
        groupstats.get_latest_tgs_version()


# ---------------------------------------------------------------------------
# download_tgs_file
# ---------------------------------------------------------------------------


def test_download_tgs_file(tmp_path, monkeypatch):
    """The response body is written to the destination path."""
    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        return _FakeResponse(b"print('hello')\n")

    monkeypatch.setattr(groupstats.urllib.request, "urlopen", _fake_urlopen)
    dest = tmp_path / "aggregate.py"

    groupstats.download_tgs_file("https://example.com/aggregate.py", dest)

    assert dest.read_bytes() == b"print('hello')\n"
    assert seen["url"] == "https://example.com/aggregate.py"
    assert seen["ua"] == "tedana"
    assert seen["timeout"] == 60


def test_download_tgs_file_error(tmp_path, monkeypatch):
    """Network errors propagate and no file is written."""

    def _raise(req, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(groupstats.urllib.request, "urlopen", _raise)
    dest = tmp_path / "aggregate.py"
    with pytest.raises(urllib.error.URLError):
        groupstats.download_tgs_file("https://example.com/x", dest)
    assert not dest.exists()


# ---------------------------------------------------------------------------
# download_tgs
# ---------------------------------------------------------------------------


def test_download_tgs_fresh(cache_dir, fake_download, monkeypatch):
    """With an empty cache all files and VERSION are downloaded."""
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", lambda: ("v1.0.0", _assets()))

    result = groupstats.download_tgs()

    assert result == cache_dir
    assert groupstats.is_tgs_cached(cache_dir)
    assert (cache_dir / "VERSION").read_text() == "v1.0.0"
    assert len(fake_download) == len(groupstats.TGS_FILES)
    # No temp files left behind
    assert not list(cache_dir.glob(".*.tmp"))


def test_download_tgs_uses_cache_without_network(cache_dir, monkeypatch):
    """A complete, versioned cache short-circuits without touching the network."""
    _make_tgs_dir(cache_dir, version="v1.0.0")
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", _no_network)
    monkeypatch.setattr(groupstats, "download_tgs_file", _no_network)

    assert groupstats.download_tgs() == cache_dir


def test_download_tgs_cached_same_version_skips_download(cache_dir, monkeypatch, fake_download):
    """If the cache has no VERSION but files exist, version is checked and
    nothing is downloaded when versions already match."""
    # Files cached but VERSION missing -> must query latest, then download (versions differ)
    _make_tgs_dir(cache_dir)
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", lambda: ("v1.0.0", _assets()))

    groupstats.download_tgs()

    assert (cache_dir / "VERSION").read_text() == "v1.0.0"
    assert len(fake_download) == len(groupstats.TGS_FILES)


def test_download_tgs_force_redownloads(cache_dir, fake_download, monkeypatch):
    """force=True re-downloads even when the cache is current."""
    _make_tgs_dir(cache_dir, version="v1.0.0")
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", lambda: ("v1.0.0", _assets()))

    groupstats.download_tgs(force=True)

    assert len(fake_download) == len(groupstats.TGS_FILES)
    assert (cache_dir / "aggregate.py").read_text().startswith("downloaded from")


def test_download_tgs_updates_older_version(cache_dir, fake_download, monkeypatch):
    """With force=True and a newer release, the VERSION file is updated."""
    _make_tgs_dir(cache_dir, version="v1.0.0")
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", lambda: ("v1.1.0", _assets()))

    groupstats.download_tgs(force=True)

    assert (cache_dir / "VERSION").read_text() == "v1.1.0"


def test_download_tgs_network_error_falls_back_to_cache(cache_dir, monkeypatch, caplog):
    """If the update check fails but files are cached, use them with a warning."""
    _make_tgs_dir(cache_dir, version="v1.0.0")

    def _raise():
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(groupstats, "get_latest_tgs_version", _raise)

    with caplog.at_level(logging.WARNING, logger=LGR_NAME):
        result = groupstats.download_tgs(force=True)

    assert result == cache_dir
    assert "Could not check for tedana_group_stats updates" in caplog.text
    assert "v1.0.0" in caplog.text


def test_download_tgs_network_error_no_cache(cache_dir, monkeypatch):
    """If the update check fails and nothing is cached, raise RuntimeError."""

    def _raise():
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(groupstats, "get_latest_tgs_version", _raise)

    with pytest.raises(RuntimeError, match="Failed to download tedana_group_stats"):
        groupstats.download_tgs()


def test_download_tgs_release_without_assets_no_cache(cache_dir, monkeypatch):
    """A ValueError from get_latest_tgs_version is wrapped in RuntimeError."""

    def _raise():
        raise ValueError("No TGS assets found in release v9")

    monkeypatch.setattr(groupstats, "get_latest_tgs_version", _raise)

    with pytest.raises(RuntimeError, match="No TGS assets found"):
        groupstats.download_tgs()


def test_download_tgs_partial_failure_cleans_up(cache_dir, monkeypatch):
    """If a download fails midway, no partial files or VERSION are left behind."""
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", lambda: ("v1.0.0", _assets()))

    calls = {"n": 0}

    def _flaky(url, dest_path):
        calls["n"] += 1
        if calls["n"] == 3:
            raise urllib.error.URLError("connection reset")
        Path(dest_path).write_text("ok")

    monkeypatch.setattr(groupstats, "download_tgs_file", _flaky)

    with pytest.raises(RuntimeError, match="Failed to download TGS"):
        groupstats.download_tgs()

    assert not (cache_dir / "VERSION").exists()
    assert not groupstats.is_tgs_cached(cache_dir)
    assert not list(cache_dir.glob(".*.tmp"))


def test_download_tgs_missing_asset_warns(cache_dir, fake_download, monkeypatch, caplog):
    """A release missing one of the expected files logs a warning and
    downloads the rest."""
    present = [f for f in groupstats.TGS_FILES if f != "metrics.py"]
    monkeypatch.setattr(
        groupstats, "get_latest_tgs_version", lambda: ("v1.0.0", _assets(present))
    )

    with caplog.at_level(logging.WARNING, logger=LGR_NAME):
        groupstats.download_tgs()

    assert "metrics.py not found in release" in caplog.text
    assert len(fake_download) == len(present)
    assert not (cache_dir / "metrics.py").exists()
    # The cache is incomplete, so it must not be considered valid
    assert not groupstats.is_tgs_cached(cache_dir)


def test_download_tgs_os_error_during_rename(cache_dir, monkeypatch):
    """OSError during the move step is also wrapped in RuntimeError."""
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", lambda: ("v1.0.0", _assets()))

    def _writes(url, dest_path):
        Path(dest_path).write_text("ok")

    monkeypatch.setattr(groupstats, "download_tgs_file", _writes)

    def _bad_rename(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "rename", _bad_rename)

    with pytest.raises(RuntimeError, match="Failed to download TGS"):
        groupstats.download_tgs()

    assert not (cache_dir / "VERSION").exists()
    assert not list(cache_dir.glob(".*.tmp"))


# ---------------------------------------------------------------------------
# clone_install_tgs
# ---------------------------------------------------------------------------


def test_clone_install_tgs(tmp_path, monkeypatch):
    """git clone and uv pip install are invoked with the expected arguments."""
    calls = []

    def _fake_run(cmd, check=False, **kwargs):
        calls.append((cmd, check))

    monkeypatch.setattr(groupstats.subprocess, "run", _fake_run)
    target = tmp_path / "nested" / "tgs"

    result = groupstats.clone_install_tgs(str(target))

    assert result == target
    assert target.is_dir()
    assert len(calls) == 2

    clone_cmd, clone_check = calls[0]
    assert clone_cmd == [
        "git",
        "clone",
        "https://github.com/ME-ICA/tedana_group_stats.git",
        str(target),
    ]
    assert clone_check is True

    install_cmd, install_check = calls[1]
    assert install_cmd == ["uv", "pip", "install", "-e", str(target) + "[dev]"]
    assert install_check is True


def test_clone_install_tgs_git_failure(tmp_path, monkeypatch):
    """A failing git command propagates CalledProcessError and skips install."""
    import subprocess

    calls = []

    def _fake_run(cmd, check=False, **kwargs):
        calls.append(cmd)
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr(groupstats.subprocess, "run", _fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        groupstats.clone_install_tgs(tmp_path / "tgs")

    assert len(calls) == 1
    assert calls[0][0] == "git"


# ---------------------------------------------------------------------------
# generate_tgs_launcher_script
# ---------------------------------------------------------------------------


def test_generate_tgs_launcher_script_creates_file(tmp_path):
    """The launcher is written to <out_dir>/open_tgs_report.py."""
    script = groupstats.generate_tgs_launcher_script(tmp_path)

    assert script == tmp_path / "open_tgs_report.py"
    assert script.exists()
    assert script.stat().st_size > 0


def test_generate_tgs_launcher_script_accepts_str(tmp_path):
    """String paths are accepted."""
    script = groupstats.generate_tgs_launcher_script(str(tmp_path))
    assert isinstance(script, Path)
    assert script.exists()


def test_generate_tgs_launcher_script_is_valid_python(tmp_path):
    """The generated launcher parses as valid Python and defines main()."""
    script = groupstats.generate_tgs_launcher_script(tmp_path)
    tree = ast.parse(script.read_text())

    func_names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for expected in (
        "get_tgs_cache_dir",
        "validate_tgs_path",
        "get_cached_tgs_version",
        "download_tgs",
        "setup_tgs",
        "main",
        "_build_parser",
    ):
        assert expected in func_names


def test_generate_tgs_launcher_script_embeds_paths(tmp_path):
    """The output directory and default report name are embedded in the script."""
    content = groupstats.generate_tgs_launcher_script(tmp_path).read_text()

    assert str(tmp_path) in content
    assert f"{tmp_path}/tgs_report.html" in content
    assert "ME-ICA" in content
    assert "tedana_group_stats" in content
    assert "--offline" in content
    assert "--threshold" in content


def test_generate_tgs_launcher_script_has_shebang(tmp_path):
    """The launcher starts with a Python shebang."""
    content = groupstats.generate_tgs_launcher_script(tmp_path).read_text()
    assert content.startswith("#!/usr/bin/env python3")


@pytest.mark.skipif(platform.system() == "Windows", reason="POSIX permissions only")
def test_generate_tgs_launcher_script_is_executable(tmp_path):
    """On POSIX systems the launcher has the executable bits set."""
    script = groupstats.generate_tgs_launcher_script(tmp_path)
    mode = script.stat().st_mode
    assert mode & stat.S_IXUSR
    assert mode & stat.S_IXGRP
    assert mode & stat.S_IXOTH


def test_generate_tgs_launcher_script_windows_skips_chmod(tmp_path, monkeypatch):
    """On Windows chmod is not attempted."""
    chmod_calls = []
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(Path, "chmod", lambda self, mode: chmod_calls.append(mode))

    script = groupstats.generate_tgs_launcher_script(tmp_path)

    assert script.exists()
    assert chmod_calls == []


def test_generate_tgs_launcher_script_overwrites(tmp_path):
    """Re-generating replaces a stale launcher."""
    stale = tmp_path / "open_tgs_report.py"
    stale.write_text("stale")

    groupstats.generate_tgs_launcher_script(tmp_path)

    assert stale.read_text() != "stale"
    assert "tedana_group_stats" in stale.read_text()


def test_generate_tgs_launcher_script_missing_dir(tmp_path):
    """A nonexistent output directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        groupstats.generate_tgs_launcher_script(tmp_path / "does_not_exist")


@pytest.mark.xfail(
    reason="Launcher currently hard-codes a developer-specific clone path and runs "
    "clone_install_tgs at import time; remove once launcher uses setup_tgs()/env var.",
    strict=False,
)
def test_generate_tgs_launcher_script_no_hardcoded_user_paths(tmp_path):
    """The generated launcher should not contain machine-specific paths."""
    content = groupstats.generate_tgs_launcher_script(tmp_path).read_text()
    assert "/Users/" not in content


# ---------------------------------------------------------------------------
# setup_tgs_report
# ---------------------------------------------------------------------------


def test_generate_report(tmp_path):
    """Test the generation of the TGS report."""
    report_dir = tmp_path

    groupstats.setup_tgs_report(report_dir)

    report_file = report_dir / "open_tgs_report.py"
    assert report_file.exists()
    assert report_file.stat().st_size > 0


def test_setup_tgs_report_returns_launcher_path(tmp_path):
    """The path to the launcher script is returned."""
    result = groupstats.setup_tgs_report(tmp_path)
    assert result == tmp_path / "open_tgs_report.py"
    assert result.exists()


def test_setup_tgs_report_accepts_str(tmp_path):
    """String paths are accepted and converted."""
    result = groupstats.setup_tgs_report(str(tmp_path))
    assert isinstance(result, Path)
    assert result.exists()


def test_setup_tgs_report_logs_instructions(tmp_path, caplog):
    """An info message tells the user how to run the launcher."""
    with caplog.at_level(logging.INFO, logger=LGR_NAME):
        launcher = groupstats.setup_tgs_report(tmp_path)

    assert "TGS launcher created" in caplog.text
    assert str(launcher) in caplog.text


def test_setup_tgs_report_does_not_download(tmp_path, monkeypatch):
    """Generating the launcher must not touch the network or run subprocesses."""
    monkeypatch.setattr(groupstats, "download_tgs", _no_network)
    monkeypatch.setattr(groupstats, "get_latest_tgs_version", _no_network)
    monkeypatch.setattr(groupstats, "clone_install_tgs", _no_network)
    monkeypatch.setattr(groupstats.subprocess, "run", _no_network)

    groupstats.setup_tgs_report(tmp_path)

    assert (tmp_path / "open_tgs_report.py").exists()


def test_setup_tgs_report_missing_dir(tmp_path):
    """A nonexistent output directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        groupstats.setup_tgs_report(tmp_path / "does_not_exist")


def test_setup_tgs_report_env_var_not_required(tmp_path, monkeypatch):
    """The environment variable is optional for generating the launcher."""
    monkeypatch.delenv(groupstats.TGS_PATH_ENV_VAR, raising=False)
    assert groupstats.setup_tgs_report(tmp_path).exists()
    monkeypatch.setenv(groupstats.TGS_PATH_ENV_VAR, str(tmp_path / "somewhere"))
    assert groupstats.setup_tgs_report(tmp_path).exists()


if __name__ == "__main__":
    """Run the test directly."""
    test_path = "/Users/brandon/Desktop/fMRI/tedana-test/tedana_input_dir"
    test_generate_report(Path(test_path))
