# Phase 6 — چرخه تکامل، Elitism و گزارش آزمایش

## API و محدوده

```python
from scheduler import load_dataset
from scheduler.evaluation_context import EvaluationContext
from scheduler.evolution import EvolutionSettings, run_evolution

dataset = load_dataset('data/UniversityDataset_Corrected.xlsx')
context = EvaluationContext.from_dataset(dataset)  # یک بار برای کل اجرا
result = run_evolution(context, EvolutionSettings(
    population_size=30, max_generations=20, random_seed=11,
    max_fitness_evaluations=4000,
))
```

Runner در `src/scheduler/evolution.py` مستقل از Excel و CLI است. CLI بارگذاری و اعتبارسنجی Excel و زمان ساخت Context را انجام می‌دهد و یک Context ثابت به Runner می‌دهد. در ماتریس، همان Context بین اجراهای متوالی بازاستفاده می‌شود؛ Dataset و Policy در اجرا قابل تعویض نیستند. هیچ ماژول تأییدشدهٔ فازهای قبل بازنویسی نشده است. Selection، Crossover، Mutation، Repair، ارزیابی و Initialization از APIهای موجود فراخوانی می‌شوند. `InitializedIndividual` موجود، رکورد عمومی فرد ارزیابی‌شده باقی مانده است.

پیش‌فرض `EvolutionSettings(version='evolution-phase6-v1')`: جمعیت ۱۰۰، Tournament=3، Elite=2، حداکثر ۵۰۰ نسل، Mutation=0.30، Crossover=0.70 و Repair روشن. Seed قابل تنظیم، توقف روی اولین جواب معتبر پیش‌فرض خاموش، رکود و سقف Fitness پیش‌فرض None هستند. initialization_settings، mutation_settings و repair_settings تغییرناپذیر و در گزارش کامل ثبت می‌شوند. mutation_probability سطح Runner، دروازه معتبر و واحد است و مقدار nested MutationSettings را نیز همسان می‌کند.

این Baseline با طرح تاریخی 70/15/15 متفاوت است: احتمال ۰٫۷۰ فقط Course-Based Crossover است؛ ۰٫۳۰ باقی‌مانده Clone مستقل یکی از دو والد است. Student-Based Crossover وجود ندارد. Adaptive Mutation، Incremental Fitness، پردازش موازی، UI و توسعه چنددانشکده‌ای ساخته نشده‌اند.

## Lifecycle و Replacement

نسل صفر جمعیت کامل Initializer همراه Fitnessهای Hard/Soft آن است. برای هر نسل کامل:

1. جمعیت با comparison_key=(HardPenalty,-SoftScore) و ترتیب پایدار index در تساوی رتبه‌بندی می‌شود.
2. Eliteها مستقل Clone می‌شوند. Fitness تغییرناپذیر موجودشان همراهشان منتقل می‌شود؛ نه ارزیابی مجدد، نه Mutation و نه Repair روی آنها اجرا می‌شود. Cache عددی خود Clone همچنان None است؛ Fitness معتبر در رکورد فرد نگهداری می‌شود.
3. هر فرزند دو انتخاب مستقل Tournament دارد؛ انتخاب یک فرد برای هر دو والد مجاز و نرخ آن ثبت می‌شود.
4. Crossover یا Clone اجرا و فرزند کامل ارزیابی می‌شود. Snapshot شامل fingerprint و Context همان ژنوتیپ است.
5. maybe_mutate دروازه مستقل احتمال را اعمال می‌کند. اگر applied باشد، ژنوتیپ جدید کامل ارزیابی و Snapshot تازه ساخته می‌شود؛ در skipped/failed/not_applicable، Snapshot فقط پس از کنترل fingerprint بازاستفاده می‌شود.
6. Repair روشن، با Snapshot فعلی و بودجه محدود فراخوانی می‌شود؛ Snapshot خروجی نیز پیش از پذیرش از نظر Context، fingerprint و ساختار بررسی می‌شود. خروجی Repair نباید از ورودی همان Repair بدتر باشد.
7. فرزند مستقل از مقایسه با والدها وارد نسل بعد می‌شود. جمعیت تنها پس از تکمیل دقیق PopulationSize جایگزین می‌شود.

