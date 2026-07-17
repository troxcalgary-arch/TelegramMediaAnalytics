// Telegram Analytics Frontend
// Handles connect (auth), scan, download, pagination, filters

let authToken = localStorage.getItem('tg_auth_token') || null;
let currentResults = [];
let currentPage = 1;
const DEFAULT_ROWS_PER_PAGE = 10;
let currentScanTaskId = null;
let currentAuthSessionId = null;
let selectedMessageIds = new Set();

// Loading overlay functions
function showLoading(text = "Подключение...") {
    const overlay = document.getElementById('loadingOverlay');
    const loadingText = document.getElementById('loadingText');
    if (overlay) {
        if (loadingText) loadingText.textContent = text;
        overlay.style.display = 'flex';
    }
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.style.display = 'none';
}

// Download progress overlay functions
function showDownloadProgress(message, progress = 0, done = 0, total = 0) {
    const overlay = document.getElementById('downloadOverlay');
    const bar = document.getElementById('downloadProgressBar');
    const text = document.getElementById('downloadProgressText');
    const pct = document.getElementById('downloadPercent');
    const title = document.getElementById('downloadTitle');
    if (overlay) {
        overlay.style.display = 'flex';
    }
    if (title) title.textContent = message;
    if (bar) bar.style.width = progress + '%';
    if (text) text.textContent = done + ' / ' + total;
    if (pct) pct.textContent = progress + '%';
}

function hideDownloadProgress() {
    const overlay = document.getElementById('downloadOverlay');
    if (overlay) overlay.style.display = 'none';
}

// ---------- Column resize for stats-table ----------
function initColumnResize(table, storageKey) {
    const key = storageKey || 'stats_table_col_widths';
    const ths = table.querySelectorAll('thead th');
    if (!ths.length) return;

    // Restore saved widths
    try {
        const saved = JSON.parse(localStorage.getItem(key));
        if (saved && saved.length === ths.length) {
            ths.forEach((th, i) => { if (saved[i]) th.style.width = saved[i]; });
        }
    } catch (_) {}

    ths.forEach((th, idx) => {
        // Skip hidden columns and checkbox column (first column)
        if (getComputedStyle(th).display === 'none' || idx === 0) return;

        const handle = document.createElement('div');
        handle.className = 'col-resize';
        th.appendChild(handle);

        let startX, startW;

        handle.addEventListener('mousedown', (e) => {
            e.preventDefault();
            startX = e.pageX;
            startW = th.offsetWidth;
            handle.classList.add('active');

            const onMove = (ev) => {
                const diff = ev.pageX - startX;
                th.style.width = Math.max(40, startW + diff) + 'px';
            };
            const onUp = () => {
                handle.classList.remove('active');
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                // Save all widths
                const widths = Array.from(ths).map(t => t.style.width || '');
                try { localStorage.setItem(key, JSON.stringify(widths)); } catch (_) {}
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        });
    });
}

// Error modal functions
function showErrorModal(errorText) {
    const modal = document.getElementById('errorModal');
    const detail = document.getElementById('errorDetail');
    if (modal && detail) {
        detail.textContent = errorText;
        modal.style.display = 'flex';
    }
}

function hideErrorModal() {
    const modal = document.getElementById('errorModal');
    if (modal) modal.style.display = 'none';
}

// Current filter state
let currentFilters = {
    channel_id: '-1001911644885',
    username: '',
    date_from: '',
    date_to: '',
    sort_by: 'date_desc',
    per_page: DEFAULT_ROWS_PER_PAGE
};

// Stats pagination state
let statsCurrentPage = 1;
let statsTotalPages = 1;

// Initialize flatpickr for date range
document.addEventListener('DOMContentLoaded', () => {
    // Initialize i18n
    i18n.init();

    // Language switcher
    document.querySelectorAll('.lang-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            i18n.lang = btn.dataset.lang;
            // Reload page to apply all translations
            location.reload();
        });
    });
    // Set initial active state
    document.querySelector(`.lang-btn[data-lang="${i18n.lang}"]`)?.classList.add('active');

    // Pre-fill from .env (served via /telegram/api/config)
    loadEnvConfig();
    
    // Date range picker (for scan form)
    const fp = flatpickr("#dateRange", {
        mode: "range",
        dateFormat: "Y-m-d",
        locale: "ru",
        onChange: (selectedDates) => {
            if (selectedDates.length === 2) {
                document.getElementById('days').value = '';
            }
        }
    });

    // Clear date range button
    document.getElementById('clearDateRange').addEventListener('click', () => {
        fp.clear();
    });
    
    // Folder picker button
    document.getElementById('pickFolderBtn').addEventListener('click', pickFolder);
    
    // Auth form handlers
    document.getElementById('connectBtn').addEventListener('click', handleConnect);
    document.getElementById('verifyCodeBtn').addEventListener('click', handleVerifyCode);
    document.getElementById('cancelAuthBtn').addEventListener('click', cancelAuth);
    document.getElementById('verify2FABtn').addEventListener('click', handleVerify2FA);
    document.getElementById('cancel2FABtn').addEventListener('click', cancel2FA);
    document.getElementById('logoutBtn').addEventListener('click', handleLogout);
    
    // Form handlers
    document.getElementById('scanForm').addEventListener('submit', handleScan);
    document.getElementById('connectForm').addEventListener('submit', (e) => e.preventDefault()); // Prevent default submit
    
    // Error modal close handler
    document.getElementById('errorModalClose').addEventListener('click', hideErrorModal);
    
    // Pagination (results)
    document.getElementById('prevPage').addEventListener('click', () => changePage(currentPage - 1));
    document.getElementById('nextPage').addEventListener('click', () => changePage(currentPage + 1));
    
    // Download button
    document.getElementById('downloadBtn').addEventListener('click', handleDownload);
    
    // Stats button handler
    document.getElementById('loadStatsBtn').addEventListener('click', loadStats);
    
    // Stats pagination handlers
    document.getElementById('statsPrevPage').addEventListener('click', () => changeStatsPage(statsCurrentPage - 1));
    document.getElementById('statsNextPage').addEventListener('click', () => changeStatsPage(statsCurrentPage + 1));
    
    // Stats filter changes reset to page 1
    document.getElementById('statsUsername').addEventListener('change', () => { statsCurrentPage = 1; loadStats(); });
    document.getElementById('statsSortBy').addEventListener('change', () => { statsCurrentPage = 1; loadStats(); });
    document.getElementById('statsRowsPerPage').addEventListener('change', () => { statsCurrentPage = 1; loadStats(); });
    
    // Filters
    document.getElementById('applyFiltersBtn').addEventListener('click', applyFilters);
    document.getElementById('rowsPerPage').addEventListener('change', applyFilters);
    document.getElementById('sortBy').addEventListener('change', applyFilters);
    
    // Load initial data if available
    loadSavedResults();
    loadSidebarHistory();
    checkForUpdates();

    // Check if already authenticated
    checkAuthStatus().then(() => checkActiveDownloads());
});

