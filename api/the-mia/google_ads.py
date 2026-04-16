from http.server import BaseHTTPRequestHandler
import csv
import io
import json
import ssl
from collections import defaultdict
from datetime import datetime
from urllib.request import Request, urlopen

SHEET_KEY = "1TQuXgtu8Gl4i1-csfJgdlFlZjAnZePwVKnpbcj7w7d0"
RSA_GID = "1664202459"  # RSA_Headline_7D
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/export?format=csv&gid={RSA_GID}"


def parse_float(value):
    if value is None:
        return 0.0
    v = str(value).strip()
    if not v:
        return 0.0
    v = v.replace("₺", "").replace("%", "").replace(",", "")
    try:
        return float(v)
    except Exception:
        return 0.0


def parse_rows():
    req = Request(CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=20) as response:
            data = response.read().decode("utf-8")
    except Exception as e:
        # Bazı ortamlarda sertifika zinciri eksik olabiliyor; fallback ile çekiyoruz.
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
        with urlopen(req, timeout=20, context=ssl._create_unverified_context()) as response:
            data = response.read().decode("utf-8")

    if data.startswith("\ufeff"):
        data = data[1:]

    reader = csv.DictReader(io.StringIO(data))
    return list(reader)


def build_assets(rows):
    grouped = defaultdict(lambda: {
        "campaign": "",
        "ad_group": "",
        "ad_id": "",
        "headlines": [],
        "performance_labels": set(),
        "impressions": 0.0,
        "clicks": 0.0,
        "ctr": 0.0,
        "cost": 0.0,
        "conversions": 0.0,
        "conv_value": 0.0,
        "conv_rate": 0.0,
    })

    for row in rows:
        campaign_status = (row.get("Campaign Status") or "").strip().upper()
        ad_group_status = (row.get("Ad Group Status") or "").strip().upper()
        ad_status = (row.get("Ad Status") or "").strip().upper()

        if campaign_status != "ENABLED" or ad_group_status != "ENABLED" or ad_status != "ENABLED":
            continue

        ad_id = (row.get("Ad ID") or "").strip()
        if not ad_id:
            continue

        item = grouped[ad_id]
        item["campaign"] = (row.get("Campaign") or "").strip()
        item["ad_group"] = (row.get("Ad Group") or "").strip()
        item["ad_id"] = ad_id

        headline = (row.get("Headline Text") or "").strip()
        if headline and headline not in item["headlines"]:
            item["headlines"].append(headline)

        perf_label = (row.get("Performance Label") or "").strip()
        if perf_label:
            item["performance_labels"].add(perf_label)

        # Aynı ad için satır bazında tekrar eden metriklerde şişmeyi engellemek için max alıyoruz.
        item["impressions"] = max(item["impressions"], parse_float(row.get("Impressions (7D)")))
        item["clicks"] = max(item["clicks"], parse_float(row.get("Clicks (7D)")))
        item["ctr"] = max(item["ctr"], parse_float(row.get("CTR %")))
        item["cost"] = max(item["cost"], parse_float(row.get("Cost (7D)")))
        item["conversions"] = max(item["conversions"], parse_float(row.get("Conversions (7D)")))
        item["conv_value"] = max(item["conv_value"], parse_float(row.get("Conv. Value (7D)")))
        item["conv_rate"] = max(item["conv_rate"], parse_float(row.get("Conv Rate %")))

    week_key = datetime.utcnow().strftime("%Y-%m-%d")
    assets = []

    for idx, ad in enumerate(grouped.values(), 1):
        cost = ad["cost"]
        conv_value = ad["conv_value"]
        roas = (conv_value / cost) if cost > 0 else 0.0
        cpc = (ad["cost"] / ad["clicks"]) if ad["clicks"] > 0 else 0.0

        assets.append({
            "id": idx,
            "adId": ad["ad_id"],
            "name": f'{ad["campaign"]} / {ad["ad_group"]}',
            "campaignName": ad["campaign"],
            "adGroupName": ad["ad_group"],
            "adType": "Responsive Search Ad",
            "status": "ACTIVE",
            "headlines": ad["headlines"][:15],
            "performanceLabels": sorted(list(ad["performance_labels"])),
            "imageUrl": "",
            "roas": roas,
            "weeklyData": {
                "weeks": [week_key],
                "impressions": [int(round(ad["impressions"]))],
                "clicks": [int(round(ad["clicks"]))],
                "ctr": [round(ad["ctr"], 2)],
                "cost": [round(ad["cost"], 2)],
                "cpc": [round(cpc, 2)],
                "conversions": [round(ad["conversions"], 2)],
                "conv_value": [round(conv_value, 2)],
                "conv_rate": [round(ad["conv_rate"], 2)],
                "roas": [round(roas, 2)],
            },
        })

    assets.sort(key=lambda x: x.get("roas", 0), reverse=True)
    return assets


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        try:
            rows = parse_rows()
            assets = build_assets(rows)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(assets, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": str(e),
                "message": "Google Ads sheet verisi okunamadı",
            }).encode("utf-8"))
