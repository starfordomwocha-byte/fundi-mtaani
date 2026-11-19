// main.js — the small bits of JavaScript FundiMtaani needs.
//
// Only three jobs live here:
//   1. The light/dark theme toggle (remembered in localStorage).
//   2. The WhatsApp message composer on a fundi's profile page (FR05):
//      pick a message template or type your own, and the WhatsApp link
//      updates to carry that exact message.
//   3. The live search box on the admin dashboard, which filters the
//      visible table rows as the admin types.
//
// Everything the old file did that no FR asks for is gone: the saved-
// fundis/favourites system, the cost calculator, and the profile share
// tools (with their QR code) were removed with those features.

/* Theme Manager (Immediate execution to prevent flash) */
(function() {
  const savedTheme = localStorage.getItem('fundimtaani_theme');
  const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  const activeTheme = savedTheme || (prefersDark ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', activeTheme);
})();

document.addEventListener('DOMContentLoaded', () => {
  initThemeSystem();
  initWhatsAppComposer();
  initAdminTableFilters();
});

/* ==========================================================================
   THEME SWITCHING SYSTEM (Dark / Light Mode)
   ========================================================================== */

function initThemeSystem() {
  const toggleBtns = document.querySelectorAll('.btn-theme-toggle');

  toggleBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
      const newTheme = currentTheme === 'dark' ? 'light' : 'dark';

      document.documentElement.setAttribute('data-theme', newTheme);
      localStorage.setItem('fundimtaani_theme', newTheme);
    });
  });
}

/* ==========================================================================
   INTERACTIVE WHATSAPP MESSAGE COMPOSER (fundi profile page)
   ========================================================================== */

function initWhatsAppComposer() {
  const container = document.getElementById('whatsappComposer');
  if (!container) return;

  const phoneDigits = container.dataset.phoneDigits || '';
  const workerName = container.dataset.workerName || '';
  const workerTrade = container.dataset.workerTrade || '';
  const workerEstate = container.dataset.workerEstate || '';
  const waButton = document.getElementById('composerWaLink');
  const customInput = document.getElementById('composerCustomText');
  const templateButtons = container.querySelectorAll('.wa-template-btn');

  function updateLink(messageText) {
    if (!waButton) return;
    const encoded = encodeURIComponent(messageText);
    waButton.href = `https://wa.me/${phoneDigits}?text=${encoded}`;
  }

  templateButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      templateButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const templateType = btn.dataset.template;
      let msg = '';

      if (templateType === 'urgent') {
        msg = `Hello ${workerName}, I need urgent assistance with ${workerTrade.toLowerCase()} in ${workerEstate}. Are you available today?`;
      } else if (templateType === 'quote') {
        msg = `Hello ${workerName}, I found you on FundiMtaani. Could you share a price estimate for a ${workerTrade.toLowerCase()} job in ${workerEstate}?`;
      } else if (templateType === 'weekend') {
        msg = `Hello ${workerName}, I would like to schedule a ${workerTrade.toLowerCase()} visit for this coming weekend in ${workerEstate}.`;
      } else {
        msg = `Hello ${workerName}, I found your listing on FundiMtaani regarding ${workerTrade.toLowerCase()} service in ${workerEstate}.`;
      }

      if (customInput) customInput.value = msg;
      updateLink(msg);
    });
  });

  if (customInput) {
    customInput.addEventListener('input', () => {
      updateLink(customInput.value);
    });
  }
}

/* ==========================================================================
   ADMIN TABLE LIVE SEARCH & FILTERS
   ========================================================================== */

function initAdminTableFilters() {
  const searchInput = document.getElementById('adminLiveSearch');
  if (!searchInput) return;

  searchInput.addEventListener('input', () => {
    const query = searchInput.value.toLowerCase().trim();
    const rows = document.querySelectorAll('.admin-filterable-row');

    rows.forEach(row => {
      const text = row.textContent.toLowerCase();
      row.style.display = text.includes(query) ? '' : 'none';
    });
  });
}

/* ==========================================================================
   Helper Utilities
   ========================================================================== */

function escapeHtml(string) {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(string));
  return div.innerHTML;
}
