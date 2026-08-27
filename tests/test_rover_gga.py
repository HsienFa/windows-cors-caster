"""Pure parser regressions for bounded rover NMEA GGA input."""

import unittest

from src.rover_gga import MAX_ACCUMULATOR_SIZE, RoverGGAAccumulator


def nmea_sentence(payload):
    checksum = 0
    for character in payload.encode("ascii"):
        checksum ^= character
    return f"${payload}*{checksum:02X}\r\n".encode("ascii")


class RoverGGAParserTests(unittest.TestCase):
    def test_gpgga_rtk_fixed_fields(self):
        accumulator = RoverGGAAccumulator()

        parsed = accumulator.feed(
            nmea_sentence(
                "GPGGA,123519,2503.7136,N,12138.7452,E,4,20,0.6,50.2,M,0.0,M,,"
            )
        )

        self.assertEqual(len(parsed), 1)
        message = parsed[0]
        self.assertAlmostEqual(message.latitude, 25.0618933333, places=8)
        self.assertAlmostEqual(message.longitude, 121.6457533333, places=8)
        self.assertEqual(message.gga_fix_quality, 4)
        self.assertEqual(message.satellites, 20)
        self.assertEqual(message.hdop, 0.6)
        self.assertEqual(message.altitude, 50.2)
        self.assertTrue(message.has_valid_position)

    def test_gngga_float_south_and_west(self):
        accumulator = RoverGGAAccumulator()

        parsed = accumulator.feed(
            nmea_sentence(
                "GNGGA,225444,3450.0000,S,05823.0000,W,5,18,0.8,12.3,M,0.0,M,,"
            )
        )

        self.assertEqual(len(parsed), 1)
        message = parsed[0]
        self.assertAlmostEqual(message.latitude, -34.8333333333, places=8)
        self.assertAlmostEqual(message.longitude, -58.3833333333, places=8)
        self.assertEqual(message.gga_fix_quality, 5)
        self.assertTrue(message.has_valid_position)

    def test_quality_zero_is_not_a_valid_position(self):
        parsed = RoverGGAAccumulator().feed(
            nmea_sentence(
                "GPGGA,123519,2503.7136,N,12138.7452,E,0,00,9.9,50.2,M,0.0,M,,"
            )
        )

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].gga_fix_quality, 0)
        self.assertFalse(parsed[0].has_valid_position)

    def test_fragmented_sentence_is_buffered_until_complete(self):
        accumulator = RoverGGAAccumulator()
        sentence = nmea_sentence(
            "GPGGA,123519,2503.7136,N,12138.7452,E,4,20,0.6,50.2,M,0.0,M,,"
        )

        self.assertEqual(accumulator.feed(sentence[:17]), [])
        self.assertEqual(accumulator.feed(sentence[17:53]), [])
        parsed = accumulator.feed(sentence[53:])

        self.assertEqual(len(parsed), 1)
        self.assertEqual(accumulator.buffered_bytes, 0)

    def test_multiple_sentences_and_other_legal_talker(self):
        accumulator = RoverGGAAccumulator()
        chunk = b"".join(
            (
                nmea_sentence("GPRMC,123519,A,2503.7136,N,12138.7452,E,0.0,0.0,230394,,"),
                nmea_sentence("GLGGA,123520,2503.7136,N,12138.7452,E,1,12,1.0,50.0,M,0.0,M,,"),
                nmea_sentence("GNGGA,123521,2503.7137,N,12138.7453,E,5,18,0.7,50.1,M,0.0,M,,"),
            )
        )

        parsed = accumulator.feed(chunk)

        self.assertEqual(len(parsed), 2)
        self.assertEqual(
            [message.gga_fix_quality for message in parsed],
            [1, 5],
        )

    def test_checksum_error_and_incomplete_sentence_are_discarded(self):
        accumulator = RoverGGAAccumulator()
        valid = nmea_sentence(
            "GPGGA,123519,2503.7136,N,12138.7452,E,4,20,0.6,50.2,M,0.0,M,,"
        )
        invalid_checksum = valid[:-4] + (
            b"00" if valid[-4:-2] != b"00" else b"FF"
        ) + b"\r\n"
        incomplete = b"$GPGGA,123519,2503.7136,N"

        parsed = accumulator.feed(invalid_checksum + valid)

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].gga_fix_quality, 4)
        self.assertEqual(accumulator.feed(incomplete), [])
        self.assertGreater(accumulator.buffered_bytes, 0)

    def test_oversized_garbage_is_bounded_and_future_gga_still_parses(self):
        accumulator = RoverGGAAccumulator()

        self.assertEqual(accumulator.feed(b"x" * (MAX_ACCUMULATOR_SIZE + 2048)), [])
        self.assertLessEqual(accumulator.buffered_bytes, MAX_ACCUMULATOR_SIZE)
        parsed = accumulator.feed(
            nmea_sentence(
                "GPGGA,123519,2503.7136,N,12138.7452,E,4,20,0.6,50.2,M,0.0,M,,"
            )
        )

        self.assertEqual(len(parsed), 1)


if __name__ == "__main__":
    unittest.main()
