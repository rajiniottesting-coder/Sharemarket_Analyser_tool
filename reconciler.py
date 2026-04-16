import pandas as pd
import numpy as np

def reconcile_exchanges(nse_df, bse_df):
    """
    Implements Section 1B: Cross-Exchange Reconciliation & Tagging.
    Matches via ISIN to identify DUAL_LISTED, NSE_ONLY, and BSE_ONLY.
    """
    if nse_df is None: return bse_df
    if bse_df is None: return nse_df

    # 1. Merge on ISIN (Section 1B primary key requirement)
    # We use an outer join to keep stocks present on either or both exchanges
    merged = pd.merge(
        nse_df, 
        bse_df, 
        on='isin', 
        how='outer', 
        suffixes=('_NSE', '_BSE')
    )

    # 2. Exchange Tagging Logic (Section 1B)
    def apply_tags(row):
        # DUAL_LISTED: Found on both
        if pd.notnull(row['symbol_NSE']) and pd.notnull(row['symbol_BSE']):
            return 'DUAL_LISTED'
        # BSE_SME: Specifically flag if it's on the BSE SME platform
        if pd.notnull(row['symbol_BSE']) and str(row.get('group_BSE', '')).strip() in ['M', 'MT']:
            return 'BSE_SME'
        # BSE_ONLY: Not on NSE
        if pd.notnull(row['symbol_BSE']):
            return 'BSE_ONLY'
        return 'NSE_ONLY'

    merged['exchange_tag'] = merged.apply(apply_tags, axis=1)

    # 3. Arbitrage Signal Logic (Section 1B)
    # Compute NSE/BSE price differential: (NSE_close − BSE_close) / BSE_close × 100
    # INITIALIZE with 0.0 to prevent NaN issues
    merged['diff_pct'] = 0.0
    
    # --- CRITICAL SAFETY FIX: ZERO DIVISION PROTECTION ---
    # Create a condition that MUST be true for the calculation to run:
    # 1. Stock must be DUAL_LISTED
    # 2. NSE price must be a valid number and > 0
    # 3. BSE price must be a valid number and > 0 (The Denominator)
    safe_calc_mask = (
        (merged['exchange_tag'] == 'DUAL_LISTED') & 
        (merged['close_NSE'] > 0) & 
        (merged['close_BSE'] > 0)
    )
    
    # Only perform the calculation on the safe rows
    if safe_calc_mask.any():
        merged.loc[safe_calc_mask, 'diff_pct'] = (
            (merged.loc[safe_calc_mask, 'close_NSE'] - merged.loc[safe_calc_mask, 'close_BSE']) / 
            merged.loc[safe_calc_mask, 'close_BSE'] * 100
        )

    # 4. Primary Price Selection (Section 1B Rule)
    # Use NSE as primary for liquid, BSE for others
    merged['final_close'] = merged['close_NSE'].fillna(merged['close_BSE'])
    merged['final_symbol'] = merged['symbol_NSE'].fillna(merged['symbol_BSE'])
    
    return merged