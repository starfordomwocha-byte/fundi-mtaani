# app.py — FundiMtaani, the web app itself.
#
# What this file is: every page of the site lives here as a "route" — a
# small function that answers one web address. When a visitor opens a page,
# Flask calls the matching route, the route reads or writes the database
# (see models.py), and then shows a template (see templates/).
#
# What FundiMtaani is: a directory of local fundis (plumbers, electricians,
# carpenters and more) in three Nairobi estates — Langata, Madaraka and
# Nairobi West. Residents find a fundi, book them, and pay the fundi
# directly. There is no payment gateway, no commission and no fee anywhere
# in this system.
#
# How the site is organised, in the order a person meets it:
#   Visitor  — search and browse fundis, view a profile, contact a fundi
#              on WhatsApp or by phone (FR01-FR05).
#   Customer — register, verify their email with a code, log in by password
#              or by emailed code, book a fundi, confirm a job is done,
#              and review a completed booking (FR06-FR11).
#   Fundi    — a customer who applies through /join, is approved by Admin,
#              then sees their own bookings, accepts or declines them, and
#              marks jobs done (FR12-FR17).
#   Admin    — logs in on a separate page, approves or rejects fundi
#              applications after checking the ID, suspends or reactivates
#              customers, manages categories, removes reviews and reads
#              reports (FR18-FR27).
#
# A note on sessions: a customer login and an admin login are kept apart.
# session["user_id"] means "a customer is signed in"; session["admin_id"]
# means "an admin is signed in". The two never mix.

import csv
import hmac
import io
import os
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict
from datetime import timedelta
from functools import wraps

from dotenv import load_dotenv
from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
# Passwords are hashed with pbkdf2:sha256 (not Werkzeug's default scrypt),
# because the system Python on macOS has no hashlib.scrypt, so the
# default raises AttributeError there. check_password_hash() reads the
# method from the stored hash, so logins work the same either way.
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from models import Admin, Booking, Category, Review, User, VerificationCode, Worker, db
from notifications import (
    send_application_decision,
    send_booking_created,
    send_booking_status,
    send_login_code,
    send_verification_code,
)

load_dotenv()

# The three estates FundiMtaani covers. This list is the whole estate
# universe of the system — every form, filter and seed record uses exactly
# these three names, and nothing else. (The old app had 212 estates; the
# documentation allows only these three.)
ESTATES = ["Langata", "Madaraka", "Nairobi West"]

# The eight trades a fundi can work in. These are the starting names; Admin
# can add, edit or delete categories later, but the system starts with
# exactly these.
CATEGORY_NAMES = [
    "Plumbing",
    "Electrical",
    "Carpentry",
    "Painting",
    "Cleaning",
    "Welding",
    "Mechanics",
    "Masonry",
]

# Time slots a customer can pick when booking. Fixed list, so the fundi
# always sees a tidy value instead of free text.
TIME_SLOTS = [
    "Morning (8:00 AM - 12:00 PM)",
    "Afternoon (12:00 PM - 4:00 PM)",
    "Evening (4:00 PM - 7:00 PM)",
]

# File types a fundi may upload (profile photo, ID document). Anything else
# is refused, so nobody can upload a script or an executable.
ALLOWED_UPLOAD_EXTENSIONS = {"png", "jpg", "jpeg"}

app = Flask(__name__)

# Secrets fail closed: the app refuses to start at all if SECRET_KEY is not
# set. There is deliberately NO fallback value here — a forgotten secret
# must be loud, not silently insecure. See .env.example for how to make one.
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set. Copy .env.example to .env and set SECRET_KEY "
        "(generate one with: python -c \"import secrets; print(secrets.token_hex(32))\")"
    )
app.config["SECRET_KEY"] = SECRET_KEY

basedir = os.path.abspath(os.path.dirname(__file__))
instance_dir = os.path.join(basedir, "instance")
os.makedirs(instance_dir, exist_ok=True)
# Uploaded photos and ID documents live here, created when the app starts.
upload_dir = os.path.join(instance_dir, "uploads")
os.makedirs(upload_dir, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL") or f"sqlite:///{os.path.join(instance_dir, 'fundimtaani.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Session hardening: the session cookie cannot be read by JavaScript, is
# only sent back to this site, and is only sent over HTTPS in production.
# A signed-in session lasts 4 hours.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("FLASK_ENV") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=4)
# No single request (form + uploads) may be bigger than 2 MB. This stops
# somebody filling the server's memory with a giant upload.
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

db.init_app(app)

# --- Simple rate limiting -------------------------------------------------
# A small in-memory sliding window: for each visitor + page + method we
# remember the recent request times, and refuse when there are too many.
# This is what stops somebody hammering the login or registration pages.
# (It lives in memory, so it resets when the app restarts — that is fine
# for a small site like this one.)
_RATE_LIMIT_STORE = defaultdict(list)
_RATE_LIMIT_LOCK = threading.Lock()


def check_rate_limit(key, max_requests, window_seconds):
    now = time.time()
    with _RATE_LIMIT_LOCK:
        timestamps = _RATE_LIMIT_STORE[key]
        _RATE_LIMIT_STORE[key] = [t for t in timestamps if now - t < window_seconds]
        if len(_RATE_LIMIT_STORE[key]) >= max_requests:
            return False
        _RATE_LIMIT_STORE[key].append(now)
        return True


def rate_limited(max_requests=10, window_seconds=60, methods=("POST",), error_message="Too many requests. Please slow down."):
    # A decorator: put @rate_limited(...) above a route and that route
    # refuses when one visitor posts to it too often.
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if methods is None or request.method in methods:
                client_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(",")[0].strip()
                key = f"{client_ip}:{request.endpoint}:{request.method}"
                if not check_rate_limit(key, max_requests, window_seconds):
                    abort(429, description=error_message)
            return f(*args, **kwargs)

        return wrapped

    return decorator


# --- CSRF protection ------------------------------------------------------
# Every form on the site carries a hidden token that only this server and
# this visitor's browser know. Before any POST is handled, the token in the
# form is compared with the one in the session; a mismatch means the
# request did not come from our own page, and it is refused. This stops
# other websites submitting forms on a visitor's behalf.

def generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]


def sanitize_input(text, max_len=1000):
    # Clean text typed into a form: strip it, drop invisible null bytes,
    # and cut it to a sane length. Note this does NOT escape HTML — the
    # templates escape everything when they display it (Jinja autoescape),
    # so escaping here too would store "&amp;" in the database and show
    # it doubled on the page.
    if not text:
        return ""
    text = text.replace("\x00", "").strip()
    return str(text)[:max_len]


@app.before_request
def csrf_protect():
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken")
        expected = session.get("_csrf_token")
        if not expected or not token or not hmac.compare_digest(str(token), str(expected)):
            abort(400, description="Security verification failed: Invalid or expired CSRF token.")


