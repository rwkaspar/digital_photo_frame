"""HTTP server entry point for the digital photo frame."""

import os
import sys
import logging
from pathlib import Path
from http.server import HTTPServer

import yaml

from frame.config import AppState
from frame.energy import SysinfoCache, EnergySaveManager
from frame.wifi import WiFiManager
from frame.routes import PhotoFrameHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_handler(app, photos_dir, viewer_dir, slideshow_config=None):
    """Create a handler class bound to the given AppState."""
    class Handler(PhotoFrameHandler):
        pass
    Handler.app = app

    def factory(*args, **kwargs):
        return Handler(
            *args,
            photos_dir=photos_dir,
            viewer_dir=viewer_dir,
            slideshow_config=slideshow_config,
            **kwargs,
        )
    return factory


def main():
    """Main entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config_frame.yaml'

    app = AppState(config_path)
    config = app.config

    photos_config = config.get('photos', {})
    photos_dir = photos_config.get('base_dir', '/srv/frame/photos')
    # Resolve viewer dir relative to the repo root (parent of frame/)
    viewer_dir = str(Path(__file__).parent.parent / 'viewer')
    port = int(os.environ.get('PORT', 8080))

    # Ensure directories exist
    Path(photos_dir).mkdir(parents=True, exist_ok=True)
    (Path(photos_dir) / 'horizontal').mkdir(parents=True, exist_ok=True)
    (Path(photos_dir) / 'vertical').mkdir(parents=True, exist_ok=True)

    slideshow_config = config.get('slideshow', {})

    # Detect wizard mode: setup not yet completed
    if not config.get('setup_complete'):
        app.wizard_mode = True
        logger.info("Setup not complete — entering wizard mode")

    # Start background services
    app.sysinfo_cache = SysinfoCache(photos_dir)
    app.energy_save = EnergySaveManager(app_state=app)

    # Check WiFi connectivity and start hotspot if needed
    app.wifi_manager = WiFiManager()
    if app.wifi_manager.check_connectivity():
        logger.info("WiFi connected — normal mode")
        app.init_syncer()
        if app.syncer:
            logger.info("Triggering startup photo sync")
            app.syncer.run_sync()
    else:
        logger.warning("No WiFi connectivity — starting hotspot")
        app.wifi_manager.start_hotspot()

    # Start energy-save loop AFTER syncer is ready (avoids race where
    # _set_sleep triggers before syncer is initialized)
    app.energy_save.start()

    handler = create_handler(app, photos_dir, viewer_dir, slideshow_config)
    HTTPServer.allow_reuse_address = True
    server = HTTPServer(('0.0.0.0', port), handler)

    logger.info(f"Starting photo frame server on port {port}")
    logger.info(f"Photos directory: {photos_dir}")
    logger.info(f"Viewer directory: {viewer_dir}")
    if app.wifi_manager.mode == 'hotspot':
        logger.info(f"Hotspot mode: connect to '{app.wifi_manager.get_status()['hotspot_ssid']}'")
    else:
        logger.info(f"Open http://localhost:{port}/ to view")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")


if __name__ == '__main__':
    main()
