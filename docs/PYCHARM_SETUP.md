# اجرای پروژه در PyCharm روی Windows

پروژه فعلاً ابزار خط فرمان و خروجی JSON دارد؛ رابط گرافیکی ندارد. اجرای موتور، خواندن XLSX و آزمون‌های اصلی فقط به کتابخانه استاندارد Python نیاز دارند. Python 3.11 به بالا لازم است؛ نسخه 3.13 در توسعه بررسی شده است. برای اجرای فایل XLSX موجود نیازی به نصب Excel یا نویسندهٔ Spreadsheet نیست.

1. در PyCharm، مخزن `https://github.com/Mysistani/Course-Selection-System.git` را Clone کنید یا پوشهٔ مخزن را با Open باز کنید. پوشهٔ دارای README و src را انتخاب کنید.
2. شاخهٔ `codex/evolution-loop-phase6` را انتخاب و آخرین تغییرات آن را دریافت کنید. اگر در پوشه فعلی تغییر ذخیره‌نشده دارید، پیش از همگام‌سازی آن را بررسی کنید؛ از reset اجباری استفاده نکنید.
3. در تنظیمات Python Interpreter، یک محیط Virtualenv با Python 3.11+ و مسیر `.venv` بسازید و به پروژه اختصاص دهید. نام و جای منو ممکن است با نسخه IDE فرق کند.
4. روی پوشهٔ src راست‌کلیک و Mark Directory as → Sources Root را انتخاب کنید تا importها در ویرایشگر شناخته شوند. اسکریپت‌های اجرا خودشان مسیر src را آماده می‌کنند.
5. از فهرست Run، ابتدا **Dataset B smoke** را اجرا کنید. سپس **Dataset B experiment** برای آزمایش ۴۰۰۰ Fitness یا **All tests** را انتخاب کنید. Interpreter هر تنظیم باید Project Interpreter باشد. اگر تنظیم به‌علت تفاوت نام Module شناخته نشد، در Edit Configurations ماژول پروژهٔ خودتان را انتخاب کنید.

اگر تنظیمات اشتراکی نمایش داده نشدند، فایل `scripts/run_evolution_experiment.py` را باز و یک Python Run Configuration با Working directory برابر ریشه پروژه بسازید. برای اجرای کوتاه، این Parameters را وارد کنید:

```text
--workbook data/Dataset_B_Constructive_seed20261103.xlsx --population-size 10 --max-generations 2 --random-seed 11 --max-fitness-evaluations 100 --output outputs/PYCHARM_SMOKE.json
```

راه معادل در Terminal پروژه:

```powershell
python scripts/run_evolution_experiment.py --workbook data/Dataset_B_Constructive_seed20261103.xlsx --population-size 10 --max-generations 2 --max-fitness-evaluations 100 --random-seed 11 --output outputs/PYCHARM_SMOKE.json
python scripts/run_tests.py
```

گزارش و برنامهٔ بهترین جواب در outputs/PYCHARM_SMOKE.json و outputs/PYCHARM_SMOKE_solutions.json ذخیره می‌شوند. وجود خروجی به معنی معتبر بودن برنامه نیست؛ feasible_found و hard_penalty را بررسی کنید. گواهی Dataset B ورودی GA نیست.

رسم نمودار به Matplotlib اختیاری وابسته است. بازتولید Excel جدید به bundled artifact-tool نیاز دارد؛ برای استفاده از دادهٔ موجود هیچ‌کدام لازم نیستند. آزمون بازتولید Excel در نبود runtime اختیاری skip می‌شود. مقایسهٔ موتور pinشده نیز به Git و تاریخچه commit مرجع نیاز دارد؛ جزئیات در CONTROLLED_DATASET_GENERATOR.md است.

پوشهٔ .idea و محیط مجازی شخصی وارد Git نمی‌شوند؛ تنظیمات قابل‌اشتراک در .run قرار دارند. در زمان آماده‌سازی، ویرایش مستقلی در initialization.py وجود داشت که preparation_seconds را حذف کرده بود و آزمون مربوط به آن را ناسازگار می‌کند؛ این ویرایش در commit راه‌اندازی وارد نشده است. برای آزمون نسخهٔ مرجع بدون دست‌زدن به آن تغییر، `python scripts/test_controlled_baseline.py` را اجرا کنید.
