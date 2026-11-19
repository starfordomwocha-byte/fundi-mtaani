# FundiMtaani

A directory of verified local fundis (plumbers, electricians, carpenters and
more) in three Nairobi estates — **Langata, Madaraka and Nairobi West**.
Residents find a fundi, book them, and pay the fundi directly.

Built with **Flask + MySQL** (SQLAlchemy), Bootstrap 5, mobile-first.

## The model (read this first)

- **No payment gateway, no commission, no fees.** The customer pays the
  fundi directly, in person, once the work is done. FundiMtaani takes
  nothing. There is no featured-listing upsell and no advertising.
- **Email Service and WhatsApp are not part of the system.** WhatsApp
  (`wa.me`) and phone links on a fundi's profile are the contact paths —
  the customer talks to the fundi directly.
- **Three estates only:** Langata, Madaraka, Nairobi West. Every form,
  filter and seed record uses exactly these three.
- **Eight trades only:** Plumbing, Electrical, Carpentry, Painting,
  Cleaning, Welding, Mechanics, Masonry.
- **Email is verified by a 6-digit code; the phone is never verified.**
- **Booking lifecycle:** Pending → Accepted → Job Done → Completed, with
  Declined branching from Pending. The fundi marks the job done; **only
  the customer** confirms completion.
- **Fundis are hidden until Admin approves them** (after checking the ID
  document the fundi uploaded when applying).
- **Reviews only after a completed booking**, one per booking, by that
  booking's customer only.

## Features (mapped to the requirements, FR01–FR27)

| Who | What |
| --- | --- |
| Visitor | Search fundis by trade, estate, text (name, bio, rate, trade) and minimum rating; sort by newest / highest rated / most reviewed (FR01–FR03); view a fundi profile with its reviews (FR04); call or WhatsApp the fundi (FR05) |
| Customer | Register (FR06); verify email with a 6-digit code (FR07); log in by password or by emailed code (FR08); book a chosen fundi (FR09); confirm a finished job (FR10); review a completed booking, one per booking (FR11) |
| Fundi | Apply with trade, estate, photo, National ID number and ID document; save a draft and finish it later (FR12); see own bookings (FR13); accept / decline (FR14/FR15); mark job done (FR16); update own profile — rate, estate, bio, photo (FR17) |
| Admin | Separate admin login against the Admin table (FR18); approve / reject applications after the ID check, with a recorded reason (FR19/FR20); suspend / reactivate customers (FR21/FR22); add / edit / delete categories (FR23–FR25); remove reviews — the rating recalculates automatically (FR26); reports page with counts and breakdowns, plus CSV exports (FR27) |

Access control is enforced on the server on every route: visitors cannot
book or review; customers and fundis only ever see and change their own
records; only Admin can approve, suspend, manage categories, remove
reviews or view reports.

## Quick start

```bash
cd fundimtaani
python -m venv venv
source venv/bin/activate        # Windows: .\venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then set SECRET_KEY (see below)
# Start MySQL first (XAMPP: start MySQL in the manager). seed.py creates
# the `fundimtaani` database itself if it does not exist yet.
python seed.py                  # demo data (idempotent — safe to re-run)
python app.py
```

Open http://127.0.0.1:5000 · Admin at `/admin/login`.

**SECRET_KEY is required.** The app refuses to start without it. Generate
one with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

and put it in `.env` as `SECRET_KEY=<the value>`.

To re-seed from scratch: `python seed.py --reset` (wipes all tables).
Without `--reset`, `seed.py` prints a message and changes nothing when
the database already has data.

## Demo accounts

> **Change these passwords before real use.** The admin password can be
> set with `ADMIN_EMAIL` / `ADMIN_PASSWORD` in `.env` before seeding.

| Account | Email | Password |
| --- | --- | --- |
| Customer (verified) | demo@fundimtaani.co.ke | demo1234 |
| Admin | admin@fundimtaani.co.ke | admin1234 |
| Fundis | `<firstname.lastname>@fundimtaani.co.ke` (e.g. james.mwangi@fundimtaani.co.ke) | fundi1234 |
| Pending applicant | daniel.mwangi@fundimtaani.co.ke | fundi1234 |

