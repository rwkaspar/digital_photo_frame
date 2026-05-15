"""iCloud photo client supporting both Shared Albums and iCloud Links.

Shared Albums:  https://www.icloud.com/sharedalbum/#TOKEN
iCloud Links:   https://www.icloud.com/photos/#/icloudlinks/TOKEN
                https://share.icloud.com/photos/TOKEN

Auto-detects URL type and uses the appropriate API.
"""

import base64
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

HEADERS = {
    'Content-Type': 'application/json',
    'Origin': 'https://www.icloud.com',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
}

CLOUDKIT_RESOLVE_URL = (
    'https://ckdatabasews.icloud.com/database/1/'
    'com.apple.photos.cloud/production/public/records/resolve'
)


def _base62_to_int(s: str) -> int:
    result = 0
    for c in s:
        result = result * 62 + BASE62.index(c)
    return result


def _detect_url_type(url: str) -> str:
    """Return 'sharedalbum' or 'icloudlinks' based on URL pattern."""
    if 'sharedalbum' in url:
        return 'sharedalbum'
    # icloudlinks, share.icloud.com/photos, or bare token after #
    if 'icloudlinks' in url or 'share.icloud.com' in url:
        return 'icloudlinks'
    # Fragment without /sharedalbum/ path => assume icloudlinks
    parsed = urlparse(url)
    if parsed.fragment and 'sharedalbum' not in parsed.path:
        return 'icloudlinks'
    return 'icloudlinks'


def _extract_icloudlinks_token(url: str) -> str:
    """Extract short GUID from iCloud Links URL."""
    parsed = urlparse(url)
    # Fragment: #/icloudlinks/TOKEN or #TOKEN
    if parsed.fragment:
        frag = parsed.fragment.strip('/')
        parts = frag.split('/')
        if 'icloudlinks' in parts:
            idx = parts.index('icloudlinks')
            if idx + 1 < len(parts):
                return parts[idx + 1].strip('/')
        # Bare fragment
        return parts[-1].strip('/')
    # Path: /photos/TOKEN or share.icloud.com/photos/TOKEN
    path = parsed.path.strip('/')
    parts = path.split('/')
    if parts:
        return parts[-1].strip('/')
    raise ValueError(f"Could not extract iCloud Links token from: {url}")