@app.after_request
def apply_security_headers(response):
    # A set of headers that tell the browser how strictly to treat this
    # site: only load scripts/styles from here (and the Bootstrap CDN),
    # never show the site inside another site's frame, never guess a
    # file's type, and never let the page use the camera, microphone or
    # payment features.
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "form-action 'self' https://wa.me; "
        "base-uri 'self'; "
        "object-src 'none';"
    )
    response.headers["Content-Security-Policy"] = csp
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# --- Friendly error pages -------------------------------------------------
# Instead of Flask's plain error text, show our own pages.

@app.errorhandler(400)
def error_bad_request(e):
    return render_template("errors/400.html", error_description=getattr(e, "description", None)), 400


@app.errorhandler(403)
def error_forbidden(e):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def error_not_found(e):
    return render_template("errors/404.html"), 404


@app.errorhandler(429)
def error_rate_limit(e):
    return render_template("errors/429.html", error_description=getattr(e, "description", None)), 429


@app.errorhandler(500)
def error_server(e):
    return render_template("errors/500.html"), 500


# --- Small helpers ---------------------------------------------------------

def slugify(text):
    # Turn a name into a web-safe slug: "Masonry" -> "masonry",
    # "Phone Repair" -> "phone-repair". Used for category links.
    text = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[-\s]+", "-", text) or "fundi"


def normalize_phone(raw):
    # Take a Kenyan phone number in any common shape — 0712 345 678,
    # +254712345678, 712345678 — and return it in the one shape the app
    # stores: "0712 345 678" plus a pretty spaced version. Returns None
    # when the number is not a valid Kenyan mobile (07XX or 01XX).
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("254"):
        digits = "0" + digits[3:]
    if len(digits) == 9 and digits.startswith(("1", "7")):
        digits = "0" + digits
    if not re.fullmatch(r"0(?:1|7)\d{8}", digits):
        return None
    pretty = f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    return {"raw": digits, "pretty": pretty}


def allowed_upload(filename):
    # True when the uploaded file's extension is one of the image types
    # we accept (png/jpg/jpeg). Everything else is refused.
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_UPLOAD_EXTENSIONS


def save_upload(file_storage, prefix):
    # Save one uploaded file under instance/uploads/ and return the file
    # name that was stored (or None when there was no file at all).
    # The name is a random id plus the original extension, so two fundis
    # can never overwrite each other's files and a nasty file name can
    # never escape the uploads folder.
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_upload(file_storage.filename):
        return None
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    stored_name = f"{prefix}_{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(upload_dir, stored_name))
    return stored_name


def get_current_user():
    # The customer who is signed in right now, or None when nobody is.
    # A suspended account counts as signed out: even if the session still
    # holds their id, they get nothing.
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = User.query.get(user_id)
    if not user or not user.is_active:
        return None
    return user


def get_current_admin():
    # The admin who is signed in right now, or None when nobody is.
    # A deactivated admin account counts as signed out.
    admin_id = session.get("admin_id")
    if not admin_id:
        return None
    admin = Admin.query.get(admin_id)
    if not admin or not admin.is_active:
        return None
    return admin


def login_required(view):
    # A decorator for customer pages: visitors who are not signed in are
    # sent to the login page, and brought back here afterwards.
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    # A decorator for admin pages: anyone who is not a signed-in admin is
    # sent to the admin login page.
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not get_current_admin():
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)

    return wrapped


def issue_code(user, purpose, send_fn):
    # Make a fresh 6-digit code for a customer and email it to them.
    # Any older unused code for the same job is cancelled first, so there
    # is only ever one live code per customer per job. Returns the code
    # and whether it really went out by email (False = demo mode, in which
    # case the calling page shows the code in a labelled demo box).
    old = VerificationCode.query.filter_by(
        user_id=user.id, purpose=purpose, used=False
    ).all()
    for record in old:
        record.used = True
    record, code = VerificationCode.new_code()
    record.user_id = user.id
    record.purpose = purpose
    db.session.add(record)
    db.session.commit()
    sent = send_fn(user, code)
    return code, sent


def check_code(user, purpose, entered):
    # Check a 6-digit code a customer typed in. The code must exist, be
    # for this job, be unused, be unexpired, and match the stored hash.
    # A good code is marked used straight away so it can never be tried
    # a second time. Returns True only when everything checks out.
    entered = (entered or "").strip()
    if not re.fullmatch(r"\d{6}", entered):
        return False
    record = (
        VerificationCode.query.filter_by(user_id=user.id, purpose=purpose, used=False)
        .order_by(VerificationCode.created_at.desc())
        .first()
    )
    if not record or record.is_expired:
        return False
    if not check_password_hash(record.code_hash, entered):
        return False
    record.used = True
    db.session.commit()
    return True


def log_user_in(user):
    # Sign a customer in: forget any previous session (so a shared
    # computer cannot carry the last person's login), then remember this
    # customer's id and make the session last.
    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    session["_csrf_token"] = secrets.token_hex(32)


def visible_workers_query():
    # The fundis the public is allowed to see: approved by Admin, and
    # belonging to an account that is not suspended. A fundi row with no
    # linked account (old demo data) is still shown.
    query = Worker.query.filter(Worker.status == Worker.STATUS_APPROVED)
    query = query.outerjoin(User, Worker.customer_id == User.id)
    query = query.filter(db.or_(User.id.is_(None), User.is_active.is_(True)))
    return query


@app.route("/favicon.ico")
def favicon():
    # The little logo the browser shows in the tab.
    return send_from_directory(
        os.path.join(app.root_path, "static", "img"),
        "favicon.svg",
        mimetype="image/svg+xml",
    )


@app.context_processor
def inject_globals():
    # Values every template can use without the route passing them:
    # the category list (for the nav dropdown), the three estates, the
    # signed-in customer and admin (for the nav bar), and the CSRF helper.
    categories = Category.query.order_by(Category.name).all()
    return {
        "all_categories": categories,
        "estates": ESTATES,
        "site_name": "FundiMtaani",
        "current_user": get_current_user(),
        "current_admin": get_current_admin(),
        "csrf_token": generate_csrf_token,
    }


# ==========================================================================
# PUBLIC PAGES — anyone can open these, no login needed (FR01-FR05)
# ==========================================================================

@app.route("/")
def index():
    # The home page: a search box, the eight trades, and the newest
    # approved fundis, plus a few headline numbers.
    recent = (
        visible_workers_query()
        .order_by(Worker.created_at.desc(), Worker.id.desc())
        .limit(6)
        .all()
    )
    stats = {
        "workers": visible_workers_query().count(),
        "reviews": Review.query.count(),
        "categories": Category.query.count(),
        "estates_count": len(ESTATES),
    }
    return render_template("index.html", recent=recent, stats=stats)