**Greedy Replacement وجود ندارد.** فرزند بدتر از هر دو والد مجاز است؛ شکست Repair نیز همان فرزند را حفظ می‌کند و والد را جانشین نمی‌کند. تضمین بهترین عضو هر نسل از Elitism می‌آید. برای این تضمین، نسخه اول `1 <= EliteCount < PopulationSize` و PopulationSize>=2 را الزام می‌کند؛ همه ظرفیت به Elite اختصاص نمی‌یابد. TournamentSize نیز باید در اندازه جمعیت بگنجد. EliteCount=0 در این نسخه پشتیبانی نمی‌شود، زیرا تضمین درخواست‌شدهٔ عدم افت بهترین عضو را نقض می‌کند.

تساوی کامل Eliteها با ترتیب موجود جمعیت حل می‌شود؛ هیچ RNG اضافی برای آن مصرف نمی‌شود. Initialization و Evolution دو stream محلی Random با همان Seed ثبت‌شده دارند؛ stream تکامل بین نسل‌ها ادامه می‌یابد. RNG سراسری دست‌نخورده است. آزمون Seed و PYTHONHASHSEED برای بازتولید ژنوتیپ/آمار غیرزمانی وجود دارد؛ زمان اجرا قرار نیست یکسان باشد.

## توقف و بودجه

MaxGenerations تعداد نسل‌های جدید پس از Initialization است؛ صفر یعنی فقط نسل صفر. در مرز نسل کامل، علت‌های فعال با ترتیب first_feasible، stagnation، max_generations و fitness_budget ثبت می‌شوند؛ ممکن است چند علت هم‌زمان باشند. StopOnFirstFeasible فقط با فعال‌بودن، پس از تکمیل جمعیت اولیه/نسل باعث توقف می‌شود. در حالت پیش‌فرض تکامل Soft پس از Hard=0 هم ادامه دارد.

رکود، تعداد نسل‌های پیاپی بدون بهبود strict در بهترین comparison_key جمعیت است. بهبود Soft در تساوی Hard شمارنده رکود را صفر می‌کند. stagnation_generations یا None است یا عدد مثبت. رسیدن به سقف نسل/بودجه/رکود به معنی معتبر بودن جواب نیست.

max_fitness_evaluations سقف کل فراخوانی‌های واقعی Fitness، شامل Initialization است و باید دست‌کم PopulationSize باشد. بدون بازنویسی Initializer، جمعیت اولیه کامل ساخته می‌شود؛ آن API دقیقاً یک Fitness برای هر عضو پذیرفته‌شده دارد و تکرار تلاش تنوع را ارزیابی نمی‌کند. پیش از تولید هر فرزند، یک فراخوانی برای baseline و در صورت MutationProbability>0، یک فراخوانی برای امکان Mutation رزرو می‌شود. به همین دلیل ممکن است یک واحد بودجه استفاده‌نشده باقی بماند. Repair فقط بودجه واقعیِ باقی‌مانده را می‌گیرد و سقف کاندیدای آن با min(سقف خودش، باقی‌مانده کل) محدود می‌شود؛ ارزیابی مبنای اضافی ندارد.

اگر بودجه وسط نسل تمام شود، آن نسل به جمعیت نهایی تبدیل نمی‌شود. `partial_generation` هزینه، تعداد فرزندان تکمیل‌شده، آمار عملگرها و علت توقف آن تلاش را جدا نگه می‌دارد. `population` و FinalPopulationBest همواره آخرین نسل کامل با اندازه دقیق تنظیم‌شده‌اند؛ هیچ پرکردن پنهانی با والد یا کروموزوم ارزیابی‌نشده رخ نمی‌دهد. جواب‌های واقعاً مشاهده‌شده در تلاش نیمه‌تمام همچنان در BestOverall/BestFeasible قابل ثبت‌اند. هزینه مصرف‌شده دور ریخته یا از گزارش حذف نمی‌شود.

