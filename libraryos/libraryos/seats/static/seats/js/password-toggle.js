/* Adds a show/hide (eye) button inside every password field. */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('input[type="password"]').forEach(input => {
    const wrap = document.createElement('div');
    wrap.className = 'relative';
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    input.classList.add('pr-10');

    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'absolute inset-y-0 right-0 px-3 text-slate-400 hover:text-slate-600';
    btn.setAttribute('aria-label', 'Show password');
    btn.innerHTML = '<i class="fa-solid fa-eye"></i>';
    btn.addEventListener('click', () => {
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      btn.firstChild.className = show ? 'fa-solid fa-eye-slash' : 'fa-solid fa-eye';
    });
    wrap.appendChild(btn);
  });
});

/* Back/forward cache: never show a restored page from before sign-out. */
window.addEventListener('pageshow', e => { if (e.persisted) location.reload(); });
