import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from src.utils import setup_logging

logger = setup_logging("gmail-sender")

class GmailSender:
    def __init__(self):
        self.sender_email = os.getenv('GMAIL_SENDER_EMAIL')
        self.app_password = os.getenv('GMAIL_APP_PASSWORD')
        self.recipient_email = os.getenv('GMAIL_RECIPIENT_EMAIL')
        
        if not all([self.sender_email, self.app_password, self.recipient_email]):
            raise ValueError("Gmail credentials missing")
    
    def send_email(self, subject: str, html_content: str) -> bool:
        try:
            logger.info(f"Enviando email: {subject}")
            
            message = MIMEMultipart('alternative')
            message['From'] = self.sender_email
            message['To'] = self.recipient_email
            message['Subject'] = subject
            
            message.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
                server.login(self.sender_email, self.app_password)
                server.send_message(message)
            
            logger.info("✓ Email enviado")
            return True
        except Exception as e:
            logger.error(f"Error: {e}")
            return False