@app.route("/fundis")
def fundis():
    # The directory page (FR01, FR02, FR03). Visitors filter the approved
    # fundis by trade, estate, a text search, a minimum star rating,
    # availability, and a sort order. The rating work is done by the DATABASE, not by loading
    # every fundi into Python and sorting there: a small sub-query works
    # out each fundi's average rating and review count, and the main query
    # joins it and orders by it.
    category_slug = request.args.get("category", "").strip()
    estate = request.args.get("estate", "").strip()
    q = request.args.get("q", "").strip()
    sort_by = request.args.get("sort", "newest").strip()
    min_rating_raw = request.args.get("min_rating", "").strip()
    try:
        min_rating = int(min_rating_raw) if min_rating_raw else 0
    except ValueError:
        min_rating = 0
    if min_rating not in (0, 3, 4, 5):
        min_rating = 0
    # "Available only" — ticked means show only fundis who say they are
    # taking new jobs right now (FR03). Unticked shows every approved
    # fundi, available or not.
    available_only = request.args.get("available", "").strip() == "1"

    # Average rating + review count per fundi, worked out by the database.
    rating_sq = (
        db.session.query(
            Review.worker_id.label("worker_id"),
            db.func.avg(Review.rating).label("avg_rating"),
            db.func.count(Review.id).label("review_count"),
        )
        .group_by(Review.worker_id)
        .subquery()
    )

    query = visible_workers_query()
    query = query.outerjoin(rating_sq, rating_sq.c.worker_id == Worker.id)

    active_category = None
    if category_slug:
        active_category = Category.query.filter_by(slug=category_slug).first()
        if active_category:
            query = query.filter(Worker.category_id == active_category.id)
    if estate:
        # An estate value that is not one of the three matches nothing —
        # the filter is applied as given, so an invented estate returns
        # an empty directory rather than silently ignoring the filter.
        query = query.filter(Worker.estate == estate)
    if q:
        # The text search looks at the fundi's name, bio and rate — and at
        # the TRADE name too, so searching "plumbing" finds plumbers.
        like = f"%{q}%"
        query = query.join(Category, Worker.category_id == Category.id).filter(
            db.or_(
                Worker.name.ilike(like),
                Worker.bio.ilike(like),
                Worker.rate.ilike(like),
                Category.name.ilike(like),
            )
        )
    if min_rating:
        # Only fundis whose average rating reaches the chosen minimum.
        # A fundi with no reviews has no average, so they are left out.
        query = query.filter(rating_sq.c.avg_rating >= min_rating)
    if available_only:
        # Only fundis who say they are taking new jobs right now. The
        # flag is switched by the fundi on their own profile (FR17).
        query = query.filter(Worker.is_available.is_(True))

    # Sort in the database query, newest first by default.
    if sort_by == "rating":
        query = query.order_by(
            rating_sq.c.avg_rating.desc(), rating_sq.c.review_count.desc()
        )
    elif sort_by == "reviews":
        query = query.order_by(
            db.func.coalesce(rating_sq.c.review_count, 0).desc(),
            Worker.created_at.desc(),
        )
    else:
        sort_by = "newest"
        query = query.order_by(Worker.created_at.desc(), Worker.id.desc())

    workers = query.limit(150).all()
    return render_template(
        "workers.html",
        workers=workers,
        active_category=active_category,
        selected_estate=estate,
        q=q,
        sort_by=sort_by,
        min_rating=min_rating,
        available_only=available_only,
    )


@app.route("/fundi/<int:worker_id>")
@app.route("/fundi/<int:worker_id>-<slug>")
def worker_detail(worker_id, slug=None):
    # One fundi's profile page (FR04, FR05). Only approved fundis whose
    # owner is not suspended can be opened — anything else is a 404, so a
    # pending or suspended fundi can never be found by guessing the link.
    # The page shows the fundi's details, their reviews, the WhatsApp and
    # phone contact buttons, and the booking form (which itself is gated
    # behind login + verified email).
    worker = (
        visible_workers_query().filter(Worker.id == worker_id).first()
    )
    if not worker:
        abort(404)
    reviews = sorted(worker.reviews, key=lambda r: r.created_at, reverse=True)
    # If the signed-in customer has a completed booking with this fundi
    # that they have not reviewed yet, the page points them to My Bookings
    # where the review form lives (FR11).
    reviewable_booking = None
    user = get_current_user()
    if user:
        reviewable_booking = (
            Booking.query.filter_by(
                customer_id=user.id,
                worker_id=worker.id,
                status=Booking.STATUS_COMPLETED,
            )
            .outerjoin(Review, Review.booking_id == Booking.id)
            .filter(Review.id.is_(None))
            .order_by(Booking.created_at.desc())
            .first()
        )
    return render_template(
        "worker_detail.html",
        w=worker,
        reviews=reviews,
        breakdown=worker.rating_breakdown,
        booked=request.args.get("booked"),
        reviewable_booking=reviewable_booking,
    )


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    # Serve an uploaded file from instance/uploads/.
    # Profile photos are public (they are shown on the fundi's page).
    # ID documents are private: only a signed-in admin may open one.
    # (ID files are the ones whose stored name starts with "id_".)
    if filename.startswith("id_") and not get_current_admin():
        abort(403)
    return send_from_directory(upload_dir, filename)


# ==========================================================================
# CUSTOMER ACCOUNTS — register, verify, log in, log out (FR06-FR08)
# ==========================================================================

@app.route("/register", methods=["GET", "POST"])
@rate_limited(max_requests=10, window_seconds=3600, error_message="Too many registrations. Please wait before trying again.")
def register():
    # FR06 — create a customer account. All four fields are mandatory:
    # full name, email, phone and password (at least 6 characters).
    # Email and phone must both be unique. The phone is stored only and
    # never verified. After the account is created, a 6-digit code is
    # emailed and the customer is sent to the verify page (FR07).
    form = {"full_name": "", "email": "", "phone": ""}
    if request.method == "POST":
        if request.form.get("website"):
            # Honeypot field: real people leave it empty; spam bots fill
            # it in. A filled honeypot is quietly treated as a success so
            # the bot learns nothing.
            return render_template("register.html", form=form)
        form["full_name"] = sanitize_input(request.form.get("full_name", ""), max_len=120)
        form["email"] = request.form.get("email", "").strip().lower()
        phone = normalize_phone(request.form.get("phone", ""))
        password = request.form.get("password", "")

        errors = []
        if len(form["full_name"]) < 3:
            errors.append("Please enter your full name.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", form["email"]):
            errors.append("Please enter a valid email address.")
        if not phone:
            errors.append("Please enter a valid Kenyan phone number (e.g. 0712 345 678).")
        if len(password) < 6:
            errors.append("Your password must be at least 6 characters.")
        if not errors and User.query.filter_by(email=form["email"]).first():
            errors.append("An account with that email already exists. Try logging in.")
        if not errors and User.query.filter_by(phone=phone["pretty"]).first():
            errors.append("An account with that phone number already exists. Try logging in.")

        if errors:
            for err in errors:
                flash(err, "warning")
            return render_template("register.html", form=form)

        user = User(
            full_name=form["full_name"],
            email=form["email"],
            phone=phone["pretty"],
            password_hash=generate_password_hash(password, method="pbkdf2:sha256"),
        )
        db.session.add(user)
        db.session.commit()
        # Email the verification code (FR07). In demo mode (no mail
        # server) the code is kept in the session so the verify page can
        # show it in a labelled demo box.
        code, sent = issue_code(user, "verify", send_verification_code)
        if not sent:
            session["demo_verify_code"] = code
        flash("Account created. Enter the 6-digit code we emailed you to activate it.", "success")
        return redirect(url_for("verify", email=user.email))

    return render_template("register.html", form=form)