// Check authentication status on page load
async function checkAuthStatus() {
    if (!authToken) return;
    try {
        const res = await fetch('/telegram/api/session', {
            headers: { 'Authorization': 'Bearer ' + authToken },
            credentials: 'include'
        });
        if (res.ok) {
            // JWT is valid — show auth success
            showAuthSuccess({});
        } else if (res.status === 401) {
            // Token expired or invalid — clear it
            authToken = null;
            localStorage.removeItem('tg_auth_token');
        }
    } catch (e) {
        console.log('Auth check failed:', e);
    }
}

// Check for active download tasks (after page reload)
async function checkActiveDownloads() {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        const res = await fetch('/telegram/api/tasks/active', { headers, credentials: 'include' });
        if (!res.ok) return;
        const data = await res.json();
        const tasks = data.tasks || [];
        if (tasks.length === 0) return;
        for (const t of tasks) {
            showStatus('scanStatus',
                `⏳ ${i18n.t('status_active_download')} «${t.channel_title}» (${t.channel_id}) ${i18n.t('status_active_download_mid')} — ${t.message || '...'} (${t.progress || 0}%)`
            );
        }
    } catch (e) {
        console.log('Active tasks check failed:', e);
    }
}

// Load scan history into sidebar
async function loadSidebarHistory() {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        const res = await fetch('/telegram/api/scan-history?limit=3', { headers, credentials: 'include' });
        if (!res.ok) return;
        const data = await res.json();
        const history = data.history || [];
        const el = document.getElementById('scanHistorySidebar');
        if (!el) return;
        if (history.length === 0) {
            el.innerHTML = `<p style="color:#999; font-size:12px;">${i18n.t('history_empty')}</p>`;
            return;
        }
        const locale = i18n.lang === 'en' ? 'en-US' : 'ru-RU';
        let html = '';
        history.forEach(h => {
            const dt = new Date(h.timestamp).toLocaleString(locale);
            const msgs = i18n.lang === 'en' ? 'messages' : 'сообщений';
            const auths = i18n.lang === 'en' ? 'authors' : 'авторов';
            html += `<div style="padding:8px 0; border-bottom:1px solid #eee; font-size:12px;">`;
            html += `<div style="color:#555;">${dt}</div>`;
            html += `<div><strong>${h.total_messages || 0}</strong> ${msgs}, <strong>${h.unique_authors || 0}</strong> ${auths}</div>`;
            html += `<div style="color:#888; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${h.channel_id}">${h.channel_title || h.channel_id}</div>`;
            html += `</div>`;
        });
        el.innerHTML = html;
    } catch (e) {
        console.log('Sidebar history load failed:', e);
    }
}

// Check for application updates
async function checkForUpdates() {
    try {
        const res = await fetch('/telegram/api/version');
        if (!res.ok) return;
        const data = await res.json();
        if (data.update_available) {
            const el = document.getElementById('scanHistorySidebar');
            if (el) {
                const updateHtml = `<div style="margin-top:10px; padding:8px 10px; background:#fff3cd; border:1px solid #ffc107; border-radius:6px; font-size:12px;">
                    ⬆️ <strong>${i18n.t('update_available')}</strong>
                </div>`;
                el.insertAdjacentHTML('afterend', updateHtml);
            }
        }
    } catch (e) {
        console.log('Update check failed:', e);
    }
}

