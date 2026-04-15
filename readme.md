# NSE/BSE Stock Analyser v7.0

An end-to-end automated equity research engine designed for the Indian markets. This platform identifies high-conviction "Early Mover" opportunities by combining quantitative price/volume data with deep fundamental forensics and AI-driven intelligence.

## 🚀 Key Features
- **3-Stage Pre-Screener (Section 0):** Python-native filtering of 5,000+ stocks down to 100 to optimize API costs.
- **Deep Valuation Engine (Section 3):** 7-model weighted Fair Value (DCF, Graham, PEG, etc.) with Margin of Safety (MoS) calculation.
- **Forensic Risk Checks (Section 3G/H):** Automated suppression of alerts for stocks with high promoter pledge (>20%), Altman Z distress, or Beneish M-Score manipulation risk.
- **Section 10 Compliance:** Generation of 6-sheet professional Excel dashboards with Indian currency formatting (Lakhs/Crores) and alternating "Lakh-White" row stripes.
- **WhatsApp Gateway (Section 11):** Interactive command parser (`why RELIANCE`, `early movers today`) via Twilio Sandbox.
- **Automated Gatekeeper (Section 12):** Market holiday awareness and data integrity checks prior to daily 19:00 IST execution.

## 🛠 Project Architecture (33 Files)
- `master_funnel.py`: Core orchestrator of the daily pipeline.
- `gate_check.py`: Execution gatekeeper (holidays/file availability).
- `v7_analysis_engine.py`: Primary quant and fundamental logic.
- `excel_generator.py`: Institutional-grade Excel dashboard engine.
- `whatsapp_gateway.py`: Flask-based Twilio/Ngrok bridge for mobile alerts.

## ⚙️ Configuration & Deployment
This project is designed to run for free on **GitHub Actions**.

### 1. Requirements
```bash
pip install pandas openpyxl requests google-generativeai twilio pytz flask python-dotenv