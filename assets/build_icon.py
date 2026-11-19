"""Regenerate the macOS icon from the editable SVG using Apple's image tools."""

from pathlib import Path
import subprocess
import tempfile
from xml.etree import ElementTree


# Paint the macOS visible footprint on whole pixels at each output size.
VISIBLE_WIDTH = {16: 14, 32: 26, 64: 52, 128: 102, 256: 206, 512: 410, 1024: 820}


def build_icon():
    assets = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="clear-jpg-hdr-icon-") as temporary:
        work = Path(temporary)
        iconset = work / "AppIcon.iconset"
        iconset.mkdir()
        ElementTree.register_namespace("", "http://www.w3.org/2000/svg")
        drawing = ElementTree.parse(assets / "AppIcon.svg")
        root = drawing.getroot()
        frame = work / "frame.svg"
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                pixels = size * scale
                side = 820 * pixels / VISIBLE_WIDTH[pixels]
                inset = (1024 - side) / 2
                root.set("width", str(pixels))
                root.set("height", str(pixels))
                root.set("viewBox", f"{inset} {inset} {side} {side}")
                drawing.write(frame, encoding="utf-8")
                output = iconset / f"icon_{size}x{size}{suffix}.png"
                subprocess.run(["/usr/bin/sips", "-s", "format", "png", str(frame),
                                "--out", str(output)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["/usr/bin/iconutil", "-c", "icns", str(iconset),
                        "-o", str(assets / "AppIcon.icns")], check=True)


if __name__ == "__main__":
    build_icon()