// Load .env config from server
async function loadEnvConfig() {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const res = await fetch('/telegram/api/config', {
            headers,
            credentials: 'include'
        });
        if (res.ok) {
            const config = await res.json();
            document.getElementById('api_id').value = config.api_id || '';
            document.getElementById('api_hash').value = config.api_hash || '';
            document.getElementById('phone').value = config.phone || '';
            // Set default download path
            if (config.download_path) {
                document.getElementById('download_path').value = config.download_path;
            }
        }
    } catch (e) {
        console.warn('Could not load .env config:', e);
    }
}

// ==================== AUTHENTICATION FLOW ====================

async function handleConnect() {
    hideStatus('connectStatus');
    const apiId = parseInt(document.getElementById('api_id').value);
    const apiHash = document.getElementById('api_hash').value;
    const phone = document.getElementById('phone').value;
    
    if (!apiId || !apiHash || !phone) {
        showStatus('connectStatus', '❌ Заполните все поля', true);
        return;
    }
    
    showStatus('connectStatus', '⏳ Отправка кода в Telegram...');
    
    try {
        const formData = new FormData();
        formData.append('api_id', apiId);
        formData.append('api_hash', apiHash);
        formData.append('phone', phone);
        
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const res = await fetch('/telegram/api/auth/connect', {
            method: 'POST',
            credentials: 'include',
            headers,
            body: formData
        });
        
        const data = await res.json();
        
        if (res.ok) {
            // Store session_id for subsequent calls
            currentAuthSessionId = data.session_id;
            // Show code input step
            document.getElementById('authCodeStep').style.display = 'block';
            document.getElementById('connectBtn').disabled = true;

            document.getElementById('auth_code').focus();
            showStatus('connectStatus', '✅ Код отправлен. Проверьте Telegram (не SMS!). Введите код ниже.', false);
        } else {
            showStatus('connectStatus', '❌ ' + (data.detail || 'Ошибка отправки кода'), true);
        }
    } catch (err) {
        showStatus('connectStatus', '❌ Ошибка сети: ' + err.message, true);
    }
}

async function handleVerifyCode() {
    hideStatus('connectStatus');
    const code = document.getElementById('auth_code').value.trim();
    
    if (!code || code.length < 5) {
        showStatus('connectStatus', '❌ Введите 5-значный код', true);
        return;
    }
    
    if (!currentAuthSessionId) {
        showStatus('connectStatus', '❌ Сессия не найдена. Начните заново.', true);
        return;
    }
    
    showStatus('connectStatus', '⏳ Проверка кода...');
    
    try {
        const formData = new FormData();
        formData.append('session_id', currentAuthSessionId);
        formData.append('code', code);
        
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const res = await fetch('/telegram/api/auth/verify-code', {
            method: 'POST',
            credentials: 'include',
            headers,
            body: formData
        });
        
        const data = await res.json();
        
        if (res.ok) {
            if (data.need_2fa) {
                // Show 2FA step
                document.getElementById('authCodeStep').style.display = 'none';
                document.getElementById('twoFAStep').style.display = 'block';
                document.getElementById('twofa_password').focus();
                showStatus('connectStatus', '🔐 Требуется пароль двухфакторной аутентификации', false);
            } else {
                // Success!
                showAuthSuccess(data);
            }
        } else {
            showStatus('connectStatus', '❌ ' + (data.detail || 'Неверный код'), true);
        }
    } catch (err) {
        showStatus('connectStatus', '❌ Ошибка сети: ' + err.message, true);
    }
}

async function handleVerify2FA() {
    hideStatus('connectStatus');
    const password = document.getElementById('twofa_password').value;
    
    if (!password) {
        showStatus('connectStatus', '❌ Введите пароль 2FA', true);
        return;
    }
    
    if (!currentAuthSessionId) {
        showStatus('connectStatus', '❌ Сессия не найдена. Начните заново.', true);
        return;
    }
    
    showStatus('connectStatus', '⏳ Проверка пароля 2FA...');
    
    try {
        const formData = new FormData();
        formData.append('session_id', currentAuthSessionId);
        formData.append('password', password);
        
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const res = await fetch('/telegram/api/auth/verify-2fa', {
            method: 'POST',
            credentials: 'include',
            headers,
            body: formData
        });
        
        const data = await res.json();
        
        if (res.ok) {
            showAuthSuccess(data);
        } else {
            showStatus('connectStatus', '❌ ' + (data.detail || 'Неверный пароль 2FA'), true);
        }
    } catch (err) {
        showStatus('connectStatus', '❌ Ошибка сети: ' + err.message, true);
    }
}

function cancelAuth() {
    document.getElementById('authCodeStep').style.display = 'none';
    document.getElementById('connectBtn').disabled = false;

    document.getElementById('auth_code').value = '';
    currentAuthSessionId = null;
    hideStatus('connectStatus');
}

function cancel2FA() {
    document.getElementById('twoFAStep').style.display = 'none';
    document.getElementById('authCodeStep').style.display = 'block';
    document.getElementById('twofa_password').value = '';
    hideStatus('connectStatus');
}