@app.route("/verify", methods=["GET", "POST"])
@rate_limited(max_requests=10, window_seconds=3600, error_message="Too many verification attempts. Please wait before trying again.")
def verify():
    # FR07 — check the 6-digit code emailed after registration. A correct
    # code marks the email verified, signs the customer in, and sends them
    # on their way. A wrong, used or expired code is refused.
    email = (request.args.get("email") or request.form.get("email") or "").strip().lower()
    user = User.query.filter_by(email=email).first() if email else None
    if not user:
        flash("We could not find that account. Please register first.", "warning")
        return redirect(url_for("register"))
    if user.is_email_verified:
        flash("Your email is already verified. You can log in.", "info")
        return redirect(url_for("login"))

    # The demo code is shown once, then forgotten, so refreshing the page
    # does not keep flashing it around.
    demo_code = session.pop("demo_verify_code", None)

    if request.method == "POST":
        code = request.form.get("code", "")
        if check_code(user, "verify", code):
            user.is_email_verified = True
            db.session.commit()
            log_user_in(user)
            flash("Your email is verified — welcome to FundiMtaani!", "success")
            return redirect(request.args.get("next") or url_for("index"))
        flash("That code is wrong or has expired. Check your email and try again.", "danger")
        return render_template("verify.html", email=email, demo_code=None)

    return render_template("verify.html", email=email, demo_code=demo_code)


@app.route("/login", methods=["GET", "POST"])
@rate_limited(max_requests=10, window_seconds=3600, error_message="Too many login attempts. Please wait before trying again.")
def login():
    # FR08 — one Login page, password branch. The customer types their
    # email and password; a matching hashed password signs them in.
    # (The email-code branch lives on /login/request-code below.)
    # Suspended accounts (is_active False) cannot log in at all.
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.is_active and check_password_hash(user.password_hash, password):
            log_user_in(user)
            flash(f"Welcome back, {user.full_name.split()[0]}.", "success")
            return redirect(request.args.get("next") or url_for("index"))
        if user and not user.is_active:
            flash("This account has been suspended. Contact the FundiMtaani admin.", "danger")
        else:
            flash("Wrong email or password. Please try again.", "danger")
    return render_template("login.html")


@app.route("/login/request-code", methods=["POST"])
@rate_limited(max_requests=5, window_seconds=3600, error_message="Too many code requests. Please wait before trying again.")
def login_request_code():
    # FR08 — the email-code branch of Login, step one: the customer gives
    # their email, a 6-digit login code is emailed to them, and they are
    # sent to the code entry page. The reply never says whether the email
    # exists or not, so the page cannot be used to probe for accounts.
    email = request.form.get("email", "").strip().lower()
    user = User.query.filter_by(email=email).first()
    if user and user.is_active:
        code, sent = issue_code(user, "login", send_login_code)
        if not sent:
            session["demo_login_code"] = code
    flash("If that email belongs to an account, a login code is on its way.", "info")
    return redirect(url_for("login_code", email=email))


@app.route("/login/code", methods=["GET", "POST"])
@rate_limited(max_requests=10, window_seconds=3600, error_message="Too many login attempts. Please wait before trying again.")
def login_code():
    # FR08 — the email-code branch of Login, step two: the customer types
    # the 6-digit code from their email. A correct code signs them in
    # without a password.
    email = (request.args.get("email") or request.form.get("email") or "").strip().lower()
    user = User.query.filter_by(email=email).first() if email else None
    if not user:
        flash("Please request a login code first.", "warning")
        return redirect(url_for("login"))

    demo_code = session.pop("demo_login_code", None)

    if request.method == "POST":
        if not user.is_active:
            flash("This account has been suspended. Contact the FundiMtaani admin.", "danger")
            return render_template("login_code.html", email=email, demo_code=None)
        if check_code(user, "login", request.form.get("code", "")):
            log_user_in(user)
            flash(f"Welcome back, {user.full_name.split()[0]}.", "success")
            return redirect(request.args.get("next") or url_for("index"))
        flash("That code is wrong or has expired. Request a new one and try again.", "danger")
        return render_template("login_code.html", email=email, demo_code=None)

    return render_template("login_code.html", email=email, demo_code=demo_code)


@app.route("/logout")
def logout():
    # Sign the customer (or admin) out: forget the whole session.
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("index"))


# ==========================================================================
# FUNDI APPLICATION + PROFILE (FR12, FR17)
# ==========================================================================

def _join_form_values(form, worker=None):
    # Collect the fundi application fields from a submitted form, falling
    # back to an existing (draft) profile's values when the form is empty.
    # Used to refill the form when something fails validation.
    values = {
        "category_id": form.get("category_id", ""),
        "estate": form.get("estate", ""),
        "rate": form.get("rate", ""),
        "experience": form.get("experience", ""),
        "bio": form.get("bio", ""),
        "national_id": form.get("national_id", ""),
    }
    if worker:
        for key in values:
            if not values[key]:
                values[key] = getattr(worker, key, "") or ""
    # The template compares category_id with the category id as a string,
    # so always hand it a string, whichever path filled it in.
    values["category_id"] = str(values["category_id"] or "")
    return values


