"""Nextcloud client for public shared folders via WebDAV."""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import urlparse, unquote

import requests

logger = logging.getLogger(__name__)

DAV_NS = '{DAV:}'
OC_NS = '{http://owncloud.org/ns}'

PROPFIND_BODY = '''<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:prop>
    <d:getlastmodified/>
    <d:getcontentlength/>
    <d:getcontenttype/>
    <d:resourcetype/>
    <d:getetag/>
    <oc:fileid/>
    <oc:size/>
  </d:prop>
</d:propfind>'''

IMAGE_TYPES = {
    'image/jpeg', 'image/png', 'image/gif', 'image/webp',
    'image/heic', 'image/heif', 'image/tiff', 'image/bmp',
}

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.heic', '.heif', '.tiff', '.bmp'}


class NextcloudClient:
    """Client for Nextcloud public shared folders via WebDAV.

    Works with all Nextcloud versions using the legacy /public.php/webdav/ endpoint.
    Authentication is HTTP Basic Auth with share_token as username.
    """

    def __init__(self, share_url: str, passphrase: str = ''):
        self.share_url = share_url
        self.passphrase = passphrase
        parsed = urlparse(share_url)
        self.base_url = f"{parsed.scheme}://{parsed.netloc}"
        self.token = self._extract_token(share_url)
        self.session = requests.Session()
        self.session.auth = (self.token, self.passphrase)
        self.session.headers.update({
            'X-Requested-With': 'XMLHttpRequest',
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        })
        self._album_name = ''
        # Try with proper certs first; fall back to unverified for self-signed
        self.session.verify = True

    @staticmethod
    def _extract_token(url: str) -> str:
        """Extract share token from URL like https://cloud.example.com/s/AbCdEf12345."""
        parsed = urlparse(url)
        parts = parsed.path.strip('/').split('/')
        # /s/TOKEN or /index.php/s/TOKEN
        if 's' in parts:
            idx = parts.index('s')
            if idx + 1 < len(parts):
                return parts[idx + 1]
        # Fallback: last path segment
        if parts:
            return parts[-1]
        raise ValueError(f"Could not extract Nextcloud share token from: {url}")

    @property
    def _webdav_url(self) -> str:
        return f"{self.base_url}/public.php/webdav"

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make a request, retrying with verify=False on SSL errors."""
        try:
            return self.session.request(method, url, timeout=30, **kwargs)
        except requests.exceptions.SSLError:
            logger.warning("Nextcloud SSL error, retrying with verify=False")
            self.session.verify = False
            return self.session.request(method, url, timeout=30, **kwargs)

    def _propfind(self, path: str = '/', depth: str = '1') -> List[Dict[str, Any]]:
        """PROPFIND on a WebDAV path, return parsed file entries."""
        url = self._webdav_url + path
        resp = self._request(
            'PROPFIND', url,
            headers={'Depth': depth, 'Content-Type': 'application/xml'},
            data=PROPFIND_BODY,
        )
        if resp.status_code == 401:
            raise PermissionError(f"Authentication failed (401) for Nextcloud share {self.token}")
        if resp.status_code != 207:
            raise RuntimeError(f"PROPFIND failed ({resp.status_code}): {resp.text[:200]}")

        tree = ET.fromstring(resp.content)
        entries = []
        first = True

        for response in tree.findall(f'{DAV_NS}response'):
            href = response.findtext(f'{DAV_NS}href', '')
            props = response.find(f'{DAV_NS}propstat/{DAV_NS}prop')
            if props is None:
                continue

            resource_type = props.find(f'{DAV_NS}resourcetype')
            is_collection = (resource_type is not None and
                             resource_type.find(f'{DAV_NS}collection') is not None)

            # Skip root folder entry
            if first:
                first = False
                if is_collection:
                    # Extract display name for album name
                    display = props.findtext(f'{DAV_NS}displayname', '')
                    if display:
                        self._album_name = display
                    continue

            content_type = props.findtext(f'{DAV_NS}getcontenttype', '')
            size = props.findtext(f'{DAV_NS}getcontentlength', '0')
            etag = props.findtext(f'{DAV_NS}getetag', '').strip('"')
            fileid = props.findtext(f'{OC_NS}fileid', '')

            filename = unquote(href.split('/')[-1]) if href else ''

            entries.append({
                'href': href,
                'filename': filename,
                'content_type': content_type,
                'size': int(size) if size else 0,
                'etag': etag,
                'fileid': fileid,
                'is_collection': is_collection,
            })

        return entries

    def initialize_share(self) -> bool:
        """Verify the share is accessible and get the folder name."""
        try:
            # Depth 0 to just check access and get folder name
            url = self._webdav_url + '/'
            resp = self._request(
                'PROPFIND', url,
                headers={'Depth': '0', 'Content-Type': 'application/xml'},
                data='''<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:">
  <d:prop><d:displayname/><d:resourcetype/></d:prop>
</d:propfind>''',
            )
            if resp.status_code == 401:
                logger.error(f"Nextcloud share authentication failed (wrong password?)")
                return False
            if resp.status_code != 207:
                logger.error(f"Nextcloud share access failed ({resp.status_code})")
                return False

            tree = ET.fromstring(resp.content)
            for response in tree.findall(f'{DAV_NS}response'):
                props = response.find(f'{DAV_NS}propstat/{DAV_NS}prop')
                if props is not None:
                    name = props.findtext(f'{DAV_NS}displayname', '')
                    if name:
                        self._album_name = name

            logger.info(f"Nextcloud share initialized: name={self._album_name!r}")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Nextcloud share: {e}")
            return False

    def get_album_name(self) -> str:
        return self._album_name

    def get_all_items(self, path: str = '/') -> List[Dict[str, Any]]:
        """List all image files in the shared folder (recursive)."""
        entries = self._propfind(path)
        items = []

        for entry in entries:
            if entry['is_collection']:
                # Recurse into subfolders
                subfolder = entry['href']
                # Extract relative path from webdav root
                webdav_prefix = '/public.php/webdav'
                if subfolder.startswith(webdav_prefix):
                    rel_path = subfolder[len(webdav_prefix):]
                else:
                    rel_path = '/' + entry['filename'] + '/'
                try:
                    items.extend(self.get_all_items(rel_path))
                except Exception as e:
                    logger.warning(f"Failed to list subfolder {rel_path}: {e}")
                continue

            # Filter for image and video files
            from frame.video import VIDEO_EXTENSIONS, is_video_mime
            content_type = entry.get('content_type', '')
            filename = entry.get('filename', '')
            ext = Path(filename).suffix.lower()

            is_image = content_type in IMAGE_TYPES or ext in IMAGE_EXTS
            is_video = is_video_mime(content_type) or ext in VIDEO_EXTENSIONS

            if is_image or is_video:
                file_id = entry.get('fileid') or entry.get('etag') or filename
                items.append({
                    'id': f'nc_{file_id}',
                    'filename': filename,
                    'filesize': entry.get('size', 0),
                    '_webdav_path': entry['href'],
                    'media_type': 'video' if is_video else 'photo',
                })

        logger.info(f"Nextcloud: found {len(items)} photos in shared folder")
        return items

    def download_item(self, webdav_path: str, output_path: Path) -> bool:
        """Download a file via WebDAV GET."""
        try:
            url = f"{self.base_url}{webdav_path}"
            resp = self._request('GET', url, stream=True)
            resp.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"Failed to download Nextcloud file: {e}")
            return False

    @classmethod
    def resolve_album_name(cls, share_url: str, passphrase: str = '') -> str:
        """Create a temporary client and return the folder name."""
        try:
            client = cls(share_url, passphrase)
            if client.initialize_share():
                return client.get_album_name()
        except Exception as e:
            logger.warning(f"Failed to resolve Nextcloud album name: {e}")
        return ''
