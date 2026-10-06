"""Hardware capability detection.

Lets the video transcode adapt to the host board: a RAM-constrained Pi
Zero 2W keeps the conservative single-threaded, pre-scaled, screen-freeing
path, while a roomier Pi 4/5 transcodes multi-threaded in one pass at higher
quality without touching the display.
"""
import os
import logging

logger = logging.getLogger(__name__)

# Boards below this much RAM are treated as memory-constrained (Pi Zero 2W
# ~512 MB, Pi 3 ~1 GB). Pi 4/5 have 2–8 GB and clear it comfortably.
LOW_MEMORY_THRESHOLD_MB = 1536


def total_memory_mb() -> int:
    """Total system RAM in MB (conservative 1024 fallback if unreadable)."""
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return 1024


def cpu_count() -> int:
    return os.cpu_count() or 1


def is_low_memory() -> bool:
    return total_memory_mb() < LOW_MEMORY_THRESHOLD_MB


def transcode_profile() -> dict:
    """Return transcode parameters tuned to the host hardware.

    On low-memory boards everything stays bounded: one encoder thread, a
    pre-scale pass to avoid huge YUV buffers, the kiosk stopped to free RAM,
    and conservative quality/bitrate caps. On roomier boards we use all
    cores, a single pass, leave the display running, and raise quality.
    """
    mem = total_memory_mb()
    cores = cpu_count()
    low = mem < LOW_MEMORY_THRESHOLD_MB
    profile = {
        'low_memory': low,
        'mem_mb': mem,
        'cores': cores,
        'threads': 1 if low else min(cores, 4),
        'stop_cage': low,      # free RAM for Chromium only when it is scarce
        'pre_scale': low,      # extra down-scale pass only when memory-bound
        'crf': 28 if low else 23,
        'maxrate_k': 2500 if low else 8000,
        'bufsize_k': 5000 if low else 16000,
        'fps_cap': 30 if low else 60,
        'level': '4.0' if low else '4.2',
    }
    return profile
