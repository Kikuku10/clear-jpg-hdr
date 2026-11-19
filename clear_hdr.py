#!/usr/bin/python3
"""Remove explicitly identified JPEG gain maps from selected images and folders."""

import argparse
from collections import Counter
import ctypes
import errno
import json
import os
from pathlib import Path
import re
import stat as stat_flags
import struct
import subprocess
import sys
import tempfile
from xml.etree import ElementTree


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
    # The separate preview-preserving path handles supported three-image files.
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


XMP_ID = b"http://ns.adobe.com/xap/1.0/\0"
ISO_GAIN_ID = b"urn:iso:std:iso:ts:21496:-1\0"
HDRGM = "{http://ns.adobe.com/hdr-gain-map/1.0/}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
XMP = "{adobe:ns:meta/}"


class UnsupportedLayout(ValueError):
    pass


def check_layout(condition, reason="三图索引或增益图边界不明确"):
    if not condition:
        raise UnsupportedLayout(reason)


def gain_segments(data):
    """Find complete metadata segments before the first scan of one bounded JPEG."""
    sections = {}
    pos = 2
    while pos < len(data):
        check_layout(data[pos] == 0xff and pos + 4 <= len(data))
        marker = data[pos + 1]
        if marker == 0xda:
            # A second scan can carry its own metadata. Leave unfamiliar files
            # untouched if a declaration lies beyond the headers we can edit.
            for signature in (XMP_ID, ISO_GAIN_ID, b"MPF\0", b"http://ns.adobe.com/xmp/extension/\0"):
                check_layout(data.find(signature, pos) == -1)
            break
        end = pos + 2 + int.from_bytes(data[pos + 2:pos + 4], "big")
        check_layout(pos + 4 <= end <= len(data))
        payload = data[pos + 4:end]
        for kind, code, prefix in (("xmp", 0xe1, XMP_ID), ("iso", 0xe2, ISO_GAIN_ID),
                                   ("mpf", 0xe2, b"MPF\0")):
            if marker == code and payload.startswith(prefix):
                check_layout(kind not in sections)
                sections[kind] = (pos, end, payload[len(prefix):])
        check_layout(not payload.startswith(b"http://ns.adobe.com/xmp/extension/\0"))
        pos = end
    check_layout(pos < len(data))
    return sections


def check_gain_declarations(sections, primary):
    check_layout("xmp" in sections and "iso" in sections)
    # Version 0, single-channel ISO metadata, using the base colour space and
    # zero base headroom. Keep unfamiliar versions and HDR base renditions.
    iso = sections["iso"][2]
    check_layout(len(iso) == 61 and iso[:13] == bytes.fromhex("00000000400000000000000001"),
                 "主图 HDR 状态或增益图声明不受支持")
    raw = sections["xmp"][2]
    check_layout(b"<!" not in raw)
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as error:
        raise UnsupportedLayout("无法识别增益图 XMP") from error
    descriptions = root.findall("./" + RDF + "RDF/" + RDF + "Description")
    check_layout(len(descriptions) == 1)
    desc = descriptions[0]
    check_layout(desc.get(HDRGM + "Version") == "1.0")
    if primary:
        # Remove a packet only if every property belongs to the HDR declaration.
        check_layout(root.tag == XMP + "xmpmeta" and len(root) == 1 and len(root[0]) == 1
                     and set(root.attrib) <= {XMP + "xmptk"} and not root[0].attrib
                     and len(desc) == 0 and set(desc.attrib) <= {
                         RDF + "about", HDRGM + "Version", HDRGM + "BaseRenditionIsHDR"}
                     and not desc.get(RDF + "about") and
                     all(not (el.text or "").strip() and not (el.tail or "").strip() for el in root.iter()),
                     "主图 XMP 含有其他信息")
    check_layout(desc.get(HDRGM + "BaseRenditionIsHDR", "False" if primary else "").lower()
                 in ("false", "0"), "主图本身为 HDR 或主图 HDR 状态不明确")


def mp_index(order, entries):
    """A single standard MP Index IFD: version, image count, then MP entries."""
    def pack(fmt, *args):
        return struct.pack(order + fmt, *args)
    count = len(entries)
    tiff = (b"II" if order == "<" else b"MM") + pack("HIH", 42, 8, 3)
    tiff += pack("HHI", 0xb000, 7, 4) + b"0100"
    tiff += pack("HHII", 0xb001, 4, 1, count)
    tiff += pack("HHIII", 0xb002, 7, 16 * count, 50, 0)
    return tiff + b"".join(pack("IIIHH", kind, size, offset, 0, 0) for kind, size, offset in entries)


