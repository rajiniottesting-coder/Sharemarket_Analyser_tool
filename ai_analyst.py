import os
import time
import google.generativeai as genai
from dotenv import load_dotenv
from fundamental_engine import FundamentalEngine # Import our math engine

# load_dotenv()
# genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
# model = genai.GenerativeModel('gemini-1.5-pro')

# import os
# from dotenv import load_dotenv
# import google.generativeai as genai

# Only loads if .env exists (local); ignored on GitHub Actions
load_dotenv()

# Section 12 Integration: Fetch key from GitHub Secrets injected into Env
gemini_key = os.getenv("GEMINI_API_KEY")

if not gemini_key:
    # This acts as a Section 12B Gatekeeper for API integrity
    raise ValueError("CRITICAL ERROR: GEMINI_API_KEY is missing from environment secrets.")

genai.configure(api_key=gemini_key)
model = genai.GenerativeModel('gemini-1.5-pro')

def get_ai_analysis(stock_list_df):
    """
    Implements Section 0D & 3: Grounded Batch Processing
    Uses FundamentalEngine math to prevent AI 'guessing'.
    """
    all_investor_cards = []
    batch_size = 10 
    engine = FundamentalEngine()
    
    # 1. PRE-CALCULATION: Inject Math into the DataFrame
    # This ensures the AI sees our calculated CFV and Graham Number
    print("🧮 Running Python Fundamental Engine for Batch...")
    
    for index, row in stock_list_df.iterrows():
        # Calculate Graham Number (Section 3A)
        stock_list_df.at[index, 'Graham_No'] = engine.calculate_graham_number(
            row.get('eps', 0), row.get('bvps', 0)
        )
        
        # Calculate PEG Ratio (Section 3A)
        stock_list_df.at[index, 'PEG_Ratio'] = engine.calculate_peg_ratio(
            row.get('pe', 0), row.get('growth_rate', 0)
        )
        
        # Calculate Section 5B: Composite Fair Value (CFV)
        # We pass calculated models to get the weighted average
        models_data = {
            "DCF": row.get('dcf_val', 0),
            "PE": row.get('pe_val', 0),
            "PEG": row.get('peg_val', 0)
        }
        stock_list_df.at[index, 'Calculated_CFV'] = engine.calculate_composite_fair_value(
            row.get('symbol'), row.get('sector', 'General'), models_data
        )

    # 2. BATCH EXECUTION
    batches = [stock_list_df[i:i + batch_size] for i in range(0, len(stock_list_df), batch_size)]
    
    with open("master_prompt/NSE_BSE_Analyser_Master_Prompt_v7_FINAL.txt", "r") as f:
        master_prompt = f.read()

    for idx, batch in enumerate(batches):
        print(f"Processing Batch {idx + 1}/{len(batches)}...")
        
        # Now the string contains our Hard Math columns
        stock_data_text = batch.to_string()
        
        # Explicit Instruction to the AI to USE these numbers
        grounding_instruction = (
            "\n\nCRITICAL: DO NOT calculate valuations yourself. "
            "USE the provided 'Calculated_CFV' and 'Graham_No' columns for your Section 8 cards."
        )
        
        full_query = f"{master_prompt}{grounding_instruction}\n\nDATA BATCH {idx+1}:\n{stock_data_text}"

        try:
            response = model.generate_content(full_query)
            all_investor_cards.append(response.text)
            time.sleep(2) # Section 0D Rate Limiting
        except Exception as e:
            print(f"❌ Batch {idx+1} Error: {e}")

    return "\n\n".join(all_investor_cards)