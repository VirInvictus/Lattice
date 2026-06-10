import os
import struct
import tempfile
import unittest

from lattice.modes.artwork import _get_image_size, run_art_quality_audit


def _png(width, height):
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">L", 13)
        + b"IHDR"
        + struct.pack(">LL", width, height)
    )


def _jpeg(width, height, pad=0):
    # SOI, an optional APP1 padding segment (stands in for a large EXIF/ICC
    # block pushing SOF deep into the file), then a SOF0 (baseline) segment:
    # marker, length, precision, height, width — the shape _get_image_size
    # walks looking for SOFn.
    app1 = b""
    if pad:
        app1 = b"\xff\xe1" + struct.pack(">H", pad + 2) + b"\x00" * pad
    return (
        b"\xff\xd8"
        + app1
        + b"\xff\xc0"
        + struct.pack(">H", 17)
        + b"\x08"
        + struct.pack(">H", height)
        + struct.pack(">H", width)
    )


class GetImageSizeTests(unittest.TestCase):
    def test_png_dimensions(self):
        self.assertEqual(_get_image_size(_png(800, 600)), (800, 600))

    def test_jpeg_dimensions(self):
        self.assertEqual(_get_image_size(_jpeg(800, 600)), (800, 600))

    def test_jpeg_sof_after_large_app1_segment(self):
        self.assertEqual(_get_image_size(_jpeg(800, 600, pad=10 * 1024)), (800, 600))

    def test_non_image_returns_none(self):
        self.assertIsNone(_get_image_size(b"not an image at all"))

    def test_too_short_returns_none(self):
        self.assertIsNone(_get_image_size(b"\x89PNG"))


class ArtQualityAuditTests(unittest.TestCase):
    def test_low_res_cover_with_deep_sof_is_flagged(self):
        # Regression: the folder-art read was capped at 8 KB, so a cover whose
        # SOF sat past a big APP1 block parsed as None and was never flagged.
        with tempfile.TemporaryDirectory() as tmp:
            album = os.path.join(tmp, "Artist", "Album")
            os.makedirs(album)
            open(os.path.join(album, "track.mp3"), "wb").close()
            with open(os.path.join(album, "cover.jpg"), "wb") as f:
                f.write(_jpeg(300, 300, pad=10 * 1024))
            report = os.path.join(tmp, "report.txt")
            rc = run_art_quality_audit(tmp, report, 500, quiet=True)
            self.assertEqual(rc, 0)
            with open(report, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("Below floor: 1", text)
            self.assertIn("300x300", text)


if __name__ == "__main__":
    unittest.main()
