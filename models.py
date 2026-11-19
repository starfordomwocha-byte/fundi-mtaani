# models.py — the database tables for FundiMtaani.
#
# This file only describes the shape of the data. It does not contain any
# web pages or routes; app.py is where the pages live.
#
# The tables match the design in Chapter 4 of the project report:
#   User (the customer account), Fundi (called Worker in the code),
#   Category, Booking and Review — plus an Admin table of its own.
# There is no payment table anywhere, because FundiMtaani takes no payment
# and charges no commission: the customer pays the fundi directly.

from datetime import datetime, timedelta, timezone

from flask_sqlalchemy import SQLAlchemy

# "db" is the one database connection the whole app shares. app.py calls
# db.init_app(app) to plug it into Flask, and seed.py uses it to fill the
# database with the starting fundis and demo accounts.
db = SQLAlchemy()


def utcnow():
    # One small helper so every "created at" time in the app means the same
    # thing: the current moment in UTC, with a timezone attached.
    return datetime.now(timezone.utc)


class Category(db.Model):
    # A trade a fundi can work in, for example Plumbing or Masonry.
    # The documentation fixes these at eight trades, and Admin can add, edit
    # or delete them from the admin pages (FR23-FR25). Fundis are grouped
    # under these on the home page and in the directory filters.
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    # The trade name, e.g. "Plumbing". It must be unique so the same trade
    # can never be added twice.
    name = db.Column(db.String(80), unique=True, nullable=False)
    # A short web-safe version of the name, e.g. "plumbing", used in
    # directory links like /fundis?category=plumbing.
    slug = db.Column(db.String(80), unique=True, nullable=False)

    # All the fundis in this trade. Reading category.workers gives them.
    workers = db.relationship("Worker", backref="category", lazy=True)


class User(db.Model):
    # A customer account (FR06). Everybody who books a fundi, or applies to
    # become one, first has an account here. A fundi is not a separate kind
    # of account: a fundi is a customer who also has a Worker profile
    # (see Worker.customer_id below).
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # The customer's full name, shown on bookings and reviews.
    full_name = db.Column(db.String(120), nullable=False)
    # The email address. Unique, so one email can only ever own one
    # account, and this is where verification and login codes are sent.
    email = db.Column(db.String(160), unique=True, nullable=False)
    # The phone number, in the Kenyan 07XX XXX XXX shape. Also unique.
    # The phone is stored only — it is NEVER verified (FR07 verifies the
    # email, never the phone).
    phone = db.Column(db.String(20), unique=True, nullable=False)
    # The password, stored as a salted hash (never as plain text). Checked
    # with check_password_hash() when the customer logs in (FR08).
    password_hash = db.Column(db.String(255), nullable=False)
    # True once the customer has entered the 6-digit code emailed to them
    # after registration (FR07). Booking is blocked until this is True.
    is_email_verified = db.Column(db.Boolean, default=False, nullable=False)
    # True for a normal account. Admin can flip this to False to suspend
    # the account (FR21); flipping it back reactivates it (FR22). A
    # suspended account cannot log in, and its fundi profile (if it has
    # one) disappears from search and browse until it is reactivated.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    # The fundi profile belonging to this account, if any. One account can
    # have at most one profile — that is the "one posting per account" rule
    # (Data Integrity NFR), enforced by the UNIQUE link on Worker.
    fundi_profile = db.relationship(
        "Worker", backref="owner", uselist=False, lazy=True
    )
    # The bookings this customer has made, and the reviews they have written.
    bookings = db.relationship("Booking", backref="customer", lazy=True)
    reviews = db.relationship("Review", backref="customer", lazy=True)


class VerificationCode(db.Model):
    # A short-lived 6-digit code emailed to a customer, for one of two jobs:
    #   purpose "verify" — proving they own their email after registering
    #       (FR07), or
    #   purpose "login"  — logging in without a password (FR08).
    # Only the HASH of the code is stored here, never the code itself, so
    # somebody reading the database cannot steal a live code. A code works
    # once only, and dies after 15 minutes.
    __tablename__ = "verification_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Either "verify" or "login" (see above).
    purpose = db.Column(db.String(20), nullable=False)
    # Salted hash of the 6-digit code, made with generate_password_hash() using pbkdf2 (see the note at the call site below).
    code_hash = db.Column(db.String(255), nullable=False)
    # The moment after which the code stops working.
    expires_at = db.Column(db.DateTime, nullable=False)
    # True once the code has been used. A used code can never be reused.
    used = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    user = db.relationship("User", backref="verification_codes", lazy=True)

    @property
    def is_expired(self):
        # A code is dead when the current time has passed its expiry.
        # The stored time is timezone-aware (UTC), so compare like with like.
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) > expires

    @classmethod
    def new_code(cls):
        # Make a fresh code that lives for 15 minutes. The code itself is
        # returned so the caller can email it; only its hash is stored.
        import secrets

        # pbkdf2:sha256, not the scrypt default: the system Python on macOS
        # has no hashlib.scrypt, so the default raises AttributeError there.
        from werkzeug.security import generate_password_hash

        code = f"{secrets.randbelow(900000) + 100000:06d}"
        record = cls(
            code_hash=generate_password_hash(code, method="pbkdf2:sha256"),
            expires_at=utcnow() + timedelta(minutes=15),
        )
        return record, code


