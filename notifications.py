# notifications.py — the one place in the app that sends email.
#
# Every email the system ever sends goes through send_notification() here:
#   * the 6-digit code that verifies a new account (FR07),
#   * the 6-digit code that logs a customer in without a password (FR08),
#   * "a customer booked you" to the fundi (FR09),
#   * "your booking was accepted / declined" to the customer (FR14/FR15),
#   * "the job is done, please confirm" to the customer (FR16),
#   * "your fundi application was approved / rejected" to the fundi (FR19/FR20).
#
# How it sends depends on whether a real mail server is configured:
#   * If MAIL_SERVER and friends are set in the environment (see
#     .env.example), the message is sent for real with Python's built-in
#     smtplib — no extra library is needed or installed.
#   * If no mail server is configured, the app runs in DEMO mode: the
#     message is written to instance/dev-outbox.txt (a plain text file you
#     can open and read), and send_notification() returns False so the
#     calling page can show the code in a clearly labelled "demo email"
#     box. That way every email flow works end to end on a laptop with no
#     mail server at all.
#
# The function returns True when the email really went out, and False when
# it was only written to the demo outbox. It never raises an error for a
# mail problem — a failed mail server must never break a booking or a
# login; the message is written to the outbox instead and the flow carries
# on.

import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage

# The folder the demo outbox lives in. It sits next to the database, under
# instance/, and is created the first time a demo email is written.
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
OUTBOX_PATH = os.path.join(INSTANCE_DIR, "dev-outbox.txt")


def mail_is_configured():
    # True only when the settings a real send needs are all present.
    # MAIL_SERVER alone is not enough: without a username and password
    # most servers will refuse the message anyway.
    return bool(
        os.environ.get("MAIL_SERVER")
        and os.environ.get("MAIL_USERNAME")
        and os.environ.get("MAIL_PASSWORD")
    )


