"""
forensics_engine.py — v10.4

Consolidated forensic & solvency engine with INLINE balance-sheet fetcher.

v10.4 key change (vs v10.3):
- Added fetch_forensic_inputs(symbol) static method that pulls
  ticker.balance_sheet / ticker.cashflow / ticker.income_stmt / ticker.info
  ON DEMAND for a single symbol, returning a dict of the 10 forensic inputs
  the calculation needs (ebit_cr, int_expense_cr, capex_cr, total_assets_cr,
  total_liab_cr, retained_earnings_cr, working_cap_cr, inventory_days,
  receivable_days, payable_days, plus total_debt_cr, cash_cr, q_ebitda_cr,
  q_rev_cr, q_pat_cr, operating_cf_cr as fallbacks).
- This removes the dependency on backfill_history.py's 4th pass being run
  first. master_funnel can call this inline for the top-100 stocks.
- Robust keyword matching: tries multiple row-name variants that yfinance
  uses across versions (Retained Earnings / RetainedEarnings, Total Assets /
  TotalAssets, EBIT / OperatingIncome, InterestExpense / InterestPaid, etc.).
- Safe: never raises. Returns empty dict if Yahoo is unreachable.

v10.3 changes (preserved):
- Returns '—' for metrics whose inputs are missing (was returning 0 or 1.0).
- Does NOT return 'total_debt' in the result dict (would overwrite
  master_funnel's own 3-tier derivation at line 1169-1173).
- Sets 'cash' when balance-sheet cash is present AND existing value missing.
- Accepts both yfinance-style ('total_debt', 'cash', 'ebitda') and our
  DB-canonical ('total_debt_cr', 'cash_cr', 'q_ebitda_cr') key names.
"""


def _num(row, *keys, default=0.0):
    """Read the first non-empty numeric value from any of the provided keys.
    Treats None, '', '—', '--', 'N/A' as missing. Never raises."""
    for k in keys:
        v = row.get(k)
        if v is None or v == "" or str(v) in ("—", "--", "N/A"):
            continue
        try:
            return float(v)
        except (ValueError, TypeError):
            continue
    return float(default)


