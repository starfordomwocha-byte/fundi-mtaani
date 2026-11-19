# seed.py — fill a fresh database with the starting data for FundiMtaani.
#
# What it puts in:
#   * The 8 documented trades (Plumbing, Electrical, Carpentry, Painting,
#     Cleaning, Welding, Mechanics, Masonry).
#   * The 10 fundis from the Checkpoint 1 prototype, each linked to their
#     own verified customer account, each in Langata, Madaraka or
#     Nairobi West — the only three estates the system covers.
#   * One extra Pending application, so Admin has something to approve
#     the first time they open the dashboard.
#   * 3 completed bookings made by the demo customer, each with one review
#     behind it (reviews only ever exist behind a completed booking).
#   * The demo customer (demo@fundimtaani.co.ke / demo1234) and the demo
#     admin (admin@fundimtaani.co.ke / admin1234, or ADMIN_EMAIL /
#     ADMIN_PASSWORD from the environment when those are set).
#
# Two things worth knowing before you run it:
#   * The 10 seed fundis are stored as APPROVED. That is deliberate demo
#     data, not a shortcut around the approval flow: the directory has to
#     work the moment the app starts, and the approval flow itself is
#     demonstrated by the one Pending application and by any new
#     application, which always starts Pending. The approval code is
#     unchanged — Admin still approves or rejects every new application.
#   * Seeding is IDEMPOTENT. If the database already has data, seed.py
#     prints a message and changes nothing. It never wipes your data.
#     Pass --reset only when you really mean "throw everything away and
#     start again".
#
# Usage:
#   python seed.py            seed a fresh (or empty) database
#   python seed.py --reset    wipe all tables and seed from scratch

import os
import re
import sys

# --- Create the MySQL database BEFORE importing app -----------------------
# app.py creates the tables at import time (db.create_all() at the bottom
# of app.py), which needs the database to already exist. So when the app
# is pointed at MySQL, create the database here first, before the import
# below. (SQLite needs nothing — its file is created on first use.)
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


def ensure_mysql_database(database_url):
    # Create the MySQL database named in the URL if it does not exist yet.
    # Does nothing for SQLite or when no URL is given.
    if not database_url or not database_url.startswith("mysql"):
        return
    url = make_url(database_url)
    db_name = url.database
    if not db_name or not re.fullmatch(r"[A-Za-z0-9_]+", db_name):
        raise RuntimeError(
            f"Refusing to create a MySQL database called {db_name!r}: "
            "use letters, numbers and underscores only."
        )
    # Connect to the server without naming a database, then create it.
    # (URL.set(database=None) does NOT clear the database in SQLAlchemy
    # 2.x, so build the server URL from the parts instead.)
    server_url = URL.create(
        drivername=url.drivername,
        username=url.username,
        password=url.password,
        host=url.host,
        port=url.port,
    )
    engine = create_engine(server_url)
    with engine.begin() as conn:
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
    engine.dispose()
    print(f"MySQL database '{db_name}' is ready.")


ensure_mysql_database(os.environ.get("DATABASE_URL"))

# Passwords are hashed with pbkdf2:sha256 (not Werkzeug's default scrypt),
# because the system Python on macOS has no hashlib.scrypt, so the
# default raises AttributeError there. check_password_hash() reads the
# method from the stored hash, so logins work the same either way.
from werkzeug.security import generate_password_hash

from app import app
from models import Admin, Booking, Category, Review, User, Worker, db


def ensure_database_exists():
    # Safety net: create the database from the app's resolved URI too.
    # Normally the pre-import call above already did this.
    ensure_mysql_database(app.config["SQLALCHEMY_DATABASE_URI"])


# The eight documented trades, with the slug each one uses in links.
CATEGORIES = [
    ("Plumbing", "plumbing"),
    ("Electrical", "electrical"),
    ("Carpentry", "carpentry"),
    ("Painting", "painting"),
    ("Cleaning", "cleaning"),
    ("Welding", "welding"),
    ("Mechanics", "mechanics"),
    ("Masonry", "masonry"),
]

