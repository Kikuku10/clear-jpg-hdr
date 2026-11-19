"""Exercise real ExifTool writes on disposable JPEG fixtures only."""

import importlib.util
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
ENGINE = Path(os.environ.get("CLEAR_HDR_TEST_ENGINE", ROOT / "clear_hdr.py"))
SPEC = importlib.util.spec_from_file_location("clear_hdr", ENGINE)
clear_hdr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clear_hdr)
FIXTURES = Path(__file__).with_name("fixtures")
IMAGES = (
    ("paris_exif_xmp_gainmap_littleendian.jpg", 4138, "little"),
    ("paris_exif_xmp_gainmap_bigendian.jpg", 4138, "big"),
    ("paris_exif_xmp_icc_gainmap_bigendian.jpg", 4676, "big"),
)


def image_payload(data):
    """Return the primary JPEG excluding APP/COM metadata, including all scans."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("missing JPEG SOI")
    result = bytearray(data[:2])
    position = 2
    while position < len(data):
        start = position
        if data[position] != 0xff:
            raise ValueError("invalid marker")
        while data[position] == 0xff:
            position += 1
        marker = data[position]
        position += 1
        if marker == 0xd9:
            result.extend(data[start:position])
            return bytes(result)
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ValueError("invalid segment length")
        position += length
        if not 0xe0 <= marker <= 0xef and marker != 0xfe:
            result.extend(data[start:position])
        if marker == 0xda:
            scan_start = position
            while position < len(data) - 1:
                if data[position] != 0xff:
                    position += 1
                    continue
                following = data[position + 1]
                if following == 0 or 0xd0 <= following <= 0xd7:
                    position += 2
                    continue
                if following == 0xff:
                    position += 1
                    continue
                break
            result.extend(data[scan_start:position])
    raise ValueError("missing JPEG EOI")


def binary_metadata(path, tag):
    result = clear_hdr.exiftool(path, "-b", "-" + tag, binary=True)
    result.check_returncode()
    return result.stdout


def set_xattr(path, name, data):
    if os.name == "nt":
        Path(str(path) + ":" + name.replace(":", "_")).write_bytes(data)
        return
    subprocess.run(["/usr/bin/xattr", "-wx", name, data.hex(), str(path)], check=True)


def xattrs(path):
    if os.name == "nt":
        return {name: stream.read_bytes()
                for name in ("com.apple.metadata:_kMDItemUserTags", "com.mimi.clear-hdr-test")
                for stream in (Path(str(path) + ":" + name.replace(":", "_")),) if stream.exists()}
    names = subprocess.run(
        ["/usr/bin/xattr", str(path)], capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    return {name: bytes.fromhex(subprocess.run(
        ["/usr/bin/xattr", "-px", name, str(path)],
        capture_output=True, text=True, check=True,
    ).stdout) for name in names}


class ClearHDRTests(unittest.TestCase):
    def test_only_failures_show_a_prompt(self):
        if os.name == "nt":
            path = self.directory / "损坏的照片.jpg"
            path.write_bytes(b"\xff\xd8broken")
            result = subprocess.run([sys.executable, str(ENGINE), "--", str(path)],
                                    capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["failed"][0][0], str(path))
            return
        for report in (
            {"processed": ["/tmp/photo.jpg"], "skipped": [], "failed": []},
            {"processed": [], "skipped": [["/tmp/photo.jpg", "普通图片"]], "failed": []},
            {"processed": [], "skipped": [], "failed": []},
        ):
            with self.subTest(report=report), mock.patch.object(clear_hdr.subprocess, "run") as execute:
                clear_hdr.notify(report)
                execute.assert_not_called()
        report = {"processed": ["/tmp/ok.jpg"], "skipped": [],
                  "failed": [["/tmp/bad.jpg", "写入失败"]]}
        with mock.patch.object(clear_hdr.subprocess, "run", return_value=
                               subprocess.CompletedProcess([], 0, "", "")) as execute:
            clear_hdr.notify(report)
            execute.assert_called_once()
            command = execute.call_args.args[0]
            self.assertEqual(command[0], "/usr/bin/osascript")
            self.assertIn("display dialog", command[2])
            self.assertNotIn("display notification", command[2])
            self.assertIn("bad.jpg：写入失败", command[3])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="clear-hdr-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def fixture(self, name="input.jpg", image=IMAGES[0], image_type=0x50000):
        original_name, offset, endian = image
        data = bytearray((FIXTURES / original_name).read_bytes())
        # The public fixtures use Undefined. Change only the MP entry's type.
        previous = int.from_bytes(data[offset:offset + 4], endian)
        self.assertEqual(previous & 0xffffff, 0)
        if image_type is not None:
            data[offset:offset + 4] = ((previous & 0xff000000) | image_type).to_bytes(4, endian)
        destination = self.directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return destination

    def test_gain_maps_removed_without_changing_primary_or_file_attributes(self):
        for index, image in enumerate(IMAGES):
            with self.subTest(image=image[0]):
                path = self.fixture("typed-{}.JpEg".format(index), image)
                tags = plistlib.dumps(["HDR测试\n6"], fmt=plistlib.FMT_BINARY)
                set_xattr(path, "com.apple.metadata:_kMDItemUserTags", tags)
                set_xattr(path, "com.mimi.clear-hdr-test", b"retain this attribute")
                os.utime(path, ns=(1690000000123456789, 1690000000123456789))
                before_stat = path.stat()
                before_payload = image_payload(path.read_bytes())
                before_exif = binary_metadata(path, "EXIF")
                before_icc = binary_metadata(path, "ICC_Profile")
                before_xattrs = xattrs(path)

                report = clear_hdr.run([str(path)])

                self.assertEqual(report, {"processed": [str(path)], "skipped": [], "failed": []})
                self.assertEqual(image_payload(path.read_bytes()), before_payload)
                self.assertEqual(binary_metadata(path, "EXIF"), before_exif)
                self.assertEqual(binary_metadata(path, "ICC_Profile"), before_icc)
                self.assertEqual(xattrs(path), before_xattrs)
                self.assertEqual(path.stat().st_birthtime, before_stat.st_birthtime)
                if os.name == "nt":
                    self.assertEqual(path.stat().st_birthtime_ns, before_stat.st_birthtime_ns)
                self.assertEqual(path.stat().st_mtime_ns, before_stat.st_mtime_ns)
                self.assertFalse(Path(str(path) + "_original").exists())
                self.assertEqual(clear_hdr.values(clear_hdr.metadata(path), "MPImageType"), [])
                self.assertNotIn(b"hdr-gain-map", path.read_bytes())
                self.assertEqual(len(path.read_bytes()), path.read_bytes().rfind(b"\xff\xd9") + 2)
                self.assertEqual(clear_hdr.run([str(path)])["processed"], [])

    def test_cli_backup_keeps_original_and_skips_it_on_rerun(self):
        path = self.fixture("我的 '照片' 1.jpg")
        unchanged = self.fixture("普通.jpg", image_type=None)
        original_bytes = path.read_bytes()
        tags = plistlib.dumps(["HDR\n6"], fmt=plistlib.FMT_BINARY)
        set_xattr(path, "com.apple.metadata:_kMDItemUserTags", tags)
        original_time = path.stat().st_mtime_ns
        original_birth = path.stat().st_birthtime
        backup_path = Path(str(path) + "_original")
        args = [sys.executable, str(ENGINE), "--backup", "--", str(self.directory)]
        selection = json.dumps([str(path), str(unchanged)], ensure_ascii=False).encode("utf-8")
        first = json.loads(subprocess.run([sys.executable, str(ENGINE), "--backup", "--stdin-paths"],
                                          input=selection, check=True, capture_output=True).stdout)
        self.assertEqual(first["failed"], [])
        self.assertEqual(first["processed"], [str(path)])
        self.assertEqual(backup_path.read_bytes(), original_bytes)
        self.assertFalse(Path(str(unchanged) + "_original").exists())
        self.assertEqual(image_payload(path.read_bytes()), image_payload(original_bytes))
        for candidate in (path, backup_path):
            self.assertEqual(candidate.stat().st_mtime_ns, original_time)
            self.assertEqual(xattrs(candidate)["com.apple.metadata:_kMDItemUserTags"], tags)
            if os.name == "nt":
                self.assertEqual(candidate.stat().st_birthtime, original_birth)
        second = json.loads(subprocess.run(args, check=True, capture_output=True).stdout)
        self.assertEqual(second["failed"], [])
        self.assertEqual(second["processed"], [])
        self.assertEqual(backup_path.read_bytes(), original_bytes)

    def test_backup_failure_preserves_photo_and_existing_backup(self):
        path = self.fixture()
        original_bytes = path.read_bytes()
        backup_path = Path(str(path) + "_original")
        backup_path.write_bytes(b"existing")
        report = clear_hdr.run([str(path)], backup=True)
        self.assertEqual(report["processed"], [])
        self.assertEqual(len(report["failed"]), 1)
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(backup_path.read_bytes(), b"existing")

        backup_path.unlink()
        def partial_write(work, stat):
            work.write_bytes(b"partial failed edit")
            raise RuntimeError("write failed")
        with mock.patch.object(clear_hdr, "clear_file", side_effect=partial_write):
            report = clear_hdr.run([str(path)], backup=True)
        self.assertEqual(report["failed"], [[str(path), "write failed"]])
        self.assertEqual(path.read_bytes(), original_bytes)
        self.assertEqual(list(path.parent.iterdir()), [path])

        if os.name == "nt":
            # A concurrent writer may create the backup after the initial check.
            real_rename = os.rename
            def collision(source, destination):
                Path(destination).write_bytes(b"concurrent backup")
                return real_rename(source, destination)
            with mock.patch.object(os, "rename", side_effect=collision):
                report = clear_hdr.run([str(path)], backup=True)
            self.assertEqual(len(report["failed"]), 1)
            self.assertEqual(backup_path.read_bytes(), b"concurrent backup")
            self.assertEqual(path.read_bytes(), original_bytes)

    def test_ordinary_undefined_preview_and_extra_trailer_are_unchanged(self):
        ordinary = self.fixture("ordinary.jpg")
        ordinary.write_bytes(ordinary.read_bytes()[:33487])
        clear_hdr.exiftool(ordinary, "-MPF:All=", "-XMP:All=", "-overwrite_original").check_returncode()
        undefined = self.fixture("undefined.jpg", image_type=None)
        preview = self.fixture("preview.jpg", image_type=0x010001)
        trailer = self.fixture("extra-trailer.jpg")
        trailer.write_bytes(trailer.read_bytes() + b"KEEP-OTHER-TRAILER")
        images = [ordinary, undefined, preview, trailer]
        before = {str(path): path.read_bytes() for path in images}

        report = clear_hdr.run([str(path) for path in images])

        self.assertEqual(report["processed"], [])
        self.assertEqual(report["failed"], [])
        self.assertEqual({entry[0] for entry in report["skipped"]}, set(before))
        for path in images:
            self.assertEqual(path.read_bytes(), before[str(path)])

    def test_zero_padding_is_accepted_but_unknown_gap_content_is_untouched(self):
        for name, padding, expected in (("zero-gap.jpg", bytes(11), "processed"),
                                        ("other-gap.jpg", b"OTHER-CONTENT", "skipped")):
            with self.subTest(name=name):
                path = self.fixture(name)
                data = bytearray(path.read_bytes())
                start = clear_hdr.values(clear_hdr.metadata(path), "MPImageStart")[-1]
                offset = IMAGES[0][1] + 8
                old_offset = int.from_bytes(data[offset:offset + 4], "little")
                data[offset:offset + 4] = (old_offset + len(padding)).to_bytes(4, "little")
                data = data[:start] + padding + data[start:]
                path.write_bytes(data)
                before_payload = image_payload(data)

                report = clear_hdr.run([str(path)])

                self.assertEqual(report["failed"], [])
                if expected == "processed":
                    self.assertEqual(report["processed"], [str(path)])
                    self.assertEqual(image_payload(path.read_bytes()), before_payload)
                else:
                    self.assertEqual(report["processed"], [])
                    self.assertEqual([entry[0] for entry in report["skipped"]], [str(path)])
                    self.assertEqual(path.read_bytes(), data)

    def test_folder_is_shallow_deduplicated_and_paths_and_links_are_safe(self):
        selected = self.fixture("选择 中文 空格 '单引号' &.JPEG" if os.name == "nt"
                                else "选择 中文 空格 '单引号' \"双引号\"\n换行.JPEG")
        nested = self.fixture("子目录/nested.jpg")
        hard_target = self.fixture("子目录/hard-target.jpg")
        hard_link = self.directory / "hard-link.jpg"
        os.link(hard_target, hard_link)
        if os.name == "nt":
            # Directory junctions need no Developer Mode or administrator privilege.
            symlink = self.directory / "junction.jpg"
            subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/j", str(symlink),
                            str(nested.parent)], capture_output=True, check=True)
        else:
            symlink = self.directory / "symbolic-link.jpg"
            symlink.symlink_to(nested)
        text_file = self.directory / "text.png"
        text_file.write_bytes(b"not a JPEG")
        protected = [nested, hard_target, hard_link, text_file]
        before = {path: path.read_bytes() for path in protected}

        report = clear_hdr.run([str(self.directory), str(selected), str(text_file), str(symlink)])

        self.assertEqual(report["processed"], [str(selected)])
        self.assertEqual(report["failed"], [])
        skipped = {path for path, _ in report["skipped"]}
        self.assertTrue({str(hard_link), str(symlink), str(text_file)} <= skipped)
        for path in protected:
            self.assertEqual(path.read_bytes(), before[path])
        self.assertFalse(any(self.directory.rglob("*_original")))

    def test_read_and_write_errors_are_reported_without_false_success(self):
        damaged = self.directory / "damaged.jpg"
        damaged.write_bytes(b"\xff\xd8broken")
        report = clear_hdr.run([str(damaged)])
        self.assertEqual(report["processed"], [])
        self.assertEqual(len(report["failed"]), 1)

        unreadable = self.fixture("unreadable.jpg")
        with mock.patch.object(clear_hdr, "metadata", side_effect=PermissionError("read denied")):
            report = clear_hdr.run([str(unreadable)])
        self.assertEqual(report["processed"], [])
        self.assertEqual(report["failed"], [[str(unreadable), "read denied"]])

        failed_write = self.fixture("failed-write.jpg")
        before = failed_write.read_bytes()
        real_run = clear_hdr.exiftool

        def write_error(path, *options):
            if "-MPF:All=" in options:
                return subprocess.CompletedProcess(options, 1, "", "write denied")
            return real_run(path, *options)

        with mock.patch.object(clear_hdr, "exiftool", side_effect=write_error):
            report = clear_hdr.run([str(failed_write)])
        self.assertEqual(report["processed"], [])
        self.assertEqual(report["failed"], [[str(failed_write), "write denied"]])
        self.assertEqual(failed_write.read_bytes(), before)

        if os.name == "nt":
            failed_write.chmod(stat.S_IREAD)
            try:
                for backup in (False, True):
                    report = clear_hdr.run([str(failed_write)], backup=backup)
                    self.assertEqual(report["failed"], [[str(failed_write), "图片为只读文件"]])
                    self.assertEqual(failed_write.read_bytes(), before)
                    self.assertFalse(Path(str(failed_write) + "_original").exists())
            finally:
                failed_write.chmod(stat.S_IREAD | stat.S_IWRITE)


if __name__ == "__main__":
    unittest.main()
