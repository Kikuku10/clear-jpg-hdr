"""Exercise real ExifTool writes on disposable JPEG fixtures only."""

import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import stat
import struct
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


def preview_fixture(endian="little", extra_xmp=b"", gap=b"\0" * 7, gain_is_hdr=b"False"):
    """Build an explicitly typed three-JPEG MPF using only public fixture pixels."""
    source = FIXTURES / IMAGES[2][0]
    pixels = image_payload(source.read_bytes())

    def app(marker, payload):
        return bytes([0xff, marker]) + struct.pack(">H", len(payload) + 2) + payload

    def xmp(attributes):
        return app(0xe1, b'http://ns.adobe.com/xap/1.0/\0'
                   b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
                   b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                   b'<rdf:Description xmlns:g="http://ns.adobe.com/hdr-gain-map/1.0/" '
                   b'g:Version="1.0" ' + attributes + b'/></rdf:RDF></x:xmpmeta>')

    exif = app(0xe1, b"Exif\0\0" + binary_metadata(source, "EXIF"))
    icc = app(0xe2, b"ICC_PROFILE\0\1\1" + binary_metadata(source, "ICC_Profile"))
    iso = app(0xe2, b"urn:iso:std:iso:ts:21496:-1\0" + bytes.fromhex("0000000040")
              + struct.pack(">14I", 0, 1, 1, 1, 0, 1, 1, 1, 1, 1, 0, 1, 0, 1))
    preview = pixels[:2] + exif + pixels[2:]
    gain = pixels[:2] + iso + xmp(b'g:BaseRenditionIsHDR="' + gain_is_hdr + b'"') + pixels[2:]
    before = pixels[:2] + exif
    after = icc + pixels[2:]
    if endian == "little":
        before += xmp(extra_xmp)
        after = iso + after
    else:
        after = xmp(extra_xmp) + iso + after

    # CIPA MP Index IFD: three standard fields, no next IFD; entries at TIFF + 50.
    order = "<" if endian == "little" else ">"
    header = (b"II" if endian == "little" else b"MM") + struct.pack(order + "HIH", 42, 8, 3)
    header += struct.pack(order + "HHI4s", 0xb000, 7, 4, b"0100")
    header += struct.pack(order + "HHII", 0xb001, 4, 1, 3)
    header += struct.pack(order + "HHIII", 0xb002, 7, 48, 50, 0)
    origin = len(before) + 8
    primary_size = origin + len(header) + 48 + len(after)
    preview_offset = primary_size + 2 - origin
    entries = [(0x30000, primary_size, 0), (0x10002, len(preview), preview_offset),
               (0x50000, len(gain), preview_offset + len(preview) + len(gap))]
    table = b"".join(struct.pack(order + "IIIHH", *entry, 0, 0) for entry in entries)
    main = before + app(0xe2, b"MPF\0" + header + table) + after
    return main + b"\0\0" + preview + gap + gain, preview


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
    def test_menu_result_includes_counts_and_reasons(self):
        good = self.fixture("一.jpg")
        skipped = self.directory / "普通.jpg"
        # Use a real SDR copy with the old MPF index removed.
        self.assertEqual(clear_hdr.run([str(good)])["processed"], [str(good)])
        skipped.write_bytes(good.read_bytes())
        good = self.fixture("二.jpg")
        bad = self.directory / "损坏.jpg"
        bad.write_bytes(b"\xff\xd8broken")
        for paths, code, counts in (
            ([good], 0, "成功 1 个，跳过 0 个，失败 0 个"),
            ([good, skipped], 0, "成功 0 个，跳过 2 个，失败 0 个"),
            ([self.fixture("三.jpg"), skipped, bad], 1, "成功 1 个，跳过 1 个，失败 1 个"),
            ([], 0, "成功 0 个，跳过 0 个，失败 0 个"),
        ):
            with self.subTest(counts=counts):
                result = subprocess.run([sys.executable, str(ENGINE), "--stdin-paths"],
                                        input=json.dumps(list(map(str, paths))).encode(),
                                        capture_output=True, timeout=60)
                self.assertEqual(result.returncode, code)
                self.assertEqual(result.stderr, b"")
                report = json.loads(result.stdout)
                self.assertIn(counts, report["message"])
                self.assertTrue(report["title"])
                if report["skipped"]:
                    self.assertIn(report["skipped"][0][1], report["message"])
                if code:
                    self.assertIn(bad.name, report["message"])
                    self.assertIn(report["failed"][0][1], report["message"])

    def test_cli_progress_counts_each_outcome_once(self):
        photo = self.fixture("good.jpg")
        self.fixture("skip.jpg", image_type=None)
        (self.directory / "broken.jpg").write_bytes(b"\xff\xd8broken")
        empty = self.directory / "empty"
        empty.mkdir()
        for selection, counts in (([self.directory, photo, self.directory], (1, 1, 1)),
                                  ([empty], (0, 0, 0)), ({"bad": True}, (0, 0, 1))):
            with self.subTest(counts=counts):
                data = [str(path) for path in selection] if isinstance(selection, list) else selection
                result = subprocess.run([sys.executable, str(ENGINE), "--stdin-paths", "--progress", "--backup"],
                                        input=json.dumps(data).encode(), capture_output=True, timeout=60)
                report = json.loads(result.stdout)
                self.assertEqual(tuple(len(report[k]) for k in ("processed", "skipped", "failed")), counts)
                self.assertEqual(result.returncode, int(counts[2] > 0))
                total = sum(counts)
                self.assertEqual(result.stderr.decode("ascii").splitlines(),
                                 [f"PROGRESS {done} {total}" for done in range(total + 1)])

    def test_progress_is_flushed_between_files(self):
        photos = [self.fixture("first.jpg"), self.fixture("second.jpg")]
        buffer = io.BytesIO()
        def read_metadata(path):
            done = photos.index(path)
            self.assertEqual(buffer.getvalue().splitlines()[-1], f"PROGRESS {done} 2".encode())
            return {"File:FileType": "JPEG"}
        with io.TextIOWrapper(buffer, encoding="ascii") as stream, mock.patch.object(sys, "stderr", stream), \
                mock.patch.object(clear_hdr, "metadata", side_effect=read_metadata):
            result = clear_hdr.run(photos, progress=clear_hdr.emit_progress)
            self.assertEqual(len(result["skipped"]), 2)
            self.assertEqual(buffer.getvalue().splitlines()[-1], b"PROGRESS 2 2")

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


    def test_three_images_preserve_primary_preview_and_file_metadata(self):
        for order, backup in (("little", False), ("big", True)):
            with self.subTest(order=order):
                original, preview = preview_fixture(order)
                path = self.directory / (order + ".jpg")
                path.write_bytes(original)
                set_xattr(path, "com.mimi.clear-hdr-test", b"retain")
                os.utime(path, ns=(1690000000123456789,) * 2)
                before = path.stat()
                exif = binary_metadata(path, "EXIF")
                icc = binary_metadata(path, "ICC_Profile")
                self.assertTrue(exif and icc)

                self.assertEqual(clear_hdr.run([str(path)], backup=backup),
                                 {"processed": [str(path)], "skipped": [], "failed": []})
                result = path.read_bytes()
                record = clear_hdr.metadata(path)
                end = len(result) - len(preview)
                self.assertEqual(result[end:], preview)
                self.assertEqual(result[end-2:end], b"\xff\xd9")
                self.assertEqual(clear_hdr.values(record, "MPImageType"), [0x30000, 0x10002])
                self.assertEqual(clear_hdr.values(record, "MPImageStart"), [0, end])
                self.assertEqual(clear_hdr.values(record, "MPImageLength"), [end, len(preview)])
                self.assertEqual(image_payload(result), image_payload(original))
                self.assertNotIn(b"hdr-gain-map", result)
                self.assertNotIn(b"21496", result)
                self.assertEqual(binary_metadata(path, "EXIF"), exif)
                self.assertEqual(binary_metadata(path, "ICC_Profile"), icc)
                self.assertEqual(xattrs(path)["com.mimi.clear-hdr-test"], b"retain")
                self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
                self.assertEqual(path.stat().st_birthtime, before.st_birthtime)
                saved = Path(str(path) + "_original")
                self.assertEqual(saved.exists(), backup)
                if backup:
                    self.assertEqual(saved.read_bytes(), original)
                self.assertEqual(len(clear_hdr.run([str(path)])["skipped"]), 1)
                self.assertEqual(path.read_bytes(), result)

    def test_three_images_skip_ambiguous_content_without_writes(self):
        for label, options, trailer in (
            ("caption", {"extra_xmp": b'xmlns:d="http://purl.org/dc/elements/1.1/" d:title="keep"'}, b""),
            ("unknown gap", {"gap": b"KEEP"}, b""),
            ("trailer", {}, b"unindexed data"),
            ("HDR base", {"gain_is_hdr": b"True"}, b""),
        ):
            with self.subTest(label=label):
                photo, _ = preview_fixture(**options)
                original = photo + trailer
                path = self.directory / (label + ".jpg")
                path.write_bytes(original)
                report = clear_hdr.run([str(path)], backup=True)
                self.assertEqual(report["failed"], [])
                self.assertEqual([x[0] for x in report["skipped"]], [str(path)])
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse(Path(str(path) + "_original").exists())


if __name__ == "__main__":
    unittest.main()
