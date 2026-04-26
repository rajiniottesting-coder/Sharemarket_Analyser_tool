"""
email_service.py
SECTION 12E — Email Delivery Service (v7 FINAL)

Fixes:
- Added msg['From'], msg['To'], msg['Reply-To'] (missing before, caused Gmail rejection)
- Used smtp.sendmail() explicitly with from/to instead of send_message()
- Added is_error parameter support (called from master_funnel on critical failure)
- recipient_email now properly used
- Safe guard when env vars are missing
- Fixed date_str to reflect the market target date embedded in skip_reason,
  not the pipeline run date — avoids confusing date mismatch in emails
- Stripped redundant "SKIP: " prefix from body (reason already says "Pipeline skipped:")
- Cleaned subject line: shows short reason only, no raw prefix
- Fixed delivery time: "05:00 IST" (was incorrectly "07:00 IST"; later updated to current schedule)
"""

import smtplib
import os
import re
from email.message import EmailMessage
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


def _clean_reason(raw_reason: str) -> str:
    """
    Strip internal prefixes (e.g. 'SKIP: ', 'C1 ', 'C5 FAIL: ') that are
    meaningful in logs but look confusing in email bodies.

    Examples:
        'SKIP: 2026-04-18 is a weekend (Saturday). No market data.'
        → '2026-04-18 is a weekend (Saturday). No market data.'

        'C5 FAIL: NSE bhav copy not available yet.'
        → 'NSE bhav copy not available yet.'
    """
    reason = raw_reason or "Unknown reason"
    # Remove known log prefixes
    reason = re.sub(r'^SKIP:\s*', '', reason)
    reason = re.sub(r'^C\d+\s+FAIL:\s*', '', reason)
    reason = re.sub(r'^C\d+\s+\w+:\s*', '', reason)   # e.g. "C1 Weekend: "
    reason = re.sub(r'^C\d+:\s*', '', reason)
    return reason.strip()


def _short_reason(raw_reason: str) -> str:
    """
    Build a clean one-line subject summary (max 60 chars, word-boundary safe).

    Rules:
    - Strips log prefixes via _clean_reason()
    - Removes leading date stamp (already shown in the subject pipe)
    - Removes embedded 'for YYYY-MM-DD' phrases (C3 NSE-style reasons)
    - Splits sentences only when the word before the period is >= 4 chars,
      so abbreviations like 'Dr.', 'Mr.', 'St.' are never broken mid-word
    - Truncates at last word boundary within 60 chars

    Examples:
        'SKIP: 2026-04-18 is a weekend (Saturday). No market data.'
        → 'is a weekend (Saturday). No market data'

        'SKIP: 2026-04-14 is a market holiday (Dr. Ambedkar Jayanti).'
        → 'is a market holiday (Dr. Ambedkar Jayanti)'

        'SKIP: NSE Bhav Copy not available for 2026-04-17. URL returned non-200.'
        → 'NSE Bhav Copy not available'

        'C5 FAIL: NSE row count too low (120 rows). Minimum expected: 500.'
        → 'NSE row count too low (120 rows). Minimum expected: 500'
    """
    clean = _clean_reason(raw_reason)
    # Strip leading date stamp (already in subject as market_date pipe)
    s = re.sub(r'^\d{4}-\d{2}-\d{2}\s+', '', clean)
    # Strip embedded date reference e.g. "not available for 2026-04-17"
    s = re.sub(r'\s+for \d{4}-\d{2}-\d{2}', '', s)
    # Split sentences only where preceding word is >=4 chars (avoids 'Dr.', 'Mr.')
    parts = re.split(r'(?<=\w{4})\.\s+(?=[A-Z])', s)
    text  = parts[0].strip().rstrip('.')
    if len(text) <= 60:
        return text
    # Word-boundary truncation — never cut mid-word
    t = text[:60]
    last_space = t.rfind(' ')
    return t[:last_space] if last_space > 30 else t


