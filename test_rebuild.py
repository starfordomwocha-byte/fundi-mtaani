# Full happy-path + access-control test for the rebuilt FundiMtaani app.
# Run from the app directory with SECRET_KEY set, after `python seed.py`.
import io
import os
import re
import sys

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-local-testing-only")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402
from models import Booking, Category, Review, User, Worker, db  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" — {extra}" if extra and not cond else ""))


def csrf(client, path):
    """Fetch a page and pull the CSRF token out of its first form."""
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, f"no csrf token on {path}"
    return m.group(1)


def login_as(client, email, password):
    token = csrf(client, "/login")
    r = client.post("/login", data={"csrf_token": token, "email": email, "password": password},
                    follow_redirects=False)
    assert r.status_code == 302, f"login failed for {email}: {r.status_code}"
    return r


client = app.test_client()

# --- Public pages ---------------------------------------------------------
r = client.get("/")
check("home 200", r.status_code == 200)
check("home lists the newest seed fundi", b"Dennis Kiprop" in r.data)
check("home has no featured section", b"Featured Hustlers" not in r.data)
check("home has no post-a-job", b"Post a Job" not in r.data)

r = client.get("/fundis")
check("fundis 200", r.status_code == 200)
check("fundis lists 10 seed fundis", r.data.count(b"View Profile") == 10, f"got {r.data.count(b'View Profile')}")

r = client.get("/fundis?category=plumbing")
check("filter by category", b"James Mwangi" in r.data and b"Brian Otieno" not in r.data)
r = client.get("/fundis?estate=Madaraka")
check("filter by estate", b"Peter Mwangi" in r.data and b"James Mwangi" not in r.data)
r = client.get("/fundis?q=solar")
check("text search matches bio", b"Brian Otieno" in r.data)
r = client.get("/fundis?q=plumbing")
check("text search matches trade name", b"James Mwangi" in r.data)
r = client.get("/fundis?min_rating=5")
check("min rating filter", b"James Mwangi" in r.data and b"Dennis Kiprop" not in r.data)
r = client.get("/fundis?sort=rating")
check("sort by rating 200", r.status_code == 200)
r = client.get("/fundis?sort=reviews")
check("sort by reviews 200", r.status_code == 200)
r = client.get("/fundis?estate=Kasarani")
check("old estate returns nothing", b"View Profile" not in r.data)

with app.app_context():
    james = Worker.query.filter_by(name="James Mwangi").first()
    james_id = james.id
    daniel = Worker.query.filter_by(name="Daniel Mwangi").first()
    daniel_id = daniel.id
    demo = User.query.filter_by(email="demo@fundimtaani.co.ke").first()
    demo_id = demo.id

r = client.get(f"/fundi/{james_id}")
check("fundi profile 200", r.status_code == 200)
check("profile shows verified badge", b"Verified" in r.data)
check("profile has no QR modal", b"fundiCardModal" not in r.data and b"qrcode" not in r.data)
check("profile has no anonymous review form", b"Leave a Verified Review" not in r.data)
check("profile shows seeded review", b"James fixed our leaking tank" in r.data)
r = client.get(f"/fundi/{daniel_id}")
check("pending fundi profile is 404", r.status_code == 404)
r = client.get("/fundi/99999")
check("missing fundi 404", r.status_code == 404)

for gone in ["/coverage", "/guides", "/refer", "/request", "/post-job", "/privacy", "/terms", "/f/1"]:
    check(f"removed route {gone} is 404", client.get(gone).status_code == 404)

r = client.get("/register")
check("register page 200", r.status_code == 200)
r = client.get("/login")
check("login page 200", r.status_code == 200)
r = client.get("/admin/login")
check("admin login page 200", r.status_code == 200)

# --- Visitor cannot book ---------------------------------------------------
r = client.post(f"/fundi/{james_id}/book", data={"csrf_token": csrf(client, "/login"),
                "estate": "Langata", "message": "x"}, follow_redirects=False)
check("visitor booking redirects to login", r.status_code == 302 and "/login" in r.headers["Location"])

# --- Register -> verify -> login (password) --------------------------------
token = csrf(client, "/login")
r = client.post("/register", data={
    "csrf_token": token, "full_name": "Test Customer", "email": "test@example.com",
    "phone": "0712 999 888", "password": "secret12"}, follow_redirects=False)
