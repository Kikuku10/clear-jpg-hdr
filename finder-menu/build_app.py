#!/usr/bin/python3
"""Build a self-contained Finder app and optionally a drag-to-install DMG."""

import argparse
import platform
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
APP_NAME = "清除JPG HDR小工具"
APP_ID = "local.mimi.ClearJPGHDR"
EXT_ID = APP_ID + ".FinderMenu"
VERSION = "1.3.2"


def run(*arguments):
    subprocess.run(arguments, check=True)


def write_plist(path, value):
    with path.open("wb") as stream:
        plistlib.dump(value, stream, sort_keys=False)


def build(output):
    output = Path(output).absolute()
    app = output / (APP_NAME + ".app")
    extension = app / "Contents/PlugIns/FinderMenu.appex"
    worker = app / "Contents/Helpers/ClearJPGHDRWorker.app"
    if app.exists():
        raise FileExistsError("Refusing to overwrite existing app: " + str(app))
    for source in (ROOT / "Host.swift", ROOT / "FinderMenu.swift", ROOT.parent / "clear_hdr.py"):
        if not source.is_file():
            raise FileNotFoundError(source)
    for bundle in (app, extension, worker):
        (bundle / "Contents/MacOS").mkdir(parents=True)
        (bundle / "Contents/Resources").mkdir()

    target = platform.machine() + "-apple-macos13.0"
    run("/usr/bin/xcrun", "swiftc", "-target", target, "-parse-as-library",
        "-framework", "AppKit", "-framework", "SwiftUI", "-framework", "FinderSync",
        "-o", str(app / "Contents/MacOS/ClearJPGHDR"), str(ROOT / "Host.swift"))
    shutil.copyfile(app / "Contents/MacOS/ClearJPGHDR", worker / "Contents/MacOS/ClearJPGHDRWorker")
    (worker / "Contents/MacOS/ClearJPGHDRWorker").chmod(0o755)
    run("/usr/bin/xcrun", "swiftc", "-target", target, "-parse-as-library",
        "-framework", "AppKit", "-framework", "FinderSync",
        "-Xlinker", "-e", "-Xlinker", "_NSExtensionMain",
        "-o", str(extension / "Contents/MacOS/FinderMenu"), str(ROOT / "FinderMenu.swift"))
    shutil.copy2(ROOT.parent / "clear_hdr.py", app / "Contents/Resources/clear_hdr.py")
    shutil.copyfile(ROOT.parent / "assets/AppIcon.icns", app / "Contents/Resources/AppIcon.icns")
    pack = ROOT.parent / "packaging"
    run(sys.executable, str(pack / "bundle_python.py"), str(app), "--arch", platform.machine())
    run(sys.executable, str(pack / "bundle_exiftool.py"), str(app / "Contents/Resources"))

    base = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": "7",
        "CFBundleSupportedPlatforms": ["MacOSX"],
        "LSMinimumSystemVersion": "13.0",
        "LSUIElement": True,
        "NSPrincipalClass": "NSApplication",
    }
    write_plist(app / "Contents/Info.plist", dict(base, **{
        "CFBundleExecutable": "ClearJPGHDR",
        "CFBundleIdentifier": APP_ID,
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundlePackageType": "APPL",
        "CFBundleIconFile": "AppIcon.icns",
    }))
    write_plist(extension / "Contents/Info.plist", dict(base, **{
        "CFBundleExecutable": "FinderMenu",
        "CFBundleIdentifier": EXT_ID,
        "CFBundleName": "FinderMenu",
        "CFBundleDisplayName": APP_NAME,
        "CFBundlePackageType": "XPC!",
        "NSExtension": {
            "NSExtensionAttributes": {},
            "NSExtensionPointIdentifier": "com.apple.FinderSync",
            "NSExtensionPrincipalClass": "FinderMenu",
        },
    }))
    write_plist(worker / "Contents/Info.plist", dict(base, **{
        "CFBundleExecutable": "ClearJPGHDRWorker",
        "CFBundleIdentifier": APP_ID + ".Worker",
        "CFBundleName": "ClearJPGHDRWorker",
        "CFBundlePackageType": "APPL",
    }))
    entitlements = output / "FinderMenu.entitlements"
    write_plist(entitlements, {
        "com.apple.security.app-sandbox": True,
        "com.apple.security.temporary-exception.files.home-relative-path.read-write": [
            "/Library/Application Support/ClearJPGHDR/Requests/",
        ],
    })
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none",
        "--entitlements", str(entitlements), str(extension))
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(worker))
    run("/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(app))
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
    run("/usr/bin/plutil", "-lint", str(app / "Contents/Info.plist"),
        str(extension / "Contents/Info.plist"), str(entitlements))
    return app


def make_dmg(app):
    dmg = app.parent / ("ClearJPGHDR-{}-{}.dmg".format(VERSION, platform.machine()))
    with tempfile.TemporaryDirectory(prefix="clear-jpg-hdr-dmg-") as directory:
        stage = Path(directory)
        run("/usr/bin/ditto", str(app), str(stage / app.name))
        (stage / "Applications").symlink_to("/Applications")
        run("/usr/bin/hdiutil", "create", "-fs", "HFS+", "-format", "UDZO",
            "-volname", APP_NAME, "-srcfolder", directory, str(dmg))
    run("/usr/bin/hdiutil", "verify", str(dmg))
    return dmg


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?", default=ROOT / "build")
    parser.add_argument("--dmg", action="store_true")
    args = parser.parse_args()
    app = build(args.output)
    print(make_dmg(app) if args.dmg else app)
