# api/kigili/youtube/index.py
# Kigili YouTube Ad Report -> VI zone dashboard JSON
from http.server import BaseHTTPRequestHandler
from urllib.request import urlopen, Request
import csv
import io
import json
import ssl
from datetime import datetime, timedelta
from collections import defaultdict

SHEET_ID = "1rxWe5gdc4r4q7E96v2x1A3RHHQSlzt6VASbBFaieX4E"
GID = "733085847"


def parse_float(value):
    """Para birimi/format karakterlerini temizleyip locale-bağımsız float parse eder."""
    if value is None:
        return 0.0
    v = str(value).strip()
    if v == "" or v.upper() == "NA" or v == "N/A" or v == "__":
        return 0.0

    # Para birimi, yüzde, boşluk vb. temizle
    v = v.replace("₺", "").replace("$", "").replace("€", "").replace("£", "")
    v = v.replace("%", "").replace(" ", "")

    # Karışık locale desteği:
    # - 1.234,56 -> 1234.56
    # - 1,234.56 -> 1234.56
    # - 1.145    -> 1145
    if "," in v and "." in v:
        if v.rfind(",") > v.rfind("."):
            v = v.replace(".", "").replace(",", ".")
        else:
            v = v.replace(",", "")
    elif "," in v:
        v = v.replace(".", "").replace(",", ".")
    elif "." in v:
        if v.count(".") > 1:
            v = v.replace(".", "")
        else:
            left, right = v.split(".", 1)
            if left and right.isdigit() and len(right) == 3:
                v = left + right

    try:
        return float(v)
    except ValueError:
        return 0.0


def parse_percent(value):
    """Yüzde değerlerini 0-100 aralığına normalize eder."""
    if value is None:
        return 0.0
    s = str(value).strip()
    if s == "" or s.upper() == "NA" or s == "N/A" or s == "__":
        return 0.0

    has_percent_sign = "%" in s
    val = parse_float(s)
    if has_percent_sign:
        return val
    return val * 100 if 0 < val <= 1 else val


def parse_int(value):
    try:
        f = parse_float(value)
        return int(round(f))
    except:
        return 0


def parse_week(week_str):
    """Week formatını YYYY-MM-DD formatına çevirir. Örnek: 2025-W18 -> 2025-04-28"""
    if not week_str or week_str.strip() == "":
        return ""
    
    week_str = week_str.strip()
    
    # Eğer zaten YYYY-MM-DD formatındaysa
    if "-" in week_str and len(week_str) >= 8 and week_str[0:4].isdigit():
        parts = week_str.split('-')
        if len(parts) == 3 and len(parts[0]) == 4:  # YYYY-MM-DD
            return week_str[:10]
    
    # 2025-W18 formatını parse et
    if "W" in week_str.upper():
        try:
            parts = week_str.split('-')
            if len(parts) == 2 and parts[0].isdigit():  # 2025-W18
                year = int(parts[0])
                week_num = int(parts[1].upper().replace('W', ''))
                
                # ISO 8601 hafta hesaplama
                jan_4 = datetime(year, 1, 4)
                week_start = jan_4 - timedelta(days=jan_4.weekday())
                target_week_start = week_start + timedelta(weeks=week_num - 1)
                
                return target_week_start.strftime("%Y-%m-%d")
            elif parts[0].upper().startswith('W'):  # W18
                week_num = int(parts[0].upper().replace('W', ''))
                year = 2025
                
                jan_4 = datetime(year, 1, 4)
                week_start = jan_4 - timedelta(days=jan_4.weekday())
                target_week_start = week_start + timedelta(weeks=week_num - 1)
                
                return target_week_start.strftime("%Y-%m-%d")
        except:
            pass
    
    return week_str


