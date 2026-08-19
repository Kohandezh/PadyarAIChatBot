import { API_BASE } from './state.js';

export async function logout() {
    await fetch('/admin/logout', { method: 'POST', credentials: 'include' });
    window.location.href = '/secure-panel-inotex/login';
}

export async function reloadDataset() {
    const msg = document.getElementById('action-msg');
    if (!msg) {
        alert('این عملیات فقط از داشبورد قابل اجراست.');
        return;
    }
    msg.className = 'text-center fw-bold mt-2 text-warning';
    msg.innerText = '⏳ در حال بارگذاری مجدد دیتاست...';

    const res = await fetch(API_BASE + '/reload_dataset', { method: 'POST', credentials: 'include' });
    if (res.ok) {
        msg.className = 'text-center fw-bold mt-2 text-success';
        msg.innerText = '✅ دیتاست با موفقیت آپدیت شد';
        setTimeout(() => msg.innerText = '', 3000);
    } else {
        msg.className = 'text-center fw-bold mt-2 text-danger';
        msg.innerText = '❌ خطا در آپدیت دیتاست';
    }
}

export async function downloadCSV() {
    const res = await fetch(API_BASE + '/export_csv', { credentials: 'include' });
    if (res.ok) {
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `inotex-export-${new Date().toLocaleDateString()}.csv`;
        document.body.appendChild(a);
        a.click();
        a.remove();
    }
}

export function initLogin() {
    document.getElementById('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const username = document.getElementById('username').value;
        const password = document.getElementById('password').value;
        const sec_answer = document.getElementById('sec-answer').value;
        const errorDiv = document.getElementById('login-error');

        errorDiv.innerText = '';

        try {
            const res = await fetch('/admin/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ username, password, sec_answer })
            });

            if (res.ok) {
                window.location.href = '/secure-panel-inotex';
            } else if (res.status === 429) {
                const data = await res.json();
                errorDiv.innerText = '⚠️ ' + data.detail;
            } else {
                errorDiv.innerText = 'اطلاعات ورود اشتباه است';
            }
        } catch (err) {
            console.error(err);
            errorDiv.innerText = 'خطای ارتباط با سرور';
        }
    });
}