## بهترین جواب‌ها

- BestOverall: بهترین جواب واقعاً ارزیابی‌شده، شامل فرزند پیش از Mutation، پس از Mutation و خروجی Repair؛ کپی مستقل نگهداری می‌شود.
- BestFeasible: بهترین Hard=0 دیده‌شده، در غیر این صورت None.
- FinalPopulationBest: بهترین فرد آخرین جمعیت کامل.

ممکن است BestOverall با FinalPopulationBest متفاوت باشد: مثلاً یک فرزند خوب قبل از Mutation دیده شود و جهش آن را بدتر کند، یا بودجه یک نسل ناقص تمام شود. ثبت جواب خوب به معنی تزریق پنهانی آن به جمعیت نیست. Eliteها فقط از جمعیت کامل قبل انتخاب می‌شوند. در هر سه رکورد، Hard مثبت صریحاً نامعتبر است؛ نبود جواب معتبر، اثبات ناممکن‌بودن داده نیست.

## شمارش و سنجش عملکرد

EvaluationUsage هزینه را به initialization، child_baseline، post_mutation، mutation_baseline، repair_baseline و repair_candidates می‌شکند. دو baseline اخیر در اجرای سالم فعلی صفرند، چون Snapshot معتبر داده می‌شود. هر Fitness سالم دقیقاً یک Hard و یک Soft دارد. جمع هزینه نسل‌های کامل، به‌علاوه partial_generation، باید با هزینه کل برابر باشد. Elite، Selection، Crossover، Mutation با Snapshot، آمارگیری و ذخیره JSON هیچ Fitness تازه‌ای اجرا نمی‌کنند.

GenerationStatistics برای نسل صفر و هر نسل کامل شامل بهترین/میانگین Hard، Soft بهترین فرد سخت‌اول/میانگین Soft، تعداد feasible، تعداد fingerprint یکتا، پوشش میانگین، میانگین گروه فعال و درخواست بی‌پاسخ، هزینه نسل/تجمعی، هزینه کاندیداهای Repair، زمان نسل/تجمعی و آمار عملگرهاست. BestSoftScore، امتیاز Soft فرد سخت‌اولِ برتر است، نه بیشینه مستقل Soft. active_sections و unassigned_requests میانگین به‌ازای هر فردند.

فرزندان نهایی پس از Mutation/Repair با هر دو والد مقایسه می‌شوند: better_than_both و worse_than_both strict هستند؛ between_parents شامل تساوی‌هاست و این سه دسته جمعاً تعداد فرزندان را می‌دهند. identical_to_a_parent آمار ژنوتیپ مستقل و هم‌پوشان است. self_mating_rate = self_matings/children؛ در نسل صفر صفر است. هزینهٔ Context، Initialization و Evolution جدا ثبت می‌شود؛ زمان بارگذاری XLSX در خلاصه ماتریس نیز موجود است. این‌ها اندازه‌گیری محلی‌اند و تضمین مقیاس دانشگاه نیستند.

خطای غیرمنتظره پنهان نمی‌شود: EvolutionFailure نام مرحله، نسل و index فرزند را همراه علت اصلی نگه می‌دارد؛ در خطای بعد از Initialization، آخرین نتیجه کامل سالم و partial_generation هم در exception.result قابل دسترسی‌اند. CLI گزارش failure می‌نویسد و exit code=1 می‌دهد. اگر زیرماژول وسط ارزیابی exception بدهد، هزینه داخلی ناتمام لزوماً قابل بازیابی نیست؛ گزارش failure دارای evaluation_accounting_complete=false و total/Hard/Soft نامعلوم است، با known_calls شناخته‌شده. چنین گزارشی benchmark موفق تلقی نمی‌شود. خطای ساختاری به جمعیت راه نمی‌یابد و برای رفع آن والد به‌طور پنهانی جایگزین نمی‌شود.

