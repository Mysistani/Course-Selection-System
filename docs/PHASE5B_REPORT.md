# گزارش Phase 5B

تاریخ: ۲۰۲۶-۱۱-۰۳. شاخه: `codex/mutation-repair-5b`، پایه main: `a93f096`. قرارداد API، دلایل شکست و تصمیم‌های باز در [MUTATION_REPAIR.md](MUTATION_REPAIR.md) ثبت شده‌اند. PR و Merge با کاربر است.

## تغییرات

- `src/scheduler/mutation.py`: هر ۱۰ عملگر، اجرای مستقیم، انتخاب محدود و دروازه احتمال جدا.
- `src/scheduler/repair.py`: Structural Repair با rollback و Conflict Repair محدود با پذیرش سخت‌اول.
- `src/scheduler/operator_support.py`: پیش‌نویس اتمی، Snapshot مقید به fingerprint/Context و شمارش ارزیابی‌ها.
- `src/scheduler/chromosome.py`: فقط تعمیم نوع ورودی validator به نمای فقط‌خواندنی نگاشت برای کنترل پیش از ساخت؛ ساختمان ژن‌ها و API قدیمی حفظ شده‌اند.
- `tests/test_mutation_repair.py`، ابزار `scripts/check_mutation_repair.py` و اسناد/وضعیت به‌روز شدند.

Excel، Dataset، EvaluationContext، فرمول‌های Hard/Soft، Initialization، Selection و Crossover تغییری نکردند. Evolution Loop ساخته نشده است.

## آزمون‌ها

فرمان `python -m unittest discover -s tests -q` با PYTHONPATH=src: **۱۹۹ آزمون موفق**، شامل ۱۶۱ آزمون پیشین و ۳۸ آزمون جدید؛ زمان اجرای ثبت‌شده ۴٫۶۴ ثانیه روی Python 3.13.

آزمون‌ها هر ۱۰ عملگر، مرزهای ۱۵/۱۶، حداقل ۸، ظرفیت ادغام و انتقال، None، Slot ممنوع/تکراری، تک‌استاد/تک‌آزمایشگاه، نبود منابع، استقلال والد/Elite/cache، اتمی بودن شکست پس از آغاز ساخت پیش‌نویس، Seed و PYTHONHASHSEED متفاوت، fallback هدفمند، Snapshot کهنه و Context ناسازگار، بودجه تلاش/ارزیابی صفر و مثبت و داده ساختاری خراب را پوشش می‌دهند. سقف دقیق ۵ کاندیدا برای هر تعارض و ۵۰ برای کل فراخوانی با شمارش واقعی فراخوانی تابع آزموده شد؛ برای اشباع بودجه از پیشنهادهای کنترل‌شده ولی ساختاری سالم و شناسه‌های واقعی fixture استفاده شد. نتایج برابر و بدتر با آزمون Comparator کنترل و رد شدند؛ بهبود واقعی تداخل هم بدون mock آزموده شد. ۱۵ فرزند جمعیت مصنوعی نیز از Selection/Crossover به Mutation/Repair و ارزیابی کامل عبور کردند.

## آزمایش فایل واقعی

دو اجرای مستقل، هرکدام روی جمعیت ثابت ۱۰۰عضوی و ۱۰۰ فرزند Phase5A، InitializationSeed=2026 و OperatorSeed=55. یک Context برای هر اجرا. زمان‌ها اندازه‌گیری محلی‌اند و benchmark دانشگاهی نیستند.

| معیار | دروازه احتمال ۰٫۳۰ | اجرای بدون دروازه |
|---|---:|---:|
| Mutation موفق | ۲۵ | ۱۰۰ |
| skipped | ۷۵ | ۰ |
| failed/not_applicable | ۰ | ۰ |
| نرخ تغییر ژنوتیپ از کل فرزندان | ۲۵٪ | ۱۰۰٪ |
| مسیر هدفمند / fallback | ۱۶ / ۰ | ۷۰ / ۰ |
| Repair بهبود‌یافته / بدون تغییر | ۱۷ / ۸۳ | ۲۱ / ۷۹ |
| میانگین Hard قبل Repair | ۱۴۷۶٫۰۸ | ۱۴۷۷٫۱۷ |
| میانگین Hard بعد Repair | ۱۴۷۵٫۸۵ | ۱۴۷۷٫۰۳ |
| ساختار سالم | ۱۰۰/۱۰۰ | ۱۰۰/۱۰۰ |
| Fitness جمعیت اولیه | ۱۰۰ | ۱۰۰ |
| Fitness صریح فرزند/بعد Mutation | ۱۲۵ | ۲۰۰ |
| Fitness داخل Mutation (Snapshot داده شد) | ۰ | ۰ |
| Fitness کاندیدا داخل Repair | ۷۵ | ۷۰ |
| مجموع Fitness، معادل تعداد Hard و نیز Soft | ۳۰۰ | ۳۷۰ |
| پیش‌نویس/کاندیدای rollbackشده | ۶۱ | ۴۸ |
| میانگین زمان مسیر هر فرزند | ۰٫۰۵۹۷ ثانیه | ۰٫۰۷۵۲ ثانیه |

همه خروجی‌های Repair طبق Comparator نسبت به ورودی همان Repair برابر یا بهتر بودند؛ بهبود ۲۱ مورد الزاماً به معنی کاهش Hard در هر ۲۱ مورد نیست، زیرا Soft در تساوی Hard ملاک دوم است. قیود سخت هنوز نقض دارند؛ این خروجی‌ها برنامه معتبر معرفی نمی‌شوند. کمبود تقاضا/منابع و تصادفی‌بودن داده فعلی معیار کیفیت نهایی موتور نیستند.

| نوع Mutation موفق | دروازه احتمال | بدون دروازه |
|---|---:|---:|
| MoveMeeting | ۵ | ۱۴ |
| SwapMeetings | ۳ | ۹ |
| ChangeProfessor | ۳ | ۱۳ |
| ChangeRoom | ۳ | ۹ |
| SplitSection | ۳ | ۱۰ |
| MergeSections | ۰ | ۸ |
| ActivateDeactivate | ۱ | ۲ |
| MoveStudent | ۳ | ۹ |
| SwapStudents | ۳ | ۱۵ |
| AssignUnassign | ۱ | ۱۱ |

هیچ والد، cache یا فایل مبنایی تغییر نکرد. SHA256 فایل XLSX قبل و بعد:

`e5fc5786323ef43c8bb5c4ea0d5beea1e555a86b1b566da42f070e269526f664`

گزارش‌های ریز محلی در outputs/MUTATION_REPAIR_5B_REPORT.json و outputs/MUTATION_REPAIR_5B_UNCONDITIONAL.json هستند. سقف ۱۰۰ تلاش باعث می‌شود بودجه ۵۰ ارزیابی در این نمونه اغلب پر نشود؛ این نتیجه عمداً با اعداد واقعی ثبت شده است. بهینه‌سازی هدف‌گیری برای تعارض‌های کسری واحد و تأیید سیاست‌های آزمایشی، کار معماری/آزمایش بعدی است. provenance کروموزوم خام نیز تصمیم باز است؛ جزئیات در سند قرارداد آمده است.