async function handleLogout() {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        if (currentAuthSessionId) {
            const formData = new FormData();
            formData.append('session_id', currentAuthSessionId);
            await fetch('/telegram/api/auth/logout', {
                method: 'POST',
                credentials: 'include',
                headers,
                body: formData
            });
        }
        // Reset UI
        document.getElementById('authSuccess').style.display = 'none';
        document.getElementById('connectBtn').disabled = false;
    
        document.getElementById('auth_code').value = '';
        document.getElementById('twofa_password').value = '';
        currentAuthSessionId = null;
        authToken = null;
        localStorage.removeItem('tg_auth_token');
        showStatus('connectStatus', '✅ Вы вышли из аккаунта');
    } catch (e) {
        showStatus('connectStatus', '❌ Ошибка выхода: ' + e.message, true);
    }
}

function showAuthSuccess(data) {
    document.getElementById('authCodeStep').style.display = 'none';
    document.getElementById('twoFAStep').style.display = 'none';
    document.getElementById('authSuccess').style.display = 'block';
    document.getElementById('connectBtn').disabled = true;

    const name = (data.first_name || '') + ' ' + (data.last_name || '');
    const username = data.username ? '@' + data.username : '';
    document.getElementById('authSuccess').innerHTML = `
        <h3>${i18n.t('auth_success')}</h3>
        <p><strong>${name.trim()}</strong> ${username}</p>
        <p>${i18n.t('auth_success_desc')}</p>
        <button type="button" id="logoutBtn" style="background: #dc3545; color: white; width:100%; margin-top:8px;">${i18n.t('logout')}</button>
    `;
    // Re-attach logout handler
    document.getElementById('logoutBtn').addEventListener('click', handleLogout);
    hideStatus('connectStatus');

    // Auto-login to get JWT token
    autoLogin(currentAuthSessionId);
}


async function autoLogin(sessionId) {
    try {
        const formData = new FormData();
        formData.append('session_id', sessionId);
        
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const res = await fetch('/telegram/api/auth/auto-login', {
            method: 'POST',
            credentials: 'include',
            headers,
            body: formData
        });
        
        const data = await res.json();
        
        if (res.ok && data.access_token) {
            // Store token for future requests
            authToken = data.access_token;
            localStorage.setItem('tg_auth_token', authToken);
            console.log('JWT token obtained');
        } else {
            console.warn('Auto-login failed:', data.detail || data.message);
        }
    } catch (err) {
        console.error('Auto-login error:', err);
    }
}

// ==================== SCANNING ====================

async function handleScan(e) {
    e.preventDefault();
    hideStatus('scanStatus');
    showLoading("Сканирование канала...");  // Show spinner with backdrop
    
    const formData = new FormData(e.target);
    const payload = {
        channel_id: formData.get('channel_id'),
        media_type: formData.get('media_type'),
        days: formData.get('days') ? parseInt(formData.get('days')) : null,
        limit: parseInt(formData.get('limit'))
    };
    
    // Handle date range
    const dateRange = document.getElementById('dateRange').value;
    if (dateRange) {
        const parts = dateRange.split(' to ');
        payload.start_date = parts[0] || null;
        // If only one date selected, use same date for end
        payload.end_date = parts[1] || parts[0] || null;
    }
    
    try {
        const headers = { 'Content-Type': 'application/json' };
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;

        // Update current filters so pagination/download uses the scanned channel and dates
        currentFilters.channel_id = payload.channel_id;
        currentFilters.start_date = payload.start_date || null;
        currentFilters.end_date = payload.end_date || null;

        const res = await fetch('/telegram/api/scan', {
            method: 'POST',
            headers,
            credentials: 'include',
            body: JSON.stringify(payload)
        });
        
        const task = await res.json();
        if (task.task_id) {
            currentScanTaskId = task.task_id;
            hideLoading();
            showDownloadProgress(i18n.t('scan_progress'), 0, 0, 0);
            pollTask(task.task_id);
        } else {
            hideLoading();
            hideDownloadProgress();
            showStatus('scanStatus', i18n.t('status_error') + ' ' + (task.detail || 'Unknown'), true);
        }
    } catch (err) {
        hideLoading();
        hideDownloadProgress();
        showStatus('scanStatus', i18n.t('status_network_error') + ' ' + err.message, true);
        showErrorModal(err.message);
    }
}

// Poll task status
async function pollTask(taskId) {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;

        const res = await fetch('/telegram/api/task/' + taskId, {
            headers,
            credentials: 'include'
        });
        const task = await res.json();

        if (task.status === 'running') {
            // Parse progress from message like "Scanned 15000 messages, found 305..."
            const scannedMatch = (task.message || '').match(/Scanned (\d+)/);
            const foundMatch = (task.message || '').match(/found (\d+)/);
            const scanned = scannedMatch ? parseInt(scannedMatch[1]) : 0;
            const found = foundMatch ? parseInt(foundMatch[1]) : 0;
            showDownloadProgress(
                task.message || 'Сканирование...',
                task.progress || 0,
                found,
                scanned
            );
            setTimeout(() => pollTask(taskId), 2000);
        } else if (task.status === 'completed') {
            hideDownloadProgress();
            showStatus('scanStatus', '✅ ' + task.message);
            // Load results from saved scan via API (uses correct channel_id)
            await loadFilteredResults();
            loadSidebarHistory();
        } else if (task.status === 'error') {
            hideDownloadProgress();
            hideLoading();
            showErrorModal(i18n.t('status_error') + ' ' + task.message);
            showStatus('scanStatus', '❌ Ошибка: ' + task.message, true);
        }
    } catch (err) {
        hideDownloadProgress();
        showStatus('scanStatus', i18n.t('status_poll_error') + ' ' + err.message, true);
        showErrorModal(err.message);
    }
}

