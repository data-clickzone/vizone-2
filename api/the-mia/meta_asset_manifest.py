from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import csv
import io
import json
import os
import ssl


DEFAULT_MANIFEST_FILE_ID = "1eUBerJUE44jRZzho-auPhL4rAYgK6dKE"
DRIVE_DOWNLOAD_URL = "https://drive.google.com/uc?export=download&id={file_id}"


def env_first(*names):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def json_response(handler, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def fetch_text(url):
    req = Request(url, headers={"User-Agent": "VI-zone/1.0"})
    try:
        with urlopen(req, timeout=45) as response:
            return response.read().decode("utf-8")
    except HTTPError:
        raise
    except Exception as error:
        if "CERTIFICATE_VERIFY_FAILED" not in str(error):
            raise
        with urlopen(req, timeout=45, context=ssl._create_unverified_context()) as response:
            return response.read().decode("utf-8")


def normalize_row(row):
    drive_file_id = (row.get("drive_file_id") or "").strip()
    drive_direct_url = f"https://drive.google.com/thumbnail?id={drive_file_id}&sz=w1600" if drive_file_id else ""

    return {
        "adId": (row.get("ad_id") or "").strip(),
        "adName": (row.get("ad_name") or "").strip(),
        "campaignId": (row.get("campaign_id") or "").strip(),
        "campaignName": (row.get("campaign_name") or "").strip(),
        "adSetId": (row.get("adset_id") or "").strip(),
        "adSetName": (row.get("adset_name") or "").strip(),
        "creativeId": (row.get("creative_id") or "").strip(),
        "creativeName": (row.get("creative_name") or "").strip(),
        "assetRole": (row.get("asset_role") or "").strip(),
        "assetKey": (row.get("asset_key") or "").strip(),
        "sourceExpiresAt": (row.get("source_expires_at") or "").strip(),
        "width": (row.get("width") or "").strip(),
        "height": (row.get("height") or "").strip(),
        "driveFileId": drive_file_id,
        "driveViewUrl": (row.get("drive_view_url") or "").strip(),
        "driveDirectUrl": drive_direct_url,
        "uploadStatus": (row.get("upload_status") or "").strip(),
    }


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        json_response(self, 200, {"ok": True})

    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        file_id = (
            (query.get("file_id") or [""])[0].strip()
            or env_first("DESA_META_ASSET_MANIFEST_FILE_ID", "META_ASSET_MANIFEST_FILE_ID")
            or DEFAULT_MANIFEST_FILE_ID
        )

        try:
            csv_text = fetch_text(DRIVE_DOWNLOAD_URL.format(file_id=file_id))
            if csv_text.startswith("\ufeff"):
                csv_text = csv_text[1:]

            reader = csv.DictReader(io.StringIO(csv_text))
            rows = []
            for raw_row in reader:
                normalized = normalize_row(raw_row)
                if not (normalized["adId"] or normalized["adName"]):
                    continue
                rows.append(normalized)

            json_response(self, 200, {
                "source": "google_drive_manifest",
                "manifestFileId": file_id,
                "count": len(rows),
                "assets": rows,
            })
        except Exception as error:
            json_response(self, 500, {
                "error": str(error),
                "message": "Meta Drive asset manifest could not be fetched.",
            })
