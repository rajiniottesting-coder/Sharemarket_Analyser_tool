"""
forensics_engine.py — v10.3

Consolidated forensic & solvency engine.

Changes vs v10.2:
- Bug D fix: removed 'total_debt' from return dict (master_funnel has its own
  3-tier fallback for this key that we must not overwrite). Legacy callers
  that want it can read 'total_debt_cr' directly from the input row.
- Bug B fix: results['cash'] aliased to cash_cr value so Excel's "Cash (₹Cr)"
  column (which reads stock["cash"]) populates whenever balance sheet data
  is available — not only when yfinance .info had totalCash.
- 'cash_equivalents' preserved for any legacy callers.
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
    @staticmethod
    def calculate_altman_z(data):
        """Altman Z-Score (5-variable manufacturing model). 0.0 = insufficient data."""
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

    @staticmethod
    def calculate_accounting_forensics(row):
        """
        v10.3 consolidated forensic & solvency calculator.

        Does NOT return 'total_debt' (master_funnel manages that with its own
        3-tier fallback). Returns 'cash' when balance-sheet cash is present
        so Excel's Cash column populates.
        """
        results = {}

        # 1. CASH CONVERSION CYCLE
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
        ebitda     = _num(row, 'q_ebitda_cr', 'ebitda')
        if ebitda > 0:
            results['nd_ebitda'] = round((total_debt - cash) / ebitda, 2)
        else:
            results['nd_ebitda'] = "—"

        ebit    = _num(row, 'ebit_cr', 'ebit')
        int_exp = _num(row, 'int_expense_cr', 'int_expense', 'interest_expense')
        if int_exp > 0 and ebit != 0:
            results['int_coverage'] = round(ebit / int_exp, 2)
        else:
            results['int_coverage'] = "—"

        # 3. CAPITAL EFFICIENCY
        capex = abs(_num(row, 'capex_cr', 'capex'))
        rev   = _num(row, 'q_rev_cr', 'revenue', 'total_revenue')
        if capex > 0 and rev > 0:
            results['capex_rev'] = round((capex / rev) * 100, 2)
        else:
            results['capex_rev'] = "—"

        # 4. EARNINGS QUALITY (CFO / PAT)
        cfo = _num(row, 'operating_cf_cr', 'cfo', 'operating_cf')
        pat = _num(row, 'q_pat_cr', 'net_profit', 'net_income')
        if pat != 0 and cfo != 0:
            results['earnings_quality'] = round(cfo / pat, 2)
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

        # 7. BACKWARD-COMPAT KEYS (read-only passthroughs — no overwrites)
        results['contingent_liabilities'] = _num(row, 'contingent_liabilities')
        results['networth']               = _num(row, 'networth', default=1)
        results['cash_equivalents']       = cash + _num(row, 'bank_balance')

        # Bug B fix: expose 'cash' so Excel's "Cash (₹Cr)" column populates.
        # Only set if balance sheet had a value AND row doesn't already carry
        # a non-numeric placeholder — we don't want to override master_funnel's
        # yfinance-sourced stock["cash"] if it was already set. But if that
        # upstream value is "—" or 0, we fill from balance sheet cash.
        _existing = row.get('cash')
        _existing_num = _num(row, 'cash')
        if _existing is None or _existing == "" or str(_existing) in ("—", "--", "N/A") \
           or _existing_num == 0:
            if cash > 0:
                results['cash'] = round(cash, 2)

        # 8. SHAREHOLDING DIRECTION (passthrough)
        results['promoter_qoq']     = row.get('promoter_qoq', 0)
        results['pledge_direction'] = row.get('pledge_dir', row.get('pledge_direction', "STABLE"))
        results['fii_qoq']          = row.get('fii_qoq', 0)
        results['dii_qoq']          = row.get('dii_qoq', 0)

        return results