def send_analysis_email(
    is_skip: bool = False,
    skip_reason: str = None,
    is_error: bool = False,
    error_msg: str = None,
    attachments: list = None,
) -> None:
    """
    SECTION 12E: Email Delivery.

    Handles three scenarios:
    1. is_skip=True  → brief skip notification (no attachments)
    2. is_error=True → critical error alert (no attachments)
    3. default       → full report with attachments
    """
    sender_email    = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_APP_PASSWORD")
    recipient_email = os.getenv("USER_EMAIL_ID")

    if not all([sender_email, sender_password, recipient_email]):
        print("⚠️  Email config incomplete (SENDER_EMAIL / SENDER_APP_PASSWORD / USER_EMAIL_ID).")
        return

    # Use today's run date for the header.
    # For skip emails the body also shows the market date extracted from the reason.
    run_date_str = datetime.now().strftime("%Y-%m-%d")
    msg          = EmailMessage()

    # ── Required headers (Gmail rejects without these) ────────────────────────
    msg["From"]     = sender_email
    msg["To"]       = recipient_email
    msg["Reply-To"] = sender_email

    # ── Message content by scenario ──────────────────────────────────────────
    if is_skip:
        raw_reason    = skip_reason or "Unknown reason"
        clean_reason  = _clean_reason(raw_reason)
        short_reason  = _short_reason(raw_reason)

        # Extract the market date from the reason if present (e.g. "2026-04-18 is a weekend")
        # For C5 data failures there is no date in the reason — fall back to run date.
        date_match   = re.search(r'(\d{4}-\d{2}-\d{2})', clean_reason)
        market_date  = date_match.group(1) if date_match else None
        date_label   = f"Market date: {market_date}" if market_date else f"Run date:    {run_date_str}"

        msg["Subject"] = f"NSE/BSE Analyser | SKIPPED | {market_date or run_date_str} | {short_reason}"
        msg.set_content(
            f"NSE/BSE Analyser  ·  {date_label}\n"
            f"Run triggered:    {run_date_str}\n\n"
            f"Pipeline skipped\n"
            f"─────────────────\n"
            f"{clean_reason}\n\n"
            f"System status:    OK — no action needed.\n"
            f"Next scheduled:   Next trading day at 05:00 IST."
        )

    elif is_error:
        err = error_msg or "Unknown error"
        msg["Subject"] = f"NSE/BSE Analyser | CRITICAL ERROR | {run_date_str}"
        msg.set_content(
            f"NSE/BSE Analyser · {run_date_str}\n\n"
            f"CRITICAL PIPELINE FAILURE:\n{err}\n\n"
            f"Please check the GitHub Actions log for the full traceback."
        )

    else:
        msg["Subject"] = f"NSE/BSE Analyser Report | {run_date_str} | Dashboards Attached"
        msg.set_content(
            f"NSE/BSE Analyser · {run_date_str}\n\n"
            f"Today's institutional-grade research dashboards are attached.\n\n"
            f"Files included:\n"
            + "\n".join(
                [f"  • {os.path.basename(a)}" for a in (attachments or []) if os.path.exists(a)]
            )
        )

        # ── Attachments ───────────────────────────────────────────────────────
        if attachments:
            for file_path in attachments:
                if file_path and os.path.exists(file_path):
                    with open(file_path, "rb") as f:
                        file_data = f.read()
                    filename = os.path.basename(file_path)
                    # Determine subtype from extension
                    if filename.endswith(".xlsx"):
                        subtype = "vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    elif filename.endswith(".txt"):
                        subtype = "plain"
                        msg.add_attachment(
                            file_data, maintype="text", subtype=subtype, filename=filename
                        )
                        continue
                    else:
                        subtype = "octet-stream"
                    msg.add_attachment(
                        file_data, maintype="application", subtype=subtype, filename=filename
                    )
                else:
                    print(f"⚠️  Attachment not found, skipping: {file_path}")

    # ── Send via Gmail SMTP SSL ───────────────────────────────────────────────
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.sendmail(sender_email, recipient_email, msg.as_string())
        print(f"✅ Email sent to {recipient_email}")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


if __name__ == "__main__":
    # Test skip notification
    send_analysis_email(is_skip=True, skip_reason="Manual test run")