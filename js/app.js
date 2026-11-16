// FundiMtaani - app.js


//where everything is stored
var STORE_KEY = "fundi-mtaani";

// Read the stored data, or start fresh if there is none
function loadStore() {
  try {
    var raw = localStorage.getItem(STORE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* storage is empty or broken, start fresh */ }
  return {
    users: [
      // Demo account, so you can test login straight away
      { name: "Starford Omwocha", email: "starford@fundimtaani.me", phone: "254799242459", estate: "Langata", password: "fundimtaani123", verified: true }
    ],
    bookings: [],
    applications: [],
    nextBookingId: 1
  };
}

// Save the whole store back to the browser
function saveStore(store) {
  localStorage.setItem(STORE_KEY, JSON.stringify(store));
}

// Who is logged in right now? Saved separately, so a
// refresh does not log you out.
function currentUserEmail() {
  return localStorage.getItem("fundimtaani-manual-session");
}
function logIn(email) {
  localStorage.setItem("fundimtaani-manual-session", email);
}
function findUser(store, email) {
  email = (email || "").toLowerCase().trim();
  for (var i = 0; i < store.users.length; i++) {
    if (store.users[i].email.toLowerCase() === email) return store.users[i];
  }
  return null;
}

// Make a random 6-digit code, e.g. "483920"
function makeCode() {
  return String(Math.floor(100000 + Math.random() * 900000));
}

// Show a short message under a form
function showMessage(id, text) {
  var el = document.getElementById(id);
  if (el) el.textContent = text;
}

// Run when the page has loaded
document.addEventListener("DOMContentLoaded", function () {
  var store = loadStore();

  // ----- REGISTER -----
  var registerForm = document.getElementById("register-form");
  if (registerForm) {
    registerForm.addEventListener("submit", function (event) {
      event.preventDefault(); // stop the page reloading
      var name = document.getElementById("fullname").value.trim();
      var email = document.getElementById("email").value.trim();
      var phone = document.getElementById("phone").value.trim();
      var estate = document.getElementById("estate").value;
      var password = document.getElementById("password").value;
      var confirm = document.getElementById("confirm-password").value;
      if (password !== confirm) {
        showMessage("register-message", "Passwords do not match. Type the same password twice.");
        return;
      }
      if (findUser(store, email)) {
        showMessage("register-message", "That email already has an account. Log in instead.");
        return;
      }
      var code = makeCode();
      store.users.push({ name: name, email: email, phone: phone, estate: estate, password: password, verified: false, verifyCode: code });
      saveStore(store);
      showMessage("register-message", "Account created. Now verify your email below.");
      showMessage("demo-code", "Demo only - your verification code is: " + code);
      document.getElementById("verify-form").scrollIntoView();
    });
  }

  // ----- VERIFY EMAIL -----
  var verifyForm = document.getElementById("verify-form");
  if (verifyForm) {
    verifyForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var code = document.getElementById("code").value.trim();
      var user = null;
      for (var i = 0; i < store.users.length; i++) {
        if (store.users[i].verifyCode === code) user = store.users[i];
      }
      if (!user) {
        showMessage("verify-message", "That code does not match. Check the code shown above.");
        return;
      }
      user.verified = true;
      delete user.verifyCode;
      saveStore(store);
      showMessage("verify-message", "Email verified. Taking you to login...");
      setTimeout(function () { window.location.href = "login.html"; }, 1000);
    });
  }

  // ----- LOGIN WITH PASSWORD -----
  var loginForm = document.getElementById("login-password-form");
  if (loginForm) {
    loginForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var email = document.getElementById("login-email").value.trim();
      var password = document.getElementById("login-password").value;
      var user = findUser(store, email);
      if (!user || user.password !== password) {
        showMessage("login-message", "Wrong email or password. Try again, or use a login code below.");
        return;
      }
      if (!user.verified) {
        showMessage("login-message", "Verify your email first. Register again to get a new code.");
        return;
      }
      logIn(user.email);
      window.location.href = "bookings.html";
    });
  }

  // ----- LOGIN WITH CODE: step 1, ask for a code -----
  var codeRequestForm = document.getElementById("login-code-request-form");
  if (codeRequestForm) {
    codeRequestForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var email = document.getElementById("code-email").value.trim();
      var user = findUser(store, email);
      if (!user) {
        showMessage("login-code-message", "No account with that email. Register first.");
        return;
      }
      user.loginCode = makeCode();
      saveStore(store);
      showMessage("login-code-message", "Demo only - your login code is: " + user.loginCode);
    });
  }

  // ----- LOGIN WITH CODE: step 2, enter the code -----
  var codeForm = document.getElementById("login-code-form");
  if (codeForm) {
    codeForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var code = document.getElementById("login-code").value.trim();
      var user = null;
      for (var i = 0; i < store.users.length; i++) {
        if (store.users[i].loginCode === code) user = store.users[i];
      }
      if (!user) {
        showMessage("login-code-message", "That login code does not match. Ask for a new one.");
        return;
      }
      delete user.loginCode;
      saveStore(store);
      logIn(user.email);
      window.location.href = "bookings.html";
    });
  }

  // ----- BOOK A FUNDI -----
  var bookForm = document.getElementById("book-form");
  if (bookForm) {
    var email = currentUserEmail();
    if (!email) {
      showMessage("book-message", "Log in first to book. Taking you to login...");
      setTimeout(function () { window.location.href = "login.html"; }, 1500);
    }
    bookForm.addEventListener("submit", function (event) {
      event.preventDefault();
      if (!currentUserEmail()) {
        window.location.href = "login.html";
        return;
      }
      var booking = {
        id: store.nextBookingId++,
        customer: currentUserEmail(),
        fundi: document.getElementById("fundi").value,
        service: document.getElementById("service").value.trim(),
        date: document.getElementById("date").value,
        time: document.getElementById("time").value,
        estate: document.getElementById("estate").value,
        address: document.getElementById("address").value.trim(),
        phone: document.getElementById("phone").value.trim(),
        status: "Pending"
      };
      store.bookings.push(booking);
      saveStore(store);
      window.location.href = "bookings.html";
    });
  }

  // ----- MY BOOKINGS: show the bookings I saved -----
  var bookingsList = document.getElementById("bookings-list");
  if (bookingsList) {
    var me = currentUserEmail();
    if (!me) {
      bookingsList.textContent = "Log in to see your bookings.";
    } else {
      var mine = store.bookings.filter(function (b) { return b.customer === me; });
      if (mine.length === 0) {
        bookingsList.textContent = "You have no bookings saved yet. Book a fundi to see one here.";
      } else {
        var html = "<h3>Your saved bookings</h3><ul>";
        mine.forEach(function (b) {
          html += "<li><strong>" + b.fundi + "</strong> - " + b.service +
            " - " + b.date + " (" + b.time + ") - " + b.status + "</li>";
        });
        html += "</ul>";
        bookingsList.innerHTML = html;
      }
    }
    // Confirm completion: a Job Done booking becomes Completed
    var confirmButtons = document.querySelectorAll("[data-confirm-booking]");
    confirmButtons.forEach(function (btn) {
      btn.addEventListener("click", function (event) {
        event.preventDefault();
        showMessage("bookings-message", "In the saved version, only your own bookings can be confirmed. This sample row is an example.");
      });
    });
  }

  // ----- JOIN AS FUNDI -----
  var joinForm = document.getElementById("join-form");
  if (joinForm) {
    joinForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var app = {
        name: document.getElementById("fullname").value.trim(),
        email: document.getElementById("email").value.trim(),
        trade: document.getElementById("trade").value,
        estate: document.getElementById("estate").value,
        experience: document.getElementById("experience").value,
        status: "Pending"
      };
      store.applications.push(app);
      saveStore(store);
      showMessage("join-message", "Application sent. Your profile stays hidden until Admin approves it.");
      joinForm.reset();
    });
  }

  // ----- FUNDIS FILTER: hide cards that do not match the web address -----
  var filterForm = document.getElementById("filter-form");
  if (filterForm) {
    var params = new URLSearchParams(window.location.search);
    var q = (params.get("q") || "").toLowerCase();
    var trade = (params.get("trade") || "").toLowerCase();
    var estate = (params.get("estate") || "").toLowerCase();
    var cards = document.querySelectorAll(".fundi-card");
    var shown = 0;
    cards.forEach(function (card) {
      var text = card.textContent.toLowerCase();
      var ok = true;
      if (q && text.indexOf(q) === -1) ok = false;
      if (trade && text.indexOf(trade) === -1) ok = false;
      if (estate && text.indexOf(estate) === -1) ok = false;
      card.style.display = ok ? "" : "none";
      if (ok) shown++;
    });
    var count = document.getElementById("fundis-count");
    if (count) count.textContent = "Showing " + shown + " of " + cards.length + " fundis.";
  }
});
