import { fetchAuth, showMsg } from './utils.js';

async function loadThemes() {
    const res = await fetchAuth('/admin/api/themes');
    if (!res.ok) {
        showMsg('theme-msg', '❌ خطا در بارگذاری قالب‌ها', 'danger');
        return;
    }
    const data = await res.json();
    renderThemes(data.themes);
}

function renderThemes(themes) {
    const grid = document.getElementById('themes-grid');
    if (!themes.length) {
        grid.innerHTML = '<div class="col-12 text-center py-4 text-muted">قالبی یافت نشد</div>';
        return;
    }

    grid.innerHTML = themes.map(theme => {
        const colors = theme.preview_colors || {};
        const colorDots = Object.values(colors).map(c =>
            `<span class="color-dot" style="background:${c}"></span>`
        ).join('');

        const isActive = theme.is_active;
        const cardClass = isActive ? 'theme-card active-theme' : 'theme-card';

        const actionBtn = isActive
            ? `<span class="active-badge"><i class="fas fa-check-circle"></i> فعال</span>`
            : `<button class="btn btn-primary btn-sm" onclick="window.activateTheme('${theme.name}')">
                   <i class="fas fa-power-off me-1"></i>فعال‌سازی
               </button>`;

        return `
            <div class="col-md-6 col-lg-4 mb-4">
                <div class="${cardClass}">
                    <img class="screenshot"
                         src="/themes/${theme.name}/${theme.screenshot}"
                         alt="${theme.display_name}"
                         onerror="this.style.background='#ddd'; this.src='data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%221200%22 height=%22900%22><rect fill=%22%23ddd%22 width=%221200%22 height=%22900%22/></svg>'">
                    <div class="card-info">
                        <div class="theme-name">${theme.display_name}</div>
                        <div class="theme-desc">${theme.description}</div>
                        <div class="color-dots">${colorDots}</div>
                        <div class="theme-footer">
                            <small class="text-muted">${theme.version || ''} ${theme.author ? '• ' + theme.author : ''}</small>
                            ${actionBtn}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

async function activateTheme(name) {
    try {
        const res = await fetchAuth('/admin/api/themes/activate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });
        if (res.ok) {
            showMsg('theme-msg', '✅ قالب فعال شد', 'success');
            loadThemes();
        } else {
            const data = await res.json();
            showMsg('theme-msg', '❌ ' + (data.detail || 'خطا در فعال‌سازی'), 'danger');
        }
    } catch {
        showMsg('theme-msg', '❌ خطای ارتباط با سرور', 'danger');
    }
}

export function initThemes() {
    loadThemes();
    window.activateTheme = activateTheme;
}
