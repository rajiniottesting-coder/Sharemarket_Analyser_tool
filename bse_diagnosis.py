"""
bse_diagnose.py — Run this FIRST to find out why BSE is failing.
Usage: python bse_diagnose.py

This script tests every possible BSE download method and prints
exactly what works and what doesn't on YOUR machine.
"""

import sys
import datetime
import zipfile
import io

print("=" * 60)
print("BSE DOWNLOAD DIAGNOSTICS")
print("=" * 60)

# ── Step 1: Check installed packages ─────────────────────────────
print("\n[1] Checking installed packages...")
packages = {}
for pkg in ['requests', 'curl_cffi', 'bse', 'pandas']:
    try:
        mod = __import__(pkg)
        ver = getattr(mod, '__version__', 'installed')
        print(f"   ✅ {pkg}: {ver}")
        packages[pkg] = True
    except ImportError:
        print(f"   ❌ {pkg}: NOT INSTALLED")
        packages[pkg] = False

# ── Step 2: Test a known BSE date ────────────────────────────────
# Use 2025-04-16 — a Wednesday, confirmed trading day
test_date = datetime.date(2025, 4, 16)
ds6  = test_date.strftime("%d%m%y")   # 160425
ds8  = test_date.strftime("%Y%m%d")   # 20250416
ds8b = test_date.strftime("%d%m%Y")   # 16042025

urls = [
    f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{ds6}_CSV.ZIP",
    f"https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{ds8}_F_0000.CSV.ZIP",
    f"https://www.bseindia.com/download/BhavCopy/Equity/EQ{ds8b}_CSV.ZIP",
]

print(f"\n[2] Testing BSE archive URLs for {test_date}...")

# ── Test with plain requests ──────────────────────────────────────
if packages['requests']:
    import requests as req
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36',
        'Referer': 'https://www.bseindia.com/',
    }
    print("\n   [2a] Plain requests (no session warmup):")
    for url in urls:
        try:
            r = req.get(url, headers=headers, timeout=15)
            print(f"      HTTP {r.status_code} | size={len(r.content)} bytes | {url.split('/')[-1]}")
        except Exception as e:
            print(f"      ERROR: {e} | {url.split('/')[-1]}")

    print("\n   [2b] requests WITH session warmup (visiting BSE homepage first):")
    try:
        s = req.Session()
        s.headers.update(headers)
        s.get("https://www.bseindia.com/markets/equity/eqreports/equitydebcopy.aspx", timeout=12)
        print("      Homepage visited OK")
    except Exception as e:
        print(f"      Homepage visit FAILED: {e}")
    for url in urls:
        try:
            r = s.get(url, timeout=15)
            print(f"      HTTP {r.status_code} | size={len(r.content)} bytes | {url.split('/')[-1]}")
            if r.status_code == 200 and len(r.content) > 500:
                try:
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        print(f"         ZIP contents: {z.namelist()}")
                except Exception as ze:
                    print(f"         ZIP error: {ze}")
        except Exception as e:
            print(f"      ERROR: {e} | {url.split('/')[-1]}")

# ── Test with curl_cffi ───────────────────────────────────────────
if packages['curl_cffi']:
    from curl_cffi import requests as cf_req
    print("\n   [2c] curl_cffi (Chrome TLS impersonation):")
    try:
        s = cf_req.Session(impersonate="chrome124")
        s.get("https://www.bseindia.com/markets/equity/eqreports/equitydebcopy.aspx",
              headers=headers, timeout=12)
        print("      Homepage visited OK")
    except Exception as e:
        print(f"      Homepage visit FAILED: {e}")
    for url in urls:
        try:
            r = s.get(url, headers=headers, timeout=15)
            print(f"      HTTP {r.status_code} | size={len(r.content)} bytes | {url.split('/')[-1]}")
            if r.status_code == 200 and len(r.content) > 500:
                try:
                    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                        csv_files = [f for f in z.namelist() if f.upper().endswith('.CSV')]
                        print(f"         ✅ ZIP OK: {csv_files}")
                        import pandas as pd
                        df = pd.read_csv(z.open(csv_files[0]))
                        print(f"         ✅ CSV rows={len(df)}, cols={list(df.columns[:5])}")
                except Exception as ze:
                    print(f"         ZIP/CSV error: {ze}")
        except Exception as e:
            print(f"      ERROR: {e} | {url.split('/')[-1]}")
else:
    print("\n   [2c] curl_cffi: NOT INSTALLED — this is the fix!")
    print("        Run:  pip install curl_cffi")

# ── Test bse package ─────────────────────────────────────────────
if packages['bse']:
    print("\n   [2d] bse pip package:")
    try:
        import tempfile
        from bse import BSE
        tmp = tempfile.mkdtemp()
        with BSE(download_folder=tmp) as b:
            # Try today - 1 (yesterday)
            yesterday = datetime.date.today() - datetime.timedelta(days=1)
            fp = b.bhavcopyReport(
                date=datetime.datetime.combine(yesterday, datetime.datetime.min.time()),
                folder=tmp
            )
            if fp:
                import pandas as pd
                df = pd.read_csv(fp)
                print(f"      ✅ bse package: {len(df)} rows for {yesterday}")
            else:
                print(f"      ❌ bse package: no file returned for {yesterday}")
    except Exception as e:
        print(f"      ❌ bse package error: {e}")
else:
    print("\n   [2d] bse: NOT INSTALLED — run: pip install bse")

# ── Summary ───────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("SUMMARY & RECOMMENDED FIX:")
if not packages['curl_cffi']:
    print("  → curl_cffi is missing. This is almost certainly the fix.")
    print("    Run:  pip install curl_cffi")
    print("    Then re-run: python backfill_history.py 365")
else:
    print("  → curl_cffi is installed. Check the HTTP status codes above.")
    print("    If you see HTTP 403: BSE is geo-blocking this IP.")
    print("    If you see HTTP 200: the issue is in parsing — share this output.")
print("=" * 60)