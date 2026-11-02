# ساختار واقعی Excel

مبنای مشاهده: `data/UniversityDataset_Corrected.xlsx`، ۱۴ شیت، سطر نخست عنوان ستون‌ها. شمارش‌های زیر بدون سطر عنوان است. این سند فهرست مشاهده‌شده است؛ تفسیرهای پیشنهادی صریح مشخص شده‌اند. گزارش ماشینی در DATASET_AUDIT.json قرار دارد.

## شیت‌ها و کلیدها

| شیت | رکورد | کلید یکتا |
|---|---:|---|
| Faculties | 4 | FacultyID |
| Majors | 16 | MajorID |
| Courses | 120 | CourseID |
| Classrooms | 20 | RoomID |
| TimeSlots | 25 | SlotID؛ زوج Day/TimeSlot نیز باید یکتا باشد |
| Professors | 32 | ProfessorID |
| ProfessorCourses | 149 | ProfessorID + CourseID |
| ProfessorAvailability | 313 | ProfessorID + Day + TimeSlot |
| ProfessorPreferences | 128 | ProfessorID + PreferenceType |
| Students | 200 | StudentID |
| PassedCourses | 2073 | StudentID + CourseID |
| StudentRequests | 1695 | StudentID + CourseID |
| StudentAvailability | 1199 | StudentID + Day + TimeSlot |
| StudentPreferences | 800 | StudentID + PreferenceType |

## فرهنگ ستون‌ها

### Faculties

`FacultyID`: شناسه متنی مانند ENG، کلید اصلی؛ `FacultyName`: نام فارسی. ENG/SCI/MTH/HUM چهار دانشکده‌اند. وجود کاتالوگ دانشگاهی به معنای گسترش آزمایش اول به همه آن‌ها نیست.

### Majors

`MajorID`: شناسه متنی، کلید اصلی؛ `FacultyID`: کلید خارجی Faculties؛ `MajorName`: نام فارسی. ۱۶ رشته کاتالوگ وجود دارد، ولی درس/استاد/دانشجوی آزمایش فقط برای CE، EE، ME، CIV است.

### Courses

| ستون | نوع/معنا و قاعده |
|---|---|
| CourseID | متن یکتا مانند CE001؛ هنگام خواندن و صادرکردن تغییر نکند |
| MajorID | متن، FK به Majors |
| CourseName | نام فارسی |
| Credits | عدد صحیح ۱ تا ۴؛ مشاهده فعلی ۱،۲،۳ |
| SuggestedSemester | عدد صحیح ترم پیشنهادی؛ فعلی ۱ تا ۸؛ منع ترم دیگر از آن استنتاج نشود |
| CourseType | Theory، Lab یا Workshop؛ معادل الزامی/اختیاری نیست |
| SessionsPerWeek | عدد صحیح مثبت؛ فعلی ۱ یا ۲؛ تعداد جلسه گروه |
| SessionDuration | دقیقه، فعلی همه ۹۰ |
| RequiredRoomType | نوع دقیق فضای مورد نیاز |
| MinSectionSize | صحیح؛ فعلی همه ۸ |
| MaxSectionSize | سقف گروه؛ فعلی ۵۰ نظری، یا ۱۰/۱۲/۱۵ تخصصی |
| DifficultyLevel | صحیح ۱ تا ۵؛ آستانه «دشوار» باید تثبیت شود |

توزیع: ۹۳ Theory، ۲۴ Lab، ۳ Workshop. SessionDuration و SessionsPerWeek مبنای ساعت استادند، Credits مبنای واحد دانشجو است. این دو را با هم جایگزین نکنید.

### Classrooms

`RoomID`: متن یکتا؛ `FacultyID`: FK به Faculties؛ `RoomType`: نوع؛ `Capacity`: تعداد نفر صحیح مثبت؛ `Equipment`: متن فهرست جداشده با `;`، بدون نیاز به تفسیر محاسباتی در نسخه اول.

