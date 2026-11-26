# Conflict-Aware Meeting Relocation — Repair 8

این enhancement فقط مسیر Repair تعارض `H04_ROOM_OVERLAP` را هدف می‌گیرد؛ هیچ قید، Fitness، وزن، Mutation، Crossover، Selection، Initialization، schema یا رفتار Seed تغییر نکرده است.

## API سازگار

```python
from random import Random
from scheduler.repair import RepairSettings, repair_conflicts

result = repair_conflicts(chromosome, context, Random(11),
    RepairSettings(), evaluation=existing_snapshot)

# بازگشت به مسیر پیشنهادهای قدیمی برای مقایسه/rollback:
legacy = repair_conflicts(chromosome, context, Random(11),
    RepairSettings(conflict_aware_relocation=False), evaluation=existing_snapshot)
```

`RepairSettings.version='repair-8-v1'` و `conflict_aware_relocation=True` پیش‌فرض جدیدند. پارامتر جدید در انتهای dataclass افزوده شده؛ فراخوانی‌های قدیمی معتبرند. همهٔ حدود پیشین حفظ شده‌اند: `max_local_candidates=5`، `max_candidate_evaluations=50`، `max_candidate_attempts=100`، `max_conflicts=50` و `max_structural_steps=20`. baseline ارائه‌نشده همچنان یک Fitness جدا مصرف می‌کند؛ snapshot معتبر این فراخوانی را حذف می‌کند. ساختار خروجی قبلی حفظ و فیلدهای گزارش با defaults اضافه شده‌اند.

CLI و Web UI بدون بازنویسی موتور از تنظیمات جدید استفاده می‌کنند؛ حدود واقعی و flag در nested RepairSettings گزارش می‌شوند. برای استفاده در یک سرور ازقبل باز، فقط پس از پایان اجرای جاری restart لازم است؛ اجرای قبلی را نباید برای اعمال کد جدید قطع کرد.

## مسیر هدفمند

۱. نقض‌های اتاق، با ترتیب پایدار، پیش از پیشنهادهای عمومی پردازش می‌شوند. زوجی که بعد از یک تغییر پذیرفته‌شده دیگر در نتیجهٔ canonical نیست، کنار گذاشته می‌شود. `max_conflicts` همچنان سقف تعداد رکوردهای مورد بررسی است.

۲. `section_ids` و `slot_keys` همان Violation مرجع با هم تطبیق داده می‌شوند؛ meeting_index هر دو سمت مشخص است. پیشنهادهای خام دو سمت به‌صورت round-robin تولید می‌شوند. مسئلهٔ self-overlap دو جلسهٔ یک گروه هم با indexهای متفاوت قابل بررسی است.

۳. ابتدا تا `2 * max_local_candidates` پیشنهاد خام با همان استاد/اتاق، با اولویت روز فعلی و فاصلهٔ زمانی نزدیک‌تر بررسی می‌شود؛ سپس تعویض سازگار اتاق در زمان اصلی، سپس بقیهٔ جابه‌جایی‌های همان اتاق و سرانجام سایر ترکیب‌های زمان/اتاق. این **ترتیب bounded** به معنی جست‌وجوی همهٔ زمان‌ها پیش از fallback نیست. هیچ product نامحدود یا retry برای رسیدن به جواب ساخته نمی‌شود.

۴. از باقی‌ماندهٔ سقف تلاش، سهم هر زوج اتاق تقریباً `remaining_attempts // pending_room_pairs` است؛ زوج اول نمی‌تواند همهٔ ۱۰۰ تلاش را ببلعد. این سقف داخلی در کنار سقف جهانی و ۵/۵۰ ارزیابی اعمال می‌شود؛ با بودجهٔ بسیار کوچک بررسی همهٔ endpointها تضمین نمی‌شود. تولید/فیلتر هر پیشنهاد خام، حتی ردشده، یک تلاش مصرف می‌کند. hash تکراری، مقصد نامعتبر یا پیشنهاد نامرتبط Fitness تازه نمی‌سازد.

۵. `RelocationIndex` از mapping اعضای `_State` و جلسات فعلی، اشغال اتاق/استاد/دانشجو را برای هر روز می‌سازد. Context موجود نگاشت ظرفیت مؤثر، فضاهای سازگار و بازه‌های unavailable را می‌دهد. همان تابع `_overlap` ارزیاب مرجع استفاده می‌شود؛ مقایسهٔ صرف SlotID کافی نیست. فاصلهٔ دو بازهٔ مجاور تداخل نیست.