// Render results from background task
function renderTaskResults(task) {
    let rows = [];
    
    if (task.stats) {
        // Video stats format
        rows = task.stats.map((stat, i) => ({
            '#': i + 1,
            'Пользователь': stat.name || 'ID:' + stat.user_id,
            'Username': stat.username ? '@' + stat.username : '-',
            'Видео': stat.video_count,
            'Последнее': stat.last_date ? new Date(stat.last_date).toLocaleString('ru-RU') : '-'
        }));
    } else if (task.messages) {
        // Raw messages format
        rows = task.messages.map((msg, i) => ({
            '#': i + 1,
            'ID': msg.id,
            'Дата': msg.date ? new Date(msg.date).toLocaleString('ru-RU') : '-',
            'Автор': msg.sender_name || 'ID:' + msg.sender_id,
            'Тип': msg.media_type,
            'Размер': msg.size ? formatBytes(msg.size) : '-'
        }));
    } else {
        rows = [{'#': 1, 'Пользователь': 'Нет данных', 'Видео': 0}];
    }
    
    currentResults = rows;
    currentPage = 1;
    renderPage();
    document.getElementById('pagination').style.display = 'flex';
    document.getElementById('downloadSection').style.display = 'block';
}

// ==================== RESULTS PAGINATION & FILTERING ====================

// Load saved metadata results on page load
async function loadSavedResults() {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const channelId = document.getElementById('channel_id').value || '-1001911644885';
        const res = await fetch(`/telegram/api/videos?page=1&per_page=50&channel_id=${encodeURIComponent(channelId)}`, {
            headers,
            credentials: 'include'
        });
        if (res.ok) {
            const data = await res.json();
            if (data.total > 0) {
                currentResults = data.videos;
                currentPage = data.page;
                renderResultsPage(data);
                document.getElementById('pagination').style.display = 'flex';
                document.getElementById('downloadSection').style.display = 'block';
            }
        }
    } catch (e) {
        console.warn('Could not load saved results:', e);
    }
}

// Apply filters and reload results
async function applyFilters() {
    currentPage = 1;
    currentFilters.channel_id = document.getElementById('channel_id').value;
    currentFilters.username = document.getElementById('filterUsername').value.trim();
    currentFilters.date_from = document.getElementById('filterDateFrom').value;
    currentFilters.date_to = document.getElementById('filterDateTo').value;
    currentFilters.sort_by = document.getElementById('sortBy').value;
    currentFilters.per_page = parseInt(document.getElementById('rowsPerPage').value);
    
    await loadFilteredResults();
}

// Load filtered results from API
async function loadFilteredResults() {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const params = new URLSearchParams({
            page: currentPage,
            per_page: currentFilters.per_page,
            channel_id: currentFilters.channel_id,
            sort_by: currentFilters.sort_by
        });
        
        if (currentFilters.username) params.append('username', currentFilters.username);
        if (currentFilters.date_from) params.append('date_from', currentFilters.date_from);
        if (currentFilters.date_to) params.append('date_to', currentFilters.date_to);
        
        const res = await fetch(`/telegram/api/videos?${params.toString()}`, {
            headers,
            credentials: 'include'
        });
        if (res.ok) {
            const data = await res.json();
            currentResults = data.videos;
            renderResultsPage(data);
        }
    } catch (err) {
        console.error('Failed to load filtered results:', err);
    }
}