۱۵ Theory و از هرکدام ComputerLab، ElectricalLab، PhysicsLab، MechanicalWorkshop، CivilLab یک فضا وجود دارد. همه زیر ENG هستند. ظرفیت مؤثر گروه برابر min(Courses.MaxSectionSize, Classrooms.Capacity) است. Equipment فعلاً توصیفی است چون درس ستون تجهیزات لازم ندارد؛ از نام درس الزام تجهیزات استخراج نشود. نوع تخصصی باید دقیق تطبیق داده شود.

### TimeSlots

`SlotID`: متن یکتا مثل SAT-T1؛ `Day`: Saturday/Sunday/Monday/Tuesday/Wednesday؛ `TimeSlot`: T1 تا T5؛ `StartTime` و `EndTime`: در فایل متن HH:MM، به دقیقه تبدیل شوند؛ `IsAllowed`: Boolean واقعی Excel، ۲۵ مورد True.

| بازه | شروع | پایان |
|---|---|---|
| T1 | 08:00 | 09:30 |
| T2 | 10:00 | 11:30 |
| T3 | 13:30 | 15:00 |
| T4 | 15:30 | 17:00 |
| T5 | 17:30 | 19:00 |

وقفه بین T2 و T3 برابر ۱۲۰ دقیقه است؛ با قاعده مازاد ۳۰، هزینه فاصله خام آن ۹۰ دقیقه خواهد بود، مگر سیاست جدیدی برای ناهار تصویب شود. «استراحت ناهار معاف» در متن بازیابی‌شده نیست.

### Professors

`ProfessorID`: شناسه متنی یکتا؛ `MajorID`: FK به Majors؛ `ProfessorName`: نام؛ `MinHours` و `MaxHours`: ساعت واقعی هفتگی (اعشاری، مثلاً ۷٫۵). ۳۲ استاد و در هر رشته ۸ نفر. فرمول بار: مجموع SessionDuration/60 روی تمام جلسات گروه‌های فعال استاد، بدون ضرب در تعداد دانشجویان. سیاست حداقل ساعت باز است؛ سقف پیشنهادی سخت است.

### ProfessorCourses

`ProfessorID`: FK به Professors؛ `CourseID`: FK به Courses؛ `CanTeach`: Boolean واقعی، نه متن "True"؛ `Willingness`: عدد ۱ تا ۱۰، شدت تمایل (جهت بزرگ‌تر=بهتر پیشنهاد بسته). ۱۴۹ رابطه، همگی CanTeach=True. هر ۱۲۰ درس دست‌کم یک استاد واجد صلاحیت دارد. ردیف نداشتن، مجوز تدریس نیست.

### ProfessorAvailability / StudentAvailability

ستون نخست به‌ترتیب `ProfessorID` یا `StudentID`، FK به جدول شخص. سپس `Day`، `TimeSlot`، `Status`، `Weight`.

زوج Day/TimeSlot به TimeSlots متصل می‌شود. وضعیت‌های مشاهده‌شده فقط `Unavailable` (ممنوع سخت) و `Preferred` (مطلوب نرم) هستند. Weight در Unavailable خالی است و نباید به خطای وزن یا امتیاز صفر قابل جبران تبدیل شود. Weight در Preferred عدد شدت مطلوبیت است. برای استاد ۱۶۰ ردیف Unavailable و ۱۵۳ Preferred؛ برای دانشجو ۳۹۹ و ۸۰۰ ردیف است. جدول‌ها sparse هستند؛ نبود ردیف فعلاً **پیشنهاد: آزاد/خنثی**، نیازمند تثبیت سیاست. برنامه نهایی باید وضعیت ناشناخته یا ردیف متناقض را رد کند.

### ProfessorPreferences / StudentPreferences

ستون نخست `ProfessorID` یا `StudentID` (FK)، سپس `PreferenceType`، `Target`، `Weight`. Weight مستقل برای هر شخص و نوع ترجیح، در داده ۰ تا ۱۰؛ صفر مجاز و خاموش است. Target همواره «امتیاز» نیست؛ مقدار یا آستانه هدف است.

