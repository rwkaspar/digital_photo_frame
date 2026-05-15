"""Synology Photos API client via public share links."""

import os
import time
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class SynologyPhotosClient:
    """Client for Synology Photos API via public share links."""

    def __init__(self, share_url: str, passphrase: str):
        self.share_url = share_url
        self.passphrase = passphrase
        parsed = urlparse(share_url)
        self.api_base = f"{parsed.scheme}://{parsed.netloc}"
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        })
        self.share_token = self._extract_share_token(share_url)
        logger.info(f"Initialized client: token={self.share_token}")

    def _extract_share_token(self, share_url: str) -> str:
        parsed = urlparse(share_url)
        path_parts = parsed.path.strip('/').split('/')
        if 'sharing' in path_parts:
            idx = path_parts.index('sharing')
            if idx + 1 < len(path_parts):
                return path_parts[idx + 1]
        raise ValueError(f"Could not extract share token from: {share_url}")

    def _api_url(self, api_name: str) -> str:
        return f"{self.api_base}/webapi/entry.cgi/{api_name}"

    def initialize_share(self) -> bool:
        """Log in to the shared album to obtain a sharing_sid cookie."""
        try:
            logger.info("Initializing share session...")
            api_url = f"{self.api_base}/webapi/entry.cgi"

            login_data = {
                'api': 'SYNO.Core.Sharing.Login',
                'method': 'login',
                'version': 1,
                'sharing_id': self.share_token,
                'password': self.passphrase or '',
            }
            resp = self.session.post(api_url, data=login_data, timeout=10)
            result = resp.json()
            logger.info(f"Sharing login: {result}")

            if not result.get('success'):
                logger.error(f"Sharing login failed: {result}")
                return False

            self.session.headers['x-syno-sharing'] = self.share_token
            return True
        except Exception as e:
            logger.error(f"Failed to initialize share: {e}")
            return False

    def list_items(self, offset: int = 0, limit: int = 100) -> Optional[Dict[str, Any]]:
        """List items in the shared album."""
        api = 'SYNO.Foto.Browse.Item'
        data = {
            'api': api,
            'method': 'list',
            'version': 1,
            'offset': offset,
            'limit': limit,
        }
        try:
            resp = self.session.post(self._api_url(api), data=data)
            resp.raise_for_status()
            result = resp.json()
            if result.get('success'):
                return result.get('data', {})
            logger.error(f"list_items failed: {result}")
            return None
        except Exception as e:
            logger.error(f"list_items exception: {e}")
            return None

    def get_all_items(self) -> List[Dict[str, Any]]:
        """Get all photo items from the shared album with pagination."""
        all_items = []
        offset = 0
        limit = 100

        while True:
            data = self.list_items(offset=offset, limit=limit)
            if not data:
                break

            items = data.get('list', [])
            if not items:
                break

            from frame.video import VIDEO_EXTENSIONS
            for item in items:
                filename = item.get('filename', '')
                ext = os.path.splitext(filename)[1].lower()
                is_video = (ext in VIDEO_EXTENSIONS or item.get('type') == 'video')
                if is_video:
                    item['media_type'] = 'video'
                elif item.get('type') == 'photo' or 'filename' in item:
                    item['media_type'] = 'photo'
                else:
                    continue
                all_items.append(item)

            logger.info(
                f"Fetched {len(items)} items (offset={offset}), "
                f"photos so far: {len(all_items)}"
            )

            if len(items) < limit:
                break

            offset += limit
            time.sleep(0.5)

        logger.info(f"Total photos fetched: {len(all_items)}")
        return all_items

    def get_album_name(self) -> str:
        """Get album name after share initialization.

        Tries SYNO.Foto.Browse.Album first, then SYNO.Foto.Sharing.Misc.
        """
        for api, key_path in [
            ('SYNO.Foto.Browse.Album', ('list', 0, 'name')),
            ('SYNO.Foto.Sharing.Misc', ('sharing', 'album_name')),
        ]:
            try:
                data = {
                    'api': api,
                    'method': 'get',
                    'version': 1,
                    'offset': 0,
                    'limit': 1,
                }
                resp = self.session.post(self._api_url(api), data=data)
                result = resp.json()
                if result.get('success'):
                    obj = result.get('data', {})
                    for k in key_path:
                        if isinstance(k, int):
                            obj = obj[k]
                        else:
                            obj = obj.get(k, {})
                    if isinstance(obj, str) and obj:
                        return obj
            except Exception:
                continue
        return ''

    @classmethod
    def resolve_album_name(cls, share_url: str, passphrase: str) -> str:
        """Create a temporary client, auth, and return the album name."""
        try:
            client = cls(share_url, passphrase)
            if client.initialize_share():
                return client.get_album_name()
        except Exception as e:
            logger.warning(f"Failed to resolve Synology album name: {e}")
        return ''

    def download_item(self, item_id: int, output_path: Path) -> bool:
        """Download a single item to the specified path."""
        api = 'SYNO.Foto.Download'
        url = self._api_url(api)
        try:
            data = {
                'api': api,
                'method': 'download',
                'version': 1,
                'unit_id': f'[{item_id}]',
                'force_download': 'true',
            }
            resp = self.session.post(url, data=data, stream=True)
            resp.raise_for_status()

            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            return True
        except Exception as e:
            logger.error(f"Failed to download item {item_id}: {e}")
            return False