check("register redirects to verify", r.status_code == 302 and "/verify" in r.headers["Location"])
with client.session_transaction() as sess:
    demo_code = sess.get("demo_verify_code")
check("demo verification code shown", bool(demo_code) and len(demo_code) == 6)
r = client.post("/verify?email=test@example.com", data={
    "csrf_token": csrf(client, "/login"),
    "email": "test@example.com", "code": "000000"}, follow_redirects=True)
check("wrong code refused", b"wrong or has expired" in r.data)
r = client.post("/verify?email=test@example.com", data={
    "csrf_token": csrf(client, "/login"),
    "email": "test@example.com", "code": demo_code}, follow_redirects=True)
check("correct code verifies + logs in", b"email is verified" in r.data)
with app.app_context():
    u = User.query.filter_by(email="test@example.com").first()
    check("user marked verified in db", u is not None and u.is_email_verified)

# --- Login by emailed code -------------------------------------------------
client.get("/logout")
token = csrf(client, "/login")
r = client.post("/login/request-code", data={"csrf_token": token, "email": "test@example.com"},
                follow_redirects=False)
check("login code requested", r.status_code == 302 and "/login/code" in r.headers["Location"])
with client.session_transaction() as sess:
    login_code = sess.get("demo_login_code")
check("demo login code shown", bool(login_code) and len(login_code) == 6)
r = client.post("/login/code?email=test@example.com", data={
    "csrf_token": csrf(client, "/login"),
    "email": "test@example.com", "code": login_code}, follow_redirects=True)
check("login by code works", b"Welcome back" in r.data)

# --- Book -> accept -> job done -> confirm -> review (full lifecycle) -------
token = csrf(client, "/login")
r = client.post(f"/fundi/{james_id}/book", data={
    "csrf_token": token, "estate": "Langata", "preferred_date": "Tomorrow",
    "preferred_slot": "Morning (8:00 AM - 12:00 PM)",
    "message": "Fix the kitchen tap, it leaks all night."}, follow_redirects=True)
check("booking created", b"Booking sent to James Mwangi" in r.data)
with app.app_context():
    b = Booking.query.filter_by(customer_id=u.id).order_by(Booking.id.desc()).first()
    booking_id = b.id
    check("booking is Pending", b.status == Booking.STATUS_PENDING)
    check("booking has no free-text name", not hasattr(b, "client_name"))

