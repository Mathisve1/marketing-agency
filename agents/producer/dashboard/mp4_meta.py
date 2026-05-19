"""Phase 1H/1I lightweight MP4 metadata probe.

The Phase 1G runner downloads `result.mp4` into the per-job folder; the
ingester needs to record `byte_size`, `duration_sec`, `resolution`, and
`mime` on the `generated_assets` row it creates.

Probe order:

  1. `ffprobe` (most accurate; populates codec names, exact stream count,
     full `format` block).
  2. **Phase 1I:** pure-stdlib MP4 atom parser (`_atom_probe`). Walks the
     `moov` / `trak` / `mvhd` / `tkhd` / `hdlr` / `stsd` atoms and
     recovers `duration_sec`, `width`, `height`, `has_audio_track`,
     `video_codec`, `audio_codec` from the file bytes alone. Slow but
     stdlib-only.
  3. byte-only fallback (`byte_size` + `mime`) when both fail.

The atom parser landed in Phase 1I after a real Pai 720p run revealed
that operators commonly have no ffprobe on PATH. The ingester used to
write NULL `duration_sec` / `resolution` on `generated_assets` in that
case; now it doesn't.

No new dependencies.
"""

from __future__ import annotations

import json
import logging
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("yuvo.dashboard.mp4_meta")

# Resolution buckets we recognise on the dashboard. Anything else gets
# emitted as a free-form `<width>x<height>` string so the operator can
# still see it on the asset row.
_KNOWN_RES_BUCKETS = (
    ("480p", 854, 480),
    ("720p", 1280, 720),
    ("1080p", 1920, 1080),
)
_RES_TOLERANCE = 0.10  # ±10% on each side, generous on 16:9 vs 9:16


@dataclass(frozen=True)
class Mp4Meta:
    """Result of probing a downloaded `result.mp4`.

    `byte_size` and `mime` are always populated. The remaining fields
    are populated when EITHER ffprobe runs OR the Phase 1I stdlib atom
    parser succeeds. `probe_source` records which path provided the
    metadata so the dashboard / docs can call it out:

      - "ffprobe"      — full report; `ffprobe_raw` carries the JSON.
      - "stdlib_atoms" — Phase 1I fallback; codecs from `stsd` sample
                         entries (`avc1`/`hev1`/... for video,
                         `mp4a`/`opus`/... for audio).
      - "byte_only"    — both paths failed (or returned no streams).
    """

    byte_size: int
    mime: str
    ffprobe_available: bool
    probe_source: str = "byte_only"
    duration_sec: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    resolution_label: Optional[str] = None  # '720p' | '1080p' | '<W>x<H>'
    has_audio_track: Optional[bool] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    ffprobe_raw: Optional[dict] = None  # full ffprobe -show_streams output


def _classify_resolution(width: Optional[int], height: Optional[int]) -> Optional[str]:
    """Bucket a (width, height) pair onto one of the dashboard's tier
    labels, with a 10% tolerance to absorb aspect-ratio variance.
    Falls back to `WxH` when nothing matches."""
    if not width or not height:
        return None
    # Compare against both orientations because UGC ships 9:16.
    longer = max(width, height)
    for label, ref_long, _ref_short in _KNOWN_RES_BUCKETS:
        # The "vertical" 720p output is actually 720×1280; the long side
        # is 1280. We match on the long side ± tolerance.
        lo = ref_long * (1 - _RES_TOLERANCE)
        hi = ref_long * (1 + _RES_TOLERANCE)
        if lo <= longer <= hi:
            return label
    return f"{width}x{height}"


