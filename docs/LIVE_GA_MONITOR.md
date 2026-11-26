# مانیتور زنده و توقف ایمن

```python
result = run_evolution(context, settings,
    on_generation=receive_progress,
    stop_requested=stop_event.is_set)
```

هر `EvolutionProgress` شامل GenerationStatistics واقعی، کپی مستقل اعضای جمعیت کامل، BestOverall، BestFeasible و شمار نسل‌های رکود است. رویداد ابتدا برای نسل صفر و سپس فقط پس از تکمیل هر نسل منتشر می‌شود. callback هیچ Random دریافت یا مصرف نمی‌کند و Fitness دوباره محاسبه نمی‌کند. کپی ژن‌ها از موتور جداست؛ تغییر احتمالی گیرنده به والد/Elite/جمعیت موتور سرایت نمی‌کند. ارزیابی‌ها و آمار تغییرناپذیرند. خطای observer به EvolutionFailure همراه آخرین نتیجهٔ کامل تبدیل می‌شود.

Stop در مرز نسل کامل بررسی می‌شود؛ ممکن است تکمیل جمعیت اولیه یا یک نسل زمان ببرد. توقف دستی worker را kill نمی‌کند؛ آخرین جمعیت کامل، BestOverall/BestFeasible و گزارش ذخیره می‌شوند و وضعیت Stopped است. stop_reasons می‌تواند هم‌زمان چند دلیل داشته باشد، مثلاً manual_stop و max_generations. توقف FirstFeasible، Stagnation و FitnessBudget قواعد قبلی موتور را حفظ کرده‌اند.

Stagnation مطابق `(HardPenalty,-SoftScore)` است؛ هر کاهش Hard یا افزایش Soft در Hard مساوی شمارنده را صفر می‌کند. اتمام رکود اثبات optimum نیست. Soft نمودار «بهترین» مربوط به برترین عضو Hard-first جمعیت است، نه بیشترین Soft مستقل آن نسل.

مرورگر هر ۱٫۵ ثانیه با cursor آخرین نسل دریافت‌شده polling می‌کند؛ هر پاسخ حداکثر ۱۰۰ رویداد دارد. پس از reconnect رویدادهای ازدست‌رفته با همان cursor بازیابی می‌شوند. نمودارها فقط دادهٔ نسل‌های واقعی را append می‌کنند و نسل صفر دیده می‌شود. هیچ صف نامحدود به‌ازای client وجود ندارد. بازکردن تقویم اجرای GA را متوقف نمی‌کند. شش نمودار Hard/Soft بهترین و میانگین، یکتایی جمعیت و هزینهٔ تجمعی ارزیابی/زمان موجود است.

پنل عملگر نسل انتخابی از OperatorStatistics استفاده می‌کند: Eliteهای واقعی و fitness آنان، حفظ بهترین عضو قبلی، Tournament/Self-mating، Course-Based/Clone، وضعیت و نوع Mutation، وضعیت و هزینه/حدود Repair و مقایسهٔ فرزند با دو والد. فرزند بدتر همچنان پذیرفته می‌شود؛ Greedy Replacement ساخته نشده است.

Snapshotهای بهترین نسل با BestOverall متفاوت‌اند. اولی عضو واقعی جمعیت همان نسل است؛ دومی می‌تواند پیش از Mutation یا در میانهٔ نسل دیده شده باشد. UI این سه عنوان را جدا نگه می‌دارد: بهترین جمعیت، بهترین کل، بهترین معتبر. نبود feasible صریحاً نمایش داده می‌شود؛ Hard مثبت هرگز «برنامه معتبر» نیست.

## اجرای Windows

۱. پوشهٔ پروژه را در File Explorer باز و روی `start_ui.bat` دوبار کلیک کنید. Python 3.11+ باید نصب باشد. Launcher Python را بررسی می‌کند، `.venv-ui` محلی می‌سازد و وابستگی‌ها را فقط در اولین اجرا یا تغییر فایل requirements نصب می‌کند.

۲. صفحهٔ `http://127.0.0.1:8000` باز می‌شود. از «آزمایش جدید»، Dataset B یا فایل خودتان را انتخاب کنید. صحت ساختاری فایل و هشدارها را ببینید.

۳. برای آزمایش سریع جمعیت ۱۰، نسل ۳، Tournament=3 و Elite=2 را وارد کنید. Seed و سایر تنظیمات در گزارش ثبت می‌شود. سپس شروع را بزنید و نمودارهای واقعی را ببینید.

۴. در «کروموزوم و برنامه هفتگی»، نسل و کروموزوم را انتخاب کنید. نقش دانشجو/استاد/اتاق و شناسه را تغییر دهید. با کلیک هر جلسه جزئیات و نقض‌های واقعی را ببینید. دریافت Excel دقیقاً مربوط به snapshot منتخب است.

۵. برای توقف GA از «درخواست توقف ایمن» استفاده کنید و منتظر پایان نسل جاری بمانید. برای بستن کل سرور در پنجرهٔ launcher یک بار Ctrl+C بزنید و منتظر پیام shutdown بمانید. بستن اجباری پنجره می‌تواند نسل جاری را ناتمام بگذارد؛ نسل‌های قبلی روی دیسک‌اند.

PyCharm و دسترسی Administrator لازم نیست؛ تنظیم سراسری Windows تغییر نمی‌کند. اگر port اشغال است: `start_ui.bat --port 8001`. نصب اولیه اینترنت لازم دارد؛ Bootstrap/Chart.js به‌صورت محلی همراه مخزن‌اند. در صورت خطای نصب، همان پنجره متن خطا را نگه می‌دارد.

راه‌اندازی دستی:

```powershell
py -3 -m venv .venv-ui
.venv-ui\Scripts\python.exe -m pip install -r requirements-ui.txt
.venv-ui\Scripts\python.exe scripts/start_ui.py
```

آزمون‌ها:

```powershell
$env:PYTHONPATH='src'
.venv-ui\Scripts\python.exe -m unittest discover -s tests -v
node --check src/scheduler_web/static/app.js
```

Node برای استفادهٔ عادی لازم نیست؛ فرمان آخر فقط بررسی نحوی frontend در توسعه است. تست عادی شامل ۵۰۰ نسل نیست؛ fixture کوچک و یک integration کوتاه Dataset B دارد.
