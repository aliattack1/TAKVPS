"""(G2) — Per-page help text.

A JSON-like dict mapping page keys to short help strings. The shell's topbar
shows a "?" icon that opens a dialog with the current page's help.

Persian translations live in PERSIAN_PAGE_HELP — get_help() picks the right
one based on the user's language.
"""

PAGE_HELP = {
    "overview": "Server health at a glance: OS, kernel, uptime, load, memory, privilege level, "
    "package manager. Tap any quick-action to run it.",
    "monitor": "Live server stats: CPU (overall + per-core), RAM, swap, disk, network, temperature. "
    "Polls every N seconds (Settings → Monitoring). History is 120 samples in memory. "
    "Health colors: green < amber < red, based on your thresholds.",
    "files": "SFTP file browser: navigate, upload, download, rename, delete, permissions, properties. "
    "Drag files from your OS to upload. Folder download/upload supported. "
    "Delete goes to the Recycle Bin (~/.takvps-recycle/) — restore from there.",
    "editor": "Multi-tab code editor with syntax highlighting (12+ languages). "
    "Save keeps permissions and warns if the file changed on the server. "
    "Find/replace with regex. Adjustable font size.",
    "terminal": "Interactive PTY shell with VT100/xterm emulator. Supports vim/nano/less/htop. "
    "CJK/emoji wide chars, mouse reporting, search-in-scrollback (🔍), tmux sessions, "
    "long-press → Ask AI. Smart auto-scroll stays at bottom unless you scroll up.",
    "snippets": "Reusable commands with {{variables}}, Git deploy recipes, command packs. "
    "Variables are shell-quoted. Risk gate on every run. Background runs use nohup.",
    "services": "systemd service manager: list, start/stop/restart, enable/disable, view logs. "
    "Edit unit files via the editor (saved with sudo install + daemon-reload).",
    "cron": "Cron job manager: visual schedule builder, next-run preview, enable/disable. "
    "Your crontab is validated by the server's `crontab` command. Backups kept (last 10).",
    "packages": "Package manager (apt/dnf/yum/pacman/apk/zypper): update, install, remove. "
    "Security vs normal split. Changelog preview on apt. Reboot-required flag.",
    "docker": "Docker manager: containers, images, volumes, networks, compose. "
    "Live logs, inspect, shell-in-terminal, stats, prune. Auto-detects installation.",
    "nginx": "Nginx manager: sites, config test, reload/restart, HTTPS via certbot. "
    "Templates: static site, reverse proxy, PHP-FPM. Config edit tests after save.",
    "logs": "Log viewer: journald presets + /var/log files. Live follow, filter (text/regex/case), "
    "severity coloring, pause, copy/export.",
    "firewall": "Firewall manager: ufw / firewalld (iptables/nftables read-only). "
    "SSH lockout guard: can't delete the rule that allows your connection port.",
    "processes": "Process manager: signals (TERM/KILL/HUP/INT/STOP/CONT), renice, search/sort. "
    "PID 1 and sshd need typed confirmation. Auto-refresh stops when you leave.",
    "network": "Network: listening ports (public vs local), connections by remote IP, "
    "ping/traceroute/DNS, public-IP lookup (done by the server).",
    "users": "Users & SSH keys: authorized_keys viewer with SHA256 fingerprints, add/remove keys, "
    "failed-login analysis. Removing your last key needs typed confirmation.",
    "quick": "Quick actions: update, clear caches, free memory, vacuum journal, reboot, shutdown. "
    "All confirm; power actions need typed confirmation.",
    "activity": "Audit log of state-changing commands (reads aren't logged). "
    "Each record is SHA-256 chained (tamper-evident). Verify integrity with the ✓ button.",
    "fleet": "All-servers dashboard: parallel health probes + run commands across every server. "
    "▶ button runs a one-off command or saved snippet in parallel. Stop-all cancels everything.",
    "backups": "tar -czf backups of server folders. Excludes, size/free-space check, optional "
    "download to phone, optional daily cron job with delete-after-N-days.",
    "alerts": "Alerts: CRITICAL thresholds (CPU/RAM/disk) + watched services. "
    "Debounced (2 checks in a row, 30-min cooldown). In-app notifications; OS notifications via hook.",
    "health": "Health score: 0–100, computed from one read-only command + update list. "
    "Each penalty explains itself and taps through to the page that fixes it.",
    "diskusage": "Disk usage drill-down with bars + cleanup assistant: package caches, journal, "
    "rotated logs, unused Docker data, unused packages/old kernels. Shows space freed.",
    "databases": "Databases: MySQL/MariaDB, PostgreSQL, SQLite, Redis via the server's CLI. "
    "SQL classified: reads run, writes ask, DROP/TRUNCATE without WHERE need typed RUN. "
    "Dumps are gzip; restore needs typed RESTORE.",
    "tunnels": "SSH tunnels: local forwards bound to 127.0.0.1 only. Stop when connection drops. "
    "Favorites per server.",
    "envedit": ".env editor: parses/edits only the lines you touch. Masks secret-looking keys. "
    "Never logs or sends values. Optional service/container restart after saving.",
    "wireguard": "WireGuard VPN manager: view interface status (running/stopped), start/stop "
    "with wg-quick, view peers and their allowed IPs. Config read from /etc/wireguard/wg0.conf.",
    "k8s": "Kubernetes manager: list pods, deployments, services. View pod logs, exec into a pod. "
    "Uses the server's own kubectl — nothing installed by the app.",
    "proxy": "Reverse-proxy manager: detects Caddy or Traefik. Shows status (running/stopped). "
    "Caddy: generate config templates (static site, reverse proxy, PHP-FPM) with copy button.",
    "tools": "Tools hub: one tap to any manager page.",
    "settings": "Settings: theme, monitoring, files, AI assistant, terminal, editor, security, "
    "backup & restore, fleet concurrency, per-server AI prompts.",
    "servers": "Connection manager: add/edit/duplicate/delete servers. Tags, color, jump host. "
    "Test button checks reachability. Import/export encrypted bundles.",
}

