"""Application state and configuration management."""

import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class AppState:
    """Central application state replacing global singletons.

    Holds config, syncer, energy manager, sysinfo cache, wifi manager,
    and album name cache. Passed to the HTTP handler factory.
    """

    def __init__(self, config_path: str):
        self.config_path = config_path
        self.config = self.load_config()
        self.syncer = None
        self.energy_save = None
        self.sysinfo_cache = None
        self.wifi_manager = None
        self.album_name_cache = {}  # url -> resolved album name
        self._album_cache_path = Path(self.config_path).parent / '.album_names.json'
        self._load_album_cache()
        self.wizard_mode = False

    def load_config(self) -> dict:
        """Load config from YAML file."""
        with open(self.config_path, 'r') as f:
            return yaml.safe_load(f) or {}

    def save_config(self):
        """Write current config back to YAML file."""
        with open(self.config_path, 'w') as f:
            yaml.dump(self.config, f, default_flow_style=False)

    def _load_album_cache(self):
        """Load persisted album name cache from disk."""
        try:
            if self._album_cache_path.exists():
                with open(self._album_cache_path) as f:
                    self.album_name_cache = json.load(f)
                logger.debug(f"Loaded {len(self.album_name_cache)} cached album names")
        except Exception as e:
            logger.warning(f"Failed to load album name cache: {e}")
            self.album_name_cache = {}

    def save_album_cache(self):
        """Persist album name cache to disk."""
        try:
            with open(self._album_cache_path, 'w') as f:
                json.dump(self.album_name_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save album name cache: {e}")

    def has_album_sources(self) -> bool:
        """Check if any real photo album sources are configured."""
        def has_real_urls(urls):
            return any(u for u in urls if u and 'REPLACE_ME' not in u and 'example.com' not in u)
        for key in ('synology', 'google_photos', 'immich', 'icloud', 'nextcloud'):
            if has_real_urls(self.config.get(key, {}).get('share_urls', [])):
                return True
        return False

    def init_syncer(self):
        """Initialize the photo syncer (always, so cleanup works even with no albums)."""
        from frame.sync import PhotoSyncer
        self.syncer = PhotoSyncer(self.config)
        # Defer video transcoding to the sleep window while a sleep schedule is
        # active (display off = all cores free, no UI impact); do it inline if
        # sleep is disabled so videos are not stuck forever. Evaluated lazily
        # at each sync so it tracks the current schedule state.
        self.syncer.set_video_gate(self._should_transcode_videos_inline)

    def _should_transcode_videos_inline(self):
        return not getattr(getattr(self, 'energy_save', None), 'enabled', False)
