"""tedana_group_stats integration module for tedana (modeled after TGS integration).

This module provides functionality to:
1. Download and cache tedana_group_stats from GitHub releases
2. Generate group-level report files in tedana output directories

tedana_group_stats aggregates tedana multi-echo fMRI outputs across a BIDS-derivatives tree into a single,
self-contained interactive QC report — so you can spot group trends and outlier runs without opening every report by hand.
For more information, see: https://github.com/ME-ICA/tedana_group_stats
"""

import json
import logging
import os
import platform
import stat
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple, Union
import subprocess
import site

LGR = logging.getLogger("GENERAL")

# tedana_group_stats GitHub repository information
TGS_REPO_OWNER = "ME-ICA"
TGS_REPO_NAME = "tedana_group_stats"
TGS_GITHUB_API = (
    f"https://api.github.com/repos/{TGS_REPO_OWNER}/{TGS_REPO_NAME}/releases/latest"
)

# Files to download from TGS releases
TGS_FILES = ["aggregate.py", "discovery.py", "metrics.py", "report.py"]

# Environment variable for local TGS path
TGS_PATH_ENV_VAR = "TEDANA_TGS_PATH"

# Bundled TGS path (relative to tedana package)
TGS_BUNDLED_PATH = Path(__file__).parent / "resources" / "tedana_group_stats"


def validate_tgs_path(tgs_path: Union[str, Path]) -> bool:
    """Check if a local path contains the required TGS files.

    Parameters
    ----------
    tgs_path : str or Path
        Path to a local TGS directory to validate.

    Returns
    -------
    bool
        True if the path exists and contains all required TGS files,
        False otherwise.
    """
    tgs_path = Path(tgs_path)

    if not tgs_path.exists():
        LGR.debug(f"TGS path does not exist: {tgs_path}")
        return False

    if not tgs_path.is_dir():
        LGR.debug(f"TGS path is not a directory: {tgs_path}")
        return False

    missing_files = []
    for filename in TGS_FILES:
        if not (tgs_path / filename).exists():
            missing_files.append(filename)

    if missing_files:
        LGR.debug(f"TGS path {tgs_path} is missing files: {missing_files}")
        return False

    LGR.debug(f"TGS path {tgs_path} is valid.")
    return True


def validate_tgs_path(tgs_path: Union[str, Path]) -> bool:
    """Check if a local TGS path contains the required TGS files.

    Parameters
    ----------
    tgs_path : str or Path
        Path to a local TGS directory to validate.

    Returns
    -------
    bool
        True if the path exists and contains all required TGS files,
        False otherwise.
    """
    tgs_path = Path(tgs_path)

    if not tgs_path.exists():
        LGR.debug(f"TGS path does not exist: {tgs_path}")
        return False

    if not tgs_path.is_dir():
        LGR.debug(f"TGS path is not a directory: {tgs_path}")
        return False

    missing_files = []
    for filename in TGS_FILES:
        if not (tgs_path / filename).exists():
            missing_files.append(filename)

    if missing_files:
        LGR.debug(f"TGS path {tgs_path} is missing files: {missing_files}")
        return False

    return True


def clone_install_tgs(tgs_path: Union[str, Path]) -> Path:
    """Clone and install TGS files to a local directory.

    Parameters
    ----------
    tgs_path : str or Path
        Path to the local directory where TGS files will be installed.

    Returns
    -------
    Path
        Path to the installed TGS directory.
    """
    tgs_path = Path(tgs_path)
    tgs_path.mkdir(parents=True, exist_ok=True)

    # Clone the TGS repository
    repo_url = f"https://github.com/{TGS_REPO_OWNER}/{TGS_REPO_NAME}.git"
    subprocess.run(["git", "clone", repo_url, str(tgs_path)], check=True)
    subprocess.run(["uv", "pip", "install", "-e", str(tgs_path) + "[dev]"], check=True)

    return tgs_path