// Render paginated page with video metadata
function renderResultsPage(data) {
    const pageData = data.videos;
    const totalPages = data.total_pages;

    if (pageData.length === 0) {
        document.getElementById('results').innerHTML = '<p class="placeholder">' + i18n.t('results_placeholder') + '</p>';
        return;
    }

    // Build table with video metadata columns
    const columns = [
        '', // checkbox column
        i18n.t('col_num'), i18n.t('col_date'), i18n.t('col_author'), i18n.t('col_username'),
        i18n.t('col_duration'), i18n.t('col_size'), i18n.t('col_mime'),
        i18n.t('col_topic'), i18n.t('col_caption')
    ];
    let html = '<div class="table-container"><table class="stats-table"><thead><tr>';
    columns.forEach(col => html += '<th>' + col + '</th>');
    html += '</tr></thead><tbody>';

    const locale = i18n.lang === 'en' ? 'en-US' : 'ru-RU';
    pageData.forEach((video, idx) => {
        const sender = video.sender || {};
        const v = video.video || {};
        const attrs = v.attributes || {};
        const videoAttrs = attrs.video || {};
        const audioAttrs = attrs.audio || {};
        const duration = videoAttrs.duration || audioAttrs.duration;
        const fullName = ((sender.first_name || '') + ' ' + (sender.last_name || '')).trim();
        const msgId = video.message_id;

        html += '<tr>';
        html += '<td><input type="checkbox" class="file-checkbox" data-msg-id="' + msgId + '" onchange="toggleFileSelection(this)"></td>';
        html += '<td>' + (data.start + idx + 1) + '</td>';
        html += '<td>' + (video.date ? new Date(video.date).toLocaleString(locale) : '-') + '</td>';
        html += '<td>' + (fullName || '-') + '</td>';
        html += '<td>' + (sender.username ? '@' + sender.username : (sender.id ? 'ID:' + sender.id : '-')) + '</td>';
        html += '<td>' + (duration ? duration + 'с' : '-') + '</td>';
        html += '<td>' + formatBytes(v.size) + '</td>';
        html += '<td>' + (v.mime_type || '-') + '</td>';
        html += '<td>' + (video.topic_id || '-') + '</td>';
        html += '<td>' + (video.caption ? (video.caption.length > 118 ? video.caption.substring(0, 118) + '…' : video.caption) : '-') + '</td>';
        html += '</tr>';
    });
    
    html += '</tbody></table></div>';
    document.getElementById('results').innerHTML = html;

    // Restore checkbox states for already selected files
    document.querySelectorAll('.file-checkbox').forEach(cb => {
        const msgId = parseInt(cb.dataset.msgId);
        if (selectedMessageIds.has(msgId)) {
            cb.checked = true;
        }
    });

    // Init column resize on the new table
    const newTable = document.querySelector('#results .stats-table');
    if (newTable) initColumnResize(newTable, 'results_col_widths');

    // Update pagination
    document.getElementById('pageInfo').textContent = `${i18n.t('pagination_page')} ${data.page} ${i18n.t('pagination_of')} ${totalPages} (${data.total} ${i18n.t('pagination_records')})`;
    document.getElementById('prevPage').disabled = data.page <= 1;
    document.getElementById('nextPage').disabled = data.page >= totalPages;
    document.getElementById('pagination').style.display = 'flex';
    document.getElementById('downloadSection').style.display = 'block';
}

function changePage(page) {
    if (page >= 1) {
        currentPage = page;
        loadFilteredResults();
    }
}

// ==================== STATISTICS ====================

// Load and display statistics by author with pagination
async function loadStats() {
    hideStatus('statsStatus');
    showLoading("Загрузка статистики...");
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;
        
        const channelId = document.getElementById('channel_id').value;
        const username = document.getElementById('statsUsername').value.trim();
        const sortBy = document.getElementById('statsSortBy').value;
        const perPage = parseInt(document.getElementById('statsRowsPerPage').value);
        
        const params = new URLSearchParams({
            channel_id: channelId,
            page: statsCurrentPage,
            per_page: perPage,
            sort_by: sortBy
        });
        if (username) params.append('username', username);
        
        const res = await fetch(`/telegram/api/videos/stats?${params.toString()}`, {
            headers,
            credentials: 'include'
        });
        if (!res.ok) throw new Error('Failed to load stats');
        const data = await res.json();
        hideLoading();
        renderStatsTable(data);
        updateStatsPagination(data);
        hideStatus('statsStatus');
    } catch (err) {
        hideLoading();
        showStatus('statsStatus', '❌ Ошибка: ' + err.message, true);
        showErrorModal(err.message);
    }
}

// Render statistics table with pagination info
function renderStatsTable(data) {
    const container = document.getElementById('statsTableContainer');

    if (!data.authors || data.authors.length === 0) {
        container.innerHTML = '<p class="placeholder">' + i18n.t('results_placeholder') + '</p>';
        return;
    }

    const startNum = data.start || 0;

    let html = `
        <div style="margin-bottom: 10px;">
            <strong>${i18n.t('stats_total_videos')}</strong> ${data.total_videos} |
            <strong>${i18n.t('stats_unique_authors')}</strong> ${data.total}
        </div>
        <div class="table-container">
        <table class="stats-table">
            <thead>
                <tr>
                    <th>${i18n.t('col_num')}</th>
                    <th>${i18n.t('col_user')}</th>
                    <th>${i18n.t('col_username')}</th>
                    <th>${i18n.t('col_videos')}</th>
                    <th>${i18n.t('col_total_mb')}</th>
                    <th>${i18n.t('col_last_upload')}</th>
                </tr>
            </thead>
            <tbody>
    `;

    const locale = i18n.lang === 'en' ? 'en-US' : 'ru-RU';
    data.authors.forEach((author, idx) => {
        const name = author.first_name || '';
        const lastName = author.last_name || '';
        const fullName = (name + ' ' + lastName).trim() || 'ID:' + author.sender_id;
        const username = author.username ? '@' + author.username : '-';
        const totalMB = (author.total_size / (1024 * 1024)).toFixed(1);
        const lastDate = author.last_date ? new Date(author.last_date * 1000).toLocaleString(locale) : '-';

        html += `
            <tr>
                <td>${startNum + idx + 1}</td>
                <td>${fullName}</td>
                <td>${username}</td>
                <td>${author.count}</td>
                <td>${totalMB}</td>
                <td>${lastDate}</td>
            </tr>
        `;
    });
    
    html += `
            </tbody>
        </table>
        </div>
    `;
    
    container.innerHTML = html;

    // Init column resize on the stats table
    const statsTable = container.querySelector('.stats-table');
    if (statsTable) initColumnResize(statsTable, 'stats_authors_col_widths');
}

