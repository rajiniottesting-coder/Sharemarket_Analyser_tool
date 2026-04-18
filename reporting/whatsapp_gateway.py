from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from reporting.command_parser import CommandParser
from database.data_bridge import load_latest_analysis_results

app = Flask(__name__)

# Initialize our Analyst Brain
try:
    context = load_latest_analysis_results()
    parser = CommandParser(context)
except:
    parser = CommandParser([])

@app.route("/whatsapp", methods=['POST'])
def whatsapp_reply():
    # 1. Get the message sent by Rajkumar
    incoming_msg = request.values.get('Body', '').lower()
    
    # 2. Pass it to our Section 11 Command Parser
    response_text = parser.execute(incoming_msg)
    
    # 3. Build the WhatsApp Response
    twilio_resp = MessagingResponse()
    msg = twilio_resp.message()
    msg.body(response_text)
    
    return str(twilio_resp)

if __name__ == "__main__":
    # Runs on port 5000
    app.run(port=5000)