## CLI و خروجی قابل بازیابی

از ریشه پروژه در PowerShell:

```powershell
$env:PYTHONPATH = 'src'
python -m unittest discover -s tests -v
python scripts/run_evolution_experiment.py --population-size 30 --max-generations 20 --random-seed 11 --max-fitness-evaluations 4000 --output outputs/PHASE6_FIRST.json
python scripts/run_evolution_experiment.py --matrix --population-size 30 --max-generations 20 --seeds 11 22 33 --max-fitness-evaluations 4000 --output outputs/PHASE6_MATRIX.json
python scripts/run_evolution_experiment.py --population-size 100 --max-generations 30 --random-seed 11 --max-fitness-evaluations 4000 --output outputs/PHASE6_LARGE.json
```

پارامترهای مستقل: --workbook، --tournament-size، --elite-count، --mutation-probability، --crossover-probability، --[no-]repair-enabled، --repair-evaluation-budget، --repair-local-budget، --repair-attempt-budget، --[no-]stop-on-first-feasible و --stagnation-generations. از پیش‌فرض ۵۰۰ نسل برای اجرای سریع آزمایشی استفاده نکنید؛ بودجه را صریح تنظیم کنید. --matrix سه حالت A بدون Mutation/Repair، B با Mutation و بدون Repair، C با Mutation/Repair را برای هر Seed می‌سازد؛ تنظیمات واقعی هر اجرا در JSON همان اجرا ثبت می‌شوند.

JSON اصلی شامل Settings، هش XLSX، ContextID متکی به محتوای Dataset/Policy، نسخه Policy، تمام آمار نسل‌ها، هزینه‌ها، زمان‌ها، توقف و خلاصه بهترین جواب‌ها همراه Violations است. فایل جداگانه `<report>_solutions.json` جلسات و تخصیص همه درخواست‌ها از جمله None را همراه ارزیابی مستقل هر یک از سه بهترین جواب نگه می‌دارد. این خروجی، checkpoint ادامه RNG یا Resume تکامل نیست.

برای رسم نمودار از داده‌های واقعی ماتریس، در محیط دارای Matplotlib (وابستگی اختیاریِ رسم، نه موتور): `python scripts/plot_evolution_experiments.py outputs/PHASE6_MATRIX.json --output outputs/PHASE6_PROGRESS.png`. سه محور، نسل کامل، شمارش Fitness و زمان Evolution را جدا نشان می‌دهند؛ اسکریپت هیچ Fitness محاسبه نمی‌کند. نسخه بررسی‌شدهٔ تصویر و اعداد ماتریس در docs نگهداری شده‌اند.

```python
import json
from scheduler.evolution_reporting import restore_solution
from scheduler.fitness import evaluate_fitness
saved = json.load(open('outputs/PHASE6_FIRST_solutions.json', encoding='utf-8'))
chromosome = restore_solution(saved['best_overall'], context)
evaluation = evaluate_fitness(chromosome, context)  # هزینه مستقلِ بازیابی، بیرون از اجرای GA
```

بازیابی Context، کلیدهای تکراری، ساختار و fingerprint را کنترل می‌کند؛ نمرات ذخیره‌شده بدون ارزیابی مجدد وارد جمعیت نمی‌شوند. فایل داده اصلی هرگز برای نوشتن باز نمی‌شود؛ CLI از یکی‌شدن مسیر Workbook و خروجی جلوگیری و هش پایان را کنترل می‌کند. فایل‌های آزمایش محلی در outputs و خارج از Git هستند. خلاصه نتایج واقعی و محدودیت‌های مقایسه در PHASE6_REPORT ثبت می‌شوند.