// Update stats pagination controls
function updateStatsPagination(data) {
    statsTotalPages = data.total_pages || 1;
    document.getElementById('statsPageInfo').textContent = `${i18n.t('pagination_page')} ${data.page} ${i18n.t('pagination_of')} ${statsTotalPages} (${data.total} ${i18n.t('pagination_authors')})`;
    document.getElementById('statsPrevPage').disabled = data.page <= 1;
    document.getElementById('statsNextPage').disabled = data.page >= statsTotalPages;
    document.getElementById('statsPagination').style.display = statsTotalPages > 1 ? 'flex' : 'none';
}

function changeStatsPage(page) {
    if (page >= 1 && page <= statsTotalPages) {
        statsCurrentPage = page;
        loadStats();
    }
}

// ==================== DOWNLOAD ====================

// Folder picker using File System Access API (modern) or webkitdirectory fallback
async function pickFolder() {
    const input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;
    input.directory = true;
    input.style.display = 'none';
    
    // For File System Access API (Chrome 86+, Edge 86+)
    if ('showDirectoryPicker' in window) {
        try {
            const handle = await window.showDirectoryPicker({ mode: 'readwrite' });
            document.getElementById('download_path').value = handle.name;
            // Store handle for later use
            window.selectedFolderHandle = handle;
            return;
        } catch (e) {
            console.warn('File System Access API failed, falling back:', e);
        }
    }
    
    // Fallback: invisible file input with webkitdirectory
    input.onchange = (e) => {
        if (e.target.files.length > 0) {
            const path = e.target.files[0].webkitRelativePath;
            const folder = path.split('/')[0];
            document.getElementById('download_path').value = folder;
            window.selectedFolderFiles = e.target.files;
        }
    };
    document.body.appendChild(input);
    input.click();
    document.body.removeChild(input);
}

// Handle file checkbox selection
function toggleFileSelection(checkbox) {
    const msgId = parseInt(checkbox.dataset.msgId);
    if (checkbox.checked) {
        selectedMessageIds.add(msgId);
    } else {
        selectedMessageIds.delete(msgId);
    }
    updateDownloadButton();
}

// Update download button based on selection
function updateDownloadButton() {
    const btn = document.getElementById('downloadBtn');
    if (!btn) return;
    if (selectedMessageIds.size > 0) {
        btn.textContent = `📥 ${i18n.t('download_selected')} (${selectedMessageIds.size})`;
        btn.classList.add('download-selected');
    } else {
        btn.textContent = `📥 ${i18n.t('download_btn')}`;
        btn.classList.remove('download-selected');
    }
}

// Handle download
async function handleDownload() {
    const path = document.getElementById('download_path').value;
    if (!path) {
        showErrorModal(i18n.t('error_select_folder'));
        return;
    }

    const channelId = document.getElementById('channel_id').value;
    const mediaType = document.getElementById('media_type').value;
    const days = document.getElementById('days').value ? parseInt(document.getElementById('days').value) : null;
    const delayMin = parseFloat(document.getElementById('delay_min').value) || 2.0;
    const delayMax = parseFloat(document.getElementById('delay_max').value) || 5.0;
    const skipExisting = document.getElementById('skip_existing').checked;

    // Pre-check: is this channel already being downloaded?
    try {
        const h = {};
        if (authToken) h['Authorization'] = 'Bearer ' + authToken;
        const actRes = await fetch('/telegram/api/tasks/active', { headers: h, credentials: 'include' });
        if (actRes.ok) {
            const actData = await actRes.json();
            const dup = (actData.tasks || []).find(t => t.channel_id === channelId);
            if (dup) {
                showStatus('scanStatus',
                    `⏳ Для канала «${dup.channel_title}» (${dup.channel_id}) уже выполняется скачивание в фоне — дождитесь завершения.`, true);
                return;
            }
        }
    } catch (e) { /* proceed anyway */ }

    hideStatus('scanStatus');
    showLoading(i18n.t('status_download_started'));

    try {
        const headers = { 'Content-Type': 'application/json' };
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;

        // Check if specific files are selected
        const useSelected = selectedMessageIds.size > 0;
        const endpoint = useSelected ? '/telegram/api/download-selected' : '/telegram/api/download';

        const body = {
            channel_id: channelId,
            media_type: mediaType,
            days: days,
            download_path: path,
            limit: 1000,
            delay_min: delayMin,
            delay_max: delayMax,
            skip_existing: skipExisting,
            start_date: currentFilters.start_date || null,
            end_date: currentFilters.end_date || null
        };

        // Add message_ids for selected files download
        if (useSelected) {
            body.message_ids = Array.from(selectedMessageIds);
        }

        const res = await fetch(endpoint, {
            method: 'POST',
            headers,
            credentials: 'include',
            body: JSON.stringify(body)
        });
        
        const task = await res.json();
        if (res.status === 409) {
            hideLoading();
            showStatus('scanStatus', '⏳ ' + (task.detail || i18n.t('status_duplicate_download')), true);
        } else if (task.task_id) {
            hideLoading();
            showDownloadProgress(i18n.t('download_title'), 0, 0, 0);
            pollDownloadTask(task.task_id);
        } else {
            hideLoading();
            showStatus('scanStatus', '❌ Ошибка запуска скачивания', true);
        }
    } catch (err) {
        hideLoading();
        showStatus('scanStatus', '❌ Ошибка: ' + err.message, true);
    }
}