۶. پیش‌فیلتر فقط اثر تغییر پیشنهادی را غربال می‌کند: Slot مجاز، طول دقیقاً برابر جلسهٔ قبلی، عدم تکرار جلسه، ظرفیت/نوع اتاق، اشغال اتاق، استاد و تمام دانشجویان گروه، و نبود زمان unavailable در مقصد. زمان واقعی از Slot خوانده می‌شود؛ ۹۰ دقیقه hardcode نشده است. دقیقاً presence قدیمی جلسهٔ منتخب از occupancy حذف می‌شود؛ سایر جلسات همان گروه هنوز مانع واقعی‌اند.

۷. چون RoomID سطح گروه است، اتاق جایگزین در **همهٔ جلسات گروه** ثابت می‌ماند؛ تمام آن جلسات برای اتاق جدید کنترل می‌شوند. زمان هیچ جلسهٔ دیگر، استاد، تعداد جلسات، SectionID/CourseID و هیچ Enrollment تغییر نمی‌کند. room-only fallback ممکن است RoomID گروه را عوض کند؛ اتاق مستقل per-meeting اضافه نشده است. عوض‌نشدن مدت، Enrollment، استاد و تعداد جلسات به معنی ثابت‌ماندن اثر بر واحدها، PassedCourses و سقف تدریس است؛ صحت کامل همچنان فقط توسط evaluator تعیین می‌شود.

۸. پس از `commit_draft` و کنترل fingerprint، حتماً `evaluate_fitness` مرجع اجرا می‌شود. پذیرش فقط با comparison_key کوچک‌تر است: کاهش Hard یا در Hard برابر افزایش Soft. Soft نمی‌تواند Hard بالاتر را جبران کند. والد و cache او تغییر نمی‌کنند؛ خروجی clone جداست. هرگز با نتیجهٔ پیش‌فیلتر به‌تنهایی تغییری پذیرفته نمی‌شود.

مسیرهای قدیمی برای سایر constraintها حفظ شده‌اند. بنابراین ادعای «هیچ category دیگری هرگز در کل Repair تغییر نمی‌کند» نمی‌کنیم؛ محدودسازی به تک‌جلسه و ثابت‌ماندن Enrollmentها مربوط به مسیر جدید تعارض اتاق است.

## هزینه و determinism

ساخت index متناسب با اعضا و جلسات موجود است، نه اندازهٔ یک فضای ضرب دکارتی مقصدها. در هر فراخوانی Repair، index با fingerprint کروموزوم cache می‌شود و فقط بعد از تغییر پذیرفته‌شده دوباره ساخته می‌شود. پیشنهادهای محلی هر زوج از یک state ثابت ساخته می‌شوند؛ هر کاندیدای ارزیابی‌شده با بهترین نتیجهٔ canonical فعلی مقایسه می‌شود و در زوج بعدی state جدید مبناست.

ترتیب Section/Slot/Room صریحاً مرتب است؛ مسیر هدفمند RNG مصرف نمی‌کند. مسیرهای عمومی همچنان از RNG محلی موجود استفاده می‌کنند. خروجی برای همان seed/input/version تکرارپذیر است؛ مسیر GA بین نسخهٔ قدیم/جدید می‌تواند به علت مصرف متفاوت RNG در Repair متفاوت شود. زمان‌های wall-clock بخشی از تضمین تکرارپذیری نیستند.

فیلدهای جدید RepairReport: `relocation_attempts`، `relocation_filtered`، `relocation_evaluations`، `relocation_accepted`، `relocation_index_builds`، `relocation_index_seconds`، `relocation_filter_seconds` و `room_conflicts_resolved`. شمار آخر، تعداد رکوردهای تعارض اتاق ورودی است که در خروجی نمانده‌اند؛ با تعداد زوج‌های یکتای همهٔ افراد/نسل‌ها اشتباه نشود. timerهای جزئی تمام مرتب‌سازی و overhead را پوشش نمی‌دهند؛ زمان کل Repair در benchmark جدا سنجیده شده است.

## محدودیت واقعی

جابه‌جایی امن تک‌جلسه همیشه ممکن نیست. در snapshot واقعی Hard=4 این فاز، هیچ مقصد admissible در مجموعهٔ محدود همهٔ حرکات یک جلسه/اتاق برای دو زوج یافت نشد. عبور موقت از Hard بالاتر، coordinated swap، تغییر استاد/Enrollment در مسیر هدفمند و تغییر schema پیاده نشده‌اند. نتیجه و جزئیات بدون ادعای Hard=0 در [PHASE8_REPORT.md](PHASE8_REPORT.md) ثبت شده است.