def get_tgs_from_local(tgs_path: Union[str, Path]) -> Path:
    """Validate and return the path to a local TGS directory.

    Parameters
    ----------
    tgs_path : str or Path
        Path to a local TGS directory.

    Returns
    -------
    Path
        Validated path to the local TGS directory.

    Raises
    ------
    ValueError
        If the path does not exist, is not a directory, or is missing
        required TGS files.
    """
    tgs_path = Path(tgs_path)

    if not tgs_path.exists():
        raise ValueError(f"Local TGS path does not exist: {tgs_path}")

    if not tgs_path.is_dir():
        raise ValueError(f"Local TGS path is not a directory: {tgs_path}")

    missing_files = []
    for filename in TGS_FILES:
        if not (tgs_path / filename).exists():
            missing_files.append(filename)

    if missing_files:
        raise ValueError(
            f"Local TGS path {tgs_path} is missing required files: {missing_files}. "
            f"Required files are: {TGS_FILES}"
        )

    LGR.debug(f"Validated local TGS path: {tgs_path}")
    return tgs_path


def get_tgs_cache_dir() -> Path:
    """Get the platform-specific cache directory for TGS files.

    Returns
    -------
    Path
        Path to TGS cache directory.
        - Linux: ~/.cache/tedana/tgs
        - macOS: ~/Library/Caches/tedana/tgs
        - Windows: %LOCALAPPDATA%/tedana/tgs
    """
    system = platform.system()

    if system == "Linux":
        base_cache = Path.home() / ".cache"
    elif system == "Darwin":  # macOS
        base_cache = Path.home() / "Library" / "Caches"
    elif system == "Windows":
        base_cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        # Fallback for other systems
        base_cache = Path.home() / ".cache"

    tgs_cache = base_cache / "tedana" / "tgs"
    tgs_cache.mkdir(parents=True, exist_ok=True)

    return tgs_cache


def get_latest_tgs_version() -> Tuple[str, Dict]:
    """Fetch the latest TGS version information from GitHub.

    Returns
    -------
    version : str
        The version tag (e.g., "v2.0.0").
    assets : dict
        Dictionary mapping filename to download URL.

    Raises
    ------
    urllib.error.URLError
        If unable to connect to GitHub API.
    ValueError
        If the release has no assets.
    """
    req = urllib.request.Request(
        TGS_GITHUB_API,
        headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "tedana"},
    )

    with urllib.request.urlopen(req, timeout=30) as response:
        release_info = json.loads(response.read().decode("utf-8"))

    version = release_info["tag_name"]
    assets = {}

    for asset in release_info.get("assets", []):
        name = asset["name"]
        if name in TGS_FILES:
            assets[name] = asset["browser_download_url"]

    if not assets:
        raise ValueError(f"No TGS assets found in release {version}")

    return version, assets


