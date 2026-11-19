"""Exercise the signed app's Python, ExifTool and photo operations after relocation."""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def verify(app):
    app = app.resolve()
    resources = app / "Contents/Resources"
    python = app / "Contents/Helpers/python3"
    tests = Path(__file__).resolve().parents[1] / "tests"
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", "--deep", str(app)], check=True)
    with tempfile.TemporaryDirectory(prefix="hdr-release-test-") as temp:
        work = Path(temp)
        (work / "clear_hdr.py").symlink_to(resources / "clear_hdr.py")
        (work / "tests").mkdir()
        (work / "tests/fixtures").symlink_to(tests / "fixtures")
        # The tests find clear_hdr.py relative to their own path.
        (work / "tests/test_clear_hdr.py").write_bytes((tests / "test_clear_hdr.py").read_bytes())
        env = {"HOME": temp, "TMPDIR": temp, "PATH": "/usr/bin:/bin", "LC_CTYPE": "UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1"}
        check = (
            "import os,sys,runpy; "
            "e=runpy.run_path(sys.argv[1])['EXIFTOOL']; "
            "assert os.path.commonpath([os.path.realpath(e),sys.argv[2]])==sys.argv[2], e; "
            "print(sys.version.split()[0], e, flush=True)"
        )
        subprocess.run([str(python), "-I", "-B", "-c", check,
                        str(work / "clear_hdr.py"), str(app)], env=env, check=True)
        subprocess.run([str(python), "-I", "-B", "-m", "unittest", "discover", "-s",
                        str(work / "tests"), "-v"], cwd=temp, env=env, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    verify(parser.parse_args().app)