async function pollDownloadTask(taskId) {
    try {
        const headers = {};
        if (authToken) headers['Authorization'] = 'Bearer ' + authToken;

        const res = await fetch('/telegram/api/task/' + taskId, {
            headers,
            credentials: 'include'
        });
        const task = await res.json();

        if (task.status === 'running') {
            // Parse "Downloading 15/21..." from task.message
            const match = (task.message || '').match(/(\d+)\/(\d+)/);
            const done = match ? parseInt(match[1]) : 0;
            const total = match ? parseInt(match[2]) : 0;
            const progress = task.progress || (total ? Math.round(done / total * 100) : 0);
            showDownloadProgress(i18n.t('download_title'), progress, done, total);
            setTimeout(() => pollDownloadTask(taskId), 3000);
        } else if (task.status === 'completed') {
            hideDownloadProgress();
            showStatus('scanStatus', '✅ ' + task.message);
        } else if (task.status === 'error') {
            hideDownloadProgress();
            showStatus('scanStatus', '❌ Ошибка скачивания: ' + task.message, true);
            showErrorModal(task.message);
        }
    } catch (err) {
        hideDownloadProgress();
        showStatus('scanStatus', i18n.t('status_poll_error') + ' ' + err.message, true);
        showErrorModal(err.message);
    }
}

// ==================== UTILITIES ====================

// Loading overlay (spinner with backdrop)
function showLoading(text = "Подключение...") {
    const overlay = document.getElementById('loadingOverlay');
    const textEl = document.getElementById('loadingText');
    if (overlay && textEl) {
        textEl.textContent = text;
        overlay.style.display = 'flex';
    }
}

function hideLoading() {
    const overlay = document.getElementById('loadingOverlay');
    if (overlay) overlay.style.display = 'none';
}

// Error modal
function showErrorModal(message) {
    const modal = document.getElementById('errorModal');
    const detail = document.getElementById('errorDetail');
    if (modal && detail) {
        detail.textContent = message;
        modal.style.display = 'flex';
    }
}

function hideErrorModal() {
    const modal = document.getElementById('errorModal');
    if (modal) modal.style.display = 'none';
}

// Initialize error modal close button
document.addEventListener('DOMContentLoaded', () => {
    const closeBtn = document.getElementById('errorModalClose');
    if (closeBtn) {
        closeBtn.addEventListener('click', hideErrorModal);
    }
    // Close on backdrop click
    const modal = document.getElementById('errorModal');
    if (modal) {
        modal.addEventListener('click', (e) => {
            if (e.target === modal) hideErrorModal();
        });
    }
});

function showStatus(elementId, message, isError = false) {
    const el = document.getElementById(elementId);
    el.textContent = message;
    el.className = 'status ' + (isError ? 'error' : 'success');
    el.style.display = 'block';
}

function hideStatus(elementId) {
    document.getElementById(elementId).style.display = 'none';
}

function formatBytes(bytes) {
    if (bytes === 0 || !bytes) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// Legacy render for task-based results (kept for compatibility)
function renderPage() {
    const start = (currentPage - 1) * currentFilters.per_page;
    const end = start + currentFilters.per_page;
    const pageData = currentResults.slice(start, end);
    const totalPages = Math.ceil(currentResults.length / currentFilters.per_page);
    
    if (pageData.length === 0) {
        document.getElementById('results').innerHTML = '<p class="placeholder">Нет данных для отображения</p>';
        return;
    }
    
    const columns = Object.keys(pageData[0]);
    let html = '<div class="table-container"><table class="stats-table"><thead><tr>';
    columns.forEach(col => html += '<th>' + col + '</th>');
    html += '</tr></thead><tbody>';
    
    pageData.forEach(row => {
        html += '<tr>';
        columns.forEach(col => html += '<td>' + (row[col] ?? '-') + '</td>');
        html += '</tr>';
    });
    
    html += '</tbody></table></div>';
    document.getElementById('results').innerHTML = html;
    
    document.getElementById('pageInfo').textContent = `${i18n.t('pagination_page')} ${currentPage} ${i18n.t('pagination_of')} ${totalPages} (${currentResults.length} ${i18n.t('pagination_records')})`;
    document.getElementById('prevPage').disabled = currentPage <= 1;
    document.getElementById('nextPage').disabled = currentPage >= totalPages;
}