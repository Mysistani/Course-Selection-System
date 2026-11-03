# معماری رابط مدیریت آموزش — Phase 7

این برنامهٔ محلی تک‌مدیر با FastAPI، Jinja2، Bootstrap 5 RTL، Chart.js و JavaScript ساده اجرا می‌شود. موتور `scheduler` مرجع محاسبات است؛ frontend هیچ Hard/Soft یا تصمیم ژنتیکی محاسبه نمی‌کند. نقش دانشجو/استاد برای ورود وجود ندارد؛ فقط نمایش برنامهٔ آنان است.

## لایه‌ها

- `scheduler/evolution.py`: دو ورودی اختیاری `on_generation` و `stop_requested`؛ رفتار پیش‌فرض API قبلی حفظ شده است. تنها این فایل موتور تغییر کرده؛ Selection/Crossover/Mutation/Repair/Fitness دست نخورده‌اند.
- `scheduler_web/services.py`: اعتبارسنجی بارگذاری، Context، یک Thread کنترل‌شده، چرخهٔ وضعیت، فایل‌های اتمی، نگهداری snapshot و تاریخچه.
- `scheduler_web/projection.py`: تبدیل ژن‌ها و Enrollmentها به تقویم و Excel؛ استفاده از ارزیابی ذخیره‌شده، بدون Fitness تازه.
- `scheduler_web/app.py`: API، محدودسازی میزبان، token محلی درخواست‌های نوشتنی، template و فایل‌های محلی frontend.

Thread اجرای GA مستقل از درخواست‌های وب است. برنامه با **یک Uvicorn worker** اجرا شود؛ چند فرایند وب یا اجرای هم‌زمان دو سرور روی storage مشترک پشتیبانی نمی‌شود. قفل RLock مانع اجرای هم‌زمان دو آزمایش در همان فرایند است. refresh مرورگر worker را متوقف نمی‌کند.

## داده و ذخیره‌سازی

مسیر پیش‌فرض `outputs/web_ui` و خارج از Git است؛ متغیر `SCHEDULER_UI_STORAGE` مسیر را تغییر می‌دهد. `datasets/<uuid>/input.xlsx` کپی مستقل ورودی است؛ نام مشتری هرگز مسیر فایل را تعیین نمی‌کند. متادیتا شامل نام پاک‌سازی‌شده، SHA-256، خطا، هشدار، Diagnostics و شمارش واقعی است. فقط xlsx تا ۱۰ MiB پذیرفته می‌شود؛ ZIP پیش از Loader از نظر تعداد اجزا و حجم بازشده محدود می‌شود. هیچ گواهی feasible یا certificate وارد مسیر اجرای GA نمی‌شود. Dataset A/B با همان مسیر بارگذاری کپی می‌شوند و مبنا تغییر نمی‌کند.

هر اجرا شناسهٔ UUID، Dataset hash، ContextID، تنظیمات کامل، زمان UTC قابل ردیابی، وضعیت، رویدادهای نسل کامل و گزارش نهایی دارد. فایل JSON ابتدا در `.tmp` نوشته و با replace اتمی جایگزین می‌شود. گزارش آخرین نسل کامل مستقل از نتیجهٔ نهایی پایدار است. پس از crash، Running/Stopping به Failed با توضیح قطع اجرا تبدیل می‌شود؛ ازسرگیری RNG وجود ندارد.

### سیاست snapshot

- جمعیت کامل جاری + N جمعیت قبلی؛ N پیش‌فرض ۱۰ و مجاز ۰ تا ۲۰ است.
- بهترین فرد **جمعیت** هر نسل کامل همیشه روی دیسک می‌ماند؛ ارزیابی تازه یا بازسازی حدسی نیست.
- BestOverall و BestFeasible جدا ذخیره می‌شوند؛ ممکن است مربوط به مرحلهٔ میانی یک نسل باشند.
- Full populationهای قدیمی پس از eviction حذف می‌شوند؛ API و UI صریحاً فقط بهترین همان نسل را عرضه می‌کنند.
- دکمهٔ «حفظ کامل این نسل» فقط یک جمعیت موجود را از eviction معاف می‌کند. این انتخاب‌های صریح، فضا مصرف می‌کنند؛ سقف خودکار دیسک برای تاریخچه یا نسل‌های pinned نداریم.
- snapshot با `solution_payload` به JSON پایدار تبدیل می‌شود؛ fingerprint ژن و Context ارزیابی پیش از ذخیره کنترل می‌شود. هیچ Chromosome زنده‌ای به درخواست HTTP داده نمی‌شود. خواندن snapshot یک شیء JSON مستقل برمی‌گرداند.

جمعیت‌های کامل همهٔ نسل‌ها در RAM نگهداری نمی‌شوند؛ موتور جمعیت فعلی و callback کپی موقت دارد، فایل‌ها روی دیسک‌اند. آمار جمع‌وجور همهٔ نسل‌ها در RAM و متادیتا نگهداری می‌شود؛ با تعداد نسل‌ها رشد می‌کند. بهترین snapshot هر نسل روی دیسک است؛ برای اجرای بسیار طولانی باید فضای کافی در نظر گرفت. سریال‌سازی هر نسل و نوشتن متادیتا سربار قابل اندازه‌گیری دارد و زمان UI را نباید benchmark خام موتور دانست.

## API

تمام endpointها زیر `/api` هستند؛ IDها تولیدشدهٔ سرورند و مسیر arbitrary دانلود وجود ندارد.

