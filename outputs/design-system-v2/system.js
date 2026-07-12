document.querySelectorAll('[data-mega-toggle]').forEach(button => {
  button.addEventListener('click', () => {
    const menu = document.querySelector('[data-mega-menu]');
    const isOpen = menu?.classList.toggle('is-open');
    button.setAttribute('aria-expanded', String(Boolean(isOpen)));
  });
});

document.querySelectorAll('[data-mobile-open]').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelector('[data-mobile-panel]')?.classList.add('is-open');
    document.body.style.overflow = 'hidden';
  });
});

document.querySelectorAll('[data-mobile-close]').forEach(button => {
  button.addEventListener('click', () => {
    document.querySelector('[data-mobile-panel]')?.classList.remove('is-open');
    document.body.style.overflow = '';
  });
});

document.querySelectorAll('[data-page-tab]').forEach(tab => {
  tab.addEventListener('click', event => {
    event.preventDefault();
    const frame = document.querySelector('[data-page-frame]');
    if (frame) frame.src = tab.getAttribute('href');
    document.querySelectorAll('[data-page-tab]').forEach(item => item.classList.remove('is-active'));
    tab.classList.add('is-active');
  });
});