# The 10 fundis, taken from the Checkpoint 1 prototype (same names, phones,
# trades and estates). Each one gets their own customer account below
# (email built from their name), and the fundi profile is linked to it.
# The "cat" number is the position of the trade in CATEGORIES above.
# Alice Wanjiku was a Tailor in the old seed data; Tailoring is not one of
# the 8 documented trades, so she is a Cleaner here.
WORKERS = [
    dict(name="James Mwangi", phone="0722 000 111", cat=0, estate="Langata",
         rate="KES 1,500/day", experience="10 years",
         bio="Licensed plumber with 10 years across Langata and Nairobi. Leaks, tanks, toilets and full pipework — same-day service."),
    dict(name="John Kariuki", phone="0712 345 678", cat=0, estate="Langata",
         rate="From KSh 800 per visit", experience="8 years",
         bio="Plumber serving Langata and the surrounding estates. Same-day call-outs, installations and repairs."),
    dict(name="Brian Otieno", phone="0733 222 111", cat=1, estate="Nairobi West",
         rate="KSh 1,000 call-out fee", experience="6 years",
         bio="Certified electrician. House wiring, sockets, breakers, security lights and solar installation."),
    dict(name="Grace Njeri", phone="0720 987 654", cat=4, estate="Langata",
         rate="From KSh 2,500 per session", experience="5 years",
         bio="Professional cleaner for homes and offices in Langata and Nairobi West. Deep cleaning, move-in cleaning and regular sessions."),
    dict(name="Peter Mwangi", phone="0745 111 222", cat=2, estate="Madaraka",
         rate="Project based / negotiable", experience="12 years",
         bio="Carpenter and joiner with 12 years. Doors, cabinets, beds, wardrobes and locks — custom work to order."),
    dict(name="Samuel Ochieng", phone="0711 444 555", cat=5, estate="Nairobi West",
         rate="Quoted per site visit", experience="10 years",
         bio="Painter for homes in Nairobi West. Interior and exterior painting, straight lines and clean finishes."),
    dict(name="Kevin Mutua", phone="0799 666 777", cat=6, estate="Langata",
         rate="From KSh 1,500 diagnostic", experience="7 years",
         bio="Motor mechanic for saloon cars and vans. Diagnostics, servicing, brakes and suspension."),
    dict(name="Alice Wanjiku", phone="0726 333 444", cat=4, estate="Madaraka",
         rate="From KSh 2,000 per session", experience="9 years",
         bio="Professional cleaner for homes in Madaraka and Langata. Deep cleaning, move-in cleaning and regular sessions."),
    dict(name="Hassan Abdi", phone="0718 888 999", cat=5, estate="Madaraka",
         rate="Quoted per site visit", experience="11 years",
         bio="Welder and fabricator. Gates, window grilles, burglar-proofing and metal repairs."),
    dict(name="Dennis Kiprop", phone="0759 121 212", cat=7, estate="Nairobi West",
         rate="From KSh 100 per sq m", experience="5 years",
         bio="Mason and tiler. Walls, plastering, flooring and tiling for houses and shops."),
]

# The one Pending application Admin will find waiting in the dashboard.
# Daniel is a real seeded account (verified, password "fundi1234") whose
# fundi profile is still Pending — approving him is the first thing a new
# admin can do to see the approval flow work.
PENDING_APPLICANT = dict(
    name="Daniel Mwangi", email="daniel.mwangi@fundimtaani.co.ke",
    phone="0722 555 666", password="fundi1234",
    cat=0, estate="Madaraka", rate="From KSh 900 per visit", experience="6 years",
    national_id="31234567",
    bio="Plumber in Madaraka. Leaks, taps, toilets and tank installations.",
)

# Demo accounts. The passwords below are for DEMO ONLY — change them
# before this app ever faces real users. (The admin password can also be
# set with ADMIN_EMAIL / ADMIN_PASSWORD in the environment.)
DEMO_CUSTOMER = dict(
    name="Starford Omwocha", email="demo@fundimtaani.co.ke",
    phone="0700 111 222", password="demo1234",
)
DEMO_ADMIN_NAME = "FundiMtaani Admin"
FUNDI_ACCOUNT_PASSWORD = "fundi1234"

