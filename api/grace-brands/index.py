# api/desa/index.py
# Desa Meta Ad Report -> VI zone dashboard JSON
from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import csv
from collections import defaultdict

# Google Sheets ayarları - DESA
SHEET_KEY = "1QLq_MAYRYM7OKY71UtwWGrpxQcbDp6aIMpvZLnEcmzQ"
GID = "1416437952"  # Meta_Pivot_AdName_Weekly sekme ID'si

class handler(BaseHTTPRequestHandler):
    
    def do_GET(self):
        try:
            # Google Sheets CSV export URL (published sheets için)
            url = f"https://docs.google.com/spreadsheets/d/{SHEET_KEY}/export?format=csv&gid={GID}"
            
            # CSV verisini çek
            with urllib.request.urlopen(url) as response:
                csv_data = response.read().decode('utf-8')
            
            # CSV'yi parse et
            lines = csv_data.strip().split('\n')
            reader = csv.reader(lines)
            rows = list(reader)
            
            if len(rows) < 2:
                self.send_error(500, "Sheet boş veya hatalı format")
                return
            
            # Parse ve grupla
            assets = self.parse_sheet_data(rows)
            
            # JSON yanıt gönder
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            response_data = json.dumps(assets, ensure_ascii=False)
            self.wfile.write(response_data.encode('utf-8'))
            
        except Exception as e:
            self.send_response(500)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            error_response = json.dumps({
                'error': str(e),
                'message': 'Veri çekme hatası'
            })
            self.wfile.write(error_response.encode('utf-8'))
    
    def parse_sheet_data(self, rows):
        """
        Google Sheets'teki haftalık raw data formatını parse eder
        Verileri Ad Name yerine Ad ID bazında gruplayarak metrik karışmasını önler
        """
        headers = [h.strip() for h in rows[0]]
        col_indices = {}
        
        for idx, header in enumerate(headers):
            header_lower = header.lower()
            
            if 'ad name' in header_lower: col_indices['ad_name'] = idx
            elif 'campaign name' in header_lower: col_indices['campaign_name'] = idx
            elif 'ad set name' in header_lower: col_indices['ad_set_name'] = idx
            elif 'image url' in header_lower: col_indices['image_url'] = idx
            elif header_lower == 'week start' or 'week start' in header_lower: col_indices['week_start'] = idx
            elif header_lower == 'week end' or 'week end' in header_lower: col_indices['week_start'] = idx
            elif header_lower == 'status': col_indices['status'] = idx
            elif 'days live' in header_lower: col_indices['days_live'] = idx
            elif header_lower == 'frequency': col_indices['frequency'] = idx
            
            # THE MISSING IDS
            elif header_lower == 'ad_id' or header_lower == 'ad id': col_indices['ad_id'] = idx
            elif header_lower == 'campaign_id' or header_lower == 'campaign id': col_indices['campaign_id'] = idx
            elif header_lower == 'adset_id' or header_lower == 'adset id': col_indices['adset_id'] = idx
                
            elif header_lower == 'impressions': col_indices['impressions'] = idx
            elif header_lower == 'reach': col_indices['reach'] = idx
            elif header_lower == 'clicks': col_indices['clicks'] = idx
            elif header_lower == 'ctr': col_indices['ctr'] = idx
            elif header_lower == 'cpc': col_indices['cpc'] = idx
            elif header_lower == 'cpm': col_indices['cpm'] = idx
            elif header_lower == 'spend': col_indices['spend'] = idx
            elif 'purchases' in header_lower and 'count' in header_lower: col_indices['purchases'] = idx
            elif 'purchase value' in header_lower: col_indices['revenue'] = idx
            elif 'add to cart' in header_lower and 'count' in header_lower: col_indices['add_to_cart'] = idx
            elif 'view content' in header_lower and 'count' in header_lower: col_indices['view_content'] = idx
            elif 'video plays' in header_lower and 'any' in header_lower: col_indices['video_plays'] = idx
            elif 'video 25' in header_lower: col_indices['video_25'] = idx
            elif 'video 50' in header_lower: col_indices['video_50'] = idx
            elif 'video 75' in header_lower: col_indices['video_75'] = idx
            elif 'video 95' in header_lower: col_indices['video_95'] = idx
            elif 'video avg watch time' in header_lower: col_indices['video_avg_watch'] = idx
            elif 'quality ranking' in header_lower: col_indices['quality_ranking'] = idx
            elif 'engagement rate ranking' in header_lower: col_indices['engagement_ranking'] = idx
            elif 'conversion rate ranking' in header_lower: col_indices['conversion_ranking'] = idx

        if 'ad_name' not in col_indices: raise ValueError("Ad Name sütunu bulunamadı!")
        if 'week_start' not in col_indices: raise ValueError("Week Start/End sütunu bulunamadı!")
        
        grouped_data = defaultdict(lambda: {
            'ad_id': '',
            'name': '',
            'campaign_name': '',
            'ad_set_name': '',
            'status': 'ACTIVE',
            'imageUrl': '',
            'weeks': [],
            'weekly_metrics': []
        })
        
        for row in rows[1:]:
            if len(row) < 5: continue
            
            ad_name = row[col_indices['ad_name']].strip() if 'ad_name' in col_indices and col_indices['ad_name'] < len(row) else ''
            ad_id = row[col_indices['ad_id']].strip() if 'ad_id' in col_indices and col_indices['ad_id'] < len(row) else ''
            
            if not ad_name: continue
            
            # GROUP BY AD_ID TO PREVENT METRIC MIXING (Fallback to ad_name if ID is missing)
            group_key = ad_id if ad_id else ad_name
            
            if not grouped_data[group_key]['name']:
                grouped_data[group_key]['name'] = ad_name
                grouped_data[group_key]['ad_id'] = ad_id
                
                if 'campaign_name' in col_indices and col_indices['campaign_name'] < len(row):
                    grouped_data[group_key]['campaign_name'] = row[col_indices['campaign_name']].strip()
                if 'ad_set_name' in col_indices and col_indices['ad_set_name'] < len(row):
                    grouped_data[group_key]['ad_set_name'] = row[col_indices['ad_set_name']].strip()
                    
                if 'status' in col_indices and col_indices['status'] < len(row):
                    status = row[col_indices['status']].strip()
                    grouped_data[group_key]['status'] = status if status else 'ACTIVE'
                if 'image_url' in col_indices and col_indices['image_url'] < len(row):
                    grouped_data[group_key]['imageUrl'] = row[col_indices['image_url']].strip()
            
            week_start = row[col_indices['week_start']].strip() if 'week_start' in col_indices and col_indices['week_start'] < len(row) else ''
            
            def get_value(key, default=0):
                if key not in col_indices or col_indices[key] >= len(row): return default
                value = row[col_indices[key]].strip().replace('₺', '').replace('%', '').replace(',', '').strip()
                try: return float(value) if value else default
                except ValueError: return default
            
            def get_string(key, default=''):
                if key not in col_indices or col_indices[key] >= len(row): return default
                return row[col_indices[key]].strip()
            
            grouped_data[group_key]['weeks'].append(week_start)
            grouped_data[group_key]['weekly_metrics'].append({
                'impressions': get_value('impressions', 0),
                'reach': get_value('reach', 0),
                'frequency': get_value('frequency', 0),
                'clicks': get_value('clicks', 0),
                'ctr': get_value('ctr', 0),
                'cpc': get_value('cpc', 0),
                'cpm': get_value('cpm', 0),
                'spend': get_value('spend', 0),
                'purchases': get_value('purchases', 0),
                'revenue': get_value('revenue', 0),
                'roas': (get_value('revenue', 0) / get_value('spend', 0)) if get_value('spend', 0) > 0 else 0,
                'add_to_cart': get_value('add_to_cart', 0),
                'view_content': get_value('view_content', 0),
                'video_plays': get_value('video_plays', 0),
                'video_25': get_value('video_25', 0),
                'video_50': get_value('video_50', 0),
                'video_75': get_value('video_75', 0),
                'video_95': get_value('video_95', 0),
                'video_avg_watch': get_value('video_avg_watch', 0),
                'days_live': get_value('days_live', 0),
                'quality_ranking': get_string('quality_ranking', 'UNKNOWN'),
                'engagement_ranking': get_string('engagement_ranking', 'UNKNOWN'),
                'conversion_ranking': get_string('conversion_ranking', 'UNKNOWN')
            })
        
        assets = []
        for idx, (group_key, data) in enumerate(grouped_data.items(), 1):
            if not data['weekly_metrics']: continue
            
            weeks = data['weeks']
            total_spend = sum(m['spend'] for m in data['weekly_metrics'])
            total_revenue = sum(m['revenue'] for m in data['weekly_metrics'])
            total_video_plays = sum(m['video_plays'] for m in data['weekly_metrics'])
            
            ctrs = [m['ctr'] for m in data['weekly_metrics']]
            cpcs = [m['cpc'] for m in data['weekly_metrics']]
            
            # PASS THE REAL META ID SO IMAGES SYNC PERFECTLY
            asset = {
                'id': data['ad_id'] or idx,
                'adId': data['ad_id'],
                'name': data['name'],
                'campaignName': data['campaign_name'],
                'adSetName': data['ad_set_name'],
                'status': data['status'],
                'imageUrl': data['imageUrl'],
                'hasVideo': total_video_plays > 0,
                'labels': ['video'] if total_video_plays > 0 else [],
                
                'impression': int(sum(m['impressions'] for m in data['weekly_metrics'])),
                'reach': int(sum(m['reach'] for m in data['weekly_metrics'])),
                'click': int(sum(m['clicks'] for m in data['weekly_metrics'])),
                'ctr': round(sum(ctrs) / len(ctrs) if ctrs else 0, 2),
                'spend': round(total_spend, 2),
                'purchase': int(sum(m['purchases'] for m in data['weekly_metrics'])),
                'revenue': round(total_revenue, 2),
                'roas': round((total_revenue / total_spend) if total_spend > 0 else 0, 2),
                'add_to_cart': int(sum(m['add_to_cart'] for m in data['weekly_metrics'])),
                'view_content': int(sum(m['view_content'] for m in data['weekly_metrics'])),
                'video_plays': int(total_video_plays),
                
                'weeklyData': {
                    'weeks': weeks,
                    'impressions': [m['impressions'] for m in data['weekly_metrics']],
                    'reach': [m['reach'] for m in data['weekly_metrics']],
                    'frequency': [m['frequency'] for m in data['weekly_metrics']],
                    'clicks': [m['clicks'] for m in data['weekly_metrics']],
                    'ctr': ctrs,
                    'cpc': cpcs,
                    'cpm': [m['cpm'] for m in data['weekly_metrics']],
                    'spend': [m['spend'] for m in data['weekly_metrics']],
                    'purchases': [m['purchases'] for m in data['weekly_metrics']],
                    'revenue': [m['revenue'] for m in data['weekly_metrics']],
                    'roas': [m['roas'] for m in data['weekly_metrics']],
                    'add_to_cart': [m['add_to_cart'] for m in data['weekly_metrics']],
                    'view_content': [m['view_content'] for m in data['weekly_metrics']],
                    'video_plays': [m['video_plays'] for m in data['weekly_metrics']],
                    'video_25': [m['video_25'] for m in data['weekly_metrics']],
                    'video_50': [m['video_50'] for m in data['weekly_metrics']],
                    'video_75': [m['video_75'] for m in data['weekly_metrics']],
                    'video_95': [m['video_95'] for m in data['weekly_metrics']],
                    'video_avg_watch': [m['video_avg_watch'] for m in data['weekly_metrics']],
                    'days_live': [m['days_live'] for m in data['weekly_metrics']],
                    'quality_ranking': [m['quality_ranking'] for m in data['weekly_metrics']],
                    'engagement_ranking': [m['engagement_ranking'] for m in data['weekly_metrics']],
                    'conversion_ranking': [m['conversion_ranking'] for m in data['weekly_metrics']]
                }
            }
            assets.append(asset)
        
        return assets

    def do_OPTIONS(self):
        """CORS için OPTIONS request'i handle et"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
