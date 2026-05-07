"""
services/email.py — Resend email service
Sends welcome email with EA download + API key + install instructions
"""

import httpx
import os

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL     = "TradePattrnly <onboarding@tradepattrnly.com>"
EA_URL         = "https://azzzmgxsydlvwicfqvxm.supabase.co/storage/v1/object/public/assets/TradePattrnly_EA.mq5"

def send_email(to: str, subject: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print(f"[Email] RESEND_API_KEY not set — skipping email to {to}")
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from":    FROM_EMAIL,
                "to":      [to],
                "subject": subject,
                "html":    html,
            },
            timeout=10,
        )
        if r.status_code == 200:
            print(f"[Email] Sent to {to}: {subject}")
            return True
        else:
            print(f"[Email] Failed to {to}: {r.status_code} {r.text}")
            return False
    except Exception as e:
        print(f"[Email] Error: {e}")
        return False


def send_welcome_email(to: str, name: str, api_key: str) -> bool:
    subject = "Welcome to TradePattrnly — Your EA & Setup Guide"
    html = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    body {{ margin:0; padding:0; background:#070b14; font-family:'Helvetica Neue',Arial,sans-serif; }}
    .wrapper {{ max-width:600px; margin:0 auto; background:#0c0f1a; }}
    .header {{ background:#070b14; padding:32px 40px; border-bottom:1px solid #1a1f30; text-align:center; }}
    .body {{ padding:40px; }}
    .footer {{ padding:24px 40px; border-top:1px solid #1a1f30; text-align:center; }}
    h1 {{ color:#E8ECF4; font-size:24px; margin:0 0 8px; font-weight:700; }}
    h2 {{ color:#00C97A; font-size:14px; margin:24px 0 12px; text-transform:uppercase; letter-spacing:.08em; }}
    p {{ color:#8899b4; font-size:14px; line-height:1.7; margin:0 0 12px; }}
    .api-key {{ background:#111626; border:1px solid #252d42; border-radius:6px;
      padding:14px 16px; font-family:monospace; font-size:13px; color:#00C97A;
      word-break:break-all; margin:12px 0; }}
    .btn {{ display:inline-block; padding:14px 28px; background:#00C97A;
      color:#000; text-decoration:none; border-radius:6px; font-weight:700;
      font-size:14px; margin:8px 0; }}
    .step {{ background:#111626; border-left:3px solid #00C97A; border-radius:0 6px 6px 0;
      padding:14px 16px; margin:10px 0; }}
    .step-num {{ color:#00C97A; font-weight:700; font-size:12px;
      text-transform:uppercase; letter-spacing:.06em; margin-bottom:4px; }}
    .step p {{ margin:0; font-size:13px; }}
    .warning {{ background:rgba(240,160,0,.05); border:1px solid rgba(240,160,0,.2);
      border-radius:6px; padding:14px 16px; margin:16px 0; }}
    .warning p {{ color:#F0A500; margin:0; font-size:13px; }}
    a {{ color:#00C97A; }}
    .muted {{ color:#556080; font-size:12px; }}
  </style>
</head>
<body>
  <div class="wrapper">

    <!-- Header -->
    <div class="header">
      <svg width="160" height="36" viewBox="0 0 180 40" fill="none">
        <rect x="0" y="6" width="4" height="22" rx="2" fill="#00C97A"/>
        <rect x="6" y="3" width="4" height="28" rx="2" fill="#00C97A" opacity=".7"/>
        <rect x="12" y="0" width="4" height="34" rx="2" fill="#00C97A" opacity=".9"/>
        <rect x="18" y="5" width="4" height="24" rx="2" fill="#F0A500"/>
        <rect x="24" y="10" width="4" height="16" rx="2" fill="#00C97A" opacity=".6"/>
        <rect x="30" y="2" width="4" height="30" rx="2" fill="#00C97A"/>
        <text x="40" y="28" font-family="Georgia,serif" font-size="20" font-weight="700" fill="#E8ECF4">Trade</text>
        <text x="90" y="28" font-family="Georgia,serif" font-size="20" font-weight="400" fill="#00C97A">Pattrnly</text>
      </svg>
    </div>

    <!-- Body -->
    <div class="body">
      <h1>Welcome, {name}! 👋</h1>
      <p>Your account is ready. Follow the steps below to connect your MT5 and start journalling your trades automatically.</p>

      <!-- API Key -->
      <h2>Your API Key</h2>
      <p>Keep this safe — it connects your MT5 to your journal:</p>
      <div class="api-key">{api_key}</div>

      <!-- Download EA -->
      <h2>Step 1 — Download the EA</h2>
      <div class="step">
        <div class="step-num">Download</div>
        <p>Click below to download the TradePattrnly Expert Advisor (.mq5 file):</p>
        <br>
        <a href="{EA_URL}" class="btn">Download TradePattrnly EA</a>
      </div>

      <!-- Install EA -->
      <h2>Step 2 — Install in MetaTrader 5</h2>
      <div class="step">
        <div class="step-num">Install</div>
        <p>1. Open MetaTrader 5</p>
        <p>2. Go to <strong style="color:#E8ECF4">File → Open Data Folder</strong></p>
        <p>3. Navigate to <strong style="color:#E8ECF4">MQL5 → Experts</strong></p>
        <p>4. Copy <strong style="color:#E8ECF4">TradePattrnly_EA.mq5</strong> into that folder</p>
        <p>5. Back in MT5, press <strong style="color:#E8ECF4">F5</strong> to refresh the Navigator</p>
        <p>6. Find <strong style="color:#E8ECF4">TradePattrnly_EA</strong> under Expert Advisors</p>
      </div>

      <!-- Configure EA -->
      <h2>Step 3 — Configure the EA</h2>
      <div class="step">
        <div class="step-num">Configure</div>
        <p>Drag the EA onto any chart, then set these parameters:</p>
        <br>
        <p><strong style="color:#E8ECF4">API_KEY</strong> = your key above</p>
        <p><strong style="color:#E8ECF4">ACCOUNT_ID</strong> = shown in your dashboard after first sync</p>
      </div>

      <!-- Allow WebRequest -->
      <h2>Step 4 — Allow WebRequest</h2>
      <div class="step">
        <div class="step-num">Required</div>
        <p>1. Go to <strong style="color:#E8ECF4">Tools → Options → Expert Advisors</strong></p>
        <p>2. Check <strong style="color:#E8ECF4">Allow WebRequest for listed URL</strong></p>
        <p>3. Add: <strong style="color:#E8ECF4">https://mt5-scanner-production.up.railway.app</strong></p>
        <p>4. Click OK</p>
      </div>

      <div class="warning">
        <p>⚡ Once the EA is running, your trades will appear in your journal automatically within 60 seconds.</p>
      </div>

      <!-- CTA -->
      <div style="text-align:center; margin:32px 0;">
        <a href="https://tradepattrnly.com/dashboard" class="btn">Open Your Dashboard →</a>
      </div>

      <p>If you need help, reply to this email or visit <a href="https://tradepattrnly.com">tradepattrnly.com</a>.</p>
      <p>Happy trading! 🚀</p>
    </div>

    <!-- Footer -->
    <div class="footer">
      <p class="muted">TradePattrnly · tradepattrnly.com</p>
      <p class="muted">
        <a href="https://tradepattrnly.com/terms">Terms</a> ·
        <a href="https://tradepattrnly.com/privacy">Privacy</a> ·
        <a href="https://tradepattrnly.com/refund">Cancellation</a>
      </p>
      <p class="muted">You're receiving this because you created a TradePattrnly account.</p>
    </div>

  </div>
</body>
</html>
"""
    return send_email(to, subject, html)
