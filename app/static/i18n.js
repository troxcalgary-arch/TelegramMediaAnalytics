// Internationalization module
// Supports RU and EN with localStorage persistence

const translations = {
    ru: {
        // Header
        app_title: "📊 Telegram Analytics",

        // Connection
        connect_title: "Подключение к Telegram",
        api_id: "API ID:",
        api_hash: "API Hash:",
        phone: "Телефон:",
        connect_btn: "🔐 Подключиться",
        enter_code: "📱 Введите код из Telegram",
        code_label: "Код подтверждения:",
        verify_code: "✅ Подтвердить код",
        cancel: "Отмена",
        two_fa_title: "🔐 Двухфакторная аутентификация",
        two_fa_label: "Пароль 2FA:",
        two_fa_placeholder: "Ваш пароль 2FA",
        verify_2fa: "✅ Войти с 2FA",
        auth_success: "✅ Успешно авторизован!",
        auth_success_desc: "Теперь можно сканировать каналы и скачивать медиа.",
        logout: "🚪 Выйти",

        // History sidebar
        history_title: "📋 История сканирований",
        history_loading: "Загрузка...",
        history_empty: "Нет записей",
        history_view: "Посмотреть историю →",

        // Scan
        scan_title: "Сканирование канала",
        channel_id_label: "ID канала или @username:",
        channel_id_placeholder: "@ourpover или -1001234567890",
        media_type: "Тип медиа:",
        media_all: "Все медиа",
        media_video: "Видео",
        media_audio: "Аудио / Голосовые",
        media_photo: "Фото",
        media_document: "Документы",
        period_label: "Период (дней) или диапазон дат:",
        period_placeholder: "7, 30, 90, 365...",
        period_hint: "Количество дней назад от сегодня. Оставьте пустым для всех сообщений.",
        date_range_label: "Или выберите диапазон дат (альтернатива):",
        date_range_placeholder: "Выберите диапазон дат...",
        date_range_hint: 'Если выбрать диапазон — поле "Период" будет игнорироваться',
        limit_label: "Лимит сообщений:",
        limit_hint: "0 = без лимита (все сообщения)",
        folder_label: "Папка для скачивания:",
        folder_placeholder: "Выберите папку...",
        folder_btn: "📁 Выбрать папку",
        folder_hint: "Опционально - только для скачивания файлов. Кнопка использует системный диалог выбора папки.",
        download_settings: "Настройки скачивания (защита от бана):",
        delay_min: "Мин. задержка (сек):",
        delay_max: "Макс. задержка (сек):",
        skip_existing: "Пропускать существующие файлы",
        delay_recommendation: "Рекомендуется: мин 2 сек, макс 5 сек. Случайная задержка между файлами защищает от FloodWait. При больших объёмах увеличьте до 5-10 сек.",
        scan_btn: "Запустить сканирование",
        error_select_folder: "Сначала выберите папку для скачивания",

        // Stats
        stats_title: "📈 Статистика по авторам",
        stats_username_placeholder: "@username или имя...",
        stats_sort: "Сортировка:",
        stats_sort_count_desc: "По кол-ву видео (↓)",
        stats_sort_count_asc: "По кол-ву видео (↑)",
        stats_sort_size_desc: "По размеру (↓)",
        stats_sort_size_asc: "По размеру (↑)",
        stats_sort_date_desc: "По последней загрузке (↓)",
        stats_sort_date_asc: "По последней загрузке (↑)",
        stats_rows: "Строк на странице:",
        stats_load_btn: "Загрузить статистику",
        stats_placeholder: 'Нажмите «Загрузить статистику» для получения данных по авторам...',
        stats_total_videos: "Всего видео:",
        stats_unique_authors: "Уникальных авторов:",

        // Results
        results_title: "Результаты",
        filter_username_placeholder: "@username или имя...",
        filter_date_from: "Дата от:",
        filter_date_to: "Дата до:",
        filter_sort: "Сортировка:",
        filter_sort_date_desc: "Дата (новые сначала)",
        filter_sort_date_asc: "Дата (старые сначала)",
        filter_sort_duration_desc: "Длительность (длинные сначала)",
        filter_sort_duration_asc: "Длительность (короткие сначала)",
        filter_sort_size_desc: "Размер (большие сначала)",
        filter_sort_size_asc: "Размер (маленькие сначала)",
        filter_apply: "Применить фильтры",
        results_placeholder: "Здесь будут отображаться результаты сканирования...",
        prev_page: "← Назад",
        next_page: "Вперёд →",
        download_btn: "📥 Скачать файлы в выбранную папку",
        download_selected: "📥 Скачать выбранные файлы",

        // Overlays
        loading_connect: "Подключение...",
        download_title: "Скачивание файлов...",
        scan_progress: "Сканирование канала...",
        error_title: "❌ Ошибка",
        error_close: "Закрыть",

        // Status messages
        status_connecting: "Подключение...",
        status_connected: "Подключено. Получение канала...",
        status_scanning: "Сканирование сообщений...",
        status_scan_started: "✅ Сканирование запущено. Ожидайте...",
        status_scan_complete: "✅ Сканирование завершено",
        status_downloading: "⏳ Скачивание...",
        status_download_started: "✅ Скачивание запущено",
        status_error: "❌ Ошибка:",
        status_network_error: "❌ Ошибка сети:",
        status_poll_error: "❌ Ошибка опроса:",
        pagination_page: "Страница",
        pagination_of: "из",
        pagination_records: "записей",
        pagination_authors: "авторов",
        no_data: "Нет данных для отображения",
        status_active_download: "⏳ Для канала",
        status_active_download_mid: "сейчас в фоне выполняется скачивание",
        status_duplicate_download: "⏳ Скачивание уже выполняется в фоне",

        // Scan history page
        history_page_title: "📋 История сканирований",
        history_date: "Дата и время",
        history_channel: "Канал",
        history_media_type: "Тип медиа",
        history_messages: "Сообщений",
        history_authors: "Авторов",
        history_topic: "Топик",
        history_empty_page: "Нет записей",

        // Results columns
        col_num: "#",
        col_date: "Дата",
        col_author: "Автор",
        col_username: "Username",
        col_duration: "Продолжительность",
        col_size: "Размер",
        col_mime: "MIME",
        col_topic: "Топик",
        col_caption: "Подпись",

        // Stats columns
        col_user: "Пользователь",
        col_videos: "Видео",
        col_total_mb: "Всего MB",
        col_last_upload: "Последняя загрузка",
    },

    en: {
        // Header
        app_title: "📊 Telegram Analytics",

        // Connection
        connect_title: "Connect to Telegram",
        api_id: "API ID:",
        api_hash: "API Hash:",
        phone: "Phone:",
        connect_btn: "🔐 Connect",
        save_config: "💾 Save configuration",
        enter_code: "📱 Enter code from Telegram",
        code_label: "Verification code:",
        verify_code: "✅ Verify code",
        cancel: "Cancel",
        two_fa_title: "🔐 Two-Factor Authentication",
        two_fa_label: "2FA Password:",
        two_fa_placeholder: "Your 2FA password",
        verify_2fa: "✅ Login with 2FA",
        auth_success: "✅ Successfully authorized!",
        auth_success_desc: "You can now scan channels and download media.",
        logout: "🚪 Logout",

        // History sidebar
        history_title: "📋 Scan History",
        history_loading: "Loading...",
        history_empty: "No records",
        history_view: "View history →",

        // Scan
        scan_title: "Channel Scanning",
        channel_id_label: "Channel ID or @username:",
        channel_id_placeholder: "@channelname or -1001234567890",
        media_type: "Media type:",
        media_all: "All media",
        media_video: "Video",
        media_audio: "Audio / Voice",
        media_photo: "Photo",
        media_document: "Documents",
        period_label: "Period (days) or date range:",
        period_placeholder: "7, 30, 90, 365...",
        period_hint: "Number of days back from today. Leave empty for all messages.",
        date_range_label: "Or select date range (alternative):",
        date_range_placeholder: "Select date range...",
        date_range_hint: 'If a range is selected, the "Period" field will be ignored',
        limit_label: "Message limit:",
        limit_hint: "0 = no limit (all messages)",
        folder_label: "Download folder:",
        folder_placeholder: "Select folder...",
        folder_btn: "📁 Select folder",
        folder_hint: "Optional - only for file downloads. Button uses system folder dialog.",
        download_settings: "Download settings (anti-ban protection):",
        delay_min: "Min delay (sec):",
        delay_max: "Max delay (sec):",
        skip_existing: "Skip existing files",
        delay_recommendation: "Recommended: min 2 sec, max 5 sec. Random delay between files protects from FloodWait. For large volumes increase to 5-10 sec.",
        scan_btn: "Start scanning",
        error_select_folder: "Please select a download folder first",

        // Stats
        stats_title: "📈 Author Statistics",
        stats_username_placeholder: "@username or name...",
        stats_sort: "Sort:",
        stats_sort_count_desc: "By video count (↓)",
        stats_sort_count_asc: "By video count (↑)",
        stats_sort_size_desc: "By size (↓)",
        stats_sort_size_asc: "By size (↑)",
        stats_sort_date_desc: "By last upload (↓)",
        stats_sort_date_asc: "By last upload (↑)",
        stats_rows: "Rows per page:",
        stats_load_btn: "Load statistics",
        stats_placeholder: 'Click "Load statistics" to get author data...',
        stats_total_videos: "Total videos:",
        stats_unique_authors: "Unique authors:",

        // Results
        results_title: "Results",
        filter_username_placeholder: "@username or name...",
        filter_date_from: "Date from:",
        filter_date_to: "Date to:",
        filter_sort: "Sort:",
        filter_sort_date_desc: "Date (newest first)",
        filter_sort_date_asc: "Date (oldest first)",
        filter_sort_duration_desc: "Duration (longest first)",
        filter_sort_duration_asc: "Duration (shortest first)",
        filter_sort_size_desc: "Size (largest first)",
        filter_sort_size_asc: "Size (smallest first)",
        filter_apply: "Apply filters",
        results_placeholder: "Scan results will appear here...",
        prev_page: "← Back",
        next_page: "Next →",
        download_btn: "📥 Download files to selected folder",
        download_selected: "📥 Download selected files",

        // Overlays
        loading_connect: "Connecting...",
        download_title: "Downloading files...",
        scan_progress: "Scanning channel...",
        error_title: "❌ Error",
        error_close: "Close",

        // Status messages
        status_connecting: "Connecting...",
        status_connected: "Connected. Fetching channel...",
        status_scanning: "Scanning messages...",
        status_scan_started: "✅ Scan started. Please wait...",
        status_scan_complete: "✅ Scan completed",
        status_downloading: "⏳ Downloading...",
        status_download_started: "✅ Download started",
        status_error: "❌ Error:",
        status_network_error: "❌ Network error:",
        status_poll_error: "❌ Poll error:",
        status_active_download: "⏳ Channel",
        status_active_download_mid: "is currently being downloaded in background",
        status_duplicate_download: "⏳ Download already in progress",
        pagination_page: "Page",
        pagination_of: "of",
        pagination_records: "records",
        pagination_authors: "authors",
        no_data: "No data to display",

        // Scan history page
        history_page_title: "📋 Scan History",
        history_date: "Date & Time",
        history_channel: "Channel",
        history_media_type: "Media type",
        history_messages: "Messages",
        history_authors: "Authors",
        history_topic: "Topic",
        history_empty_page: "No records",

        // Results columns
        col_num: "#",
        col_date: "Date",
        col_author: "Author",
        col_username: "Username",
        col_duration: "Duration",
        col_size: "Size",
        col_mime: "MIME",
        col_topic: "Topic",
        col_caption: "Caption",

        // Stats columns
        col_user: "User",
        col_videos: "Videos",
        col_total_mb: "Total MB",
        col_last_upload: "Last upload",
    }
};

// i18n Manager
const i18n = {
    _lang: localStorage.getItem('app_lang') || 'en',

    get lang() {
        return this._lang;
    },

    set lang(newLang) {
        this._lang = newLang;
        localStorage.setItem('app_lang', newLang);
        document.documentElement.lang = newLang;
        this.apply();
    },

    t(key) {
        return translations[this._lang]?.[key] || translations['ru']?.[key] || key;
    },

    apply() {
        // Update all elements with data-i18n attribute
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            el.textContent = this.t(key);
        });

        // Update all elements with data-i18n-placeholder
        document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            el.placeholder = this.t(key);
        });

        // Update all elements with data-i18n-html
        document.querySelectorAll('[data-i18n-html]').forEach(el => {
            const key = el.getAttribute('data-i18n-html');
            el.innerHTML = this.t(key);
        });

        // Update title
        document.title = this.t('app_title');
    },

    init() {
        document.documentElement.lang = this._lang;
        this.apply();
    }
};
