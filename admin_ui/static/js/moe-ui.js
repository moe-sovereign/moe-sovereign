/* SPDX-License-Identifier: Apache-2.0 */
/* Small, opt-in enhancements shared by Admin and Portal. */
(() => {
  'use strict';
  document.addEventListener('DOMContentLoaded', () => {
    const navigation = document.getElementById('portal-navigation');
    if (navigation) {
      const mobile = matchMedia('(max-width: 767.98px)');
      const fitNavigation = () => { navigation.open = !mobile.matches; };
      fitNavigation();
      mobile.addEventListener('change', fitNavigation);
      navigation.querySelector('.sidebar-link.active')?.setAttribute('aria-current', 'page');
    }

    document.querySelectorAll('[data-list-tools]').forEach(toolbar => {
      const search = toolbar.querySelector('input[type="search"]');
      const density = toolbar.querySelector('[data-list-density]');
      const privacy = toolbar.querySelector('[data-list-privacy]');
      const count = toolbar.querySelector('[data-list-count]');
      const empty = toolbar.querySelector('[data-list-empty]');
      const rows = () => [...document.querySelectorAll(toolbar.dataset.target)];
      // Search visible record content and editable identifiers, never credentials.
      const searchable = row => [row.textContent, ...[...row.querySelectorAll('input')]
        .filter(input => /srv_(name|url)_/.test(input.name)).map(input => input.value)]
        .join(' ').toLocaleLowerCase();
      function filter() {
        const records = rows();
        const term = search.value.trim().toLocaleLowerCase();
        let visible = 0;
        records.forEach(row => {
          row.hidden = Boolean((term && !searchable(row).includes(term)) || (privacy?.value && row.dataset.privacy !== privacy.value));
          if (!row.hidden) visible++;
        });
        count.textContent = `${visible} / ${records.length}`;
        empty.hidden = !term || visible > 0;
      }
      function setDensity() {
        const first = rows()[0];
        const target = first?.closest('table') || first?.parentElement;
        target?.classList.toggle('moe-compact', density.value === 'compact');
      }
      toolbar.hidden = false;
      search.addEventListener('input', filter);
      search.addEventListener('keydown', event => {
        // Searching inside the global configuration form must never save it.
        if (event.key === 'Enter') event.preventDefault();
      });
      privacy?.addEventListener('change', filter);
      density.addEventListener('change', setDensity);
      // Adding a record reveals it, even when the previous search did not match.
      toolbar.closest('form')?.addEventListener('moe:form-change', () => {
        search.value = ''; filter(); setDensity();
      });
      filter();
    });

    const form = document.getElementById('config-form');
    const bar = document.getElementById('config-save-bar');
    if (!form || !bar) return;
    const status = document.getElementById('config-save-status');
    const save = document.getElementById('save-btn');
    const discard = document.getElementById('config-discard');
    let baseline, dirty = false, submitting = false;
    // Keep original field values only in memory; never put configuration in storage.
    const snapshot = () => JSON.stringify([...new FormData(form).entries()]
      .filter(([name]) => name !== 'csrf_token').sort(([a], [b]) => a.localeCompare(b)));
    function update() {
      if (baseline === undefined || submitting) return;
      dirty = snapshot() !== baseline;
      bar.classList.toggle('is-dirty', dirty);
      status.textContent = dirty ? bar.dataset.dirty : bar.dataset.clean;
    }
    // Existing endpoint selectors finish their setup in DOMContentLoaded handlers.
    setTimeout(() => { baseline = snapshot(); update(); }, 0);
    ['input', 'change', 'moe:form-change'].forEach(type => form.addEventListener(type, update));
    discard.addEventListener('click', event => {
      if (dirty && !confirm(bar.dataset.discard)) { event.preventDefault(); return; }
      submitting = true;
    });
    window.addEventListener('beforeunload', event => {
      if (dirty && !submitting) { event.preventDefault(); event.returnValue = ''; }
    });
    form.addEventListener('submit', event => {
      if (event.defaultPrevented) return;
      submitting = true;
      save.disabled = true;
      save.textContent = bar.dataset.saving;
      status.textContent = bar.dataset.saving;
      form.setAttribute('aria-busy', 'true');
    });
    const saveLabel = save.innerHTML;
    window.addEventListener('pageshow', event => {
      if (!event.persisted) return;
      submitting = false; save.disabled = false; save.innerHTML = saveLabel;
      form.removeAttribute('aria-busy'); update();
    });

    const sections = document.getElementById('config-section');
    form.querySelectorAll(':scope > .row > div > .card').forEach((card, index) => {
      const header = card.querySelector('.card-header');
      if (!header) return;
      card.id ||= `config-section-${index}`;
      const title = (header.querySelector('span') || header).textContent.trim();
      sections?.add(new Option(title, card.id));
    });
    if (sections) {
      sections.parentElement.hidden = false;
      sections.addEventListener('change', () => {
        const target = document.getElementById(sections.value);
        if (!target) return;
        target.tabIndex = -1;
        target.focus({preventScroll: true});
        target.scrollIntoView({block: 'start'});
      });
    }
  });
})();