def fetch_rows():
    """Sheet'i CSV olarak indirip DictReader ile satır listesi döner."""
    try:
        csv_url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID}"
        req = Request(csv_url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            with urlopen(req, timeout=15) as resp:
                data = resp.read().decode("utf-8")
        except Exception as e:
            if "CERTIFICATE_VERIFY_FAILED" not in str(e):
                raise
            with urlopen(req, timeout=15, context=ssl._create_unverified_context()) as resp:
                data = resp.read().decode("utf-8")
        
        # BOM karakterini temizle
        if data.startswith('\ufeff'):
            data = data[1:]
        
        reader = csv.DictReader(io.StringIO(data))
        rows = list(reader)
        return rows
        
    except Exception as e:
        raise Exception(f"Sheet'e erişilemedi. Lütfen sheet'in public/published olduğundan emin olun. Hata: {str(e)}")


def build_assets(rows):
    """
    Satırları 'VideoTitle' (reklam adı) bazında grupla ve haftalık verilere dönüştür.
    """
    if not rows:
        return []
    
    # Haftalık verileri grupla
    weekly_data = defaultdict(lambda: defaultdict(lambda: {
        "impressions": 0,
        "clicks": 0,
        "ctr": 0.0,
        "cost": 0.0,
        "conversions": 0,
        "conv_value": 0.0,
        "views": 0,
        "view_rate": 0.0,
        "cpm": 0.0,
        "video_25": 0.0,
        "video_50": 0.0,
        "video_75": 0.0,
        "video_100": 0.0,
        "days_count": 0,
        "thumbnail_url": "",
    }))
    asset_meta = {}

    for row in rows:
        # Reklam adı
        ad_name = (row.get("VideoTitle") or "").strip()

        # Hafta bilgisi
        week = parse_week(row.get("Week") or "")
        if not week:
            continue

        # Thumbnail URL - "__" değilse al
        thumbnail_url = (row.get("ThumbnailUrl") or "").strip()
        if thumbnail_url == "__":
            thumbnail_url = ""
        if thumbnail_url.startswith("vid:"):
            thumbnail_url = ""

        # Pivot sheet için anahtar: öncelik görsel URL, yoksa video adı
        asset_key = thumbnail_url or ad_name
        if not asset_key:
            continue

        # Metrikler
        impressions = parse_int(row.get("Impressions"))
        clicks = parse_int(row.get("Clicks"))
        ctr = parse_percent(row.get("CTR"))
        cost = parse_float(row.get("Cost"))
        conversions = parse_int(row.get("Conversions"))
        conv_value = parse_float(row.get("ConversionValue") or row.get("Conversion Value"))
        views = parse_int(row.get("VideoViews") or row.get("Video Views") or row.get("Views"))
        view_rate = parse_percent(row.get("ViewRate") or row.get("View Rate"))
        cpm = parse_float(row.get("AverageCpm") or row.get("Average Cpm") or row.get("CPM"))
        
        # Video completion rates
        video_25 = parse_percent(row.get("VideoQuartile25Rate") or row.get("Video Quartile 25 Rate"))
        video_50 = parse_percent(row.get("VideoQuartile50Rate") or row.get("Video Quartile 50 Rate"))
        video_75 = parse_percent(row.get("VideoQuartile75Rate") or row.get("Video Quartile 75 Rate"))
        video_100 = parse_percent(row.get("VideoQuartile100Rate") or row.get("Video Quartile 100 Rate"))
        
        active_days = parse_int(row.get("ActiveDays") or row.get("Active Days"))
        metric_days = 1 if active_days <= 0 else active_days

        # Haftalık verileri topla
        week_data = weekly_data[asset_key][week]
        week_data["impressions"] += impressions
        week_data["clicks"] += clicks
        week_data["ctr"] += ctr
        week_data["cost"] += cost
        week_data["conversions"] += conversions
        week_data["conv_value"] += conv_value
        week_data["views"] += views
        week_data["view_rate"] += view_rate
        week_data["cpm"] += cpm
        week_data["video_25"] += video_25
        week_data["video_50"] += video_50
        week_data["video_75"] += video_75
        week_data["video_100"] += video_100
        week_data["days_count"] += metric_days
        
        if thumbnail_url and not week_data["thumbnail_url"]:
            week_data["thumbnail_url"] = thumbnail_url
        if asset_key not in asset_meta:
            asset_meta[asset_key] = {
                "name": ad_name or "Untitled Video",
                "thumbnail_url": thumbnail_url,
            }
        else:
            if ad_name and asset_meta[asset_key]["name"] == "Untitled Video":
                asset_meta[asset_key]["name"] = ad_name
            if thumbnail_url and not asset_meta[asset_key]["thumbnail_url"]:
                asset_meta[asset_key]["thumbnail_url"] = thumbnail_url

    # Asset objelerini oluştur
    assets = []
    for idx, (asset_key, weeks_dict) in enumerate(weekly_data.items(), 1):
        weeks = sorted(weeks_dict.keys())
        
        if not weeks:
            continue
        ad_name = asset_meta.get(asset_key, {}).get("name") or "Untitled Video"
        
        # Haftalık dizileri oluştur
        weekly_metrics = {
            "weeks": [],
            "impressions": [],
            "clicks": [],
            "ctr": [],
            "cost": [],
            "cpc": [],
            "conversions": [],
            "conv_value": [],
            "cvr": [],
            "cpa": [],
            "views": [],
            "vtr": [],
            "cpm": [],
            "video_25": [],
            "video_50": [],
            "video_75": [],
            "video_100": [],
        }

        total_impressions = 0
        total_clicks = 0
        total_cost = 0.0
        total_conversions = 0
        total_conv_value = 0.0
        total_views = 0
        thumbnail_url = ""

        for week in weeks:
            week_data = weeks_dict[week]
            
            impressions = week_data["impressions"]
            clicks = week_data["clicks"]
            cost = week_data["cost"]
            conversions = week_data["conversions"]
            conv_value = week_data["conv_value"]
            views = week_data["views"]
            days = week_data["days_count"] if week_data["days_count"] > 0 else 1

            # Ortalama metrikler
            ctr = week_data["ctr"] / days
            view_rate = week_data["view_rate"] / days
            cpm = week_data["cpm"] / days
            video_25 = week_data["video_25"] / days
            video_50 = week_data["video_50"] / days
            video_75 = week_data["video_75"] / days
            video_100 = week_data["video_100"] / days

            # Hesaplanan metrikler
            cpc = (cost / clicks) if clicks > 0 else 0
            cvr = (conversions / clicks * 100) if clicks > 0 else 0
            cpa = (cost / conversions) if conversions > 0 else 0
            vtr = view_rate

            # Haftalık verileri ekle
            weekly_metrics["weeks"].append(week)
            weekly_metrics["impressions"].append(impressions)
            weekly_metrics["clicks"].append(clicks)
            weekly_metrics["ctr"].append(round(ctr, 2))
            weekly_metrics["cost"].append(round(cost, 2))
            weekly_metrics["cpc"].append(round(cpc, 2))
            weekly_metrics["conversions"].append(conversions)
            weekly_metrics["conv_value"].append(round(conv_value, 2))
            weekly_metrics["cvr"].append(round(cvr, 2))
            weekly_metrics["cpa"].append(round(cpa, 2))
            weekly_metrics["views"].append(views)
            weekly_metrics["vtr"].append(round(vtr, 2))
            weekly_metrics["cpm"].append(round(cpm, 2))
            weekly_metrics["video_25"].append(round(video_25, 2))
            weekly_metrics["video_50"].append(round(video_50, 2))
            weekly_metrics["video_75"].append(round(video_75, 2))
            weekly_metrics["video_100"].append(round(video_100, 2))

            # Toplamları hesapla
            total_impressions += impressions
            total_clicks += clicks
            total_cost += cost
            total_conversions += conversions
            total_conv_value += conv_value
            total_views += views
            
            if week_data["thumbnail_url"]:
                thumbnail_url = week_data["thumbnail_url"]
            elif not thumbnail_url:
                thumbnail_url = asset_meta.get(asset_key, {}).get("thumbnail_url", "")

        # Toplam metrikler
        total_ctr = (total_clicks / total_impressions * 100) if total_impressions > 0 else 0
        total_cpc = (total_cost / total_clicks) if total_clicks > 0 else 0
        total_cvr = (total_conversions / total_clicks * 100) if total_clicks > 0 else 0
        total_cpa = (total_cost / total_conversions) if total_conversions > 0 else 0
        total_vtr = (total_views / total_impressions * 100) if total_impressions > 0 else 0
        total_roas = (total_conv_value / total_cost) if total_cost > 0 else 0

        asset = {
            "id": idx,
            "name": ad_name,
            "status": "ACTIVE",
            "imageUrl": thumbnail_url,
            "hasVideo": True,
            "labels": ["video"],
            
            # Toplam değerler
            "impression": total_impressions,
            "click": total_clicks,
            "ctr": round(total_ctr, 2),
            "spend": round(total_cost, 2),
            "cpc": round(total_cpc, 2),
            "conversion": total_conversions,
            "conv_value": round(total_conv_value, 2),
            "cvr": round(total_cvr, 2),
            "cpa": round(total_cpa, 2),
            "roas": round(total_roas, 2),
            "views": total_views,
            "vtr": round(total_vtr, 2),
            
            # Haftalık detay verisi
            "weeklyData": weekly_metrics
        }
        
        assets.append(asset)

    return assets


class handler(BaseHTTPRequestHandler):
    """Vercel Python Function için HTTP handler."""

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
            rows = fetch_rows()
            assets = build_assets(rows)
            body = json.dumps(assets, ensure_ascii=False).encode("utf-8")

            self.send_response(200)
            self._set_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            error = {
                "error": str(e),
                "type": type(e).__name__,
                "sheet_url": CSV_URL
            }
            body = json.dumps(error, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self._set_cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)
