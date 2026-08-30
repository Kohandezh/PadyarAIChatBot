// Shared "page size + prev/next" control, used by any admin list/table.
//
// Mirrors the pattern already hand-rolled on the conversations/visitors/logs
// pages (a #page-size select, #btn-prev/#btn-next, a range label) so every
// page reads the same way to an operator, instead of reinventing pagination
// per screen. Works for both:
//   - server-side paging: onPage(offset, limit) fetches a page from the API
//   - client-side paging: onPage(offset, limit) slices an already-loaded array
// The caller owns fetching/rendering; this only owns the offset/limit state
// and the prev/next/page-size wiring.

export function createPager({ pageSizeEl, prevBtnEl, nextBtnEl, rangeEl, defaultLimit = 25, onPage }) {
  const state = { limit: defaultLimit, offset: 0 };

  if (pageSizeEl) {
    const initial = parseInt(pageSizeEl.value, 10);
    if (initial > 0) state.limit = initial;
    pageSizeEl.addEventListener('change', () => {
      state.limit = parseInt(pageSizeEl.value, 10) || defaultLimit;
      state.offset = 0;
      onPage(state.offset, state.limit);
    });
  }
  if (prevBtnEl) {
    prevBtnEl.addEventListener('click', () => {
      state.offset = Math.max(0, state.offset - state.limit);
      onPage(state.offset, state.limit);
    });
  }
  if (nextBtnEl) {
    nextBtnEl.addEventListener('click', () => {
      state.offset += state.limit;
      onPage(state.offset, state.limit);
    });
  }

  // Call after each render with what came back, so prev/next and the range
  // label reflect the page actually shown (not just the requested offset).
  function setResult({ shown = 0, total, hasMore }) {
    if (prevBtnEl) prevBtnEl.disabled = state.offset <= 0;
    if (nextBtnEl) {
      nextBtnEl.disabled = hasMore !== undefined
        ? !hasMore
        : (total !== undefined ? state.offset + shown >= total : shown < state.limit);
    }
    if (rangeEl) {
      if (!shown) {
        rangeEl.textContent = 'موردی نیست';
      } else {
        const from = state.offset + 1;
        const to = state.offset + shown;
        rangeEl.textContent = total !== undefined
          ? `${from} تا ${to} از ${total}`
          : `${from} تا ${to}`;
      }
    }
  }

  // Back to page 1 — call before reloading after a filter/search change.
  function reset() {
    state.offset = 0;
  }

  return { state, setResult, reset, load: () => onPage(state.offset, state.limit) };
}
