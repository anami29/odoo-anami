/** @odoo-module **/
/**
 * Page-by-page navigation on the portal agreement.
 *
 * A twelve-page contract stacked vertically buries the signing panel, so only one
 * page is shown at a time. Every page is already in the DOM — navigating hides and
 * shows, it never fetches — so the per-page signature pads keep their own callUrl
 * and nothing has to be rebuilt as you move.
 */

const STORE_KEY = 'agreement_portal_page';

function initPager(root) {
    const pages = Array.from(root.querySelectorAll('.o_agreement_page'));
    if (!pages.length) {
        return;
    }
    const label = root.querySelector('.o_agreement_pager_label');
    const prev = root.querySelector('.o_agreement_prev');
    const next = root.querySelector('.o_agreement_next');
    let current = 0;

    function show(index) {
        current = Math.max(0, Math.min(pages.length - 1, index));
        pages.forEach((page, i) => page.classList.toggle('d-none', i !== current));
        if (label) {
            label.textContent = `Page ${current + 1} / ${pages.length}`;
        }
        if (prev) {
            prev.disabled = current === 0;
        }
        if (next) {
            next.disabled = current === pages.length - 1;
        }
        try {
            // signing a page reloads the document, so come back where they were
            sessionStorage.setItem(STORE_KEY, String(current));
        } catch {
            // private browsing, or storage blocked — paging still works
        }
    }

    prev?.addEventListener('click', () => show(current - 1));
    next?.addEventListener('click', () => show(current + 1));

    let start = 0;
    try {
        start = parseInt(sessionStorage.getItem(STORE_KEY) || '0', 10) || 0;
    } catch {
        start = 0;
    }
    // prefer the first page still awaiting a signature, if there is one
    const firstUnsigned = pages.findIndex((p) => p.querySelector('.o_portal_sign_form, owl-component'));
    show(firstUnsigned >= 0 && !start ? firstUnsigned : start);
}

function setup() {
    document.querySelectorAll('.o_agreement_pager').forEach(initPager);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
} else {
    setup();
}
