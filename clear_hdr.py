#!/usr/bin/python3
"""Remove explicitly identified JPEG gain maps from selected images and folders."""

import argparse
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import stat as stat_flags
import subprocess
import sys
import tempfile


TITLE = "清除JPG HDR小工具"
# The installed app must be independent of the developer's Homebrew environment.
RESOURCES = Path(__file__).resolve().parent
BUNDLED = os.name == "nt" or (RESOURCES.name == "Resources" and RESOURCES.parent.name == "Contents")
if os.name == "nt":
    EXIFTOOL = str(RESOURCES / "exiftool/exiftool.exe")
elif BUNDLED:
    EXIFTOOL = str(RESOURCES / "exiftool/bin/exiftool")
else:
    EXIFTOOL = next((path for path in (
        "/opt/homebrew/bin/exiftool", "/usr/local/bin/exiftool", "/opt/local/bin/exiftool",
    ) if os.path.isfile(path) and os.access(path, os.X_OK)), None)
READ_TAGS = [
    "-FileType", "-NumberOfImages", "-MPImageType", "-MPImageLength",
    "-MPImageStart", "-MPImageFormat", "-XMP-hdrgm:All", "-XMP-GContainer:All",
    "-XMP-HDRGainMap:All", "-Warning", "-Error",
]


def exiftool(path, *options, binary=False):
    if os.name == "nt":
        # ExifTool's Windows launcher uses the system code page for argv. Give it
        # the filename through a UTF-8 argument file on stdin, even for reads.
        if any(c in str(path) for c in ("\r", "\n", "\0")):
            raise ValueError("文件名含有不支持的字符")
        result = subprocess.run(
            [EXIFTOOL, "-config", "", "-charset", "filename=utf8", *options, "-@", "-"],
            input=(str(path) + "\n").encode("utf-8"), capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if not binary:
            result.stdout = result.stdout.decode("utf-8")
        result.stderr = result.stderr.decode("utf-8", errors="replace")
        return result
    return subprocess.run([EXIFTOOL, *options, str(path)], capture_output=True, text=not binary)


def metadata(path):
    result = exiftool(path, "-j", "-a", "-G1:4", "-n", *READ_TAGS)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "无法读取图片")
    record = json.loads(result.stdout)[0]
    errors = values(record, "Error") + values(record, "Warning")
    if errors:
        raise RuntimeError("；".join(str(error) for error in errors))
    return record


def values(record, tag):
    return [value for key, value in record.items() if key.split(":")[-1] == tag]


def jpeg_end(data):
    """Locate the first image's EOI, excluding embedded EXIF thumbnails."""
    if data[:2] != b"\xff\xd8":
        return None
    position = 2
    while position < len(data):
        if data[position] != 0xff:
            return None
        while position < len(data) and data[position] == 0xff:
            position += 1
        if position >= len(data):
            return None
        marker = data[position]
        position += 1
        if marker == 0xd9:
            return position
        if marker in (0, 0xd8) or position + 2 > len(data):
            return None
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            return None
        position += length
        if marker == 0xda:
            while position < len(data):
                position = data.find(b"\xff", position)
                if position < 0:
                    return None
                following = position + 1
                while following < len(data) and data[following] == 0xff:
                    following += 1
                if following >= len(data):
                    return None
                if data[following] != 0 and not 0xd0 <= data[following] <= 0xd7:
                    break
                position = following + 1
    return None


