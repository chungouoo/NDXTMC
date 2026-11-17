"""One bounded, unauthenticated Nasdaq connectivity check; no proxies or challenges."""
import hashlib
import http.cookiejar
import json
import re
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

API = "https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks&fromdate=2026-11-13&todate=2026-11-16&limit=10"
PAGE = "https://www.nasdaq.com/market-activity/stocks/aapl/historical"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
jar = http.cookiejar.CookieJar()
opener = build_opener(HTTPCookieProcessor(jar))
results = []

def check(label, url, html=False, content_type=False):
    request = Request(url, headers={**HEADERS, **({"Accept":"text/html"} if html else {}), **({"Content-Type":"application/x-www-form-urlencoded"} if content_type else {})})
    try:
        response = opener.open(request, timeout=20)
    except HTTPError as error:
        response = error
    except Exception as error:
        result = {"check":label,"error_type":type(error).__name__}
        results.append(result)
        print(json.dumps(result), flush=True)
        return False, False
    with response:
        body = response.read(2000000)
        status = response.status
        headers = response.headers
    text = body.decode("utf-8",errors="replace")
    denied = bool(re.search(r"access.denied|verify.you.are.human|captcha|automated.traffic|bot.detection|just.a.moment",text,re.I))
    result = {"check":label,"status":status,"server":headers.get("Server"),"content_type":headers.get("Content-Type"),"response_prefix_sha256":hashlib.sha256(body).hexdigest(),"explicit_block_text":denied}
    if not html and status==200:
        try:
            payload=json.loads(body)
            data=payload.get("data") or {}
            result["symbol"]=data.get("symbol")
            result["rows"]=len((data.get("tradesTable") or {}).get("rows") or [])
        except (ValueError,TypeError):
            result["json_valid"]=False
    if denied:
        result["denial_excerpt"]=re.sub(r"<[^>]+>"," ",text)[:240]
    results.append(result)
    print(json.dumps(result),flush=True)
    return status==200 and (html or result.get("rows",0)>0), denied

FULL_API = "https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks&fromdate=2025-01-02&todate=2026-11-16&limit=5000"
plain_ok, plain_blocked = check("full_history_normal_get", FULL_API)
# Compare the existing production GET header using the same runner and session.
# Explicit challenges are never solved.
header_ok, header_blocked = check("full_history_existing_get_content_type", FULL_API, content_type=True) if not plain_blocked else (False, True)
print(json.dumps({"conclusion":"NASDAQ_NORMAL_GET_AVAILABLE" if plain_ok else "NASDAQ_FULL_HISTORY_UNAVAILABLE_KEEP_YAHOO","normal_get_ok":plain_ok,"existing_header_ok":header_ok,"production_changed":False,"checks":results}),flush=True)
