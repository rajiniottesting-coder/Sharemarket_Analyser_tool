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

            # Section 3I: Early Detection Score
            early_data = v7_engine.calculate_section_3I_early_score(stock)
            stock["early_entry_score"] = early_data["early_score"]
            stock["early_mover_badge"] = early_data.get("badge", "")
            stock["early_label"]       = early_data.get("label", "EMERGING")

            # Section 3L: Sector Rotation Stage
            stock["rotation_stage"] = rotation.calculate_rotation_stage(
                stock.get("sector_return", 0),
                stock.get("nifty_return", 0),
                "neutral",
            )

            # Section 4: Balance Sheet Health
            from bs_engine import BalanceSheetEngine
            hist_q = historical_map.get(sym) or {}
            bs_report = BalanceSheetEngine().analyze_bs_health(stock, hist_q)
            stock["bs_status"] = bs_report["status"]
            stock["bs_flags"]  = ", ".join(bs_report["flags"])
            stock["bs_output"] = bs_report.get("output_line", "")

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 5: WEEKLY MOMENTUM DELTAS + DB ENRICHMENT
        # Pull 52w high/low, day_chg, technical indicators, company_name
        # from tables already populated by backfill_history.py
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
                    f"""SELECT fm.symbol, fm.pe_ttm, fm.pb, fm.earn_yield,
                        fm.div_yield, fm.piotroski_f, fm.altman_z, fm.beneish_m,
                        fm.roe, fm.roce, fm.roa, fm.gross_margin, fm.ebitda_margin,
                        fm.net_margin, fm.debt_equity, fm.current_ratio,
                        fm.rev_cagr_1y, fm.rev_cagr_3y, fm.pat_cagr_1y, fm.pat_cagr_3y,
                        fm.rev_yoy, fm.pat_yoy, fm.total_debt_cr, fm.fcf_cr,
                        fm.nd_ebitda, fm.int_coverage
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

            # Symbol master: company_name, sector, cap_category
            _sm_rows = _conn.execute(
                f"SELECT symbol, company_name, sector, cap_category FROM symbol_master "
                f"WHERE symbol IN ({_sym_placeholders})", _syms
            ).fetchall()
            for r in _sm_rows:
                _sm_map[r[0]] = r[1:]

            # Daily prices extra columns for today
            _dp_rows = _conn.execute(
                f"""SELECT symbol, day_chg_pct, week_high_52, week_low_52, vol_50d_avg
                    FROM daily_prices
                    WHERE symbol IN ({_sym_placeholders}) AND date=?""",
                _syms + [_date_str]
            ).fetchall()
            for r in _dp_rows:
                _dp_map[r[0]] = r[1:]

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

            # Enrich from symbol_master
            if sym in _sm_map:
                cn, sec, cap = _sm_map[sym]
                if not stock.get("company_name") and cn:
                    stock["company_name"] = cn
                if not stock.get("sector") or stock.get("sector") == "General":
                    if sec:
                        stock["sector"] = sec
                if not stock.get("cap_category") or stock.get("cap_category") == "—":
                    if cap:
                        stock["cap_category"] = cap

            # Enrich from daily_prices extended columns
            if sym in _dp_map:
                dc, h52, l52, vol50 = _dp_map[sym]
                if dc:  stock["day_change"]  = round(float(dc), 2)
                if h52: stock["high_52w"]    = round(float(h52), 2)
                if l52: stock["low_52w"]     = round(float(l52), 2)
                if vol50 and float(vol50) > 0:
                    curr_vol = float(stock.get("volume", 0) or 0)
                    stock["vol_ratio"] = round(curr_vol / float(vol50), 2)

            # Enrich from fundamental_metrics
            if sym in _fm_map:
                pe, pb, ey, dy, pf, az, bm, roe, roce, roa, gm, em, nm,                 de, cr, rc1, rc3, pc1, pc3, ry, py, td, fcf, nde, ic = _fm_map[sym]
                def _fv(v): return float(v) if v and float(v) != 0 else "—"
                stock.setdefault("pe",           _fv(pe))
                stock.setdefault("pb",           _fv(pb))
                stock.setdefault("earnings_yield", _fv(ey))
                stock.setdefault("earn_yield",   _fv(ey))
                stock.setdefault("div_yield",    _fv(dy))
                stock.setdefault("piotroski_f",  _fv(pf))
                stock.setdefault("altman_z",     _fv(az))
                stock.setdefault("beneish_m",    _fv(bm))
                stock.setdefault("roe",          _fv(roe))
                stock.setdefault("roce",         _fv(roce))
                stock.setdefault("roa",          _fv(roa))
                stock.setdefault("gross_margin", _fv(gm))
                stock.setdefault("ebitda_margin",_fv(em))
                stock.setdefault("npm",          _fv(nm))
                stock.setdefault("debt_equity",  _fv(de))
                stock.setdefault("current_ratio",_fv(cr))
                stock.setdefault("rev_cagr_1y",  _fv(rc1))
                stock.setdefault("rev_cagr_3y",  _fv(rc3))
                stock.setdefault("pat_cagr_1y",  _fv(pc1))
                stock.setdefault("pat_cagr_3y",  _fv(pc3))
                stock.setdefault("rev_yoy",      _fv(ry))
                stock.setdefault("pat_yoy",      _fv(py))
                stock.setdefault("total_debt",   _fv(td))
                stock.setdefault("fcf",          _fv(fcf))
                stock.setdefault("nd_ebitda",    _fv(nde))
                stock.setdefault("int_coverage", _fv(ic))

            # Enrich from shareholding
            if sym in _sh_map:
                pro, proq, pled, pledd, fii, fiiq, dii, diiq, pub = _sh_map[sym]
                def _fv2(v): return float(v) if v and float(v) != 0 else "—"
                stock.setdefault("promoter_pct",    _fv2(pro))
                stock.setdefault("promoter_qoq",    _fv2(proq))
                stock.setdefault("pledge_pct",      _fv2(pled))
                stock.setdefault("pledge_direction",pledd or "—")
                stock.setdefault("fii_pct",         _fv2(fii))
                stock.setdefault("fii_qoq",         _fv2(fiiq))
                stock.setdefault("dii_pct",         _fv2(dii))
                stock.setdefault("dii_qoq",         _fv2(diiq))
                stock.setdefault("public_float",    _fv2(pub))

            # Enrich from technical_indicators
            if sym in _ti_map:
                sma200, st, adx, rsi, macd_s, stk, mfi, obv_s, vwap_s, s1, s2, r1, r2 = _ti_map[sym]
                stock["sma_200"]    = round(float(sma200), 2) if sma200 else "—"
                stock["supertrend"] = st or "NEUTRAL"
                stock["adx"]        = round(float(adx), 2) if adx else "—"
                stock["rsi"]        = round(float(rsi), 2) if rsi else "—"
                stock["macd_signal"]= macd_s or "NEUTRAL"
                stock["stoch_k"]    = round(float(stk), 2) if stk else "—"
                stock["mfi"]        = round(float(mfi), 2) if mfi else "—"
                stock["obv_signal"] = obv_s or "—"
                stock["above_vwap"] = vwap_s or "—"
                stock["support_1"]  = round(float(s1), 2) if s1 else "—"
                stock["support_2"]  = round(float(s2), 2) if s2 else "—"
                stock["resist_1"]   = round(float(r1), 2) if r1 else "—"
                stock["resist_2"]   = round(float(r2), 2) if r2 else "—"

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 5B: FAIR VALUE ENGINE
        # ─────────────────────────────────────────────────────────────────────
        from fair_value_engine import FairValueEngine
        fv_engine = FairValueEngine()
        for stock in final_100_list:
            beta        = float(stock.get("beta", 1.0) or 1.0)
            growth_3yr  = float(stock.get("pat_cagr_3y",
                                stock.get("rev_cagr_3y", 10)) or 10)
            models      = fv_engine.calculate_all_models(stock, beta, growth_3yr)
            fv_result   = fv_engine.get_composite_fair_value(
                models, stock.get("sector", "IT"), float(stock.get("close", 1) or 1)
            )
            stock.update(models)
            stock.update(fv_result)

        # ─────────────────────────────────────────────────────────────────────
        # SECTION 6: SCORING + KEY FIXES + PRICE TARGETS
        # ─────────────────────────────────────────────────────────────────────
        from scoring_engine import ScoringEngine
        scoring = ScoringEngine()
        for stock in final_100_list:
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

            # Spike fields
            stock.setdefault("spike_count", 0)
            stock.setdefault("spike_score", 0)

            # Vol ratio (use DB-enriched value if already set, else calculate)
            if not stock.get("vol_ratio"):
                from data_bridge import get_20d_avg_vol
                avg_vol = get_20d_avg_vol(str(stock.get("symbol", "") or ""))
                curr_vol = float(stock.get("volume", 0) or 0)
                stock["vol_ratio"] = round(curr_vol / avg_vol, 2) if avg_vol > 0 else 1.0

            # Smart money signals
            if not stock.get("smart_money_signals"):
                sentiment = stock.get("smart_money_sentiment", "NEUTRAL")
                insider   = stock.get("insider_buy_alert", "NO")
                signals = []
                if sentiment == "ACCUMULATION": signals.append("INST ACCUMULATION")
                if insider == "YES":            signals.append("INSIDER BUYING")
                stock["smart_money_signals"] = ", ".join(signals) if signals else "NEUTRAL"

            # MoS label
            mos = float(stock.get("mos_pct", 0) or 0)
            if   mos > 40:  stock["mos_label"] = "EXCEPTIONAL"
            elif mos > 25:  stock["mos_label"] = "STRONG"
            elif mos > 10:  stock["mos_label"] = "ADEQUATE"
            elif mos > 0:   stock["mos_label"] = "THIN"
            elif mos > -15: stock["mos_label"] = "SLIGHT PREMIUM"
            else:           stock["mos_label"] = "SIGNIFICANT PREMIUM"

            # Key-name fixes: engine output key → excel column key
            if "earn_yield" in stock and not stock.get("earnings_yield"):
                stock["earnings_yield"] = stock["earn_yield"]
            if not stock.get("total_debt") and stock.get("total_debt_cr"):
                stock["total_debt"] = stock["total_debt_cr"]
            if stock.get("bs_flags") and not stock.get("bs_output"):
                stock["bs_output"] = stock["bs_flags"]

            # Price targets from CMP and CFV
            cmp = float(stock.get("close", 0) or 0)
            cfv = float(stock.get("cfv", 0) or 0)
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
        excel_gen = ExcelGeneratorV6(final_100_list, date_str)
        master_file, gold_file = excel_gen.generate_excel_reports()

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