| PreferenceType | شخص | Target مشاهده‌شده | تفسیر پیشنهادی |
|---|---|---|---|
| PreferredTeachingDays | هر دو | ۲،۳،۴ | تعداد روز حضور مطلوب؛ برای دانشجو نیز نام ستون همین است |
| MinimizeGaps | هر دو | ۳۰ | دقیقه فاصله معاف |
| PreferredTimeSlots | هر دو | خالی | اهداف زمانی از Availability با Status=Preferred |
| PreferredCourses | استاد | خالی | شدت تمایل به درس از ProfessorCourses.Willingness |
| SpreadDifficultCourses | دانشجو | ۱،۲،۳ | پیشنهاد: سقف درس دشوار متمایز در روز |

خالی بودن Target در دو نوع ترجیح بالا در تمام ردیف‌های آن نوع مشاهده شده و نباید خودکار «نقص داده» نامیده شود. معنای دقیق آن‌ها از گفت‌وگوی بازیابی‌شده قطعی نیست. هر استاد/دانشجو چهار نوع ترجیح دارد؛ اطلاعات فعلی با این الگو سازگار است.

### Students

`StudentID`: متن یکتا؛ `MajorID`: FK؛ `CurrentSemester`: عدد صحیح ترم فعلی؛ `MinCredits` و `MaxCredits`: حداقل و حداکثر واحد شخصی. فعلی: ترم‌های ۳ تا ۷؛ حداقل واحد ۸/۹/۱۰/۱۲ و حداکثر ۱۵/۱۸/۲۰. مقادیر شخصی را با یک عدد سراسری بازنویسی نکنید.

### PassedCourses

`StudentID` و `CourseID`: هر دو FK و زوج یکتا. تاریخچه گذراندن برای جلوگیری از اخذ مجدد پیشنهاد می‌شود؛ از این جدول پیش‌نیاز استخراج نشود. تاریخ/نمره ندارد.

### StudentRequests

`StudentID` و `CourseID`: هر دو FK و زوج یکتا؛ `Priority`: صحیح ۱ تا ۱۰. شناسه درخواست مستقل ندارد، کلید درخواست همین زوج است. تخصیص خالی در کروموزوم یعنی درخواست پاسخ نگرفته؛ خود ردیف درخواست از ورودی حذف نشود. هیچ درخواست فعلی با PassedCourses همان دانشجو مشترک نیست.

## مدل ارتباطی و بخش‌های تولیدی

Faculties ← Majors ← Courses/Professors/Students و Faculties ← Classrooms. ProfessorCourses پیوند استاد–درس؛ PassedCourses و StudentRequests پیوند دانشجو–درس؛ Availability پیوند شخص–زمان؛ Preferences پیوند شخص–نوع ترجیح.

Section، Meeting و Assignment در Excel شیت ورودی ندارند؛ محصول جست‌وجوی الگوریتم‌اند. از نبود آن‌ها نقص ساختاری برداشت نشود. SectionID باید در دامنه موتور تولید شود، نه با تغییر Excel. پیش‌نیاز، هم‌نیاز، هزینه اتاق و دسترس‌پذیری اتاق به‌صورت مستقل در این داده وجود ندارد.

## قرارداد خواندن

شناسه‌ها متن؛ اعداد طبق نوع؛ null متمایز از ۰؛ Boolean واقعی به bool تبدیل شود (مقایسه رشته‌ای با "1" خطاست). ردیف عنوان حذف و نام ستون‌ها دقیق حفظ شود. شمارش رکوردهای واقعی جایگزین استفاده کورکورانه از UsedRange شود. مقدارهای ناشناخته/کلید مفقود با شیت و سطر گزارش شوند. خروجی برنامه بر پایه داده مبنا باشد و اصل فایل تغییر نکند.

ممیزی این بسته ساختار و روابط را بررسی کرده است؛ خواندن ظاهر Excel، اثبات امکان جواب معتبر یا نهایی‌شدن تفسیر Target از آن نتیجه نمی‌شود.
