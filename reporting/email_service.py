"""
email_service.py
SECTION 12E — Email Delivery Service (v7 FINAL)

Fixes:
- Added msg['From'], msg['To'], msg['Reply-To'] (missing before, caused Gmail rejection)
- Used smtp.sendmail() explicitly with from/to instead of send_message()
- Added is_error parameter support (called from master_funnel on critical failure)
- recipient_email now properly used
- Safe guard when env vars are missing
"""

import smtplib
import os
from email.message import EmailMessage
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


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

    date_str = datetime.now().strftime("%Y-%m-%d")
    msg      = EmailMessage()

    # ── Required headers (Gmail rejects without these) ────────────────────────
    msg["From"]     = sender_email
    msg["To"]       = recipient_email
    msg["Reply-To"] = sender_email

    # ── Message content by scenario ──────────────────────────────────────────
    if is_skip:
        reason = skip_reason or "Unknown reason"
        msg["Subject"] = f"NSE/BSE Analyser | SKIPPED | {date_str} | {reason}"
        msg.set_content(
            f"NSE/BSE Analyser · {date_str}\n\n"
            f"Pipeline skipped: {reason}\n"
            f"System status: OK — no action needed.\n"
            f"Next scheduled run: next trading day at 07:00 IST."
        )

    elif is_error:
        err = error_msg or "Unknown error"
        msg["Subject"] = f"NSE/BSE Analyser | CRITICAL ERROR | {date_str}"
        msg.set_content(
            f"NSE/BSE Analyser · {date_str}\n\n"
            f"CRITICAL PIPELINE FAILURE:\n{err}\n\n"
            f"Please check the GitHub Actions log for the full traceback."
        )

    else:
        msg["Subject"] = f"NSE/BSE Analyser Report | {date_str} | Dashboards Attached"
        msg.set_content(
            f"NSE/BSE Analyser · {date_str}\n\n"
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
