"""Minimal YouTube Data API client using requests and OAuth refresh."""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from config import Config
from storage import Database, iso

log = logging.getLogger("yta.api")

API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
]


class ApiError(Exception):
    def __init__(self, status: int, reason: str, message: str = ""):
        super().__init__(
            f"YouTube API error {status} {reason}: {message}"[:500])
        self.status, self.reason = status, reason


class QuotaExceeded(ApiError):
    pass


class AuthError(Exception):
    pass


class YouTubeAPI:
    def __init__(self, cfg: Config, db: Database | None = None, session: requests.Session | None = None):
        self.cfg = cfg
        self.db = db
        self.s = session or requests.Session()
        self._token: str | None = None
        self._token_exp = 0.0

    @property
    def has_oauth(self) -> bool:
        return bool(self.cfg.google_client_id and self.cfg.google_client_secret and self.cfg.google_refresh_token)

    def access_token(self, force: bool = False) -> str:
        if not self.has_oauth:
            raise AuthError("Google OAuth not configured")
        if not force and self._token and time.time() < self._token_exp - 60:
            return self._token

        r = self.s.post(
            TOKEN_URL,
            data={
                "client_id": self.cfg.google_client_id,
                "client_secret": self.cfg.google_client_secret,
                "refresh_token": self.cfg.google_refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code != 200 or "access_token" not in j:
            raise AuthError(
                f"OAuth refresh failed: {j.get('error', r.status_code)}")
        self._token = j["access_token"]
        self._token_exp = time.time() + int(j.get("expires_in", 3600))
        return self._token

    def auth_headers(self, oauth: bool) -> dict:
        if oauth or not self.cfg.youtube_api_key:
            return {"Authorization": f"Bearer {self.access_token()}"}
        return {"x-goog-api-key": self.cfg.youtube_api_key}

    def raise_for_error(self, r: requests.Response) -> None:
        if r.status_code < 400:
            return
        try:
            body = r.json()
        except ValueError:
            body = {}
        error = body.get("error", {})
        reason = (error.get("errors") or [{}])[0].get(
            "reason") or error.get("status", "")
        msg = error.get("message", "")
        if reason in {"quotaExceeded", "dailyLimitExceeded"}:
            raise QuotaExceeded(r.status_code, reason, msg)
        raise ApiError(r.status_code, reason or "error", msg)

    def request(self, method: str, resource: str, endpoint: str, params: dict | None = None,
                body: dict | None = None, oauth: bool = False) -> dict:
        params = params or {}
        url = f"{API_BASE}/{resource}"
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                r = self.s.request(
                    method,
                    url,
                    params=params,
                    json=body,
                    headers=self.auth_headers(oauth),
                    timeout=60,
                )
            except requests.RequestException as exc:
                if attempt >= self.cfg.max_retries:
                    raise ApiError(0, "network", str(exc)) from exc
                time.sleep(min(2 ** attempt, 20))
                continue

            if r.status_code == 401 and oauth:
                try:
                    self.access_token(force=True)
                    continue
                except AuthError:
                    pass

            if r.status_code in (429, 500, 502, 503, 504):
                if attempt >= self.cfg.max_retries:
                    self.raise_for_error(r)
                time.sleep(min(2 ** attempt, 30))
                continue

            self.raise_for_error(r)
            return r.json() if r.content else {}
        raise ApiError(0, "unavailable", f"{resource} failed after retries")

    def videos_list(self, ids: list[str], oauth: bool = False) -> list[dict]:
        out = []
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            j = self.request(
                "GET",
                "videos",
                "list",
                {"part": "snippet,contentDetails,statistics,status",
                    "id": ",".join(chunk), "maxResults": 50},
                oauth=oauth,
            )
            out.extend(j.get("items", []))
        return out

    def most_popular(self, region: str, category_id: str | None, max_results: int = 50) -> list[dict]:
        params = {"part": "snippet,contentDetails,statistics", "chart": "mostPopular",
                  "regionCode": region, "maxResults": min(max_results, 50)}
        if category_id:
            params["videoCategoryId"] = category_id
        return self.request("GET", "videos", "list", params).get("items", [])

    def search(self, q: str, region: str, language: str, published_after: str | None, max_results: int = 25) -> list[dict]:
        params = {
            "part": "snippet",
            "q": q,
            "type": "video",
            "order": "viewCount",
            "regionCode": region,
            "relevanceLanguage": language,
            "safeSearch": "strict",
            "maxResults": min(max_results, 50)
        }
        if published_after:
            params["publishedAfter"] = published_after
        return self.request("GET", "search", "list", params).get("items", [])

    def channel_mine(self) -> dict | None:
        j = self.request("GET", "channels", "list", {
                         "part": "id,snippet", "mine": "true"}, oauth=True)
        items = j.get("items", [])
        return items[0] if items else None


def build_auth_url(cfg: Config, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urlencode(params)


def exchange_code(cfg: Config, code: str, redirect_uri: str, session: requests.Session | None = None) -> str:
    s = session or requests.Session()
    r = s.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": cfg.google_client_id,
            "client_secret": cfg.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code != 200 or not body.get("refresh_token"):
        raise AuthError(
            f"Authorization failed: {body.get('error_description', body.get('error', 'unknown'))}")
    return body["refresh_token"]


def authorize_loopback(cfg: Config, port: int = 8080, timeout: int = 300) -> None:
    if not (cfg.google_client_id and cfg.google_client_secret):
        raise AuthError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first")

    state = secrets.token_urlsafe(16)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            # type: ignore[attr-defined]
            self.server.result = {k: v[0] for k, v in q.items()}
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"Authorization complete. You may close this tab.")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.result = None  # type: ignore[attr-defined]
    redirect = f"http://localhost:{server.server_address[1]}"
    url = build_auth_url(cfg, redirect, state)
    print("Open this URL in your browser:\n\n" + url + "\n")
    deadline = time.time() + timeout
    # type: ignore[attr-defined]
    while server.result is None and time.time() < deadline:
        server.handle_request()
    if not server.result:
        raise AuthError("Timed out waiting for OAuth redirect")
    result = server.result  # type: ignore[attr-defined]
    if result.get("state") != state:
        raise AuthError("OAuth state mismatch")
    if "error" in result:
        raise AuthError(f"Authorization denied: {result['error']}")
    token = exchange_code(cfg, result["code"], redirect)
    with open(cfg.env_path, "a+", encoding="utf-8") as f:
        f.write(f"\nGOOGLE_REFRESH_TOKEN={token}\n")
    cfg.google_refresh_token = token
    print("Saved refresh token to .env")


def authorize_manual(cfg: Config, input_fn: Callable[[str], str] = input) -> None:
    if not (cfg.google_client_id and cfg.google_client_secret):
        raise AuthError("Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET first")
    state = secrets.token_urlsafe(16)
    redirect = "http://localhost:8080"
    url = build_auth_url(cfg, redirect, state)
    print("Open this URL, approve access, then paste the redirected URL below:\n")
    print(url)
    pasted = input_fn("Paste the redirected URL: ").strip()
    q = parse_qs(urlparse(pasted).query)
    if "code" not in q:
        raise AuthError("No authorization code found in the pasted URL")
    if q.get("state", [""])[0] != state:
        raise AuthError("OAuth state mismatch")
    token = exchange_code(cfg, q["code"][0], redirect)
    with open(cfg.env_path, "a+", encoding="utf-8") as f:
        f.write(f"\nGOOGLE_REFRESH_TOKEN={token}\n")
    cfg.google_refresh_token = token
    print("Saved refresh token to .env")
