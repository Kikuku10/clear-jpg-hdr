"""Build a per-user Windows x64 installer from checksum-pinned official runtimes."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
VERSION = "1.3.0"
PYTHON_URL = "https://www.python.org/ftp/python/3.13.17/python-3.13.17-embed-amd64.zip"
PYTHON_SHA = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
EXIF_URL = "https://downloads.sourceforge.net/project/exiftool/exiftool-13.55_64.zip"
EXIF_SHA = "9fadaf5221dcb07a5d018e21c8529e257917d2fad1fdfc8e64855c4fb73293df"


def cached_zip(cache, url, expected):
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / url.rsplit("/", 1)[1]
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == expected:
        return target
    with tempfile.TemporaryDirectory(dir=cache) as work:
        download = Path(work) / target.name
        subprocess.run(["curl", "-fLSs", "--retry", "3", "-o", str(download), url], check=True)
        if hashlib.sha256(download.read_bytes()).hexdigest() != expected:
            raise RuntimeError("SHA-256 mismatch for " + target.name)
        download.replace(target)
    return target


def default_cache():
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "ClearJPGHDR-build/cache"
    if sys.platform == "darwin":
        return Path.home() / "Library/Caches/ClearJPGHDR/windows"
    return Path.home() / ".cache/clear-jpg-hdr/windows"


def prepare(payload, native, cache):
    native_files = ["ClearJPGHDR.exe", "ExplorerMenu-arm64.dll", "ExplorerMenu-x64.dll",
                    "Assets/StoreLogo.png", "Assets/Logo44.png", "Assets/Logo150.png"]
    for arch in ("arm64", "x64"):
        for name in ("AppxManifest.xml", "identity.msix"):
            native_files.append(f"modern/{arch}/{name}")
        signed = f"modern/{arch}/identity.signed.msix"
        if (native / signed).is_file():
            native_files.append(signed)
    for name in native_files:
        if not (native / name).is_file():
            raise FileNotFoundError("Missing Windows component: " + str(native / name))
    payload.mkdir()
    with ZipFile(cached_zip(cache, PYTHON_URL, PYTHON_SHA)) as archive:
        archive.extractall(payload / "runtime")
    # Keep the embedded interpreter isolated while allowing imports from our app root.
    (payload / "runtime/python313._pth").write_text("python313.zip\n.\n..\n", encoding="ascii")
    with tempfile.TemporaryDirectory(dir=payload.parent) as work:
        with ZipFile(cached_zip(cache, EXIF_URL, EXIF_SHA)) as archive:
            archive.extractall(work)
        package = Path(work) / "exiftool-13.55_64"
        (package / "exiftool(-k).exe").rename(package / "exiftool.exe")
        package.rename(payload / "exiftool")
    shutil.copyfile(ROOT / "clear_hdr.py", payload / "clear_hdr.py")
    shutil.copyfile(HERE / "AppIcon.ico", payload / "AppIcon.ico")
    for name in native_files:
        (payload / name).parent.mkdir(exist_ok=True, parents=True)
        shutil.copyfile(native / name, payload / name)


def quote_path(path):
    path = str(path)
    if any(c in path for c in '"\r\n'):
        raise ValueError("Invalid NSIS path: " + path)
    return path.replace("$", "$$")


def build_setup(output, payload, nsis, version):
    """Create an explicit uninstall manifest, preserving files added by the user."""
    files = sorted((p for p in payload.rglob("*") if p.is_file()),
                   key=lambda p: (p.name == "ClearJPGHDR.exe", str(p)))
    dirs = sorted((p for p in payload.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts))
    manifest = output / "uninstall-files.nsh"
    with manifest.open("w", encoding="utf-8-sig") as stream:
        for command, group in (("Delete", files), ("RMDir", dirs)):
            for p in group:
                rel = quote_path(p.relative_to(payload).as_posix().replace("/", "\\"))
                path = f'"$INSTDIR\\{rel}"'
                if command == "Delete":
                    # Keep the uninstaller and host usable if Explorer or an open app holds a file.
                    stream.write(f'IfFileExists {path} 0 +4\nClearErrors\nDelete {path}\nIfErrors filesLocked\n')
                else:
                    stream.write(f'RMDir {path}\n')
    target = output / f"ClearJPGHDR-{version}-windows-x64-setup.exe"
    env = os.environ.copy()
    # Respect NSISDIR; a locally extracted Unix NSIS distribution is relocatable.
    share = Path(nsis).resolve().parents[1] / "share/nsis"
    if (share / "Stubs").is_dir():
        env.setdefault("NSISDIR", str(share))
    flag = "/" if os.name == "nt" else "-"
    defines = {"OUTPUT": target, "PAYLOAD": payload, "REMOVEFILES": manifest,
               "ICONFILE": HERE / "AppIcon.ico", "VERSION": version}
    subprocess.run([nsis, flag + "V2", *(flag + f"D{k}={quote_path(v)}" for k, v in defines.items()),
                    str(HERE / "installer.nsi")], env=env, check=True)
    print(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh output directory")
    parser.add_argument("--cache", type=Path, default=default_cache())
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--makensis", default=shutil.which("makensis"))
    parser.add_argument("--payload-only", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("version must be three numeric components")
    if not args.payload_only and not args.makensis:
        parser.error("Install NSIS 3 or pass --makensis PATH")
    dest = args.output.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    payload = dest / "payload"
    if payload.exists():
        parser.error("payload already exists; choose a fresh output directory")
    prepare(payload, args.native_dir.resolve(), args.cache)
    if args.payload_only:
        print(payload)
    else:
        build_setup(dest, payload, args.makensis, args.version)
