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

    # 构造激活邮件（纯文本，163 SMTP 无需 HTML）
    msg = MIMEMultipart()
    msg["Subject"] = Header("【AI问答后端】账号激活", "utf-8")
    msg["From"] = settings.MAIL_FROM or settings.MAIL_USERNAME
    msg["To"] = email
    body = (
        f"你好，{username}：\n\n"
        f"请点击以下链接激活账号（{settings.ACTIVATION_TOKEN_EXPIRE_MINUTES} 分钟内有效）：\n"
        f"{activation_link}\n\n"
        f"如非本人操作，请忽略本邮件。"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # SMTP 是阻塞 IO，放线程池执行，避免卡住事件循环
    await asyncio.to_thread(_send_sync, email, msg)



def _send_sync(email: str, msg: MIMEMultipart):
    try:
        with smtplib.SMTP_SSL(settings.MAIL_HOST, settings.MAIL_PORT) as server:
            server.login(settings.MAIL_USERNAME, settings.MAIL_PASSWORD)
            server.sendmail(settings.MAIL_FROM, [email], msg.as_string())
        print(f"✅ 邮件发送成功 -> {email}")
    except Exception as e:
        print(f"❌ 邮件发送失败: {e}")