@app.route("/join", methods=["GET", "POST"])
@login_required
@rate_limited(max_requests=10, window_seconds=3600, error_message="Too many submissions. Please wait before trying again.")
def join():
    # FR12 — apply to become a fundi. Only a signed-in customer can apply,
    # and the application is linked to their account, so one account can
    # only ever have one fundi profile.
    #
    # The form asks for: trade (one of the 8), estate (one of the 3),
    # experience, rate, bio, a profile photo, and a National ID / passport
    # number plus a photo of the ID document. Admin checks the ID before
    # approving (FR19).
    #
    # Two buttons: "Save draft" stores what was typed so far (an unfinished
    # application can be picked up again later), and "Submit application"
    # sends it to Admin as Pending. A submitted application is hidden from
    # the directory until Admin approves it.
    user = get_current_user()
    worker = user.fundi_profile

    # If this account already has a live application, show its status
    # instead of a blank form — except for a draft, which opens the form
    # again so it can be finished. A rejected applicant gets the form
    # prefilled with their old answers, so they can fix and resubmit.
    if worker and worker.status != Worker.STATUS_DRAFT and request.method == "GET":
        form = (
            _join_form_values({}, worker)
            if worker.status == Worker.STATUS_REJECTED
            else None
        )
        return render_template("join.html", worker=worker, form=form)

    if request.method == "POST":
        # A pending application is with Admin and an approved one is live:
        # neither can be changed through this form. (A rejected one can be
        # fixed and sent again; a draft can be finished.) Approved fundis
        # edit their profile on /fundi/edit instead.
        if worker and worker.status in (Worker.STATUS_PENDING, Worker.STATUS_APPROVED):
            flash("Your application is already submitted. See its status below.", "info")
            return redirect(url_for("join"))
        if request.form.get("website"):
            return render_template("join_success.html", worker=worker)
        action = request.form.get("action", "submit")
        values = _join_form_values(request.form, worker)
        category = (
            db.session.get(Category, int(values["category_id"]))
            if values["category_id"].isdigit()
            else None
        )

        errors = []
        if action == "submit":
            # A submitted application must be complete: Admin cannot check
            # an application with no trade, no estate and no ID.
            if not category:
                errors.append("Choose your trade.")
            if values["estate"] not in ESTATES:
                errors.append("Choose the estate you work in.")
            if not values["national_id"]:
                errors.append("Enter your National ID or passport number.")
            if not worker or not worker.id_document_filename:
                if not request.files.get("id_document") or not request.files.get("id_document").filename:
                    errors.append("Upload a photo or scan of your ID document.")
            if not worker or not worker.photo_filename:
                if not request.files.get("photo") or not request.files.get("photo").filename:
                    errors.append("Upload a profile photo.")
        else:
            # A draft only needs to make sense where something was typed.
            if values["category_id"] and not category:
                errors.append("Choose a valid trade.")
            if values["estate"] and values["estate"] not in ESTATES:
                errors.append("Choose one of the three estates.")

        # Refuse files that are not images, before anything is saved.
        photo_file = request.files.get("photo")
        id_file = request.files.get("id_document")
        if photo_file and photo_file.filename and not allowed_upload(photo_file.filename):
            errors.append("The profile photo must be a PNG or JPG image.")
        if id_file and id_file.filename and not allowed_upload(id_file.filename):
            errors.append("The ID document must be a PNG or JPG image.")

        if errors:
            for err in errors:
                flash(err, "warning")
            return render_template("join.html", worker=None, form=values)

        # Save any uploaded files first, so the profile can point at them.
        photo_name = save_upload(photo_file, "photo") if photo_file else None
        id_name = save_upload(id_file, "id") if id_file else None

        if worker:
            # An existing draft (or a rejected application being fixed)
            # is updated in place — never duplicated.
            worker.category_id = category.id if category else worker.category_id
            worker.estate = values["estate"] or worker.estate
            worker.rate = sanitize_input(values["rate"], max_len=80)
            worker.experience = sanitize_input(values["experience"], max_len=40)
            worker.bio = sanitize_input(values["bio"], max_len=2000)
            worker.national_id = sanitize_input(values["national_id"], max_len=40)
            if photo_name:
                worker.photo_filename = photo_name
            if id_name:
                worker.id_document_filename = id_name
        else:
            worker = Worker(
                customer_id=user.id,
                name=user.full_name,
                phone=user.phone,
                category_id=category.id if category else None,
                estate=values["estate"] or "",
                rate=sanitize_input(values["rate"], max_len=80),
                experience=sanitize_input(values["experience"], max_len=40),
                bio=sanitize_input(values["bio"], max_len=2000),
                national_id=sanitize_input(values["national_id"], max_len=40),
                photo_filename=photo_name or "",
                id_document_filename=id_name or "",
            )
            db.session.add(worker)

        if action == "draft":
            worker.status = Worker.STATUS_DRAFT
            db.session.commit()
            flash("Draft saved. You can come back and finish it any time.", "success")
            return redirect(url_for("join"))

        # Submitting (again, after a rejection) sends the profile to Admin
        # as Pending and clears the old rejection reason.
        worker.status = Worker.STATUS_PENDING
        worker.rejection_reason = ""
        db.session.commit()
        return render_template("join_success.html", worker=worker)

    # GET: a draft opens prefilled; a brand-new applicant gets an empty form.
    form = _join_form_values({}, worker) if worker else {
        "category_id": "", "estate": "", "rate": "",
        "experience": "", "bio": "", "national_id": "",
    }
    return render_template("join.html", worker=None, form=form)


@app.route("/fundi/edit", methods=["GET", "POST"])
@login_required
def edit_fundi_profile():
    # FR17 — a fundi updates their own profile: rate, estate, bio,
    # experience, photo, and whether they are available for new jobs
    # (the "Available only" filter in FR03 reads that flag). Only the
    # owner of the profile can open this page, and only their own profile
    # is ever changed. Editing does not change the approval status: an
    # approved fundi stays approved.
    user = get_current_user()
    worker = user.fundi_profile
    if not worker or worker.status == Worker.STATUS_DRAFT:
        flash("Apply as a fundi first, then you can edit your profile.", "warning")
        return redirect(url_for("join"))

    if request.method == "POST":
        estate = request.form.get("estate", "").strip()
        photo_file = request.files.get("photo")
        errors = []
        if estate not in ESTATES:
            errors.append("Choose one of the three estates.")
        if photo_file and photo_file.filename and not allowed_upload(photo_file.filename):
            errors.append("The profile photo must be a PNG or JPG image.")
        if errors:
            for err in errors:
                flash(err, "warning")
            return render_template("my_profile.html", worker=worker)

        photo_name = save_upload(photo_file, "photo") if photo_file else None
        worker.estate = estate
        # A checkbox only sends a value when it is ticked, so "not in the
        # form" means the fundi unticked it: not available for new jobs.
        worker.is_available = request.form.get("is_available") == "1"
        worker.rate = sanitize_input(request.form.get("rate", ""), max_len=80)
        worker.experience = sanitize_input(request.form.get("experience", ""), max_len=40)
        worker.bio = sanitize_input(request.form.get("bio", ""), max_len=2000)
        if photo_name:
            worker.photo_filename = photo_name
        db.session.commit()
        flash("Your profile has been updated.", "success")
        return redirect(url_for("worker_detail", worker_id=worker.id))

    return render_template("my_profile.html", worker=worker)


# ==========================================================================
# BOOKINGS — the job lifecycle (FR09, FR10, FR13-FR16) and REVIEWS (FR11)
# ==========================================================================

