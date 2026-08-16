import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import asyncio
from app.core.config import settings

async def send_activation_email(email: str, username: str, token: str):
    # 使用配置中的 BASE_URL
    activation_link = f"{settings.BASE_URL}/api/v1/activate?token={token}"
    # 如果是降级打印链接，也用同样的方式
    if not settings.MAIL_USERNAME or not settings.MAIL_PASSWORD:
        print(f"📧 邮件未配置，请复制链接激活: {activation_link}")
        return



def _send_sync(email: str, msg: MIMEMultipart):
    try:
        with smtplib.SMTP_SSL(settings.MAIL_HOST, settings.MAIL_PORT) as server:
            server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
            server.sendmail(settings.MAIL_FROM, [email], msg.as_string())
        print(f"✅ 邮件发送成功 -> {email}")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")


