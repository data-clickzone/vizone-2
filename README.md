# Vizone Dashboard

Vizone is a comprehensive advertising performance dashboard designed to visualize marketing data from various sources (Meta, Google Ads, etc.) in a unified, aesthetically pleasing interface.

## 🎯 Features
- **Performance Overview:** High-level metrics for ad campaigns.
- **Platform Breakdown:** Detailed views for specific platforms (Meta, Google Ads).
- **Competitive Benchmark:** Comparative analysis against competitors (sourced from Google Sheets).
- **Video & Creative Analysis:** Performance metrics for creative assets.

## 🏗 Architecture
- **Frontend:** Single-file HTML/JS/CSS application (`desa-dashboard.html`). Uses vanilla JavaScript and CSS variables for theming.
- **Backend:** Python-based Vercel Serverless Functions (`/api`).
    - **Google Ads:** Validates Oauth2 credentials and fetches live data via Google Ads API (`api/desa/google_ads.py`).
    - **Benchmark:** Fetches competitor tracking data from Google Sheets (`api/desa/benchmark.py`).
    - **Meta/YouTube:** Proxies CSV data associated with `index.py`.

## 🔐 Environment Variables
The following variables must be configured in Vercel for the API to function:

| Variable | Description |
| :--- | :--- |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | API Dev Token (Basic/Standard Access required) |
| `GOOGLE_ADS_CLIENT_ID` | OAuth2 Client ID |
| `GOOGLE_ADS_CLIENT_SECRET` | OAuth2 Client Secret |
| `GOOGLE_ADS_REFRESH_TOKEN` | OAuth2 Refresh Token |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | MCC (Manager) Account ID |
| `GOOGLE_ADS_CUSTOMER_ID` | Target Client Account ID |

## 🛠 Local Development
1. Clone the repository.
2. Install dependencies: `pip install -r requirements.txt`.
3. Set up local env vars (or use `vercel env pull`).
4. Run `vercel dev` to start the local server with API support.
5. Open `http://localhost:3000/vizone/desa/desa-dashboard.html` (note the rewrite path matches production).

## 📁 Structure
- `desa-dashboard.html`: Main dashboard application.
- `api/desa/google_ads.py`: Google Ads API integration.
- `api/desa/benchmark.py`: Benchmark data handler.
- `requirements.txt`: Python dependencies (`google-ads`, `google-auth`, etc.).
- `vercel.json`: Routing and rewrite rules.
