import struct
import unittest

from lattice.modes.artwork import _get_image_size


def _png(width, height):
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">L", 13)
        + b"IHDR"
        + struct.pack(">LL", width, height)
    )


def _jpeg(width, height):
    # SOI, then a SOF0 (baseline) segment: marker, length, precision,
    # height, width — the shape _get_image_size walks looking for SOFn.
    return (
        b"\xff\xd8"
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

    def test_non_image_returns_none(self):
        self.assertIsNone(_get_image_size(b"not an image at all"))

    def test_too_short_returns_none(self):
        self.assertIsNone(_get_image_size(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
