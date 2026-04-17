"""
ai_analyst.py
SECTION 0D & 7 — AI Batch Analysis Engine (v7 FINAL)

Switched from deprecated google-generativeai (Gemini) to Anthropic Claude.
Master prompt v7 goes into the system parameter.
Batch data goes into the user message.
"""

import os
import time
import anthropic
from dotenv import load_dotenv
from fundamental_engine import FundamentalEngine


def _sf(val, default=0.0):
    """Safe float — handles '—', None, '', non-numeric strings."""
    if val is None or val == "" or str(val) in ("—", "--", "N/A"):
        return float(default)
    try:
        return float(val)
    except (ValueError, TypeError):
        return float(default)

load_dotenv()

_anthropic_key = os.getenv("ANTHROPIC_API_KEY")
if not _anthropic_key:
    raise ValueError(
        "CRITICAL ERROR: ANTHROPIC_API_KEY is missing from environment secrets. "
        "Add it to GitHub Secrets or your .env file."
    )

client = anthropic.Anthropic(api_key=_anthropic_key)
MASTER_PROMPT_PATH = "master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt"


def _load_master_prompt() -> str:
    try:
        with open(MASTER_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"⚠️  Master prompt not found at {MASTER_PROMPT_PATH}. Using minimal fallback.")
        return (
            "You are a senior equity research analyst. "
            "For each stock in the data batch, produce a concise investor card "
            "with: verdict, fair value estimate, key strengths, key risks, "
            "and a 150-word analysis summary."
        )


def get_ai_analysis(stock_list_df) -> str:
    """
    SECTION 0D & 3: Grounded Batch Processing via Anthropic Claude.

    Pre-calculates Graham Number, PEG Ratio, and CFV using FundamentalEngine
    so the AI uses our computed values rather than estimating them.

    Batch size: 10–15 stocks per API call (Section 0D).
    Rate limiting: 2s delay between batches.
    Timeout/retry: 1 retry per failed batch.
    """
    all_investor_cards = []
    batch_size = 12  # 10-15 per Section 0D

    engine       = FundamentalEngine()
    master_prompt = _load_master_prompt()

    # ── Pre-calculation: Inject hard math into the DataFrame ──────────────────
    print("🧮 Running Python Fundamental Engine pre-calculations...")
    import pandas as pd
    if not isinstance(stock_list_df, pd.DataFrame):
        stock_list_df = pd.DataFrame(stock_list_df)

    for index, row in stock_list_df.iterrows():
        # Graham Number (Section 3A)
        stock_list_df.at[index, "Graham_No"] = engine.calculate_graham_number(
            _sf(row.get("eps", 0)),
            _sf(row.get("bvps", 0)),
        )
        # PEG Ratio (Section 3A)
        stock_list_df.at[index, "PEG_Ratio"] = engine.calculate_peg_ratio(
            _sf(row.get("pe", 0)),
            _sf(row.get("pat_cagr_3y", row.get("growth_rate", 0))),
        )
        # Composite Fair Value (Section 5B) — uses pre-calculated model values
        models_data = {
            "DCF": _sf(row.get("M1_DCF", row.get("dcf_val", 0))),
            "PE":  _sf(row.get("M3_PE",  row.get("pe_val",  0))),
            "PEG": _sf(row.get("M7_PEG", row.get("peg_val", 0))),
        }
        stock_list_df.at[index, "Calculated_CFV"] = engine.calculate_composite_fair_value(
            str(row.get("symbol", "")),
            str(row.get("sector", "General")),
            models_data,
        )

    # ── Batch execution ───────────────────────────────────────────────────────
    batches = [
        stock_list_df.iloc[i: i + batch_size]
        for i in range(0, len(stock_list_df), batch_size)
    ]
    total_batches = len(batches)

    grounding_instruction = (
        "\n\nCRITICAL INSTRUCTION: Do NOT calculate valuations yourself. "
        "USE the provided 'Calculated_CFV' column as the Composite Fair Value "
        "and 'Graham_No' as the Graham Number. "
        "These values were computed by the Python Fundamental Engine — trust them. "
        "For each stock, output: "
        "(1) Investor Card (Section 8 Blocks A–G), "
        "(2) Analysis Summary (Block H: 150–250 words, absolute facts, recent events)."
    )

    credit_exhausted = False   # flag to abort all batches on credit error

    for idx, batch in enumerate(batches):
        # Skip remaining batches if credits exhausted
        if credit_exhausted:
            all_investor_cards.append(
                f"[Batch {idx + 1} skipped — Anthropic credits exhausted. "
                f"Top up at console.anthropic.com/settings/billing]"
            )
            continue

        print(f"🤖 Processing batch {idx + 1}/{total_batches} "
              f"({len(batch)} stocks)...")

        stock_data_text = batch.to_string(max_colwidth=120)
        user_message    = (
            f"{grounding_instruction}\n\n"
            f"DATA BATCH {idx + 1}/{total_batches}:\n{stock_data_text}"
        )

        # Try once; NO retry on credit errors (pointless — credits won't refill mid-run)
        try:
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=4096,
                system=master_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            card_text = response.content[0].text
            all_investor_cards.append(card_text)
            print(f"   ✅ Batch {idx + 1} complete.")
            # Section 0D: Rate limiting — 2s between successful batches
            if idx < total_batches - 1:
                time.sleep(2)
        except Exception as e:
            err_str = str(e)
            # Detect credit exhaustion — no point retrying
            if "credit balance is too low" in err_str or "insufficient_balance" in err_str:
                credit_exhausted = True
                print(f"   ⚠️  Anthropic credits exhausted — skipping all remaining batches.")
                print(f"      Top up at: https://console.anthropic.com/settings/billing")
                all_investor_cards.append(
                    f"[AI analysis unavailable — Anthropic credit balance too low. "
                    f"Please top up at console.anthropic.com/settings/billing. "
                    f"All other Excel data is complete and accurate.]"
                )
            else:
                # Non-credit error — retry once
                print(f"   ⚠️  Batch {idx + 1} attempt 1 failed: {e}. Retrying in 5s...")
                time.sleep(5)
                try:
                    response = client.messages.create(
                        model="claude-sonnet-4-5",
                        max_tokens=4096,
                        system=master_prompt,
                        messages=[{"role": "user", "content": user_message}],
                    )
                    card_text = response.content[0].text
                    all_investor_cards.append(card_text)
                    print(f"   ✅ Batch {idx + 1} complete (retry).")
                    if idx < total_batches - 1:
                        time.sleep(2)
                except Exception as e2:
                    print(f"   ❌ Batch {idx + 1} failed after retry: {e2}")
                    all_investor_cards.append(
                        f"[Batch {idx + 1} analysis unavailable: {e2}]"
                    )

    return "\n\n".join(all_investor_cards)