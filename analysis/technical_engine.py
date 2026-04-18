import pandas as pd
try:
    import pandas_ta as ta
    PANDAS_TA_AVAILABLE = True
except ImportError:
    PANDAS_TA_AVAILABLE = False
    ta = None

class TechnicalAnalysisEngine:
    def __init__(self):
        # Timeframes: Daily (primary) + Weekly + Monthly (Section 5 structure)
        pass

    def calculate_indicators(self, df, exchange_tag='NSE'):
        """
        Implements Section 5: Trend, Momentum, Volume, and Volatility.
        """
        # --- TREND INDICATORS ---
        df['SMA_5'] = ta.sma(df['close'], length=5)
        df['SMA_20'] = ta.sma(df['close'], length=20)
        df['SMA_50'] = ta.sma(df['close'], length=50)
        df['SMA_100'] = ta.sma(df['close'], length=100)
        df['SMA_200'] = ta.sma(df['close'], length=200)
        
        # Golden Cross / Death Cross (Section 5)
        df['golden_cross'] = df['SMA_50'] > df['SMA_200']
        
        # EMA & Supertrend (10,3)
        df['EMA_9'] = ta.ema(df['close'], length=9)
        supertrend = ta.supertrend(df['high'], df['low'], df['close'], length=10, multiplier=3)
        df['ST_Direction'] = supertrend['SUPERTd_10_3.0'] # 1=Buy, -1=Sell

        # --- MOMENTUM OSCILLATORS ---
        df['RSI_14'] = ta.rsi(df['close'], length=14)
        macd = ta.macd(df['close'])
        df['MACD'] = macd['MACD_12_26_9']
        df['MACD_Signal'] = macd['MACDS_12_26_9']
        df['Stoch_K'] = ta.stoch(df['high'], df['low'], df['close'])['STOCHk_14_3_3']

        # --- VOLUME & DELIVERY (Section 5) ---
        df['OBV'] = ta.obv(df['close'], df['volume'])
        df['VWAP'] = ta.vwap(df['high'], df['low'], df['close'], df['volume'])
        
        # BSE SME Specific Logic: Use 10-day volume (Section 5)
        vol_window = 10 if exchange_tag == 'BSE_SME' else 50
        df['avg_vol_50d'] = df['volume'].rolling(window=vol_window).mean()
        df['vol_ratio'] = df['volume'] / df['avg_vol_50d']
        
        # Volume Quality Tagging
        df['vol_quality'] = "Standard"
        df.loc[df['vol_ratio'] > 2, 'vol_quality'] = "Significant"
        df.loc[df['vol_ratio'] > 5, 'vol_quality'] = "Extraordinary"

        # --- VOLATILITY ---
        bbands = ta.bbands(df['close'], length=20, std=2)
        df['BB_Upper'] = bbands['BBU_20_2.0']
        df['BB_Lower'] = bbands['BBL_20_2.0']
        df['ATR_14'] = ta.atr(df['high'], df['low'], df['close'], length=14)

        return df