def keep_preview(path, record):
    kinds = [0x30000, 0x10002, 0x50000]
    check_layout(values(record, "NumberOfImages") == [3] and values(record, "FileType") == ["JPEG"])
    rows = []
    for index, kind in enumerate(kinds, 1):
        tags = {key.split(":")[-1]: val for key, val in record.items()
                if key.split(":")[0] == "MPImage" + str(index)}
        check_layout(tags.get("MPImageFormat") == 0 and tags.get("MPImageType") == kind)
        start, length = tags.get("MPImageStart"), tags.get("MPImageLength")
        check_layout(isinstance(start, int) and isinstance(length, int) and start >= 0 and length >= 4)
        rows.append((start, length))
    data = path.read_bytes()
    images, previous = [], 0
    for start, length in rows:
        check_layout(start >= previous and start + length <= len(data) and not any(data[previous:start]))
        image = data[start:start + length]
        check_layout(jpeg_end(image) == length)
        images.append(image)
        previous = start + length
    check_layout(rows[0][0] == 0 and previous == len(data))
    primary, preview, gain = images
    sections = gain_segments(primary)
    check_layout(set(sections) == {"xmp", "iso", "mpf"} and not gain_segments(preview))
    check_gain_declarations(sections, True)
    gain_tags = gain_segments(gain)
    check_layout(set(gain_tags) == {"xmp", "iso"})
    check_gain_declarations(gain_tags, False)
    pos, _, original = sections["mpf"]
    origin = pos + 8   # FF E2, length, "MPF\0", then TIFF offset zero
    order = "<" if original.startswith(b"II") else ">"
    check_layout(rows[1][0] >= origin and rows[2][0] >= origin)
    entries = [(k, size, start - origin if start else 0) for k, (start, size) in zip(kinds, rows)]
    # Exact comparison rejects extra IFD tags, dependency flags/indices and
    # unaccounted data in the index rather than silently discarding them.
    check_layout(original == mp_index(order, entries), "多图索引含有其他标记或依赖")
    primary_size = len(primary) - 16
    new_origin = origin
    for kind in ("xmp", "iso"):
        a, b, _ = sections[kind]
        primary_size -= b - a
        if a < pos:
            new_origin -= b - a
    tiff = mp_index(order, [(kinds[0], primary_size, 0), (kinds[1], len(preview), primary_size - new_origin)])
    marker = b"\xff\xe2" + struct.pack(">H", len(tiff) + 6) + b"MPF\0" + tiff
    for a, b, _ in sorted(sections.values(), reverse=True):
        primary = primary[:a] + (marker if a == pos else b"") + primary[b:]
    check_layout(len(primary) == primary_size)
    return primary + preview


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


def clear_staged(path, stat, backup_enabled, preview_output=None):
    backup = path.with_name(path.name + "_original")
    if backup_enabled and os.path.lexists(backup):
        raise RuntimeError("同名备份已存在，原图未修改")
    if not os.access(path, os.W_OK):
        raise PermissionError("无法写入图片")
    with tempfile.TemporaryDirectory(prefix=".clear-jpg-hdr-", dir=path.parent) as scratch:
        work = Path(scratch) / "work.jpg"
        original = Path(scratch) / "original"
        copy_original(path, work, stat)
        if backup_enabled:
            copy_original(path, original, stat)
        if preview_output is None:
            clear_file(work, stat)
        else:
            with work.open("wb") as stream:
                stream.write(preview_output)
            checked = metadata(work)
            primary_end = jpeg_end(preview_output)
            if (values(checked, "NumberOfImages") != [2]
                    or values(checked, "MPImageType") != [0x30000, 0x10002]
                    or primary_end is None or values(checked, "MPImageStart") != [0, primary_end]
                    or values(checked, "MPImageLength") != [primary_end, len(preview_output) - primary_end]
                    or work.read_bytes() != preview_output):
                raise RuntimeError("写入后预览图校验失败")
            os.utime(work, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        current = path.lstat()
        if (current.st_dev, current.st_ino, current.st_ctime_ns, current.st_mtime_ns, current.st_size) != (
                stat.st_dev, stat.st_ino, stat.st_ctime_ns, stat.st_mtime_ns, stat.st_size):
            raise RuntimeError("处理期间原图已改变，已停止写入")

        if os.name == "nt":
            # Windows rename refuses to replace an existing backup. ReplaceFileW
            # merges the original file's ACLs, creation time and named streams.
            if backup_enabled:
                os.rename(original, backup)
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            if not kernel.ReplaceFileW(str(path), str(work), None, 0, None, None):
                raise ctypes.WinError(ctypes.get_last_error())
            # Merging NTFS named streams can update the replacement's last-write time.
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return
        # Darwin RENAME_EXCL publishes the completed backup without overwriting a collision.
        if backup_enabled:
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
            record = metadata(path)
            preview_output = None
            if values(record, "MPImageType") == [0x30000, 0x10002, 0x50000]:
                try:
                    preview_output = keep_preview(path, record)
                    reason = None
                except UnsupportedLayout as error:
                    reason = str(error)
            else:
                reason = skip_reason(path, record)
            if reason:
                report["skipped"].append([str(path), reason])
                continue
            if os.name == "nt" and stat.st_file_attributes & stat_flags.FILE_ATTRIBUTE_READONLY:
                raise PermissionError("图片为只读文件")
            if backup or preview_output is not None:
                clear_staged(path, stat, backup, preview_output)
            else:
                clear_file(path, stat)
            report["processed"].append(str(path))
        except (OSError, ValueError, RuntimeError) as error:
            report["failed"].append([str(path), str(error)])
    return report


def menu_result(report):
    processed, skipped, failed = (len(report[key]) for key in ("processed", "skipped", "failed"))
    title = "清除成功" if processed else "处理完成"
    if failed:
        title = "部分照片清除失败" if processed else "清除失败"
    message = f"成功 {processed} 个，跳过 {skipped} 个，失败 {failed} 个。"
    if skipped:
        reasons = Counter(reason for _, reason in report["skipped"])
        message += "\n\n跳过原因\n" + "\n".join(
            f"{reason}（{count} 个）" for reason, count in reasons.most_common(3)
        )
    if failed:
        message += "\n\n失败原因\n" + "\n".join(
            f"{Path(path).name}：{reason}" for path, reason in report["failed"][:5]
        )
        if failed > 5:
            message += f"\n另有 {failed - 5} 个失败项。"
    if not (processed or skipped or failed):
        message += "\n所选位置没有 JPG/JPEG 图片。"
    return dict(report, title=title, message=message)


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
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(menu_result(outcome), ensure_ascii=False))
    raise SystemExit(1 if outcome["failed"] else 0)
