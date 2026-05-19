"""Phase 1O — extract specific frames from the v4 retry result for
label-readability inspection. Uses the bundled imageio-ffmpeg binary
(no system ffmpeg install required)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

JOB_DIR = Path(
    r"prospects/pai-skincare/production/dashboard_job_runs/"
    r"4e4e4e4e-4e4e-4e4e-4e4e-4e4e4e4e4e4e"
)
SRC = JOB_DIR / "result.mp4"
OUT_DIR = JOB_DIR / "frames"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Per operator request — concentrate on the prop-beat window (5-9s),
# the application beat (9-13s), and the bookends.
TIMESTAMPS_SEC = [3, 6, 8, 11, 14]

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

results = []
for ts in TIMESTAMPS_SEC:
    out_path = OUT_DIR / f"frame_t{ts:02d}s.jpg"
    # -ss BEFORE -i = fast seek (input-level). For a 15s clip this is
    # accurate enough. -frames:v 1 grabs one frame, -q:v 2 ~= JPEG 95.
    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-ss", str(ts),
        "-i", str(SRC),
        "-frames:v", "1",
        "-q:v", "2",
        str(out_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ok = r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    results.append({
        "t": ts,
        "ok": ok,
        "path": str(out_path),
        "bytes": out_path.stat().st_size if out_path.exists() else 0,
        "stderr_tail": (r.stderr or "")[-200:],
    })

for r in results:
    flag = "OK " if r["ok"] else "FAIL"
    print(f"  [{flag}] t={r['t']:>2}s  bytes={r['bytes']:>6}  {r['path']}")
    if not r["ok"]:
        print(f"         stderr: {r['stderr_tail']}")
sys.exit(0 if all(r["ok"] for r in results) else 1)