# Illegal: customer tries to confirm a Pending booking -> refused
r = client.post(f"/booking/{booking_id}/confirm", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
with app.app_context():
    check("confirm on Pending refused", Booking.query.get(booking_id).status == Booking.STATUS_PENDING)
# Illegal: customer tries to accept their own booking -> 403
r = client.post(f"/booking/{booking_id}/accept", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=False)
check("customer cannot accept a booking", r.status_code == 403)

# Fundi side
client.get("/logout")
login_as(client, "james.mwangi@fundimtaani.co.ke", "fundi1234")
r = client.get("/my-jobs")
check("my jobs 200", r.status_code == 200)
check("fundi sees the booking", b"Fix the kitchen tap" in r.data)
# Illegal: fundi tries job-done on a Pending booking -> refused
r = client.post(f"/booking/{booking_id}/job-done", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
with app.app_context():
    check("job-done on Pending refused", Booking.query.get(booking_id).status == Booking.STATUS_PENDING)
r = client.post(f"/booking/{booking_id}/accept", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("fundi accepts", b"accepted" in r.data)
with app.app_context():
    check("booking is Accepted", Booking.query.get(booking_id).status == Booking.STATUS_ACCEPTED)
# Illegal: fundi tries to accept again -> refused
r = client.post(f"/booking/{booking_id}/accept", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("double accept refused", b"Only a pending booking can be accepted" in r.data)
r = client.post(f"/booking/{booking_id}/job-done", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("fundi marks job done", b"marked as job done" in r.data)
with app.app_context():
    check("booking is Job Done", Booking.query.get(booking_id).status == Booking.STATUS_JOB_DONE)

# Customer confirms
client.get("/logout")
login_as(client, "test@example.com", "secret12")
r = client.get("/bookings")
check("my bookings 200", r.status_code == 200)
r = client.post(f"/booking/{booking_id}/confirm", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("customer confirms completion", b"confirmed as complete" in r.data)
with app.app_context():
    check("booking is Completed", Booking.query.get(booking_id).status == Booking.STATUS_COMPLETED)

# Review: once, then refused a second time
r = client.post(f"/booking/{booking_id}/review", data={
    "csrf_token": csrf(client, "/login"), "rating": "5",
    "comment": "Fixed the tap quickly, fair price."}, follow_redirects=True)
check("review published", b"review has been published" in r.data)
with app.app_context():
    rev = Review.query.filter_by(booking_id=booking_id).first()
    check("review linked to booking + customer", rev is not None and rev.customer_id == u.id and rev.rating == 5)
    check("avg rating recalculated", abs(Worker.query.get(james_id).avg_rating - 5.0) < 0.01)
r = client.post(f"/booking/{booking_id}/review", data={
    "csrf_token": csrf(client, "/login"), "rating": "1",
    "comment": "second review"}, follow_redirects=True)
check("second review on same booking refused", b"already reviewed" in r.data)
with app.app_context():
    check("still one review", Review.query.filter_by(booking_id=booking_id).count() == 1)

# Decline path (second booking)
token = csrf(client, "/login")
client.post(f"/fundi/{james_id}/book", data={
    "csrf_token": token, "estate": "Langata", "message": "Second job."}, follow_redirects=True)
with app.app_context():
    b2 = Booking.query.filter_by(customer_id=u.id, message="Second job.").first()
    b2_id = b2.id
client.get("/logout")
login_as(client, "james.mwangi@fundimtaani.co.ke", "fundi1234")
r = client.post(f"/booking/{b2_id}/decline", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("fundi declines", b"declined" in r.data)
with app.app_context():
    check("booking is Declined", Booking.query.get(b2_id).status == Booking.STATUS_DECLINED)

# --- Fundi application: draft -> submit -> approve -------------------------
client.get("/logout")
login_as(client, "test@example.com", "secret12")
r = client.get("/join")
check("join page 200", r.status_code == 200)
png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
token = csrf(client, "/login")
r = client.post("/join", data={
    "csrf_token": token, "action": "draft", "category_id": "1", "estate": "Langata",
    "rate": "From KSh 700", "experience": "3 years", "bio": "Test fundi bio.",
    "national_id": "39887766",
    "photo": (io.BytesIO(png), "photo.png"),
    "id_document": (io.BytesIO(png), "id.png")},
    content_type="multipart/form-data", follow_redirects=True)
check("draft saved", b"Draft saved" in r.data)
with app.app_context():
    w = User.query.filter_by(email="test@example.com").first().fundi_profile
    check("draft profile exists, hidden from directory",
          w is not None and w.status == Worker.STATUS_DRAFT)
    draft_id = w.id
r = client.get("/fundis")
check("draft not listed", b"Test Customer" not in r.data)
token = csrf(client, "/login")
r = client.post("/join", data={
    "csrf_token": token, "action": "submit", "category_id": "1", "estate": "Langata",
    "rate": "From KSh 700", "experience": "3 years", "bio": "Test fundi bio.",
    "national_id": "39887766"},
    content_type="multipart/form-data", follow_redirects=True)
check("application submitted", b"Application Submitted" in r.data)
with app.app_context():
    check("profile is Pending", Worker.query.get(draft_id).status == Worker.STATUS_PENDING)
# Second application blocked — status page instead
r = client.get("/join")
check("existing application shows status, not blank form", b"Application Pending" in r.data)
# Uploads went to instance/uploads
check("uploads saved", len(os.listdir("instance/uploads")) >= 2)

# --- Admin: login, approve, reject, categories, suspend, reports -----------
client.get("/logout")
token = csrf(client, "/login")
r = client.post("/admin/login", data={"csrf_token": token, "email": "admin@fundimtaani.co.ke",
                                      "password": "admin1234"}, follow_redirects=False)
check("admin login works", r.status_code == 302 and "/admin" in r.headers["Location"])
r = client.get("/admin")
check("admin dashboard 200", r.status_code == 200)
check("dashboard shows pending applicant", b"Daniel Mwangi" in r.data)
check("dashboard shows ID number", b"31234567" in r.data)
check("dashboard has no feature buttons", b"Feature" not in r.data and b"Unfeature" not in r.data)
check("dashboard has no commission wording", b"commission tracking" not in r.data.lower() and b"success fee" not in r.data.lower())
r = client.get("/admin/reports")
check("reports page 200", r.status_code == 200)
check("reports show fundis by trade", b"Plumbing" in r.data)
check("reports show bookings by status", b"Completed" in r.data)
r = client.get("/admin/customers")
check("customers page 200", r.status_code == 200)
r = client.get("/admin/categories")
check("categories page 200", r.status_code == 200)
r = client.get("/admin/export/bookings")
check("bookings CSV export 200", r.status_code == 200 and b"Customer Name" in r.data)
r = client.get("/admin/export/workers")
check("workers CSV export 200", r.status_code == 200 and b"Owner Email" in r.data)

# Approve Daniel
r = client.post(f"/admin/worker/{daniel_id}/approve", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("admin approves pending application", b"approved and is now live" in r.data)
with app.app_context():
    check("Daniel is Approved", Worker.query.get(daniel_id).status == Worker.STATUS_APPROVED)
r = client.get("/fundis")
check("newly approved fundi now listed", b"Daniel Mwangi" in r.data)

# Category CRUD: add, rename, delete-blocked, delete
token = csrf(client, "/login")
r = client.post("/admin/categories", data={"csrf_token": token, "name": "Tiling"},
                follow_redirects=True)
check("category added", b"Tiling" in r.data)
with app.app_context():
    tiling = Category.query.filter_by(name="Tiling").first()
    tiling_id = tiling.id
    plumbing = Category.query.filter_by(name="Plumbing").first()
    plumbing_id = plumbing.id
r = client.post(f"/admin/category/{tiling_id}/edit", data={"csrf_token": csrf(client, "/login"), "name": "Tiling & Flooring"},
                follow_redirects=True)
check("category renamed", b"Tiling &amp; Flooring" in r.data or b"Tiling & Flooring" in r.data)
r = client.post(f"/admin/category/{plumbing_id}/delete", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("delete blocked while fundis use it", b"Cannot delete" in r.data)
with app.app_context():
    check("plumbing still exists", Category.query.get(plumbing_id) is not None)
r = client.post(f"/admin/category/{tiling_id}/delete", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("unused category deleted", b"deleted" in r.data)

# Suspend the test customer -> their pending fundi profile hidden; reactivate restores.
# The admin stays logged in on the main client throughout; a second client
# is used for the suspended customer's login attempt, so the admin session
# is never disturbed (and the 3/hour admin-login limit is not hit).
client2 = app.test_client()
with app.app_context():
    test_user_id = User.query.filter_by(email="test@example.com").first().id
r = client.post(f"/admin/customer/{test_user_id}/suspend", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("customer suspended", b"suspended" in r.data)
token = csrf(client2, "/login")
r = client2.post("/login", data={"csrf_token": token, "email": "test@example.com", "password": "secret12"},
                 follow_redirects=True)
check("suspended user cannot log in", b"suspended" in r.data)
# Reactivate
r = client.post(f"/admin/customer/{test_user_id}/reactivate", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("customer reactivated", b"reactivated" in r.data)

# Suspend a fundi owner -> fundi hidden from directory
with app.app_context():
    james_user = User.query.filter_by(email="james.mwangi@fundimtaani.co.ke").first()
    james_user_id = james_user.id
r = client.post(f"/admin/customer/{james_user_id}/suspend", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("fundi owner suspended", b"suspended" in r.data)
r = client.get("/fundis")
check("suspended fundi hidden from directory", b"James Mwangi" not in r.data)
r = client.post(f"/admin/customer/{james_user_id}/reactivate", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
r = client.get("/fundis")
check("reactivated fundi listed again", b"James Mwangi" in r.data)

# Admin review removal recalculates the rating
with app.app_context():
    rev_id = Review.query.filter_by(booking_id=booking_id).first().id
    before = Worker.query.get(james_id).avg_rating
r = client.post(f"/admin/review/{rev_id}/delete", data={"csrf_token": csrf(client, "/login")},
                follow_redirects=True)
check("admin deletes review", b"recalculated" in r.data)
with app.app_context():
    after = Worker.query.get(james_id).avg_rating
    check("rating recalculated after removal", abs(after - 5.0) < 0.01 and before == 5.0,
          f"before={before} after={after}")

# Reject flow with reason (new applicant). The demo customer applies on a
# second client; the admin (still logged in on the main client) rejects.
client2 = app.test_client()
login_as(client2, "demo@fundimtaani.co.ke", "demo1234")
r = client2.get("/join")
check("demo customer can open join", r.status_code == 200)
token = csrf(client2, "/login")
r = client2.post("/join", data={
    "csrf_token": token, "action": "submit", "category_id": "2", "estate": "Madaraka",
    "rate": "KSh 1,000", "experience": "4 years", "bio": "Demo electrician.",
    "national_id": "30112233",
    "photo": (io.BytesIO(png), "photo.png"),
    "id_document": (io.BytesIO(png), "id.png")},
    content_type="multipart/form-data", follow_redirects=True)
check("demo customer applies", b"Application Submitted" in r.data)
with app.app_context():
    demo_worker = User.query.filter_by(email="demo@fundimtaani.co.ke").first().fundi_profile
    demo_worker_id = demo_worker.id
r = client.post(f"/admin/worker/{demo_worker_id}/reject", data={
    "csrf_token": csrf(client, "/login"), "reason": "ID photo was too blurry to read."},
    follow_redirects=True)
check("admin rejects with reason", b"rejected" in r.data)
with app.app_context():
    w = Worker.query.get(demo_worker_id)
    check("rejection reason recorded", w.status == Worker.STATUS_REJECTED and "blurry" in w.rejection_reason)

# Demo outbox got the emails
outbox = open("instance/dev-outbox.txt").read() if os.path.exists("instance/dev-outbox.txt") else ""
check("outbox has verification email", "verification code" in outbox)
check("outbox has booking email", "has booked you" in outbox)
check("outbox has approval email", "has been approved" in outbox)

# Non-admin cannot reach admin pages
client.get("/logout")
r = client.get("/admin", follow_redirects=False)
check("visitor redirected from admin", r.status_code == 302 and "/admin/login" in r.headers["Location"])
login_as(client, "test@example.com", "secret12")
r = client.get("/admin", follow_redirects=False)
check("customer redirected from admin", r.status_code == 302 and "/admin/login" in r.headers["Location"])
r = client.get("/admin/reports", follow_redirects=False)
check("customer redirected from reports", r.status_code == 302)

# FR03 availability filter (James Mwangi toggles his own availability, FR17)
client3 = app.test_client()
r = client3.get("/fundis?available=1")
check("available filter lists James", b"James Mwangi" in r.data)
login_as(client3, "james.mwangi@fundimtaani.co.ke", "fundi1234")
with app.app_context():
    james = Worker.query.filter_by(name="James Mwangi").first()
    james_id, james_rate, james_exp, james_bio = james.id, james.rate, james.experience, james.bio
# Untick availability (a checkbox sends nothing when unticked = off)
r = client3.post("/fundi/edit", data={
    "csrf_token": csrf(client3, "/fundi/edit"),
    "estate": "Langata", "rate": james_rate, "experience": james_exp, "bio": james_bio},
    follow_redirects=True)
check("fundi can switch availability off", b"profile has been updated" in r.data)
with app.app_context():
    check("James now unavailable in DB", Worker.query.get(james_id).is_available is False)
r = client3.get("/fundis?available=1")
check("unavailable James hidden by available filter", b"James Mwangi" not in r.data)
r = client3.get("/fundis")
check("unavailable James still listed without filter", b"James Mwangi" in r.data)
# Switch back on
r = client3.post("/fundi/edit", data={
    "csrf_token": csrf(client3, "/fundi/edit"),
    "estate": "Langata", "rate": james_rate, "experience": james_exp, "bio": james_bio,
    "is_available": "1"},
    follow_redirects=True)
check("fundi can switch availability back on", b"profile has been updated" in r.data)
r = client3.get("/fundis?available=1")
check("James listed again by available filter", b"James Mwangi" in r.data)

print()
print(f"PASSED {len(PASS)}  FAILED {len(FAIL)}")
if FAIL:
    print("Failures:", FAIL)
    sys.exit(1)
