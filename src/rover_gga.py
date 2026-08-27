"""Bounded NMEA GGA parsing for NTRIP rover input."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from numbers import Real
from typing import Optional

from pynmeagps import (
    NMEAMessageError,
    NMEA_TALKERS,
    NMEAParseError,
    NMEAReader,
    NMEAStreamError,
    NMEATypeError,
    VALCKSUM,
)


MAX_ACCUMULATOR_SIZE = 8 * 1024
MAX_SENTENCE_SIZE = 1024
_CHECKSUM_SUFFIX = re.compile(br"\*[0-9A-Fa-f]{2}$")


@dataclass(frozen=True)
class RoverGGA:
    latitude: Optional[float]
    longitude: Optional[float]
    gga_fix_quality: Optional[int]
    satellites: Optional[int]
    hdop: Optional[float]
    altitude: Optional[float]
    has_valid_position: bool

    def to_dict(self):
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "gga_fix_quality": self.gga_fix_quality,
            "satellites": self.satellites,
            "hdop": self.hdop,
            "altitude": self.altitude,
            "has_valid_position": self.has_valid_position,
        }


def _optional_number(value, converter):
    if value in (None, ""):
        return None
    try:
        converted = converter(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if isinstance(converted, Real) and not math.isfinite(converted):
        return None
    return converted


def parse_gga_sentence(sentence: bytes) -> Optional[RoverGGA]:
    """Parse one complete, checksummed GGA sentence without leaking errors."""
    candidate = sentence.strip()
    if (
        len(candidate) < 7
        or len(candidate) > MAX_SENTENCE_SIZE
        or not candidate.startswith(b"$")
        or candidate[3:6] != b"GGA"
        or candidate[6:7] != b","
        or _CHECKSUM_SUFFIX.search(candidate) is None
    ):
        return None

    try:
        message = NMEAReader.parse(candidate + b"\r\n", validate=VALCKSUM)
    except (
        NMEAMessageError,
        NMEAParseError,
        NMEAStreamError,
        NMEATypeError,
        ValueError,
        TypeError,
    ):
        return None

    if (
        message is None
        or getattr(message, "msgID", None) != "GGA"
        or getattr(message, "talker", None) not in NMEA_TALKERS
    ):
        return None

    latitude = _optional_number(getattr(message, "lat", None), float)
    longitude = _optional_number(getattr(message, "lon", None), float)
    quality = _optional_number(getattr(message, "quality", None), int)
    satellites = _optional_number(getattr(message, "numSV", None), int)
    hdop = _optional_number(getattr(message, "HDOP", None), float)
    altitude = _optional_number(getattr(message, "alt", None), float)
    has_valid_position = bool(
        quality is not None
        and quality > 0
        and latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )

    return RoverGGA(
        latitude=latitude,
        longitude=longitude,
        gga_fix_quality=quality,
        satellites=satellites,
        hdop=hdop,
        altitude=altitude,
        has_valid_position=has_valid_position,
    )


class RoverGGAAccumulator:
    """Accumulate fragmented rover input and return complete valid GGA data."""

    def __init__(self, max_size=MAX_ACCUMULATOR_SIZE):
        self.max_size = max_size
        self._buffer = bytearray()

    @property
    def buffered_bytes(self):
        return len(self._buffer)

    def feed(self, data: bytes):
        if not data:
            return []
        if not isinstance(data, (bytes, bytearray, memoryview)):
            return []

        self._buffer.extend(bytes(data))
        parsed_messages = []

        while True:
            newline_index = self._buffer.find(b"\n")
            if newline_index < 0:
                break
            raw_line = bytes(self._buffer[:newline_index]).rstrip(b"\r")
            del self._buffer[: newline_index + 1]

            sentence_start = raw_line.rfind(b"$")
            if sentence_start < 0:
                continue
            parsed = parse_gga_sentence(raw_line[sentence_start:])
            if parsed is not None:
                parsed_messages.append(parsed)

        self._bound_incomplete_data()
        return parsed_messages

    def _bound_incomplete_data(self):
        if len(self._buffer) <= self.max_size:
            return

        retained = bytes(self._buffer[-self.max_size :])
        sentence_start = retained.rfind(b"$")
        if sentence_start < 0:
            self._buffer.clear()
            return
        self._buffer = bytearray(retained[sentence_start:])
