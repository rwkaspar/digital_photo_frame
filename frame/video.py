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
    """Return {width, height, duration, rotation} or None if not a video.

    Uses -analyzeduration/-probesize limits so ffprobe doesn't scan the
    entire file on slow SD cards (Pi Zero often hits 30s timeout otherwise).
    """
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-analyzeduration', '2000000',  # 2 s
             '-probesize', '5000000',         # 5 MB
             '-print_format', 'json',
             '-show_streams', '-show_format', str(path)],
            capture_output=True, text=True, timeout=60,
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
                  blur_radius: int, blur_fill: bool = True) -> str:
    """Build ffmpeg filter graph: same orientation = scale+crop, cross = blur-fill.

    To keep memory low on Pi Zero, we ALWAYS scale source to the target
    bounding box first (limits decoded YUV frame size). For cross-orientation
    we then split that smaller frame.

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

    if not blur_fill:
        # Letterbox fallback: scale to fit, pad with black. Lowest memory.
        return (f"scale={tgt_w}:{tgt_h}:force_original_aspect_ratio=decrease,"
                f"pad={tgt_w}:{tgt_h}:(ow-iw)/2:(oh-ih)/2:color=black")

    # Cross orientation blur-fill: scale source down FIRST so split copies
    # work on a small frame, then build the blur background + overlay.
    return (
        f"[0:v]scale={tgt_w}:{tgt_h}:force_original_aspect_ratio=decrease,split=2[bg][fg];"
        f"[bg]scale={tgt_w}:{tgt_h}:force_original_aspect_ratio=increase,"
        f"crop={tgt_w}:{tgt_h},boxblur={box_r}:1,eq=brightness=-0.2[bg2];"
        f"[bg2][fg]overlay=(W-w)/2:(H-h)/2"
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
    # Pi Zero 2W has 425MB RAM — running with -threads 1 keeps memory
    # bounded (libx264 with multiple threads buffers extra frames per thread).
    threads = '1'

    # PASS 1: if source is large (4K HEVC, etc.), pre-scale to 1080p H.264
    # FIRST. Without this, the filter_complex blur-fill pass has to allocate
    # huge YUV decode buffers AND duplicate them via split= — hits OOM on
    # the Pi. The intermediate is small (~5MB), the second pass operates on
    # cheap H.264 frames.
    PRE_SCALE_TARGET = 1080
    if max(src_w, src_h) > PRE_SCALE_TARGET:
        intermediate = source_path.parent / f"_scaled_{item_id}.mp4"
        scale_filter = (f"scale={PRE_SCALE_TARGET}:-2:"
                        f"force_original_aspect_ratio=decrease")
        cmd_pre = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-threads', threads,
            '-i', str(source_path),
            '-vf', scale_filter,
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-x264-params', f'threads={threads}',
            '-an',
            str(intermediate),
        ]
        try:
            pre_timeout = max(900, int(probe['duration'] * 30) + 300)
            res = subprocess.run(cmd_pre, capture_output=True, text=True,
                                 timeout=pre_timeout)
            if res.returncode != 0:
                logger.warning(f"Pre-scale failed for {filename}, using original "
                               f"source: {res.stderr[:200]}")
                intermediate.unlink(missing_ok=True)
            else:
                logger.info(f"Pre-scaled {filename} to 1080p intermediate")
                source_path = intermediate
                # Re-probe to update dimensions
                probe2 = probe_video(source_path)
                if probe2:
                    src_w, src_h = _effective_dimensions(probe2)
        except subprocess.TimeoutExpired:
            logger.warning(f"Pre-scale timeout for {filename}, using original")
            intermediate.unlink(missing_ok=True)

    def _run(filter_str: str, complex_filter: bool) -> tuple:
        """Run ffmpeg with the given filter. Returns (returncode, stderr)."""
        cmd = [
            'ffmpeg', '-y', '-loglevel', 'error',
            '-threads', threads,
            '-i', str(source_path),
        ]
        if complex_filter:
            cmd += ['-filter_complex', filter_str]
        else:
            cmd += ['-vf', filter_str]
        cmd += [
            '-r', '30',  # cap output framerate — Pi Zero can't decode 60fps H.264 smoothly
            '-c:v', 'libx264',
            '-profile:v', 'baseline',
            '-level', '4.0',
            '-pix_fmt', 'yuv420p',
            '-preset', 'ultrafast',
            '-crf', '28',
            '-maxrate', '2500k',
            '-bufsize', '5000k',
            '-x264-params', f'threads={threads}',
            # Fragmented MP4: each fragment is self-contained, so a partial
            # file from a SIGKILLed ffmpeg is still playable up to the last
            # complete fragment. Faststart would require a final rewrite that
            # never happens on timeout-kill.
            '-movflags', 'frag_keyframe+empty_moov+default_base_moof',
            '-an',
            str(out_path),
        ]
        try:
            # HEVC software decode + libx264 on Pi Zero can run at 0.1-0.2× realtime
            # for 4K source. Be generous to avoid SIGKILL truncating output.
            timeout = max(600, int(probe['duration'] * 30) + 300)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
            )
            return result.returncode, result.stderr or ''
        except subprocess.TimeoutExpired:
            return -1, 'TIMEOUT'

    for orient in orientations:
        tgt_w, tgt_h = h_size if orient == 'horizontal' else v_size
        out_path = output_dir / orient / out_name

        src_landscape = src_w >= src_h
        tgt_landscape = tgt_w >= tgt_h
        is_cross = (src_landscape != tgt_landscape)

        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # Try blur-fill (cross-orient) or scale+crop (same-orient)
            vf = _build_filter(src_w, src_h, tgt_w, tgt_h, blur_radius,
                               blur_fill=True)
            rc, err = _run(vf, complex_filter=is_cross)

            if rc != 0 and is_cross:
                # OOM or other failure with blur-fill — fall back to letterbox
                out_path.unlink(missing_ok=True)
                logger.warning(f"Blur-fill failed for {filename} ({orient}); "
                               f"retrying with letterbox. err={err[:200]}")
                vf_lb = _build_filter(src_w, src_h, tgt_w, tgt_h, blur_radius,
                                      blur_fill=False)
                rc, err = _run(vf_lb, complex_filter=False)

            if rc != 0:
                logger.error(f"ffmpeg failed for {filename} ({orient}): {err[:300]}")
                out_path.unlink(missing_ok=True)
                return None

            if orient == 'horizontal':
                h_fn = out_name
            else:
                v_fn = out_name
            logger.debug(f"Transcoded {filename} -> {out_name} ({orient})")
        except OSError as e:
            logger.error(f"ffmpeg error for {filename}: {e}")
            return None

    # Clean up the pre-scaled intermediate if we created one
    if source_path.name.startswith('_scaled_'):
        source_path.unlink(missing_ok=True)

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
    # Allow generous time per orientation (blur-fill + letterbox retry).
    # Pi Zero with HEVC 4K source is genuinely slow.
    timeout = max(1200, int(duration * 60 * len(orientations)) + 300)

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
