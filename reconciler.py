import pandas as pd

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
    merged['diff_pct'] = 0.0
    dual_mask = merged['exchange_tag'] == 'DUAL_LISTED'
    merged.loc[dual_mask, 'diff_pct'] = (
        (merged['close_NSE'] - merged['close_BSE']) / merged['close_BSE'] * 100
    )

    # 4. Primary Price Selection (Section 1B Rule)
    # Use NSE as primary for liquid, BSE for others
    merged['final_close'] = merged['close_NSE'].fillna(merged['close_BSE'])
    merged['final_symbol'] = merged['symbol_NSE'].fillna(merged['symbol_BSE'])
    
    return merged