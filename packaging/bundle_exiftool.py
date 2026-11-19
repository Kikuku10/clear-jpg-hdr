"""Build a relocatable, architecture-specific Perl and ExifTool for the app."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import tempfile

PERL = "5.44.0"
EXIF = "13.55"
CACHE = Path.home() / "Library/Caches/clear-jpg-hdr/exiftool"
PERL_URL = f"https://www.cpan.org/src/5.0/perl-{PERL}.tar.xz"
PERL_SHA = "505cf43912e9480495c344c70260452e32aa2a73c546a026b3f100053b23ce91"
EXIF_URL = f"https://cpan.metacpan.org/authors/id/E/EX/EXIFTOOL/Image-ExifTool-{EXIF}.tar.gz"
EXIF_SHA = "5f4c81d34ad406538c2871ad72dbfceb5d9b412b2f16cbbeb4d712d270846667"


def source(url, digest):
    name = url.rsplit("/", 1)[1]
    path = CACHE / name
    if not path.is_file():
        with tempfile.TemporaryDirectory(dir=CACHE) as download:
            temp = Path(download) / name
            subprocess.run(["/usr/bin/curl", "-fLsS", "--retry", "3", url, "-o", temp], check=True)
            if hashlib.sha256(temp.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Invalid SHA-256: {name}")
            os.replace(temp, path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Invalid SHA-256: {path}")
    return path


def build_runtime(work, arch, perl_source, exif_source):
    for archive in (perl_source, exif_source):
        subprocess.run(["/usr/bin/tar", "-xf", archive, "-C", work], check=True)
    perl = work / f"perl-{PERL}"
    exif = work / f"Image-ExifTool-{EXIF}"
    dest = work / "stage"
    dest.mkdir()
    # Keep a neutral compiled-in prefix. Perl expands @INC relative to its executable.
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LC_ALL": "C", "MACOSX_DEPLOYMENT_TARGET": "13.0"}
    arch_cmd = ["/usr/bin/arch", "-" + arch]
    commands = [
        ["/bin/sh", "Configure", "-des", "-Dprefix=/ClearJPGHDR", "-Duserelocatableinc",
         "-Uusedl", "-Uuseshrplib", "-Dcc=/usr/bin/clang", f"-Dccflags=-arch {arch}",
         f"-Dldflags=-arch {arch}", "-Dlocincpth=", "-Dloclibpth=", "-Dman1dir=none",
         "-Dman3dir=none", "-Dcf_by=builder", "-Dcf_email=builder@localhost",
         "-Dmyhostname=build", "-Dmydomain=.invalid", "-Dperladmin=builder@localhost",
         "-Dnoextensions=DB_File GDBM_File NDBM_File ODBM_File"],
        ["/usr/bin/make", "-j8"],
        ["/usr/bin/make", "install", f"DESTDIR={dest}"],
    ]
    for command in commands:
        subprocess.run(arch_cmd + command, env=env, cwd=perl, check=True)
    tree = dest / "ClearJPGHDR"
    result = work / "runtime"
    (result / "bin").mkdir(parents=True)
    (result / "tool").mkdir()
    (result / "licenses").mkdir()
    shutil.copy2(tree / "bin/perl", result / "bin/perl")
    shutil.copytree(tree / "lib", result / "lib", symlinks=True)
    shutil.copy2(exif / "exiftool", result / "tool/exiftool")
    shutil.copytree(exif / "lib", result / "tool/lib")
    shutil.copy2(exif / "README", result / "licenses/ExifTool-README")
    for name in ("Artistic", "Copying", "README"):
        shutil.copy2(perl / name, result / ("licenses/Perl-" + name))
    (result / "bin/exiftool").write_text(
        '#!/bin/sh\nunset PERL5OPT PERL5LIB PERLLIB PERLIO PERL_UNICODE\n'
        'exec "${0%/*}/perl" "${0%/*}/../tool/exiftool" -config "" "$@"\n'
    )
    (result / "bin/exiftool").chmod(0o755)
    version = subprocess.check_output(arch_cmd + [result / "bin/exiftool", "-ver"],
                                      env={"PATH": ""}, text=True).strip()
    if version != EXIF:
        raise RuntimeError("Bundled ExifTool version check failed")
    return result


def bundle(resources, arch=None):
    arch = arch or platform.machine()
    if arch not in ("arm64", "x86_64"):
        raise ValueError("Expected arm64 or x86_64")
    CACHE.mkdir(parents=True, exist_ok=True)
    # Increment the recipe revision when changing the compiled Perl configuration.
    cached = CACHE / f"perl-{PERL}-exiftool-{EXIF}-{arch}-r1"
    if not cached.is_dir():
        perl = source(PERL_URL, PERL_SHA)
        exif = source(EXIF_URL, EXIF_SHA)
        with tempfile.TemporaryDirectory(prefix="clearhdr-perl-") as temp:
            runtime = build_runtime(Path(temp), arch, perl, exif)
            runtime.rename(cached)
    out = Path(resources) / "exiftool"
    # The interpreter has static extensions. Headers, static archives and Perl
    # developer tools are unnecessary for ExifTool and should not be distributed.
    shutil.copytree(cached, out, symlinks=True, ignore=shutil.ignore_patterns(
        "CORE", "*.a", "*.h", "*.ph", "*.pod", "*.bs", ".packlist", "perllocal.pod",
        "buildcustomize.pl", "Config.pod", "perldiag.pod",
    ))
    for config in (out / "lib").rglob("Config_heavy.pl"):
        content = config.read_text()
        for hostname in {socket.gethostname(), platform.node()}:
            if hostname:
                content = content.replace(hostname.lower(), "build.invalid").replace(hostname, "build.invalid")
        config.chmod(0o644)
        config.write_text(content)
    subprocess.run(["/usr/bin/strip", "-S", out / "bin/perl"], check=True)
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none",
                    out / "bin/perl"], check=True)
    return out / "bin/exiftool"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path)
    parser.add_argument("--arch", choices=("arm64", "x86_64"))
    args = parser.parse_args()
    print(bundle(args.resources, args.arch))
