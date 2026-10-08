import smtplib
import ssl
import os
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


def _checkmark(val: bool) -> str:
    return "✓" if val else "✗"


def _condition_rows(conditions: dict, label: str) -> str:
    if not conditions:
        return ""
    rows = f"<tr><td colspan='2' style='background:#1e1e1e;color:#888;font-size:11px;padding:4px 8px;'>{label}</td></tr>"
    for name, passed in conditions.items():
        icon = "✓" if passed else "✗"
        color = "#22c55e" if passed else "#888"
        rows += f"""
        <tr>
          <td style='padding:4px 12px;color:#ccc;font-size:12px;'>{name.replace('_', ' ').title()}</td>
          <td style='padding:4px 8px;color:{color};font-weight:bold;'>{icon}</td>
        </tr>"""
    return rows


def build_html(signal: dict) -> str:
    env = signal.get("environment_json", {})
    conditions = signal.get("conditions_json", {})
    ctx_conditions = conditions.get("context", {}) if isinstance(conditions, dict) else {}
    ent_conditions = conditions.get("entry", {}) if isinstance(conditions, dict) else {}

    pair = signal.get("pair", "")
    price = signal.get("buy_stop_price")
    price_str = f"{price:.5f}" if price else "N/A"
    fired_at = signal.get("fired_at", "")
    strategy = signal.get("strategy", "")
    trend = env.get("trend_state", "N/A") if isinstance(env, dict) else "N/A"
    stack = "Bullish ✓" if (isinstance(env, dict) and env.get("ma_stack_bullish")) else "Bearish / Neutral"

    ctx_rows = _condition_rows(ctx_conditions, "4H Context Conditions")
    ent_rows = _condition_rows(ent_conditions, "30min Entry Conditions")

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="background:#121212;color:#e0e0e0;font-family:monospace;margin:0;padding:24px;">
  <div style="max-width:520px;margin:0 auto;background:#1a1a1a;border:1px solid #333;border-radius:8px;overflow:hidden;">
    <div style="background:#22c55e;padding:16px 24px;">
      <h1 style="margin:0;font-size:20px;color:#fff;">🟢 Signal: {pair}</h1>
      <p style="margin:4px 0 0;color:#d1fae5;font-size:13px;">{strategy}</p>
    </div>
    <div style="padding:16px 24px;">
      <table style="width:100%;border-collapse:collapse;">
        <tr>
          <td style="padding:8px 0;color:#888;font-size:12px;">BUY STOP PRICE</td>
          <td style="padding:8px 0;color:#22c55e;font-size:22px;font-weight:bold;text-align:right;">{price_str}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#888;font-size:12px;">PAIR</td>
          <td style="padding:4px 0;text-align:right;">{pair}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#888;font-size:12px;">TIMESTAMP (UTC)</td>
          <td style="padding:4px 0;text-align:right;font-size:12px;">{fired_at}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#888;font-size:12px;">TREND</td>
          <td style="padding:4px 0;text-align:right;">{trend.replace('_', ' ').title()}</td>
        </tr>
        <tr>
          <td style="padding:4px 0;color:#888;font-size:12px;">MA STACK</td>
          <td style="padding:4px 0;text-align:right;">{stack}</td>
        </tr>
      </table>
    </div>
    <div style="padding:0 24px 8px;">
      <table style="width:100%;border-collapse:collapse;border-top:1px solid #333;">
        {ctx_rows}
        {ent_rows}
      </table>
    </div>
    <div style="background:#0f2a1a;padding:12px 24px;border-top:1px solid #333;">
      <p style="margin:0;color:#4ade80;font-size:12px;">
        ⚠️ Validate on TradingView before placing order. This is an automated scan — confirm structure manually.
      </p>
    </div>
  </div>
</body>
</html>"""


def send_email(signal: dict) -> bool:
    sender = os.getenv("EMAIL_SENDER", "")
    password = os.getenv("EMAIL_PASSWORD", "")
    recipient = os.getenv("EMAIL_RECIPIENT", sender)

    if not sender or not password:
        logger.warning("Email credentials not configured — skipping alert")
        return False

    pair = signal.get("pair", "")
    price = signal.get("buy_stop_price")
    price_str = f"{price:.5f}" if price else "N/A"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🟢 Signal: {pair} — Buy Stop {price_str}"
    msg["From"] = sender
    msg["To"] = recipient

    html_body = build_html(signal)
    msg.attach(MIMEText(html_body, "html"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender, password)
            server.sendmail(sender, recipient, msg.as_string())
        logger.info(f"Email sent for {pair} buy stop {price_str}")
        return True
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        return False