The seeded fundis are stored as **approved** demo data so the directory
works out of the box; the approval flow itself is demonstrated by the one
Pending application (Daniel Mwangi) and by any new application, which
always starts Pending. There are also 3 completed bookings by the demo
customer, each with one review behind it.

## How email codes work (no mail server needed)

All six notification emails (verification code, login code, booking
created, booking accepted/declined, job done, application approved/
rejected) go through one helper, `notifications.py`.

- **With a mail server configured** (`MAIL_SERVER`, `MAIL_PORT`,
  `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM` in `.env`), emails are
  sent for real with Python's built-in `smtplib`.
- **Without one (the default)**, the app runs in **demo mode**: every
  email is written to `instance/dev-outbox.txt`, and the 6-digit code is
  also shown on the verify / login-code page in a clearly labelled
  "demo email" box, so every flow works end to end with no mail server.

## Project layout

```
app.py             Every page (route): public, accounts, fundi, bookings, admin
models.py          Database tables: User, Admin, Category, Worker (Fundi),
                   Booking, Review, VerificationCode
notifications.py   The one email helper (smtplib, or demo outbox when unconfigured)
seed.py            Demo data: 8 trades, 10 fundis, 1 pending application,
                   3 completed bookings + reviews, demo accounts
templates/         Jinja templates (Bootstrap 5); templates/admin/ is the admin area
static/            CSS, JS (theme toggle, WhatsApp composer, admin table search)
instance/          Uploaded photos/ID documents + demo outbox
                   (created at runtime; not in git)
wsgi.py            Entry point for gunicorn (gunicorn app:app)
run_server.py      Convenience runner (python run_server.py)
requirements.txt   Flask, Flask-SQLAlchemy, PyMySQL, python-dotenv, Werkzeug, MarkupSafe, gunicorn
.env.example       Every setting, with no real secrets in it
```

Uploads (profile photos and ID documents) go to `instance/uploads/`
(PNG/JPG only, 2 MB limit). ID documents are only openable by a signed-in
admin; profile photos are public on the fundi's page.

## Deploy (putting it online)

Locally the app runs on your own machine at http://127.0.0.1:5000 with
XAMPP MySQL. Online it is the same code, but the host runs it instead of
you, and the address is a real domain instead of 127.0.0.1:5000. Nothing
in the code changes — only the settings in `.env` and how it is started.

1. **Get a host that runs Python and MySQL** (a small VPS, or a Python
   host such as PythonAnywhere or Render). The host needs Python 3.9+,
   MySQL, and a way to run a long-lived process.
2. **Put the code on the host** and install the requirements
   (`pip install -r requirements.txt`).
3. **Create a MySQL database on the host** (in the host's panel, or let
   `seed.py` create it — it runs `CREATE DATABASE IF NOT EXISTS
   fundimtaani` when the MySQL user has permission to create databases).
4. **Set the environment on the host** (in `.env` or the host's
   environment settings — never commit real secrets to git):
   - `DATABASE_URL` — the host's MySQL address, e.g.
     `mysql+pymysql://fundimtaani:<password>@<host>:3306/fundimtaani`.
   - `SECRET_KEY` — a fresh random value (generate one with
     `python -c "import secrets; print(secrets.token_hex(32))"`).
     Never reuse the local development key.
   - `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`,
     `MAIL_FROM` — a real mail account, so the 6-digit codes are really
     emailed. Without these the app stays in demo mode (codes shown on
     screen), which is fine for testing but not for real users.
   - `FLASK_ENV=production` — this makes the session cookie HTTPS-only.
   - `ADMIN_EMAIL` / `ADMIN_PASSWORD` — set these before the first
     seeding, so the admin account is not the public demo one.
5. **Seed once on the host** (`python seed.py`) — this creates the 7
   tables and the demo data. Use `--reset` only if you want to wipe it
   and start over.
6. **Run it with gunicorn, not the development server:**
   `gunicorn app:app` (the `wsgi.py` entry point). `run_server.py` and
   `python app.py` run the debug server, which is for local use only.
7. **Backups:** the database lives in MySQL — back it up with mysqldump
   (`mysqldump -u <user> -p fundimtaani > fundimtaani.sql`). Uploads
   (profile photos and ID documents) live under `instance/uploads/`;
   back those up with it. The `instance/` folder must be writable by
   the app and must survive restarts — on hosts with an ephemeral
   filesystem, point uploads at persistent storage instead.
