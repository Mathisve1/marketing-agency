"""Phase 1I — `mp4_meta.probe_mp4` regression tests.

The Phase 1H probe was ffprobe-only; the Phase 1I fallback walks ISO
BMFF atoms with the stdlib alone. These tests pin the contract by
synthesising small valid mp4 byte streams + (when available) running
the parser against the real Pai 720p run produced by Phase 1G.

No external dependencies. Skips gracefully when the real artefact
isn't on disk (e.g. fresh clone before any submit).
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from agents.producer.dashboard.mp4_meta import (
    Mp4Meta,
    _atom_probe,
    meta_to_event_payload,
    probe_mp4,
)

# ---------------------------------------------------------------------------
# Helpers — minimal hand-rolled MP4 atoms for unit tests.
# ---------------------------------------------------------------------------


def _atom(atype: bytes, body: bytes) -> bytes:
    """Build a top-level atom: [size BE u32][type][body]."""
    assert len(atype) == 4
    size = 8 + len(body)
    return struct.pack(">I", size) + atype + body


def _mvhd_v0(timescale: int, duration_ticks: int) -> bytes:
    """version-0 mvhd payload (after the size+type header):
      [v 1][flags 3][ctime 4][mtime 4][timescale 4][duration 4]
      [rate 4][volume 2][reserved 10][matrix 36][predef 24][next_track_id 4]
    Total body = 100 bytes.
    """
    body = (
        b"\x00\x00\x00\x00"  # version 0 + flags
        b"\x00\x00\x00\x00"  # ctime
        b"\x00\x00\x00\x00"  # mtime
        + struct.pack(">I", timescale)
        + struct.pack(">I", duration_ticks)
        + b"\x00" * (4 + 2 + 10 + 36 + 24 + 4)
    )
    return _atom(b"mvhd", body)


def _tkhd_v0(width: int, height: int) -> bytes:
    """version-0 tkhd payload. The dimensions we care about live in the
    last 8 bytes (16.16 fixed-point). The leading fixed-shape padding
    follows the spec but the parser only reads the trailing 8 bytes.
    Total body = 84 bytes."""
    body = (
        b"\x00\x00\x00\x07"  # version 0 + flags (track enabled+in-movie+in-preview)
        b"\x00\x00\x00\x00"  # ctime
        b"\x00\x00\x00\x00"  # mtime
        b"\x00\x00\x00\x01"  # track_id
        b"\x00\x00\x00\x00"  # reserved
        b"\x00\x00\x00\x00"  # duration
        b"\x00\x00\x00\x00\x00\x00\x00\x00"  # reserved
        b"\x00\x00"  # layer
        b"\x00\x00"  # alt_group
        b"\x00\x00"  # volume
        b"\x00\x00"  # reserved
        + b"\x00" * 36  # matrix (3×3 of 16.16 fixed; zero matrix is fine for tests)
        + struct.pack(">II", width << 16, height << 16)
    )
    return _atom(b"tkhd", body)


def _hdlr(handler_type: bytes) -> bytes:
    """hdlr payload: [v+flags 4][pre_defined 4][handler_type 4]
    [reserved 12][name C-string 1]. Total body = 25 bytes."""
    body = b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00" + handler_type + b"\x00" * 12 + b"\x00"
    return _atom(b"hdlr", body)


def _stsd(sample_entry_type: bytes) -> bytes:
    """stsd payload with one minimal sample entry:
      [v+flags 4][entry_count=1 4][sample entry: [size 4][type 4]]
    """
    entry = struct.pack(">I", 8) + sample_entry_type  # zero-payload entry
    body = b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry
    return _atom(b"stsd", body)


def _stbl(sample_entry_type: bytes) -> bytes:
    return _atom(b"stbl", _stsd(sample_entry_type))


def _minf(sample_entry_type: bytes) -> bytes:
    return _atom(b"minf", _stbl(sample_entry_type))


def _mdia(handler_type: bytes, sample_entry_type: bytes) -> bytes:
    return _atom(b"mdia", _hdlr(handler_type) + _minf(sample_entry_type))


def _trak(
    *, handler: bytes, sample_entry: bytes,
    width: int = 0, height: int = 0,
) -> bytes:
    body = _tkhd_v0(width, height) + _mdia(handler, sample_entry)
    return _atom(b"trak", body)


def _synthetic_mp4(
    *,
    timescale: int = 1000,
    duration_ticks: int = 15070,
    video_dim: tuple[int, int] | None = (720, 1280),
    video_codec: bytes | None = b"avc1",
    audio_codec: bytes | None = b"mp4a",
) -> bytes:
    """Compose ftyp + moov(mvhd + traks). Skips mdat — the parser
    never reads sample data."""
    ftyp = _atom(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")
    moov_body = _mvhd_v0(timescale, duration_ticks)
    if video_dim and video_codec:
        moov_body += _trak(
            handler=b"vide",
            sample_entry=video_codec,
            width=video_dim[0],
            height=video_dim[1],
        )
    if audio_codec:
        moov_body += _trak(handler=b"soun", sample_entry=audio_codec)
    moov = _atom(b"moov", moov_body)
    return ftyp + moov


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_atom_probe_pai_720p_synthetic(tmp_path: Path) -> None:
    """A muxed 720p 9:16 mp4 with AAC audio (the Pai shape)."""
    p = tmp_path / "synthetic.mp4"
    p.write_bytes(_synthetic_mp4())
    got = _atom_probe(p)
    assert got is not None
    assert got["duration_sec"] == pytest.approx(15.070, abs=1e-3)
    assert got["width"] == 720
    assert got["height"] == 1280
    assert got["video_codec"] == "avc1"
    assert got["audio_codec"] == "mp4a"
    assert got["has_audio_track"] is True


def test_atom_probe_video_only(tmp_path: Path) -> None:
    """No `soun` track → has_audio_track False, audio_codec None."""
    p = tmp_path / "video_only.mp4"
    p.write_bytes(_synthetic_mp4(audio_codec=None))
    got = _atom_probe(p)
    assert got is not None
    assert got["has_audio_track"] is False
    assert got["audio_codec"] is None
    assert got["video_codec"] == "avc1"


def test_atom_probe_v1_extended_size(tmp_path: Path) -> None:
    """An atom with size==1 + 64-bit extended size should round-trip.

    We wrap the moov in a 64-bit-size header by hand-rewriting its
    leading bytes.
    """
    raw = _synthetic_mp4()
    # find moov atom in `raw`
    i = 0
    while i + 8 <= len(raw):
        size = struct.unpack_from(">I", raw, i)[0]
        if raw[i + 4 : i + 8] == b"moov":
            # Rewrite as size==1 + 64-bit extended size in the FIRST 8 bytes
            # of the atom, splicing 8 extra bytes for the extended size.
            extended_size = size + 8  # we'll add 8 bytes
            new_header = (
                struct.pack(">I", 1)
                + b"moov"
                + struct.pack(">Q", extended_size)
            )
            rewritten = raw[:i] + new_header + raw[i + 8 : i + size]
            p = tmp_path / "extended.mp4"
            p.write_bytes(rewritten)
            got = _atom_probe(p)
            assert got is not None
            assert got["width"] == 720
            assert got["duration_sec"] == pytest.approx(15.07, abs=1e-3)
            return
        i += size
    raise AssertionError("synthetic mp4 had no moov to rewrite")


def test_atom_probe_no_ftyp_is_none(tmp_path: Path) -> None:
    """A file without ftyp is not an MP4 — parser returns None."""
    p = tmp_path / "not_mp4.bin"
    p.write_bytes(b"\x00" * 64)
    assert _atom_probe(p) is None


def test_atom_probe_truncated_mvhd_is_safe(tmp_path: Path) -> None:
    """A moov with a malformed mvhd should fall through without crashing."""
    truncated_mvhd = _atom(b"mvhd", b"\x00\x00\x00\x00")  # 4-byte body
    moov = _atom(b"moov", truncated_mvhd)
    ftyp = _atom(b"ftyp", b"isom\x00\x00\x02\x00")
    p = tmp_path / "truncated.mp4"
    p.write_bytes(ftyp + moov)
    got = _atom_probe(p)
    assert got is not None
    assert got["duration_sec"] is None


def test_probe_mp4_uses_stdlib_when_ffprobe_missing(tmp_path: Path) -> None:
    """End-to-end: when ffprobe isn't on PATH, the public probe_mp4()
    should fall through to the stdlib parser and emit
    probe_source='stdlib_atoms' (or 'ffprobe' if the CI runner happens to
    have ffmpeg installed — both are acceptable terminal states)."""
    p = tmp_path / "synthetic.mp4"
    p.write_bytes(_synthetic_mp4())
    m = probe_mp4(p)
    assert isinstance(m, Mp4Meta)
    assert m.byte_size == p.stat().st_size
    assert m.mime == "video/mp4"
    # Either probe path must have populated the dimensions.
    assert m.width == 720
    assert m.height == 1280
    assert m.resolution_label == "720p"
    assert m.has_audio_track is True
    assert m.probe_source in {"stdlib_atoms", "ffprobe"}
    # The Phase 1I dataclass carries probe_source — meta_to_event_payload
    # must echo it.
    ep = meta_to_event_payload(m)
    assert ep["probe_source"] == m.probe_source


_REAL_PAI = (
    Path(__file__).resolve().parents[1]
    / "prospects"
    / "pai-skincare"
    / "production"
    / "dashboard_job_runs"
    / "1b1b1b1b-1b1b-1b1b-1b1b-1b1b1b1b1b1b"
    / "result.mp4"
)


@pytest.mark.skipif(not _REAL_PAI.exists(), reason="Pai 720p result.mp4 not on disk")
def test_probe_mp4_real_pai_artifact() -> None:
    """Anchor test: the real 720p Pai run dropped by Phase 1G's runner.
    Skips gracefully on a clean clone.
    """
    m = probe_mp4(_REAL_PAI)
    assert m.byte_size == 5_501_657
    # Either ffprobe or stdlib fallback must populate these.
    assert m.width == 720
    assert m.height == 1280
    assert m.resolution_label == "720p"
    assert m.has_audio_track is True
    assert m.duration_sec is not None and abs(m.duration_sec - 15.07) < 0.05
    assert m.probe_source in {"stdlib_atoms", "ffprobe"}
