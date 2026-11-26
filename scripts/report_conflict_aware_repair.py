"""Create the Phase 8 report from completed measurements, never execute GA."""
import argparse
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]


def main():
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,default=ROOT/'docs/PHASE8_COMPARISON_RESULTS.json')
    parser.add_argument('--diagnostic',type=Path,default=ROOT/'docs/PHASE8_NEIGHBORHOOD_DIAGNOSTIC.json')
    args=parser.parse_args()
    measured=args.input
    diagnostic=args.diagnostic
    data=json.loads(measured.read_text(encoding='utf-8'))
    base=data['variants']['baseline'];new=data['variants']['enhanced']
    fig,axes=plt.subplots(2,2,figsize=(11,7))
    for name,variant,color in [('Baseline',base,'#a26038'),('Conflict-aware',new,'#147d88')]:
        rows=variant['evolution']['generations'];x=[r['generation_index'] for r in rows]
        for ax,key,label in [(axes[0,0],'best_hard_penalty','Best hard penalty'),(axes[0,1],'mean_hard_penalty','Mean hard penalty'),
                             (axes[1,0],'cumulative_fitness_evaluation_count','Cumulative fitness calls'),(axes[1,1],'generation_elapsed_seconds','Generation seconds')]:
            ax.plot(x,[r[key] for r in rows],label=name,color=color);ax.set_xlabel('Completed generation');ax.set_ylabel(label);ax.grid(alpha=.25)
    axes[0,0].legend();fig.suptitle(f"Phase 8 — Dataset B — seed 11 — population {data['arguments']['population']} — cap {data['arguments']['fitness_budget']} calls");fig.tight_layout()
    fig.savefig(ROOT/'docs/PHASE8_COMPARISON.png',dpi=160)
    if measured.resolve()!=(ROOT/'docs/PHASE8_COMPARISON_RESULTS.json').resolve():shutil.copyfile(measured,ROOT/'docs/PHASE8_COMPARISON_RESULTS.json')
    if diagnostic.resolve()!=(ROOT/'docs/PHASE8_NEIGHBORHOOD_DIAGNOSTIC.json').resolve():shutil.copyfile(diagnostic,ROOT/'docs/PHASE8_NEIGHBORHOOD_DIAGNOSTIC.json')
    focused=['| سناریو / روش | Hard قبل → بعد | کاندیداهای Fitness در هر فراخوانی | تلاش خام | زمان میانگین Repair (ms) | تعارض اتاق حذف‌شده |',
             '|---|---:|---:|---:|---:|---:|']
    for case in ('actual_hard4','hand_room_overlap'):
        for label,variant in [('مبنا',base),('جدید',new)]:
            r=variant['focused'][case];p=r['report']
            before=sum(r['before_counts'].values());after=sum(r['after_counts'].values())
            resolved=r['before_counts']['H04_ROOM_OVERLAP']-r['after_counts']['H04_ROOM_OVERLAP']
            focused.append(f"| {case} / {label} | {before} → {after} | {p['evaluations']['candidates']} | {p['attempts']} | {r['mean_repair_seconds']*1000:.4f} | {resolved} |")
    table=['| شاخص | مبنا | Conflict-aware |','|---|---:|---:|']
    for label,get,fmt in [
        ('نسل کامل',lambda v:v['evolution']['completed_generations'],'.0f'),
        ('Hard اولیه',lambda v:v['evolution']['generations'][0]['best_hard_penalty'],'.0f'),
        ('بهترین Hard مشاهده‌شده',lambda v:v['evolution']['best_overall']['hard_penalty'],'.0f'),
        ('کل Fitness calls',lambda v:v['evolution']['evaluation_usage']['total'],'.0f'),
        ('Fitness کاندیداهای Repair',lambda v:v['repair_totals']['candidate_evaluations'],'.0f'),
        ('تعداد فراخوانی Repair',lambda v:v['repair_totals']['repair_calls'],'.0f'),
        ('میانگین زمان Repair (ms)',lambda v:v['mean_repair_seconds']*1000,'.4f'),
        ('میانگین زمان نسل کامل (s)',lambda v:v['mean_complete_generation_seconds'],'.4f'),
        ('زمان کل Evolution (s)',lambda v:v['evolution']['timings']['evolution_seconds'],'.4f'),
        ('رکوردهای تعارض اتاق حذف‌شده در مجموع Repairها',lambda v:v['repair_totals']['room_conflicts_resolved'],'.0f'),
        ('مجموع کاهش Hard درون Repairها',lambda v:v['repair_totals']['net_hard_improvement_inside_repair'],'.0f')]:
        table.append(f'| {label} | {get(base):{fmt}} | {get(new):{fmt}} |')
    decrease=100*(1-new['mean_complete_generation_seconds']/base['mean_complete_generation_seconds'])
    remaining=new['focused']['actual_hard4']['remaining_violations']
    violations='\n'.join(f"- `{v['constraint_type']}`، گروه‌های `{', '.join(v['section_ids'])}`، روز `{v['day']}`، بازه `{v['slot_keys'][0][1]}`"+
                          (f"، دانشجو `{v['student_id']}`" if v['student_id'] else '') for v in remaining)
    text=f'''# Phase 8 — Conflict-Aware Repair: گزارش اجرا

شاخه: `codex/conflict-aware-repair`. مبنا: `{data['baseline_commit']}`، شامل UI و Seed خودکار؛ PRهای ۶ و ۷ هنگام بررسی باز بودند و main آن کدها را نداشت. PR جدید یا Merge ساخته نشده است.

## تغییر و ضعف مبنا

Repair قدیمی پیشنهادهای عمومی MoveMeeting/ChangeProfessor/ChangeRoom/MoveStudent را با ترتیب shuffle و فیلتر درشت section/course تولید می‌کرد. حتی پیشنهاد نامرتبط تلاش خام مصرف می‌کرد؛ مقصد از نظر اشغال/Availability پیش‌فیلتر نمی‌شد و جلسهٔ واقعاً درگیر مستقیماً هدف قرار نمی‌گرفت. ثبت baseline violations نیز می‌توانست زوجی را دوباره بررسی کند که تغییر قبلی حل کرده بود.

مسیر جدید فقط برای H04_ROOM_OVERLAP فعال است: هر دو endpoint و meeting_index دقیق را می‌گیرد، پیشنهادها را بین دو سمت نوبتی توزیع و پیش از Fitness، بازه‌های واقعی اشغال اتاق/استاد/دانشجو و Unavailableهای Context را بررسی می‌کند. تابع overlap همان helper ارزیاب مرجع است. نوع/ظرفیت اتاق از نگاشت آمادهٔ Context می‌آید. پذیرش همچنان فقط با canonical evaluate_fitness و comparison_key=(HardPenalty,-SoftScore) است. هیچ فرمول یا قید تغییر نکرد.

جزئیات API، ترتیب، بودجه‌ها، cache و قرارداد اتاق در [CONFLICT_AWARE_REPAIR.md](CONFLICT_AWARE_REPAIR.md) آمده است. تمام ردها از والد جدا هستند؛ جایگاه گروه، استاد، Enrollmentها و سایر جلسات زمانی عوض نمی‌شوند. اگر اتاق جایگزین شود، چون RoomID یک ژن سطح گروه است، تمام جلسات همان گروه از نظر اتاق جدید کنترل می‌شوند؛ اتاق مستقل برای هر جلسه ساخته نشده است.

## regression واقعی Hard=4

فایل واقعی `outputs/web_ui/runs/69c4cb75262144f2a341d750848544b3/best_overall.json` موجود بود و بدون بازسازی به `tests/fixtures/phase8_dataset_b_hard4.json` کپی شد. نسل کامل آن اجرا ۵۰۰، Seed=11 و fingerprint آن:

`83711cb7b56c735009c8278c8a6f57c77661242bc66c7ae5ce27a3878d8520a4`

SHA-256 فایل fixture: `2b824515e073d78967605f4db7a2cddead50db3263fb0b4aeac87acae380b893`.

Dataset B SHA-256: `{data['dataset_b_sha256']}`. restore_solution ساختار، Context و fingerprint را کنترل کرد؛ canonical Fitness اولیه دقیقاً ۴ بود: دو room overlap و دو student-unavailable مطابق درخواست. این snapshot فقط در تست Repair استفاده شد؛ به Initializer یا آزمایش Evolution داده نشد.

{chr(10).join(focused)}

هر میانگین از {data["arguments"]["repetitions"]} تکرار مستقل همان ورودی و Random(11) گرفته شده است؛ baseline snapshot مشترک بود و هزینهٔ یک Fitness خارجی آماده‌سازی در زمان Repair منظور نشده است. مبنا کد واقعی Repair از کامیت مرجع است، نه پیاده‌سازی بازنویسی‌شده. حالت غیرفعال ویژگی نیز از نظر خروجی/هزینه با همان کد مرجع تطبیق داده شد.

**نتیجهٔ regression: هر دو room overlap باقی ماندند و Hard=4 حفظ شد.** هیچ تغییر بدتری برای ساختن نتیجهٔ ظاهری پذیرفته نشد. سناریوی دستیِ دارای مقصد معتبر به Hard=0 رسید؛ اما دادهٔ واقعی حاضر چنین مقصدی در همسایگی بررسی‌شده ندارد.

نقض‌های واقعی باقی‌مانده:

{violations}

در تشخیص جداگانهٔ گزارش، حداکثر ۴۰۰ ترکیب مشخص تک‌جلسه/اتاق (۲۰۰ برای هر زوج، شامل دو endpoint) فقط با پیش‌فیلتر بررسی شد و هیچ‌کدام admissible نبود. این بررسی خارج از Repair و خارج از بودجهٔ آزمایش GA است؛ Production همچنان حداکثر ۱۰۰ تلاش دارد. دو ارزیابی canonical تشخیصی جدا نشان دادند تعویض کور اتاق به B-R04، Hard=4 را حفظ و تعارض را به B-CE-05-01 یا B-CIV-06-06 منتقل می‌کند. [JSON تشخیص](PHASE8_NEIGHBORHOOD_DIAGNOSTIC.json) عدد و مثال‌ها را ثبت کرده است. این نتیجه اثبات حل‌ناپذیری Dataset نیست؛ صرفاً نبود حرکت امن در این همسایگی ثابت‌استاد/ثابت‌Enrollment است. جابه‌جایی هماهنگ چند گروه/جلسه یا خروج موقت از plateau خارج از محدودهٔ این فاز است.

## مقایسهٔ تکامل محدود

Dataset B، Seed=11، Population={data["arguments"]["population"]}، Tournament=3، Elite=2، MaxGenerations={data["arguments"]["generations"]}، Mutation=.30، Crossover=.70، Repair روشن؛ **سقف Fitness هر دو {data["arguments"]["fitness_budget"]}**، Stagnation=None و StopOnFirstFeasible=false. حدود Repair هر دو ۵ ارزیابی محلی، ۵۰ کاندیدا و ۱۰۰ تلاش خام است. تعداد واقعی calls یکسان نیست، چون فیلتر جدید کارهای نامناسب را پیش از Fitness حذف می‌کند. هیچ اجرا ۵۰۰ نسل نداشت؛ تعداد نسل‌های واقعاً کامل‌شده در جدول ثبت شده است.

{chr(10).join(table)}

میانگین زمان نسل در این اجرای اندازه‌گیری‌شده **{decrease:.2f}% کاهش** داشت، نه افزایش. آماده‌سازی indexهای جدید مجموعاً {new['repair_totals']['relocation_index_seconds']:.6f} ثانیه با {new['repair_totals']['relocation_index_builds']} ساخت index و زمان فیلتر {new['repair_totals']['relocation_filter_seconds']:.6f} ثانیه بود. index فقط وقتی fingerprint پذیرفته‌شده تغییر کند دوباره ساخته می‌شود. مرتب‌سازی پیشنهادها و سایر سربارها در زمان کل Repair لحاظ شده‌اند، نه لزوماً در دو تایمر جزئی.

«رکوردهای حذف‌شده» مجموع رکوردهای واقعی ورودی هر Repair است که در خروجی آن نمانده‌اند؛ شمار زوج‌های یکتای کل آزمایش نیست و تکرار یک تعارض در افراد مختلف می‌تواند چند بار شمرده شود. کاهش Hard داخل Repairها نیز جمع بهبودهای محلی است، نه کاهش Hard بهترین جمعیت.

نسخهٔ اولیهٔ همین enhancement پیش از cache index جدا اندازه‌گیری شد. افزودن cache باعث تغییر fingerprint بهترین نسل‌ها، آمار عملگر یا هزینهٔ Fitness نشد؛ فقط زمان preprocessing کاهش یافت. JSON نهایی مربوط به نسخهٔ دارای cache است. مقایسه فقط یک Seed، یک اندازهٔ جمعیت و یک اجرای کوتاه است؛ تفاوت مصرف RNG در مسیر Repair باعث تفاوت مسیر فرزندان در دو نسخه می‌شود. از این اعداد ادعای برتری عمومی یا تضمین رفع plateau نمی‌کنیم.

![مقایسهٔ واقعی](PHASE8_COMPARISON.png)

## تست‌ها و سلامت ورودی

۱۸ تست کوچک هدفمند و یک regression واقعی اضافه شد. تست‌های قبلی Mutation/Repair/Evolution همگی موفق بودند؛ **کل ۳۰۶ تست، بدون skip یا failure موفق شد**. این مجموعه سازگاری Web UI، Seed خودکار/دستی و چرخهٔ تکامل را نیز شامل می‌شود. آزمون‌های جدید جابه‌جایی یک جلسه، دو endpoint، اتاق جایگزین، استاد/دانشجوی unavailable، overlap جدید، مدت متفاوت، duplicate/forbidden slot، ظرفیت، رد canonical برابر/بدتر، جدایی والد/cache، determinism، بودجه و Hard=4 واقعی را بررسی می‌کنند.

برای حفظ تغییر محلی قدیمی Initialization و قفل فایل Excel، suite کامل در کپی تمیز source با Initialization نسخهٔ committed و فایل‌های دادهٔ Git اجرا شد؛ B با همان SHA مرجع کنترل شد. آن تغییر محلی و `.run`ها دست‌نخورده‌اند. هیچ Dataset، Fitness، Constraint، Selection، Crossover، Mutation، Initialization، Chromosome schema یا Seed behavior توسط این فاز تغییر نکرده است.

## فایل‌ها و بازتولید

- `.gitattributes` برای حفظ byte identity فایل fixture واقعی.
- تغییر `src/scheduler/repair.py`؛ helper جدید `src/scheduler/repair_relocation.py`.
- `tests/test_conflict_aware_repair.py` و fixture واقعی `tests/fixtures/phase8_dataset_b_hard4.json`.
- `scripts/benchmark_conflict_aware_repair.py` و `scripts/report_conflict_aware_repair.py`.
- اسناد API/این گزارش/وضعیت پیاده‌سازی، JSON اندازه‌گیری و تشخیص، و نمودار.

```powershell
$env:PYTHONPATH='src'
.venv-ui\\Scripts\\python.exe -m unittest discover -s tests -v
.venv-ui\\Scripts\\python.exe scripts/benchmark_conflict_aware_repair.py --output outputs/PHASE8_NEW_MEASUREMENT.json
```

اسکریپت از بازنویسی نتایج قبلی جلوگیری می‌کند و source مرجع را با Git pin می‌کند. برای ساخت نمودار matplotlib لازم است؛ اجرای موتور به آن وابسته نیست. دادهٔ تمام نسل‌های آزمایش و hash موتور در [PHASE8_COMPARISON_RESULTS.json](PHASE8_COMPARISON_RESULTS.json) ثبت شده است. اجراهای وب قدیمی که قبلاً شروع شده‌اند خودبه‌خود کد Repair جدید را دریافت نمی‌کنند؛ برای استفادهٔ بعدی از نسخهٔ جدید باید پس از پایان اجرای جاری سرور را مجدداً راه انداخت.
'''
    (ROOT/'docs/PHASE8_REPORT.md').write_text(text,encoding='utf-8')


if __name__=='__main__':main()