def send_notification(to, subject, body):
    """Send one email, for real or in demo mode.

    to      — the email address the message is for.
    subject — the subject line.
    body    — the plain-text body.

    Returns True if the message was sent through a real mail server,
    False if it was only written to the demo outbox.
    """
    if not to:
        # No address, no email. This happens for old seed rows with no
        # owner; the caller has already decided the flow goes on without it.
        return False

    if mail_is_configured():
        # Real send, using only Python's standard library.
        try:
            message = EmailMessage()
            message["From"] = os.environ.get("MAIL_FROM") or os.environ.get(
                "MAIL_USERNAME"
            )
            message["To"] = to
            message["Subject"] = subject
            message.set_content(body)

            server = os.environ.get("MAIL_SERVER")
            port = int(os.environ.get("MAIL_PORT", "587"))
            # Port 587 is the normal "submit with STARTTLS" port; port 465
            # is the older "connect already encrypted" port. Both are
            # handled so the common setups just work.
            if port == 465:
                smtp = smtplib.SMTP_SSL(server, port, timeout=15)
            else:
                smtp = smtplib.SMTP(server, port, timeout=15)
                smtp.starttls()
            smtp.login(
                os.environ.get("MAIL_USERNAME"), os.environ.get("MAIL_PASSWORD")
            )
            smtp.send_message(message)
            smtp.quit()
            return True
        except Exception:
            # A mail server that is down or misconfigured must not break
            # the user's booking or login. Fall through to the demo outbox
            # so the message (and any code in it) is still visible.
            pass

    # Demo mode: write the message to a plain text file, newest first in
    # reading order (each message is appended at the end with a divider).
    os.makedirs(INSTANCE_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    text = (
        f"\n{'=' * 60}\n"
        f"To: {to}\n"
        f"Subject: {subject}\n"
        f"Date: {stamp}\n"
        f"{'-' * 60}\n"
        f"{body}\n"
    )
    with open(OUTBOX_PATH, "a", encoding="utf-8") as handle:
        handle.write(text)
    return False


def send_verification_code(user, code):
    # The email a new customer gets right after registering (FR07).
    # Returns True when it really went out, False in demo mode.
    return send_notification(
        user.email,
        "Your FundiMtaani verification code",
        (
            f"Hello {user.full_name},\n\n"
            f"Welcome to FundiMtaani. Your verification code is:\n\n"
            f"    {code}\n\n"
            "Enter this code on the verification page to activate your "
            "account. The code works for 15 minutes and can only be used once.\n\n"
            "If you did not create this account, just ignore this email."
        ),
    )


def send_login_code(user, code):
    # The email a customer gets when they ask to log in with a code
    # instead of their password (FR08).
    return send_notification(
        user.email,
        "Your FundiMtaani login code",
        (
            f"Hello {user.full_name},\n\n"
            f"Your login code is:\n\n"
            f"    {code}\n\n"
            "Enter this code on the login page to sign in. The code works "
            "for 15 minutes and can only be used once.\n\n"
            "If you did not ask to log in, just ignore this email."
        ),
    )


def send_booking_created(booking):
    # Tell the fundi that a customer has booked them (FR09). The fundi's
    # email comes from their customer account; a fundi row with no linked
    # account has no email, and then nothing is sent.
    fundi = booking.worker
    if not fundi or not fundi.owner:
        return False
    customer = booking.customer
    return send_notification(
        fundi.owner.email,
        f"New booking from {customer.full_name} — FundiMtaani",
        (
            f"Hello {fundi.name},\n\n"
            f"{customer.full_name} has booked you on FundiMtaani.\n\n"
            f"Job: {booking.message or 'No description given'}\n"
            f"Where: {booking.estate or fundi.estate}\n"
            f"When: {booking.preferred_date or 'Flexible'} "
            f"({booking.preferred_slot})\n"
            f"Customer phone: {customer.phone}\n\n"
            "Sign in and open My Jobs to accept or decline this booking."
        ),
    )


def send_booking_status(booking, new_status):
    # Tell the customer what happened to their booking (FR14/FR15/FR16).
    # new_status is one of "accepted", "declined" or "job_done".
    customer = booking.customer
    fundi = booking.worker
    if new_status == "accepted":
        subject = f"Your booking with {fundi.name} was accepted"
        text = (
            f"Hello {customer.full_name},\n\n"
            f"Good news — {fundi.name} has accepted your booking.\n\n"
            f"Job: {booking.message or 'No description given'}\n"
            f"When: {booking.preferred_date or 'Flexible'} "
            f"({booking.preferred_slot})\n\n"
            "You can reach them directly on "
            f"{fundi.phone} if plans change."
        )
    elif new_status == "declined":
        subject = f"Your booking with {fundi.name} was declined"
        text = (
            f"Hello {customer.full_name},\n\n"
            f"Sorry — {fundi.name} has declined your booking, so it will "
            "not go ahead.\n\n"
            "Please search the directory for another fundi in your trade "
            "and estate."
        )
    else:  # job_done
        subject = f"{fundi.name} marked your job as done — please confirm"
        text = (
            f"Hello {customer.full_name},\n\n"
            f"{fundi.name} has marked your job as done.\n\n"
            f"Job: {booking.message or 'No description given'}\n\n"
            "Please open My Bookings and confirm the job is complete — "
            "only you can confirm it. Once you confirm, you can leave a "
            "review for the fundi."
        )
    return send_notification(customer.email, subject, text)


def send_application_decision(worker, approved, reason=""):
    # Tell a fundi whether Admin approved or rejected their application
    # (FR19/FR20). A rejected fundi gets the reason Admin wrote.
    if not worker.owner:
        return False
    if approved:
        subject = "Your FundiMtaani application was approved"
        text = (
            f"Hello {worker.name},\n\n"
            "Good news — your fundi application has been approved. "
            "Your profile is now live in the FundiMtaani directory and "
            "customers can find you, book you and contact you.\n\n"
            "Sign in to see your bookings under My Jobs."
        )
    else:
        subject = "Your FundiMtaani application was not approved"
        text = (
            f"Hello {worker.name},\n\n"
            "Your fundi application was not approved.\n\n"
            f"Reason given by the admin: {reason or 'No reason given.'}\n\n"
            "You can update your application and submit it again from "
            "the Join page once you have fixed the problem."
        )
    return send_notification(worker.owner.email, subject, text)
