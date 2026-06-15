import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

# apestrip.py lives in scripts/ (outside the lattice package). It writes tags, so
# it is exercised against a copy of the committed MP3 fixture, seeded with an
# APEv2 tag. The fixture's ID3 has TIT2/TPE1/TALB/TCON(Rock)/TRCK and no date,
# which lets us cover redundant fields, sole-source migration, and the genre/
# rating policy in one place.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import apestrip  # noqa: E402
from mutagen.apev2 import BINARY, TEXT, APENoHeaderError, APEv2, APEValue  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402
from mutagen.mp3 import MP3  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "library"
MP3_SRC = FIXTURES / "Cursive" / "Domestica" / "01 - The Casualty.mp3"

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16 + b"\xff\xd9"


def _build_malformed_ape(items, binary_keys=()):
    """Hand-build an APE tag whose footer has the IS_HEADER bit wrongly set,
    followed by junk bytes — the structure mutagen refuses to parse but
    _parse_raw_ape can recover (matches the real-world Lil Wayne rips)."""
    body = b""
    for k, v in items.items():
        kind = 1 if k in binary_keys else 0
        body += struct.pack("<II", len(v), kind << 1) + k.encode("latin1") + b"\x00" + v
    size = len(body) + 32
    header = (
        b"APETAGEX"
        + struct.pack("<IIII", 2000, size, len(items), 0xA0000000)
        + b"\x00" * 8
    )
    footer = (
        b"APETAGEX"
        + struct.pack("<IIII", 2000, size, len(items), 0x80000000)
        + b"\x00" * 8
    )
    junk = b"\xde\xad\xbe\xef\xba\x4c\xa3\xad\x73\x9e\x09\x00"
    return header + body + footer + junk + b"TAG" + b"\x00" * 125


def _write_malformed(path):
    base = Path(MP3_SRC).read_bytes()
    blob = _build_malformed_ape(
        {"Title": b"The Casualty", "GENRE": b"Rap", "YEAR": b"2011"}
    )
    Path(path).write_bytes(base + blob)


class ClassifyTests(unittest.TestCase):
    def test_core_text_field(self):
        action, payload = apestrip.classify_ape_field("Year")
        self.assertEqual(action, "frame")
        self.assertEqual(payload.__name__, "TDRC")

    def test_sort_order(self):
        action, payload = apestrip.classify_ape_field("Albumartistsortorder")
        self.assertEqual(action, "frame")
        self.assertEqual(payload.__name__, "TSO2")

    def test_genre_is_skip(self):
        self.assertEqual(apestrip.classify_ape_field("Genre"), ("genre", None))

    def test_rating_is_report(self):
        self.assertEqual(apestrip.classify_ape_field("Rating"), ("rating", None))

    def test_cover_and_lyrics(self):
        self.assertEqual(apestrip.classify_ape_field("Cover Art (Front)")[0], "cover")
        self.assertEqual(apestrip.classify_ape_field("Unsynced lyrics")[0], "lyrics")

    def test_comment(self):
        self.assertEqual(apestrip.classify_ape_field("Comment"), ("comment", None))

    def test_unknown_passthrough(self):
        action, payload = apestrip.classify_ape_field("Barcode")
        self.assertEqual(action, "txxx")
        self.assertEqual(payload, "Barcode")


class ApeStripTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "track.mp3")
        shutil.copy(MP3_SRC, self.path)
        ape = APEv2()
        ape["Year"] = APEValue("1986", TEXT)  # sole-source -> migrate
        ape["Title"] = APEValue("The Casualty", TEXT)  # redundant with ID3
        ape["Genre"] = APEValue("Trash Metal", TEXT)  # stray -> never migrate
        ape["Comment"] = APEValue("from the rip", TEXT)  # sole-source -> COMM
        ape["Barcode"] = APEValue("075992413114", TEXT)  # unknown -> TXXX
        ape["Rating"] = APEValue("4", TEXT)  # report only
        ape["Unsynced lyrics"] = APEValue("la la la", TEXT)  # -> USLT
        ape["Cover Art (Front)"] = APEValue(b"cover.jpg\x00" + JPEG, BINARY)  # -> APIC
        ape.save(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self):
        return apestrip.process_file(self.path, dry_run=False)

    def test_ape_tag_removed(self):
        r = self._run()
        self.assertTrue(r.stripped)
        self.assertIsNone(r.error)
        with self.assertRaises(APENoHeaderError):
            APEv2(self.path)

    def test_genre_not_migrated(self):
        self._run()
        id3 = ID3(self.path)
        self.assertEqual(id3["TCON"].text, ["Rock"])  # APE "Trash Metal" ignored

    def test_sole_source_year_preserved(self):
        self._run()
        id3 = ID3(self.path)
        frame = id3.get("TDRC") or id3.get("TYER")
        self.assertIsNotNone(frame)
        self.assertIn("1986", str(frame))

    def test_redundant_title_not_duplicated(self):
        self._run()
        id3 = ID3(self.path)
        self.assertEqual(id3["TIT2"].text, ["The Casualty"])

    def test_unknown_field_to_txxx(self):
        self._run()
        id3 = ID3(self.path)
        self.assertIn("TXXX:Barcode", id3)
        self.assertEqual(id3["TXXX:Barcode"].text, ["075992413114"])

    def test_comment_to_comm(self):
        self._run()
        id3 = ID3(self.path)
        comms = [id3[k] for k in id3 if k.startswith("COMM")]
        self.assertTrue(any("from the rip" in str(c.text[0]) for c in comms))

    def test_cover_to_apic(self):
        self._run()
        id3 = ID3(self.path)
        apics = [id3[k] for k in id3 if k.startswith("APIC")]
        self.assertEqual(len(apics), 1)
        self.assertEqual(apics[0].data, JPEG)
        self.assertEqual(apics[0].mime, "image/jpeg")

    def test_lyrics_to_uslt(self):
        self._run()
        id3 = ID3(self.path)
        uslts = [id3[k] for k in id3 if k.startswith("USLT")]
        self.assertTrue(any("la la la" in str(u.text) for u in uslts))

    def test_rating_reported_not_written(self):
        r = self._run()
        self.assertEqual(r.ratings, ["4"])
        id3 = ID3(self.path)
        self.assertNotIn("TXXX:Rating", id3)
        # No new POPM beyond the fixture's existing one.
        self.assertEqual(len([k for k in id3 if k.startswith("POPM")]), 1)

    def test_idempotent(self):
        self._run()
        second = apestrip.process_file(self.path, dry_run=False)
        self.assertFalse(second.had_ape)
        self.assertFalse(second.stripped)

    def test_dry_run_writes_nothing(self):
        r = apestrip.process_file(self.path, dry_run=True)
        self.assertTrue(r.had_ape)
        self.assertTrue(any(k == "Year" for k, _ in r.migrated))
        # APE tag and original ID3 genre are untouched.
        self.assertIsNotNone(APEv2(self.path))
        self.assertEqual(ID3(self.path)["TCON"].text, ["Rock"])