@app.route("/fundi/<int:worker_id>/book", methods=["POST"])
@login_required
@rate_limited(max_requests=10, window_seconds=3600, error_message="Too many booking requests. Please wait before submitting another.")
def book_worker(worker_id):
    # FR09 — book a chosen fundi. The customer must be signed in AND have
    # a verified email; anyone else is sent to log in or verify first.
    # The booking is created as Pending, and the fundi is notified by
    # email. The name and phone are NOT typed in — they come from the
    # customer's account, so a booking can never pretend to be somebody else.
    user = get_current_user()
    worker = visible_workers_query().filter(Worker.id == worker_id).first()
    if not worker:
        abort(404)
    if not user.is_email_verified:
        flash("Verify your email first — check your inbox for the code.", "warning")
        return redirect(url_for("verify", email=user.email))
    if request.form.get("website"):
        return redirect(url_for("worker_detail", worker_id=worker.id))

    estate = request.form.get("estate", "").strip()
    preferred_date = sanitize_input(request.form.get("preferred_date", ""), max_len=40)
    preferred_slot = request.form.get("preferred_slot", TIME_SLOTS[0])
    message = sanitize_input(request.form.get("message", ""), max_len=1000)

    if estate not in ESTATES:
        flash("Choose the estate where the job is.", "warning")
        return redirect(url_for("worker_detail", worker_id=worker.id))
    if preferred_slot not in TIME_SLOTS:
        preferred_slot = TIME_SLOTS[0]

    booking = Booking(
        worker_id=worker.id,
        customer_id=user.id,
        estate=estate,
        preferred_date=preferred_date,
        preferred_slot=preferred_slot,
        message=message,
        status=Booking.STATUS_PENDING,
    )
    db.session.add(booking)
    db.session.commit()
    # Let the fundi know. If the email cannot be sent, the booking still
    # stands — the database record is the truth, not the email.
    send_booking_created(booking)
    flash(f"Booking sent to {worker.name}. They will accept or decline it.", "success")
    return redirect(url_for("worker_detail", worker_id=worker.id, booked=1))


