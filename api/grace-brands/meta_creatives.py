from http.server import BaseHTTPRequestHandler
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json
import os
import ssl


DEFAULT_GRAPH_VERSION = "v23.0"
DEFAULT_LIMIT = 25
DEFAULT_MAX_PAGES = 1


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


def env_first(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def normalize_account_id(value):
    value = (value or "").strip()
    if not value:
        return ""
    return value if value.startswith("act_") else f"act_{value}"


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def graph_get(path_or_url, params=None, token="", version=DEFAULT_GRAPH_VERSION):
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
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except Exception:
            payload = {"error": {"message": str(error)}}
        message = payload.get("error", {}).get("message") or str(error)
        raise RuntimeError(message)
    except Exception as error:
        if "CERTIFICATE_VERIFY_FAILED" not in str(error):
            raise
        with urlopen(req, timeout=30, context=ssl._create_unverified_context()) as response:
            return json.loads(response.read().decode("utf-8"))


def first_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def first_item(items):
    return items[0] if isinstance(items, list) and items else {}


def safe_int(value, default, minimum, maximum):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def deep_get(data, *path):
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def extract_video_ids(creative):
    ids = []

    def add(value):
        if value and value not in ids:
            ids.append(str(value))

    add(creative.get("video_id"))
    add(deep_get(creative, "object_story_spec", "video_data", "video_id"))

    asset_feed = creative.get("asset_feed_spec") or {}
    for video in asset_feed.get("videos") or []:
        if isinstance(video, dict):
            add(video.get("video_id"))

    return ids


def extract_image_hashes(creative):
    hashes = []

    def add(value):
        if value and value not in hashes:
            hashes.append(str(value))

    add(creative.get("image_hash"))
    add(deep_get(creative, "object_story_spec", "link_data", "image_hash"))
    add(deep_get(creative, "object_story_spec", "template_data", "image_hash"))
    add(deep_get(creative, "object_story_spec", "video_data", "image_hash"))

    asset_feed = creative.get("asset_feed_spec") or {}
    for image in asset_feed.get("images") or []:
        if isinstance(image, dict):
            add(image.get("hash"))

    for source in (
        deep_get(creative, "object_story_spec", "link_data", "child_attachments"),
        deep_get(creative, "object_story_spec", "template_data", "child_attachments"),
    ):
        for item in source or []:
            if isinstance(item, dict):
                add(item.get("image_hash"))

    return hashes


def get_ad_images(account_id, hashes, token, version):
    if not hashes:
        return {}

    result = {}
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
        except Exception:
            continue

        for image in payload.get("data") or []:
            image_hash = image.get("hash")
            if image_hash:
                result[image_hash] = image
    return result


def get_videos(video_ids, token, version, max_videos=40):
    result = {}
    for video_id in video_ids[:max_videos]:
        try:
            result[video_id] = graph_get(
                f"/{video_id}",
                {"fields": "id,picture,source,permalink_url,title,description,thumbnails{uri,width,height,is_preferred}"},
                token=token,
                version=version,
            )
        except Exception:
            continue
    return result


def chunked(items, size):
    return [items[start:start + size] for start in range(0, len(items), size)]


def fetch_ads_by_ids(ad_ids, token, version):
    ads = []
    for chunk in chunked(ad_ids, 25):
        try:
            payload = graph_get(
                "/",
                {
                    "ids": ",".join(chunk),
                    "fields": AD_FIELDS,
                },
                token=token,
                version=version,
            )
        except Exception:
            continue

        for ad_id in chunk:
            ad = payload.get(ad_id)
            if isinstance(ad, dict):
                ads.append(ad)
    return ads


def is_probably_thumbnail_url(url):
    lowered = (url or "").lower()
    thumbnail_markers = [
        "p64x64",
        "p100x100",
        "p128x128",
        "url_128",
        "c0.5000x0.5000f_dst-emg0",
    ]
    return any(marker in lowered for marker in thumbnail_markers)


def first_non_thumbnail_url(*values):
    for value in values:
        if isinstance(value, str) and value.strip() and not is_probably_thumbnail_url(value):
            return value.strip()
    return ""


def best_video_thumbnail(video):
    thumbnails = (((video or {}).get("thumbnails") or {}).get("data") or [])
    if not isinstance(thumbnails, list):
        return ""

    candidates = []
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


def is_dynamic_catalog_creative(ad, creative):
    haystack = " ".join([
        str(ad.get("name") or ""),
        str(creative.get("name") or ""),
        str(creative.get("title") or ""),
        str(creative.get("body") or ""),
    ]).lower()
    return "{{product." in haystack or "catalog" in haystack or "dpa_" in haystack


def extract_asset_feed_text(asset_feed, key):
    item = first_item(asset_feed.get(key) or [])
    return first_string(item.get("text"), item.get("title"), item.get("body"))


def extract_child_attachments(link_data):
    attachments = []
    for item in link_data.get("child_attachments") or []:
        if not isinstance(item, dict):
            continue
        attachments.append({
            "name": first_string(item.get("name")),
            "description": first_string(item.get("description")),
            "link": first_string(item.get("link")),
            "imageUrl": first_string(item.get("picture"), item.get("image_url")),
            "videoId": first_string(item.get("video_id")),
            "callToAction": first_string(deep_get(item, "call_to_action", "type")),
        })
    return attachments


def normalize_ad(ad, image_by_hash, video_by_id):
    creative = ad.get("creative") or {}
    story_spec = creative.get("object_story_spec") or {}
    link_data = story_spec.get("link_data") or story_spec.get("template_data") or {}
    video_data = story_spec.get("video_data") or {}
    asset_feed = creative.get("asset_feed_spec") or {}

    first_image_hash = first_string(*extract_image_hashes(creative))
    ad_image = image_by_hash.get(first_image_hash) or {}
    first_video_id = first_string(*extract_video_ids(creative))
    video = video_by_id.get(first_video_id) or {}

    primary_text = first_string(
        creative.get("body"),
        link_data.get("message"),
        video_data.get("message"),
        extract_asset_feed_text(asset_feed, "bodies"),
    )
    headline = first_string(
        creative.get("title"),
        link_data.get("name"),
        video_data.get("title"),
        extract_asset_feed_text(asset_feed, "titles"),
        creative.get("name"),
        ad.get("name"),
    )
    description = first_string(
        link_data.get("description"),
        video_data.get("description"),
        extract_asset_feed_text(asset_feed, "descriptions"),
        video.get("description"),
    )
    cta = first_string(
        creative.get("call_to_action_type"),
        deep_get(creative, "call_to_action", "type"),
        deep_get(link_data, "call_to_action", "type"),
        deep_get(video_data, "call_to_action", "type"),
    )
    video_thumbnail_url = best_video_thumbnail(video)
    raw_image_url = first_string(
        ad_image.get("url"),
        ad_image.get("permalink_url"),
        creative.get("image_url"),
        link_data.get("picture"),
        video_data.get("image_url"),
        first_item(asset_feed.get("images") or []).get("url"),
        video_thumbnail_url,
        video.get("picture"),
        creative.get("thumbnail_url"),
    )
    image_url = first_non_thumbnail_url(raw_image_url)
    thumbnail_url = first_non_thumbnail_url(video_thumbnail_url, creative.get("thumbnail_url"), video.get("picture"))

    return {
        "adId": ad.get("id"),
        "name": ad.get("name"),
        "status": ad.get("status"),
        "effectiveStatus": ad.get("effective_status"),
        "campaignId": deep_get(ad, "campaign", "id"),
        "campaignName": deep_get(ad, "campaign", "name"),
        "adSetId": deep_get(ad, "adset", "id"),
        "adSetName": deep_get(ad, "adset", "name"),
        "creativeId": creative.get("id"),
        "creativeName": creative.get("name"),
        "primaryText": primary_text,
        "headline": headline,
        "description": description,
        "callToAction": cta,
        "displayUrl": first_string(creative.get("object_url"), creative.get("link_url"), link_data.get("link")),
        "imageUrl": image_url,
        "thumbnailUrl": thumbnail_url,
        "videoId": first_video_id,
        "videoSourceUrl": first_string(video.get("source")),
        "instagramPermalinkUrl": creative.get("instagram_permalink_url"),
        "effectiveObjectStoryId": creative.get("effective_object_story_id"),
        "objectStoryId": creative.get("object_story_id"),
        "creative": creative,
        "carousel": extract_child_attachments(link_data),
        "mediaMeta": {
            "imageHash": first_image_hash,
            "imageWidth": ad_image.get("width"),
            "imageHeight": ad_image.get("height"),
            "originalWidth": ad_image.get("original_width"),
            "originalHeight": ad_image.get("original_height"),
            "rawImageUrl": raw_image_url,
            "isProbableThumbnail": bool(raw_image_url and is_probably_thumbnail_url(raw_image_url)),
            "isDynamicCatalog": is_dynamic_catalog_creative(ad, creative),
        },
    }


def fetch_ads(account_id, token, version, limit, max_pages, status):
    params = {
        "fields": AD_FIELDS,
        "limit": limit,
    }
    if status == "active":
        params["effective_status"] = json.dumps(["ACTIVE"])

    ads = []
    payload = graph_get(f"/{account_id}/ads", params, token=token, version=version)
    pages = 0

    while True:
        pages += 1
        ads.extend(payload.get("data") or [])
        next_url = (payload.get("paging") or {}).get("next")
        if not next_url or pages >= max_pages:
            break
        payload = graph_get(next_url, token=token, version=version)

    return ads


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        json_response(self, 200, {"ok": True})

    def do_GET(self):
        token = env_first("GRACE_BRANDS_META_ACCESS_TOKEN", "META_ACCESS_TOKEN", "FACEBOOK_ACCESS_TOKEN")
        account_id = normalize_account_id(env_first("GRACE_BRANDS_META_AD_ACCOUNT_ID", "META_AD_ACCOUNT_ID", "FACEBOOK_AD_ACCOUNT_ID"))
        version = env_first("GRACE_BRANDS_META_API_VERSION", "META_API_VERSION") or DEFAULT_GRAPH_VERSION

        if not token or not account_id:
            json_response(self, 503, {
                "error": "config_required",
                "message": "Set GRACE_BRANDS_META_ACCESS_TOKEN and GRACE_BRANDS_META_AD_ACCOUNT_ID to enable Meta creative enrichment.",
                "requiredEnv": ["GRACE_BRANDS_META_ACCESS_TOKEN", "GRACE_BRANDS_META_AD_ACCOUNT_ID"],
            })
            return

        query = parse_qs(urlparse(self.path).query)
        requested_ids = [
            ad_id.strip()
            for raw in (query.get("ids") or [])
            for ad_id in raw.split(",")
            if ad_id.strip()
        ]
        limit = safe_int(first_string(*(query.get("limit") or [])), DEFAULT_LIMIT, 1, 500)
        max_pages = safe_int(first_string(*(query.get("max_pages") or [])), DEFAULT_MAX_PAGES, 1, 20)
        status = first_string(*(query.get("status") or [])) or "active"

        try:
            ads = fetch_ads_by_ids(requested_ids, token, version) if requested_ids else fetch_ads(account_id, token, version, limit, max_pages, status)

            hashes = []
            video_ids = []
            for ad in ads:
                creative = ad.get("creative") or {}
                hashes.extend(extract_image_hashes(creative))
                video_ids.extend(extract_video_ids(creative))

            hashes = list(dict.fromkeys(hashes))
            video_ids = list(dict.fromkeys(video_ids))
            image_by_hash = get_ad_images(account_id, hashes, token, version)
            video_by_id = get_videos(video_ids, token, version)
            normalized = [normalize_ad(ad, image_by_hash, video_by_id) for ad in ads]

            json_response(self, 200, {
                "source": "meta_marketing_api",
                "accountId": account_id,
                "apiVersion": version,
                "count": len(normalized),
                "requestedIds": len(requested_ids),
                "ads": normalized,
            })
        except Exception as error:
            json_response(self, 500, {
                "error": str(error),
                "message": "Meta creative data could not be fetched.",
            })