class Admin(db.Model):
    # An administrator account (FR18). Admins are NOT customers: they live
    # in their own table, log in on their own page (/admin/login), and their
    # session is separate from a customer's session. There is no shared
    # admin password any more — each admin has their own email and a
    # hashed password, exactly like a customer account.
    __tablename__ = "admins"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    # Unique, so each admin email can only belong to one admin.
    email = db.Column(db.String(160), unique=True, nullable=False)
    # Salted hash of the admin's password (never plain text).
    password_hash = db.Column(db.String(255), nullable=False)
    # An admin whose account is deactivated cannot log in.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)


class Worker(db.Model):
    # A fundi profile — the listing a customer sees in the directory.
    # The code calls it "Worker" (the original name in this codebase) but
    # it is the Fundi of the documentation: trade, estate, experience,
    # rate, bio, photo and National ID / passport.
    #
    # A Worker is a PROFILE linked to a customer account, not an account
    # itself. A customer applies through /join and, once Admin approves the
    # application, this row becomes visible in the directory (FR12, FR19).
    STATUS_DRAFT = "draft"
    STATUS_PENDING = "pending"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"

    __tablename__ = "workers"

    id = db.Column(db.Integer, primary_key=True)
    # The customer account this profile belongs to. UNIQUE means one
    # account can own at most one fundi profile — the "one posting per
    # account" rule, enforced by the database itself. It is nullable only
    # so that old seed/demo rows without an owner can still exist; every
    # profile made through /join always has an owner.
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), unique=True, nullable=True, index=True
    )
    # The fundi's display name. For profiles made through /join this is
    # copied from the owner's account name, so the listing and the account
    # can never drift apart.
    name = db.Column(db.String(120), nullable=False)
    # The phone customers call or WhatsApp. Copied from the owner's
    # account phone for the same reason as the name.
    phone = db.Column(db.String(20), nullable=False)
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id"), nullable=False, index=True
    )
    # Where the fundi works. Only three estates exist in the whole system
    # (Langata, Madaraka, Nairobi West) — see ESTATES in app.py.
    estate = db.Column(db.String(80), nullable=False, index=True)
    # A short description of the work the fundi does.
    bio = db.Column(db.Text, default="")
    # What the fundi charges, written in their own words, e.g.
    # "From KSh 800 per visit". This is the fundi's own rate, not a price
    # set by the system — there is no system price list.
    rate = db.Column(db.String(80), default="")
    # Years of experience, written in the fundi's own words, e.g. "8 years".
    experience = db.Column(db.String(40), default="")
    # The profile photo the fundi uploaded when applying. Just the file
    # name; the file itself lives under instance/uploads/. Empty until a
    # photo is uploaded.
    photo_filename = db.Column(db.String(200), default="")
    # The National ID or passport number the fundi typed in when applying.
    # Admin reads this (together with the uploaded document) when deciding
    # whether to approve the application (FR19).
    national_id = db.Column(db.String(40), default="")
    # The uploaded photo/scan of that ID document. File name only; the
    # file lives under instance/uploads/. Only Admin can open it.
    id_document_filename = db.Column(db.String(200), default="")
    # Where the profile sits in its life: "draft" (started but not sent),
    # "pending" (waiting for Admin), "approved" (live in the directory)
    # or "rejected" (Admin said no — see rejection_reason).
    status = db.Column(db.String(20), default=STATUS_PENDING, index=True)
    # Why Admin rejected the application, written by Admin at rejection
    # time (FR20). The fundi sees this on their application page and the
    # reason is emailed to them.
    rejection_reason = db.Column(db.Text, default="")
    # Whether the fundi is taking new jobs right now. The fundi switches
    # this on their own profile (FR17); the directory's "Available only"
    # filter (FR03) uses it. A fundi who is off is still listed — they are
    # just hidden when a visitor asks for available fundis only. New
    # profiles start available.
    is_available = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow)

    # The reviews left on this fundi, and the bookings made for them.
    # Deleting a fundi deletes their reviews and bookings too, so no
    # orphaned rows are left behind.
    reviews = db.relationship(
        "Review", backref="worker", lazy=True, cascade="all, delete-orphan"
    )
    bookings = db.relationship(
        "Booking", backref="worker", lazy=True, cascade="all, delete-orphan"
    )

    @property
    def initials(self):
        # The first letters of the first two names, e.g. "JM" for
        # "James Mwangi". Used for the round avatar shown when the fundi
        # has not uploaded a photo.
        parts = self.name.split()
        return "".join(p[0] for p in parts[:2]).upper()

    @property
    def avg_rating(self):
        # The fundi's average star rating. This is COMPUTED from the
        # reviews every time it is read — it is never stored in a column.
        # That matters: when Admin removes a review (FR26), the average
        # fixes itself automatically, with nothing extra to update.
        # Returns None when there are no reviews yet.
        if not self.reviews:
            return None
        return sum(r.rating for r in self.reviews) / len(self.reviews)

    @property
    def review_count(self):
        # How many reviews this fundi has. Just the count of the rows.
        return len(self.reviews)

    @property
    def rating_breakdown(self):
        # How many 5-star, 4-star ... 1-star reviews there are, plus each
        # share as a percentage. The profile page draws its rating bars
        # from this.
        counts = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        for r in self.reviews:
            if r.rating in counts:
                counts[r.rating] += 1
        total = len(self.reviews) or 1
        percentages = {k: int((v / total) * 100) for k, v in counts.items()}
        return {"counts": counts, "percentages": percentages, "total": len(self.reviews)}

    def whatsapp_link(self, message=None):
        # Build the wa.me link customers tap to open WhatsApp with this
        # fundi (FR05). The fundi's own phone number is used — never a
        # shared or placeholder number. The Kenyan 07XX number is turned
        # into the international 2547XX shape WhatsApp needs.
        digits = "".join(c for c in self.phone if c.isdigit())
        if digits.startswith("0"):
            digits = "254" + digits[1:]
        text = message or (
            f"Hello {self.name}, I found your listing on FundiMtaani. I would like to request assistance with "
            f"{self.category.name.lower()} service in {self.estate}."
        )
        return f"https://wa.me/{digits}?text={text.replace(' ', '%20')}"