class ForensicsEngine:

    # ──────────────────────────────────────────────────────────────────────
    # v10.4 INLINE FETCHER
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def fetch_forensic_inputs(symbol, timeout_sec=15):
        """
        Pulls forensic inputs DIRECTLY from yfinance for ONE symbol.
        Returns a dict that can be .update()'d onto a stock dict before
        calling calculate_accounting_forensics(stock).

        Fetched keys:
            total_assets_cr, total_liab_cr, retained_earnings_cr, working_cap_cr
            ebit_cr, int_expense_cr, capex_cr
            inventory_days, receivable_days, payable_days
            total_debt_cr, cash_cr (fallback if .info didn't provide)
            operating_cf_cr, q_ebitda_cr, q_rev_cr, q_pat_cr

        Returns {} if the symbol can't be fetched.
        Never raises — swallows all Yahoo errors.
        """
        try:
            import yfinance as yf
        except ImportError:
            return {}

        _INR_CR = 1e7
        out = {}

        def _find_row(df_idx, keywords, excludes=()):
            """Find a DataFrame row label matching ALL keywords (case-insensitive)."""
            if df_idx is None:
                return None
            for r in df_idx:
                rs = str(r).lower().strip()
                if all(k.lower() in rs for k in keywords) and \
                   not any(e.lower() in rs for e in excludes):
                    return r
            return None

        def _val_cr(df, row_label):
            """Return the most recent column's value in ₹Cr. 0 if missing."""
            if df is None or row_label is None:
                return 0.0
            try:
                if hasattr(df, 'empty') and df.empty:
                    return 0.0
                cols = list(df.columns)
                if not cols:
                    return 0.0
                v = df.loc[row_label].iloc[0]
                if v is None or str(v) == "nan":
                    return 0.0
                return round(float(v) / _INR_CR, 2)
            except Exception:
                return 0.0

        try:
            # Try .NS first, fall back to .BO if nothing
            tk = None
            bs = cf = inc = None
            info = {}
            for suffix in (".NS", ".BO"):
                try:
                    tk = yf.Ticker(symbol + suffix)
                    bs = getattr(tk, "balance_sheet", None)
                    cf = getattr(tk, "cashflow", None)
                    inc = getattr(tk, "income_stmt", None)
                    if inc is None or (hasattr(inc, 'empty') and inc.empty):
                        inc = getattr(tk, "financials", None)
                    info = tk.info if hasattr(tk, 'info') else {}
                    # If we got ANY data, stop trying suffixes
                    if (bs is not None and hasattr(bs, 'empty') and not bs.empty) or \
                       (info and info.get('marketCap')):
                        break
                except Exception:
                    continue

            # ── BALANCE SHEET ────────────────────────────────────────────
            if bs is not None and hasattr(bs, 'empty') and not bs.empty:
                # Total Assets (many variants)
                ta_row = _find_row(bs.index, ["total", "assets"],
                                   excludes=["current", "non", "intangible"]) or \
                         _find_row(bs.index, ["totalassets"])
                ta = _val_cr(bs, ta_row)
                if ta > 0: out["total_assets_cr"] = ta

                # Total Liabilities
                tl_row = _find_row(bs.index, ["total", "liab"],
                                   excludes=["current", "non current"]) or \
                         _find_row(bs.index, ["totalliab"])
                tl = _val_cr(bs, tl_row)
                if tl > 0: out["total_liab_cr"] = tl

                # Retained Earnings
                re_row = _find_row(bs.index, ["retained", "earning"]) or \
                         _find_row(bs.index, ["retainedearning"])
                re_v = _val_cr(bs, re_row)
                if re_v != 0: out["retained_earnings_cr"] = re_v

                # Working Capital = Current Assets - Current Liabilities
                ca_row = _find_row(bs.index, ["current", "assets"],
                                   excludes=["non current", "noncurrent", "other"])
                cl_row = _find_row(bs.index, ["current", "liab"],
                                   excludes=["non current", "noncurrent",
                                             "deferred", "other"])
                ca = _val_cr(bs, ca_row)
                cl = _val_cr(bs, cl_row)
                if ca > 0 and cl > 0:
                    out["working_cap_cr"] = round(ca - cl, 2)
                    out["curr_assets_cr"] = ca
                    out["curr_liab_cr"]   = cl

                # Total Debt (fallback)
                td_row = _find_row(bs.index, ["total", "debt"]) or \
                         _find_row(bs.index, ["longterm", "debt"]) or \
                         _find_row(bs.index, ["totaldebt"])
                td = _val_cr(bs, td_row)
                if td > 0: out["total_debt_cr"] = td

                # Cash (fallback)
                cash_row = _find_row(bs.index, ["cash", "equivalent"]) or \
                           _find_row(bs.index, ["cashandcashequivalents"]) or \
                           _find_row(bs.index, ["cash"], excludes=["flow", "operating"])
                cash = _val_cr(bs, cash_row)
                if cash > 0: out["cash_cr"] = cash

                # Day-count ratios (need revenue)
                inv_row = _find_row(bs.index, ["inventor"], excludes=["non"])
                rec_row = _find_row(bs.index, ["receivable"], excludes=["non"])
                pay_row = _find_row(bs.index, ["payable"], excludes=["non"]) or \
                          _find_row(bs.index, ["accounts", "payable"])
                inv_cr = _val_cr(bs, inv_row)
                rec_cr = _val_cr(bs, rec_row)
                pay_cr = _val_cr(bs, pay_row)

                rev_raw = float(info.get("totalRevenue", 0) or 0)
                rev_cr = rev_raw / _INR_CR if rev_raw > 0 else 0
                if rev_cr > 0:
                    if inv_cr > 0:
                        out["inventory_days"]  = round(inv_cr / rev_cr * 365, 1)
                    if rec_cr > 0:
                        out["receivable_days"] = round(rec_cr / rev_cr * 365, 1)
                    if pay_cr > 0:
                        out["payable_days"]    = round(pay_cr / rev_cr * 365, 1)

            # ── CASH FLOW STATEMENT ──────────────────────────────────────
            if cf is not None and hasattr(cf, 'empty') and not cf.empty:
                capex_row = _find_row(cf.index, ["capital", "expenditure"]) or \
                            _find_row(cf.index, ["capitalexpenditure"]) or \
                            _find_row(cf.index, ["purchase", "ppe"]) or \
                            _find_row(cf.index, ["investmentinppe"])
                capex = abs(_val_cr(cf, capex_row))
                if capex > 0: out["capex_cr"] = capex

                # Operating CF fallback
                ocf_row = _find_row(cf.index, ["operating", "cash"]) or \
                          _find_row(cf.index, ["cashflowfromcontinuingoperatingactivities"]) or \
                          _find_row(cf.index, ["operatingcashflow"])
                ocf = _val_cr(cf, ocf_row)
                if ocf != 0: out["operating_cf_cr"] = ocf

            # ── INCOME STATEMENT ─────────────────────────────────────────
            if inc is not None and hasattr(inc, 'empty') and not inc.empty:
                # EBIT
                ebit_row = _find_row(inc.index, ["ebit"], excludes=["ebitda"]) or \
                           _find_row(inc.index, ["operating", "income"]) or \
                           _find_row(inc.index, ["operatingincome"])
                ebit = _val_cr(inc, ebit_row)
                if ebit != 0: out["ebit_cr"] = ebit

                # Interest Expense
                int_row = _find_row(inc.index, ["interest", "expense"]) or \
                          _find_row(inc.index, ["interestexpense"]) or \
                          _find_row(inc.index, ["interest", "paid"])
                intx = abs(_val_cr(inc, int_row))
                if intx > 0: out["int_expense_cr"] = intx

                # Revenue, EBITDA, Net Income in ₹Cr (quarterly latest proxy)
                rev_row = _find_row(inc.index, ["total", "revenue"]) or \
                          _find_row(inc.index, ["revenue"])
                rev = _val_cr(inc, rev_row)
                if rev > 0: out["q_rev_cr"] = rev

                ebitda_row = _find_row(inc.index, ["ebitda"],
                                       excludes=["normalized", "adjusted"]) or \
                             _find_row(inc.index, ["ebitda"])
                ebitda = _val_cr(inc, ebitda_row)
                if ebitda > 0: out["q_ebitda_cr"] = ebitda

                ni_row = _find_row(inc.index, ["net", "income"],
                                   excludes=["non controlling", "minority"]) or \
                         _find_row(inc.index, ["netincome"])
                ni = _val_cr(inc, ni_row)
                if ni != 0: out["q_pat_cr"] = ni

            return out

        except Exception:
            return out   # return whatever we got, never raise

    # ──────────────────────────────────────────────────────────────────────
    # CORE CALCULATIONS (v10.3, unchanged)
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def calculate_altman_z(data):
        """Altman Z-Score (5-variable). 0.0 = insufficient data.

        v12.5: capped at 10 — Z > 7 already signals exceptional safety, and
        values 14-26 observed in production (ALIVUS 14.69, GOPAL 17.27,
        CPEDU 26.70) are typically unit-mismatch artefacts in the X4
        component (mcap / total_liabilities) where one figure is in raw
        rupees and the other in Cr. Capping protects downstream scoring
        without losing the "very safe" signal at the high end.
        """
        ta = _num(data, 'total_assets', 'total_assets_cr')
        tl = _num(data, 'total_liabilities', 'total_liab_cr', 'total_liabilities_cr')
        if ta <= 0 or tl <= 0:
            return 0.0
        try:
            x1 = _num(data, 'working_cap', 'working_capital', 'working_cap_cr') / ta
            x2 = _num(data, 'retained_earnings', 'retained_earnings_cr') / ta
            x3 = _num(data, 'ebit', 'ebit_cr') / ta
            x4 = _num(data, 'mcap', 'mcap_cr') / tl
            x5 = _num(data, 'sales', 'revenue', 'q_rev_cr', 'total_revenue') / ta
            z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
            # v12.5: clamp to [0, 10] — preserves negative-Z distress
            # signals (returned 0 when ta or tl <= 0 above) and caps the
            # implausible 15+ outliers from unit-mismatch artefacts.
            if z > 10:
                z = 10
            return round(z, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    @staticmethod
    def calculate_beneish_m(data):
        """Beneish M-Score via accrual-quality proxy. 0.0 = insufficient data."""
        ta = _num(data, 'total_assets', 'total_assets_cr')
        if ta <= 0:
            return 0.0
        try:
            ni = _num(data, 'ni_from_ops', 'net_income', 'q_pat_cr', 'net_profit')
            cfo = _num(data, 'cfo', 'operating_cf_cr', 'operating_cf')
            if ni == 0 and cfo == 0:
                return 0.0
            tata = (ni - cfo) / ta
            if tata < 0:    return -2.50
            if tata < 0.05: return -2.22
            return -1.50
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    @staticmethod
    def beneish_status(m_score):
        try:
            m = float(m_score)
        except (ValueError, TypeError):
            return "DATA_INSUFFICIENT"
        if m == 0:    return "DATA_INSUFFICIENT"
        if m > -2.22: return "MANIPULATION_RISK"
        return "CLEAN"

    @staticmethod
    def calculate_cash_cycle(inventory_days, receivable_days, payable_days):
        return inventory_days + receivable_days - payable_days

    @staticmethod
    def detect_earnings_manipulation(rev_growth, rec_growth):
        if rev_growth > 0 and rec_growth > (rev_growth * 1.5):
            return True
        return False

    @staticmethod
    def get_ownership_signal(curr_fii, prev_fii, curr_pledge, prev_pledge):
        return {
            "fii_increasing":   curr_fii > prev_fii,
            "pledge_improving": curr_pledge < prev_pledge,
        }

    @staticmethod
    def check_wealth_creation(roce, cost_of_capital=11.5):
        return roce > cost_of_capital

    # ──────────────────────────────────────────────────────────────────────
    # MAIN CONSOLIDATED CALCULATOR
    # ──────────────────────────────────────────────────────────────────────
    @staticmethod
    def calculate_accounting_forensics(row):
        """v10.4 consolidated forensic & solvency calculator."""
        results = {}

        # 1. CASH CONVERSION CYCLE
        # v12.5: skip for finance-sector stocks. CCC = Inventory + Receivable
        # − Payable days is meaningless for Banks / NBFCs / HFCs / Insurance:
        # they don't carry inventory, and their "receivables" are loans
        # measured by a different convention. Production audit showed
        # TATACAP at 7,739 days, FUSION at 3,216 days (microfinance),
        # LICHSGFIN at −267 days — all finance-sector outliers.
        _sector_raw = str(row.get('sector', '') or '').lower()
        _is_finance = any(kw in _sector_raw for kw in (
            'financial', 'finance', 'bank', 'nbfc', 'insurance', 'housing finance'
        ))
        if _is_finance:
            results['ccc']      = "—"
            results['ccc_days'] = "—"
        else:
            inv = _num(row, 'inventory_days')
            rec = _num(row, 'receivable_days')
            pay = _num(row, 'payable_days')
            if inv > 0 or rec > 0 or pay > 0:
                _ccc = inv + rec - pay
                results['ccc']      = round(_ccc, 1)
                results['ccc_days'] = round(_ccc, 1)
            else:
                results['ccc']      = "—"
                results['ccc_days'] = "—"

        # 2. SOLVENCY & COVERAGE
        total_debt = _num(row, 'total_debt_cr', 'total_debt')
        cash       = _num(row, 'cash_cr', 'cash', 'total_cash')

        # v10.6 FIX (Bug #1): ND/EBITDA must use ANNUAL EBITDA, not quarterly.
        # Source priority:
        #   1. 'ebitda' key — TTM annual from yfinance .info (correct annual scale)
        #   2. 'ebitda_cr' — annual figure if explicitly named
        #   3. 'q_ebitda_cr' × 4 — quarterly DB value annualized as fallback
        # Without ×4, ND/EBITDA was inflating by ~4× (showing 33 instead of 8).
        ebitda_annual = _num(row, 'ebitda', 'ebitda_cr')
        if ebitda_annual <= 0:
            _q_ebitda = _num(row, 'q_ebitda_cr')
            if _q_ebitda > 0:
                ebitda_annual = _q_ebitda * 4   # annualize from quarterly
        if ebitda_annual > 0:
            results['nd_ebitda'] = round((total_debt - cash) / ebitda_annual, 2)
        else:
            results['nd_ebitda'] = "—"

        ebit    = _num(row, 'ebit_cr', 'ebit')
        int_exp = _num(row, 'int_expense_cr', 'int_expense', 'interest_expense')
        if int_exp > 0 and ebit != 0:
            results['int_coverage'] = round(ebit / int_exp, 2)
        else:
            results['int_coverage'] = "—"

        # 3. CAPITAL EFFICIENCY
        # v10.6 FIX: use ANNUAL revenue for Capex/Rev ratio (capex is annual, so
        # denominator must match). Same annualization fallback as ND/EBITDA.
        capex = abs(_num(row, 'capex_cr', 'capex'))
        rev_annual = _num(row, 'revenue', 'total_revenue', 'revenue_cr')
        if rev_annual <= 0:
            _q_rev = _num(row, 'q_rev_cr')
            if _q_rev > 0:
                rev_annual = _q_rev * 4
        if capex > 0 and rev_annual > 0:
            results['capex_rev'] = round((capex / rev_annual) * 100, 2)
        else:
            results['capex_rev'] = "—"

        # 4. EARNINGS QUALITY — v10.8: CATEGORICAL HIGH / LOW / MODERATE / —
        # The tooltip already says "HIGH = cash-backed earnings", so output
        # should match. Raw CFO/PAT ratio was misleading (negative PAT gave
        # nonsensical -246 values; users couldn't interpret a raw ratio
        # without context). Accounting convention:
        #   CFO/PAT ≥ 0.8  → HIGH     (cash flow matches profits)
        #   CFO/PAT < 0.5  → LOW      (accounting concern — profits aren't cash)
        #   0.5 ≤ x < 0.8  → MODERATE
        #   PAT ≤ 0        → "—"     (ratio undefined with zero/negative PAT)
        cfo = _num(row, 'operating_cf_cr', 'cfo', 'operating_cf')
        pat = _num(row, 'q_pat_cr', 'net_profit', 'net_income')
        if pat > 0 and cfo != 0:
            _eq_ratio = cfo / pat
            if   _eq_ratio >= 0.8: results['earnings_quality'] = "HIGH"
            elif _eq_ratio <  0.5: results['earnings_quality'] = "LOW"
            else:                  results['earnings_quality'] = "MODERATE"
        else:
            results['earnings_quality'] = "—"

        # 5. FORENSIC SCORES
        _alt = ForensicsEngine.calculate_altman_z(row)
        _ben = ForensicsEngine.calculate_beneish_m(row)
        results['altman_z']  = _alt if _alt != 0.0 else "—"
        results['beneish_m'] = _ben if _ben != 0.0 else "—"

        # 6. RISK & QUALITY FLAGS
        results['earnings_manipulation_risk'] = (
            _num(row, 'rec_growth') > (_num(row, 'rev_growth') * 1.5)
        )
        results['is_wealth_creator'] = _num(row, 'roce') > 11.5

        # 7. BACKWARD-COMPAT KEYS (no total_debt — master_funnel owns that key)
        results['contingent_liabilities'] = _num(row, 'contingent_liabilities')
        results['networth']               = _num(row, 'networth', default=1)
        results['cash_equivalents']       = cash + _num(row, 'bank_balance')

        # Expose 'cash' for Excel's "Cash (₹Cr)" column when balance-sheet
        # cash is available AND row doesn't already carry a valid value.
        _existing = row.get('cash')
        _existing_num = _num(row, 'cash')
        if _existing is None or _existing == "" or str(_existing) in ("—", "--", "N/A") \
           or _existing_num == 0:
            if cash > 0:
                results['cash'] = round(cash, 2)

        # 8. SHAREHOLDING DIRECTION (passthrough)
        results['promoter_qoq']     = row.get('promoter_qoq', 0)
        # v10.6 FIX (Bug #2): default to '—' instead of 'STABLE' when no pledge
        # history exists. 'STABLE' is misleading because it implies the value was
        # measured and didn't change — but here it just means we have no comparison.
        results['pledge_direction'] = row.get('pledge_dir', row.get('pledge_direction', "—"))
        results['fii_qoq']          = row.get('fii_qoq', 0)
        results['dii_qoq']          = row.get('dii_qoq', 0)

        return results