class PlanGuardTests(unittest.TestCase):
    def test_file_without_ape_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "clean.mp3")
            shutil.copy(MP3_SRC, p)
            r = apestrip.process_file(p, dry_run=False)
            self.assertFalse(r.had_ape)
            self.assertFalse(r.stripped)

    def test_raw_signature_detection(self):
        with tempfile.TemporaryDirectory() as d:
            clean = os.path.join(d, "clean.mp3")
            shutil.copy(MP3_SRC, clean)
            self.assertFalse(apestrip._has_raw_ape_signature(clean))
            tagged = os.path.join(d, "tagged.mp3")
            shutil.copy(MP3_SRC, tagged)
            ape = APEv2()
            ape["Genre"] = APEValue("Rap", TEXT)
            ape.save(tagged)
            self.assertTrue(apestrip._has_raw_ape_signature(tagged))

    def test_malformed_ape_is_reported_not_skipped(self):
        # An APETAGEX signature that mutagen cannot parse must be reported with
        # an error and left untouched, never silently treated as "no APE tag".
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "track.mp3")
            shutil.copy(MP3_SRC, p)
            ape = APEv2()
            ape["Genre"] = APEValue("Rap", TEXT)
            ape.save(p)
            data = bytearray(Path(p).read_bytes())
            fidx = data.rfind(b"APETAGEX")
            struct.pack_into("<I", data, fidx + 12, 8)  # bogus tag size
            Path(p).write_bytes(data)
            with self.assertRaises(Exception):
                APEv2(p)  # confirm mutagen now chokes on it
            r = apestrip.process_file(p, dry_run=False)
            self.assertTrue(r.had_ape)
            self.assertFalse(r.stripped)
            self.assertIsNotNone(r.error)

    def test_genre_warning_when_id3_has_no_genre(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "track.mp3")
            shutil.copy(MP3_SRC, p)
            id3 = ID3(p)
            id3.delall("TCON")
            id3.save(p)
            ape = APEv2()
            ape["Genre"] = APEValue("Trash Metal", TEXT)
            ape.save(p)
            r = apestrip.process_file(p, dry_run=True)
            self.assertTrue(r.genre_warning)


class RepairMalformedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._tmp.name, "track.mp3")
        _write_malformed(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_mutagen_cannot_parse_but_raw_parse_can(self):
        with self.assertRaises(APENoHeaderError):
            APEv2(self.path)
        data = Path(self.path).read_bytes()
        parsed = apestrip._parse_raw_ape(data)
        self.assertIsNotNone(parsed)
        _, _, items = parsed
        self.assertIn("YEAR", items)
        self.assertIn("GENRE", items)

    def test_repair_off_by_default_reports(self):
        r = apestrip.process_file(self.path, dry_run=False, repair=False)
        self.assertTrue(r.had_ape)
        self.assertFalse(r.repaired)
        self.assertIn("repair-malformed", r.error)

    def test_repair_excises_and_migrates_sole_source(self):
        r = apestrip.process_file(self.path, dry_run=False, repair=True)
        self.assertTrue(r.repaired)
        self.assertTrue(r.stripped)
        self.assertIsNone(r.error)
        self.assertFalse(apestrip._has_raw_ape_signature(self.path))
        with self.assertRaises(APENoHeaderError):
            APEv2(self.path)
        id3 = ID3(self.path)
        # sole-source YEAR migrated; stray GENRE never migrated (ID3 stays Rock)
        self.assertIn("2011", str(id3.get("TDRC") or id3.get("TYER")))
        self.assertEqual(id3["TCON"].text, ["Rock"])
        # audio still decodes
        self.assertGreater(getattr(MP3(self.path).info, "length", 0), 0)

    def test_repair_dry_run_writes_nothing(self):
        before = Path(self.path).read_bytes()
        r = apestrip.process_file(self.path, dry_run=True, repair=True)
        self.assertTrue(r.malformed)
        self.assertTrue(any(k == "YEAR" for k, _ in r.migrated))
        self.assertEqual(Path(self.path).read_bytes(), before)

    def test_parse_raw_ape_rejects_inconsistent_structure(self):
        # Corrupt the header size so the footer no longer sits at header+size:
        # the parser must refuse rather than excise a guessed region.
        data = bytearray(Path(self.path).read_bytes())
        hidx = data.find(b"APETAGEX")
        struct.pack_into("<I", data, hidx + 12, 999999)
        self.assertIsNone(apestrip._parse_raw_ape(bytes(data)))


if __name__ == "__main__":
    unittest.main()