class Review(db.Model):
    # A review of a fundi (FR11). A review can only ever exist behind a
    # completed booking: it points at the booking, at the customer who
    # made that booking, and at the fundi. One booking can have at most
    # one review — the UNIQUE on booking_id makes the database enforce
    # that, and the route checks it too.
    __tablename__ = "reviews"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False)
    # The booking this review belongs to. UNIQUE = one review per booking.
    booking_id = db.Column(
        db.Integer, db.ForeignKey("bookings.id"), unique=True, nullable=False
    )
    # The customer who wrote it — always the customer who made the booking.
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    # Stars, 1 to 5.
    rating = db.Column(db.Integer, nullable=False)
    # The written comment. May be empty.
    comment = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=utcnow)

    # The booking itself, so a review can always show which job it was for.
    # The backref is uselist=False, so booking.review is ONE review (or
    # None) — never a list. The routes rely on that: "booking.review is
    # not None" means "this booking already has its one review".
    booking = db.relationship(
        "Booking", backref=db.backref("review", uselist=False), lazy=True
    )


class Booking(db.Model):
    # A booking: a customer asking a chosen fundi to do a job (FR09).
    # The booking moves through one fixed path, and only in this order:
    #   pending -> accepted -> job_done -> completed
    # with "declined" branching off from pending only.
    # The fundi accepts or declines (FR14/FR15), the fundi marks the job
    # done (FR16), and ONLY the customer confirms completion (FR10).
    # Every step is guarded in app.py — a request that tries to jump the
    # order is refused and nothing changes.
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_JOB_DONE = "job_done"
    STATUS_COMPLETED = "completed"

    __tablename__ = "bookings"

    id = db.Column(db.Integer, primary_key=True)
    worker_id = db.Column(db.Integer, db.ForeignKey("workers.id"), nullable=False)
    # The customer who made the booking. A booking always belongs to a
    # logged-in, email-verified account — there are no anonymous bookings,
    # and no free-text name/phone any more: the name and phone come from
    # the account itself.
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    # Where the job is: one of the three estates (Langata, Madaraka,
    # Nairobi West).
    estate = db.Column(db.String(80), default="")
    # When the customer would like the work done, in their own words,
    # e.g. "Tomorrow" or "2026-11-25".
    preferred_date = db.Column(db.String(40), default="")
    # Morning / afternoon / evening, chosen from a fixed list.
    preferred_slot = db.Column(db.String(40), default="Morning (8:00 AM - 12:00 PM)")
    # What needs doing, in the customer's own words.
    message = db.Column(db.Text, default="")
    # Where the booking sits in its life (see the path above).
    status = db.Column(db.String(20), default=STATUS_PENDING, index=True)
    created_at = db.Column(db.DateTime, default=utcnow)

    @property
    def status_label(self):
        # The friendly words shown in the interface for each status value.
        labels = {
            self.STATUS_PENDING: "Pending",
            self.STATUS_ACCEPTED: "Accepted",
            self.STATUS_DECLINED: "Declined",
            self.STATUS_JOB_DONE: "Job Done",
            self.STATUS_COMPLETED: "Completed",
        }
        return labels.get(self.status, self.status)
