"""Google Photos client for public shared albums (no OAuth).

Parses the AF_initDataCallback JS data embedded in Google Photos shared
album pages to extract all photo URLs (up to ~500 per album).
"""

import re
import json
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any

import requests

logger = logging.getLogger(__name__)


class GooglePhotosClient:
    """Client for Google Photos shared albums via public share links."""

    def __init__(self, share_url: str):
        self.share_url = share_url
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
        })

    def get_all_items(self) -> List[Dict[str, Any]]:
        """Fetch the shared album page and extract image URLs.

        Parses the AF_initDataCallback data structure which contains
        all photo entries as nested JS arrays.
        """
        resp = self.session.get(self.share_url, timeout=30)
        resp.raise_for_status()
        html = resp.text

        # Parse structured data from AF_initDataCallback for reliable extraction
        items = self._parse_af_data(html)

        # Fallback to regex if AF_initDataCallback parsing fails
        if not items:
            logger.warning("AF_initDataCallback parsing found no photos, trying regex fallback")
            items = self._regex_fallback(html)

        if len(items) >= 500:
            logger.warning(
                f"Google Photos: found {len(items)} images — album may contain more. "
                "Google only embeds ~500 photos per page. Split into smaller albums "
                "and add each link separately to get all photos."
            )
        else:
            logger.info(f"Google Photos: found {len(items)} images in shared album")
        return items

    def _parse_af_data(self, html: str) -> List[Dict[str, Any]]:
        """Extract photo URLs from AF_initDataCallback data structure.

        Google Photos embeds photo data in AF_initDataCallback({key:'ds:N', data:[...]});
        blocks. The photo data block contains nested arrays where each photo entry has:
          [0] = photo ID
          [1][0] = lh3 base URL
          [1][1] = width
          [1][2] = height
        """
        # Find all AF_initDataCallback data blocks
        pattern = r'AF_initDataCallback\(\{[^}]*key:\s*\'ds:\d+\'[^}]*data:(\[[\s\S]*?)\}\);</script>'
        blocks = re.findall(pattern, html)
        if not blocks:
            return []

        # Use the largest block (contains photo data)
        data_str = max(blocks, key=len)
        if len(data_str) < 100:
            return []

        # Extract lh3 photo/video URLs from the structured data.
        # Each entry looks like: ["ID",["https://lh3...com/pw/HASH",WIDTH,HEIGHT,...
        # Videos have a [null,DURATION_MS,...] block after the image metadata.
        # We capture each entry's full preamble (~600 chars) to test for video markers.
        entry_pattern = r'\["([^"]{10,})",\["(https://lh3\.googleusercontent\.com/[^"]+)",(\d+),(\d+)([^\[]{0,600})'
        matches = re.findall(entry_pattern, data_str)

        items = []
        seen = set()
        for photo_id, url, width, height, tail in matches:
            if url in seen:
                continue
            seen.add(url)
            # Heuristic: a video entry has a nested array with a duration value
            # like [null,12345,...] (duration in ms) shortly after the image meta.
            is_video = bool(re.search(r'\[null,\d{3,7},', tail))
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            if is_video:
                # =dv yields the highest-res mp4 download (Google Photos video format)
                items.append({
                    'id': f'gph_{url_hash}',
                    'filename': f'gphoto_{url_hash}.mp4',
                    '_download_url': url + '=dv',
                    '_width': int(width),
                    '_height': int(height),
                    'media_type': 'video',
                })
            else:
                items.append({
                    'id': f'gph_{url_hash}',
                    'filename': f'gphoto_{url_hash}.jpg',
                    '_download_url': url + '=w0',
                    '_width': int(width),
                    '_height': int(height),
                    'media_type': 'photo',
                })

        return items

    def _regex_fallback(self, html: str) -> List[Dict[str, Any]]:
        """Fallback: extract lh3 photo URLs via regex."""
        pattern = r'https://lh3\.googleusercontent\.com/pw/[a-zA-Z0-9_/\-]+'
        raw_urls = list(set(re.findall(pattern, html)))
        items = []
        for url in raw_urls:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
            items.append({
                'id': f'gph_{url_hash}',
                'filename': f'gphoto_{url_hash}.jpg',
                '_download_url': url + '=w0',
                'media_type': 'photo',
            })
        return items

    @staticmethod
    def resolve_album_name(share_url: str) -> str:
        """Fetch the shared album page and extract the album name from <title>."""
        try:
            resp = requests.get(share_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
            })
            match = re.search(r'<title>([^<]+)</title>', resp.text)
            if match:
                name = match.group(1).strip()
                for suffix in [' - Google Photos', ' \u2013 Google Photos']:
                    if name.endswith(suffix):
                        name = name[:-len(suffix)]
                if name and name != 'Google Photos':
                    return name
        except Exception as e:
            logger.warning(f"Failed to resolve Google album name: {e}")
        return ''

    def download_item(self, download_url: str, output_path: Path) -> bool:
        """Download a single image from Google Photos."""
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
            logger.error(f"Failed to download Google photo: {e}")
            return False
