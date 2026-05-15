"""Immich client for shared album links."""

import logging
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


class ImmichClient:
    """Client for Immich shared album links.

    Authenticates via shared link key + optional password, then lists and
    downloads assets through the Immich REST API.
    """

    def __init__(self, share_url: str, passphrase: str = ''):
        self.share_url = share_url
        self.passphrase = passphrase
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        })
        parsed = urlparse(share_url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.key = self._extract_key(share_url)
        self._album_name = ''
        # Try with proper certs first; fall back to unverified for self-signed
        self.session.verify = True

    @staticmethod
    def _extract_key(url: str) -> str:
        """Extract the shared link key from a /share/<key> URL."""
        parsed = urlparse(url)
        parts = parsed.path.strip('/').split('/')
        if 'share' in parts:
            idx = parts.index('share')
            if idx + 1 < len(parts):
                return parts[idx + 1]
        raise ValueError(f"Could not extract share key from: {url}")

    def _api(self, method: str, path: str, params: dict = None, **kwargs) -> requests.Response:
        """Make an API request, retrying with verify=False on SSL errors."""
        url = f"{self.base_url}{path}"
        # Always pass key as a proper query param (avoids encoding issues)
        if params is None:
            params = {}
        if 'key' not in params:
            params['key'] = self.key
        try:
            return self.session.request(method, url, params=params, timeout=30, **kwargs)
        except requests.exceptions.SSLError:
            logger.warning("Immich SSL error, retrying with verify=False")
            self.session.verify = False
            return self.session.request(method, url, params=params, timeout=30, **kwargs)

    def initialize_share(self) -> bool:
        """Authenticate to the shared link (login if password-protected).

        Supports both Immich v2.6+ (POST /shared-links/login) and
        v2.5.x (password as query param on GET /shared-links/me).
        """
        try:
            params = {}
            if self.passphrase:
                params['password'] = self.passphrase

            # Try GET /api/shared-links/me (works on v2.5+)
            resp = self._api('GET', '/api/shared-links/me', params=params)

            if resp.status_code == 401 and self.passphrase:
                # Try v2.6+ POST login endpoint
                resp = self._api('POST', '/api/shared-links/login',
                                 json={'password': self.passphrase})
                if resp.status_code in (200, 201):
                    token = resp.cookies.get('immich_shared_link_token')
                    if token:
                        self.session.cookies.set('immich_shared_link_token', token)
                    # Re-fetch shared link info
                    resp = self._api('GET', '/api/shared-links/me')

            if resp.status_code != 200:
                logger.error(f"Immich login failed ({resp.status_code}): {resp.text[:200]}")
                return False
            data = resp.json()

            album = data.get('album') or {}
            self._album_name = album.get('albumName', '')
            logger.info(f"Immich share initialized: album={self._album_name!r}, "
                        f"assets={len(data.get('assets', []))}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Immich share: {e}")
            return False

    def get_all_items(self) -> List[Dict[str, Any]]:
        """List all photo assets from the shared link."""
        params = {}
        if self.passphrase:
            params['password'] = self.passphrase
        resp = self._api('GET', '/api/shared-links/me', params=params)
        resp.raise_for_status()
        data = resp.json()

        assets = data.get('assets', [])

        # v2.5: shared-links/me may return empty assets for ALBUM type;
        # fetch album directly to get the actual asset list
        if not assets and data.get('type') == 'ALBUM':
            album = data.get('album', {})
            album_id = album.get('id', '')
            if album_id:
                logger.info(f"Immich: fetching album {album_id} for asset list")
                resp2 = self._api('GET', f'/api/albums/{album_id}', params=params)
                if resp2.status_code == 200:
                    assets = resp2.json().get('assets', [])

        items = []
        for asset in assets:
            asset_type = asset.get('type', '').upper()
            media_type = 'video' if asset_type == 'VIDEO' else 'photo'
            asset_id = asset.get('id', '')
            filename = asset.get('originalFileName', asset.get('originalPath', '').split('/')[-1])
            if not filename:
                filename = f'{asset_id}.mp4' if media_type == 'video' else f'{asset_id}.jpg'
            items.append({
                'id': f'imm_{asset_id}',
                'filename': filename,
                'filesize': asset.get('exifInfo', {}).get('fileSizeInByte', 0),
                'time': 0,
                'media_type': media_type,
            })

        logger.info(f"Immich: found {len(items)} items in shared album")
        return items

    def get_album_name(self) -> str:
        return self._album_name

    @classmethod
    def resolve_album_name(cls, share_url: str, passphrase: str = '') -> str:
        """Create a temporary client, auth, and return the album name."""
        try:
            client = cls(share_url, passphrase)
            if client.initialize_share():
                return client.get_album_name()
        except Exception as e:
            logger.warning(f"Failed to resolve Immich album name: {e}")
        return ''

    def download_item(self, asset_id: str, output_path: Path) -> bool:
        """Download an asset by its UUID."""
        try:
            resp = self._api(
                'GET',
                f'/api/assets/{asset_id}/original',
                stream=True,
            )
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"Failed to download Immich asset {asset_id}: {e}")
            return False
