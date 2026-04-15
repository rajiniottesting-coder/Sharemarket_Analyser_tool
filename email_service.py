import smtplib
import os
from email.message import EmailMessage
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

def send_analysis_email(is_skip=False, skip_reason=None, attachments=None):
    """
    Handles Section 12E Email Delivery.
    """
    sender_email = os.getenv("SENDER_EMAIL")
    sender_password = os.getenv("SENDER_APP_PASSWORD") # Use Gmail App Password
    recipient_email = os.getenv("USER_EMAIL_ID")
    
    msg = EmailMessage()
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    if is_skip:
        # Section 12E - Skip Notification
        msg['Subject'] = f"NSE/BSE Analyser | SKIPPED | {date_str} | {skip_reason}"
        msg.set_content(f"NSE/BSE Analyser · {date_str}\n\nPipeline skipped: {skip_reason}\nSystem status: OK")
    else:
        # Full Report Delivery
        msg['Subject'] = f"NSE/BSE Analyser Report - {date_str}"
        msg.set_content(f"Please find the institutional-grade research dashboards for {date_str} attached.")
        
        # Section 12E - Attachments (Full Dashboard + Gold List)
        if attachments:
            for file_path in attachments:
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as f:
                        file_data = f.read()
                        file_name = os.path.basename(file_path)
                    msg.add_attachment(file_data, maintype='application', subtype='octet-stream', filename=file_name)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print(f"Email sent successfully to {recipient_email}")
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == "__main__":
    # Test Skip Notification
    send_analysis_email(is_skip=True, skip_reason="Manual Test")