@app.route("/bookings")
@login_required
def my_bookings():
    # FR10 + FR11 — "My Bookings": everything this customer has booked,
    # newest first. From here the customer confirms a finished job
    # (Job Done -> Completed) and writes a review on a completed booking.
    user = get_current_user()
    bookings = (
        Booking.query.filter_by(customer_id=user.id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    return render_template("bookings.html", bookings=bookings)


@app.route("/booking/<int:booking_id>/confirm", methods=["POST"])
@login_required
def confirm_booking(booking_id):
    # FR10 — the CUSTOMER confirms a job is complete. Only the customer
    # who made the booking may do this, and only when the booking is in
    # the "Job Done" state (the fundi has said the work is finished).
    # Any other state, or anyone else, is refused and nothing changes.
    user = get_current_user()
    booking = Booking.query.get_or_404(booking_id)
    if booking.customer_id != user.id:
        abort(403)
    if booking.status != Booking.STATUS_JOB_DONE:
        flash("Only a job marked done by the fundi can be confirmed.", "warning")
        return redirect(url_for("my_bookings"))
    booking.status = Booking.STATUS_COMPLETED
    db.session.commit()
    flash("Job confirmed as complete. You can now leave a review.", "success")
    return redirect(url_for("my_bookings"))


@app.route("/booking/<int:booking_id>/review", methods=["POST"])
@login_required
@rate_limited(max_requests=5, window_seconds=3600, error_message="Too many reviews submitted. Please wait before submitting another.")
def add_review(booking_id):
    # FR11 — review a fundi. The rules are checked on the server, in
    # order: the booking must belong to this customer, it must be
    # Completed, and it must not already have a review. (The database
    # also enforces "one review per booking" with a UNIQUE on
    # Review.booking_id, so even a double-click cannot create two.)
    # The review is then shown on the fundi's profile under the
    # customer's name. There is no anonymous review form anywhere.
    user = get_current_user()
    booking = Booking.query.get_or_404(booking_id)
    if booking.customer_id != user.id:
        abort(403)
    if booking.status != Booking.STATUS_COMPLETED:
        flash("You can only review a booking once the job is completed.", "warning")
        return redirect(url_for("my_bookings"))
    if booking.review is not None:
        flash("You have already reviewed this booking.", "warning")
        return redirect(url_for("my_bookings"))
    if request.form.get("website"):
        return redirect(url_for("my_bookings"))

    comment = sanitize_input(request.form.get("comment", ""), max_len=1000)
    try:
        rating = max(1, min(5, int(request.form.get("rating", "5"))))
    except ValueError:
        rating = 5
    db.session.add(
        Review(
            worker_id=booking.worker_id,
            booking_id=booking.id,
            customer_id=user.id,
            rating=rating,
            comment=comment,
        )
    )
    db.session.commit()
    flash("Thank you! Your review has been published.", "success")
    return redirect(url_for("worker_detail", worker_id=booking.worker_id))


@app.route("/my-jobs")
@login_required
def my_jobs():
    # FR13 — "My Jobs": the bookings made for this customer's fundi
    # profile. A customer with no fundi profile is sent to apply first.
    # Only their own bookings are ever listed here.
    user = get_current_user()
    worker = user.fundi_profile
    if not worker:
        flash("Apply as a fundi first to see your jobs.", "warning")
        return redirect(url_for("join"))
    bookings = (
        Booking.query.filter_by(worker_id=worker.id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    return render_template("my_jobs.html", worker=worker, bookings=bookings)


def _fundi_booking_or_403(booking_id, user):
    # Helper for the three fundi actions below: find the booking, and
    # make sure it belongs to a fundi profile owned by this customer.
    # Anything else is a 403 — a fundi can never touch another fundi's jobs.
    booking = Booking.query.get_or_404(booking_id)
    worker = user.fundi_profile
    if not worker or booking.worker_id != worker.id:
        abort(403)
    return booking


@app.route("/booking/<int:booking_id>/accept", methods=["POST"])
@login_required
def accept_booking(booking_id):
    # FR14 — the fundi accepts a booking: Pending -> Accepted. Only a
    # booking that is still Pending can be accepted; anything else is
    # refused with no change. The customer is notified by email.
    user = get_current_user()
    booking = _fundi_booking_or_403(booking_id, user)
    if booking.status != Booking.STATUS_PENDING:
        flash("Only a pending booking can be accepted.", "warning")
        return redirect(url_for("my_jobs"))
    booking.status = Booking.STATUS_ACCEPTED
    db.session.commit()
    send_booking_status(booking, "accepted")
    flash(f"Booking #{booking.id} accepted.", "success")
    return redirect(url_for("my_jobs"))


@app.route("/booking/<int:booking_id>/decline", methods=["POST"])
@login_required
def decline_booking(booking_id):
    # FR15 — the fundi declines a booking: Pending -> Declined. Same
    # guard as accepting: only a Pending booking can be declined, and the
    # customer is notified by email.
    user = get_current_user()
    booking = _fundi_booking_or_403(booking_id, user)
    if booking.status != Booking.STATUS_PENDING:
        flash("Only a pending booking can be declined.", "warning")
        return redirect(url_for("my_jobs"))
    booking.status = Booking.STATUS_DECLINED
    db.session.commit()
    send_booking_status(booking, "declined")
    flash(f"Booking #{booking.id} declined.", "info")
    return redirect(url_for("my_jobs"))


@app.route("/booking/<int:booking_id>/job-done", methods=["POST"])
@login_required
def mark_job_done(booking_id):
    # FR16 — the fundi marks a job done: Accepted -> Job Done. Only an
    # accepted booking can be marked done, and the customer is notified
    # and asked to confirm completion (FR10). The fundi can never mark
    # their own job complete — that is the customer's step.
    user = get_current_user()
    booking = _fundi_booking_or_403(booking_id, user)
    if booking.status != Booking.STATUS_ACCEPTED:
        flash("Only an accepted booking can be marked as done.", "warning")
        return redirect(url_for("my_jobs"))
    booking.status = Booking.STATUS_JOB_DONE
    db.session.commit()
    send_booking_status(booking, "job_done")
    flash(f"Booking #{booking.id} marked as job done. The customer will confirm it.", "success")
    return redirect(url_for("my_jobs"))


# ==========================================================================
# ADMIN — FR18-FR27. Everything here needs a signed-in admin.
# ==========================================================================

@app.route("/admin/login", methods=["GET", "POST"])
@rate_limited(max_requests=3, window_seconds=3600, methods=("POST",), error_message="Security Alert: Maximum 3 login attempts per hour exceeded. Access temporarily restricted for 1 hour.")
def admin_login():
    # FR18 — admin login. The email and password are checked against the
    # Admin table, and the password is compared as a HASH, never as plain
    # text. There is no shared admin password and no fallback password:
    # if no admin account exists, nobody can log in. Admin sessions are
    # separate from customer sessions.
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        admin = Admin.query.filter_by(email=email).first()
        if admin and admin.is_active and check_password_hash(admin.password_hash, password):
            session.clear()
            session["admin_id"] = admin.id
            session.permanent = True
            session["_csrf_token"] = secrets.token_hex(32)
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect admin email or password.", "danger")
    return render_template("admin/login.html")


@app.route("/admin/logout")
def admin_logout():
    # Sign the admin out and go back to the home page.
    session.clear()
    flash("You have been signed out from the admin portal.", "info")
    return redirect(url_for("index"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    # The admin home page: pending applications (with the ID check),
    # live fundis, bookings (read-only — Admin does NOT drive bookings),
    # and reviews. Headline counts sit at the top.
    pending = (
        Worker.query.filter_by(status=Worker.STATUS_PENDING)
        .order_by(Worker.created_at.desc())
        .all()
    )
    approved_workers = (
        Worker.query.filter_by(status=Worker.STATUS_APPROVED)
        .order_by(Worker.created_at.desc())
        .all()
    )
    bookings = Booking.query.order_by(Booking.created_at.desc()).limit(100).all()
    reviews = Review.query.order_by(Review.created_at.desc()).limit(50).all()
    counts = {
        "approved": len(approved_workers),
        "pending": len(pending),
        "customers": User.query.count(),
        "bookings_total": Booking.query.count(),
        "bookings_pending": Booking.query.filter_by(status=Booking.STATUS_PENDING).count(),
        "reviews_total": Review.query.count(),
    }
    return render_template(
        "admin/dashboard.html",
        pending=pending,
        approved_workers=approved_workers,
        bookings=bookings,
        reviews=reviews,
        counts=counts,
    )


@app.route("/admin/worker/<int:worker_id>/approve", methods=["POST"])
@admin_required
def admin_worker_approve(worker_id):
    # FR19 — approve a fundi application. The fundi's ID document and
    # number are shown on the dashboard so Admin can actually check them
    # before approving. Approval makes the profile live in the directory,
    # and the fundi is notified by email.
    worker = Worker.query.get_or_404(worker_id)
    if worker.status != Worker.STATUS_PENDING:
        flash("Only a pending application can be approved.", "warning")
        return redirect(url_for("admin_dashboard"))
    worker.status = Worker.STATUS_APPROVED
    worker.rejection_reason = ""
    db.session.commit()
    send_application_decision(worker, approved=True)
    flash(f"{worker.name} has been approved and is now live.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/worker/<int:worker_id>/reject", methods=["POST"])
@admin_required
def admin_worker_reject(worker_id):
    # FR20 — reject a fundi application. Admin must write a reason: it is
    # stored on the profile, shown to the fundi, and emailed to them, so
    # the fundi knows what to fix before applying again.
    worker = Worker.query.get_or_404(worker_id)
    if worker.status != Worker.STATUS_PENDING:
        flash("Only a pending application can be rejected.", "warning")
        return redirect(url_for("admin_dashboard"))
    reason = sanitize_input(request.form.get("reason", ""), max_len=1000)
    if not reason:
        flash("Please write a reason for the rejection — the fundi needs to know what to fix.", "warning")
        return redirect(url_for("admin_dashboard"))
    worker.status = Worker.STATUS_REJECTED
    worker.rejection_reason = reason
    db.session.commit()
    send_application_decision(worker, approved=False, reason=reason)
    flash(f"{worker.name}'s application was rejected.", "secondary")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/customers")
@admin_required
def admin_customers():
    # FR21/FR22 — the customers page: every customer account, with its
    # email, phone, verification state, fundi profile (if any) and whether
    # the account is active. Suspend and Reactivate live here.
    customers = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin/customers.html", customers=customers)


@app.route("/admin/customer/<int:user_id>/suspend", methods=["POST"])
@admin_required
def admin_customer_suspend(user_id):
    # FR21 — suspend a customer: flip is_active off. The account cannot
    # log in any more, and if they have a fundi profile it disappears
    # from search and browse. Nothing is deleted — suspension is meant
    # to be reversible.
    user = User.query.get_or_404(user_id)
    user.is_active = False
    db.session.commit()
    flash(f"{user.full_name}'s account has been suspended.", "warning")
    return redirect(url_for("admin_customers"))


@app.route("/admin/customer/<int:user_id>/reactivate", methods=["POST"])
@admin_required
def admin_customer_reactivate(user_id):
    # FR22 — reactivate a suspended customer: flip is_active back on.
    # The account can log in again, and their fundi profile (if any)
    # returns to search and browse.
    user = User.query.get_or_404(user_id)
    user.is_active = True
    db.session.commit()
    flash(f"{user.full_name}'s account has been reactivated.", "success")
    return redirect(url_for("admin_customers"))


@app.route("/admin/categories", methods=["GET", "POST"])
@admin_required
def admin_categories():
    # FR23 — the categories page: the list of trades, plus the form to add
    # a new one. A new name must not be empty and must not already exist.
    if request.method == "POST":
        name = sanitize_input(request.form.get("name", ""), max_len=80)
        if not name:
            flash("Enter a category name.", "warning")
        elif Category.query.filter_by(name=name).first():
            flash(f"A category called '{name}' already exists.", "warning")
        else:
            slug = slugify(name)
            if Category.query.filter_by(slug=slug).first():
                slug = f"{slug}-{secrets.token_hex(2)}"
            db.session.add(Category(name=name, slug=slug))
            db.session.commit()
            flash(f"Category '{name}' added.", "success")
        return redirect(url_for("admin_categories"))
    categories = Category.query.order_by(Category.name).all()
    return render_template("admin/categories.html", categories=categories)


@app.route("/admin/category/<int:category_id>/edit", methods=["POST"])
@admin_required
def admin_category_edit(category_id):
    # FR24 — rename a category. The new name must not be empty and must
    # not clash with another category's name. The slug is rebuilt from
    # the new name so directory links keep working.
    category = Category.query.get_or_404(category_id)
    name = sanitize_input(request.form.get("name", ""), max_len=80)
    if not name:
        flash("Enter a category name.", "warning")
    elif Category.query.filter(Category.id != category.id, Category.name == name).first():
        flash(f"A category called '{name}' already exists.", "warning")
    else:
        category.name = name
        slug = slugify(name)
        clash = Category.query.filter(
            Category.id != category.id, Category.slug == slug
        ).first()
        if clash:
            slug = f"{slug}-{secrets.token_hex(2)}"
        category.slug = slug
        db.session.commit()
        flash(f"Category renamed to '{name}'.", "success")
    return redirect(url_for("admin_categories"))


@app.route("/admin/category/<int:category_id>/delete", methods=["POST"])
@admin_required
def admin_category_delete(category_id):
    # FR25 — delete a category. This is blocked, with a clear message,
    # while any fundi still uses the category — deleting it would leave
    # those fundis pointing at a trade that no longer exists.
    category = Category.query.get_or_404(category_id)
    if category.workers:
        flash(
            f"Cannot delete '{category.name}': {len(category.workers)} fundi(s) "
            "still use it. Move those fundis to another trade first.",
            "danger",
        )
        return redirect(url_for("admin_categories"))
    db.session.delete(category)
    db.session.commit()
    flash(f"Category '{category.name}' deleted.", "secondary")
    return redirect(url_for("admin_categories"))


@app.route("/admin/reports")
@admin_required
def admin_reports():
    # FR27 — the reports page: counts and breakdowns, not raw rows.
    # Customers (total / verified / suspended), fundis by trade and by
    # estate, bookings by status, reviews, and the average rating overall.
    # The two CSV exports are linked from here as well, as export
    # functions of the reports — they are not the reports themselves.
    customers_total = User.query.count()
    customers_verified = User.query.filter_by(is_email_verified=True).count()
    customers_suspended = User.query.filter_by(is_active=False).count()

    # Fundis by trade: one row per trade, counting approved fundis.
    # The "approved" condition sits in the JOIN, not the WHERE, so a
    # trade with no approved fundis yet still shows up with a zero
    # instead of vanishing from the report.
    fundis_by_trade = (
        db.session.query(Category.name, db.func.count(Worker.id))
        .outerjoin(
            Worker,
            db.and_(
                Worker.category_id == Category.id,
                Worker.status == Worker.STATUS_APPROVED,
            ),
        )
        .group_by(Category.name)
        .order_by(Category.name)
        .all()
    )
    # Fundis by estate: one row per estate, counting approved fundis.
    fundis_by_estate = (
        db.session.query(Worker.estate, db.func.count(Worker.id))
        .filter(Worker.status == Worker.STATUS_APPROVED)
        .group_by(Worker.estate)
        .order_by(Worker.estate)
        .all()
    )
    # Bookings by status: one row per status value.
    bookings_by_status = (
        db.session.query(Booking.status, db.func.count(Booking.id))
        .group_by(Booking.status)
        .all()
    )
    bookings_by_status = {status: count for status, count in bookings_by_status}

    reviews_total = Review.query.count()
    avg_rating = db.session.query(db.func.avg(Review.rating)).scalar()
    # The five best-rated fundis with at least one review.
    top_fundis = (
        db.session.query(
            Worker, db.func.avg(Review.rating), db.func.count(Review.id)
        )
        .join(Review, Review.worker_id == Worker.id)
        .filter(Worker.status == Worker.STATUS_APPROVED)
        .group_by(Worker.id)
        .order_by(db.func.avg(Review.rating).desc())
        .limit(5)
        .all()
    )
    return render_template(
        "admin/reports.html",
        customers_total=customers_total,
        customers_verified=customers_verified,
        customers_suspended=customers_suspended,
        fundis_by_trade=fundis_by_trade,
        fundis_by_estate=fundis_by_estate,
        bookings_by_status=bookings_by_status,
        bookings_total=Booking.query.count(),
        reviews_total=reviews_total,
        avg_rating=avg_rating,
        top_fundis=top_fundis,
    )


@app.route("/admin/review/<int:review_id>/delete", methods=["POST"])
@admin_required
def admin_review_delete(review_id):
    # FR26 — remove a review. The fundi's average rating is computed from
    # the live reviews (see Worker.avg_rating in models.py), so deleting
    # the row recalculates the rating by itself — there is no stored
    # rating to update.
    review = Review.query.get_or_404(review_id)
    worker_id = review.worker_id
    db.session.delete(review)
    db.session.commit()
    flash("Review removed. The fundi's rating has been recalculated.", "secondary")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/export/bookings")
@admin_required
def export_bookings():
    # CSV export of all bookings, linked from the reports page. This is
    # an export function of the reports (FR27) — the reports page itself
    # is the aggregated view the FR asks for.
    bookings = Booking.query.order_by(Booking.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Customer Name", "Customer Email", "Customer Phone", "Fundi Name", "Fundi Phone", "Estate", "Preferred Date", "Time Slot", "Status", "Created At", "Message"])
    for b in bookings:
        writer.writerow([
            b.id,
            b.customer.full_name if b.customer else "N/A",
            b.customer.email if b.customer else "N/A",
            b.customer.phone if b.customer else "N/A",
            b.worker.name if b.worker else "N/A",
            b.worker.phone if b.worker else "N/A",
            b.estate,
            b.preferred_date,
            b.preferred_slot,
            b.status,
            b.created_at.strftime("%Y-%m-%d %H:%M:%S") if b.created_at else "",
            b.message,
        ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=fundimtaani_bookings.csv"},
    )


@app.route("/admin/export/workers")
@admin_required
def export_workers():
    # CSV export of all fundi profiles, linked from the reports page.
    workers = Worker.query.order_by(Worker.created_at.desc()).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Trade", "Estate", "Phone", "Rate", "Experience", "Status", "Owner Email", "Created At"])
    for w in workers:
        writer.writerow([
            w.id,
            w.name,
            w.category.name if w.category else "N/A",
            w.estate,
            w.phone,
            w.rate,
            w.experience,
            w.status,
            w.owner.email if w.owner else "N/A",
            w.created_at.strftime("%Y-%m-%d %H:%M:%S") if w.created_at else "",
        ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=fundimtaani_workers.csv"},
    )


# Create the tables the first time the app runs, if they do not exist yet.
# (seed.py fills them afterwards.)
with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(debug=True)
