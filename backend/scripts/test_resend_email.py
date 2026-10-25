"""Send a standalone Resend test email using backend/.env."""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", encoding="utf-8-sig")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--to", default=os.getenv("RESEND_TEST_TO") or "ecopluseai@gmail.com",
                        help="Recipient; defaults to RESEND_TEST_TO or the Resend account email")
    args = parser.parse_args()
    api_key = os.getenv("RESEND_API_KEY", "").strip()
    if not api_key:
        print("Missing RESEND_API_KEY. Set it in backend/.env.")
        return 1

    try:
        import resend
    except ImportError:
        print("Missing Resend SDK. Install backend/requirements.txt first.")
        return 1

    resend.api_key = api_key
    try:
        response = resend.Emails.send({
            "from": os.getenv("RESEND_FROM", "").strip() or "EcoPulse AI <onboarding@resend.dev>",
            "to": [args.to],
            "subject": "EcoPulse AI - Email Test",
            "html": """
                <h2>Welcome to EcoPulse AI!</h2>
                <p>Your email notification system is working.</p>
                <p>This test was sent by the local EcoPulse test script.</p>
            """,
        })
    except Exception as error:
        # Do not print exception details that might contain credentials.
        print(f"Email test failed ({type(error).__name__}). Check the Resend dashboard logs.")
        print("The onboarding@resend.dev sender can only email your Resend account address.")
        return 1
    print("Resend accepted the test email:", response)
    print("Check the recipient inbox/spam folder and Resend delivery logs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
