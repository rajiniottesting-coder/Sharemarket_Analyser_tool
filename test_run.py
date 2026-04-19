"""
test_run.py  —  Force-run the pipeline against last Thursday (2026-04-16)
bypassing the weekend/holiday gate check.

Usage:
    cd /path/to/Sharemarket_Analyser_tool
    python test_run.py

What this does:
  1. Monkey-patches gate_check() to always return "run approved" for 2026-04-16
  2. Calls run_master_pipeline() normally — all real data fetching, DB writes,
     scoring, Excel generation, etc. run exactly as they would in production
  3. Does NOT send emails (patches send_analysis_email to just print)
"""

import datetime
import sys
import os

# ── 0. Force working directory ────────────────────────────────────────────────
os.chdir(os.path.dirname(os.path.abspath(__file__)))

FORCE_DATE = datetime.date(2026, 4, 16)   # Last Thursday (Good Friday was 17th)
print(f"\n{'='*60}")
print(f"  TEST RUN — forcing target_date = {FORCE_DATE} ({FORCE_DATE.strftime('%A')})")
print(f"{'='*60}\n")

# ── 1. Patch gate_check ───────────────────────────────────────────────────────
import ingestion.orchestrator as _orch

_original_gate_check = _orch.gate_check

def _patched_gate_check():
    print(f"[TEST_RUN] gate_check() patched → returning RUN for {FORCE_DATE}")
    return {
        "run":           True,
        "reason":        f"TEST_RUN: forced date {FORCE_DATE}",
        "target_date":   FORCE_DATE,
        "bse_available": True,
        "log":           [{"condition": "TEST_RUN", "status": "PASS",
                           "detail": f"Forced target_date={FORCE_DATE}"}],
    }

_orch.gate_check = _patched_gate_check

# Also patch it in master_funnel's namespace (already imported there)
import master_funnel as _mf
_mf.gate_check = _patched_gate_check   # override the local reference too

# ── 2. Patch email — print only, don't actually send ─────────────────────────
import reporting.email_service as _es

_original_email = _es.send_analysis_email

def _patched_email(**kwargs):
    is_skip  = kwargs.get("is_skip",  False)
    is_error = kwargs.get("is_error", False)
    if is_skip:
        print(f"[TEST_RUN] Email suppressed (skip): {kwargs.get('skip_reason','')}")
    elif is_error:
        print(f"[TEST_RUN] Email suppressed (error): {kwargs.get('error_msg','')}")
    else:
        print("[TEST_RUN] Email suppressed (report ready — attachments not sent)")

_es.send_analysis_email = _patched_email
# Also patch in master_funnel's namespace
# (master_funnel imports it locally inside functions, so we patch the module)
sys.modules["reporting.email_service"].send_analysis_email = _patched_email

# ── 3. Run ────────────────────────────────────────────────────────────────────
print("[TEST_RUN] Starting run_master_pipeline()...\n")
try:
    _mf.run_master_pipeline()
    print("\n[TEST_RUN] ✅ Pipeline completed without unhandled exception")
except Exception as e:
    import traceback
    print(f"\n[TEST_RUN] ❌ Pipeline raised exception:")
    traceback.print_exc()
    sys.exit(1)