PERSIAN_PAGE_HELP = {
    "overview": "سلامت سرور در یک نگاه: سیستم‌عامل، کرنل، زمان روشن بودن، بار، حافظه، "
    "سطح دسترسی، مدیر بسته. هر اقدام سریع را با ضربه اجرا کنید.",
    "monitor": "آمار زنده سرور: پردازنده (کل + به تفکیک هسته)، رم، swap، دیسک، شبکه، دما. "
    "هر N ثانیه به‌روزرسانی می‌شود (تنظیمات → نظارت). ۱۲۰ نمونه در حافظه نگه‌داری می‌شود. "
    "رنگ سلامت: سبز < کهربایی < قرمز، بر اساس آستانه‌های شما.",
    "files": "مرورگر فایل SFTP: پیمایش، آپلود، دانلود، تغییر نام، حذف، مجوزها، خصوصیات. "
    "برای آپلود، فایل‌ها را از سیستم‌عامل خود بکشید. دانلود/آپلود پوشه پشتیبانی می‌شود. "
    "حذف به سطل بازیافت می‌رود (~/.takvps-recycle/) — از آنجا بازیابی کنید.",
    "editor": "ویرایشگر کد چندزبانه با برجسته‌سازی نحوی (۱۲+ زبان). "
    "ذخیره، مجوزها را حفظ می‌کند و اگر فایل روی سرور تغییر کرده باشد هشدار می‌دهد. "
    "یافتن/جایگزینی با عبارت منظم. اندازه فونت قابل تنظیم.",
    "terminal": "پوسته PTY تعاملی با شبیه‌ساز VT100/xterm. از vim/nano/less/htop پشتیبانی می‌کند. "
    "نویسه‌های پهن CJK/ایموجی، گزارش ماوس، جستجو در خروجی (🔍)، نشست‌های tmux، "
    "لمس طولانی → پرسش از هوش مصنوعی. اسکرول خودکار هوشمند در پایین می‌ماند مگر اینکه بالا بروید.",
    "snippets": "دستورات قابل استفاده مجدد با {{متغیرها}}، دستورالعمل‌های استقرار Git، بسته‌های دستور. "
    "متغیرها به‌صورت shell-quoted می‌شوند. در هر اجرا دروازه ریسک فعال است. اجراهای پس‌زمینه از nohup استفاده می‌کنند.",
    "services": "مدیر سرویس systemd: فهرست، شروع/توقف/راه‌اندازی مجدد، فعال‌سازی/غیرفعال‌سازی، مشاهده لاگ‌ها. "
    "ویرایش فایل‌های unit از طریق ویرایشگر (با sudo install + daemon-reload ذخیره می‌شوند).",
    "cron": "مدیر کارهای کرون: سازنده زمان‌بندی بصری، پیش‌نمایش اجرای بعدی، فعال/غیرفعال. "
    "crontab شما با دستور `crontab` سرور اعتبارسنجی می‌شود. بکاپ‌ها نگه‌داری می‌شوند (آخرین ۱۰ مورد).",
    "packages": "مدیر بسته (apt/dnf/yum/pacman/apk/zypper): به‌روزرسانی، نصب، حذف. "
    "تفکیک امنیتی در برابر عادی. پیش‌نمایش تغییرات نسخه در apt. پرچم نیاز به راه‌اندازی مجدد.",
    "docker": "مدیر داکر: کانتینرها، ایمیج‌ها، ولوم‌ها، شبکه‌ها، compose. "
    "لاگ‌های زنده، بررسی، شل در ترمینال، آمار، هرس. نصب را خودکار تشخیص می‌دهد.",
    "nginx": "مدیر Nginx: سایت‌ها، تست پیکربندی، بارگذاری مجدد/راه‌اندازی مجدد، HTTPS با certbot. "
    "قالب‌ها: سایت ایستا، پراکسی معکوس، PHP-FPM. ویرایش پیکربندی پس از ذخیره تست می‌شود.",
    "logs": "نمایشگر لاگ: پیش‌تنظیم‌های journald + فایل‌های /var/log. دنبال‌کردن زنده، فیلتر (متن/عبارت منظم/حالت)، "
    "رنگ‌آمیزی شدت، مکث، کپی/خروجی.",
    "firewall": "مدیر فایروال: ufw / firewalld (iptables/nftables فقط خواندنی). "
    "محافظ قفل SSH: نمی‌توان قانونی را که پورت اتصال شما را مجاز می‌کند حذف کرد.",
    "processes": "مدیر فرایندها: سیگنال‌ها (TERM/KILL/HUP/INT/STOP/CONT)، renice، جستجو/مرتب‌سازی. "
    "PID 1 و sshd نیاز به تأیید تایپ‌شده دارند. به‌روزرسانی خودکار هنگام خروج متوقف می‌شود.",
    "network": "شبکه: پورت‌های در حال شنود (عمومی در برابر محلی)، اتصال‌ها بر اساس IP دوردست، "
    "ping/traceroute/DNS، جستجوی IP عمومی (توسط سرور انجام می‌شود).",
    "users": "کاربران و کلیدهای SSH: نمایشگر authorized_keys با اثر انگشت SHA256، افزودن/حذف کلیدها، "
    "تحلیل ورودهای ناموفق. حذف آخرین کلید نیاز به تأیید تایپ‌شده دارد.",
    "quick": "اقدامات سریع: به‌روزرسانی، پاک کردن کش‌ها، آزاد کردن حافظه، خلوت کردن journal، راه‌اندازی مجدد، خاموش. "
    "همه تأیید می‌شوند؛ اقدامات قدرت نیاز به تأیید تایپ‌شده دارند.",
    "activity": "لاگ ممیزی دستورات تغییردهنده وضعیت (خواندن‌ها ثبت نمی‌شوند). "
    "هر رکورد با SHA-256 زنجیره‌شده است (در برابر دستکاری مقاوم). یکپارچگی را با دکمه ✓ بررسی کنید.",
    "fleet": "داشبورد همه سرورها: بررسی‌های سلامت موازی + اجرای دستورات روی هر سرور. "
    "دکمه ▶ یک دستور یک‌باره یا قطعه‌کد ذخیره‌شده را به‌صورت موازی اجرا می‌کند. Stop-all همه چیز را لغو می‌کند.",
    "backups": "بکاپ‌های tar -czf از پوشه‌های سرور. استثناها، بررسی اندازه/فضای آزاد، "
    "دانلود اختیاری به گوشی، کار کرون روزانه اختیاری با حذف پس از N روز.",
    "alerts": "هشدارها: آستانه‌های بحرانی (CPU/RAM/دیسک) + سرویس‌های تحت نظر. "
    "تعدیل‌شده (۲ بررسی متوالی، ۳۰ دقیقه فاصله). اعلان‌های درون‌برنامه‌ای؛ اعلان‌های سیستم‌عامل از طریق hook.",
    "health": "امتیاز سلامت: ۰-۱۰۰، از یک دستور فقط‌خواندنی + فهرست به‌روزرسانی محاسبه می‌شود. "
    "هر جریمه خود را توضیح می‌دهد و به صفحه‌ای که آن را اصلاح می‌کند هدایت می‌کند.",
    "diskusage": "تجزیه‌وتحلیل استفاده از دیسک با نوارها + دستیار پاک‌سازی: کش بسته‌ها، journal، "
    "لاگ‌های چرخیده، داده‌های داکر استفاده‌نشده، بسته‌های استفاده‌نشده/کرنل‌های قدیمی. فضای آزادشده را نشان می‌دهد.",
    "databases": "پایگاه‌های داده: MySQL/MariaDB، PostgreSQL، SQLite، Redis از طریق CLI سرور. "
    "SQL طبقه‌بندی‌شده: خواندن اجرا می‌شود، نوشتن پرسیده می‌شود، DROP/TRUNCATE بدون WHERE نیاز به RUN تایپ‌شده دارد. "
    "خروجی‌ها gzip هستند؛ بازیابی نیاز به RESTORE تایپ‌شده دارد.",
    "tunnels": "تونل‌های SSH: ارسال‌های محلی فقط به 127.0.0.1 متصل می‌شوند. هنگام قطع اتصال متوقف می‌شوند. "
    "مورد علاقه برای هر سرور.",
    "envedit": "ویرایشگر .env: فقط خطوطی را که لمس می‌کنید تجزیه/ویرایش می‌کند. کلیدهای مخفی‌نما را ماسک می‌کند. "
    "هرگز مقادیر را ثبت یا ارسال نمی‌کند. راه‌اندازی مجدد اختیاری سرویس/کانتینر پس از ذخیره.",
    "wireguard": "مدیر VPN WireGuard: مشاهده وضعیت رابط (در حال اجرا/متوقف)، شروع/توقف "
    "با wg-quick، مشاهده همتایان و IPهای مجاز آنها. پیکربندی از /etc/wireguard/wg0.conf خوانده می‌شود.",
    "k8s": "مدیر کوبرنتیز: فهرست pods، استقرارها، سرویس‌ها. مشاهده لاگ‌های pod، exec در یک pod. "
    "از kubectl خود سرور استفاده می‌کند — چیزی توسط برنامه نصب نمی‌شود.",
    "proxy": "مدیر پراکسی معکوس: Caddy یا Traefik را تشخیص می‌دهد. وضعیت را نشان می‌دهد (در حال اجرا/متوقف). "
    "Caddy: تولید قالب‌های پیکربندی (سایت ایستا، پراکسی معکوس، PHP-FPM) با دکمه کپی.",
    "tools": "هاب ابزارها: یک ضربه به هر صفحه مدیریتی.",
    "settings": "تنظیمات: پوسته، نظارت، فایل‌ها، دستیار هوش مصنوعی، ترمینال، ویرایشگر، امنیت، "
    "بکاپ و بازیابی، همزمانی عملیات گروهی، دستورات هوش مصنوعی برای هر سرور.",
    "servers": "مدیر اتصال: افزودن/ویرایش/تکثیر/حذف سرورها. برچسب‌ها، رنگ، jump host. "
    "دکمه تست دسترسی را بررسی می‌کند. وارد/خروجی بسته‌های رمزگذاری‌شده.",
}


def get_help(key: str, language: str = "en") -> str:
    """Return the help text for *key* in the user's language.

    Falls back to English if no Persian translation is available for the key,
    and to a generic 'no help available' message if the key isn't found.
    """
    if language == "fa" and key in PERSIAN_PAGE_HELP:
        return PERSIAN_PAGE_HELP[key]
    return PAGE_HELP.get(key, "No help available for this page.")