# The 3 seed reviews. Each one sits behind a completed booking made by the
# demo customer — the first entry is (fundi index, stars, comment).
# The comment texts come from the Checkpoint 1 prototype.
SEEDED_REVIEWS = [
    (0, 5, "James fixed our leaking tank the same day I called. Clean work and fair price."),
    (2, 5, "Rewired our kitchen and added sockets. Everything works perfectly."),
    (3, 5, "Deep cleaned our 3-bedroom before moving in. Spotless."),
]

# What the 3 completed bookings were for (same order as SEEDED_REVIEWS).
SEEDED_BOOKINGS = [
    dict(estate="Langata", preferred_date="2026-10-28", message="Fix a leaking overhead water tank."),
    dict(estate="Nairobi West", preferred_date="2026-10-25", message="Rewire the kitchen and add two sockets."),
    dict(estate="Langata", preferred_date="2026-10-29", message="Deep clean a 3-bedroom house before moving in."),
]


def _fundi_email(name):
    # A simple predictable email for a seed fundi's account, e.g.
    # james.mwangi@fundimtaani.co.ke. Only used for demo data.
    first, last = name.lower().split(" ", 1)
    return f"{first}.{last}@fundimtaani.co.ke"


def seed():
    # Categories first — the fundis need them to exist.
    cats = []
    for name, slug in CATEGORIES:
        cat = Category(name=name, slug=slug)
        db.session.add(cat)
        cats.append(cat)
    db.session.flush()

    # The demo customer (verified, so they can book straight away).
    demo = User(
        full_name=DEMO_CUSTOMER["name"],
        email=DEMO_CUSTOMER["email"],
        phone=DEMO_CUSTOMER["phone"],
        password_hash=generate_password_hash(DEMO_CUSTOMER["password"], method="pbkdf2:sha256"),
        is_email_verified=True,
    )
    db.session.add(demo)
    db.session.flush()

    # One customer account per seed fundi, then the fundi profile linked
    # to it. The profile is APPROVED (see the note at the top of this
    # file about why the demo data starts approved).
    worker_instances = []
    for index, data in enumerate(WORKERS):
        fundi_user = User(
            full_name=data["name"],
            email=_fundi_email(data["name"]),
            phone=data["phone"],
            password_hash=generate_password_hash(FUNDI_ACCOUNT_PASSWORD, method="pbkdf2:sha256"),
            is_email_verified=True,
        )
        db.session.add(fundi_user)
        db.session.flush()

        worker = Worker(
            customer_id=fundi_user.id,
            name=data["name"],
            phone=data["phone"],
            category_id=cats[data["cat"]].id,
            estate=data["estate"],
            bio=data["bio"],
            rate=data["rate"],
            experience=data["experience"],
            # Demo ID numbers only — real fundis type their own in /join.
            national_id=f"3{1000000 + index}",
            status=Worker.STATUS_APPROVED,
        )
        db.session.add(worker)
        db.session.flush()
        worker_instances.append(worker)

    # The Pending application, with its own verified account.
    applicant = User(
        full_name=PENDING_APPLICANT["name"],
        email=PENDING_APPLICANT["email"],
        phone=PENDING_APPLICANT["phone"],
        password_hash=generate_password_hash(PENDING_APPLICANT["password"], method="pbkdf2:sha256"),
        is_email_verified=True,
    )
    db.session.add(applicant)
    db.session.flush()
    pending_worker = Worker(
        customer_id=applicant.id,
        name=PENDING_APPLICANT["name"],
        phone=PENDING_APPLICANT["phone"],
        category_id=cats[PENDING_APPLICANT["cat"]].id,
        estate=PENDING_APPLICANT["estate"],
        bio=PENDING_APPLICANT["bio"],
        rate=PENDING_APPLICANT["rate"],
        experience=PENDING_APPLICANT["experience"],
        national_id=PENDING_APPLICANT["national_id"],
        status=Worker.STATUS_PENDING,
    )
    db.session.add(pending_worker)

    # Completed bookings by the demo customer, then one review on each.
    # A review is only ever created after its booking exists and is
    # Completed — exactly the rule the app enforces for real users.
    for (worker_index, rating, comment), job in zip(SEEDED_REVIEWS, SEEDED_BOOKINGS):
        worker = worker_instances[worker_index]
        booking = Booking(
            worker_id=worker.id,
            customer_id=demo.id,
            estate=job["estate"],
            preferred_date=job["preferred_date"],
            preferred_slot="Morning (8:00 AM - 12:00 PM)",
            message=job["message"],
            status=Booking.STATUS_COMPLETED,
        )
        db.session.add(booking)
        db.session.flush()
        db.session.add(
            Review(
                worker_id=worker.id,
                booking_id=booking.id,
                customer_id=demo.id,
                rating=rating,
                comment=comment,
            )
        )

    # The admin account. The email/password come from the environment when
    # set (ADMIN_EMAIL / ADMIN_PASSWORD); otherwise the demo defaults
    # below are used. The app itself never contains a password.
    # NOTE: `or` (not just the .get default) matters here. When .env sets
    # ADMIN_PASSWORD= with nothing after it, the variable EXISTS but is
    # empty, and .get() would return "" instead of the default — which
    # once created an admin account with a blank password. An empty
    # value must fall back to the demo default too.
    admin_email = os.environ.get("ADMIN_EMAIL") or "admin@fundimtaani.co.ke"
    admin_password = os.environ.get("ADMIN_PASSWORD") or "admin1234"
    db.session.add(
        Admin(
            full_name=DEMO_ADMIN_NAME,
            email=admin_email,
            password_hash=generate_password_hash(admin_password, method="pbkdf2:sha256"),
        )
    )

    db.session.commit()
    print(f"Seeded {len(CATEGORIES)} categories, {len(WORKERS)} approved fundis, "
          f"1 pending application, {len(SEEDED_REVIEWS)} completed bookings "
          "with reviews, 1 demo customer and 1 admin.")
    print()
    print("Demo accounts (CHANGE THESE PASSWORDS before real use):")
    print(f"  Customer: {DEMO_CUSTOMER['email']} / {DEMO_CUSTOMER['password']}")
    print(f"  Admin:    {admin_email} / {admin_password}")
    print(f"  Fundis:   <firstname.lastname>@fundimtaani.co.ke / {FUNDI_ACCOUNT_PASSWORD}")
    print(f"  Applicant (pending): {PENDING_APPLICANT['email']} / {PENDING_APPLICANT['password']}")
    if admin_password == "admin1234" or DEMO_CUSTOMER["password"] == "demo1234":
        print("  WARNING: you are using the default demo passwords. "
              "Set ADMIN_EMAIL/ADMIN_PASSWORD in .env and change them.")


if __name__ == "__main__":
    reset = "--reset" in sys.argv
    with app.app_context():
        ensure_database_exists()
        db.create_all()
        already_seeded = (
            User.query.first() is not None
            or Admin.query.first() is not None
            or Category.query.first() is not None
        )
        if already_seeded and not reset:
            # Idempotent: the database already has data, so do nothing.
            # This protects real accounts, bookings and reviews from
            # being wiped by an accidental second run.
            print("Database already has data — nothing to do.")
            print("Run 'python seed.py --reset' only if you want to wipe "
                  "everything and seed from scratch.")
            sys.exit(0)
        if reset:
            print("Wiping all tables (--reset was passed)...")
            # The seeded-check above ran SELECTs, which leave an open
            # transaction on this session. MySQL holds a metadata lock
            # for that transaction, so DROP TABLE from another connection
            # would wait forever. Roll it back first. (SQLite never
            # showed this — it has no metadata locks.)
            db.session.rollback()
            db.drop_all()
            db.create_all()
        seed()