def download_tgs_file(url: str, dest_path: Path) -> None:
    """Download a single file from a URL.

    Parameters
    ----------
    url : str
        URL to download from.
    dest_path : Path
        Destination file path.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "tedana"})

    with urllib.request.urlopen(req, timeout=60) as response:
        dest_path.write_bytes(response.read())


def get_cached_tgs_version(cache_dir: Path) -> Optional[str]:
    """Get the version of TGS currently cached.

    Parameters
    ----------
    cache_dir : Path
        Path to TGS cache directory.

    Returns
    -------
    str or None
        Version string if version file exists, None otherwise.
    """
    version_file = cache_dir / "VERSION"
    if version_file.exists():
        return version_file.read_text().strip()
    return None


def is_tgs_cached(cache_dir: Path) -> bool:
    """Check if TGS files are cached and complete.

    Parameters
    ----------
    cache_dir : Path
        Path to TGS cache directory.

    Returns
    -------
    bool
        True if all required files exist in cache.
    """
    for filename in TGS_FILES:
        if not (cache_dir / filename).exists():
            return False
    return True


def download_tgs(force: bool = False) -> Path:
    """Download TGS files from GitHub releases.

    Downloads the latest TGS release to the platform-specific cache directory.
    If TGS is already cached and up-to-date, skips the download unless force=True.

    Parameters
    ----------
    force : bool, optional
        Force re-download even if TGS is cached. Default: False.

    Returns
    -------
    Path
        Path to TGS cache directory containing the downloaded files.

    Raises
    ------
    RuntimeError
        If download fails after retries.
    """
    cache_dir = get_tgs_cache_dir()
    cached_version = get_cached_tgs_version(cache_dir)

    # Check if we need to download
    if not force and is_tgs_cached(cache_dir) and cached_version:
        LGR.debug(f"TGS {cached_version} already cached at {cache_dir}")
        return cache_dir

    LGR.info("Downloading tedana_group_stats from GitHub...")

    try:
        latest_version, assets = get_latest_tgs_version()
    except (urllib.error.URLError, ValueError) as e:
        if is_tgs_cached(cache_dir):
            LGR.warning(
                f"Could not check for tedana_group_stats updates: {e}. Using cached version {cached_version}."
            )
            return cache_dir
        raise RuntimeError(f"Failed to download tedana_group_stats: {e}") from e

    # Skip if already have latest version
    if not force and cached_version == latest_version and is_tgs_cached(cache_dir):
        LGR.debug(f"tedana_group_stats {latest_version} is already cached")
        return cache_dir

    LGR.info(f"Downloading TGS {latest_version}...")

    # Download each file to temporary paths first to avoid partial downloads
    # corrupting the cache if something fails partway through
    downloaded_files = []
    try:
        for filename in TGS_FILES:
            if filename not in assets:
                LGR.warning(f"tedana_group_stats asset {filename} not found in release")
                continue

            temp_path = cache_dir / f".{filename}.tmp"
            download_tgs_file(assets[filename], temp_path)
            downloaded_files.append((temp_path, cache_dir / filename))
            LGR.debug(f"Downloaded {filename}")

        # All downloads succeeded - move temp files to final locations
        for temp_path, final_path in downloaded_files:
            temp_path.rename(final_path)

        # Write version file only after all files are in place
        (cache_dir / "VERSION").write_text(latest_version)

    except (urllib.error.URLError, OSError) as e:
        # Clean up any temp files on failure (catches download errors, rename errors, etc.)
        for temp_path, _ in downloaded_files:
            if temp_path.exists():
                temp_path.unlink()
        raise RuntimeError(f"Failed to download TGS: {e}") from e

    LGR.info(f"tedana_group_stats {latest_version} downloaded to {cache_dir}")
    return cache_dir


def generate_tgs_launcher_script(out_dir: Union[str, Path]) -> Path:
    """Generate the TGS launcher script for a tedana output directory.

    This creates a Python script that, when executed:
    1. Checks for TGS files (env var, bundled, cached, or downloads)
    2. Copies TGS files to output directory if needed
    3. Starts a local server and opens TGS in the browser

    Parameters
    ----------
    out_dir : str or Path
        The directory containing all tedana output directories.
    port : int, optional
        Default port for the local server. Default: 8000.

    Returns
    -------
    Path
        Path to the generated launcher script.
    """
    out_dir = Path(out_dir)
    script_path = out_dir / "open_tgs_report.py"

    # Cross-platform launcher script with embedded download logic
    script_content = (
    '''#!/usr/bin/env python3


from __future__ import annotations

import argparse
import sys
from pathlib import Path
import platform
import os
import urllib.request
import urllib.error
import json
import shutil
import subprocess
import importlib
import site

# tedana_group_stats GitHub repository information
TGS_REPO_OWNER = "ME-ICA"
TGS_REPO_NAME = "tedana_group_stats"
TGS_GITHUB_API = (
    f"https://api.github.com/repos/{TGS_REPO_OWNER}/{TGS_REPO_NAME}/releases/latest"
)

# Files to download from TGS releases
TGS_FILES = ["aggregate.py", "discovery.py", "metrics.py", "report.py"]

# Environment variable for local TGS path
TGS_PATH_ENV_VAR = "TEDANA_TGS_PATH"

# Bundled TGS path (relative to tedana package)
TGS_BUNDLED_PATH = Path(__file__).parent / "resources" / "tedana_group_stats"


def get_tgs_cache_dir():
    """Get platform-specific cache directory for TGS files."""
    system = platform.system()
    if system == "Linux":
        base_cache = Path.home() / ".cache"
    elif system == "Darwin":
        base_cache = Path.home() / "Library" / "Caches"
    elif system == "Windows":
        base_cache = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base_cache = Path.home() / ".cache"
    tgs_cache = base_cache / "tedana" / "tgs"
    tgs_cache.mkdir(parents=True, exist_ok=True)
    return tgs_cache


def validate_tgs_path(tgs_path):
    """Check if path contains required TGS files."""
    tgs_path = Path(tgs_path)
    if not tgs_path.exists() or not tgs_path.is_dir():
        return False
    return all((tgs_path / f).exists() for f in TGS_FILES)


def get_cached_tgs_version(cache_dir):
    """Get cached TGS version if available."""
    version_file = cache_dir / "VERSION"
    return version_file.read_text().strip() if version_file.exists() else None


def download_tgs(force=False):
    """Download TGS from GitHub releases.

    Always checks GitHub for the latest version and downloads if:
    - force=True
    - TGS is not cached
    - Cached version is older than the latest release
    """
    cache_dir = get_tgs_cache_dir()
    cached_version = get_cached_tgs_version(cache_dir)

    # Always check GitHub for the latest version (unless we have no network)
    print("[TGS] Checking for latest version...")

    try:
        req = urllib.request.Request(
            TGS_GITHUB_API,
            headers={
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "tedana-tgs-launcher",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            release_info = json.loads(response.read().decode("utf-8"))

        latest_version = release_info["tag_name"]
        assets = {}
        for asset in release_info.get("assets", []):
            if asset["name"] in TGS_FILES:
                assets[asset["name"]] = asset["browser_download_url"]

        if not assets:
            raise ValueError(f"No TGS assets found in release {latest_version}")

        # Check if we need to download
        if not force and cached_version == latest_version and validate_tgs_path(cache_dir):
            print(f"[TGS] Using cached version {cached_version} (up to date)")
            return cache_dir

        # Download needed: either forced, new version available, or not cached
        if cached_version and cached_version != latest_version:
            print(f"[TGS] Updating from {cached_version} to {latest_version}...")
        else:
            print(f"[TGS] Downloading version {latest_version}...")

        for filename, url in assets.items():
            dest_path = cache_dir / filename
            req = urllib.request.Request(url, headers={"User-Agent": "tedana-tgs-launcher"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                dest_path.write_bytes(resp.read())
            print(f"[TGS] Downloaded {filename}")

        (cache_dir / "VERSION").write_text(latest_version)
        print(f"[TGS] Successfully installed TGS {latest_version}")
        return cache_dir

    except (urllib.error.URLError, ValueError) as e:
        # Network error - fall back to cached version if available
        if validate_tgs_path(cache_dir) and cached_version:
            print(f"[TGS] Warning: Could not check for updates ({e})")
            print(f"[TGS] Using cached version {cached_version}")
            return cache_dir
        raise RuntimeError(f"Failed to download TGS: {e}") from e


def setup_tgs(output_dir, force_download=False):
    """Set up TGS files in the output directory.

    Always checks for the latest TGS version and downloads updates if available,
    unless TEDANA_TGS_PATH environment variable is set (user's explicit choice).
    """
    output_dir = Path(output_dir)
    tgs_dir = output_dir / "tgs"
    output_version_file = tgs_dir / "VERSION"

    source_dir = None

    # Priority 1: Environment variable (user's explicit choice - no auto-update)
    env_path = os.environ.get(TGS_PATH_ENV_VAR)
    if env_path and validate_tgs_path(env_path):
        source_dir = Path(env_path)
        print(f"[TGS] Using path from {TGS_PATH_ENV_VAR}: {source_dir}")

    # Priority 2: Download/update from GitHub (always checks for latest version)
    if source_dir is None:
        source_dir = download_tgs(force=force_download)

    # Check if we need to update the output directory
    source_version = get_cached_tgs_version(source_dir)
    output_version = None
    if output_version_file.exists():
        output_version = output_version_file.read_text().strip()

    is_up_to_date = (
        output_version and output_version == source_version and validate_tgs_path(tgs_dir)
    )
    if not force_download and is_up_to_date:
        print(f"[TGS] Using {output_version} from {tgs_dir}")
        return tgs_dir

    # Copy files to output (new install or update)
    tgs_dir.mkdir(exist_ok=True)
    for filename in TGS_FILES + ["VERSION"]:
        src = source_dir / filename
        if src.exists():
            shutil.copy2(src, tgs_dir / filename)

    if output_version and source_version and output_version != source_version:
        print(f"[TGS] Updated from {output_version} to {source_version} in {tgs_dir}")
    else:
        print(f"[TGS] Installed {source_version} to {tgs_dir}")
    return tgs_dir
    
def clone_install_tgs(tgs_path: Union[str, Path]) -> Path:
    """Clone and install TGS files to a local directory.

    Parameters
    ----------
    tgs_path : str or Path
        Path to the local directory where TGS files will be installed.

    Returns
    -------
    Path
        Path to the installed TGS directory.
    """
    tgs_path = Path(tgs_path)
    tgs_path.mkdir(parents=True, exist_ok=True)
    
    if not tgs_path.exists() or not tgs_path.is_dir():
        # Clone the TGS repository
        repo_url = f"https://github.com/{TGS_REPO_OWNER}/{TGS_REPO_NAME}.git"
        subprocess.run(["git", "clone", repo_url, str(tgs_path)], check=True)
        subprocess.run(["uv", "pip", "install", "-e", str(tgs_path) + "[dev]"], check=True)

    return tgs_path

clone_install_tgs('/Users/brandon/Desktop/fMRI/tedana-test/tedana_dev/tedana/tedana/resources/tedana_group_stats')

importlib.reload(site)

from tedana_group_stats import __version__
from tedana_group_stats.aggregate import DEFAULT_THRESHOLD, build_group_table
from tedana_group_stats.discovery import discover_runs
from tedana_group_stats.metrics import summarize_run
from tedana_group_stats.report import write_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tedana-group-stats",
        description=(
            "Aggregate tedana outputs across a BIDS-derivatives tree into a group QC report."
        ),
    )
    parser.add_argument("root", type=Path, default='''
        + str('"' + str(out_dir) + '"')
        + """, help="BIDS-derivatives directory to scan recursively")
    parser.add_argument(
        "-o",
        "--output",
        type=Path, default="""
        + str('"' + str(out_dir) + '/' + "tgs_report.html" + '"')
        + ''', help="output HTML path (default: %(default)s)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="inline plotly.js for a fully standalone file",
    )
    parser.add_argument(
        "--threshold",
        type=float, default=DEFAULT_THRESHOLD, help="modified z-score cutoff for outlier flagging (default: %(default)s)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root: Path = args.root
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    runs = discover_runs(root)
    if not runs:
        print(f"error: no tedana outputs found under {root}", file=sys.stderr)
        return 1
    summaries = [summarize_run(run) for run in runs]
    table = build_group_table(summaries, threshold=args.threshold)
    out = write_report(table, args.output, offline=args.offline)
    n_failed = sum(summary["status"] != "ok" for summary in summaries)
    print(f"Wrote {out} — {len(runs)} runs ({n_failed} failed to load).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    )

    script_path.write_text(script_content)

    # Make script executable on Unix systems
    if platform.system() != "Windows":
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return script_path


def setup_tgs_report(out_dir: Union[str, Path]) -> Path:
    """Generate the TGS launcher script in a tedana output directory.

    This function generates a launcher script (open_tgs_report.py) that handles
    all TGS setup when executed by the user, including:
    - Checking for TGS files (environment variable, cache)
    - Downloading TGS from GitHub if not available
    - Starting a local server to visualize results

    Parameters
    ----------
    out_dir : str or Path
        The tedana output directory.

    Returns
    -------
    Path
        Path to the generated launcher script.
    """
    out_dir = Path(out_dir)

    # Generate launcher script (all TGS setup happens when user runs the script)
    launcher_path = generate_tgs_launcher_script(out_dir)

    LGR.info(f"TGS launcher created. Run 'python {launcher_path}' to visualize results.")

    return launcher_path