def _run_ffprobe(mp4_path: Path) -> Optional[dict]:
    """Invoke `ffprobe -show_streams -show_format -of json`. Returns the
    parsed JSON or None if ffprobe isn't available or exited non-zero.
    """
    try:
        result = subprocess.run(  # noqa: S603 - we control the args
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(mp4_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        log.info(
            "ffprobe not on PATH — trying Phase 1I stdlib MP4 atom parser fallback."
        )
        return None
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out for %s — falling back.", mp4_path)
        return None
    if result.returncode != 0:
        log.warning(
            "ffprobe returned %d for %s: %s",
            result.returncode,
            mp4_path,
            (result.stderr or "")[:512],
        )
        return None
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        log.warning("ffprobe emitted non-JSON output for %s", mp4_path)
        return None


# ---------------------------------------------------------------------------
# Phase 1I — stdlib MP4 atom parser fallback.
#
# Walks the ISO BMFF (MP4) atom tree without external dependencies and
# recovers the same fields ffprobe would surface for a single A/V
# muxed mp4: duration, video width × height, audio track presence, and
# (best-effort) the codec FourCC for each track.
#
# Reference: ISO/IEC 14496-12 §8 (Movie Atoms). We only read the atom
# *headers* — never decode media samples — so the parser is fast and
# memory-cheap regardless of file size. Atom layout:
#
#   ┌────── 4 bytes ──────┬── 4 bytes ──┬──── payload ────┐
#   │ size (BE uint32)    │ type (FourCC)│      ...        │
#   └─────────────────────┴──────────────┴─────────────────┘
#
#   size == 0  →  atom runs to end of file
#   size == 1  →  next 8 bytes are a BE uint64 extended size
#   size >= 8  →  atom payload is `size - 8` bytes long
#
# The atoms we care about:
#   ftyp → file type only (sanity check the magic)
#   moov → container for movie metadata
#     mvhd → movie header: timescale + duration
#     trak → one per track
#       tkhd → track header: width/height (16.16 fixed-point) for video
#       mdia → media container
#         hdlr → handler_type FourCC: 'vide' / 'soun' / 'subt' / ...
#         minf
#           stbl
#             stsd → sample description; the first child atom's FourCC
#                    is the codec (e.g. 'avc1', 'hev1', 'av01' for video;
#                    'mp4a', 'opus', 'ec-3' for audio)
# ---------------------------------------------------------------------------


# Cap the per-atom payload we'll read into memory. The atom *headers* are
# tiny; we never read sample data. 64 MiB is a generous ceiling for
# `moov` which is what we recurse into. Real-world UGC moov is < 100 KiB.
_MAX_ATOM_PAYLOAD_BYTES = 64 * 1024 * 1024


def _atom_iter(buf: bytes, start: int, end: int):
    """Yield (type, payload_start, payload_end) for every direct child
    atom of the buf[start:end] range. Tolerates `size == 0` (atom to
    end-of-range) and `size == 1` (64-bit extended size)."""
    i = start
    while i + 8 <= end:
        size = struct.unpack_from(">I", buf, i)[0]
        atype = buf[i + 4 : i + 8]
        if size == 1:
            # 64-bit extended size follows the type field.
            if i + 16 > end:
                return
            size = struct.unpack_from(">Q", buf, i + 8)[0]
            if size < 16 or i + size > end:
                return
            yield atype, i + 16, i + size
            i += size
            continue
        if size == 0:
            # Atom runs to end of the current range.
            yield atype, i + 8, end
            return
        if size < 8 or i + size > end:
            return
        yield atype, i + 8, i + size
        i += size


def _find_atom(
    buf: bytes, start: int, end: int, atype: bytes,
) -> Optional[tuple[int, int]]:
    """First-match `_atom_iter` that returns (payload_start, payload_end)."""
    for t, s, e in _atom_iter(buf, start, end):
        if t == atype:
            return (s, e)
    return None


def _atom_probe(mp4_path: Path) -> Optional[dict]:
    """Phase 1I fallback. Read the file into memory (only mp4s under
    ~64 MiB; we cap explicitly) and walk the atom tree. Returns a dict
    in the same shape `probe_mp4` consumes, or None when parsing fails.
    """
    try:
        size = mp4_path.stat().st_size
    except OSError as e:
        log.warning("stdlib_atoms: cannot stat %s: %s", mp4_path, e)
        return None
    if size > _MAX_ATOM_PAYLOAD_BYTES:
        log.info(
            "stdlib_atoms: %s is %d bytes (> %d cap) — skipping atom parse",
            mp4_path,
            size,
            _MAX_ATOM_PAYLOAD_BYTES,
        )
        return None
    try:
        data = mp4_path.read_bytes()
    except OSError as e:
        log.warning("stdlib_atoms: cannot read %s: %s", mp4_path, e)
        return None

    # ftyp must be the first top-level atom on a sane mp4.
    moov = None
    seen_ftyp = False
    for atype, ps, pe in _atom_iter(data, 0, len(data)):
        if atype == b"ftyp":
            seen_ftyp = True
        elif atype == b"moov":
            moov = (ps, pe)
            break  # First moov is enough; later 'moof' fragments don't matter here.
    if not seen_ftyp:
        log.info("stdlib_atoms: %s has no ftyp — not an ISO-BMFF mp4", mp4_path)
        return None
    if moov is None:
        log.info("stdlib_atoms: %s has no moov atom", mp4_path)
        return None

    # mvhd → duration.
    duration_sec: Optional[float] = None
    mvhd = _find_atom(data, *moov, b"mvhd")
    if mvhd is not None:
        try:
            ms, _me = mvhd
            version = data[ms]
            if version == 0:
                # [v 1][flags 3][ctime 4][mtime 4][timescale 4][duration 4]
                timescale, duration = struct.unpack_from(">II", data, ms + 12)
            else:
                # [v 1][flags 3][ctime 8][mtime 8][timescale 4][duration 8]
                timescale, duration = struct.unpack_from(">IQ", data, ms + 20)
            if timescale > 0:
                duration_sec = duration / timescale
        except struct.error as e:
            log.warning("stdlib_atoms: malformed mvhd in %s: %s", mp4_path, e)

    # Walk traks → tkhd (width/height) + mdia/hdlr (handler) + stbl/stsd (codec).
    width: Optional[int] = None
    height: Optional[int] = None
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    has_audio: bool = False
    saw_any_trak: bool = False

    for atype, ts, te in _atom_iter(data, *moov):
        if atype != b"trak":
            continue
        saw_any_trak = True
        # Discover the track's handler type first so we know whether the
        # tkhd dimensions or stsd codec entries are interesting for video
        # vs. audio.
        handler: Optional[bytes] = None
        mdia = _find_atom(data, ts, te, b"mdia")
        if mdia is not None:
            hdlr = _find_atom(data, *mdia, b"hdlr")
            if hdlr is not None:
                hs, he = hdlr
                # hdlr: [v+flags 4][pre_defined 4][handler_type 4]...
                if he - hs >= 12:
                    handler = bytes(data[hs + 8 : hs + 12])

        if handler == b"vide" and width is None:
            tkhd = _find_atom(data, ts, te, b"tkhd")
            if tkhd is not None:
                tk_s, tk_e = tkhd
                if tk_e - tk_s >= 8:
                    w_raw, h_raw = struct.unpack_from(">II", data, tk_e - 8)
                    if w_raw > 0 and h_raw > 0:
                        width, height = w_raw >> 16, h_raw >> 16

        if mdia is not None:
            minf = _find_atom(data, *mdia, b"minf")
            if minf is not None:
                stbl = _find_atom(data, *minf, b"stbl")
                if stbl is not None:
                    stsd = _find_atom(data, *stbl, b"stsd")
                    if stsd is not None:
                        # stsd: [v+flags 4][entry_count 4][child atoms...]
                        ss, se = stsd
                        for sub_t, _ss, _se in _atom_iter(data, ss + 8, se):
                            codec = sub_t.decode("ascii", errors="replace")
                            if handler == b"vide" and video_codec is None:
                                video_codec = codec
                            elif handler == b"soun":
                                has_audio = True
                                if audio_codec is None:
                                    audio_codec = codec
                            break  # only first sample entry per stsd

        if handler == b"soun":
            has_audio = True

    # has_audio_track semantics:
    #   True  → saw at least one `soun` track
    #   False → saw at least one trak but none were `soun`
    #   None  → no trak atoms found at all (mp4 too truncated to tell)
    has_audio_field: Optional[bool] = (
        True if has_audio else (False if saw_any_trak else None)
    )

    return {
        "duration_sec": duration_sec,
        "width": width,
        "height": height,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "has_audio_track": has_audio_field,
    }


def probe_mp4(mp4_path: Path) -> Mp4Meta:
    """Probe `mp4_path`. byte_size + mime are always populated. duration,
    resolution, audio-track, and codec fields are filled by `ffprobe`
    when available, else by the Phase 1I stdlib atom parser.
    """
    if not mp4_path.exists():
        raise FileNotFoundError(f"MP4 not found at {mp4_path}")
    byte_size = mp4_path.stat().st_size
    mime = "video/mp4"

    ffprobe = _run_ffprobe(mp4_path)
    if ffprobe is not None:
        streams = ffprobe.get("streams") or []
        fmt = ffprobe.get("format") or {}

        width: Optional[int] = None
        height: Optional[int] = None
        video_codec: Optional[str] = None
        audio_codec: Optional[str] = None
        has_audio: bool = False

        for s in streams:
            codec_type = s.get("codec_type")
            if codec_type == "video" and width is None:
                width = s.get("width") if isinstance(s.get("width"), int) else None
                height = s.get("height") if isinstance(s.get("height"), int) else None
                video_codec = s.get("codec_name")
            elif codec_type == "audio":
                has_audio = True
                if audio_codec is None:
                    audio_codec = s.get("codec_name")

        duration_raw = fmt.get("duration")
        duration_sec: Optional[float] = None
        if duration_raw is not None:
            try:
                duration_sec = float(duration_raw)
            except (TypeError, ValueError):
                duration_sec = None

        return Mp4Meta(
            byte_size=byte_size,
            mime=mime,
            ffprobe_available=True,
            probe_source="ffprobe",
            duration_sec=duration_sec,
            width=width,
            height=height,
            resolution_label=_classify_resolution(width, height),
            has_audio_track=has_audio,
            video_codec=video_codec,
            audio_codec=audio_codec,
            ffprobe_raw=ffprobe,
        )

    # Phase 1I fallback path.
    atoms = _atom_probe(mp4_path)
    if atoms is not None and (
        atoms.get("duration_sec") is not None
        or atoms.get("width") is not None
        or atoms.get("has_audio_track") is not None
    ):
        return Mp4Meta(
            byte_size=byte_size,
            mime=mime,
            ffprobe_available=False,
            probe_source="stdlib_atoms",
            duration_sec=atoms.get("duration_sec"),
            width=atoms.get("width"),
            height=atoms.get("height"),
            resolution_label=_classify_resolution(
                atoms.get("width"), atoms.get("height")
            ),
            has_audio_track=atoms.get("has_audio_track"),
            video_codec=atoms.get("video_codec"),
            audio_codec=atoms.get("audio_codec"),
        )

    # Last resort: byte-only.
    return Mp4Meta(
        byte_size=byte_size,
        mime=mime,
        ffprobe_available=False,
        probe_source="byte_only",
    )


def meta_to_event_payload(meta: Mp4Meta) -> dict:
    """Compact dict for `generation_job_events.raw_payload`. Strips the
    bulky `ffprobe_raw` blob — the operator can re-run ffprobe locally
    if they need the full report. Phase 1I exposes `probe_source` so the
    operator can tell at a glance which fallback path produced the row.
    """
    return {
        "byte_size": meta.byte_size,
        "mime": meta.mime,
        "ffprobe_available": meta.ffprobe_available,
        "probe_source": meta.probe_source,
        "duration_sec": meta.duration_sec,
        "width": meta.width,
        "height": meta.height,
        "resolution_label": meta.resolution_label,
        "has_audio_track": meta.has_audio_track,
        "video_codec": meta.video_codec,
        "audio_codec": meta.audio_codec,
    }


__all__ = ["Mp4Meta", "meta_to_event_payload", "probe_mp4"]
