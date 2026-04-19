"""
test_yfinance.py — Diagnose yfinance for NSE stocks
Run: python test_yfinance.py
"""
import yfinance as yf
import sys

print(f"yfinance version: {yf.__version__}")
print()

# Test 1: Single ticker .info
print("Test 1: yf.Ticker('RELIANCE.NS').info")
try:
    tk = yf.Ticker("RELIANCE.NS")
    info = tk.info
    print(f"  Keys returned: {len(info)}")
    price_fields = {k: info[k] for k in 
                    ["currentPrice","regularMarketPrice","previousClose","marketCap"]
                    if k in info}
    print(f"  Price fields: {price_fields}")
    if not price_fields:
        print(f"  Sample keys: {list(info.keys())[:10]}")
except Exception as e:
    print(f"  ERROR: {e}")

print()

# Test 2: Quarterly income statement
print("Test 2: yf.Ticker('RELIANCE.NS').quarterly_income_stmt")
try:
    tk = yf.Ticker("RELIANCE.NS")
    qi = tk.quarterly_income_stmt
    if qi is None or qi.empty:
        print("  EMPTY or None")
        # Try old API name
        qi2 = tk.quarterly_financials
        print(f"  quarterly_financials: {'has data' if qi2 is not None and not qi2.empty else 'empty'}")
    else:
        print(f"  Shape: {qi.shape}")
        print(f"  Rows: {list(qi.index[:5])}")
        print(f"  Cols (dates): {list(qi.columns[:3])}")
except Exception as e:
    print(f"  ERROR: {e}")

print()

# Test 3: Annual income statement
print("Test 3: yf.Ticker('RELIANCE.NS').income_stmt")
try:
    tk = yf.Ticker("RELIANCE.NS")
    ai = tk.income_stmt
    if ai is None or ai.empty:
        print("  EMPTY or None")
        ai2 = tk.financials
        print(f"  financials: {'has data' if ai2 is not None and not ai2.empty else 'empty'}")
    else:
        print(f"  Shape: {ai.shape}")
        print(f"  Rows: {list(ai.index[:5])}")
        rev = [r for r in ai.index if 'revenue' in str(r).lower() or 'Revenue' in str(r)]
        print(f"  Revenue row: {rev}")
except Exception as e:
    print(f"  ERROR: {e}")