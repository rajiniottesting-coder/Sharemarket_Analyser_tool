# rotation_engine.py (Section 3L Implementation)

import pandas as pd

class SectorRotationRadar:
    def calculate_rotation_stage(self, sector_return, nifty_return, fii_flow_trend):
        """
        Implements Section 3L: Rotation Detection Logic.
        RS = (Sector Return - Nifty Return)
        """
        rs = sector_return - nifty_return
        
        # STAGE 4: Distribution (Flattening RS)
        if rs < 0 and fii_flow_trend == 'decreasing':
            return "STAGE 4 — DISTRIBUTION"
        
        # STAGE 1: Early Accumulation (Turning positive)
        if rs > 0 and fii_flow_trend == 'turning_positive':
            return "STAGE 1 — EARLY ACCUMULATION"
            
        # STAGE 2: Confirmed Uptrend
        if rs > 2.0 and fii_flow_trend == 'positive':
            return "STAGE 2 — CONFIRMED UPTREND"
            
        # STAGE 3: Momentum Peak
        if rs > 5.0:
            return "STAGE 3 — MOMENTUM PEAK"
            
        return "NEUTRAL"