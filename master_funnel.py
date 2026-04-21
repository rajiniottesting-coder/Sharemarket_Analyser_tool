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
from ingestion.orchestrator import gate_check
from ingestion.harvester import (
    download_nse_bhavcopy,
    download_nse_delivery,
    download_nse_sme_bhavcopy,
    download_nse_fo_participant_data,
)
from database.data_bridge import (
    save_to_database, check_data_integrity,
    get_historical_quarter_data,
    get_symbol_history, get_nifty_52w_high_from_db,
    get_today_consolidated_data, get_latest_fii_net_cash, get_nifty_200_sma,
    initialize_v7_tables,
)

# Section 0 & 3: Screening & Analytics
from screening.pre_screener import stage_1_filter, stage_2_fundamental_scorer
from screening.priority_ranker import get_top_100_candidates
from analysis.v7_analysis_engine import V7AnalysisEngine
from analysis.ownership_tracker import analyze_ownership_trends
from analysis.forensics_engine import ForensicsEngine
from analysis.rotation_engine import SectorRotationRadar
from database.db_maintenance import enforce_circular_queue
from analysis.intel_fetcher import fetch_latest_intelligence

# Section 7 & 8: AI & Formatting
from ai.ai_analyst import get_ai_analysis
from reporting.report_formatter import ReportFormatter


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
        from ingestion.harvester import download_bse_sme_bhavcopy
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
    initialize_v7_tables(conn)          # data_bridge tables (daily_prices etc.)
    try:
        from backfill_history import init_all_tables as _init_bf
        _init_bf(conn)                  # backfill tables (fundamental_metrics,
    except Exception:                   # symbol_master, technical_indicators etc.)
        pass
    conn.close()

    # ═══════════════════════════════════════════════════════════════════════════
    # SECTION 12B — GATE CHECK FIRST
    # Must pass ALL 6 conditions before ANY download, DB write, or analysis.
    # target_date = yesterday (next-morning schedule per master prompt v7)
    # ═══════════════════════════════════════════════════════════════════════════
    gate_result = gate_check()

    if not gate_result["run"]:
        from reporting.email_service import send_analysis_email
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
            from reporting.email_service import send_analysis_email
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
            from analysis.smart_money import SmartMoneyScraper
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
        # SECTION 1.2: GAP DETECTION — must run BEFORE save_to_database
        # Read MAX(date) now while the DB still reflects the last real run.
        # After save_to_database() the MAX date becomes today → gap = 0.
        # ─────────────────────────────────────────────────────────────────────
        _missed_trading_days = 0
        _gap_trading_days    = []
        try:
            import sqlite3 as _sq_gap
            _conn_gap  = _sq_gap.connect("market_data.db")
            _last_row  = _conn_gap.execute(
                "SELECT MAX(date) FROM daily_prices WHERE exchange='NSE'"
            ).fetchone()
            _conn_gap.close()
            _last_ds = _last_row[0] if _last_row and _last_row[0] else None
            if _last_ds:
                _last_d = datetime.date.fromisoformat(_last_ds)
                _d = _last_d + datetime.timedelta(days=1)
                while _d < target_date:
                    if _d.weekday() < 5:
                        _gap_trading_days.append(_d)
                    _d += datetime.timedelta(days=1)
                _missed_trading_days = len(_gap_trading_days)
                if _missed_trading_days >= 2:
                    print(f"⚠️  [Gap Detect] {_missed_trading_days} trading days missing "
                          f"({_gap_trading_days[0]} → {_gap_trading_days[-1]})")
        except Exception as _gde:
            print(f"   ⚠️  Gap detection (non-critical): {_gde}")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 1.3: DATABASE SYNC
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
        # SECTION 1.4: GAP-FILL — backfill missing trading days
        # Runs after today's data is saved. Downloads missing days from
        # NSE archives so technicals (SMA, Chg% windows) stay accurate.
        # ─────────────────────────────────────────────────────────────────────
        if _missed_trading_days >= 2:
            print(f"🔄 [Gap Fill] Downloading {_missed_trading_days} missing trading days...")
            try:
                from backfill_history import (
                    init_all_tables    as _bf_init,
                    download_nse_bhav  as _dl_nse,
                    download_bse_bhav  as _dl_bse,
                    normalise          as _norm,
                    upsert             as _upsert,
                    _compute_all_indicators,
                )
                import tempfile as _tmp, sqlite3 as _sq2
                from bse import BSE as _BseClient
                _bf_tmp  = _tmp.mkdtemp(prefix="gapfill_")
                _bf_conn = _sq2.connect("market_data.db")
                _bf_init(_bf_conn)
                try:
                    _bf_bse = _BseClient(download_folder=_bf_tmp)
                except Exception:
                    _bf_bse = None

                _filled = 0
                for _gd in _gap_trading_days:
                    _gd_iso  = _gd.strftime("%Y-%m-%d")
                    _nse_raw = _dl_nse(_gd)
                    _nse_df  = _norm(_nse_raw, "NSE") if _nse_raw is not None else None
                    if _nse_df is not None:
                        _nse_df["date"] = _gd_iso
                        _upsert(_nse_df, "daily_prices", _bf_conn)
                        _filled += 1
                    if _bf_bse:
                        try:
                            _bse_raw = _dl_bse(_gd, _bf_bse, _bf_tmp)
                            _bse_df  = _norm(_bse_raw, "BSE") if _bse_raw is not None else None
                            if _bse_df is not None:
                                _bse_df["date"] = _gd_iso
                                _upsert(_bse_df, "daily_prices", _bf_conn)
                        except Exception:
                            pass
                _bf_conn.commit()
                if _filled > 0:
                    print(f"   ✅ Gap filled: {_filled}/{_missed_trading_days} days. "
                          f"Recomputing indicators...")
                    _compute_all_indicators(_bf_conn)
                else:
                    print(f"   ⚠️  Gap fill: NSE archives not available for gap dates "
                          f"(older than 30 days). Continuing with available data.")
                _bf_conn.close()
                import shutil as _sh
                _sh.rmtree(_bf_tmp, ignore_errors=True)
            except Exception as _gfe:
                print(f"   ⚠️  Gap fill error (non-critical): {_gfe}")

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
            stock["spike_suppressed"]  = guard["suppressed"]
            stock["guard_reasons"]     = ", ".join(guard["reasons"])
            # risk_flag_active is read by scoring_engine for -10 penalty.
            # Bridge from spike_suppressed (same condition, different key name).
            stock["risk_flag_active"]  = guard["suppressed"]

            # Section 3I: Early Entry Score deferred to Section 6 scoring loop
            # (vol_ratio, rsi, supertrend, 2w_chg are not available yet in this pass)
            stock.setdefault("early_entry_score", 0)
            stock.setdefault("early_mover_badge", "")
            stock.setdefault("early_label", "EMERGING")

            # Section 3L: Sector Rotation Stage — derived from technical signals
            # Uses RSI + MACD + Supertrend + 4w_chg (all reliably populated)
            _sec_ret  = _sf(stock.get("4w_chg", 0), 0)
            _2w_ret   = _sf(stock.get("2w_chg", 0), 0)
            _rsi_rs   = _sf(stock.get("rsi", 50), 50)
            _macd_rs  = str(stock.get("macd_signal", "NEUTRAL")).upper()
            _st_rs    = str(stock.get("supertrend", "NEUTRAL")).upper()
            _del_rs   = _sf(stock.get("delivery_pct", 0), 0)
            _vol_rs   = _sf(stock.get("vol_ratio", 1.0), 1.0)
            if _rsi_rs > 70 and _sec_ret > 5:
                _rot_stage = "STAGE 3 — MOMENTUM PEAK"
            elif _rsi_rs > 70 and "SELL" in _macd_rs:
                _rot_stage = "STAGE 4 — DISTRIBUTION"
            elif _sec_ret < -3 and _rsi_rs < 45:
                _rot_stage = "STAGE 4 — DISTRIBUTION"
            elif "BUY" in _st_rs and "BUY" in _macd_rs and _sec_ret > 2:
                _rot_stage = "STAGE 2 — CONFIRMED UPTREND"
            elif 40 < _rsi_rs <= 58 and "BUY" in _macd_rs and _2w_ret > _sec_ret:
                _rot_stage = "STAGE 1 — EARLY ACCUMULATION"
            elif _del_rs >= 65 and _vol_rs >= 1.8 and _rsi_rs < 60:
                _rot_stage = "STAGE 1 — EARLY ACCUMULATION"
            else:
                _rot_stage = "NEUTRAL"
            stock["rotation_stage"] = _rot_stage

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
            from analysis.bs_engine import BalanceSheetEngine
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
            from backfill_history import fetch_nse_fundamentals, init_all_tables
            import sqlite3 as _sq_pre
            _conn_pre = _sq_pre.connect("market_data.db")
            init_all_tables(_conn_pre)   # ensures fundamental_metrics, symbol_master etc. exist

            # ── Seed symbol_master from daily_prices for today's symbols ────
            # fetch_nse_fundamentals uses UPDATE (not INSERT), so rows must exist first.
            # IMPORTANT: save_to_database stamps rows with date.today() (run date),
            # NOT target_date — so we seed from MAX(date) not target_date.
            try:
                _conn_pre.execute("""
                    INSERT OR IGNORE INTO symbol_master (symbol, isin, exchange, updated_on)
                    SELECT DISTINCT symbol, isin, exchange, date
                    FROM daily_prices
                    WHERE date = (SELECT MAX(date) FROM daily_prices)
                    AND symbol != ''
                """)
                _conn_pre.commit()
                _seeded = _conn_pre.execute(
                    "SELECT COUNT(*) FROM symbol_master").fetchone()[0]
                print(f"   ✅ symbol_master seeded: {_seeded} symbols")
            except Exception as _se:
                print(f"   ⚠️  symbol_master seed: {_se}")

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
        # Ensure new DB columns exist (idempotent — safe to run every time)
        try:
            import sqlite3 as _sq_mig
            _mc = _sq_mig.connect(_db)
            _existing_cols = {r[1] for r in _mc.execute(
                "PRAGMA table_info(fundamental_metrics)").fetchall()}
            for _col, _typedef in [
                ("operating_cf_cr","REAL DEFAULT 0"),
                ("curr_assets_cr", "REAL DEFAULT 0"),
                ("curr_liab_cr",   "REAL DEFAULT 0"),
                ("div_yield",      "REAL DEFAULT 0"),
                ("payout_ratio",   "REAL DEFAULT 0"),
                ("rev_yoy",        "REAL DEFAULT 0"),
                ("pat_yoy",        "REAL DEFAULT 0"),
                ("npm_q1",           "REAL DEFAULT 0"),
                ("npm_q2",           "REAL DEFAULT 0"),
                ("npm_q3",           "REAL DEFAULT 0"),
                ("margin_expansion", "INTEGER DEFAULT 0"),
                ("q_rev_cr",         "REAL DEFAULT 0"),
                ("q_pat_cr",         "REAL DEFAULT 0"),
                ("q_ebitda_cr",      "REAL DEFAULT 0"),
                ("ebitda_cagr_1y",   "REAL DEFAULT 0"),
                ("rev_cagr_1y",      "REAL DEFAULT 0"),
                ("rev_cagr_3y",      "REAL DEFAULT 0"),
                ("pat_cagr_1y",      "REAL DEFAULT 0"),
                ("pat_cagr_3y",      "REAL DEFAULT 0"),
            ]:
                if _col not in _existing_cols:
                    _mc.execute(
                        f"ALTER TABLE fundamental_metrics ADD COLUMN {_col} {_typedef}")
            _mc.commit(); _mc.close()
        except Exception:
            pass

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
                        COALESCE(fm.payout_ratio, 0)   as payout_ratio,
                        COALESCE(fm.npm_q1, 0)         as npm_q1,
                        COALESCE(fm.npm_q2, 0)         as npm_q2,
                        COALESCE(fm.npm_q3, 0)         as npm_q3,
                        COALESCE(fm.margin_expansion,0) as margin_expansion,
                        COALESCE(fm.q_rev_cr, 0)       as q_rev_cr,
                        COALESCE(fm.q_pat_cr, 0)       as q_pat_cr,
                        COALESCE(fm.q_ebitda_cr, 0)    as q_ebitda_cr,
                        COALESCE(fm.ebitda_cagr_1y, 0) as ebitda_cagr_1y
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

            # Optional extended columns — separate query so a missing DB column
            # never breaks the main SELECT above (safe, degrades gracefully)
            _fm_ext = {}   # symbol → (op_cf_cr, curr_assets_cr, curr_liab_cr)
            try:
                _ext_rows = _conn.execute(
                    f"""SELECT fm.symbol,
                        COALESCE(fm.operating_cf_cr, 0),
                        COALESCE(fm.curr_assets_cr,  0),
                        COALESCE(fm.curr_liab_cr,    0)
                        FROM fundamental_metrics fm
                        INNER JOIN (
                            SELECT symbol, MAX(date) as md FROM fundamental_metrics
                            WHERE symbol IN ({_sym_placeholders}) GROUP BY symbol
                        ) lt ON fm.symbol=lt.symbol AND fm.date=lt.md""",
                    _syms
                ).fetchall()
                for r in _ext_rows:
                    _fm_ext[r[0]] = r[1:]
            except Exception:
                pass   # DB columns don't exist yet — will populate after next backfill

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
                _fmv = list(_fm_map[sym]) + [0]*45
                # Cols 0-28 (original): pe,pb,ey,dy,pf,az,bm,roe,roce,roa,gm,em,nm,
                #       de,cr,rc1,rc3,pc1,pc3,td,fcf,nde,ic,ps,ev,peg,qr,cash,fcfy
                # Cols 29-31: rev_yoy, pat_yoy, payout_ratio
                # Cols 32-39: npm_q1, npm_q2, npm_q3, margin_expansion,
                #             q_rev_cr, q_pat_cr, q_ebitda_cr, ebitda_cagr_1y
                pe,pb,ey,dy,pf,az,bm,roe,roce,roa,gm,em,nm,de,cr,rc1,rc3,pc1,pc3,td,fcf,nde,ic,ps_v,ev_v,peg_v,qr_v,cash_v,fcfy_v = _fmv[:29] + [0]*(29-min(len(_fmv),29))
                rev_yoy_v    = _fmv[29] if len(_fmv) > 29 else 0
                pat_yoy_v    = _fmv[30] if len(_fmv) > 30 else 0
                payout_v     = _fmv[31] if len(_fmv) > 31 else 0
                # Quarterly / CAGR fields (cols 32-39 from extended SELECT)
                npm_q1_v     = _fmv[32] if len(_fmv) > 32 else 0
                npm_q2_v     = _fmv[33] if len(_fmv) > 33 else 0
                npm_q3_v     = _fmv[34] if len(_fmv) > 34 else 0
                mexp_v       = _fmv[35] if len(_fmv) > 35 else 0   # INTEGER 0/1
                q_rev_v      = _fmv[36] if len(_fmv) > 36 else 0
                q_pat_v      = _fmv[37] if len(_fmv) > 37 else 0
                q_ebitda_v   = _fmv[38] if len(_fmv) > 38 else 0
                ebitda_c1_v  = _fmv[39] if len(_fmv) > 39 else 0
                # Extended fields from secondary safe query (0 if DB column missing)
                _ext = _fm_ext.get(sym, (0, 0, 0))
                op_cf_v     = float(_ext[0]) if _ext[0] else 0   # operating cash flow ₹Cr
                curr_ass_v  = float(_ext[1]) if _ext[1] else 0   # current assets ₹Cr
                curr_liab_v = float(_ext[2]) if _ext[2] else 0   # current liabilities ₹Cr
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
                # When yfinance has no direct data (roe=0), derive from available ratios:
                # ROE = earnings_yield × PB  (EPS/CMP × CMP/BVPS = EPS/BVPS)
                _roe_direct = _fvn(roe)
                _roa_direct = _fvn(roa)
                _ey_v = _fvn(ey)   # earnings yield %
                _pb_v = _fvn(pb)   # price/book
                _de_v = _fvn(de)   # D/E (×100 from yfinance, but already divided)
                _de_ratio = _de_v / 100 if _de_v > 2.0 else _de_v

                if _roe_direct != 0:
                    _roe_display = _pct(roe)
                elif _ey_v > 0 and _pb_v > 0:
                    # Derived: ROE ≈ earnings_yield × PB (reasonable approximation)
                    _roe_derived = round(_ey_v * _pb_v, 2)
                    _roe_display = f"{_roe_derived}" if 0 < _roe_derived < 100 else "—"
                else:
                    _roe_display = "—"
                stock.setdefault("roe", _roe_display)
                # Store numeric ROE for scoring
                _roe_num_val = (_roe_direct*100 if 0<_roe_direct<2 else _roe_direct) if _roe_direct != 0 else                                (_ey_v * _pb_v if _ey_v > 0 and _pb_v > 0 else 0)
                stock["roe_num"] = round(_roe_num_val, 2)

                if _roa_direct != 0:
                    stock.setdefault("roa", _pct(roa))
                elif _roe_num_val > 0 and _de_ratio >= 0:
                    # Derived: ROA ≈ ROE / (1 + D/E)
                    _roa_derived = round(_roe_num_val / (1 + _de_ratio), 2)
                    stock.setdefault("roa", f"{_roa_derived}" if 0 < _roa_derived < 100 else "—")
                else:
                    stock.setdefault("roa", "—")

                # ROCE: not available from yfinance — derive if possible
                # ROCE ≈ ROE × equity / (equity + net_debt)
                # Simplified: ROCE ≈ ROE / (1 + nd_ebitda × ebitda_margin/100) — too complex
                # Just show "—" if no direct data
                # ROCE: not in yfinance — derive from available metrics
                # ROCE = EBIT / Capital_Employed
                # Approximation: ROCE ≈ EBITDA_margin × (Revenue/Capital_Employed)
                # Revenue ≈ MCap / P_S_ratio; Capital_Employed ≈ MCap/PB + Total_Debt
                _roce_direct = _fvn(roce)
                if _roce_direct != 0:
                    stock.setdefault("roce", _fv(roce))
                else:
                    # Method 1: if ebitda_margin and P/S available → compute Revenue, then EBIT
                    _em_v   = _fvn(em)   # ebitda margin fraction
                    _ps_v   = _fvn(ps_v) # P/S ratio
                    _mcap_r = _fvn(stock.get("mcap_cr", 0))
                    _pb_r   = _fvn(pb)
                    _td_r   = _fvn(stock.get("total_debt", 0)) if stock.get("total_debt") not in ("—", None) else 0
                    _em_pct = (_em_v * 100 if 0 < _em_v < 2.0 else _em_v)  # convert fraction→%
                    if _em_pct > 0 and _ps_v > 0 and _mcap_r > 0 and _pb_r > 0:
                        _rev_cr   = _mcap_r / _ps_v                     # Revenue ₹Cr
                        _ebit_cr  = _rev_cr * (_em_pct * 0.85) / 100    # EBIT ≈ EBITDA × 0.85
                        _equity_cr = _mcap_r / _pb_r                    # Book equity ₹Cr
                        _cap_emp  = _equity_cr + max(_td_r, 0)          # Capital Employed
                        if _cap_emp > 0:
                            _roce_calc = round(_ebit_cr / _cap_emp * 100, 2)
                            stock.setdefault("roce", _roce_calc if 0 < _roce_calc < 200 else "—")
                        else:
                            stock.setdefault("roce", "—")
                    else:
                        stock.setdefault("roce", "—")
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
                # ── Proxy F-Score (0-9) ──────────────────────────────────────
                # IMPORTANT: use local tuple variables (roa, fcf, de, cr, gm,
                # roe, cash_v, rev_yoy_v, pat_yoy_v) — they are unpacked from
                # the FM tuple above and ARE available here.
                # Do NOT use stock.get() for these — the stock dict fields
                # (stock["roa"], stock["fcf"] etc.) are only written ~50 lines
                # below this block, so stock.get() would read zeros → score = 2.
                _pf_roa  = _fvn(roa)        # ROA directly from FM tuple
                _pf_fcf  = _fvn(fcf)        # FCF directly from FM tuple
                _pf_pat  = _fvn(pat_yoy_v)  # PAT YoY from extended FM cols
                _pf_de   = _fvn(de) if _fvn(de) > 0 else 99.0   # D/E, default high if missing
                _pf_cr   = _fvn(cr)         # Current ratio from FM tuple
                _pf_gm   = stock["gm_num"]  # Gross margin — set 2 lines above this block
                _pf_rev  = _fvn(rev_yoy_v)  # Revenue YoY from extended FM cols
                _pf_roe  = stock["roe_num"]  # ROE — set 2 lines above this block
                _pf_cash = _fvn(cash_v)     # Cash from FM tuple
                _pf_score = (
                    (1 if _pf_roa  > 0   else 0) +   # P1: profitable (ROA > 0)
                    (1 if _pf_fcf  > 0   else 0) +   # P2: positive FCF
                    (1 if _pf_pat  > 0   else 0) +   # P3: growing earnings (PAT YoY > 0)
                    (1 if _pf_de   < 1.0 else 0) +   # P4: low leverage (D/E < 1)
                    (1 if _pf_cr   > 1.0 else 0) +   # P5: liquid (Current Ratio > 1)
                    (1 if _pf_gm   > 15  else 0) +   # P6: decent gross margin (>15%)
                    (1 if _pf_rev  > 0   else 0) +   # P7: growing revenue (Rev YoY > 0)
                    (1 if _pf_roe  > 10  else 0) +   # P8: good ROE (>10%)
                    (1 if _pf_cash > 0   else 0)     # P9: has cash
                )
                # Require at least 4 real data points to avoid defaulting to DB value
                _pf_data_count = sum(1 for v in [_pf_roa, _pf_fcf, _pf_de, _pf_gm, _pf_roe]
                                     if v not in (0, 99.0))
                if _pf_data_count >= 3:
                    stock["piotroski_f"] = _pf_score
                else:
                    stock.setdefault("piotroski_f", _fv(pf))  # fallback to DB value or "—"
                # Forensics — no free source for true Piotroski/Altman/Beneish
                stock.setdefault("altman_z",     _fv(az))
                stock.setdefault("beneish_m",    _fv(bm))
                # Growth CAGRs — not available from yfinance
                stock.setdefault("rev_cagr_1y",  _fv(rc1))
                stock.setdefault("rev_cagr_3y",  _fv(rc3))
                stock.setdefault("pat_cagr_1y",  _fv(pc1))
                stock.setdefault("pat_cagr_3y",  _fv(pc3))
                stock.setdefault("rev_yoy",      _fv(rev_yoy_v))
                stock.setdefault("pat_yoy",      _fv(pat_yoy_v))
                # ── Quarterly NPM / Margin Expansion (from DB via backfill) ──
                stock.setdefault("npm_q1",  _fv(npm_q1_v))
                stock.setdefault("npm_q2",  _fv(npm_q2_v))
                stock.setdefault("npm_q3",  _fv(npm_q3_v))
                # margin_expansion stored as INTEGER 0/1 in DB; display as YES/NO
                stock.setdefault("margin_expansion",
                                 "YES" if int(_fvn(mexp_v)) == 1 else "NO")
                # ── Q3 absolute figures (₹ Crore) ────────────────────────────
                stock.setdefault("q3_rev",    _fv(q_rev_v))
                stock.setdefault("q3_pat",    _fv(q_pat_v))
                stock.setdefault("q3_ebitda", _fv(q_ebitda_v))
                # ── EBITDA CAGR 1Y (not in old rc1..pc3 vars) ────────────────
                stock.setdefault("ebitda_cagr_1y", _fv(ebitda_c1_v))
                # Financial Health — with unit fixes
                stock.setdefault("debt_equity",  _ratio(de))  # yfinance ×100 → ratio
                # Numeric D/E for scoring (never "—")
                _de_raw = _fvn(de)
                stock["de_ratio_num"] = round(_de_raw / 100, 3) if abs(_de_raw) > 2.0 else round(_de_raw, 3)
                stock["cr_num"]       = round(_fvn(cr), 3)      # current ratio numeric
                stock["pe_num"]       = round(_fvn(pe), 2)      # PE numeric
                # Current Ratio: direct assignment so it always gets best value
                _cr_direct = _fvn(cr)
                _qr_direct = _fvn(qr_v)
                _ca = _fvn(curr_ass_v)
                _cl = _fvn(curr_liab_v)
                if _cr_direct > 0:
                    _cr_display = round(_cr_direct, 3)
                elif _ca > 0 and _cl > 0:
                    _cr_display = round(_ca / _cl, 3)
                else:
                    _cr_display = "—"
                stock["current_ratio"] = _cr_display   # direct assignment
                stock["cr_num"] = _cr_display if isinstance(_cr_display, (int,float)) else                                   (_cr_direct if _cr_direct > 0 else 0)

                # Quick Ratio: direct assignment
                if _qr_direct > 0:
                    stock["quick_ratio"] = round(_qr_direct, 3)
                elif _cr_display != "—" and float(str(_cr_display)) > 0:
                    stock["quick_ratio"] = round(float(str(_cr_display)) * 0.85, 3)
                else:
                    stock["quick_ratio"] = "—"
                # Total Debt: direct assignment (not setdefault) so it always overwrites
                # even if a previous 0 was set from BS engine or prior iteration
                _td_val  = _fvn(td)
                _de_raw2 = _fvn(de)
                _de_r2   = _de_raw2 / 100 if _de_raw2 > 2.0 else _de_raw2
                _pb_v2   = _fvn(pb)
                _mcap_v2 = _fvn(stock.get("mcap_cr", 0))
                if _td_val > 0:
                    stock["total_debt"] = round(_td_val, 2)
                elif _de_r2 == 0 and _fvn(pe) > 0:
                    stock["total_debt"] = 0          # confirmed zero-debt
                elif _de_r2 > 0 and _pb_v2 > 0 and _mcap_v2 > 0:
                    stock["total_debt"] = round(_de_r2 * (_mcap_v2 / _pb_v2), 2)
                else:
                    stock.setdefault("total_debt", "—")
                stock.setdefault("cash",         _fv(cash_v) if _fvn(cash_v) > 0 else "—")
                # FCF: direct assignment, 3-tier fallback
                _fcf_direct   = _fvn(fcf)
                _op_cf        = _fvn(op_cf_v)
                _mcap_for_fcf = _fvn(stock.get("mcap_cr", 0))
                _ps_for_fcf   = _fvn(ps_v)
                _em_for_fcf   = _fvn(em)
                _em_pct_fcf   = _em_for_fcf * 100 if 0 < _em_for_fcf < 2 else _em_for_fcf

                if _fcf_direct != 0:
                    stock["fcf"]  = round(_fcf_direct, 2)
                    _fcf_use = _fcf_direct
                elif _op_cf != 0:
                    stock["fcf"]  = round(_op_cf, 2)
                    _fcf_use = _op_cf
                elif _ps_for_fcf > 0 and _em_pct_fcf > 3 and _mcap_for_fcf > 0:
                    # Derive: FCF ≈ Revenue × EBITDA_margin; Revenue = MCap/P_S
                    _rev_est = _mcap_for_fcf / _ps_for_fcf
                    _fcf_est = round(_rev_est * (_em_pct_fcf / 100) * 0.7, 2)  # 70% of EBITDA
                    stock["fcf"]  = _fcf_est
                    _fcf_use = _fcf_est
                else:
                    stock["fcf"] = "—"
                    _fcf_use = 0

                # FCF Yield = FCF / MCap × 100
                _fcfy_direct = _fvn(fcfy_v)
                if _fcfy_direct > 0:
                    stock["fcf_yield"] = round(_fcfy_direct, 4)
                elif _fcf_use != 0 and _mcap_for_fcf > 0:
                    stock["fcf_yield"] = round(_fcf_use / _mcap_for_fcf * 100, 4)
                else:
                    stock["fcf_yield"] = "—"
                stock.setdefault("nd_ebitda",    _fv(nde))
                stock.setdefault("int_coverage", _fv(ic))
                # Valuation ratios — only show if yfinance returned a value
                stock.setdefault("ps",           _fv(ps_v) if _fvn(ps_v) > 0 else "—")
                stock.setdefault("ev_ebitda",    _fv(ev_v) if _fvn(ev_v) > 0 else "—")
                # PEG Ratio — 4-tier fallback for maximum coverage
                _peg_raw  = _fvn(peg_v)
                _pe_v     = _fvn(pe)
                _pat_g    = _fvn(pat_yoy_v)   # PAT YoY % from earningsGrowth
                _rev_g    = _fvn(rev_yoy_v)   # Rev YoY % from revenueGrowth
                # Tier 1: direct yfinance pegRatio
                if _peg_raw > 0 and _peg_raw < 100:
                    stock.setdefault("peg", round(_peg_raw, 2))
                # Tier 2: PE / PAT growth
                elif _pe_v > 0 and _pat_g > 0:
                    stock.setdefault("peg", round(_pe_v / _pat_g, 2))
                # Tier 3: PE / Rev growth (proxy when PAT growth unavailable)
                elif _pe_v > 0 and _rev_g > 0:
                    stock.setdefault("peg", round(_pe_v / _rev_g, 2))
                # Tier 4: PE / sustainable growth rate (ROE × retention ratio)
                # g = ROE × (1 - payout_ratio/100) — standard Gordon growth
                elif _pe_v > 0:
                    _roe_for_peg = _fvn(stock.get("roe_num", 0)) or                                    (_fvn(ey) * _fvn(pb) if _fvn(ey)>0 and _fvn(pb)>0 else 0)
                    _pay_for_peg = _fvn(payout_v)
                    if _roe_for_peg > 0:
                        _ret  = 1 - min(_pay_for_peg / 100, 0.9)  # retention ratio
                        _g_sg = _roe_for_peg * _ret               # sustainable growth %
                        if _g_sg > 0:
                            stock.setdefault("peg", round(_pe_v / _g_sg, 2))
                        else:
                            stock.setdefault("peg", "—")
                    else:
                        stock.setdefault("peg", "—")
                else:
                    stock.setdefault("peg", "—")
                # P/CF: 4-tier fallback for maximum coverage
                _fy_pcf   = _fvn(fcfy_v)
                _fcf_raw  = _fvn(fcf)
                _opcf_raw = _fvn(op_cf_v)
                _mcap_v   = _fvn(stock.get("mcap_cr", 0))
                _ps_pcf   = _fvn(ps_v)
                _em_pcf   = _fvn(em)                        # EBITDA margin (fraction)
                _em_pct_pcf = _em_pcf*100 if 0<_em_pcf<2 else _em_pcf
                # Tier 1: FCF yield (most precise)
                if _fy_pcf > 0:
                    stock.setdefault("p_cf", round(100.0 / _fy_pcf, 1))
                # Tier 2: MCap / Operating CF (standard)
                elif _opcf_raw > 0 and _mcap_v > 0:
                    stock.setdefault("p_cf", round(_mcap_v / _opcf_raw, 1))
                # Tier 3: MCap / FCF
                elif _fcf_raw > 0 and _mcap_v > 0:
                    stock.setdefault("p_cf", round(_mcap_v / _fcf_raw, 1))
                # Tier 4: P/S ÷ EBITDA_margin (derived proxy)
                # P/CF ≈ (MCap/Revenue) / EBITDA_margin = P/S / em
                # Valid because Operating CF ≈ Revenue × EBITDA_margin for most businesses
                elif _ps_pcf > 0 and _em_pct_pcf > 3:
                    _pcf_derived = round(_ps_pcf / (_em_pct_pcf / 100), 1)
                    stock.setdefault("p_cf", _pcf_derived if 1 < _pcf_derived < 500 else "—")
                else:
                    stock.setdefault("p_cf", "—")
                # Numeric fields — 0 safe for arithmetic
                stock.setdefault("pe",            _fvn(pe))
                stock.setdefault("pb",            _fvn(pb))
                stock.setdefault("earnings_yield",_fvn(ey))
                stock.setdefault("earn_yield",    _fvn(ey))
                # Normalise div_yield to % regardless of how it was stored:
                # DB stores fraction (0.0224), old rows may store % (2.24) or bad (224)
                _dy_raw = _fvn(dy)
                if _dy_raw <= 0:
                    _dy_pct = 0.0
                elif _dy_raw > 12:
                    _dy_pct = round(_dy_raw / 100, 4)   # 22→0.22%, 224→2.24%
                elif _dy_raw < 1.0:
                    _dy_pct = round(_dy_raw * 100, 4)   # 0.0224 → 2.24%
                else:
                    _dy_pct = round(_dy_raw, 4)          # 2.24 already %
                stock["div_yield"] = _dy_pct   # direct assignment: always normalise
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

            # ── Technical alignment bonus for priority_score ─────────────────
            # Rewards stocks where Supertrend AND MACD are both BUY
            # This helps them rank higher in Stage 3 priority sort
            _st_pa   = str(stock.get("supertrend",  "NEUTRAL")).upper()
            _macd_pa = str(stock.get("macd_signal", "NEUTRAL")).upper()
            _ps_curr = _sf(stock.get("priority_score", 0), 0)
            if "BUY" in _st_pa and "BUY" in _macd_pa:
                stock["priority_score"] = round(_ps_curr + 8, 2)   # both aligned → +8
            elif "BUY" in _st_pa or "BUY" in _macd_pa:
                stock["priority_score"] = round(_ps_curr + 3, 2)   # one signal → +3
            elif "SELL" in _st_pa and "SELL" in _macd_pa:
                stock["priority_score"] = round(max(0, _ps_curr - 5), 2)  # both sell → -5

            # ── Sector Stage: recomputed HERE after RSI/MACD/Supertrend loaded ──
            _rsi_rs2  = _sf(stock.get("rsi",   50), 50)
            _macd_rs2 = str(stock.get("macd_signal", "NEUTRAL")).upper()
            _st_rs2   = str(stock.get("supertrend",  "NEUTRAL")).upper()
            _sec_ret2 = _sf(stock.get("4w_chg", 0), 0)
            _2w_ret2  = _sf(stock.get("2w_chg", 0), 0)
            _del_rs2  = _sf(stock.get("delivery_pct", 0), 0)
            _vol_rs2  = _sf(stock.get("vol_ratio", 1.0), 1.0)
            if _rsi_rs2 > 70 and _sec_ret2 > 5:
                stock["rotation_stage"] = "STAGE 3 — MOMENTUM PEAK"
            elif _rsi_rs2 > 70 and "SELL" in _macd_rs2:
                stock["rotation_stage"] = "STAGE 4 — DISTRIBUTION"
            elif _sec_ret2 < -3 and _rsi_rs2 < 45:
                stock["rotation_stage"] = "STAGE 4 — DISTRIBUTION"
            elif "BUY" in _st_rs2 and "BUY" in _macd_rs2 and _sec_ret2 > 2:
                stock["rotation_stage"] = "STAGE 2 — CONFIRMED UPTREND"
            elif 40 < _rsi_rs2 <= 58 and "BUY" in _macd_rs2 and _2w_ret2 > _sec_ret2:
                stock["rotation_stage"] = "STAGE 1 — EARLY ACCUMULATION"
            elif _del_rs2 >= 65 and _vol_rs2 >= 1.8 and _rsi_rs2 < 60:
                stock["rotation_stage"] = "STAGE 1 — EARLY ACCUMULATION"
            else:
                stock["rotation_stage"] = "NEUTRAL"

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 5B: FAIR VALUE ENGINE
        # ─────────────────────────────────────────────────────────────────────
        from analysis.fair_value_engine import FairValueEngine
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

            # ── Failsafe: normalise div_yield right before FV engine call ──
            # Ensures correct % regardless of which path set stock["div_yield"]
            # Handles all DB formats: fraction (0.022), % (2.2), bad unit (220)
            _dy_pre = float(stock.get("div_yield", 0) or 0)
            if _dy_pre > 12:
                stock["div_yield"] = round(_dy_pre / 100, 4)   # 220 → 2.2%
            elif 0 < _dy_pre < 1.0:
                stock["div_yield"] = round(_dy_pre * 100, 4)   # 0.022 → 2.2%
            # else: already correct (0 = no dividend, 1-25 = valid %)

            models    = fv_engine.calculate_all_models(stock, beta, growth_3yr)
            fv_result = fv_engine.get_composite_fair_value(
                models, _sf(stock.get("close", 1), 1)
            )
            stock.update(models)
            stock.update(fv_result)

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 6: SCORING + KEY FIXES + PRICE TARGETS
        # ─────────────────────────────────────────────────────────────────────
        from analysis.scoring_engine import ScoringEngine
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
                # Session 16: added +2 for strong-but-not-overbought RSI (60-70)
                # so a truly bullish momentum stock can reach the 100 ceiling.
                if   _rsi_s > 70: _ts += 8   # still capped — overbought deserves no bonus
                elif _rsi_s > 60: _ts += 10  # sweet spot: strong + not overextended
                elif _rsi_s > 50: _ts += 4
                elif _rsi_s < 40: _ts -= 8
                elif _rsi_s < 50: _ts -= 4
                # ADX (trend strength)
                # Session 16: added a +2 tier for very strong trends (>30)
                if   _adx_s > 30: _ts += 7   # established trend
                elif _adx_s > 25: _ts += 5
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
                # H3a: Stochastic K — oversold recovery (20-40 is accumulation zone)
                _stk_s = _sf(stock.get("stoch_k", 50), 50)
                if   20 < _stk_s <= 40:  _ts += 5   # oversold recovery — bullish
                elif _stk_s > 80:        _ts -= 3   # overbought — caution
                elif _stk_s <= 20:       _ts += 2   # deeply oversold — potential reversal
                # H3b: MFI — money flow confirmation
                _mfi_s = _sf(stock.get("mfi", 50), 50)
                if   _mfi_s > 60:        _ts += 4   # strong money inflow
                elif _mfi_s < 30:        _ts -= 3   # money outflow
                # Session 16: SMA 200 trend alignment (classic long-term trend signal
                # that was missing from the scorer). +3 when CMP above SMA 200
                # (bull regime confirmed); −3 when below (bear regime).
                _sma200 = _sf(stock.get("sma_200", 0), 0)
                _cmp_ts = _sf(stock.get("close", 0), 0)
                if   _sma200 > 0 and _cmp_ts > _sma200 * 1.02: _ts += 3   # clearly above
                elif _sma200 > 0 and _cmp_ts < _sma200 * 0.98: _ts -= 3   # clearly below
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

                # H1a: PAT YoY growth — earnings momentum
                _pat_f = _sf(stock.get("pat_yoy", 0), 0)
                if   _pat_f > 20:      _fs += 8
                elif _pat_f > 10:      _fs += 4
                elif _pat_f > 0:       _fs += 2
                elif _pat_f < -10:     _fs -= 7

                # H1b: Revenue YoY growth — top-line strength
                _rev_f = _sf(stock.get("rev_yoy", stock.get("revenue_growth", 0)), 0)
                if   _rev_f > 15:      _fs += 5
                elif _rev_f > 8:       _fs += 3
                elif _rev_f > 0:       _fs += 1
                elif _rev_f < -5:      _fs -= 4

                # H1c: FCF Yield — cash generation quality
                _fcf_y = _sf(stock.get("fcf_yield", 0), 0)
                if   _fcf_y > 6:       _fs += 6
                elif _fcf_y > 3:       _fs += 3
                elif _fcf_y < 0:       _fs -= 5

                # ── H2: New growth/quality fields (CAGR + margin trend) ──────
                # 3Y CAGR is a structural signal — harder to fake than 1Y YoY
                # Only applied when data is available (non-zero means yfinance fetched it)

                # H2a: PAT CAGR 3Y — sustained earnings compounding
                _pc3 = _sf(stock.get("pat_cagr_3y", 0), 0)
                if   _pc3 > 20:        _fs += 8
                elif _pc3 > 10:        _fs += 4

                # H2b: Revenue CAGR 3Y — top-line compounding (moat signal)
                _rc3 = _sf(stock.get("rev_cagr_3y", 0), 0)
                if   _rc3 > 15:        _fs += 5
                elif _rc3 > 8:         _fs += 3

                # H2c: EBITDA CAGR 1Y — operating leverage improving
                _ec1 = _sf(stock.get("ebitda_cagr_1y", 0), 0)
                if   _ec1 > 15:        _fs += 4
                elif _ec1 > 8:         _fs += 2

                # H2d: Margin Expansion — 3 consecutive quarters of rising NPM
                # Strongest single quality signal: not a one-off quarter
                _mexp = str(stock.get("margin_expansion", "NO") or "NO").upper()
                if _mexp == "YES":     _fs += 5

                # H2e: Most-recent quarterly NPM supplements static TTM margin
                # Only applies when TTM margin is missing/stale
                _npm1 = _sf(stock.get("npm_q1", 0), 0)
                _nm_f2 = _sf(stock.get("npm", _nm_f), _nm_f)
                if _npm1 > 0 and _nm_f <= 0:
                    _fs += 4            # recovering profitability not yet in TTM
                elif _npm1 > _nm_f2 * 1.1 and _nm_f2 > 0:
                    _fs += 2            # accelerating above TTM average

                stock["fundamental_score"] = max(0, min(100, round(_fs, 1)))

            # ── Piotroski F-Score (display only — populates Excel piotroski_f col) ─
            # Session 14: wire up FundamentalEngine.calculate_piotroski_f_score.
            # Pure display field — does NOT feed back into fundamental_score to
            # avoid changing scoring behaviour on existing stocks.
            # Most YoY inputs (debt prev, current_ratio prev, etc.) are not in
            # free data, so realistic output is 2-4 of 9; full 9 needs paid data.
            if "piotroski_f" not in stock or not stock.get("piotroski_f"):
                try:
                    from analysis.fundamental_engine import FundamentalEngine as _FE
                    stock["piotroski_f"] = _FE.calculate_piotroski_f_score(stock)
                except Exception:
                    stock.setdefault("piotroski_f", 0)

            # ── Safety score from pledge/debt ────────────────────────────────
            if not stock.get("safety_score"):
                _ss = 50.0
                _pled = _sf(stock.get("pledge_pct", 0), 0)
                _bet  = _sf(stock.get("beta", 1.0), 1.0)
                _de2  = stock.get("de_ratio_num", _sf(stock.get("debt_equity", 0), 0))
                if _pled > 20: _ss -= 15
                elif _pled > 10: _ss -= 7
                # Session 16: reward zero pledge (was neutral) — clean cap
                # structure is a genuine safety signal, not just an absence of risk.
                elif _pled == 0: _ss += 4
                if _bet > 1.5: _ss -= 5
                elif _bet < 0.8: _ss += 5
                if _de2 > 2.0: _ss -= 10
                elif _de2 < 0.3 and _de2 > 0: _ss += 5   # very low debt = safer
                # H2a: FCF — negative FCF is a risk signal
                _fcf_ss = _sf(stock.get("fcf", 0), 0)
                if   _fcf_ss < 0:      _ss -= 8
                elif _fcf_ss > 0:      _ss += 3
                # H2b: BS Health status from re-evaluation
                _bs_ss = str(stock.get("bs_status", "HEALTHY"))
                if   _bs_ss == "ALERT":  _ss -= 15
                elif _bs_ss == "WATCH":  _ss -= 5
                elif _bs_ss == "HEALTHY" and _fcf_ss > 0: _ss += 3
                # Margin expansion = reducing operational risk
                if str(stock.get("margin_expansion","NO") or "NO").upper() == "YES":
                    _ss += 3
                # Session 16: additional safety branches so a genuinely safe
                # stock can reach the 100 ceiling (was capped ~69 before).
                # All gated — only fire when inputs are meaningful (not zero).
                # Net cash position (cash > total debt = very defensive)
                _cash_ss = _sf(stock.get("cash", 0), 0)
                _debt_ss = _sf(stock.get("total_debt", 0), 0)
                if _cash_ss > 0 and _cash_ss > _debt_ss: _ss += 6
                # Strong interest coverage (can service debt easily)
                _ic_ss = _sf(stock.get("int_coverage", 0), 0)
                if   _ic_ss > 10: _ss += 5
                elif _ic_ss > 5:  _ss += 2
                # Low ND/EBITDA (deleveraging quickly / negligible debt load)
                _nde_ss = _sf(stock.get("nd_ebitda", 0), 0)
                if _nde_ss != 0 and _nde_ss < 1.0: _ss += 5
                # Piotroski quality floor — high F-Score correlates with safety
                _pio_ss = _sf(stock.get("piotroski_f", 0), 0)
                if   _pio_ss >= 7: _ss += 6
                elif _pio_ss >= 5: _ss += 3
                # Anti-trigger guard clean — Altman/Beneish not flagging risk
                if not stock.get("spike_suppressed", False): _ss += 5
                stock["safety_score"] = max(0, min(100, round(_ss, 1)))

            # ── Sentiment score from smart money / FII trend ─────────────────
            if not stock.get("sentiment_score"):
                # Session 15: derive fii_3q_trend INLINE before reading it.
                # Previously this derivation happened ~250 lines later (C2 block)
                # so sentiment_score always saw "NEUTRAL" — the +10 "FII up"
                # branch was unreachable. ownership_tracker also returns
                # "NEUTRAL" by default because only 1 historical quarter is
                # passed, so fii_qoq is the reliable signal.
                if not stock.get("fii_3q_trend") or stock.get("fii_3q_trend") == "NEUTRAL":
                    _fq_inline = stock.get("fii_qoq", 0)
                    try:
                        _fq_v = float(str(_fq_inline).replace("—","0") or 0)
                        if   _fq_v > 1.0:  stock["fii_3q_trend"] = "UP"
                        elif _fq_v < -1.0: stock["fii_3q_trend"] = "DOWN"
                        else:              stock["fii_3q_trend"] = "NEUTRAL"
                    except (ValueError, TypeError):
                        stock["fii_3q_trend"] = "NEUTRAL"

                _sent = 50.0
                _fii_t = str(stock.get("fii_3q_trend", "NEUTRAL"))
                _sm    = str(stock.get("smart_money_sentiment", "NEUTRAL"))
                _ins   = str(stock.get("insider_buy_alert", "NO"))
                if _fii_t == "UP":              _sent += 10
                if _sm == "ACCUMULATION":       _sent += 10
                if _ins == "YES":               _sent += 8
                if _fii_t == "DOWN":            _sent -= 10

                # Session 16: additional sentiment branches so a stock with
                # broadly bullish flow can reach the 100 ceiling (was 78).
                # Each branch gated on real data — fires only when meaningful.
                # Promoter QoQ buying (insider signal — promoters voting with wallet)
                try:
                    _pq_sent = float(str(stock.get("promoter_qoq", 0)).replace("—","0") or 0)
                except (ValueError, TypeError):
                    _pq_sent = 0
                if   _pq_sent >  0.5:  _sent += 5
                elif _pq_sent < -0.5:  _sent -= 5
                # DII QoQ — domestic institutional accumulation
                try:
                    _dq_sent = float(str(stock.get("dii_qoq", 0)).replace("—","0") or 0)
                except (ValueError, TypeError):
                    _dq_sent = 0
                if   _dq_sent >  0.5:  _sent += 6
                elif _dq_sent >  0.3:  _sent += 4
                elif _dq_sent < -0.3:  _sent -= 3
                # News sentiment (populated by ai_analyst when credits available)
                _news_sent = str(stock.get("news_sentiment", "NEUTRAL") or "NEUTRAL").upper()
                if   _news_sent == "POSITIVE": _sent += 4
                elif _news_sent == "NEGATIVE": _sent -= 5
                # High delivery % — sustained institutional order flow
                _del_sent = _sf(stock.get("delivery_pct", 0), 0)
                if   _del_sent > 70: _sent += 4
                elif _del_sent > 60: _sent += 2
                elif 0 < _del_sent < 30: _sent -= 3
                # Pledge direction — ownership quality trend
                _pdir = str(stock.get("pledge_direction", "—") or "—").upper()
                if   "FALL" in _pdir:  _sent += 3
                elif "RIS"  in _pdir:  _sent -= 5

                stock["sentiment_score"] = max(0, min(100, round(_sent, 1)))

            # Section 3I: Early Entry Score — computed here after vol_ratio + technicals are populated
            try:
                from analysis.early_detection_engine import EarlyDetectionEngine
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

                _del_e  = _sf(stock.get("delivery_pct", 0), 0)
                _mos_e  = _sf(stock.get("mos_pct", stock.get("upside", 0)), 0)
                _verd_e = str(stock.get("verdict", ""))
                _score_e= _sf(stock.get("composite_score", 0), 0)
                _cmp_e  = _sf(stock.get("close", 0), 0)
                _h52_e  = _sf(stock.get("high_52w", 0), 0)

                # Signal: Vol Surge + RSI Accumulation (15 pts)
                if _vol_r >= 1.8 and 50 < _rsi_e <= 72:
                    _escore += 15
                    _esigs.append("VOL SURGE + RSI ACCUMULATION")

                # Signal: Momentum (10 pts) — relaxed: 2w > 2% (was: 2w>1.5 AND 4w<2w)
                if _2w_e > 2.0:
                    _escore += 10
                    _esigs.append("MOMENTUM BUILDING")

                # Signal: Trend Confluence (12 pts)
                if _st_e == "BUY" and _macd_e == "BUY":
                    _escore += 12
                    _esigs.append("TREND CONFLUENCE")

                # Signal: Institutional Footprint (10 pts)
                if _del_e >= 70 and _vol_r >= 2.0:
                    _escore += 10
                    _esigs.append("INSTITUTIONAL FOOTPRINT")

                # Signal: Dual-Listed Discovery (8 pts)
                if _etag == "DUAL_LISTED" and _vol_r >= 1.5:
                    _escore += 8
                    _esigs.append("DUAL-LISTED DISCOVERY")

                # Signal: Value + Verdict (10 pts / 5 pts)
                if _mos_e > 25 and _verd_e == "BUY":
                    _escore += 10
                    _esigs.append("DEEP VALUE + BUY")
                elif _mos_e > 10 and _verd_e in ("BUY", "WATCHLIST"):
                    _escore += 5
                    _esigs.append("VALUE OPPORTUNITY")

                # Signal: 52W Breakout (10 pts) — CMP within 5% of 52W High + vol + uptrend
                # Stock breaking multi-month resistance with institutional backing
                if _h52_e > 0 and _cmp_e > 0:
                    _dist_pct = (_h52_e - _cmp_e) / _h52_e * 100
                    if _dist_pct <= 5.0 and _vol_r > 2.0 and _st_e == "BUY":
                        _escore += 10
                        _esigs.append("52W BREAKOUT")

                # Signal: Score + Technical Convergence (8 pts)
                # Strong fundamentals (score>=70) meeting technical confirmation
                if _score_e >= 70 and _rsi_e > 60 and _st_e == "BUY":
                    _escore += 8
                    _esigs.append("SCORE CONVERGENCE")

                # Signal: FII/Promoter Accumulation (8 pts each — paid QoQ data)
                _fii_e = stock.get("fii_qoq", 0)
                try:
                    if float(str(_fii_e).replace("—","0") or 0) > 1.0:
                        _escore += 8
                        _esigs.append("FII ACCUMULATION")
                except (ValueError, TypeError):
                    pass
                _pro_e = stock.get("promoter_qoq", 0)
                try:
                    if float(str(_pro_e).replace("—","0") or 0) > 1.0:
                        _escore += 8
                        _esigs.append("PROMOTER ACCUMULATION")
                except (ValueError, TypeError):
                    pass

                _escore = min(100, _escore)
                stock["early_entry_score"] = _escore
                # Badge threshold 50 (was 70) — consistent with Gold sheet + bonus threshold.
                # Label thresholds adjusted: EARLY MOVER>=50, AHEAD OF CONSENSUS>=35.
                # Max achievable EE with free data is ~55 (no FII/Promoter QoQ).
                stock["early_mover_badge"] = "EARLY MOVER" if _escore >= 50 else ""
                stock["early_label"] = (
                    "EARLY MOVER — Act before the crowd" if _escore >= 50 else
                    "AHEAD OF CONSENSUS" if _escore >= 35 else "EMERGING"
                )
                if _esigs:
                    stock["early_signals"] = " | ".join(_esigs)
            except Exception as _ee:
                stock.setdefault("early_entry_score", 0)
                stock.setdefault("early_mover_badge", "")
                stock.setdefault("early_label", "EMERGING")

            # ── F-Score second pass (definitive) ─────────────────────────────
            # The first pass (inside `if sym in _fm_map`) reads raw FM tuple
            # values which may be 0 if the DB column was NULL for that stock.
            # This second pass runs AFTER all stock fields are fully set
            # (roa, fcf, debt_equity, current_ratio, gross_margin, roe, cash etc.)
            # so it always has the complete picture.
            # Only overwrites if the first pass left "—" (i.e. fallback ran).
            if stock.get("piotroski_f") in ("—", None, "", 0):
                try:
                    def _pfv(k, d=0.0):
                        v = stock.get(k, d)
                        if v in ("—", None, ""): return d
                        try: return float(v)
                        except: return d
                    _p1 = _pfv("roa",            0) > 0
                    _p2 = _pfv("fcf",            0) > 0
                    _p3 = _pfv("pat_yoy",        0) > 0
                    _p4 = _pfv("de_ratio_num",   _pfv("debt_equity", 99)) < 1.0
                    _p5 = _pfv("current_ratio",  0) > 1.0
                    _p6 = _pfv("gross_margin",   0) > 15
                    _p7 = _pfv("rev_yoy",        0) > 0
                    _p8 = _pfv("roe_num",        0) > 10
                    _p9 = _pfv("cash",           0) > 0
                    # Count how many fields are genuinely available
                    _p_data = sum(1 for v in [
                        _pfv("roa",0), _pfv("fcf",0),
                        _pfv("de_ratio_num", _pfv("debt_equity",0)),
                        _pfv("gross_margin",0), _pfv("roe_num",0)
                    ] if v != 0)
                    if _p_data >= 2:   # lower threshold — at least roa+gm or fcf+roe
                        stock["piotroski_f"] = sum([_p1,_p2,_p3,_p4,_p5,_p6,_p7,_p8,_p9])
                except Exception:
                    pass

            # ── BS Health re-evaluation with FM-enriched data ──────────────
            # First pass (L465) had no FM data → always HEALTHY
            # Re-run now that debt_equity, CR, FCF, total_debt, cash are populated
            try:
                _de_re   = float(str(stock.get("debt_equity",  stock.get("de_ratio_num", 0)) or 0))
                _cr_re   = float(str(stock.get("current_ratio", 0) or 0).replace("—","0") or 0)
                _fcf_re  = float(str(stock.get("fcf",  0) or 0).replace("—","0") or 0)
                _td_re   = float(str(stock.get("total_debt", 0) or 0).replace("—","0") or 0)
                _cash_re = float(str(stock.get("cash",  stock.get("cash_cr", 0)) or 0).replace("—","0") or 0)
                _roe_re  = float(str(stock.get("roe_num", stock.get("roe", 0)) or 0).replace("—","0") or 0)
                _pledge  = float(str(stock.get("pledge_pct", 0) or 0).replace("—","0") or 0)

                _flags_re = []
                _status_re = "HEALTHY"

                # Positive flags
                if _td_re > 0 and _cash_re >= _td_re:
                    _flags_re.append(f"NET CASH COMPANY (Cash ₹{int(_cash_re)}Cr > Debt ₹{int(_td_re)}Cr)")
                elif _td_re == 0 and _cash_re > 0:
                    _flags_re.append(f"ZERO DEBT | Cash ₹{int(_cash_re)}Cr")

                # Warning flags
                if _de_re > 2.0:
                    _flags_re.append(f"HIGH D/E {round(_de_re,1)}x")
                    _status_re = "WATCH"
                if 0 < _cr_re < 1.0:
                    _flags_re.append(f"LOW LIQUIDITY CR={round(_cr_re,2)}")
                    _status_re = "WATCH"
                if _fcf_re < 0:
                    _flags_re.append("NEGATIVE FCF")
                    _status_re = "WATCH"
                if _td_re > 0 and _cash_re > 0 and (_cash_re / _td_re) < 0.1:
                    _flags_re.append(f"LOW CASH COVER {round(_cash_re/_td_re,2)}x")
                    _status_re = "WATCH"
                if _pledge > 20:
                    _flags_re.append(f"HIGH PLEDGE {round(_pledge,1)}%")
                    _status_re = "ALERT"

                # Alert flags
                if _de_re > 3.0:
                    _status_re = "ALERT"
                if _roe_re > 0 and _de_re > 2.0 and _fcf_re < 0:
                    _flags_re.append("LEVERAGED + NEGATIVE FCF")
                    _status_re = "ALERT"

                # Only update if we have real data and found something meaningful
                if _flags_re:
                    stock["bs_status"] = _status_re
                    stock["bs_flags"]  = " | ".join(_flags_re)
                elif any(v > 0 for v in [_de_re, _cash_re, _td_re, _roe_re]):
                    # We have real data and no flags — genuinely healthy
                    _note = []
                    if _td_re == 0: _note.append("Debt-free")
                    if _cash_re > 0: _note.append(f"Cash ₹{int(_cash_re)}Cr")
                    if _de_re > 0: _note.append(f"D/E {round(_de_re,2)}x")
                    if _roe_re > 0: _note.append(f"ROE {round(_roe_re,1)}%")
                    stock["bs_status"] = "HEALTHY"
                    stock["bs_flags"]  = " | ".join(_note) if _note else "No red flags detected"
            except Exception:
                pass   # keep existing bs_status/bs_flags from first pass

            # Composite score + verdict
            score_result = scoring.calculate_composite_score(stock)
            stock.update(score_result)

            # ── Derive ghost keys for Storm/Sentiment/EDE before scoring ──────
            # These keys are READ by scoring_engine but were never populated,
            # causing storm/sentiment scores to always be near baseline.
            # Derived from data already available in the stock dict.

            # C1a: fcf_positive_4q — True if FCF > 0
            _fcf_ghost = _sf(stock.get("fcf", 0), 0)
            stock["fcf_positive_4q"] = bool(_fcf_ghost > 0)

            # C1b: promoter_q_increase — True if promoter holding rose QoQ
            _proq_ghost = stock.get("promoter_qoq", 0)
            try:
                stock["promoter_q_increase"] = float(
                    str(_proq_ghost).replace("—","0") or 0) > 0.3
            except (ValueError, TypeError):
                stock["promoter_q_increase"] = False

            # C1c: fii_buy_3q — True if FII holding rose QoQ
            _fiiq_ghost = stock.get("fii_qoq", 0)
            try:
                stock["fii_buy_3q"] = float(
                    str(_fiiq_ghost).replace("—","0") or 0) > 0.3
            except (ValueError, TypeError):
                stock["fii_buy_3q"] = False

            # C1d: rev_growth_yoy — revenue YoY growth %
            stock["rev_growth_yoy"] = _sf(
                stock.get("rev_yoy", stock.get("revenue_growth", 0)), 0)

            # C2: fii_3q_trend — derive from fii_qoq for sentiment score
            if not stock.get("fii_3q_trend") or stock.get("fii_3q_trend") == "NEUTRAL":
                _fii_q_sent = stock.get("fii_qoq", 0)
                try:
                    _fq = float(str(_fii_q_sent).replace("—","0") or 0)
                    if   _fq > 1.0:  stock["fii_3q_trend"] = "UP"
                    elif _fq < -1.0: stock["fii_3q_trend"] = "DOWN"
                    else:            stock["fii_3q_trend"] = "NEUTRAL"
                except (ValueError, TypeError):
                    stock["fii_3q_trend"] = "NEUTRAL"

            # C3: promoter_buying_30d — for Early Detection Engine
            try:
                _pq_ede = float(str(stock.get("promoter_qoq",0)).replace("—","0") or 0)
                stock["promoter_buying_30d"] = bool(_pq_ede > 0.5)
            except (ValueError, TypeError):
                stock["promoter_buying_30d"] = False

            # C4: de_ratio — normalise key for scoring_engine.py
            # scoring_engine reads 'de_ratio' but master_funnel stores
            # the value as 'de_ratio_num' / 'debt_equity'. Bridge both.
            stock['de_ratio'] = _sf(
                stock.get('de_ratio_num',
                stock.get('debt_equity', 1.0)), 1.0)

            # Storm score
            storm = scoring.calculate_storm_score(stock, market_vix=12.0,
                                                   market_off_peak=3.0)
            if storm:
                stock.update(storm)
            else:
                stock.setdefault("storm_score", 0)
                stock.setdefault("storm_label", "N/A")

            # ── Risk Level: computed AFTER BS status and de_ratio_num are set ───
            _cap_tr    = str(stock.get("cap_category", "")).upper()
            _beta_tr   = _sf(stock.get("beta", 1.0), 1.0)
            _de_tr     = _sf(stock.get("de_ratio_num", stock.get("debt_equity", 0)), 0)
            _bs_tr     = str(stock.get("bs_status", "HEALTHY"))
            _pledge_tr = _sf(stock.get("pledge_pct", 0), 0)
            _is_large  = "LARGE" in _cap_tr
            _is_small  = "SMALL" in _cap_tr or "MICRO" in _cap_tr
            if _bs_tr == "ALERT" or _pledge_tr > 20 or (_de_tr > 2.5 and _is_small):
                stock["risk_level"] = "HIGH"
            elif _is_large and _de_tr < 0.5 and _beta_tr < 0.9:
                stock["risk_level"] = "LOW"
            elif _is_large and _de_tr < 1.0 and _beta_tr < 1.2:
                stock["risk_level"] = "LOW"
            elif _is_small or _de_tr > 1.5 or _beta_tr > 1.5:
                stock["risk_level"] = "HIGH"
            else:
                stock["risk_level"] = "MEDIUM"

            # Spike Score — call SpikeScreener with correct key mappings
            try:
                from analysis.spike_screener import SpikeScreener
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
                # Session 15: enforce Section 3H anti-trigger guard on displayed
                # spike_count. Previously a pledge>20 / Altman / Beneish failure
                # set spike_suppressed=True (which cost −10 via risk_flag_active)
                # but the Excel cell still showed the raw 4-6 spike count — the
                # tooltip's "Suppressed to 0 if ..." was silently broken.
                # Session 18: don't add a [SUPPRESSED] prefix to the Early
                # Signals column — when triggers are suppressed, simply clear
                # them (cleaner UX; guard_reasons column still explains why).
                if stock.get("spike_suppressed"):
                    stock["spike_count"] = 0
                    stock["spike_score"] = 0
                    stock["spike_triggers"] = ""
            except Exception as _esp:
                stock.setdefault("spike_count", 0)
                stock.setdefault("spike_score", 0)
            # ── Time Horizon: computed AFTER verdict+score+spike are all set ────
            _verd_tr  = str(stock.get("verdict", "WATCHLIST"))
            _spike_tr = int(stock.get("spike_count", stock.get("spike_count", 0)) or 0)
            _st_tr    = str(stock.get("supertrend",  "NEUTRAL")).upper()
            _macd_tr  = str(stock.get("macd_signal", "NEUTRAL")).upper()
            _score_tr = _sf(stock.get("composite_score", 0), 0)
            if _verd_tr == "BUY" and _spike_tr >= 2:
                stock["horizon"] = "SHORT TERM"
            elif _verd_tr == "BUY" and "BUY" in _st_tr and "BUY" in _macd_tr:
                stock["horizon"] = "POSITIONAL"
            elif _verd_tr == "BUY" and _score_tr >= 68:
                stock["horizon"] = "POSITIONAL"
            elif _verd_tr == "BUY":
                stock["horizon"] = "LONG TERM"
            elif _verd_tr == "WATCHLIST":
                stock["horizon"] = "POSITIONAL"
            else:
                stock["horizon"] = "LONG TERM"


            # Vol ratio (use DB-enriched value if already set, else calculate)
            if not stock.get("vol_ratio"):
                from database.data_bridge import get_20d_avg_vol
                avg_vol = get_20d_avg_vol(str(stock.get("symbol", "") or ""))
                curr_vol = _sf(stock.get("volume", 0), 0)
                stock["vol_ratio"] = round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.0
                stock["days_since_analysis"] = 0  # prevent O5 firing for all stocks

            # Smart money signals — use available shareholding + technical data
            if not stock.get("smart_money_signals"):
                signals = []
                if str(stock.get("smart_money_sentiment","NEUTRAL")) == "ACCUMULATION":
                    signals.append("INST ACCUMULATION")
                if str(stock.get("insider_buy_alert","NO")) == "YES":
                    signals.append("INSIDER BUYING")
                _fii_q = stock.get("fii_qoq", 0)
                try:
                    if float(str(_fii_q).replace("—","0") or 0) > 0.5:
                        signals.append("FII INCREASING")
                except (ValueError, TypeError):
                    pass
                _pro_q = stock.get("promoter_qoq", 0)
                try:
                    if float(str(_pro_q).replace("—","0") or 0) > 0.5:
                        signals.append("PROMOTER BUYING")
                except (ValueError, TypeError):
                    pass
                _del_sm = _sf(stock.get("delivery_pct", 0), 0)
                _vol_sm = _sf(stock.get("vol_ratio", 1.0), 1.0)
                _rsi_sm = _sf(stock.get("rsi", 50), 50)
                if _del_sm >= 70 and _vol_sm >= 2.0:
                    signals.append("HIGH DELIVERY BUYING")
                if 45 < _rsi_sm <= 60 and _vol_sm >= 1.5 and "HIGH DELIVERY BUYING" not in signals:
                    signals.append("RSI ACCUMULATION ZONE")
                stock["smart_money_signals"] = " | ".join(signals) if signals else "NEUTRAL"

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

            # early_signals — combine spike triggers + EE-scorer labels + mover badge
            # Session 20: previously this block overwrote stock["early_signals"],
            # wiping the inline EE signal labels (VOL SURGE + RSI ACCUMULATION,
            # TREND CONFLUENCE, INSTITUTIONAL FOOTPRINT, DEEP VALUE + BUY, etc.)
            # that the EE scorer had written at ~line 1775. Those labels are what
            # explain the EE score — without them the user sees "EE=55" with no
            # visible reason. Fix: preserve prior early_signals content and MERGE
            # spike triggers + badge/label on top (de-duplicated).
            _ee_prev_signals = stock.get("early_signals", "")
            _early_sigs = []
            if _ee_prev_signals and _ee_prev_signals not in ("—", ""):
                _early_sigs += [s.strip() for s in str(_ee_prev_signals).split("|") if s.strip()]
            _spike_trigs = stock.get("spike_triggers", "")
            if _spike_trigs and _spike_trigs != "—":
                for s in str(_spike_trigs).split("|"):
                    s = s.strip()
                    if s and s not in _early_sigs:
                        _early_sigs.append(s)
            _early_badge = stock.get("early_mover_badge", "")
            if _early_badge and _early_badge not in _early_sigs:
                _early_sigs.append(str(_early_badge))
            _early_label = stock.get("early_label", "")
            if (_early_label and _early_label not in ("EMERGING", "—", "")
                    and _early_label not in _early_sigs):
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
        from reporting.excel_generator import ExcelGeneratorV6
        from reporting.daily_report_generator import DailyReportGenerator

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
        # Fallback: stocks still missing company_name or sector (not in EQUITY_L /
        # NSE index CSVs, e.g. BSE-only, ETFs) get the symbol as display name and
        # "General" as sector so they are never silently dropped from the Excel.
        # Real names will populate on the next full backfill run.
        _fallback_count = 0
        for _s in final_100_list:
            _cn  = str(_s.get("company_name","") or "").strip()
            _sec = str(_s.get("sector","")       or "").strip()
            if _cn  in ("","—","None","0"):
                _s["company_name"] = _s.get("symbol","Unknown")
                _fallback_count += 1
            if _sec in ("","—","None","0"):
                _s["sector"] = "General"
        if _fallback_count:
            print(f"   ℹ️  {_fallback_count} stocks used symbol as fallback name "
                  f"(not in EQUITY_L/NSE CSVs — will resolve after backfill)")

        print(f"   📊 Generating Excel for {len(final_100_list)} stocks...")
        import pytz as _ptz
        _ist = _ptz.timezone("Asia/Kolkata")
        _run_time_ist = datetime.datetime.now(_ist).strftime("%H:%M IST")

        # ── Step A: Load PREVIOUS run scores FIRST (before any write) ────────────
        # This reads yesterday's scores from latest_analysis_results so the
        # Alert Log can show a real delta vs today's scores.
        # If the table is empty (first-ever run) prev_scores = {} → shows '—'.
        try:
            from database.data_bridge import load_latest_analysis_results as _load_prev
            _prev_records = _load_prev()
            _prev_scores = {r["symbol"]: float(r.get("composite_score", 0) or 0)
                            for r in _prev_records}
        except Exception:
            _prev_scores = {}

        # ── Step B: Save TODAY's scores (for NEXT run to use as prev_scores) ─────
        # Must run AFTER loading prev_scores — otherwise we'd compare today
        # against today and Score Δ would always be 0.
        try:
            import sqlite3 as _sq_lar
            _conn_lar = _sq_lar.connect("market_data.db")
            _date_lar = target_date.strftime("%Y-%m-%d")
            for _s_lar in final_100_list:
                _conn_lar.execute(
                    """
                    INSERT OR REPLACE INTO latest_analysis_results
                        (symbol, date, composite_score, early_score,
                         spike_score, storm_score, cfv, mos_pct, verdict)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        str(_s_lar.get("symbol", "") or ""),
                        _date_lar,
                        float(_s_lar.get("composite_score", 0) or 0),
                        float(_s_lar.get("early_entry_score", 0) or 0),
                        int(_s_lar.get("spike_count", 0) or 0),
                        float(_s_lar.get("storm_score", 0) or 0),
                        float(_s_lar.get("cfv", 0) or 0),
                        float(_s_lar.get("mos_pct", 0) or 0),
                        str(_s_lar.get("verdict", "") or ""),
                    )
                )
            _conn_lar.commit()
            _conn_lar.close()
            print(f"   💾 Saved {len(final_100_list)} scores → next run will compute Δ")
        except Exception as _lar_e:
            print(f"   ⚠️  Could not save analysis results: {_lar_e}")
        excel_gen = ExcelGeneratorV6(final_100_list, date_str,
                                     run_time=_run_time_ist,
                                     prev_scores=_prev_scores,
                                     gap_days=_missed_trading_days)
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
        from reporting.email_service import send_analysis_email
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
            from reporting.email_service import send_analysis_email
            send_analysis_email(is_error=True, error_msg=str(e))
        except Exception:
            pass

    finally:
        # Always close BSE session and clean up temp files..
        _close_bse_client()


if __name__ == "__main__":
    run_master_pipeline()