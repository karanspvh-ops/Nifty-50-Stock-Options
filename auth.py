"""
auth.py — Zerodha Kite daily login helper (CLI).

Run once each trading morning:
    python auth.py

It prints the login URL. Open it, log in, and you'll be redirected to
the callback page which shows a request_token. Paste it back here and the
access token is saved to kite_token.json for the day.

(You can also do this from the dashboard's Login box.)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend.core.broker import broker

print("\n1) Open this URL in your browser and log in to Zerodha:\n")
print("   " + broker.login_url())
print("\n2) After login you'll land on the callback page showing a request_token.")
rt = input("\n3) Paste the request_token here: ").strip()

try:
    data = broker.generate_session(rt)
    print(f"\n[OK] Logged in as {data.get('user_name')} ({data.get('user_id')}).")
    print("Access token saved to kite_token.json (valid until ~6 AM tomorrow).")
except Exception as e:
    print(f"\n[FAIL] {e}")
