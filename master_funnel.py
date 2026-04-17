"""
master_funnel.py
SECTION 0–12: Master Pipeline Orchestrator (v7 FINAL)

Key fixes:
- Gate check runs FIRST — nothing else executes before it passes
- target_date comes from gate_check() result (yesterday's trading day)
- save_to_database called with keyword args matching fixed signature
- All stock dict key lookups use lowercase (matching standardize_to_v7_schema)
- Smart money DataFrames safely defaulted to empty DataFrame
- Analysis_Summary_Block_H populated from ai_analysis output
"""

import os
import glob
import datetime
import tempfile
import sys
import pytz
import pandas as pd
from pathlib import Path

# Section 1: System & Data Imports
from orchestrator import gate_check
from harvester import (
    download_nse_bhavcopy,
    download_nse_delivery,
    download_nse_sme_bhavcopy,
    download_nse_fo_participant_data,
)
from data_bridge import (
    save_to_database, check_data_integrity,
    get_historical_quarter_data,
    get_symbol_history, get_nifty_52w_high_from_db,
    get_today_consolidated_data, get_latest_fii_net_cash, get_nifty_200_sma,
    initialize_v7_tables,
)

# Section 0 & 3: Screening & Analytics
from pre_screener import stage_1_filter, stage_2_fundamental_scorer
from priority_ranker import get_top_100_candidates
from v7_analysis_engine import V7AnalysisEngine
from ownership_tracker import analyze_ownership_trends
from forensics_engine import ForensicsEngine
from rotation_engine import SectorRotationRadar
from db_maintenance import enforce_circular_queue
from intel_fetcher import fetch_latest_intelligence

# Section 7 & 8: AI & Formatting
from ai_analyst import get_ai_analysis
from report_formatter import ReportFormatter


def cleanup_temp_files():
    """Section 12: Pre-pipeline physical file cleanup."""
    patterns = ["*.zip", "*.csv", "*.DAT"]
    for pattern in patterns:
        for filepath in glob.glob(pattern):
            try:
                os.remove(filepath)
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# BSE DOWNLOAD — singleton `bse` pip package client
# Replaces ALL direct-URL BSE harvester calls.
# One client opened at pipeline start, reused for bhav + delivery + sme,
# then closed in the finally block.  pip install bse
# ─────────────────────────────────────────────────────────────────────────────

_bse_client  = None
_bse_tmp_dir = None

BSE_COL_MAP = {
    # Classic BSE equity bhav copy columns
    'SC_CODE':    'bse_code',  'SC_NAME':     'symbol',    'SC_GROUP':  'sc_group',
    'OPEN':       'open',      'HIGH':        'high',       'LOW':       'low',
    'CLOSE':      'close',     'PREVCLOSE':   'prev_close', 'NO_OF_SHRS':'volume',
    'NET_TURNOV': 'turnover',  'ISIN_CODE':   'isin',       'LAST':      'last',
    'NO_TRADES':  'num_trades','SC_TYPE':     'sc_type',
    # UDiFF new-format BSE columns (post-2024)
    'FinInstrmId':'bse_code',  'TckrSymb':    'symbol',     'ClsPric':   'close',
    'OpnPric':    'open',      'HghPric':     'high',       'LwPric':    'low',
    'PrvsClsgPric':'prev_close','TtlTradgVol':'volume',     'TtlTrfVal': 'turnover',
    'ISIN':       'isin',
}


def _get_bse_client():
    global _bse_client, _bse_tmp_dir
    if _bse_client is not None:
        return _bse_client
    try:
        from bse import BSE
        _bse_tmp_dir = tempfile.mkdtemp(prefix="bse_live_")
        _bse_client  = BSE(download_folder=_bse_tmp_dir)
        print("✅ BSE client initialised (bse package)")
    except ImportError:
        print("❌ `bse` package not found — run: pip install bse")
        _bse_client = None
    except Exception as e:
        print(f"❌ BSE client init error: {e}")
        _bse_client = None
    return _bse_client


def _close_bse_client():
    global _bse_client, _bse_tmp_dir
    if _bse_client is not None:
        try:
            _bse_client.exit()
        except Exception:
            pass
        _bse_client = None
    if _bse_tmp_dir:
        try:
            import shutil
            shutil.rmtree(_bse_tmp_dir, ignore_errors=True)
        except Exception:
            pass
        _bse_tmp_dir = None


def _parse_bse_df(df):
    """Rename + coerce + filter BSE DataFrame. Returns clean df or None."""
    if df is None or df.empty:
        return None
    df = df.copy()
    df = df.rename(columns=BSE_COL_MAP)
    df.columns = [c.lower().strip() for c in df.columns]
    if 'symbol' not in df.columns and 'sc_name' in df.columns:
        df['symbol'] = df['sc_name']
    for col in ['open', 'high', 'low', 'close', 'volume', 'prev_close', 'turnover']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    for col in ['isin', 'bse_code']:
        if col not in df.columns:
            df[col] = ''
        else:
            df[col] = df[col].fillna('').astype(str).str.strip()
    if 'close' not in df.columns:
        return None
    df = df[df['close'] > 0]
    if 'symbol' in df.columns:
        df['symbol'] = df['symbol'].fillna('').astype(str).str.strip()
        df = df[df['symbol'].str.len() > 0]
    return df.reset_index(drop=True) if not df.empty else None


def _bse_bhav(target_date):
    """BSE equity bhav copy — via bse package (always attempted)."""
    client = _get_bse_client()
    if client is None:
        return None
    try:
        fp = client.bhavcopyReport(
            date=datetime.datetime.combine(target_date, datetime.datetime.min.time()),
            folder=_bse_tmp_dir,
        )
        if fp is None or not Path(fp).exists():
            print(f"⚠️  BSE bhav: not published yet for {target_date}")
            return None
        df = pd.read_csv(fp)
        try:
            os.remove(fp)
        except Exception:
            pass
        parsed = _parse_bse_df(df)
        if parsed is not None:
            print(f"✅ BSE Bhav downloaded: {len(parsed)} records for {target_date}")
        return parsed
    except (RuntimeError, FileNotFoundError):
        return None   # holiday / not yet published — silent
    except Exception as e:
        print(f"⚠️  BSE bhav error {target_date}: {type(e).__name__}: {e}")
        return None


def _bse_delivery(target_date):
    """
    BSE delivery report — via bse package.
    Validates the downloaded file actually contains delivery percentage data.
    If bse.deliveryReport() returns a bhav copy instead (wrong format),
    returns None so the pipeline isn't contaminated with duplicate price rows.
    """
    client = _get_bse_client()
    if client is None:
        return None
    try:
        fp = client.deliveryReport(
            date=datetime.datetime.combine(target_date, datetime.datetime.min.time()),
            folder=_bse_tmp_dir,
        )
        if fp is None or not Path(fp).exists():
            return None
        df = pd.read_csv(fp)
        try:
            os.remove(fp)
        except Exception:
            pass
        df.columns = [c.lower().strip() for c in df.columns]
        # Validate: must have a delivery-percentage column.
        # If the file has OHLC price columns but no delivery column,
        # the bse package returned a bhav copy — discard it.
        _deliv_cols = {"delivery_pct", "deliv_per", "deliv_qty",
                       "net_delivery", "delivery_quantity", "deliveryquantity",
                       "delivery_%", "del_qty", "del_per"}
        if not (_deliv_cols & set(df.columns)):
            return None   # not a delivery file — discard silently
        print(f"✅ BSE Delivery downloaded: {len(df)} records for {target_date}")
        return df
    except (RuntimeError, FileNotFoundError):
        return None
    except Exception as e:
        print(f"⚠️  BSE delivery error {target_date}: {type(e).__name__}: {e}")
        return None


def _bse_sme(target_date):
    """BSE SME bhav — best-effort via harvester (non-critical)."""
    try:
        from harvester import download_bse_sme_bhavcopy
        return download_bse_sme_bhavcopy(target_date)
    except Exception:
        return None


def _sf(val, default=0.0):
    """Safe float — handles '—', None, '', and non-numeric strings."""
    if val is None or val == "" or val == "—" or val == "--":
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)


