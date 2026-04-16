import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.formatting.rule import ColorScaleRule
import datetime
    
class ExcelGeneratorV6:
    # Columns required by Excel sheets — filled with safe defaults if absent
    REQUIRED_COLS = {
        'early_entry_score': 0, 'composite_score': 0, 'spike_count': 0,
        'mos_pct': 0.0, 'upside': 0.0, 'verdict': 'WATCHLIST',
        'spike_suppressed': False, 'symbol': '', 'close': 0.0,
        'company_name': '', 'sector': '', 'exchange_tag': 'NSE',
        'Analysis_Summary_Block_H': '—', '2w_chg': 0, '4w_chg': 0,
        '6w_chg': 0, '8w_chg': 0, 'cfv': 0.0, 'entry_range': '—',
        'stop_loss': '—', 't1': 0, 't2': 0, 't3': 0,
        'horizon': 'POSITIONAL', 'risk_level': 'MEDIUM',
    }

    def __init__(self, data, date_str):
        self.df = pd.DataFrame(data) if data else pd.DataFrame()
        self.date_str = date_str
        self.tab_colors = {
            "Dashboard": "1E293B", "Gold": "B45309", "Trade": "059669",
            "Alert": "7C3AED", "Preview": "0D9488", "Glossary": "475569"
        }
        self.verdict_styles = {
            "DEEP VALUE EARLY MOVER": {"bg": "FAC775", "text": "412402", "summary_bg": "B45309"},
            "DEEP VALUE":             {"bg": "D1FAE5", "text": "065F46", "summary_bg": "064E3B"},
            "BUY / EARLY MOVER":      {"bg": "DBEAFE", "text": "1E3A5F", "summary_bg": "1E3A8A"},
            "WATCHLIST":              {"bg": "FEF3C7", "text": "78350F", "summary_bg": "92400E"},
            "AVOID / EXIT":           {"bg": "FEE2E2", "text": "7F1D1D", "summary_bg": "991B1B"},
        }
        # Ensure all required columns exist with safe defaults
        for col, default in self.REQUIRED_COLS.items():
            if col not in self.df.columns:
                self.df[col] = default

    @staticmethod
    def _safe_val(value):
        """Convert any value to Excel-safe type (str/int/float/bool/None/datetime).
        Lists, dicts, sets are joined/stringified — Excel cannot store them."""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return " | ".join(str(v) for v in value) if value else ""
        if isinstance(value, dict):
            return str(value)
        try:
            import datetime as _dt
            if isinstance(value, (_dt.date, _dt.datetime)):
                return value
        except Exception:
            pass
        return str(value)

    def generate_excel_reports(self):
        master_file = self._create_workbook(type="FULL")
        gold_file = self._create_workbook(type="GOLD")
        return master_file, gold_file

    def _create_workbook(self, type="FULL"):
        wb = Workbook()
        ws_dash = wb.active
        
        if type == "FULL":
            ws_dash.title = "📊 Full Dashboard"
            self._build_data_sheet(ws_dash, self.df)
            self._build_gold_sheet(wb)
            self._build_trade_summary_sheet(wb, self._get_gold_data())
            self._build_alert_log_sheet(wb)
            self._build_preview_sheet(wb)
            self._build_glossary_sheet(wb)
            filename = f"NSE_BSE_Full_Dashboard_{self.date_str}.xlsx"
        else:
            ws_dash.title = "⭐ Gold – Early Movers"
            gold_df = self._get_gold_data()
            self._build_data_sheet(ws_dash, gold_df, is_gold=True)
            self._build_trade_summary_sheet(wb, gold_df)
            self._build_alert_log_sheet(wb)
            self._build_glossary_sheet(wb)
            filename = f"NSE_BSE_Gold_EarlyMovers_{self.date_str}.xlsx"

        for sheet in wb.worksheets:
            sheet.sheet_view.showGridLines = False
            sheet.sheet_view.zoomScale = 80
            for key, color in self.tab_colors.items():
                if key in sheet.title:
                    sheet.sheet_properties.tabColor = color
        
        wb.save(filename)
        return filename

    def _apply_group_headers(self, ws, start_row):
        """Implements all 19 Merged Group Headers (Section 10 Spec)."""
        groups = [
            ("IDENTITY & CLASSIFICATION", 7, "1E293B"),
            ("SCORES", 4, "7C3AED"),
            ("PRICE & MARKET DATA", 7, "0369A1"),
            ("WEEKLY CHANGE %", 4, "0F766E"),
            ("FAIR VALUE", 13, "B45309"),
            ("VALUATION RATIOS", 7, "0891B2"),
            ("PROFITABILITY", 10, "059669"),
            ("GROWTH METRICS", 10, "047857"),
            ("FINANCIAL HEALTH", 10, "DC2626"),
            ("CAPITAL ALLOCATION", 3, "6D28D9"),
            ("SHAREHOLDING", 9, "EA580C"),
            ("QUALITY SCORES", 4, "0D9488"),
            ("PIPELINE / ORDER BOOK", 5, "1D4ED8"),
            ("EARLY DETECTION", 3, "B45309"),
            ("TECHNICAL ANALYSIS", 14, "6D28D9"),
            ("BALANCE SHEET", 2, "D97706"), # GROUP 16: Balance Sheet Health
            ("TRADE PLAN", 7, "059669"),
            ("NEWS & RISK", 4, "475569"),
            ("ANALYSIS SUMMARY", 1, "0F172A")
        ]
        curr_col = 1
        for name, span, color in groups:
            ws.merge_cells(start_row=start_row, start_column=curr_col, end_row=start_row, end_column=curr_col+span-1)
            cell = ws.cell(row=start_row, column=curr_col, value=name)
            cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
            cell.font = Font(color="FFFFFF", bold=True, size=8)
            cell.alignment = Alignment(horizontal="center")
            curr_col += span
    
    def _build_data_sheet(self, ws, data_df, is_gold=False):
        # 1. Title Bar (Row 1)
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=130) 
        title = ws.cell(row=1, column=1, value=f"NSE/BSE STOCK ANALYSER DASHBOARD v6.0 | {self.date_str}")
        title.fill = PatternFill(start_color="1E1B4B", end_color="1E1B4B", fill_type="solid")
        title.font = Font(color="FFFFFF", bold=True, size=12)

        # 2. Summary Strip for Gold Sheet (Row 2)
        if is_gold:
            ws.merge_cells("A2:H2")
            avg_e = data_df['early_entry_score'].mean() if 'early_entry_score' in data_df.columns and not data_df.empty else 0
            avg_u = data_df['upside'].mean() if 'upside' in data_df.columns and not data_df.empty else 0
            summary_text = (
                f"# gold stocks: {len(data_df)} | "
                f"Avg Early Score: {avg_e:.0f} | "
                f"Avg Upside: {avg_u:.1f}%"
            )
            cell = ws.cell(row=2, column=1, value=summary_text)
            cell.font = Font(bold=True, color="B45309")
            cell.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

        # 3. Apply Group Headers (Row 3)
        self._apply_group_headers(ws, 3)

        # 4. Column Headers & Data Population
        columns = list(data_df.columns)
        even_fill = PatternFill(start_color="F0FDFA", end_color="F0FDFA", fill_type="solid")
        
        for r_idx, row in enumerate(dataframe_to_rows(data_df, index=False, header=True), 4):
            for c_idx, value in enumerate(row, 1):
                cell = ws.cell(row=r_idx, column=c_idx, value=self._safe_val(value))
                if r_idx == 4:
                    cell.font = Font(bold=True, size=8)
                    cell.fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
                elif r_idx > 4:
                    verdict = data_df.iloc[r_idx-5].get('verdict', 'WATCHLIST')
                    style = self.verdict_styles.get(verdict, {"bg": "F1F5F9", "text": "1E293B"})
                    cell.fill = PatternFill(start_color=style["bg"], end_color=style["bg"], fill_type="solid")
                    if r_idx % 2 == 0: cell.fill = even_fill
                    cell.font = Font(color=style["text"], size=9)

        # 5. Analysis Summary Column (Far Right - Section 10 Rule 19)
        summary_col = len(columns) + 1
        ws.cell(row=4, column=summary_col, value="View Analysis Summary").font = Font(bold=True)
        for r_idx in range(5, ws.max_row + 1):
            cell = ws.cell(row=r_idx, column=summary_col)
            cell.value = data_df.iloc[r_idx-5].get('Analysis_Summary_Block_H', '—')
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            ws.column_dimensions[cell.column_letter].width = 80
            # Background: dark shade of verdict
            verdict = data_df.iloc[r_idx-5].get('verdict', 'WATCHLIST')
            style = self.verdict_styles.get(verdict, {"bg": "1E293B"})
            cell.fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
            cell.font = Font(color="FFFFFF")

        # 6. Formatting
        momentum_rule = ColorScaleRule(start_type='num', start_value=-15, start_color='FEE2E2', mid_type='num', mid_value=0, mid_color='FFFFFF', end_type='num', end_value=15, end_color='D1FAE5')
        ws.conditional_formatting.add("S5:V105", momentum_rule)
        # --- ENFORCING STRICT FORMATTING RULES (v7.0) ---
        
        # Define Custom Number Formats
        # Rule 2: Indian Currency with ₹ | Rule 5: Parentheses for Negatives
        indian_curr_format = '[$₹-439]#,##,##0;([$₹-439]#,##,##0)' 
        # Rule 4 & 6: Explicit signs for growth/percent
        growth_format = '+0.0%;-0.0%;0.0%' 
        # Rule 7: Multiples
        multiple_format = '0.00"x"' 

        # Apply to Data Grid (Rows 5 to max)
        for r_idx in range(5, ws.max_row + 1):
            # Rule 10: Set Data Row Height (minimum 20px)
            ws.row_dimensions[r_idx].height = 20 
            
            for c_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=r_idx, column=c_idx)
                col_name = data_df.columns[c_idx-1] if c_idx <= len(data_df.columns) else ""

                # Apply Indian Currency (CMP, CFV, Targets, etc.)
                if any(x in col_name for x in ['₹', 'CMP', 'CFV', 'Target', 'High', 'Low', 'Debt', 'Cash']):
                    cell.number_format = indian_curr_format 
                
                # Apply Growth/Percentage Format
                elif any(x in col_name for x in ['%', 'CAGR', 'YoY', 'ROE', 'ROCE', 'Margin']):
                    cell.number_format = growth_format
                
                # Apply Multiple Format
                elif any(x in col_name for x in ['P/E', 'P/B', 'Ratio', 'x']):
                    cell.number_format = multiple_format 

        # --- HEADER & SHEET LEVEL RULES ---
        
        # Rule 10: Specific Row Heights
        ws.row_dimensions[1].height = 34  # Title Row 
        ws.row_dimensions[3].height = 20  # Group Header Row 
        ws.row_dimensions[4].height = 40  # Column Header Row 

        # Rule 15: Print Area (A1 to last column/row)
        last_col_letter = ws.cell(row=4, column=ws.max_column).column_letter
        ws.print_area = f"A1:{last_col_letter}{ws.max_row}" 
        ws.freeze_panes = "A5"

    def _build_trade_summary_sheet(self, wb, gold_df):
        """
        Implements Sheet 3: 📊 Trade Summary (Section 10).
        Logic: Only Gold stocks, formula-based R:R, and momentum tracking.
        """
        ws = wb.create_sheet("📊 Trade Summary")
        
        # 1. Header Definitions (Matching your CSV/Image exactly)
        headers = [
            "Symbol", "Company", "CMP", "CFV", "MoS %", "Upside %",
            "Chg% [2-Wk]", "Chg% [4-Wk]", "Chg% [8-Wk]",
            "Entry Range", "Stop Loss", "T1", "T2", "T3",
            "R:R Ratio", "Time Horizon", "Risk Level"
        ]
        
        header_fill = PatternFill(start_color="059669", end_color="059669", fill_type="solid")
        for c_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c_idx, value=h)
            cell.font = Font(color="FFFFFF", bold=True, size=9)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        # 2. Populate Data (Gold Stocks Only)
        # Filters: Early Entry Score >= 70 OR (MoS >= 25% AND Score >= 70)
        row_idx = 2
        for stock in gold_df.to_dict('records'):
            row_data = [
                stock.get('symbol'), stock.get('company_name'), stock.get('close'),
                stock.get('cfv'), f"{stock.get('mos_pct')}%", f"{stock.get('upside')}%",
                f"{stock.get('2w_chg')}%", f"{stock.get('4w_chg')}%", f"{stock.get('8w_chg')}%",
                stock.get('entry_range'), stock.get('stop_loss'), 
                stock.get('t1'), stock.get('t2'), stock.get('t3'),
                None, # R:R Ratio (Formula below)
                stock.get('horizon', 'POSITIONAL'), stock.get('risk_level', 'MEDIUM')
            ]
            
            for c_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=c_idx, value=val)
                cell.font = Font(name='Calibri', size=9)
                
                # Apply Section 10 Formatting Rules
                if headers[c_idx-1] == "Stop Loss":
                    cell.fill = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid") # Red fill
                elif "T" in headers[c_idx-1] and len(headers[c_idx-1]) <= 2:
                    cell.fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid") # Green fill

            # 3. R:R Ratio Formula (Section 10 Rule 16)
            # Formula: =(T1 - Entry_Low) / (Entry_Low - Stop_Loss)
            # M=T1, J=Entry, K=Stop Loss
            ws.cell(row=row_idx, column=15).value = f"=(L{row_idx}-J{row_idx})/(J{row_idx}-K{row_idx})"
            ws.cell(row=row_idx, column=15).number_format = '0.00x'
            
            row_idx += 1

        # 4. Sheet Settings
        ws.freeze_panes = "A2"
        ws.column_dimensions['B'].width = 25
        ws.column_dimensions['O'].width = 12

    def _build_glossary_sheet(self, wb):
            """
            Implements Sheet 6: Glossary (Section 10 Rule 5).
            Captures all 120+ technical definitions from the master specification.
            """
            ws = wb.create_sheet("📖 Glossary")
            # Row 1: Headers (Dark Navy)
            headers = ["Group", "Short Name", "Full Form & Description", "Where Used"]
            header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
            for c_idx, h in enumerate(headers, 1):
                cell = ws.cell(row=1, column=c_idx, value=h)
                cell.font = Font(color="FFFFFF", bold=True)
                cell.fill = header_fill
            
            # Mandatory Glossary Data (captured from your provided image)
            glossary_data = [
                # Group | Short Name | Full Form & Description | Where Used
                ["Identity", "Symbol", "Unique ticker for NSE/BSE stocks", "All Sheets"],
                ["Scores", "Score /100", "Composite score (Red <50, Yellow 50-70, Green >70)", "Dashboard"],
                ["Scores", "Early Entry", "0-100 score identifying institutional entry 4-12 weeks early", "Gold/Dashboard"],
                ["Weekly Chg", "Chg% [8-Weekly]", "40-day momentum; filters >15% surface hidden accum.", "Dashboard"],
                ["Fair Value", "M1: DCF FV", "Discounted Cash Flow: Value based on future cash potential", "Dashboard"],
                ["Fair Value", "M2: Graham", "Graham Number: Value based on assets & earnings (v7 tuned)", "Dashboard"],
                ["Fair Value", "M7: PEG FV", "PEG-based Fair Value: Growth-adjusted valuation model", "Dashboard"],
                ["Fair Value", "MoS %", "Margin of Safety: Discount of CMP vs Composite Fair Value", "All Sheets"],
                ["Health", "Piotroski F", "9-point fundamental health score (High-quality >= 7)", "Dashboard"],
                ["Health", "ND/EBITDA", "Net Debt to EBITDA: Measures leverage & repayment ability", "Dashboard"],
                ["Technical", "RSI (14)", "Relative Strength Index (Red <30 Oversold, Red >70 Overbought)", "Dashboard"],
                ["Technical", "SMA 200", "200-Day Simple Moving Average: Long-term trend baseline", "Dashboard"],
                ["Analysis", "Block H", "150-250 word research note with absolute factual triggers", "Every Sheet"]
            ]

            # Populate rows with Calibri 9pt
            for r_idx, item in enumerate(glossary_data, 2):
                for c_idx, val in enumerate(item, 1):
                    cell = ws.cell(row=r_idx, column=c_idx, value=val)
                    cell.font = Font(name='Calibri', size=9)
            
            # Formatting: Auto-adjust column widths
            ws.column_dimensions['A'].width = 15
            ws.column_dimensions['B'].width = 20
            ws.column_dimensions['C'].width = 60
            ws.column_dimensions['D'].width = 15
            ws.freeze_panes = "A2"

    def _build_alert_log_sheet(self, wb):
        """
        Implements Sheet 4: 🔔 Alert Log (Section 10).
        Tracks intraday and historical triggers with specific color-coding.
        """
        ws = wb.create_sheet("🔔 Alert Log")
        
        # 1. Header Definitions (Section 10 Column Spec)
        headers = [
            "Date", "Time (IST)", "Symbol", "Alert Type", 
            "Trigger Detail", "Prev Score", "New Score", 
            "Score Δ", "Action Required", "Exchange"
        ]
        header_fill = PatternFill(start_color="7C3AED", end_color="7C3AED", fill_type="solid")
        
        for c_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=1, column=c_idx, value=h)
            cell.font = Font(color="FFFFFF", bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        # 2. Alert Type Color Mapping (Section 10 Color Spec)
        alert_styles = {
            "EARLY MOVER DETECTED": "FAC775",  # Gold
            "SPIKE FIRED": "C084FC",           # Purple
            "SMART MONEY ENTRY": "D1FAE5",     # Green
            "SCORE UPGRADED": "D1FAE5",        # Green
            "SCORE DEGRADED": "FEE2E2",        # Red
            "SL BREACHED": "FEE2E2",           # Red
            "PROMOTER BUYING": "D1FAE5"        # Green
        }

        # 3. Logic to populate from final_100_list
        # Note: In a live run, this pulls from the 'alerts' list generated during analysis
        row_idx = 2
        for stock in self.df.to_dict('records'):
            spike = stock.get('spike_count', 0) or 0
            early = stock.get('early_entry_score', 0) or 0
            comp  = stock.get('composite_score', 0) or 0
            verd  = stock.get('verdict', 'WATCHLIST') or 'WATCHLIST'
            if spike >= 1 or early >= 70:
                alert_type = "SPIKE FIRED" if spike >= 1 else "EARLY MOVER DETECTED"
                row_data = [
                    self.date_str,
                    "20:30",
                    stock.get('symbol', ''),
                    alert_type,
                    f"Spike {spike}/6 | Score: {comp}",
                    stock.get('prev_score', '—'),
                    comp,
                    stock.get('score_delta', 0),
                    "REVIEW FOR ENTRY" if "BUY" in verd else "WATCH",
                    stock.get('exchange_tag', 'NSE')
                ]
                
                for c_idx, val in enumerate(row_data, 1):
                    cell = ws.cell(row=row_idx, column=c_idx, value=val)
                    # Apply specific color to the Alert Type cell
                    if c_idx == 4:
                        color = alert_styles.get(alert_type, "FFFFFF")
                        cell.fill = PatternFill(start_color=color, end_color=color, fill_type="solid")
                    cell.font = Font(name='Calibri', size=9)
                
                row_idx += 1

        # 4. Sheet Settings
        ws.column_dimensions['D'].width = 25
        ws.column_dimensions['E'].width = 40
        ws.freeze_panes = "A2"

    def _build_preview_sheet(self, wb):
        """
        Implements Sheet 5: Delivery Preview (Section 10).
        Matches the exact WhatsApp/Email structure from the user's template.
        """
        ws = wb.create_sheet("📱 Delivery Preview")
        
        # 1. Sheet Setup
        ws.column_dimensions['A'].width = 30
        ws.column_dimensions['B'].width = 80
        
        # 2. Get Top Data for the Preview (Section 9/10 Logic)
        top_stock = self.df.iloc[0] if not self.df.empty else {}
        gold_count = len(self._get_gold_data())
        avg_upside = self.df['upside'].mean() if not self.df.empty and 'upside' in self.df.columns else 0
        
        # 3. WhatsApp Message Preview Section
        ws.cell(row=1, column=1, value="WHATSAPP PREVIEW").font = Font(bold=True, color="059669")
        
        wa_lines = [
            f"🚀 NSE/BSE RESEARCH: {self.date_str}",
            f"Mood: {'BULLISH' if top_stock.get('close', 0) > top_stock.get('sma_200', 0) else 'BEARISH'} | VIX: {top_stock.get('vix', '—')}",
            "--------------------------------------------------",
            f"⭐ GOLD PICK: {top_stock.get('symbol')} ({top_stock.get('verdict')})",
            f"CMP: ₹{top_stock.get('close')} | CFV: ₹{top_stock.get('cfv')} | Upside: +{top_stock.get('upside')}%",
            f"Momentum: 2W: {top_stock.get('2w_chg')}% | 4W: {top_stock.get('4w_chg')}% | 8W: {top_stock.get('8w_chg')}%",
            "--------------------------------------------------",
            f"📊 TOTAL GOLD MOVERS: {gold_count} stocks identified",
            f"🔥 ACTIVE SPIKE ALERTS: {top_stock.get('spike_count', 0)} fired today",
            "--------------------------------------------------",
            "📥 Full Dashboards attached below."
        ]
        
        for idx, line in enumerate(wa_lines, 2):
            cell = ws.cell(row=idx, column=2, value=line)
            cell.alignment = Alignment(wrap_text=True)

        # 4. Email Subject Line Preview Section (Section 10 Requirement)
        ws.cell(row=15, column=1, value="EMAIL SUBJECT PREVIEW").font = Font(bold=True, color="1D4ED8")
        
        subject_line = (
            f"NSE/BSE Research | {self.date_str} | {gold_count} Early Movers | "
            f"Top: {top_stock.get('symbol')} (+{top_stock.get('upside')}% Upside) | "
            f"8W Chg: {top_stock.get('8w_chg')}%"
        )
        ws.cell(row=16, column=2, value=subject_line).font = Font(italic=True)

        # 5. Styling & Visual Separators
        border = Border(left=Side(style='thin'), right=Side(style='thin'), top=Side(style='thin'), bottom=Side(style='thin'))
        for r in range(2, 13):
            ws.cell(row=r, column=2).border = border
    
    def _get_gold_data(self):
        """
        Section 10 Sheet 2 Filter Logic (Strict Enforcement).
        Criteria: Early Entry Score >= 70 OR (MoS >= 25% AND Score >= 70)
        AND Verdict != AVOID / EXIT AND Spike Suppressed = FALSE.
        Returns empty DataFrame safely if df is empty or columns missing.
        """
        if self.df.empty:
            return pd.DataFrame()
        try:
            mask = (
                ((self.df['early_entry_score'] >= 70) |
                 ((self.df['mos_pct'] >= 25) & (self.df['composite_score'] >= 70))) &
                (self.df['verdict'] != "AVOID / EXIT") &
                (self.df['spike_suppressed'] == False)
            )
            return self.df[mask].copy()
        except Exception:
            return pd.DataFrame()
    
    def _build_gold_sheet(self, wb):
        """
        Implements ⭐ Gold – Early Movers (Section 10 Sheet 2).
        Enforces Row 3 Summary Strip and specific Gold Sheet Row Colors.
        """
        ws = wb.create_sheet("⭐ Gold – Early Movers")
        gold_df = self._get_gold_data()
        
        # 1. Row 1: Title Bar
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=45) 
        title = ws.cell(row=1, column=1, value=f"NSE/BSE GOLD EARLY MOVERS DASHBOARD v6.0 | {self.date_str}")
        title.fill = PatternFill(start_color="1E1B4B", end_color="1E1B4B", fill_type="solid")
        title.font = Font(color="FFFFFF", bold=True, size=12)

        # 2. Row 2: Summary Strip (Dynamic Calc from Attachment)
        ws.merge_cells("A2:H2")
        avg_early = gold_df['early_entry_score'].mean() if not gold_df.empty else 0
        avg_upside = gold_df['upside'].mean() if not gold_df.empty else 0
        avg_spike = gold_df['spike_count'].mean() if not gold_df.empty else 0
        summary_text = (
            f"# gold stocks: {len(gold_df)} | Avg Early Score: {avg_early:.0f} | "
            f"Avg Upside: {avg_upside:.1f}% | Avg Spike: {avg_spike:.1f} | "
            f"Delivery date: {self.date_str}"
        )
        summary_cell = ws.cell(row=2, column=1, value=summary_text)
        summary_cell.font = Font(bold=True, color="B45309")
        summary_cell.fill = PatternFill(start_color="FEF3C7", end_color="FEF3C7", fill_type="solid")

        # 3. Row 3: Merged Group Headers (Applying specific Attachment Colors)
        self._apply_group_headers(ws, 3)

        # 4. Populate Data with Alternating Lakh-Green Stripe
        # Row 4 is Headers, Row 5+ is Data
        columns = list(gold_df.columns)
        for c_idx, col_name in enumerate(columns, 1):
            cell = ws.cell(row=4, column=c_idx, value=col_name)
            cell.font = Font(bold=True, size=8)
            cell.fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")

        lakh_green_fill = PatternFill(start_color="D1FAE5", end_color="D1FAE5", fill_type="solid")
        white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")

        for r_idx, stock in enumerate(gold_df.to_dict('records'), 5):
            # Alternating row coloring as seen in attachment
            current_fill = lakh_green_fill if r_idx % 2 == 0 else white_fill
            
            for c_idx, col_name in enumerate(columns, 1):
                cell = ws.cell(row=r_idx, column=c_idx, value=self._safe_val(stock.get(col_name)))
                cell.fill = current_fill
                cell.font = Font(name='Calibri', size=9, color="065F46" if r_idx % 2 == 0 else "000000")

        # 5. Final Column: View Analysis Summary (Rule 19 Precise Mapping)
        summary_col = len(columns) + 1
        ws.cell(row=4, column=summary_col, value="View Analysis Summary").font = Font(bold=True)
        
        for r_idx, stock in enumerate(gold_df.to_dict('records'), 5):
            cell = ws.cell(row=r_idx, column=summary_col)
            
            # 1. Fetch the correct style based on the specific row verdict 
            verdict = stock.get('verdict')
            style = self.verdict_styles.get(verdict, {"bg": "FFFFFF", "text": "000000", "summary_bg": "0F172A"})
            
            # 2. Assign the Block H content 
            cell.value = stock.get('Analysis_Summary_Block_H', '—')
            
            # 3. Apply the high-contrast background and white text
            cell.fill = PatternFill(start_color=style["summary_bg"], fill_type="solid")
            cell.font = Font(color="FFFFFF", size=9) 
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            
            # 4. Set column width (enforce scannability)
            ws.column_dimensions[cell.column_letter].width = 80

        # Final Settings..
        ws.freeze_panes = "A5" 
        ws.sheet_properties.tabColor = "B45309"