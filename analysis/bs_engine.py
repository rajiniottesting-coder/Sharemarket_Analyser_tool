# bs_engine.py
# ────────────────────────────────────────────────────────────────────────
# SECTION 4 — BALANCE SHEET ANALYSIS (12 MONTHS)
# ────────────────────────────────────────────────────────────────────────

class BalanceSheetEngine:
    @staticmethod
    def analyze_bs_health(current_bs, prev_bs_4q):
        """
        Performs 12-month Balance Sheet audit (Section 4).
        Preserves existing DIO/DSO, CWIP, Goodwill, and Debt/ROE logic.
        Adds: Contingent Liability % of Networth.
        """
        flags = []
        red_flags = []
        
        # --- ASSETS SIDE ANALYSIS (Preserved) ---
        if current_bs.get('cwip', 0) > (current_bs.get('net_block', 1) * 0.3):
            flags.append("LARGE CWIP")
            
        if current_bs.get('dio', 0) > prev_bs_4q.get('dio', 0):
            flags.append("RISING DIO")
        if current_bs.get('dso', 0) > prev_bs_4q.get('dso', 0):
            flags.append("RISING DSO")

        # --- LIABILITIES SIDE ANALYSIS (Preserved + New Contingent Logic) ---
        debt = current_bs.get('total_debt', 0)
        cash = current_bs.get('cash_equivalents', 0)
        
        # NEW: Contingent Liability Logic (Hidden Debt Section 4)
        cont_liab = current_bs.get('contingent_liabilities', 0)
        nw = current_bs.get('networth', 1)
        cont_ratio = (cont_liab / nw) * 100

        if cont_ratio > 25:
            flags.append(f"CONTINGENT RISK: {round(cont_ratio)}% of NW")
        if cont_ratio > 50:
            red_flags.append("CRITICAL SOLVENCY RISK (Contingent > 50% NW)")

        # Short-term debt dependency (Preserved)
        if current_bs.get('st_borrowings', 0) > (current_bs.get('lt_borrowings', 0) * 1.5):
            flags.append("WC DEPENDENCY")

        # --- RED FLAGS (Preserved Section 4) ---
        # v10.16: ROE can be '—' (v10.15 FIX #1 when yfinance has no value AND
        # no derivable proxy). Coerce defensively before comparison.
        def _roe_num(v):
            if v in (None, "", "—", "--", "N/A"): return 0.0
            try: return float(v)
            except (ValueError, TypeError): return 0.0
        _curr_roe = _roe_num(current_bs.get('roe', 0))
        _prev_roe = _roe_num(prev_bs_4q.get('roe', 0))
        if debt > prev_bs_4q.get('total_debt', 0) and _curr_roe < _prev_roe:
            red_flags.append("DEBT UP / ROE DOWN")
            
        if current_bs.get('goodwill', 0) > (current_bs.get('networth', 1) * 0.5):
            red_flags.append("HIGH GOODWILL RISK")

        # --- BS STATUS LOGIC (Preserved + Escalated) ---
        status = "HEALTHY"
        # Trigger ALERT if red flags exist or Debt up + CFO/PAT divergence
        if len(red_flags) > 0 or (debt > prev_bs_4q.get('total_debt', 0) and current_bs.get('cfo_pat_2q_low')):
            status = "ALERT"
        elif len(flags) > 0:
            status = "WATCH"

        # --- OUTPUT LINE (Section 4 Compliant) ---
        # Includes Contingent Risk in the layman summary
        coverage = round(cash/debt, 2) if debt > 0 else 'N/A'
        cfo_trend = '↓↓' if status == 'ALERT' else '↑↑'
        
        output = (f"Cash ₹{cash}Cr vs Debt ₹{debt}Cr | "
                  f"Contingent Risk: {round(cont_ratio)}% | "
                  f"Cover {coverage}x | CFO: {cfo_trend}")

        return {
            "status": status,
            "flags": flags + red_flags,
            "output_line": output
        }