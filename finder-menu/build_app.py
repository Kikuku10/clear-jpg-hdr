#!/usr/bin/python3
"""Build and ad-hoc sign the local Finder menu app; do not install it."""

import platform
import plistlib
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
APP_NAME = "清除JPG HDR小工具"
APP_ID = "local.mimi.ClearJPGHDR"
EXT_ID = APP_ID + ".FinderMenu"


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

    base = {
        "CFBundleDevelopmentRegion": "zh_CN",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleShortVersionString": "1.1",
        "CFBundleVersion": "4",
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


if __name__ == "__main__":
    if len(sys.argv) > 2:
        raise SystemExit("Usage: build_app.py [new-output-directory]")
    print(build(sys.argv[1] if len(sys.argv) == 2 else ROOT / "build"))
