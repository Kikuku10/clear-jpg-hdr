"""Install a relocatable CPython and its licenses into a macOS app."""

import argparse
import hashlib
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import zipfile


RELEASE = "20261110"
VERSION = "3.13.18"
ARCHIVES = {
    "arm64": ("aarch64", "11c316dfa24e1184e91de3c1434ed1d3e986c30b2d7a0273257067efe611a677"),
    "x86_64": ("x86_64", "a942bf40b7c1219bd05f57b5d449eff5c79fb5cadb781f3d08a8fa27a170e436"),
}
CACHE = Path.home() / "Library/Caches/ClearJPGHDR/downloads"


def archive_for(arch):
    cpu, digest = ARCHIVES[arch]
    name = f"cpython-{VERSION}+{RELEASE}-{cpu}-apple-darwin-pgo+lto-full.tar.zst"
    url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{RELEASE}/" + name.replace("+", "%2B")
    CACHE.mkdir(parents=True, exist_ok=True)
    archive = CACHE / name
    if archive.is_file() and hashlib.sha256(archive.read_bytes()).hexdigest() == digest:
        return archive
    with tempfile.TemporaryDirectory(dir=CACHE) as scratch:
        download = Path(scratch) / name
        subprocess.run(["/usr/bin/curl", "-fLSs", "--retry", "3", "--output", str(download), url], check=True)
        if hashlib.sha256(download.read_bytes()).hexdigest() != digest:
            raise ValueError("CPython archive SHA-256 mismatch: " + name)
        download.replace(archive)
    return archive


def bundle_python(app, arch, identity="-"):
    resources = Path(app) / "Contents/Resources/runtime/python"
    executable = Path(app) / "Contents/Helpers/python3"
    executable.parent.mkdir(exist_ok=True, parents=True)
    resources.mkdir(exist_ok=False, parents=True)
    with tempfile.TemporaryDirectory() as scratch:
        upstream = Path(scratch) / "python"
        subprocess.run(["/usr/bin/tar", "-xf", str(archive_for(arch)), "-C", scratch,
                        "python/install/bin/python3.13", "python/install/lib/python3.13",
                        "python/licenses"], check=True)
        # The standalone executable statically includes the extension modules used by clear_hdr.
        shutil.copy2(upstream / "install/bin/python3.13", executable)
        shutil.copytree(upstream / "licenses", resources / "licenses")
        stdlib = upstream / "install/lib/python3.13"
        with zipfile.ZipFile(resources / "stdlib.zip", "w", compression=zipfile.ZIP_DEFLATED) as lib:
            for module in sorted(stdlib.rglob("*.py")):
                relative = module.relative_to(stdlib)
                if relative.parts[0] in {"test", "site-packages", "ensurepip", "idlelib", "tkinter", "turtledemo"}:
                    continue
                lib.write(module, relative)
    # Restrict even a direct invocation to the bundled library; omit external site packages.
    (resources / "python3._pth").write_text("../Resources/runtime/python/stdlib.zip\n", encoding="utf-8")
    # Leave only signed code and symlinks in Helpers so the enclosing app can be signed.
    executable.with_suffix("._pth").symlink_to("../Resources/runtime/python/python3._pth")
    subprocess.run(["/usr/bin/codesign", "--force", "--options", "runtime", "--sign", identity,
                    "--timestamp=none" if identity == "-" else "--timestamp", str(executable)], check=True)
    return executable


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--arch", choices=ARCHIVES, default=platform.machine())
    parser.add_argument("--identity", default="-")
    options = parser.parse_args()
    print(bundle_python(options.app, options.arch, options.identity))
