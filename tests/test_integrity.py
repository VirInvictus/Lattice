import unittest

from lattice.modes.integrity import (
    classify_decode,
    TIER_OK,
    TIER_METADATA,
    TIER_SUSPECT,
    TIER_CORRUPT,
)

# Real stderr signatures captured from ffmpeg / libFLAC during the audit.
_CLAVISH = (
    "Error submitting packet to decoder: Invalid data found when processing input\n"
    "[mp3float] Header missing\n"
    "Error submitting packet to decoder: Invalid data found when processing input"
)
_BOM_NOISE = (
    "Incorrect BOM value: 0x3500\n"
    "Error reading frame artists, skipped\n"
    "Incorrect BOM value: 0x3500\n"
    "Error reading frame PERFORMER_SORT_ORDER, skipped"
)
_FLAC_TRAILING = (
    "*** Got error code 0:FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC "
    "after processing 2527850 samples"
)
_FLAC_TRUNCATED = (
    "*** Got error code 0:FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC "
    "after processing 1589248 samples"
)
_FLAC_TOTAL = 2527850


def _tier(*args, **kwargs):
    return classify_decode(*args, **kwargs)[0]


class ClassifyDecodeTests(unittest.TestCase):
    def test_clean_is_ok(self):
        self.assertEqual(_tier(0, ""), TIER_OK)

    def test_pure_tag_noise_is_metadata(self):
        self.assertEqual(_tier(0, _BOM_NOISE), TIER_METADATA)

    def test_lone_header_missing_is_metadata(self):
        self.assertEqual(_tier(0, "[mp3float] Header missing"), TIER_METADATA)

    def test_lone_backstep_is_metadata(self):
        self.assertEqual(_tier(0, "[mp3float] invalid new backstep -1"), TIER_METADATA)

    def test_completed_decode_with_faults_is_suspect(self):
        # rc == 0: decoded to the end despite 'Invalid data' lines (it plays).
        self.assertEqual(_tier(0, _CLAVISH), TIER_SUSPECT)

    def test_single_decode_fault_is_suspect(self):
        self.assertEqual(
            _tier(0, "Error submitting packet to decoder: Invalid data found"),
            TIER_SUSPECT,
        )

    def test_unknown_line_is_suspect_not_hidden(self):
        self.assertEqual(_tier(0, "some unrecognized decoder whining"), TIER_SUSPECT)

    def test_mixed_metadata_and_decode_is_suspect(self):
        self.assertEqual(
            _tier(
                0, "Incorrect BOM value: 0x10\nError submitting packet: Invalid data"
            ),
            TIER_SUSPECT,
        )

    def test_cannot_open_is_corrupt(self):
        self.assertEqual(
            _tier(1, "Error opening input: Invalid data found when processing input"),
            TIER_CORRUPT,
        )

    def test_nonzero_exit_no_stderr_is_corrupt(self):
        self.assertEqual(_tier(1, ""), TIER_CORRUPT)

    def test_flac_trailing_is_suspect(self):
        self.assertEqual(_tier(1, _FLAC_TRAILING, _FLAC_TOTAL), TIER_SUSPECT)

    def test_flac_truncated_is_corrupt(self):
        self.assertEqual(_tier(1, _FLAC_TRUNCATED, _FLAC_TOTAL), TIER_CORRUPT)

    def test_flac_lostsync_without_declared_count_is_suspect(self):
        # Can't prove truncation without the declared total, so do not escalate.
        self.assertEqual(_tier(1, _FLAC_TRAILING), TIER_SUSPECT)

    def test_reason_is_returned(self):
        tier, reason = classify_decode(1, _FLAC_TRUNCATED, _FLAC_TOTAL)
        self.assertEqual(tier, TIER_CORRUPT)
        self.assertIn("1589248", reason)


if __name__ == "__main__":
    unittest.main()
