class ForensicsEngine:
    @staticmethod
    def calculate_altman_z(data):
        """
        Section 3G: Altman Z-Score (Manufacturing & Service versions).
        Score > 2.99: Safe | 1.81 - 2.99: Gray | < 1.81: Distress

        Session 15: converted from dict-subscript to .get() so missing inputs
        return 0.0 instead of raising KeyError. This makes the function safe
        to call from the pipeline even when free data is missing (the inputs
        working_cap / retained_earnings / ebit / total_assets / total_liabilities
        typically come from paid balance-sheet feeds).
        """
        ta  = float(data.get('total_assets', 0) or 0)
        tl  = float(data.get('total_liabilities', 0) or 0)
        if ta <= 0 or tl <= 0:
            return 0.0   # insufficient data — caller should treat as "unknown"
        try:
            x1 = float(data.get('working_cap', 0) or 0)     / ta
            x2 = float(data.get('retained_earnings', 0) or 0) / ta
            x3 = float(data.get('ebit', 0) or 0)            / ta
            x4 = float(data.get('mcap', 0) or 0)            / tl
            x5 = float(data.get('sales', 0) or 0)           / ta
            z  = 1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5
            return round(z, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    @staticmethod
    def calculate_beneish_m(data):
        """
        Section 3G: Beneish M-Score (approximated via accrual-quality proxy).
        Score > -1.78: Likely Manipulator | < -2.22: Likely Non-Manipulator

        Session 15: now returns a NUMERIC M-score (float) instead of a
        "MANIPULATION_RISK" / "CLEAN" string, so it can populate the numeric
        Excel column `beneish_m` cleanly. Insufficient-data returns 0.0.

        The true 8-variable Beneish formula needs 2 years of balance sheet
        data (DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI, TATA) which free feeds
        don't supply; this approximation uses just the accrual-quality piece
        (TATA) and maps it to a plausible M-score range:
            TATA < 0     → M ≈ -2.50  (very clean)
            TATA  0-0.05 → M ≈ -2.22  (clean borderline)
            TATA > 0.05  → M ≈ -1.50  (manipulation risk)
        """
        ta  = float(data.get('total_assets', 0) or 0)
        if ta <= 0:
            return 0.0
        try:
            ni  = float(data.get('ni_from_ops', 0) or 0)
            cfo = float(data.get('cfo', 0) or 0)
            tata = (ni - cfo) / ta   # Total Accruals / Total Assets
            if tata < 0:      return -2.50
            if tata < 0.05:   return -2.22
            return -1.50
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    @staticmethod
    def beneish_status(m_score):
        """
        Session 15: helper that converts the numeric M-score back to the
        human-readable tag previously returned by calculate_beneish_m. Kept
        separate so callers that want the status string don't have to
        re-implement the threshold logic.
        """
        if m_score == 0:     return "DATA_INSUFFICIENT"
        if m_score > -2.22:  return "MANIPULATION_RISK"
        return "CLEAN"

    # --- SECTION 3D: FINANCIAL HEALTH ---
    @staticmethod
    def calculate_cash_cycle(inventory_days, receivable_days, payable_days):
        """
        Subsection 3D: Cash Conversion Cycle (DIO + DSO - DPO).
        Measures efficiency of capital usage.
        """
        return inventory_days + receivable_days - payable_days

    # --- SECTION 3G: QUALITY SCORES (Add if missing) ---
    @staticmethod
    def detect_earnings_manipulation(rev_growth, rec_growth):
        """
        Subsection 3G: Beneish M-Score Proxy.
        Flags risk if receivables grow 1.5x faster than revenue.
        """
        if rev_growth > 0 and rec_growth > (rev_growth * 1.5):
            return True
        return False

    # --- SECTION 3F & 3K: OWNERSHIP INTELLIGENCE ---
    @staticmethod
    def get_ownership_signal(curr_fii, prev_fii, curr_pledge, prev_pledge):
        """
        Subsections 3F (FII Trend) and 3K (Pledge Direction).
        Logic: Direction matters more than absolute levels.
        """
        signals = {
            "fii_increasing": curr_fii > prev_fii,
            "pledge_improving": curr_pledge < prev_pledge  # Falling pledge is BULLISH
        }
        return signals

    # --- SECTION 3B: PROFITABILITY ---
    @staticmethod
    def check_wealth_creation(roce, cost_of_capital=11.5):
        """
        Subsection 3B & 3E: ROIC/ROCE vs WACC spread.
        """
        return roce > cost_of_capital
    
    @staticmethod
    def calculate_accounting_forensics(row):
        """
        SUBSECTION 3D: Cash Conversion Cycle (DIO + DSO - DPO)
        SUBSECTION 3G: Altman Z + Beneish M + M-Score Proxy
        SUBSECTION 3B: Wealth Creation (ROCE vs WACC)
        """
        results = {}

        # 3D: Cash Cycle
        # Session 14: also expose as 'ccc_days' (the key used by excel_generator
        # FULL_COLS). Both are written so existing readers of 'ccc' keep working.
        _ccc = row.get('inventory_days', 0) + row.get('receivable_days', 0) - row.get('payable_days', 0)
        results['ccc']      = _ccc
        results['ccc_days'] = _ccc

        # 3G: Altman Z + Beneish M (Session 15: wire numeric forensics scores)
        # Both are safe — return 0.0 when paid BS inputs are missing, which is
        # the norm for yfinance free data. Caller should treat 0.0 as "unknown"
        # rather than "safe". This populates Excel cols 'altman_z' and 'beneish_m'.
        results['altman_z']   = ForensicsEngine.calculate_altman_z(row)
        results['beneish_m']  = ForensicsEngine.calculate_beneish_m(row)

        # 3G: M-Score Proxy (Is receivables growth > 1.5x Revenue growth?)
        results['earnings_manipulation_risk'] = row.get('rec_growth', 0) > (row.get('rev_growth', 0) * 1.5)
        
        # 3B: ROCE vs WACC (11.5% is the v7.0 standard benchmark)
        results['is_wealth_creator'] = row.get('roce', 0) > 11.5

        # SECTION 4: Solvency & Hidden Debt Data
        results['contingent_liabilities'] = row.get('contingent_liabilities', 0)
        results['networth'] = row.get('networth', 1) # Avoid division by zero
        results['total_debt'] = row.get('total_debt', 0)
        results['cash_equivalents'] = row.get('cash', 0) + row.get('bank_balance', 0)

        return results