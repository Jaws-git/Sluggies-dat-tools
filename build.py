import platform
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
PACKAGE = DIST / "sluggies-dat-tools"
SPEC = ROOT / "sluggies-dat-tools.spec"

RELEASE_DIRECTORIES = {
    ROOT / "SluggiesTools": PACKAGE / "SluggiesTools",
    ROOT / "1_Input": PACKAGE / "1_Input",
    ROOT / "2_Output_Models": PACKAGE / "2_Output_Models",
    ROOT / "_docs": PACKAGE / "docs",
}
RELEASE_FILES = (
    "StartTools.bat",
    "README.md",
    "BlenderGuide.md",
    "SluggiesIO_BlenderAddon_v0.7.4.zip",
)


def clean() -> None:
    for directory in (BUILD, DIST):
        if directory.exists():
            print(f"Cleaning {directory}")
            shutil.rmtree(directory)


def sync_dependencies() -> None:
    subprocess.run(["uv", "sync", "--locked"], cwd=ROOT, check=True)


def build_executable() -> None:
    subprocess.run(
        ["uv", "run", "pyinstaller", "--clean", "--noconfirm", str(SPEC)],
        cwd=ROOT,
        check=True,
    )


def ignored_release_path(directory: str, names: list[str]) -> set[str]:
    ignored = {name for name in names if name == "__pycache__" or name.endswith(".pyc")}
    if Path(directory).name in {"SluggiesTools", "Icons"}:
        ignored.update(name for name in names if name == "tests")
    return ignored


def copy_release_files() -> None:
    for source, destination in RELEASE_DIRECTORIES.items():
        if not source.exists():
            raise FileNotFoundError(f"Required release directory not found: {source}")
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignored_release_path)

    for name in RELEASE_FILES:
        source = ROOT / name
        if not source.exists():
            raise FileNotFoundError(f"Required release file not found: {source}")
        shutil.copy2(source, PACKAGE / name)

    (PACKAGE / "3_Output_Dat").mkdir(exist_ok=True)


def verify() -> Path:
    executable = PACKAGE / ("sluggies-dat-tools.exe" if platform.system() == "Windows" else "sluggies-dat-tools")
    required = (
        executable,
        PACKAGE / "StartTools.bat",
        PACKAGE / "SluggiesTools" / "export.py",
        PACKAGE / "1_Input" / "_Icons",
        PACKAGE / "SluggiesIO_BlenderAddon_v0.7.4.zip",
        PACKAGE / "docs" / "_docs_model_format" / "index.html",
    )
    missing = [path for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Release verification failed; missing: {missing}")
    return executable


def main() -> None:
    print(f"Platform: {platform.system()}")
    print(f"Python:   {sys.version}")
    clean()
    sync_dependencies()
    build_executable()
    copy_release_files()
    executable = verify()
    size_mb = executable.stat().st_size / (1024 * 1024)
    print(f"Build complete: {executable} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
