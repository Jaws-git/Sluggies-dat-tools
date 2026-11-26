import hashlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


TOOLING_ROOT = Path(__file__).resolve().parent
ROOT = TOOLING_ROOT.parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
PACKAGE = DIST / "sluggies-dat-tools"
SPEC = TOOLING_ROOT / "sluggies-dat-tools.spec"

RELEASE_DIRECTORIES = {
    ROOT / "SluggiesTools": PACKAGE / "SluggiesTools",
    ROOT / "1_Input": PACKAGE / "1_Input",
    ROOT / "_docs": PACKAGE / "docs",
}
RELEASE_FILES = (
    ("StartTools.bat", "StartTools.bat"),
    ("README.md", "README.md"),
    ("_docs/BlenderGuide.md", "BlenderGuide.md"),
    ("SluggiesTools/_build/THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
    ("3_Output_Dat/CopyFilesToGameDir.bat", "3_Output_Dat/CopyFilesToGameDir.bat"),
)

BLENDER_ADDON_PATTERN = re.compile(r"SluggiesIO_BlenderAddon_v(\d+)\.(\d+)\.(\d+)\.zip")


def find_blender_addon_zip() -> Path:
    """Pick the highest-versioned addon zip in ROOT, since the version changes per release."""
    candidates = []
    for path in ROOT.glob("SluggiesIO_BlenderAddon_v*.zip"):
        match = BLENDER_ADDON_PATTERN.fullmatch(path.name)
        if match:
            candidates.append((tuple(int(part) for part in match.groups()), path))
    if not candidates:
        raise FileNotFoundError(f"No SluggiesIO_BlenderAddon_v*.zip found in {ROOT}")
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]

WIIMMS_VERSION = "v2.42a-r8989"
WIIMMS_ARCHIVE = f"szs-{WIIMMS_VERSION}-cygwin64.zip"
WIIMMS_URL = f"https://szs.wiimm.de/download/{WIIMMS_ARCHIVE}"
# The official archive is kept in the repository: szs.wiimm.de often refuses or drops HTTPS connections,
# which failed CI builds. The download is only the fallback for a checkout without the file.
WIIMMS_VENDORED = TOOLING_ROOT / "vendor" / WIIMMS_ARCHIVE
WIIMMS_SHA256 = "ac54b82806d5867d2d9f003df972164138ae4f7a7ab8f29d8397664f31c9e892"
# Inside PyInstaller's contents directory; start.py adds its bin/ to PATH.
WIIMMS_DIR = PACKAGE / "_internal" / "tools" / "wiimms-szs-tools"


def clean() -> None:
    for directory in (BUILD, DIST):
        if directory.exists():
            print(f"Cleaning {directory}")
            shutil.rmtree(directory)


def sync_dependencies() -> None:
    subprocess.run(
        ["uv", "sync", "--locked", "--project", str(TOOLING_ROOT)],
        cwd=ROOT,
        check=True,
    )


def build_executable() -> None:
    subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(TOOLING_ROOT),
            "pyinstaller",
            "--clean",
            "--noconfirm",
            "--workpath",
            str(BUILD),
            "--distpath",
            str(DIST),
            str(SPEC),
        ],
        cwd=ROOT,
        check=True,
    )


# Never part of a release, wherever they lie: caches, a virtual environment (uv sync has just made
# SluggiesTools/_build/.venv, with numpy, pillow, Dear PyGui and PyInstaller in it) and the hand-run probes.
NOT_RELEASED_ANYWHERE = {"__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}
# Not released from SluggiesTools/: the build tooling (its notices file is copied by name) and local debug work.
NOT_RELEASED_TOOLS = {"tests", "_build", "Debug"}


def ignored_release_path(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names
               if name in NOT_RELEASED_ANYWHERE or name.endswith(".pyc")
               or (name.startswith("probe_") and name.endswith(".py"))}
    if Path(directory).name == "SluggiesTools":
        ignored.update(name for name in names if name in NOT_RELEASED_TOOLS)
    if Path(directory).name == "Icons":
        ignored.update(name for name in names if name == "tests")
    if Path(directory).name == "1_Input":            # the user's game files never ship
        ignored.update(name for name in names if name == "sound_NA" or name.lower().endswith((".dat", ".dol", ".bin")))
    return ignored


def copy_release_files() -> None:
    for source, destination in RELEASE_DIRECTORIES.items():
        if not source.exists():
            raise FileNotFoundError(f"Required release directory not found: {source}")
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignored_release_path)

    (PACKAGE / "3_Output_Dat").mkdir(exist_ok=True)

    for source_name, destination_name in RELEASE_FILES:
        source = ROOT / source_name
        if not source.exists():
            raise FileNotFoundError(f"Required release file not found: {source}")
        destination = PACKAGE / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    addon_zip = find_blender_addon_zip()
    shutil.copy2(addon_zip, PACKAGE / addon_zip.name)