| روش / مسیر | قرارداد |
|---|---|
| GET `/state` | فهرست داده‌ها، تاریخچهٔ اجراها، active_run_id و Idle/وضعیت فعال |
| POST `/datasets/upload` | multipart با `file`؛ متادیتای valid/errors/warnings/counts |
| POST `/datasets/examples/A` یا `/B` | کپی و اعتبارسنجی مثال موجود |
| POST `/runs` | JSON: `dataset_id`, `settings`؛ خطای 409 در اجرای هم‌زمان |
| GET `/runs/{id}/events?after=-1&limit=100` | رویدادهای مرتب نسل کامل، metadata اجرا و cursor بعدی؛ حداکثر ۱۰۰ ردیف |
| POST `/runs/{id}/stop` | درخواست تعاونی؛ وضعیت Stopping تا مرز نسل |
| GET `/runs/{id}/generations/{g}` | جدول خلاصهٔ جمعیت یا فقط best؛ پرچم `full_population_available` |
| POST `/runs/{id}/generations/{g}/persist` | حفظ جمعیت کامل موجود روی دیسک |
| GET `/runs/{id}/entities` | شناسه‌های واقعی دانشجو/استاد/اتاق و نام موجود |
| GET `/runs/{id}/snapshot?generation=g&selection=...` | summary و snapshot ژنوتیپ با Fitness ذخیره‌شده |
| GET `/runs/{id}/timetable?...&role=student&entity_id=...` | تقویم همان snapshot؛ role می‌تواند professor/room باشد |
| GET `/runs/{id}/download/{xlsx,json,report}` | فقط خروجی مجاز ساخته‌شده از snapshot انتخاب‌شده؛ report پس از پایان/خطا موجود است |

`selection`: شمارهٔ فرد جمعیت، `best` نسل انتخابی، `best_overall`، `best_feasible` یا `final_population_best`. برای انتخاب‌های کلی، `observed_generation` زمان اولین مشاهدهٔ همین fingerprint را نشان می‌دهد. این مشخصه نسب‌نامه یا مراحل کامل فرزند نیست.

تنظیمات از EvolutionSettings/RepairSettings فعلی اعتبارسنجی می‌شوند. بودجهٔ اختیاری Fitness پیش‌فرض None است؛ cap مخفی اضافه نشده است. retention یک تنظیم نمایشی مستقل است. UI پیش‌فرض Seed=11 دارد؛ همهٔ تنظیمات واقعی در گزارش ثبت می‌شوند.

## تقویم و خروجی

برنامهٔ دانشجو از Enrollmentهای همان snapshot، استاد/اتاق از SectionGeneهای فعال ساخته می‌شود. هر meeting یک event دارد؛ زمان شروع/پایان از Slot واقعی خوانده می‌شود، نه فرض ۹۰ دقیقه. فقط روزهای Dataset نشان داده می‌شوند؛ نام روزهای شناخته‌شده با ترتیب هفتهٔ فارسی نمایش داده می‌شود. laneهای مستقل جلسات متداخل را کنار هم نشان می‌دهند. overlapهای نمایشی محاسبات هندسی بازه‌اند؛ مقدار Hard/نقض مرجع همیشه نتیجهٔ evaluator است. نقض‌های شخصی افراد دیگر به فرد منتخب نسبت داده نمی‌شوند.

در Dataset فعلی StudentName و RoomName وجود ندارد؛ برای این دو شناسه نمایش داده می‌شود، نامی جعل نمی‌شود. ProfessorName و CourseName از Dataset می‌آید.

Excel با openpyxl دارای چهار شیت Faculty Timetable، Student Enrollments، Student Weekly Timetables و Validation Report است. هر جلسه و درخواست None صریح ثبت می‌شود. گزارش شامل Dataset hash، RunID، نسل مشاهده، fingerprint، Hard/Soft، اعتبار و تمام رکوردهای واقعی نقض است. Hard مثبت، هشدار «برنامه پیشنهادی نامعتبر — دارای نقض محدودیت‌های سخت» در گزارش و header شیت‌ها دارد. رشته‌های ورودی به‌صورت text نوشته می‌شوند تا نام درس شبیه فرمول اجرا نشود. SheetNameها برای سازگاری Excel انگلیسی‌اند؛ RTL و freeze/filter فعال‌اند.

## امنیت و محدودیت استقرار

میزبان پیش‌فرض `127.0.0.1` است؛ هیچ login یا سرویس ابری اضافه نشده است. POSTها نیازمند token تصادفی همان سرور در header هستند؛ Jinja autoescape و escape در JavaScript فعال است. دانلود تنها از run/snapshotهای شناخته‌شده مجاز است. این MVP برای شبکهٔ عمومی، چندکاربر یا چندفرایند آماده نشده است. فایل‌های تاریخچهٔ محلی باید مثل سایر داده‌های آموزشی توسط مالک سیستم نگهداری شوند.

کتابخانه‌های frontend با مجوز در `static/vendor` قرار دارند؛ CDN و اینترنت برای استفادهٔ عادی لازم نیست. اینترنت فقط برای نصب اولیهٔ بسته‌های Python لازم است. نسخه‌های مستقیم در `requirements-ui.txt` مشخص‌اند.

منابع فنی: [FastAPI Templates](https://fastapi.tiangolo.com/advanced/templates/)، [UploadFile](https://fastapi.tiangolo.com/tutorial/request-files/)، [Chart.js](https://www.chartjs.org/docs/latest/).