def run_master_pipeline():
    cleanup_temp_files()

    import sqlite3
    conn = sqlite3.connect("market_data.db")
    initialize_v7_tables(conn)
    conn.close()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 12B — GATE CHECK FIRST
    # Must pass ALL 6 conditions before ANY download, DB write, or analysis.
    # target_date = yesterday (next-morning schedule per master prompt v7)
    # ═══════════════════════════════════════════════════════════════════════════
    gate_result = gate_check()

    if not gate_result["run"]:
        from email_service import send_analysis_email
        print(f"🛑 Pipeline Halted: {gate_result['reason']}")
        try:
            send_analysis_email(is_skip=True, skip_reason=gate_result["reason"])
        except Exception as e:
            print(f"⚠️  Skip notification failed: {e}")
        return

    # Gate passed — extract the target trading date.
    # NOTE: gate_result["bse_available"] is IGNORED — gate C4 tests a direct
    # URL that cloud/GitHub-Actions IPs cannot reach. BSE always runs via
    # the `bse` pip package which handles Akamai auth internally.
    target_date = gate_result["target_date"]   # datetime.date (yesterday)
    print(f"✅ Gate passed. Processing trading day: {target_date}")

    try:
        # ─────────────────────────────────────────────────────────────────────
        # SECTION 1: MULTI-STREAM HARVESTING
        # ─────────────────────────────────────────────────────────────────────
        print("🚀 [Section 1] Harvesting Market Streams...")
        # Open BSE client once — reused for bhav, delivery, sme
        _get_bse_client()
        raw_nse   = download_nse_bhavcopy(target_date)
        raw_bse   = _bse_bhav(target_date)        # bse package — always attempted
        nse_deliv = download_nse_delivery(target_date)
        bse_deliv = _bse_delivery(target_date)    # bse package — always attempted
        sme_nse   = download_nse_sme_bhavcopy(target_date)
        sme_bse   = _bse_sme(target_date)         # best-effort
        fo_data   = download_nse_fo_participant_data(target_date)
        # Determine actual BSE availability for run_stats logging
        bse_available = isinstance(raw_bse, pd.DataFrame) and not raw_bse.empty
        print(f"   NSE: {'✅' if raw_nse is not None else '❌'}  "
              f"BSE: {'✅' if bse_available else '⚠️ NSE-only'}  "
              f"NSE-Deliv: {'✅' if nse_deliv is not None else '⚠️'}  "
              f"BSE-Deliv: {'✅' if bse_deliv is not None else '⚠️'}")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 12B C5 — DATA INTEGRITY CHECK (post-download)
        # ─────────────────────────────────────────────────────────────────────
        integrity = check_data_integrity(raw_nse, raw_bse)
        if not integrity["pass"]:
            reason = f"C5 FAIL: {integrity['message']}"
            print(f"🛑 {reason}")
            from email_service import send_analysis_email
            send_analysis_email(is_skip=True, skip_reason=reason)
            return
        print(f"✅ C5 PASS: {integrity['message']}")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 3J & 3K: SMART MONEY HARVEST
        # ─────────────────────────────────────────────────────────────────────
        print("🕵️  [Section 3J/K] Scraping Bulk Deals & Insider Trades...")
        bulk_deals_df   = pd.DataFrame()
        insider_trades_df = pd.DataFrame()
        try:
            from smart_money import SmartMoneyScraper
            scraper = SmartMoneyScraper()
            result_bulk = scraper.fetch_nse_bulk_deals()
            result_insider = scraper.fetch_sast_insider_trading()
            if result_bulk is not None:
                bulk_deals_df = result_bulk
            if result_insider is not None:
                insider_trades_df = result_insider
        except Exception as e:
            print(f"⚠️  Smart Money warning: {e}")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 1.2: DATABASE SYNC
        # ─────────────────────────────────────────────────────────────────────
        save_to_database(
            nse_data=raw_nse, bse_data=raw_bse,
            nse_del=nse_deliv, bse_del=bse_deliv,
            sme_nse=sme_nse, sme_bse=sme_bse,
            participant_data=fo_data,
        )

        if not bulk_deals_df.empty:
            save_to_database(df=bulk_deals_df, table="bulk_deals")
        if not insider_trades_df.empty:
            save_to_database(df=insider_trades_df, table="insider_trades")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 0: PRE-SCREENING FUNNEL (STAGES 1–3)
        # ─────────────────────────────────────────────────────────────────────
        print("🔍 [Section 0] Executing Funnel Stages 1–3...")
        all_stocks = get_today_consolidated_data(
            target_date,
            nse_main=raw_nse, nse_sme=sme_nse,
            bse_main=raw_bse, bse_sme=sme_bse,
            nse_deliv=nse_deliv, bse_deliv=bse_deliv,
        )

        if all_stocks.empty:
            print("❌ Consolidation produced empty DataFrame. Aborting.")
            return

        stage1_candidates = stage_1_filter(all_stocks.to_dict("records"))
        stage2_qualified  = stage_2_fundamental_scorer(pd.DataFrame(stage1_candidates))
        final_100_df      = get_top_100_candidates(stage2_qualified)
        final_100_list    = final_100_df.to_dict("records")

        print(f"   Universe: {len(all_stocks)} → Stage1: {len(stage1_candidates)} "
              f"→ Stage2: {len(stage2_qualified)} → Stage3: {len(final_100_list)}")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 6: CORE ANALYTICAL ENGINES
        # ─────────────────────────────────────────────────────────────────────
        v7_engine   = V7AnalysisEngine()
        forensics   = ForensicsEngine()
        rotation    = SectorRotationRadar()
        formatter   = ReportFormatter()
        historical_map = get_historical_quarter_data(
            [s.get("symbol", "") for s in final_100_list]
        )

        for stock in final_100_list:
            sym = stock.get("symbol", "")

            # Section 2: Latest Intelligence
            stock["intel_queries"] = fetch_latest_intelligence(
                sym, stock.get("sector", "")
            )

            # Section 3J: Bulk Deal Sentiment
            if not bulk_deals_df.empty and "symbol" in bulk_deals_df.columns:
                deals = bulk_deals_df[bulk_deals_df["symbol"] == sym]
                buy_vol  = deals[deals.get("type", pd.Series()) == "BUY"]["quantity"].sum() if "type" in deals.columns else 0
                sell_vol = deals[deals.get("type", pd.Series()) == "SELL"]["quantity"].sum() if "type" in deals.columns else 0
                stock["net_inst_flow"]       = buy_vol - sell_vol
                stock["smart_money_sentiment"] = "ACCUMULATION" if stock["net_inst_flow"] > 0 else "NEUTRAL"
            else:
                stock["net_inst_flow"]       = 0
                stock["smart_money_sentiment"] = "NEUTRAL"

            # Section 3K: Insider Buying
            if not insider_trades_df.empty and "symbol" in insider_trades_df.columns:
                ins_buys = insider_trades_df[
                    (insider_trades_df["symbol"] == sym) &
                    (insider_trades_df.get("mode", pd.Series()) == "Market Purchase")
                ] if "mode" in insider_trades_df.columns else pd.DataFrame()
                stock["insider_buy_alert"] = "YES" if not ins_buys.empty else "NO"
            else:
                stock["insider_buy_alert"] = "NO"

            # Section 3A/3C: Valuation & Growth
            stock.update(v7_engine.apply_section_3A_valuation(stock))
            stock.update(v7_engine.apply_section_3C_growth(stock))

            # Section 3B/3D/3G: Forensics
            stock.update(forensics.calculate_accounting_forensics(stock))

            # Section 3E: Capital Allocation
            roce = stock.get("roce", 0) or 0
            wacc = 11.5
            stock["wealth_creation_spread"] = roce - wacc
            stock["allocation_tag"] = "WEALTH CREATOR" if roce > wacc else "VALUE ERODER"

            # Section 3F: Ownership Trends
            hist = historical_map.get(sym)
            stock.update(analyze_ownership_trends(stock, hist))

            # Section 3H: Anti-Trigger Guard
            guard = v7_engine.apply_section_3H_guards(stock)
            stock["spike_suppressed"] = guard["suppressed"]
            stock["guard_reasons"]    = ", ".join(guard["reasons"])

            # Section 3I: Early Entry Score deferred to Section 6 scoring loop
            # (vol_ratio, rsi, supertrend, 2w_chg are not available yet in this pass)
            stock.setdefault("early_entry_score", 0)
            stock.setdefault("early_mover_badge", "")
            stock.setdefault("early_label", "EMERGING")

            # Section 3L: Sector Rotation Stage — use 4w_chg + FII trend
            _sec_ret   = _sf(stock.get("4w_chg", 0), 0)
            _nft_ret   = 0.0
            _rsi_val   = _sf(stock.get("rsi", 50), 50)
            _fii_trend = str(stock.get("fii_3q_trend", "NEUTRAL"))
            if   _fii_trend == "UP"   and _rsi_val > 55: _fii_flow = "turning_positive"
            elif _fii_trend == "UP":                      _fii_flow = "positive"
            elif _sec_ret < -2.0:                         _fii_flow = "decreasing"
            else:                                         _fii_flow = "neutral"
            stock["rotation_stage"] = rotation.calculate_rotation_stage(
                _sec_ret, _nft_ret, _fii_flow
            )

            # Ensure selection_reason is present for all stocks
            if not stock.get("selection_reason"):
                cap = str(stock.get("cap_category","") or "")
                d   = float(stock.get("delivery_pct", 0) or 0)
                v   = float(stock.get("vol_ratio", 1.0) or 1.0)
                parts = []
                if "LARGE" in cap.upper(): parts.append("Large-cap institutional quality")
                elif "MID" in cap.upper(): parts.append("Mid-cap growth candidate")
                else: parts.append("Small/micro-cap high-growth candidate")
                if d >= 65: parts.append(f"strong institutional delivery {d:.0f}%")
                if v >= 2.0: parts.append(f"volume surge {v:.1f}× avg")
                stock["selection_reason"] = "; ".join(parts) or "Passed quality filters"

            # Section 4: Balance Sheet Health — fed with yfinance data
            from bs_engine import BalanceSheetEngine
            _debt_bs  = _sf(stock.get("total_debt", stock.get("total_debt_cr", 0)), 0)
            _cash_bs  = _sf(stock.get("cash", stock.get("cash_cr", 0)), 0)
            _de_bs    = _sf(stock.get("debt_equity", 0), 0)
            _cr_bs    = _sf(stock.get("current_ratio", 0), 0)
            _fcf_bs   = _sf(stock.get("fcf", stock.get("fcf_cr", 0)), 0)
            _roe_bs   = _sf(stock.get("roe", 0), 0)
            _pb_bs    = _sf(stock.get("pb", 0), 0)
            _cmp_bs   = _sf(stock.get("close", 0), 0)
            _nw_bs    = round(_cmp_bs / _pb_bs, 2) if _pb_bs > 0 and _cmp_bs > 0 else 1

            current_bs_dict = {
                "total_debt":             _debt_bs,
                "cash_equivalents":       _cash_bs,
                "networth":               max(_nw_bs, 1),
                "roe":                    _roe_bs,
                "dio": 0, "dso": 0, "cwip": 0, "net_block": 1,
                "goodwill": 0, "contingent_liabilities": 0,
                "st_borrowings":  _debt_bs * 0.4 if _de_bs > 1.5 else 0,
                "lt_borrowings":  _debt_bs * 0.6 if _debt_bs > 0 else 0,
                "cfo_pat_2q_low": _fcf_bs < 0,
            }
            hist_q = historical_map.get(sym) or {}
            bs_report = BalanceSheetEngine().analyze_bs_health(current_bs_dict, hist_q)

            # Add quick yfinance-driven flags
            _extra_flags = []
            if _de_bs > 2.0:       _extra_flags.append(f"HIGH D/E {round(_de_bs,1)}x")
            if 0 < _cr_bs < 1.0:   _extra_flags.append(f"LOW LIQUIDITY {round(_cr_bs,2)}")
            if _fcf_bs < 0:        _extra_flags.append("NEGATIVE FCF")
            if _debt_bs > 0 and _cash_bs > 0:
                _cov = _cash_bs / _debt_bs
                if _cov < 0.1:     _extra_flags.append(f"LOW CASH COVER {round(_cov,2)}x")

            _all_flags = bs_report.get("flags", []) + _extra_flags
            _status    = bs_report.get("status", "HEALTHY")
            if _extra_flags and _status == "HEALTHY": _status = "WATCH"

            _cover_s = f"{round(_cash_bs/_debt_bs,2)}x" if _debt_bs > 0 else "N/A"
            _de_s    = f"D/E {round(_de_bs,1)}x" if _de_bs > 0 else ""
            stock["bs_status"] = _status
            stock["bs_flags"]  = ", ".join(_all_flags) if _all_flags else "No red flags detected"
            stock["bs_output"] = (
                f"BS:{_status} | Cash ₹{int(_cash_bs)}Cr vs Debt ₹{int(_debt_bs)}Cr"
                f" | Cover {_cover_s} | FCF:{'↓' if _fcf_bs < 0 else '↑'}"
                + (f" | {_de_s}" if _de_s else "")
                + (f" | {', '.join(_all_flags)}" if _all_flags else " | No flags")
            )

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 4B: NSE FUNDAMENTALS REFRESH (before DB enrichment reads)
        # Fetch PE, EPS, sector, company_name, promoter%, FII%, DII%
        # for today's top-100 NSE stocks via free NSE API (~2 min)
        # Must run BEFORE Section 5 so DB is populated when we read it
        # ─────────────────────────────────────────────────────────────────────
        print("\n🏦 [Section 4B] Refreshing NSE fundamentals for top-100 stocks...")
        try:
            from backfill_history import fetch_nse_fundamentals
            import sqlite3 as _sq_pre
            _conn_pre = _sq_pre.connect("market_data.db")
            _nse_syms = [s.get("symbol","") for s in final_100_list
                        if s.get("exchange_tag","") not in ("BSE_SME","BSE_ONLY")
                        and s.get("symbol","")]
            fetch_nse_fundamentals(_conn_pre, _nse_syms, max_symbols=100)
            _conn_pre.close()
            print(f"   ✅ NSE fundamentals refreshed for {len(_nse_syms)} stocks")
        except Exception as _epre:
            print(f"   ⚠️  NSE fundamentals refresh: {_epre}")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 5: WEEKLY MOMENTUM DELTAS + DB ENRICHMENT
        # Pull 52w high/low, day_chg, technical indicators, company_name
        # from tables already populated by backfill_history.py + Section 4B
        # ─────────────────────────────────────────────────────────────────────
        print("📈 [Section 5] Calculating Weekly Momentum + DB Enrichment...")
        import sqlite3 as _sq
        _db = "market_data.db"
        _date_str = target_date.strftime("%Y-%m-%d")

        # Bulk-load technical indicators for all 100 symbols in one query
        _syms = [s.get("symbol","") for s in final_100_list]
        _sym_placeholders = ",".join(["?"]*len(_syms))
        _ti_map = {}
        _sm_map = {}   # symbol_master: company_name, sector, cap_category
        _dp_map = {}   # daily_prices extra cols: day_chg_pct, 52w high/low
        try:
            _conn = _sq.connect(_db)
            # Fundamental metrics (latest per symbol)
            _fm_map = {}
            try:
                _fm_rows = _conn.execute(
                    f"""SELECT fm.symbol,
                        fm.pe_ttm, fm.pb, fm.earn_yield,
                        COALESCE(fm.div_yield, 0)      as div_yield,
                        0 as piotroski_f, 0 as altman_z, 0 as beneish_m,
                        fm.roe, fm.roce, fm.roa,
                        fm.gross_margin, fm.ebitda_margin, fm.net_margin,
                        fm.de_ratio, fm.current_ratio,
                        COALESCE(fm.rev_cagr_1y, 0)   as rev_cagr_1y,
                        COALESCE(fm.rev_cagr_3y, 0)   as rev_cagr_3y,
                        COALESCE(fm.pat_cagr_1y, 0)   as pat_cagr_1y,
                        COALESCE(fm.pat_cagr_3y, 0)   as pat_cagr_3y,
                        fm.total_debt_cr, fm.fcf_cr,
                        fm.nd_ebitda, fm.int_coverage,
                        fm.ps, fm.ev_ebitda, fm.peg,
                        fm.quick_ratio, fm.cash_cr, fm.fcf_yield,
                        COALESCE(fm.rev_yoy, 0)        as rev_yoy,
                        COALESCE(fm.pat_yoy, 0)        as pat_yoy,
                        COALESCE(fm.payout_ratio, 0)   as payout_ratio
                        FROM fundamental_metrics fm
                        INNER JOIN (
                            SELECT symbol, MAX(date) as md FROM fundamental_metrics
                            WHERE symbol IN ({_sym_placeholders}) GROUP BY symbol
                        ) lt ON fm.symbol=lt.symbol AND fm.date=lt.md""",
                    _syms
                ).fetchall()
                for r in _fm_rows:
                    _fm_map[r[0]] = r[1:]
            except Exception:
                pass

            # Shareholding (latest per symbol)
            _sh_map = {}
            try:
                _sh_rows = _conn.execute(
                    f"""SELECT sh.symbol, sh.promoter_pct, sh.promoter_qoq,
                        sh.pledge_pct, sh.pledge_dir, sh.fii_pct, sh.fii_qoq,
                        sh.dii_pct, sh.dii_qoq, sh.public_float
                        FROM shareholding sh
                        INNER JOIN (
                            SELECT symbol, MAX(date) as md FROM shareholding
                            WHERE symbol IN ({_sym_placeholders}) GROUP BY symbol
                        ) lt ON sh.symbol=lt.symbol AND sh.date=lt.md""",
                    _syms
                ).fetchall()
                for r in _sh_rows:
                    _sh_map[r[0]] = r[1:]
            except Exception:
                pass

            # Technical indicators (latest date per symbol)
            _ti_rows = _conn.execute(
                f"""SELECT t.symbol, t.sma_200, t.supertrend, t.adx, t.rsi_14,
                    t.macd_signal_txt, t.stoch_k, t.mfi_14, t.obv_signal,
                    t.above_vwap, t.support1, t.support2, t.resist1, t.resist2
                    FROM technical_indicators t
                    INNER JOIN (
                        SELECT symbol, MAX(date) as md FROM technical_indicators
                        WHERE symbol IN ({_sym_placeholders}) GROUP BY symbol
                    ) latest ON t.symbol=latest.symbol AND t.date=latest.md""",
                _syms
            ).fetchall()
            for r in _ti_rows:
                _ti_map[r[0]] = r[1:]

            # Symbol master: company_name, sector, cap_category, updated_on(eps/mcap tag)
            _sm_rows = _conn.execute(
                f"SELECT symbol, company_name, sector, cap_category, updated_on FROM symbol_master "
                f"WHERE symbol IN ({_sym_placeholders})", _syms
            ).fetchall()
            for r in _sm_rows:
                _sm_map[r[0]] = r[1:]

            # 52w high/low and vol50d from full price history (no date filter)
            try:
                _dp_rows = _conn.execute(
                    f"""SELECT dp.symbol,
                        MAX(CASE WHEN dp.date >= date(?, '-365 days') THEN dp.high  ELSE NULL END),
                        MIN(CASE WHEN dp.date >= date(?, '-365 days') THEN dp.low   ELSE NULL END),
                        AVG(CASE WHEN dp.date >= date(?, '-50 days')  THEN dp.volume ELSE NULL END)
                        FROM daily_prices dp
                        WHERE dp.symbol IN ({_sym_placeholders}) AND dp.exchange='NSE'
                        GROUP BY dp.symbol""",
                    [_date_str, _date_str, _date_str] + _syms
                ).fetchall()
                for r in _dp_rows:
                    _dp_map[r[0]] = r[1:]
            except Exception:
                pass

            _conn.close()
        except Exception as _e:
            print(f"⚠️  DB enrichment warning: {_e}")

        for stock in final_100_list:
            sym = stock.get("symbol", "")
            history = get_symbol_history(sym)

            # Weekly momentum from price history
            if not history.empty:
                curr = float(history.iloc[-1]["close"])
                def _chg(n):
                    if len(history) >= n:
                        base = float(history.iloc[-n]["close"])
                        return round((curr - base) / base * 100, 2) if base > 0 else 0
                    return 0
                stock["2w_chg"] = _chg(11)
                stock["4w_chg"] = _chg(21)
                stock["6w_chg"] = _chg(31)
                stock["8w_chg"] = _chg(41)
            else:
                for k in ["2w_chg", "4w_chg", "6w_chg", "8w_chg"]:
                    stock[k] = 0

            # Beta — read from weekly_momentum.beta_90d (computed by backfill)
            # Falls back to volatility-based estimate if not available
            if not stock.get("beta") or stock.get("beta") == "—":
                try:
                    import sqlite3 as _sq2
                    _bc = _sq2.connect("market_data.db")
                    _beta_row = _bc.execute(
                        """SELECT beta_90d FROM weekly_momentum
                           WHERE symbol=? ORDER BY date DESC LIMIT 1""", (sym,)
                    ).fetchone()
                    _bc.close()
                    if _beta_row and _beta_row[0] and float(_beta_row[0]) > 0:
                        stock["beta"] = round(float(_beta_row[0]), 2)
                    else:
                        # Volatility-based estimate from price history
                        import sqlite3 as _sq2b
                        _bc2 = _sq2b.connect("market_data.db")
                        _pr = _bc2.execute(
                            """SELECT close FROM daily_prices WHERE symbol=?
                               AND exchange='NSE' ORDER BY date DESC LIMIT 91""", (sym,)
                        ).fetchall()
                        _bc2.close()
                        if len(_pr) >= 20:
                            import pandas as _pd2
                            _rets = _pd2.Series([r[0] for r in reversed(_pr)]).pct_change().dropna()
                            if len(_rets) >= 10:
                                stock["beta"] = round(float(_rets.std() * (252**0.5) / 0.15), 2)
                except Exception:
                    pass

            # Enrich from symbol_master (+ parse EPS/mcap from updated_on tag)
            if sym in _sm_map:
                _sm_vals = _sm_map[sym]
                cn  = _sm_vals[0] if len(_sm_vals) > 0 else ""
                sec = _sm_vals[1] if len(_sm_vals) > 1 else ""
                cap = _sm_vals[2] if len(_sm_vals) > 2 else ""
                upd = _sm_vals[3] if len(_sm_vals) > 3 else ""
                if not stock.get("company_name") and cn:
                    stock["company_name"] = cn
                if not stock.get("sector") or stock.get("sector") == "General":
                    if sec:
                        stock["sector"] = sec
                if not stock.get("cap_category") or stock.get("cap_category") == "—":
                    if cap:
                        stock["cap_category"] = cap
                # Parse EPS and mcap from the tag in updated_on
                if upd and "|eps=" in str(upd):
                    import re as _re
                    _eps_m  = _re.search(r"eps=([0-9.]+)", str(upd))
                    _mcap_m = _re.search(r"mcap=([0-9.]+)", str(upd))
                    _pe_m   = _re.search(r"pe=([0-9.]+)",   str(upd))
                    if _eps_m  and not stock.get("eps"):
                        stock["eps"]     = float(_eps_m.group(1))
                    if _mcap_m and not stock.get("mcap_cr"):
                        stock["mcap_cr"] = float(_mcap_m.group(1))
                    if _pe_m   and not stock.get("pe"):
                        stock["pe"]      = float(_pe_m.group(1))

            # cap_category from mcap (always computable from market cap thresholds)
            if not stock.get("cap_category") or stock.get("cap_category") == "—":
                _mcap = _sf(stock.get("mcap_cr", stock.get("mcap", 0)))
                if _mcap <= 0:
                    # Estimate mcap from close × approx shares (not perfect but better than blank)
                    _mcap = _sf(stock.get("close", 0), 0) * _sf(stock.get("volume", 0), 0) / 1e7
                if   _mcap >= 20000: stock["cap_category"] = "LARGE CAP"
                elif _mcap >=  5000: stock["cap_category"] = "MID CAP"
                elif _mcap >=   500: stock["cap_category"] = "SMALL CAP"
                elif _mcap >      0: stock["cap_category"] = "MICRO CAP"
                else:                stock["cap_category"] = "—"

            # day_change directly from close/prev_close in stock dict (always available)
            _cv  = _sf(stock.get("close", 0), 0)
            _pcv = _sf(stock.get("prev_close", 0), 0)
            if _cv > 0 and _pcv > 0:
                stock["day_change"] = round((_cv - _pcv) / _pcv * 100, 2)

            # 52w high/low and vol50 from DB history
            if sym in _dp_map:
                h52, l52, vol50 = _dp_map[sym]
                if h52 and float(h52) > 0: stock["high_52w"] = round(float(h52), 2)
                if l52 and float(l52) > 0: stock["low_52w"]  = round(float(l52), 2)
                if vol50 and float(vol50) > 0:
                    _curr_vol = _sf(stock.get("volume", 0), 0)
                    stock["vol_ratio"] = round(_curr_vol / float(vol50), 2)

            # Enrich from fundamental_metrics
            if sym in _fm_map:
                _fmv = list(_fm_map[sym]) + [0]*35
                # Cols 0-28 (original): pe,pb,ey,dy,pf,az,bm,roe,roce,roa,gm,em,nm,
                #       de,cr,rc1,rc3,pc1,pc3,td,fcf,nde,ic,ps,ev,peg,qr,cash,fcfy
                # Cols 29-31 (new): rev_yoy, pat_yoy, payout_ratio
                pe,pb,ey,dy,pf,az,bm,roe,roce,roa,gm,em,nm,de,cr,rc1,rc3,pc1,pc3,td,fcf,nde,ic,ps_v,ev_v,peg_v,qr_v,cash_v,fcfy_v = _fmv[:29] + [0]*(29-min(len(_fmv),29))
                rev_yoy_v    = _fmv[29] if len(_fmv) > 29 else 0
                pat_yoy_v    = _fmv[30] if len(_fmv) > 30 else 0
                payout_v     = _fmv[31] if len(_fmv) > 31 else 0
                def _fv(v):
                    try:
                        f = float(v) if v is not None else 0.0
                        return round(f, 4) if f != 0 else "—"
                    except (ValueError, TypeError):
                        return "—"
                def _fvn(v):  # numeric version — returns 0 not "—" for safe float ops
                    try:
                        return float(v) if v is not None else 0.0
                    except (ValueError, TypeError):
                        return 0.0
                # ── Unit conversion helpers ──────────────────────────────────
                # yfinance returns fractions for %, debtToEquity as ×100
                def _pct(v):
                    """Convert yfinance fraction to % (0.16 → 16.0)"""
                    f = _fvn(v)
                    if f == 0: return "—"
                    return round(f * 100, 2) if abs(f) < 2.0 else round(f, 2)
                def _ratio(v):
                    """Convert yfinance debtToEquity (×100) to real ratio"""
                    f = _fvn(v)
                    if f == 0: return "—"
                    return round(f / 100, 3) if abs(f) > 2.0 else round(f, 3)

                # Profitability — fraction→% conversion (display)
                stock.setdefault("roe",          _pct(roe))
                stock.setdefault("roce",         _fv(roce))
                stock.setdefault("roa",          _pct(roa))
                stock.setdefault("gross_margin", _pct(gm))
                stock.setdefault("ebitda_margin",_pct(em))
                stock.setdefault("npm",          _pct(nm))
                # Numeric versions for scoring (never "—", always float)
                _roe_raw = _fvn(roe)
                _gm_raw  = _fvn(gm)
                _nm_raw  = _fvn(nm)
                # Convert fractions to % if needed
                stock["roe_num"] = round(_roe_raw * 100, 2) if 0 < abs(_roe_raw) < 2.0 else round(_roe_raw, 2)
                stock["gm_num"]  = round(_gm_raw  * 100, 2) if 0 < abs(_gm_raw)  < 2.0 else round(_gm_raw,  2)
                stock["nm_num"]  = round(_nm_raw  * 100, 2) if 0 < abs(_nm_raw)  < 2.0 else round(_nm_raw,  2)
                # Forensics — no free source
                stock.setdefault("piotroski_f",  _fv(pf))
                stock.setdefault("altman_z",     _fv(az))
                stock.setdefault("beneish_m",    _fv(bm))
                # Growth CAGRs — not available from yfinance
                stock.setdefault("rev_cagr_1y",  _fv(rc1))
                stock.setdefault("rev_cagr_3y",  _fv(rc3))
                stock.setdefault("pat_cagr_1y",  _fv(pc1))
                stock.setdefault("pat_cagr_3y",  _fv(pc3))
                stock.setdefault("rev_yoy",      _fv(rev_yoy_v))
                stock.setdefault("pat_yoy",      _fv(pat_yoy_v))
                # Financial Health — with unit fixes
                stock.setdefault("debt_equity",  _ratio(de))  # yfinance ×100 → ratio
                # Numeric D/E for scoring (never "—")
                _de_raw = _fvn(de)
                stock["de_ratio_num"] = round(_de_raw / 100, 3) if abs(_de_raw) > 2.0 else round(_de_raw, 3)
                stock["cr_num"]       = round(_fvn(cr), 3)      # current ratio numeric
                stock["pe_num"]       = round(_fvn(pe), 2)      # PE numeric
                stock.setdefault("current_ratio",_fv(cr) if _fvn(cr) > 0 else "—")
                stock.setdefault("quick_ratio",  _fv(qr_v) if _fvn(qr_v) > 0 else "—")
                stock.setdefault("total_debt",   _fv(td) if _fvn(td) > 0 else "—")
                stock.setdefault("cash",         _fv(cash_v) if _fvn(cash_v) > 0 else "—")
                stock.setdefault("fcf",          _fv(fcf) if _fvn(fcf) != 0 else "—")
                stock.setdefault("fcf_yield",    _fv(fcfy_v))
                stock.setdefault("nd_ebitda",    _fv(nde))
                stock.setdefault("int_coverage", _fv(ic))
                # Valuation ratios — only show if yfinance returned a value
                stock.setdefault("ps",           _fv(ps_v) if _fvn(ps_v) > 0 else "—")
                stock.setdefault("ev_ebitda",    _fv(ev_v) if _fvn(ev_v) > 0 else "—")
                # PEG from yfinance pegRatio, OR compute from PE / pat_yoy if missing
                _peg_raw = _fvn(peg_v)
                if _peg_raw > 0:
                    stock.setdefault("peg", round(_peg_raw, 2))
                else:
                    # Compute: PEG = PE / (pat_yoy %) — use pat_yoy as growth proxy
                    _pe_raw2  = _fvn(pe)
                    _growth_r = _fvn(rc3) if _fvn(rc3) > 0 else _fvn(pc3)
                    # pat_yoy from yfinance earningsGrowth (already ×100 in DB)
                    # but rc3/pc3 are always 0 (no free source), use pat_yoy
                    # pat_yoy_v now available — compute PEG directly
                    _pe_val = _fvn(pe)
                    _g_val  = _fvn(pat_yoy_v)  # earningsGrowth ×100 from DB
                    if _pe_val > 0 and _g_val > 0:
                        stock.setdefault("peg", round(_pe_val / _g_val, 2))
                    else:
                        stock.setdefault("peg", "—")
                # P/CF: compute from FCF yield if available, or FCF/mcap
                _fy_pcf  = _fvn(fcfy_v)
                _fcf_raw = _fvn(fcf)   # FCF in ₹Cr
                _mcap_v  = _fvn(stock.get("mcap_cr", 0))
                if _fy_pcf > 0:
                    stock.setdefault("p_cf", round(100.0 / _fy_pcf, 1))
                elif _fcf_raw != 0 and _mcap_v > 0:
                    # P/CF = mcap / FCF
                    _pcf_calc = round(_mcap_v / _fcf_raw, 1) if _fcf_raw > 0 else "—"
                    stock.setdefault("p_cf", _pcf_calc)
                else:
                    stock.setdefault("p_cf", "—")
                # Numeric fields — 0 safe for arithmetic
                stock.setdefault("pe",            _fvn(pe))
                stock.setdefault("pb",            _fvn(pb))
                stock.setdefault("earnings_yield",_fvn(ey))
                stock.setdefault("earn_yield",    _fvn(ey))
                stock.setdefault("div_yield",     _fvn(dy))
                stock.setdefault("payout_ratio",  _fv(payout_v) if _fvn(payout_v) > 0 else "—")

            # Enrich from shareholding
            if sym in _sh_map:
                pro, proq, pled, pledd, fii, fiiq, dii, diiq, pub = _sh_map[sym]
                def _fv2(v):  # display: "—" for zero/None
                    try:
                        f = float(v) if v is not None else 0.0
                        return round(f, 2) if f != 0 else "—"
                    except (ValueError, TypeError):
                        return "—"
                def _fv2n(v):  # numeric: 0 for zero/None (safe for float())
                    try:
                        return float(v) if v is not None else 0.0
                    except (ValueError, TypeError):
                        return 0.0
                stock.setdefault("promoter_pct",    _fv2n(pro))   # used in float() calcs
                stock.setdefault("promoter_qoq",    _fv2(proq))
                stock.setdefault("pledge_pct",      _fv2n(pled))  # used in float() calcs
                stock.setdefault("pledge_direction",pledd or "—")
                stock.setdefault("fii_pct",         _fv2n(fii))   # used in float() calcs
                stock.setdefault("fii_qoq",         _fv2(fiiq))
                stock.setdefault("dii_pct",         _fv2n(dii))
                stock.setdefault("dii_qoq",         _fv2(diiq))
                stock.setdefault("public_float",    _fv2(pub))

            # Enrich from technical_indicators
            if sym in _ti_map:
                sma200, st, adx, rsi, macd_s, stk, mfi, obv_s, vwap_s, s1, s2, r1, r2 = _ti_map[sym]
                stock["sma_200"]    = round(float(sma200), 2) if sma200 else 0
                stock["supertrend"] = st or "NEUTRAL"
                stock["adx"]        = round(float(adx), 2) if adx else 0
                stock["rsi"]        = round(float(rsi), 2) if rsi else 0
                stock["macd_signal"]= macd_s or "NEUTRAL"
                stock["stoch_k"]    = round(float(stk), 2) if stk else 0
                stock["mfi"]        = round(float(mfi), 2) if mfi else 0
                stock["obv_signal"] = obv_s or "—"
                stock["above_vwap"] = vwap_s or "—"
                stock["support_1"]  = round(float(s1), 2) if s1 else 0
                stock["support_2"]  = round(float(s2), 2) if s2 else 0
                stock["resist_1"]   = round(float(r1), 2) if r1 else 0
                stock["resist_2"]   = round(float(r2), 2) if r2 else 0

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 5B: FAIR VALUE ENGINE
        # ─────────────────────────────────────────────────────────────────────
        from fair_value_engine import FairValueEngine
        fv_engine = FairValueEngine()
        for stock in final_100_list:
            beta       = _sf(stock.get("beta", 1.0), 1.0)
            growth_3yr = _sf(stock.get("pat_cagr_3y",
                               stock.get("rev_cagr_3y", 10)), 10)

            # Derive BVPS from PB and CMP if not available
            if not stock.get("bvps"):
                pb  = _sf(stock.get("pb", 0), 0)
                cmp = _sf(stock.get("close", 0), 0)
                if pb > 0 and cmp > 0:
                    stock["bvps"] = round(cmp / pb, 2)

            # Derive EPS from PE and CMP if not already set
            if not stock.get("eps"):
                pe  = _sf(stock.get("pe", 0), 0)
                cmp = _sf(stock.get("close", 0), 0)
                if pe > 0 and cmp > 0:
                    stock["eps"] = round(cmp / pe, 2)

            models    = fv_engine.calculate_all_models(stock, beta, growth_3yr)
            fv_result = fv_engine.get_composite_fair_value(
                models, _sf(stock.get("close", 1), 1)
            )
            stock.update(models)
            stock.update(fv_result)

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 6: SCORING + KEY FIXES + PRICE TARGETS
        # ─────────────────────────────────────────────────────────────────────
        from scoring_engine import ScoringEngine
        scoring = ScoringEngine()
        for stock in final_100_list:

            # ── Pre-compute technical_score from real technical indicators ───
            if not stock.get("technical_score"):
                _ts = 50.0  # base
                _rsi_s  = _sf(stock.get("rsi", 50), 50)
                _adx_s  = _sf(stock.get("adx", 0), 0)
                _macd_s = str(stock.get("macd_signal", "NEUTRAL"))
                _st_s   = str(stock.get("supertrend", "NEUTRAL"))
                _vwap_s = str(stock.get("above_vwap", "NO"))
                _obv_s  = str(stock.get("obv_signal", "NEUTRAL"))
                # RSI contribution
                if   _rsi_s > 60: _ts += 8
                elif _rsi_s > 50: _ts += 4
                elif _rsi_s < 40: _ts -= 8
                elif _rsi_s < 50: _ts -= 4
                # ADX (trend strength)
                if _adx_s > 25: _ts += 5
                elif _adx_s > 20: _ts += 2
                # MACD
                if _macd_s == "BUY":  _ts += 6
                elif _macd_s == "SELL": _ts -= 6
                # Supertrend
                if _st_s == "BUY":  _ts += 8
                elif _st_s == "SELL": _ts -= 8
                # VWAP
                if _vwap_s == "YES": _ts += 4
                else:                _ts -= 2
                # OBV
                if _obv_s == "RISING":  _ts += 4
                elif _obv_s == "FALLING": _ts -= 4
                stock["technical_score"] = max(0, min(100, round(_ts, 1)))

            # ── Pre-compute fundamental_score from available data ─────────────
            if not stock.get("fundamental_score"):
                # Use numeric keys (never "—" strings) for accurate scoring
                _s2_f  = _sf(stock.get("stage2_score", 0), 0)
                _pe_f  = stock.get("pe_num",  _sf(stock.get("pe",  0), 0))
                _roe_f = stock.get("roe_num", _sf(stock.get("roe", 0), 0))
                _de_f  = stock.get("de_ratio_num", _sf(stock.get("debt_equity", 99), 99))
                _cr_f  = stock.get("cr_num",  _sf(stock.get("current_ratio", 0), 0))
                _gm_f  = stock.get("gm_num",  _sf(stock.get("gross_margin", 0), 0))
                _nm_f  = stock.get("nm_num",  _sf(stock.get("npm", 0), 0))
                _ey_f  = _sf(stock.get("earnings_yield", 0), 0)
                _pro_f = _sf(stock.get("promoter_pct", 0), 0)

                # Stage 2 score (0-30) → base 30-70
                _fs = 30.0 + (_s2_f / 30.0) * 40.0

                # PE: 5-20 excellent, 20-40 good, >60 stretched
                if   0 < _pe_f <= 20:  _fs += 12
                elif 0 < _pe_f <= 40:  _fs += 7
                elif _pe_f > 60:       _fs -= 8

                # ROE: >20% excellent, 10-20% good, <5% poor
                if   _roe_f > 20:      _fs += 12
                elif _roe_f > 10:      _fs += 6
                elif 0 < _roe_f < 5:   _fs -= 5

                # D/E ratio: <0.3 excellent, 0.3-1 ok, >2 risky
                if   0 < _de_f < 0.3:  _fs += 8
                elif 0 < _de_f <= 1.0: _fs += 4
                elif _de_f > 2.0:      _fs -= 10

                # Current ratio: >2 healthy, <1 risky
                if   _cr_f > 2.0:      _fs += 6
                elif _cr_f > 1.5:      _fs += 3
                elif 0 < _cr_f < 1.0:  _fs -= 7

                # Gross margin: >40% excellent, >20% decent
                if   _gm_f > 40:       _fs += 8
                elif _gm_f > 20:       _fs += 4

                # Net margin: >15% excellent, >5% decent, negative = penalise
                if   _nm_f > 15:       _fs += 8
                elif _nm_f > 5:        _fs += 4
                elif _nm_f < 0:        _fs -= 8

                # Earnings yield (>6% = undervalued)
                if   _ey_f > 6:        _fs += 5
                elif _ey_f > 4:        _fs += 2

                # Promoter holding
                if   _pro_f > 50:      _fs += 5
                elif _pro_f > 35:      _fs += 2
                elif 0 < _pro_f < 20:  _fs -= 3

                stock["fundamental_score"] = max(0, min(100, round(_fs, 1)))

            # ── Safety score from pledge/debt ────────────────────────────────
            if not stock.get("safety_score"):
                _ss = 50.0
                _pled = _sf(stock.get("pledge_pct", 0), 0)
                _bet  = _sf(stock.get("beta", 1.0), 1.0)
                _de2  = stock.get("de_ratio_num", _sf(stock.get("debt_equity", 0), 0))
                if _pled > 20: _ss -= 15
                elif _pled > 10: _ss -= 7
                if _bet > 1.5: _ss -= 5
                elif _bet < 0.8: _ss += 5
                if _de2 > 2.0: _ss -= 10
                elif _de2 < 0.3 and _de2 > 0: _ss += 5   # very low debt = safer
                stock["safety_score"] = max(0, min(100, round(_ss, 1)))

            # ── Sentiment score from smart money / FII trend ─────────────────
            if not stock.get("sentiment_score"):
                _sent = 50.0
                _fii_t = str(stock.get("fii_3q_trend", "NEUTRAL"))
                _sm    = str(stock.get("smart_money_sentiment", "NEUTRAL"))
                _ins   = str(stock.get("insider_buy_alert", "NO"))
                if _fii_t == "UP":              _sent += 10
                if _sm == "ACCUMULATION":       _sent += 10
                if _ins == "YES":               _sent += 8
                if _fii_t == "DOWN":            _sent -= 10
                stock["sentiment_score"] = max(0, min(100, round(_sent, 1)))

            # Section 3I: Early Entry Score — computed here after vol_ratio + technicals are populated
            try:
                from early_detection_engine import EarlyDetectionEngine
                _ede   = EarlyDetectionEngine()
                _early = _ede.calculate_early_score(stock, {})
                _escore = _early.get("total_score", 0)
                _esigs  = list(_early.get("active_signals", []))

                _vol_r  = _sf(stock.get("vol_ratio", 1.0), 1.0)
                _rsi_e  = _sf(stock.get("rsi", 50), 50)
                _4w_e   = _sf(stock.get("4w_chg", 0), 0)
                _2w_e   = _sf(stock.get("2w_chg", 0), 0)
                _st_e   = str(stock.get("supertrend", "NEUTRAL"))
                _macd_e = str(stock.get("macd_signal", "NEUTRAL"))
                _etag   = str(stock.get("exchange_tag", ""))

                if _vol_r >= 1.8 and 50 < _rsi_e <= 72:
                    _escore += 15
                    _esigs.append("VOL SURGE + RSI ACCUMULATION")
                if _2w_e > 1.5 and _4w_e < _2w_e:
                    _escore += 10
                    _esigs.append("MOMENTUM BUILDING")
                if _st_e == "BUY" and _macd_e == "BUY":
                    _escore += 12
                    _esigs.append("TREND CONFLUENCE")
                _del_e = _sf(stock.get("delivery_pct", 0), 0)
                if _del_e >= 70 and _vol_r >= 2.0:
                    _escore += 10
                    _esigs.append("INSTITUTIONAL FOOTPRINT")
                if _etag == "DUAL_LISTED" and _vol_r >= 1.5:
                    _escore += 8
                    _esigs.append("DUAL-LISTED DISCOVERY")

                _escore = min(100, _escore)
                stock["early_entry_score"] = _escore
                stock["early_mover_badge"] = "EARLY MOVER" if _escore >= 70 else ""
                stock["early_label"] = (
                    "EARLY MOVER — Act before the crowd" if _escore >= 80 else
                    "AHEAD OF CONSENSUS" if _escore >= 60 else "EMERGING"
                )
                if _esigs:
                    stock["early_signals"] = " | ".join(_esigs)
            except Exception as _ee:
                stock.setdefault("early_entry_score", 0)
                stock.setdefault("early_mover_badge", "")
                stock.setdefault("early_label", "EMERGING")

            # Composite score + verdict
            score_result = scoring.calculate_composite_score(stock)
            stock.update(score_result)

            # Storm score
            storm = scoring.calculate_storm_score(stock, market_vix=12.0,
                                                   market_off_peak=3.0)
            if storm:
                stock.update(storm)
            else:
                stock.setdefault("storm_score", 0)
                stock.setdefault("storm_label", "N/A")

            # Spike Score — call SpikeScreener with correct key mappings
            try:
                from spike_screener import SpikeScreener
                _spiker = SpikeScreener()
                # SpikeScreener uses 'vol_spike_50d' — map from our 'vol_ratio'
                _spike_input = dict(stock)
                _spike_input['vol_spike_50d'] = _sf(stock.get('vol_ratio', 1.0), 1.0)
                _spike_result = _spiker.calculate_spike_score(_spike_input, {})
                stock["spike_count"] = _spike_result.get("score", 0)
                stock["spike_score"] = _spike_result.get("score", 0)
                _spike_tags = _spike_result.get("tags", [])
                if _spike_tags:
                    stock["spike_triggers"] = " | ".join(_spike_tags)
            except Exception as _esp:
                stock.setdefault("spike_count", 0)
                stock.setdefault("spike_score", 0)

            # Vol ratio (use DB-enriched value if already set, else calculate)
            if not stock.get("vol_ratio"):
                from data_bridge import get_20d_avg_vol
                avg_vol = get_20d_avg_vol(str(stock.get("symbol", "") or ""))
                curr_vol = _sf(stock.get("volume", 0), 0)
                stock["vol_ratio"] = round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.0
                stock["days_since_analysis"] = 0  # prevent O5 firing for all stocks

            # Smart money signals
            if not stock.get("smart_money_signals"):
                sentiment = stock.get("smart_money_sentiment", "NEUTRAL")
                insider   = stock.get("insider_buy_alert", "NO")
                signals = []
                if sentiment == "ACCUMULATION": signals.append("INST ACCUMULATION")
                if insider == "YES":            signals.append("INSIDER BUYING")
                stock["smart_money_signals"] = ", ".join(signals) if signals else "NEUTRAL"

            # MoS label
            mos = _sf(stock.get("mos_pct", 0), 0)
            if   mos > 40:  stock["mos_label"] = "EXCEPTIONAL"
            elif mos > 25:  stock["mos_label"] = "STRONG"
            elif mos > 10:  stock["mos_label"] = "ADEQUATE"
            elif mos > 0:   stock["mos_label"] = "THIN"
            elif mos > -15: stock["mos_label"] = "SLIGHT PREMIUM"
            else:           stock["mos_label"] = "SIGNIFICANT PREMIUM"

            # Chart Pattern — simple candle pattern from OHLC (no external data needed)
            if not stock.get("chart_pattern") or stock.get("chart_pattern") == "—":
                _o = _sf(stock.get("open", 0), 0)
                _h = _sf(stock.get("high", 0), 0)
                _l = _sf(stock.get("low", 0), 0)
                _c = _sf(stock.get("close", 0), 0)
                _pc = _sf(stock.get("prev_close", 0), 0)
                if _o > 0 and _h > 0 and _l > 0 and _c > 0:
                    _body  = abs(_c - _o)
                    _range = _h - _l
                    _upper = _h - max(_o, _c)
                    _lower = min(_o, _c) - _l
                    if _range > 0:
                        if _body / _range < 0.1:
                            stock["chart_pattern"] = "DOJI"
                        elif _upper > _body * 2 and _lower < _body * 0.5:
                            stock["chart_pattern"] = "SHOOTING STAR" if _c < _o else "HAMMER"
                        elif _lower > _body * 2 and _upper < _body * 0.5:
                            stock["chart_pattern"] = "HAMMER" if _c > _o else "HANGING MAN"
                        elif _c > _o and _pc > 0 and _c > _pc * 1.01:
                            stock["chart_pattern"] = "BULLISH CANDLE"
                        elif _c < _o and _pc > 0 and _c < _pc * 0.99:
                            stock["chart_pattern"] = "BEARISH CANDLE"
                        else:
                            stock["chart_pattern"] = "NEUTRAL"

            # Key-name fixes + derived fields
            if "earn_yield" in stock and not stock.get("earnings_yield"):
                stock["earnings_yield"] = stock["earn_yield"]
            if not stock.get("total_debt") and stock.get("total_debt_cr"):
                stock["total_debt"] = stock["total_debt_cr"]
            if stock.get("bs_flags") and not stock.get("bs_output"):
                stock["bs_output"] = stock["bs_flags"]

            # Earnings yield from EPS/CMP if not already set from DB
            if not stock.get("earnings_yield") or stock.get("earnings_yield") == "—":
                _eps2 = _sf(stock.get("eps", 0), 0)
                _cmp2 = _sf(stock.get("close", 0), 0)
                if _eps2 > 0 and _cmp2 > 0:
                    stock["earnings_yield"] = round(_eps2 / _cmp2 * 100, 2)
                    stock["earn_yield"]     = stock["earnings_yield"]

            # P/E cross-check: if pe is from DB use it, else derive from EPS/CMP
            if not stock.get("pe") or stock.get("pe") == "—":
                _eps3 = _sf(stock.get("eps", 0), 0)
                _cmp3 = _sf(stock.get("close", 0), 0)
                if _eps3 > 0 and _cmp3 > 0:
                    stock["pe"] = round(_cmp3 / _eps3, 2)

            # OB/Bill — set to "—" explicitly (no source, not 0)
            if not stock.get("ob_bill_ratio") or stock.get("ob_bill_ratio") == 0:
                stock["ob_bill_ratio"] = "—"

            # L1 fields — set to "—" (no source in free data)
            for _k in ["l1_wins", "l1_value", "pipeline_vis", "new_market_entry"]:
                stock.setdefault(_k, "—")

            # BS flags to note
            if not stock.get("bs_output") or stock.get("bs_output") == "":
                stock["bs_output"] = f"BS: {stock.get('bs_status','HEALTHY')} — No red flags detected"

            # Price targets from CMP and CFV
            cmp = _sf(stock.get("close", 0), 0)
            cfv = _sf(stock.get("cfv", 0))
            if cmp > 0:
                stock.setdefault("stop_loss",   round(cmp * 0.93, 2))
                stock.setdefault("entry_range", f"{round(cmp*0.98,1)}–{round(cmp*1.01,1)}")
                t_base = cfv if cfv > cmp else cmp
                stock.setdefault("t1", round(cmp * 1.05, 2))
                stock.setdefault("t2", round(cmp * 1.10, 2))
                stock.setdefault("t3", round(t_base, 2) if cfv > 0 else round(cmp * 1.20, 2))
            else:
                for k in ["t1","t2","t3","stop_loss","entry_range"]:
                    stock.setdefault(k, "—")

            # early_signals — combine spike triggers + early mover signals
            _early_sigs = []
            _spike_trigs = stock.get("spike_triggers", "")
            if _spike_trigs and _spike_trigs != "—":
                _early_sigs += [s.strip() for s in str(_spike_trigs).split("|") if s.strip()]
            _early_badge = stock.get("early_mover_badge", "")
            if _early_badge:
                _early_sigs.append(str(_early_badge))
            _early_label = stock.get("early_label", "")
            if _early_label and _early_label not in ("EMERGING", "—", ""):
                _early_sigs.append(str(_early_label))
            stock["early_signals"] = " | ".join(_early_sigs) if _early_sigs else "—"

            # intel_queries: ensure string not list
            iq = stock.get("intel_queries", "")
            if isinstance(iq, list):
                stock["intel_queries"] = " | ".join(iq)

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 7 & 8: AI INVESTOR CARDS
        # ─────────────────────────────────────────────────────────────────────
        print("🤖 [Section 7/8] Generating AI Cards...")
        investor_cards_text = get_ai_analysis(pd.DataFrame(final_100_list))

        # Map AI analysis back to each stock's Block H summary
        ai_lines = investor_cards_text.split("\n\n") if investor_cards_text else []
        for i, stock in enumerate(final_100_list):
            if i < len(ai_lines):
                stock["Analysis_Summary_Block_H"] = ai_lines[i]
            else:
                stock["Analysis_Summary_Block_H"] = "Analysis pending."

        # Format investor cards for text report
        final_cards_for_display = []
        for stock in final_100_list:
            try:
                card = formatter.format_investor_card(stock)
                final_cards_for_display.append(card)
            except Exception as e:
                final_cards_for_display.append(
                    f"{stock.get('symbol', '?')} — card formatting error: {e}"
                )

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 9 & 10: REPORTING & DELIVERY
        # ─────────────────────────────────────────────────────────────────────
        print("📝 [Section 9/10] Constructing Final Deliverables...")
        from excel_generator import ExcelGeneratorV6
        from daily_report_generator import DailyReportGenerator

        date_str = target_date.strftime("%Y%m%d")

        market_stats = {
            "nifty_close":   get_nifty_52w_high_from_db(),
            "sensex_close":  0,
            "nifty_52w_high": get_nifty_52w_high_from_db(),
            "fii_net":       get_latest_fii_net_cash(),
            "nifty_200d":    get_nifty_200_sma(),
            "vix":           12.0,
        }

        # Section 10: Excel Dashboard
        if not final_100_list:
            print("❌ CRITICAL: final_100_list is empty — cannot generate Excel.")
            raise ValueError("final_100_list empty at Excel generation — check Stage 2/3 logs.")
        print(f"   📊 Generating Excel for {len(final_100_list)} stocks...")
        excel_gen = ExcelGeneratorV6(final_100_list, date_str)
        master_file, gold_file = excel_gen.generate_excel_reports()
        print(f"   ✅ Excel saved: {master_file}")

        # Section 9: Daily Research Report (text)
        report_txt = DailyReportGenerator(
            final_100_list, market_stats
        ).generate_research_report()

        report_filename = f"Daily_Analysis_Report_{date_str}.txt"
        with open(report_filename, "w", encoding="utf-8") as f:
            f.write(report_txt)
            f.write("\n\n" + "=" * 60 + "\n\n")
            f.write("--- QUICK INVESTOR CARDS (SECTION 8) ---\n")
            f.write("\n\n".join(final_cards_for_display))

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 12: EMAIL DELIVERY
        # ─────────────────────────────────────────────────────────────────────
        from email_service import send_analysis_email
        attachments = [master_file, gold_file, report_filename]
        attachments = [a for a in attachments if a and os.path.exists(a)]
        send_analysis_email(attachments=attachments)

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 13: DB MAINTENANCE
        # ─────────────────────────────────────────────────────────────────────
        enforce_circular_queue("market_data.db")



        # Log run stats
        import sqlite3
        conn = sqlite3.connect("market_data.db")
        conn.execute("""
            INSERT OR REPLACE INTO run_stats
            (run_date, total_universe, stage1_passed, stage2_passed,
             stage3_selected, gate_check_result, bse_available)
            VALUES (?, ?, ?, ?, ?, 'RUN_SUCCESS', ?)
        """, (
            target_date.strftime("%Y-%m-%d"),
            len(all_stocks),
            len(stage1_candidates),
            len(stage2_qualified),
            len(final_100_list),
            int(bse_available),
        ))
        conn.commit()
        conn.close()

        print(f"✅ Pipeline Execution Success for {target_date}.")

    except Exception as e:
        import traceback
        print(f"❌ CRITICAL FAILURE: {e}")
        traceback.print_exc()
        try:
            from email_service import send_analysis_email
            send_analysis_email(is_error=True, error_msg=str(e))
        except Exception:
            pass

    finally:
        # Always close BSE session and clean up temp files
        _close_bse_client()


if __name__ == "__main__":
    run_master_pipeline()