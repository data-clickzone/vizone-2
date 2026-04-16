from http.server import BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.parse import parse_qs, urlparse
import csv
import io
import json
import ssl
import re
from collections import defaultdict
from datetime import datetime


SHEET_ID = "1FEXNn4ogEFWmnx3eAVqpt8A2dn90OcTkT0d9RtpKMnk"
GID = "0"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"


def fetch_rows():
    req = Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=20) as response:
            data = response.read().decode("utf-8-sig")
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        with urlopen(req, timeout=20, context=ssl._create_unverified_context()) as response:
            data = response.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(data)))


def parse_dt(value):
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def normalize_brand(name):
    value = (name or "").strip()
    if not value:
        return ""
    return value

def convert_drive_url(url):
    # Extracts the ID from standard Drive links
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        # Route through Google's high-speed Photo CDN to bypass 429 rate limits
        return f"https://lh3.googleusercontent.com/d/{file_id}=s1000"
    
    match_id = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if match_id:
        file_id = match_id.group(1)
        return f"https://lh3.googleusercontent.com/d/{file_id}=s1000"
        
    return url

def choose_image_url(row):
    drive_url = (row.get("Drive Links") or "").strip()
    raw_url = (row.get("Görsel/İlan Linki") or "").strip()
    
    if drive_url.startswith("http"):
        return convert_drive_url(drive_url)
    if raw_url.startswith("http"):
        return raw_url
    return ""

def build_payload(rows, start_date="", end_date=""):
    grouped = defaultdict(list)
    all_dates = []

    start_dt = parse_dt(start_date) if start_date else None
    end_dt = parse_dt(end_date) if end_date else None

    for row in rows:
        brand = normalize_brand(row.get("Marka"))
        if not brand:
            continue

        dt = parse_dt(row.get("Tarih (UTC+3)"))
        if not dt:
            continue

        date_only = dt.strftime("%Y-%m-%d")
        all_dates.append(date_only)

        if start_dt and dt.date() < start_dt.date():
            continue
        if end_dt and dt.date() > end_dt.date():
            continue

        image_url = choose_image_url(row)
        if not image_url:
            continue

        grouped[brand].append({
            "date": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "image_url": image_url,
            "text": (row.get("Metin") or "").strip(),
            "platforms": [p.strip() for p in (row.get("Platformlar") or "").split(",") if p.strip()],
        })

    brands = []
    for brand_name in sorted(grouped.keys(), key=lambda x: x.lower()):
        ads = sorted(grouped[brand_name], key=lambda x: x["date"], reverse=True)
        if ads:
            brands.append({
                "name": brand_name,
                "ads": ads,
            })

    date_range = {
        "min": min(all_dates) if all_dates else "",
        "max": max(all_dates) if all_dates else "",
    }

    return {"brands": brands, "date_range": date_range}


class handler(BaseHTTPRequestHandler):
    def _set_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._set_cors()
        self.end_headers()

    def do_GET(self):
        try:
            query = parse_qs(urlparse(self.path).query)
            start_date = (query.get("start_date") or [""])[0]
            end_date = (query.get("end_date") or [""])[0]

            rows = fetch_rows()
            payload = build_payload(rows, start_date=start_date, end_date=end_date)
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({
                "error": str(e),
                "message": "Benchmark sheet verisi okunamadi",
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self._set_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
