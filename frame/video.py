"""Video processing: transcode to H.264 at screen resolution with blur-fill."""

import os
import json
import logging
import subprocess
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.m4v', '.webm', '.mkv', '.avi', '.wmv', '.3gp'}
VIDEO_MIME_PREFIXES = ('video/',)


def is_video_filename(filename: str) -> bool:
    return Path(filename).suffix.lower() in VIDEO_EXTENSIONS


def is_video_mime(mime: str) -> bool:
    return bool(mime) and mime.lower().startswith(VIDEO_MIME_PREFIXES)


def probe_video(path: Path) -> Optional[dict]:
    """Return {width, height, duration, rotation} or None if not a video."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-print_format', 'json',
             '-show_streams', '-show_format', str(path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.warning(f"ffprobe failed for {path.name}: {result.stderr[:200]}")
            return None
        data = json.loads(result.stdout)
        v_stream = next((s for s in data.get('streams', [])
                         if s.get('codec_type') == 'video'), None)
        if not v_stream:
            return None
        width = int(v_stream.get('width', 0))
        height = int(v_stream.get('height', 0))
        duration = float(data.get('format', {}).get('duration', 0))

        rotation = 0
        for tag_src in (v_stream.get('tags', {}), v_stream.get('side_data_list', [{}])[0]):
            r = tag_src.get('rotate') or tag_src.get('rotation')
            if r is not None:
                try:
                    rotation = int(float(r)) % 360
                    break
                except (ValueError, TypeError):
                    pass

        return {'width': width, 'height': height,
                'duration': duration, 'rotation': rotation}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        logger.warning(f"ffprobe error for {path.name}: {e}")
        return None


def _build_filter(src_w: int, src_h: int, tgt_w: int, tgt_h: int,
                  blur_radius: int) -> str:
    """Build ffmpeg filter graph: same orientation = scale+crop, cross = blur-fill.

    blur_radius is the PIL Gaussian radius from photo config; map to ffmpeg
    boxblur luma_radius (rough ratio).
    """
    src_landscape = src_w >= src_h
    tgt_landscape = tgt_w >= tgt_h
    box_r = max(5, blur_radius // 2)

    if src_landscape == tgt_landscape:
        # Same orientation: scale to cover, center-crop
        return (f"scale={tgt_w}:{tgt_h}:force_original_aspect_ratio=increase,"
                f"crop={tgt_w}:{tgt_h}")

    # Cross orientation: blur-fill background + centered foreground
    return (
        f"split=2[bg][fg];"
        f"[bg]scale={tgt_w}:{tgt_h}:force_original_aspect_ratio=increase,"
        f"crop={tgt_w}:{tgt_h},boxblur={box_r}:1,eq=brightness=-0.2[bg2];"
        f"[fg]scale={tgt_w}:{tgt_h}:force_original_aspect_ratio=decrease[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2"
    )


def _effective_dimensions(probe: dict) -> Tuple[int, int]:
    """Return (width, height) accounting for rotation metadata."""
    w = probe['width']
    h = probe['height']
    if probe.get('rotation', 0) in (90, 270):
        return h, w
    return w, h


def transcode_video(source_path: Path, output_dir: Path,
                    item_id: str, filename: str,
                    h_size: Tuple[int, int], v_size: Tuple[int, int],
                    blur_radius: int,
                    orientations: Tuple[str, ...] = ('horizontal', 'vertical'),
                    max_duration: int = 120,
                    max_filesize_mb: int = 100,
                    ) -> Optional[Tuple[Optional[str], Optional[str]]]:
    """Transcode a video to H.264 .mp4 at the requested orientations.

    Returns (h_filename, v_filename) on success — either may be None if that
    orientation was not requested.  Returns None on failure or if rejected
    by size/duration limits.
    """
    if not source_path.exists():
        logger.error(f"Video source missing: {source_path}")
        return None

    size_mb = source_path.stat().st_size / (1024 * 1024)
    if size_mb > max_filesize_mb:
        logger.info(f"Skipping video {filename}: {size_mb:.1f}MB > {max_filesize_mb}MB")
        return None

    probe = probe_video(source_path)
    if not probe:
        logger.warning(f"Could not probe {filename}, skipping")
        return None

    if probe['duration'] > max_duration:
        logger.info(f"Skipping video {filename}: {probe['duration']:.0f}s > {max_duration}s")
        return None

    src_w, src_h = _effective_dimensions(probe)
    if src_w == 0 or src_h == 0:
        logger.warning(f"Invalid video dimensions for {filename}, skipping")
        return None

    stem = Path(filename).stem
    out_name = f"{item_id}_{stem}.mp4"
    h_fn = v_fn = None

    for orient in orientations:
        tgt_w, tgt_h = h_size if orient == 'horizontal' else v_size
        out_path = output_dir / orient / out_name

        vf = _build_filter(src_w, src_h, tgt_w, tgt_h, blur_radius)

        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-i', str(source_path),
            '-filter_complex', vf,
            '-c:v', 'libx264',
            '-profile:v', 'baseline',
            '-level', '4.0',
            '-pix_fmt', 'yuv420p',
            '-preset', 'ultrafast',
            '-crf', '24',
            '-movflags', '+faststart',
            '-an',  # no audio
            str(out_path),
        ]

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            # Allow up to 10x video duration for transcode (Pi Zero is slow)
            timeout = max(120, int(probe['duration'] * 10) + 60)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg failed for {filename} ({orient}): "
                             f"{result.stderr[:300]}")
                out_path.unlink(missing_ok=True)
                return None

            if orient == 'horizontal':
                h_fn = out_name
            else:
                v_fn = out_name
            logger.debug(f"Transcoded {filename} -> {out_name} ({orient})")
        except subprocess.TimeoutExpired:
            logger.error(f"ffmpeg timeout for {filename} ({orient})")
            out_path.unlink(missing_ok=True)
            return None
        except OSError as e:
            logger.error(f"ffmpeg error for {filename}: {e}")
            return None

    return (h_fn, v_fn)


def _transcode_worker(source_path, output_dir, item_id, filename,
                      h_size, v_size, blur_radius, orientations,
                      max_duration, max_filesize_mb, result_file):
    """Subprocess target: transcode one video, write result to a temp file."""
    try:
        with open('/proc/self/oom_score_adj', 'w') as f:
            f.write('800')
    except OSError:
        pass
    result = transcode_video(
        Path(source_path), Path(output_dir), item_id, filename,
        h_size, v_size, blur_radius,
        orientations=orientations,
        max_duration=max_duration, max_filesize_mb=max_filesize_mb,
    )
    with open(result_file, 'w') as f:
        json.dump(result, f)


def transcode_video_in_subprocess(source_path, output_dir, item_id, filename,
                                  h_size, v_size, blur_radius,
                                  orientations,
                                  max_duration=120, max_filesize_mb=100,
                                  ):
    """Run transcode_video in a child process for memory cleanup.

    Timeout is dynamic: 10x video duration + 5min buffer (Pi Zero is slow).
    """
    import multiprocessing
    # Probe in parent first to set timeout (cheap, ~1s)
    probe = probe_video(Path(source_path))
    duration = probe['duration'] if probe else 60
    timeout = max(300, int(duration * 10 * len(orientations)) + 120)

    result_file = Path('/tmp') / f'frame_vproc_{os.getpid()}_{item_id}.json'
    try:
        p = multiprocessing.Process(
            target=_transcode_worker,
            args=(str(source_path), str(output_dir), item_id, filename,
                  h_size, v_size, blur_radius, orientations,
                  max_duration, max_filesize_mb, str(result_file)),
        )
        p.start()
        p.join(timeout=timeout)
        if p.is_alive():
            p.kill()
            p.join()
            logger.error(f"Video transcoding timed out for {filename}")
            return None
        if p.exitcode != 0:
            logger.error(f"Video transcoding failed for {filename} (exit {p.exitcode})")
            return None
        if result_file.exists():
            with open(result_file) as f:
                data = json.load(f)
            return tuple(data) if data else None
        return None
    except Exception as e:
        logger.error(f"Video subprocess error for {filename}: {e}")
        return None
    finally:
        result_file.unlink(missing_ok=True)
