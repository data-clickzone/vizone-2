#!/usr/bin/env python3
"""
Archive The Mia Meta creative assets to Google Drive.

This is a one-shot/backfill utility for pulling the highest quality creative
images we can get from the Meta Marketing API and storing durable copies in
Drive. It intentionally lives outside the Vercel API path so the dashboard can
keep running without Google Drive upload dependencies.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import mimetypes
import os
import re
import ssl
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen


DEFAULT_GRAPH_VERSION = "v23.0"
DEFAULT_LOOKBACK_DAYS = 90
DEFAULT_FOLDER_PREFIX = "The Mia Meta Creative Assets"

CREATIVE_FIELDS = ",".join([
    "id",
    "name",
    "body",
    "title",
    "object_type",
    "object_url",
    "link_url",
    "thumbnail_url",
    "image_url",
    "image_hash",
    "video_id",
    "call_to_action_type",
    "call_to_action",
    "instagram_permalink_url",
    "effective_instagram_media_id",
    "effective_object_story_id",
    "object_story_id",
    "object_story_spec",
    "asset_feed_spec",
])

AD_FIELDS = ",".join([
    "id",
    "name",
    "status",
    "effective_status",
    "campaign{id,name}",
    "adset{id,name}",
    f"creative{{{CREATIVE_FIELDS}}}",
])


@dataclass
class ArchiveRow:
    ad_id: str
    ad_name: str
    ad_status: str
    ad_effective_status: str
    campaign_id: str
    campaign_name: str
    adset_id: str
    adset_name: str
    creative_id: str
    creative_name: str
    asset_role: str
    asset_key: str
    source_url: str
    source_expires_at: str
    width: str
    height: str
    local_path: str
    drive_file_id: str = ""
    drive_view_url: str = ""
    drive_direct_url: str = ""
    upload_status: str = "pending"
    error: str = ""


def env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def normalize_account_id(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return value if value.startswith("act_") else f"act_{value}"


def graph_get(path_or_url: str, params: dict[str, Any] | None, token: str, version: str) -> dict[str, Any]:
    params = dict(params or {})
    if token:
        params["access_token"] = token

    if path_or_url.startswith("http"):
        url = path_or_url
        if params:
            url += ("&" if "?" in url else "?") + urlencode(params)
    else:
        path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
        url = f"https://graph.facebook.com/{version}{path}"
        if params:
            url += "?" + urlencode(params)

    req = Request(url, headers={"User-Agent": "VI-zone/1.0"})
    try:
        with urlopen(req, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except Exception:
            payload = {"error": {"message": str(error)}}
        message = payload.get("error", {}).get("message") or str(error)
        raise RuntimeError(message) from error
    except Exception as error:
        if "CERTIFICATE_VERIFY_FAILED" not in str(error):
            raise
        try:
            with urlopen(req, timeout=45, context=ssl._create_unverified_context()) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as retry_error:
            try:
                payload = json.loads(retry_error.read().decode("utf-8"))
            except Exception:
                payload = {"error": {"message": str(retry_error)}}
            message = payload.get("error", {}).get("message") or str(retry_error)
            raise RuntimeError(message) from retry_error


def paged_graph_get(
    path: str,
    params: dict[str, Any],
    token: str,
    version: str,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    payload = graph_get(path, params, token=token, version=version)
    pages = 0

    while True:
        pages += 1
        rows.extend(payload.get("data") or [])
        next_url = (payload.get("paging") or {}).get("next")
        if not next_url or pages >= max_pages:
            break
        payload = graph_get(next_url, {}, token=token, version=version)

    return rows


def deep_get(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_string(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def add_unique(items: list[str], value: Any) -> None:
    if value and str(value) not in items:
        items.append(str(value))


def extract_image_hashes(creative: dict[str, Any]) -> list[str]:
    hashes: list[str] = []
    add_unique(hashes, creative.get("image_hash"))
    add_unique(hashes, deep_get(creative, "object_story_spec", "link_data", "image_hash"))
    add_unique(hashes, deep_get(creative, "object_story_spec", "template_data", "image_hash"))
    add_unique(hashes, deep_get(creative, "object_story_spec", "video_data", "image_hash"))

    asset_feed = creative.get("asset_feed_spec") or {}
    for image in asset_feed.get("images") or []:
        if isinstance(image, dict):
            add_unique(hashes, image.get("hash"))

    for source in (
        deep_get(creative, "object_story_spec", "link_data", "child_attachments"),
        deep_get(creative, "object_story_spec", "template_data", "child_attachments"),
    ):
        for item in source or []:
            if isinstance(item, dict):
                add_unique(hashes, item.get("image_hash"))

    return hashes


def extract_video_ids(creative: dict[str, Any]) -> list[str]:
    video_ids: list[str] = []
    add_unique(video_ids, creative.get("video_id"))
    add_unique(video_ids, deep_get(creative, "object_story_spec", "video_data", "video_id"))

    asset_feed = creative.get("asset_feed_spec") or {}
    for video in asset_feed.get("videos") or []:
        if isinstance(video, dict):
            add_unique(video_ids, video.get("video_id"))

    return video_ids


def get_ad_images(account_id: str, hashes: list[str], token: str, version: str) -> dict[str, dict[str, Any]]:
    if not hashes:
        return {}

    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(hashes), 100):
        chunk = hashes[start:start + 100]
        try:
            payload = graph_get(
                f"/{account_id}/adimages",
                {
                    "fields": "hash,url,url_128,permalink_url,width,height,original_width,original_height",
                    "hashes": json.dumps(chunk),
                    "limit": len(chunk),
                },
                token=token,
                version=version,
            )
        except Exception as error:
            print(f"adimages lookup skipped for {len(chunk)} hashes: {error}", file=sys.stderr)
            continue

        for image in payload.get("data") or []:
            image_hash = image.get("hash")
            if image_hash:
                result[str(image_hash)] = image

    return result


def get_videos(video_ids: list[str], token: str, version: str, max_videos: int = 120) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for video_id in video_ids[:max_videos]:
        try:
            result[video_id] = graph_get(
                f"/{video_id}",
                {"fields": "id,picture,source,permalink_url,title,description,thumbnails{uri,width,height,is_preferred}"},
                token=token,
                version=version,
            )
        except Exception as error:
            print(f"video lookup skipped for {video_id}: {error}", file=sys.stderr)
            continue
    return result


def chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[start:start + size] for start in range(0, len(items), size)]


def fetch_ads_by_ids(ad_ids: list[str], token: str, version: str) -> list[dict[str, Any]]:
    ads: list[dict[str, Any]] = []
    for chunk in chunked(ad_ids, 25):
        ads.extend(fetch_ads_chunk(chunk, token=token, version=version))
    return ads


def fetch_ads_chunk(ad_ids: list[str], token: str, version: str) -> list[dict[str, Any]]:
    if not ad_ids:
        return []
    try:
        payload = graph_get(
            "/",
            {
                "ids": ",".join(ad_ids),
                "fields": AD_FIELDS,
            },
            token=token,
            version=version,
        )
    except Exception as error:
        if len(ad_ids) == 1:
            print(f"Skipped ad {ad_ids[0]} after creative fetch error: {error}", file=sys.stderr)
            return []
        midpoint = len(ad_ids) // 2
        return (
            fetch_ads_chunk(ad_ids[:midpoint], token=token, version=version)
            + fetch_ads_chunk(ad_ids[midpoint:], token=token, version=version)
        )

    ads: list[dict[str, Any]] = []
    for ad_id in ad_ids:
        ad = payload.get(ad_id)
        if isinstance(ad, dict):
            ads.append(ad)
    return ads


def fetch_last_30d_ad_ids(
    account_id: str,
    token: str,
    version: str,
    since: str,
    until: str,
    max_pages: int,
) -> list[str]:
    rows = paged_graph_get(
        f"/{account_id}/insights",
        {
            "level": "ad",
            "fields": "ad_id,ad_name,impressions,spend",
            "time_range": json.dumps({"since": since, "until": until}),
            "limit": 500,
        },
        token=token,
        version=version,
        max_pages=max_pages,
    )

    ad_ids: list[str] = []
    for row in rows:
        impressions = parse_number(row.get("impressions"))
        spend = parse_number(row.get("spend"))
        if (impressions > 0 or spend > 0) and row.get("ad_id"):
            add_unique(ad_ids, row.get("ad_id"))
    return ad_ids


def parse_number(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except ValueError:
        return 0.0


def sanitize_filename(value: str, fallback: str = "asset") -> str:
    value = re.sub(r"[^\w.-]+", "_", value.strip(), flags=re.UNICODE)
    value = re.sub(r"_+", "_", value).strip("._")
    return (value or fallback)[:120]


def guess_extension(content_type: str, url: str) -> str:
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type:
        extension = mimetypes.guess_extension(content_type)
        if extension:
            return ".jpg" if extension == ".jpe" else extension

    path = urlparse(url).path
    extension = Path(path).suffix.lower()
    if extension in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".mov"}:
        return extension
    return ".jpg"


def parse_meta_url_expiry(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    oe = first_string(*(query.get("oe") or []))
    if not oe:
        return ""
    try:
        expires_at = dt.datetime.fromtimestamp(int(oe, 16), tz=dt.timezone.utc)
    except ValueError:
        return ""
    return expires_at.isoformat().replace("+00:00", "Z")


def asset_candidates(
    ad: dict[str, Any],
    image_by_hash: dict[str, dict[str, Any]],
    video_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    creative = ad.get("creative") or {}
    story_spec = creative.get("object_story_spec") or {}
    link_data = story_spec.get("link_data") or story_spec.get("template_data") or {}
    video_data = story_spec.get("video_data") or {}
    asset_feed = creative.get("asset_feed_spec") or {}
    candidates: list[dict[str, Any]] = []

    for image_hash in extract_image_hashes(creative):
        ad_image = image_by_hash.get(image_hash) or {}
        url = first_string(
            ad_image.get("url"),
            ad_image.get("permalink_url"),
            ad_image.get("url_128"),
        )
        if url:
            candidates.append({
                "role": "image_hash",
                "key": image_hash,
                "url": url,
                "width": first_string(ad_image.get("original_width"), ad_image.get("width")),
                "height": first_string(ad_image.get("original_height"), ad_image.get("height")),
            })

    for index, image in enumerate(asset_feed.get("images") or [], 1):
        if not isinstance(image, dict):
            continue
        url = first_string(image.get("url"))
        if url:
            candidates.append({
                "role": f"asset_feed_image_{index}",
                "key": first_string(image.get("hash"), str(index)),
                "url": url,
                "width": "",
                "height": "",
            })

    for index, child in enumerate(link_data.get("child_attachments") or [], 1):
        if not isinstance(child, dict):
            continue
        url = first_string(child.get("image_url"), child.get("picture"))
        if url:
            candidates.append({
                "role": f"carousel_child_{index}",
                "key": first_string(child.get("image_hash"), child.get("name"), str(index)),
                "url": url,
                "width": "",
                "height": "",
            })

    first_video_id = first_string(*extract_video_ids(creative))
    video = video_by_id.get(first_video_id) or {}
    video_thumbnail_url = best_video_thumbnail(video)

    fallback_key = first_string(creative.get("image_hash"), first_video_id, creative.get("id"), ad.get("id"))
    fallback_url = first_non_thumbnail_url(
        creative.get("image_url"),
        link_data.get("picture"),
        video_data.get("image_url"),
        video_thumbnail_url,
        video.get("picture"),
        creative.get("thumbnail_url"),
    )
    if fallback_url and not any(candidate.get("key") == fallback_key for candidate in candidates):
        candidates.append({
            "role": "creative_fallback",
            "key": fallback_key,
            "url": fallback_url,
            "width": "",
            "height": "",
        })

    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for candidate in candidates:
        if candidate["url"] in seen_urls:
            continue
        seen_urls.add(candidate["url"])
        deduped.append(candidate)

    return deduped


def download_asset(url: str, destination_dir: Path, filename_stem: str) -> tuple[Path, str]:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        response = urlopen(req, timeout=60)
    except Exception as error:
        if "CERTIFICATE_VERIFY_FAILED" not in str(error):
            raise
        response = urlopen(req, timeout=60, context=ssl._create_unverified_context())

    with response:
        content_type = response.headers.get("Content-Type", "")
        extension = guess_extension(content_type, url)
        path = destination_dir / f"{filename_stem}{extension}"
        with path.open("wb") as file:
            file.write(response.read())
        return path, content_type


def is_probably_thumbnail_url(url: str) -> bool:
    lowered = url.lower()
    thumbnail_markers = [
        "p64x64",
        "p100x100",
        "p128x128",
        "url_128",
        "c0.5000x0.5000f_dst-emg0",
    ]
    return any(marker in lowered for marker in thumbnail_markers)


def first_non_thumbnail_url(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip() and not is_probably_thumbnail_url(value):
            return value.strip()
    return ""


def best_video_thumbnail(video: dict[str, Any]) -> str:
    thumbnails = ((video or {}).get("thumbnails") or {}).get("data") or []
    if not isinstance(thumbnails, list):
        return ""

    candidates: list[tuple[int, int, str]] = []
    for item in thumbnails:
        if not isinstance(item, dict):
            continue
        uri = first_string(item.get("uri"))
        if not uri or is_probably_thumbnail_url(uri):
            continue
        try:
            width = int(item.get("width") or 0)
            height = int(item.get("height") or 0)
        except (TypeError, ValueError):
            width = 0
            height = 0
        candidates.append((1 if item.get("is_preferred") else 0, width * height, uri))

    if not candidates:
        return ""
    candidates.sort(reverse=True)
    return candidates[0][2]


def load_drive_service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as error:
        raise RuntimeError(
            "Google Drive dependencies missing. Run: "
            "python3 -m pip install -r requirements-drive.txt"
        ) from error

    scopes = ["https://www.googleapis.com/auth/drive"]
    service_account_json = env_first("GOOGLE_SERVICE_ACCOUNT_JSON", "DESA_GOOGLE_SERVICE_ACCOUNT_JSON")
    service_account_file = env_first("GOOGLE_SERVICE_ACCOUNT_FILE", "DESA_GOOGLE_SERVICE_ACCOUNT_FILE")

    if service_account_json:
        info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    elif service_account_file:
        credentials = service_account.Credentials.from_service_account_file(service_account_file, scopes=scopes)
    else:
        raise RuntimeError(
            "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE for Drive upload."
        )

    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def find_or_create_folder(service: Any, name: str, parent_id: str = "") -> str:
    escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
    query_parts = [
        "mimeType = 'application/vnd.google-apps.folder'",
        f"name = '{escaped_name}'",
        "trashed = false",
    ]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")

    response = service.files().list(
        q=" and ".join(query_parts),
        spaces="drive",
        fields="files(id,name)",
        pageSize=1,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = response.get("files", [])
    if files:
        return files[0]["id"]

    metadata: dict[str, Any] = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        metadata["parents"] = [parent_id]

    folder = service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]


def upload_file(
    service: Any,
    folder_id: str,
    path: Path,
    description: str,
    app_properties: dict[str, str] | None = None,
) -> dict[str, str]:
    from googleapiclient.http import MediaFileUpload

    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True)
    metadata = {
        "name": path.name,
        "parents": [folder_id],
        "description": description[:12000],
    }
    if app_properties:
        metadata["appProperties"] = {
            key: str(value)[:124] for key, value in app_properties.items() if value is not None
        }
    file = service.files().create(
        body=metadata,
        media_body=media,
        fields="id,webViewLink",
        supportsAllDrives=True,
    ).execute()
    file_id = file["id"]
    return {
        "id": file_id,
        "webViewLink": file.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view"),
        "directUrl": f"https://drive.google.com/thumbnail?id={file_id}&sz=w1600",
    }


def list_folder_children(service: Any, folder_id: str) -> list[dict[str, str]]:
    children: list[dict[str, str]] = []
    page_token = None
    while True:
        response = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken,files(id,name,mimeType,webViewLink,appProperties)",
            pageSize=1000,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        children.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return children


def trash_folder_children(service: Any, folder_id: str, folder_name: str) -> int:
    children = list_folder_children(service, folder_id)
    for child in children:
        service.files().update(
            fileId=child["id"],
            body={"trashed": True},
            fields="id",
            supportsAllDrives=True,
        ).execute()
    if children:
        print(f"Trashed {len(children)} existing Drive item(s) in {folder_name}.")
    return len(children)


def trash_children_named(service: Any, folder_id: str, name: str) -> int:
    trashed = 0
    for child in list_folder_children(service, folder_id):
        if child.get("name") != name:
            continue
        service.files().update(
            fileId=child["id"],
            body={"trashed": True},
            fields="id",
            supportsAllDrives=True,
        ).execute()
        trashed += 1
    return trashed


def upload_dedupe_key(row: ArchiveRow) -> str:
    if row.asset_role == "image_hash" and row.asset_key:
        return f"hash:{row.asset_key}"
    if row.asset_role and row.asset_key:
        return f"asset:{row.asset_role}:{row.asset_key}"
    if row.source_url:
        return f"url:{stable_url_key(row.source_url)}"
    return f"file:{row.local_path}"


def stable_url_key(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}"


def drive_record(file: dict[str, Any]) -> dict[str, str]:
    file_id = str(file.get("id") or "")
    return {
        "id": file_id,
        "webViewLink": str(file.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"),
        "directUrl": f"https://drive.google.com/thumbnail?id={file_id}&sz=w1600",
    }


def infer_dedupe_key_from_drive_name(name: str) -> str:
    stem = Path(name).stem
    for marker, prefix in [
        ("_image_hash_", "hash"),
        ("_creative_fallback_", "asset:creative_fallback"),
    ]:
        if marker in stem:
            return f"{prefix}:{stem.rsplit(marker, 1)[1]}"

    for role_prefix in ["asset_feed_image_", "carousel_child_"]:
        match = re.search(rf"_({role_prefix}\d+)_(.+)$", stem)
        if match:
            return f"asset:{match.group(1)}:{match.group(2)}"

    return ""


def build_existing_drive_asset_index(service: Any, folder_id: str) -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for file in list_folder_children(service, folder_id):
        if str(file.get("mimeType") or "").startswith("application/vnd.google-apps."):
            continue
        app_properties = file.get("appProperties") or {}
        dedupe_key = first_string(app_properties.get("dedupeKey"), infer_dedupe_key_from_drive_name(str(file.get("name") or "")))
        if dedupe_key and dedupe_key not in index:
            index[dedupe_key] = drive_record(file)
    return index


def make_public(service: Any, file_id: str) -> None:
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
        fields="id",
        supportsAllDrives=True,
    ).execute()


def write_manifest(path: Path, rows: list[ArchiveRow]) -> None:
    fieldnames = list(ArchiveRow.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: getattr(row, field) for field in fieldnames})


def build_archive_rows(
    ads: list[dict[str, Any]],
    image_by_hash: dict[str, dict[str, Any]],
    video_by_id: dict[str, dict[str, Any]],
    download_dir: Path,
    include_all_assets: bool,
) -> list[ArchiveRow]:
    rows: list[ArchiveRow] = []
    for ad in ads:
        creative = ad.get("creative") or {}
        campaign = ad.get("campaign") or {}
        adset = ad.get("adset") or {}
        candidates = asset_candidates(ad, image_by_hash, video_by_id)
        if not include_all_assets:
            candidates = candidates[:1]

        for index, candidate in enumerate(candidates, 1):
            stem = sanitize_filename(
                "_".join([
                    str(ad.get("id") or "ad"),
                    str(creative.get("id") or "creative"),
                    str(candidate.get("role") or "asset"),
                    str(candidate.get("key") or index),
                ])
            )
            row = ArchiveRow(
                ad_id=str(ad.get("id") or ""),
                ad_name=str(ad.get("name") or ""),
                ad_status=str(ad.get("status") or ""),
                ad_effective_status=str(ad.get("effective_status") or ""),
                campaign_id=str(campaign.get("id") or ""),
                campaign_name=str(campaign.get("name") or ""),
                adset_id=str(adset.get("id") or ""),
                adset_name=str(adset.get("name") or ""),
                creative_id=str(creative.get("id") or ""),
                creative_name=str(creative.get("name") or ""),
                asset_role=str(candidate.get("role") or ""),
                asset_key=str(candidate.get("key") or ""),
                source_url=str(candidate.get("url") or ""),
                source_expires_at=parse_meta_url_expiry(str(candidate.get("url") or "")),
                width=str(candidate.get("width") or ""),
                height=str(candidate.get("height") or ""),
                local_path="",
            )
            if is_probably_thumbnail_url(row.source_url):
                row.upload_status = "skipped_thumbnail"
                row.error = "Skipped probable thumbnail URL; not archived as a high-quality asset."
                rows.append(row)
                continue
            try:
                path, _content_type = download_asset(row.source_url, download_dir, stem)
                row.local_path = str(path)
                row.upload_status = "downloaded"
            except Exception as error:
                row.upload_status = "download_failed"
                row.error = str(error)
            rows.append(row)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Archive recent active The Mia Meta creative images to Google Drive."
    )
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    parser.add_argument("--since", help="YYYY-MM-DD. Overrides --lookback-days start date.")
    parser.add_argument("--until", help="YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--max-insight-pages", type=int, default=20)
    parser.add_argument("--max-ads", type=int, default=0, help="Limit ads for testing. 0 means no limit.")
    parser.add_argument("--folder-name", default="")
    parser.add_argument("--parent-folder-id", default=env_first("THE_MIA_DRIVE_PARENT_FOLDER_ID", "DRIVE_PARENT_FOLDER_ID"))
    parser.add_argument("--output-dir", default="tmp/the-mia-meta-assets")
    parser.add_argument("--include-all-assets", action="store_true", help="Archive carousel/feed images too, not only first asset per ad.")
    parser.add_argument("--share-anyone", action="store_true", help="Make uploaded files public-readable for dashboard image tags.")
    parser.add_argument("--replace-folder-contents", action="store_true", help="Trash existing files/folders inside the selected Drive target folder before uploading.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and download only; skip Drive upload.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = env_first("THE_MIA_META_ACCESS_TOKEN", "META_ACCESS_TOKEN", "FACEBOOK_ACCESS_TOKEN")
    account_id = normalize_account_id(env_first("THE_MIA_META_AD_ACCOUNT_ID", "META_AD_ACCOUNT_ID", "FACEBOOK_AD_ACCOUNT_ID"))
    version = env_first("THE_MIA_META_API_VERSION", "META_API_VERSION") or DEFAULT_GRAPH_VERSION

    if not token or not account_id:
        print("Missing THE_MIA_META_ACCESS_TOKEN and/or THE_MIA_META_AD_ACCOUNT_ID.", file=sys.stderr)
        return 2

    today = dt.date.today()
    until = dt.date.fromisoformat(args.until) if args.until else today
    since = dt.date.fromisoformat(args.since) if args.since else until - dt.timedelta(days=args.lookback_days)
    folder_name = args.folder_name or f"{DEFAULT_FOLDER_PREFIX} {since.isoformat()} to {until.isoformat()}"

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    download_dir = Path(tempfile.mkdtemp(prefix="the-mia-meta-assets-", dir=str(output_dir)))

    print(f"Fetching Meta insight ad ids for {since.isoformat()} to {until.isoformat()}...")
    ad_ids = fetch_last_30d_ad_ids(
        account_id,
        token,
        version,
        since.isoformat(),
        until.isoformat(),
        args.max_insight_pages,
    )
    if args.max_ads and args.max_ads > 0:
        ad_ids = ad_ids[:args.max_ads]

    print(f"Found {len(ad_ids)} ads with impressions/spend in the selected range.")
    if not ad_ids:
        return 0

    ads = fetch_ads_by_ids(ad_ids, token=token, version=version)
    print(f"Fetched creative details for {len(ads)} ads.")

    hashes: list[str] = []
    video_ids: list[str] = []
    for ad in ads:
        creative = ad.get("creative") or {}
        for image_hash in extract_image_hashes(creative):
            add_unique(hashes, image_hash)
        for video_id in extract_video_ids(creative):
            add_unique(video_ids, video_id)

    image_by_hash = get_ad_images(account_id, hashes, token=token, version=version)
    print(f"Resolved {len(image_by_hash)} high-quality ad image records from {len(hashes)} image hashes.")
    video_by_id = get_videos(video_ids, token=token, version=version)
    print(f"Resolved {len(video_by_id)} video records from {len(video_ids)} video ids.")

    rows = build_archive_rows(
        ads=ads,
        image_by_hash=image_by_hash,
        video_by_id=video_by_id,
        download_dir=download_dir,
        include_all_assets=args.include_all_assets,
    )
    downloaded = [row for row in rows if row.upload_status == "downloaded" and row.local_path]
    skipped_thumbnails = [row for row in rows if row.upload_status == "skipped_thumbnail"]
    failed_downloads = [row for row in rows if row.upload_status == "download_failed"]
    print(
        f"Downloaded {len(downloaded)} assets. "
        f"Skipped probable thumbnails: {len(skipped_thumbnails)}. "
        f"Failed downloads: {len(failed_downloads)}."
    )

    service = None
    folder_id = ""
    if not args.dry_run and downloaded:
        service = load_drive_service()
        try:
            folder_id = find_or_create_folder(service, folder_name, args.parent_folder_id)
        except Exception as error:
            if args.parent_folder_id and "File not found" in str(error):
                raise RuntimeError(
                    "Drive parent folder is not visible to the service account. "
                    "Share the parent folder with the service account email, then rerun."
                ) from error
            raise
        print(f"Drive folder ready: https://drive.google.com/drive/folders/{folder_id}")
        if args.replace_folder_contents:
            trash_folder_children(service, folder_id, folder_name)

        uploaded_by_key = build_existing_drive_asset_index(service, folder_id)
        existing_key_count = len(uploaded_by_key)
        if existing_key_count:
            print(f"Loaded {existing_key_count} existing Drive asset key(s) for dedupe.")
        preexisting_keys = set(uploaded_by_key)

        run_unique_keys = {upload_dedupe_key(row) for row in downloaded}
        unique_total = len(run_unique_keys - preexisting_keys)
        unique_index = 0
        for index, row in enumerate(downloaded, 1):
            dedupe_key = upload_dedupe_key(row)
            existing = uploaded_by_key.get(dedupe_key)
            if existing:
                row.drive_file_id = existing["id"]
                row.drive_view_url = existing["webViewLink"]
                row.drive_direct_url = existing["directUrl"]
                row.upload_status = "existing_duplicate" if dedupe_key in preexisting_keys else "uploaded_duplicate"
                print(f"[{index}/{len(downloaded)}] reused existing {Path(row.local_path).name}")
                continue

            path = Path(row.local_path)
            description = json.dumps({
                "source": "meta_marketing_api",
                "accountId": account_id,
                "adId": row.ad_id,
                "creativeId": row.creative_id,
                "assetRole": row.asset_role,
                "assetKey": row.asset_key,
                "sourceExpiresAt": row.source_expires_at,
            }, ensure_ascii=False)
            try:
                unique_index += 1
                uploaded = upload_file(
                    service,
                    folder_id,
                    path,
                    description,
                    app_properties={
                        "dedupeKey": dedupe_key,
                        "source": "meta_marketing_api",
                        "adId": row.ad_id,
                        "creativeId": row.creative_id,
                        "assetRole": row.asset_role,
                        "assetKey": row.asset_key,
                    },
                )
                uploaded_by_key[dedupe_key] = uploaded
                row.drive_file_id = uploaded["id"]
                row.drive_view_url = uploaded["webViewLink"]
                row.drive_direct_url = uploaded["directUrl"]
                row.upload_status = "uploaded"
                if args.share_anyone:
                    make_public(service, row.drive_file_id)
                print(f"[{unique_index}/{unique_total}] uploaded {path.name}")
            except Exception as error:
                row.upload_status = "upload_failed"
                row.error = str(error)
                print(f"[{index}/{len(downloaded)}] upload failed {path.name}: {error}", file=sys.stderr)

    manifest_path = output_dir / f"the_mia_meta_asset_manifest_{since.isoformat()}_{until.isoformat()}.csv"
    write_manifest(manifest_path, rows)
    print(f"Manifest written: {manifest_path}")

    if service and folder_id:
        try:
            trashed_manifests = trash_children_named(service, folder_id, manifest_path.name)
            if trashed_manifests:
                print(f"Trashed {trashed_manifests} existing manifest file(s) named {manifest_path.name}.")
            uploaded = upload_file(
                service,
                folder_id,
                manifest_path,
                "The Mia Meta creative asset archive manifest",
                app_properties={
                    "source": "meta_marketing_api",
                    "manifestSince": since.isoformat(),
                    "manifestUntil": until.isoformat(),
                },
            )
            if args.share_anyone:
                make_public(service, uploaded["id"])
            print(f"Manifest uploaded: {uploaded['webViewLink']}")
        except Exception as error:
            print(f"Manifest upload failed: {error}", file=sys.stderr)

    uploaded_count = sum(1 for row in rows if row.upload_status in {"uploaded", "uploaded_duplicate", "existing_duplicate"})
    uploaded_file_count = sum(1 for row in rows if row.upload_status == "uploaded")
    existing_duplicate_count = sum(1 for row in rows if row.upload_status == "existing_duplicate")
    failed_count = sum(1 for row in rows if row.upload_status.endswith("_failed"))
    print(json.dumps({
        "ads": len(ads),
        "assets": len(rows),
        "downloaded": len(downloaded),
        "uploaded": uploaded_count,
        "uploadedFiles": uploaded_file_count,
        "existingDuplicates": existing_duplicate_count,
        "failed": failed_count,
        "folderId": folder_id,
        "manifest": str(manifest_path),
    }, ensure_ascii=False, indent=2))
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