def skip_reason(path, record):
    if values(record, "FileType") != ["JPEG"]:
        return "不是 JPEG 文件"
    types = values(record, "MPImageType")
    if 0x50000 not in types:
        return "未发现明确标记的 HDR 增益图"
    images = {}
    for key, value in record.items():
        parts = key.split(":")
        group, tag = parts[0], parts[-1]
        if re.fullmatch(r"MPImage[1-9][0-9]*", group):
            images.setdefault(group, {})[tag] = value
    # shortcut: accept only an explicit primary + gain-map pair; other HDR formats need samples.
    if (values(record, "NumberOfImages") != [2]
            or set(images) != {"MPImage1", "MPImage2"}
            or images["MPImage1"].get("MPImageType") != 0x30000
            or images["MPImage1"].get("MPImageStart") != 0
            or images["MPImage2"].get("MPImageType") != 0x50000
            or any(image.get("MPImageFormat") != 0 for image in images.values())):
        return "包含其他附图或不支持的多图结构"
    if any(str(value).lower() in ("true", "1")
           for value in values(record, "BaseRenditionIsHDR")):
        return "主图本身为 HDR，不能直接移除增益图"
    semantics = values(record, "DirectoryItemSemantic")
    if semantics and semantics != [["Primary", "GainMap"]]:
        return "XMP 容器包含其他附加内容"
    start = images["MPImage2"].get("MPImageStart")
    length = images["MPImage2"].get("MPImageLength")
    if (not isinstance(start, int) or not isinstance(length, int)
            or start < 4 or length < 4 or start + length != path.stat().st_size):
        return "增益图边界不明确或含有额外尾部数据"
    data = path.read_bytes()
    primary_end = jpeg_end(data)
    if (primary_end is None or primary_end > start
            or any(data[primary_end:start]) or jpeg_end(data[start:]) != length):
        return "增益图边界不明确"
    return None


def clear_file(path, stat):
    result = exiftool(path, "-MPF:All=", "-Trailer:All=", "-XMP-hdrgm:All=",
                      "-XMP-GContainer:All=", "-XMP-HDRGainMap:All=", "-overwrite_original_in_place")
    if result.returncode or result.stderr.strip():
        raise RuntimeError(result.stderr.strip() or "ExifTool 写入失败")
    if values(metadata(path), "MPImageType"):
        raise RuntimeError("写入后仍检测到多图数据")
    if os.name == "nt":
        # ExifTool's Windows timestamp conversion rounds creation times.
        restore_windows_creation_time(path, stat)
    # ExifTool's -P truncates sub-second timestamps on this Mac.
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def restore_windows_creation_time(path, stat):
    import msvcrt
    from ctypes import wintypes
    # Windows timestamps count 100 ns ticks from 1601 (UNIX time begins in 1970).
    birth = getattr(stat, "st_birthtime_ns", stat.st_ctime_ns) // 100 + 116444736000000000
    creation = wintypes.FILETIME(birth & 0xffffffff, birth >> 32)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    with path.open("r+b") as stream:
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
        if not kernel.SetFileTime(handle, ctypes.byref(creation), None, None):
            raise ctypes.WinError(ctypes.get_last_error())


def copy_original(path, destination, stat):
    if os.name == "nt":
        # CopyFileW also copies NTFS alternate data streams and file attributes.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel.CopyFileW(str(path), str(destination), True):
            raise ctypes.WinError(ctypes.get_last_error())
        restore_windows_creation_time(destination, stat)
    else:
        # macOS cp -cp preserves Finder metadata and uses APFS clones when available.
        result = subprocess.run(["/bin/cp", "-cp", str(path), str(destination)],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "无法复制原图")


def clear_with_backup(path, stat):
    backup = path.with_name(path.name + "_original")
    if os.path.lexists(backup):
        raise RuntimeError("同名备份已存在，原图未修改")
    if not os.access(path, os.W_OK):
        raise PermissionError("无法写入图片")
    with tempfile.TemporaryDirectory(prefix=".clear-jpg-hdr-", dir=path.parent) as scratch:
        work = Path(scratch) / "work.jpg"
        original = Path(scratch) / "original"
        for destination in (work, original):
            copy_original(path, destination, stat)
        clear_file(work, stat)
        current = path.lstat()
        if (current.st_dev, current.st_ino, current.st_ctime_ns, current.st_mtime_ns, current.st_size) != (
                stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size):
            raise RuntimeError("处理期间原图已改变，已停止写入")

        if os.name == "nt":
            # Windows rename refuses to replace an existing backup. ReplaceFileW
            # merges the original file's ACLs, creation time and named streams.
            os.rename(original, backup)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            if not kernel.ReplaceFileW(str(path), str(work), None, 0, None, None):
                raise ctypes.WinError(ctypes.get_last_error())
            return
        # Darwin RENAME_EXCL publishes the completed backup without overwriting a collision.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.renamex_np(os.fsencode(original), os.fsencode(backup), 0x00000004) != 0:
            code = ctypes.get_errno()
            if code == errno.EEXIST:
                raise RuntimeError("同名备份已存在，原图未修改")
            raise OSError(code, os.strerror(code), str(backup))
        os.replace(work, path)


