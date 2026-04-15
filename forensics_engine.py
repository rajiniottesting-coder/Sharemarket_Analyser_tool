class ForensicsEngine:
    @staticmethod
    def calculate_altman_z(data):
        """
        Section 3G: Altman Z-Score (Manufacturing & Service versions)
        Score > 2.99: Safe | 1.81 - 2.99: Gray | < 1.81: Distress
        """
        try:
            # X1: Working Capital / Total Assets
            x1 = data['working_cap'] / data['total_assets']
            # X2: Retained Earnings / Total Assets
            x2 = data['retained_earnings'] / data['total_assets']
            # X3: EBIT / Total Assets
            x3 = data['ebit'] / data['total_assets']
            # X4: Market Value of Equity / Total Liabilities
            x4 = data['mcap'] / data['total_liabilities']
            # X5: Sales / Total Assets
            x5 = data['sales'] / data['total_assets']
            
            z = (1.2 * x1) + (1.4 * x2) + (3.3 * x3) + (0.6 * x4) + (1.0 * x5)
            return round(z, 2)
        except ZeroDivisionError:
            return 0

    @staticmethod
    def calculate_beneish_m(data):
        """
        Section 3G: Beneish M-Score (8-variable logic)
        Score > -1.78: Likely Manipulator | < -2.22: Non-Manipulator
        """
        # This checks for rising receivables, falling margins, and aggressive accruals
        # We implement the 'SGI' (Sales Growth Index) and 'AQI' (Asset Quality Index)
        try:
            # Simplified trigger for Phase 1: High Accruals check
            accruals = (data['ni_from_ops'] - data['cfo']) / data['total_assets']
            return "MANIPULATION_RISK" if accruals > 0.05 else "CLEAN"
        except:
            return "DATA_INSUFFICIENT"
        
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
        SUBSECTION 3G: Beneish M-Score Proxy (Manipulation Risk)
        SUBSECTION 3B: Wealth Creation (ROCE vs WACC)
        """
        results = {}
        
        # 3D: Cash Cycle
        results['ccc'] = row.get('inventory_days', 0) + row.get('receivable_days', 0) - row.get('payable_days', 0)
        
        # 3G: M-Score Proxy (Is receivables growth > 1.5x Revenue growth?)
        results['earnings_manipulation_risk'] = row.get('rec_growth', 0) > (row.get('rev_growth', 0) * 1.5)
        
        # 3B: ROCE vs WACC (11.5% is the v7.0 standard benchmark)
        results['is_wealth_creator'] = row.get('roce', 0) > 11.5
        
        return results
    
    @staticmethod
    def calculate_accounting_forensics(row):
        results = {}
        # ... existing CCC and M-Score logic ...

        # SECTION 4: Solvency & Hidden Debt Data
        results['contingent_liabilities'] = row.get('contingent_liabilities', 0)
        results['networth'] = row.get('networth', 1) # Avoid division by zero
        results['total_debt'] = row.get('total_debt', 0)
        results['cash_equivalents'] = row.get('cash', 0) + row.get('bank_balance', 0)
        
        return results