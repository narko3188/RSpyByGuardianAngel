# Yettel API — Complete Endpoint Map
# Extracted from decompiled APK v2.20 (rs.telenor.mymenu)
# Date: 2026-07-11

## Base URL
https://api.yettel.rs/yettel

## Authentication
- OAuth2 PKCE flow
- Header: X-YETTEL-API-TOKEN: <token>
- Header: Authorization: Bearer <token>
- Token stored in FrontEngine preferences

## Endpoints

### Widgets (GET, auth required)
- /api/widget/1 — Account balance
- /api/widget/3 — Data usage
- /api/widget/4 — Voice/SMS usage
- /api/widget/5 — Roaming status

### Legacy Widgets (GET, auth required)
- /oldyettel/?q=widget — Level 1
- /oldyettel/?q=widget&level=2 — Level 2
- /oldyettel/?q=widget&level=3 — Level 3
- /oldyettel/?q=widget&level=4 — Level 4

### Auth (POST)
- /api/login/init — Login initialization

### Resource (POST, auth required)
- /api/resource?device_identifier=<id> — Device resource

### OAuth2 (POST)
- https://www.yettel.rs/nalog/api/auth
  - grant_type: authorization_code
  - client_id: (from APK)
  - code_verifier: (PKCE)
  - code: (from auth redirect)
  - code_challenge_method: S256
  - redirect_uri: (app-specific)

## Response Codes
- 401.887.7 — Unauthorized (no token)
- 403.887.2 — Forbidden (invalid/expired token)

## Stack
- FrontEngine (PanRobotics) — app framework
- Retrofit + OkHttp — HTTP client
- WebSocket — real-time chat
- Firebase — push notifications

## Secrets (from APK)
- Google API: AIzaSy...YhfA
- Firebase: moj-telenor.firebaseapp.com
- Crashlytics: a21b0954b8c5419b9c8406815598c261