def is_link(path):
    if os.name == "nt":
        return bool(path.lstat().st_file_attributes & stat_flags.FILE_ATTRIBUTE_REPARSE_POINT)
    return path.is_symlink()


def run(paths, backup=False):
    report = {"processed": [], "skipped": [], "failed": []}
    if not EXIFTOOL or not os.access(EXIFTOOL, os.X_OK):
        reason = "App 内的 ExifTool 缺失，请重新安装小工具" if BUNDLED else "未找到已安装的 ExifTool"
        report["failed"].append(["ExifTool", reason])
        return report
    candidates = []
    for raw_path in paths:
        path = Path(raw_path).absolute()
        try:
            if is_link(path):
                report["skipped"].append([str(path), "不处理链接或重解析点"])
            elif path.is_dir():
                candidates.extend(sorted(
                    (child for child in path.iterdir()
                     if child.suffix.lower() in (".jpg", ".jpeg")),
                    key=lambda item: item.name,
                ))
            else:
                candidates.append(path)
        except OSError as error:
            report["failed"].append([str(path), str(error)])
    seen = set()
    for path in candidates:
        try:
            if is_link(path) or not path.is_file() or path.suffix.lower() not in (".jpg", ".jpeg"):
                report["skipped"].append([str(path), "只处理 JPG/JPEG 普通文件"])
                continue
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino) if stat.st_ino else os.path.normcase(str(path))
            if identity in seen:
                continue
            seen.add(identity)
            # In-place writes also affect other hard links, outside the Finder selection.
            if stat.st_nlink > 1:
                report["skipped"].append([str(path), "文件存在硬链接"])
                continue
            reason = skip_reason(path, metadata(path))
            if reason:
                report["skipped"].append([str(path), reason])
                continue
            if os.name == "nt" and stat.st_file_attributes & stat_flags.FILE_ATTRIBUTE_READONLY:
                raise PermissionError("图片为只读文件")
            if backup:
                clear_with_backup(path, stat)
            else:
                clear_file(path, stat)
            report["processed"].append(str(path))
        except (OSError, ValueError, RuntimeError) as error:
            report["failed"].append([str(path), str(error)])
    return report


def notify(report):
    if not report["failed"]:
        return
    summary = "已处理 {} 张，跳过 {} 项，失败 {} 项".format(
        len(report["processed"]), len(report["skipped"]), len(report["failed"])
    )
    details = "\n".join(Path(path).name + "：" + reason for path, reason in report["failed"][:5])
    script = ('on run argv\n'
              'display dialog (item 1 of argv) with title (item 2 of argv) '
              'buttons {"好"} default button "好" with icon caution\nend run')
    message = summary + "\n\n" + details
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script, message, TITLE], capture_output=True, text=True,
    )
    if result.returncode:
        print(result.stderr, file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", action="store_true", help="keep an original beside each edited JPEG")
    parser.add_argument("--stdin-paths", action="store_true", help="read a UTF-8 JSON array of absolute paths")
    parser.add_argument("paths", nargs="*")
    options = parser.parse_args()
    try:
        paths = options.paths
        if options.stdin_paths:
            if paths:
                raise ValueError("输入来源重复")
            paths = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
            if not isinstance(paths, list) or not all(
                isinstance(p, str) and os.path.isabs(p) and "\x00" not in p for p in paths
            ):
                raise ValueError("菜单请求必须包含绝对文件路径")
        outcome = run(paths, backup=options.backup)
    except ValueError as error:
        outcome = {"processed": [], "skipped": [], "failed": [["菜单请求", str(error)]]}
    if os.name == "nt":
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(outcome, ensure_ascii=False))
        raise SystemExit(1 if outcome["failed"] else 0)
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    notify(outcome)