class ICloudSharedAlbumClient:
    """Client for iCloud photo sharing. Auto-detects URL type.

    - Shared Albums use the sharedstreams API (no auth required)
    - iCloud Links use the CloudKit records/resolve + query API
    """

    def __init__(self, share_url: str):
        self.share_url = share_url
        self.url_type = _detect_url_type(share_url)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self._album_name = ''

        if self.url_type == 'sharedalbum':
            self.token = self._extract_sharedalbum_token(share_url)
            self.base_url = self._build_sharedstreams_url(self.token)
        else:
            self.token = _extract_icloudlinks_token(share_url)
            self._partition = ''
            self._access_token = ''
            self._zone_id: Dict[str, str] = {}

    # ── Token extraction ────────────────────────────────────────────

    @staticmethod
    def _extract_sharedalbum_token(url: str) -> str:
        parsed = urlparse(url)
        if parsed.fragment:
            return parsed.fragment.split(';')[0]
        path = parsed.path.strip('/')
        parts = path.split('/')
        if 'sharedalbum' in parts:
            idx = parts.index('sharedalbum')
            if idx + 1 < len(parts):
                return parts[idx + 1].split(';')[0]
        if parts:
            return parts[-1].split(';')[0]
        raise ValueError(f"Could not extract sharedalbum token from: {url}")

    @staticmethod
    def _build_sharedstreams_url(token: str) -> str:
        if token[0] == 'A':
            partition = _base62_to_int(token[1])
        else:
            partition = _base62_to_int(token[1:3])
        return f"https://p{partition:02d}-sharedstreams.icloud.com/{token}/sharedstreams"

    # ── Shared Albums (sharedstreams API) ───────────────────────────

    def _sharedstreams_post(self, endpoint: str, payload: dict) -> dict:
        url = f"{self.base_url}/{endpoint}"
        resp = self.session.post(url, json=payload, timeout=30)
        data = resp.json()
        if 'X-Apple-MMe-Host' in data:
            new_host = data['X-Apple-MMe-Host']
            self.base_url = f"https://{new_host}/{self.token}/sharedstreams"
            url = f"{self.base_url}/{endpoint}"
            logger.info(f"iCloud redirect to {new_host}")
            resp = self.session.post(url, json=payload, timeout=30)
            data = resp.json()
        return data

    def _sharedstreams_init(self) -> bool:
        try:
            data = self._sharedstreams_post('webstream', {'streamCtag': None})
            self._album_name = data.get('streamName', '')
            logger.info(f"iCloud shared album: name={self._album_name!r}")
            return True
        except Exception as e:
            logger.error(f"Failed to init iCloud shared album: {e}")
            return False

    def _sharedstreams_items(self) -> List[Dict[str, Any]]:
        all_photos: list = []
        stream_ctag = None
        while True:
            data = self._sharedstreams_post('webstream', {'streamCtag': stream_ctag})
            photos = data.get('photos', [])
            if not photos:
                break
            all_photos.extend(photos)
            new_ctag = data.get('streamCtag')
            if new_ctag == stream_ctag:
                break
            stream_ctag = new_ctag

        download_map = self._sharedstreams_resolve_urls(all_photos)
        items = []
        for photo in all_photos:
            is_video = photo.get('mediaAssetType') == 'video'
            guid = photo.get('photoGuid', '')
            derivs = photo.get('derivatives', {})
            if not derivs:
                continue
            best = max(derivs.values(), key=lambda d: int(d.get('fileSize', 0)))
            checksum = best.get('checksum', '')
            download_url = download_map.get(checksum, '')
            if not download_url:
                continue
            ext = 'mp4' if is_video else 'jpg'
            items.append({
                'id': f'icl_{guid}',
                'filename': f'icloud_{guid}.{ext}',
                'filesize': int(best.get('fileSize', 0)),
                '_download_url': download_url,
                'media_type': 'video' if is_video else 'photo',
            })
        logger.info(f"iCloud shared album: {len(items)} items")
        return items

    def _sharedstreams_resolve_urls(self, photos: list) -> Dict[str, str]:
        url_map: Dict[str, str] = {}
        guids = [p['photoGuid'] for p in photos]
        for i in range(0, len(guids), 25):
            batch = guids[i:i + 25]
            try:
                data = self._sharedstreams_post('webasseturls', {'photoGuids': batch})
                for checksum, info in data.get('items', {}).items():
                    loc = info.get('url_location', '')
                    path = info.get('url_path', '')
                    if loc and path:
                        url_map[checksum] = f"https://{loc}{path}"
            except Exception as e:
                logger.warning(f"Failed to resolve iCloud URLs (batch {i}): {e}")
        return url_map

    # ── iCloud Links (CloudKit API) ─────────────────────────────────

    def _cloudkit_resolve(self) -> bool:
        """Resolve shortGUID to get zone info and anonymous access token."""
        try:
            payload = {
                'shortGUIDs': [{'value': self.token, 'shouldFetchRootRecord': True}]
            }
            resp = self.session.post(CLOUDKIT_RESOLVE_URL, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            result = data['results'][0]

            anon = result.get('anonymousPublicAccess', {})
            self._partition = anon.get('databasePartition', '')
            self._access_token = anon.get('token', '')
            self._zone_id = result.get('zoneID', {})

            # Album name from share title or owner name
            share = result.get('share', {})
            title = share.get('fields', {}).get('cloudkit.title', {}).get('value', '')
            if title:
                self._album_name = title
            else:
                owner = result.get('ownerIdentity', {})
                nc = owner.get('nameComponents', {})
                name = f"{nc.get('givenName', '')} {nc.get('familyName', '')}".strip()
                root = result.get('rootRecord', {}).get('fields', {})
                count = root.get('photosCount', {}).get('value', '?')
                if name:
                    self._album_name = f"iCloud ({name}, {count} Fotos)"
                else:
                    self._album_name = f"iCloud Link ({count} Fotos)"

            if not self._partition or not self._access_token:
                logger.error("iCloud Links: no anonymousPublicAccess in resolve")
                return False

            logger.info(f"iCloud link resolved: name={self._album_name!r}, "
                        f"partition={self._partition}")
            return True
        except Exception as e:
            logger.error(f"Failed to resolve iCloud link: {e}")
            return False

    def _cloudkit_query(self, record_type: str, continuation: Optional[str] = None) -> dict:
        """Query the shared CloudKit database."""
        base = (f"{self._partition}/database/1/com.apple.photos.cloud/"
                f"production/shared/records/query")
        params = {
            'publicAccessAuthToken': self._access_token,
            'remapEnums': 'true',
            'getCurrentSyncToken': 'true',
        }
        payload: Dict[str, Any] = {
            'query': {'recordType': record_type},
            'zoneID': self._zone_id,
        }
        if continuation:
            payload['continuationMarker'] = continuation
        resp = self.session.post(base, params=params, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _cloudkit_items(self) -> List[Dict[str, Any]]:
        """Fetch all photos from an iCloud Link via CloudKit."""
        all_records: list = []
        continuation = None

        while True:
            data = self._cloudkit_query(
                'CPLAssetAndMasterByAssetDateWithoutHiddenOrDeleted',
                continuation=continuation,
            )
            records = data.get('records', [])
            all_records.extend(records)
            continuation = data.get('continuationMarker')
            if not continuation:
                break

        items = []
        for rec in all_records:
            if rec.get('recordType') != 'CPLMaster':
                continue

            fields = rec.get('fields', {})
            record_name = rec.get('recordName', '')

            item_type = fields.get('itemType', {}).get('value', '')
            is_video = (item_type.startswith('public.movie')
                        or item_type.startswith('com.apple.quicktime'))

            if is_video:
                # Try video resolutions first
                res_video = (fields.get('resOriginalVidComplRes', {}).get('value', {})
                             or fields.get('resOriginalRes', {}).get('value', {}))
                download_url = res_video.get('downloadURL', '')
                filesize = res_video.get('size', 0)
            else:
                # Get original resolution asset
                res_original = fields.get('resOriginalRes', {}).get('value', {})
                download_url = res_original.get('downloadURL', '')
                filesize = res_original.get('size', 0)
                if not download_url:
                    res_med = fields.get('resJPEGMedRes', {}).get('value', {})
                    download_url = res_med.get('downloadURL', '')
                    filesize = res_med.get('size', 0)

            if not download_url:
                continue

            # Decode filename
            default_ext = 'mov' if is_video else 'jpg'
            filename_enc = fields.get('filenameEnc', {}).get('value', '')
            if filename_enc:
                try:
                    filename = base64.b64decode(filename_enc).decode('utf-8')
                except Exception:
                    filename = f'icloud_{record_name}.{default_ext}'
            else:
                filename = f'icloud_{record_name}.{default_ext}'

            # Sanitize record_name — CloudKit can include / and + in names
            safe_name = record_name.replace('/', '_').replace('+', '_')
            items.append({
                'id': f'icl_{safe_name}',
                'filename': filename,
                'filesize': filesize,
                '_download_url': download_url,
                'media_type': 'video' if is_video else 'photo',
            })

        logger.info(f"iCloud link: {len(items)} items")
        return items

    # ── Public API (delegates to the right backend) ─────────────────

    def initialize_share(self) -> bool:
        if self.url_type == 'sharedalbum':
            return self._sharedstreams_init()
        return self._cloudkit_resolve()

    def get_album_name(self) -> str:
        return self._album_name

    def get_all_items(self) -> List[Dict[str, Any]]:
        if self.url_type == 'sharedalbum':
            return self._sharedstreams_items()
        return self._cloudkit_items()

    def download_item(self, download_url: str, output_path: Path) -> bool:
        try:
            resp = self.session.get(download_url, stream=True, timeout=60)
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"Failed to download iCloud photo: {e}")
            return False

    @classmethod
    def resolve_album_name(cls, share_url: str) -> str:
        try:
            client = cls(share_url)
            if client.initialize_share():
                return client.get_album_name()
        except Exception as e:
            logger.warning(f"Failed to resolve iCloud album name: {e}")
        return ''