DOWNLOAD_ATTEMPTS = 5
DOWNLOAD_TIMEOUT = 60       # seconds, per connection and per read


def download(url: str, destination: Path) -> None:
    """Fetch ``url`` into ``destination``, trying again when the connection fails: the server now and then
    resets a connection from a CI runner (WinError 10054 in the TLS handshake). The caller checks the
    checksum, so a retry cannot let a different file in."""
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT) as response, destination.open("wb") as output:
                shutil.copyfileobj(response, output)
            return
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == DOWNLOAD_ATTEMPTS:      # a missing file will not appear on a retry
                raise
            reason = error
        except OSError as error:                                      # URLError, reset, timeout
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
            reason = error
        wait = 5 * 2 ** (attempt - 1)
        print(f"Download failed ({reason}); attempt {attempt + 1} of {DOWNLOAD_ATTEMPTS} in {wait} s")
        time.sleep(wait)


def bundle_wiimms_tools() -> None:
    """Bundle the official, checksum-pinned Windows distribution unchanged."""
    if platform.system() != "Windows":
        print("Skipping Wiimms SZS Tools bundle on non-Windows platform")
        return

    destination = WIIMMS_DIR
    with tempfile.TemporaryDirectory(prefix="sluggies-wiimms-") as temporary:
        if WIIMMS_VENDORED.exists():
            archive = WIIMMS_VENDORED
            print(f"Using vendored Wiimms SZS Tools {WIIMMS_VERSION}")
        else:
            archive = Path(temporary) / WIIMMS_ARCHIVE
            print(f"Downloading Wiimms SZS Tools {WIIMMS_VERSION}")
            download(WIIMMS_URL, archive)

        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != WIIMMS_SHA256:
            raise RuntimeError(
                f"Wiimms SZS Tools checksum mismatch: expected {WIIMMS_SHA256}, got {digest}"
            )

        with zipfile.ZipFile(archive) as package:
            members = package.infolist()
            top_level = Path(members[0].filename).parts[0]
            for member in members:
                parts = Path(member.filename).parts
                if not parts or parts[0] != top_level or ".." in parts:
                    raise RuntimeError(f"Unsafe path in Wiimms SZS Tools archive: {member.filename}")
                relative = Path(*parts[1:])
                if not relative.parts:
                    continue
                target = destination / relative
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(member) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)

    wimgt = destination / "bin" / "wimgt.exe"
    if not wimgt.exists():
        raise FileNotFoundError(f"Bundled wimgt executable not found: {wimgt}")


def verify() -> Path:
    executable = PACKAGE / ("sluggies-dat-tools.exe" if platform.system() == "Windows" else "sluggies-dat-tools")
    addon_zip = find_blender_addon_zip()
    required = (
        executable,
        PACKAGE / "StartTools.bat",
        PACKAGE / "3_Output_Dat" / "CopyFilesToGameDir.bat",
        PACKAGE / "SluggiesTools" / "export.py",
        PACKAGE / "1_Input" / "_Icons",
        PACKAGE / "1_Input" / "_RosterConfigurations",
        PACKAGE / "SluggiesTools" / "Roster" / "fonts" / "OpenSans.ttf",
        PACKAGE / "SluggiesTools" / "Roster" / "fonts" / "OpenSans-OFL.txt",
        PACKAGE / addon_zip.name,
        PACKAGE / "docs" / "_docs_model_format" / "index.html",
    )
    missing = [path for path in required if not path.exists()]
    if platform.system() == "Windows":
        windows_required = (
            WIIMMS_DIR / "bin" / "wimgt.exe",
            WIIMMS_DIR / "bin" / "cygwin1.dll",
            WIIMMS_DIR / "gpl-2.0.txt",
        )
        missing.extend(path for path in windows_required if not path.exists())
    if missing:
        raise FileNotFoundError(f"Release verification failed; missing: {missing}")
    tools = PACKAGE / "SluggiesTools"
    unwanted = [tools / name for name in sorted(NOT_RELEASED_TOOLS) if (tools / name).exists()]
    unwanted += [path for path in tools.rglob("*")
                 if path.name in NOT_RELEASED_ANYWHERE or (path.name.startswith("probe_") and path.suffix == ".py")]
    if unwanted:
        raise RuntimeError(f"Release verification failed; these must not ship: {unwanted[:10]}")
    return executable


def main() -> None:
    print(f"Platform: {platform.system()}")
    print(f"Python:   {sys.version}")
    clean()
    sync_dependencies()
    build_executable()
    copy_release_files()
    bundle_wiimms_tools()
    executable = verify()
    size_mb = executable.stat().st_size / (1024 * 1024)
    print(f"Build complete: {executable} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
