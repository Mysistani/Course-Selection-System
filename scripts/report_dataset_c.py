"""Render Dataset C's measured profile and smoke report; never execute GA."""
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def main():
    p=json.loads((ROOT/'data/Dataset_C_University_seed20261104_quality.json').read_text(encoding='utf-8'))
    smoke=json.loads((ROOT/'docs/DATASET_C_SMOKE.json').read_text(encoding='utf-8'))
    verification=json.loads((ROOT/'docs/DATASET_C_VERIFICATION.json').read_text(encoding='utf-8'))
    run=smoke['evolution']
    def histogram(values):
        return '\n'.join(f'| {k} | {v} |' for k,v in sorted(values.items(),key=lambda item:int(item[0])))
    faculty_names={'C-ENG':'مهندسی','C-SCI':'علوم پایه','C-MGT':'مدیریت و اقتصاد','C-HUM':'علوم انسانی'}
    faculty_rows='\n'.join(f'| {name} | {p["students_by_faculty"][key]} | {p["professors_by_faculty"][key]} |' for key,name in faculty_names.items())
    generations='\n'.join(f'| {g["generation_index"]} | {g["best_hard_penalty"]} | {g["mean_hard_penalty"]:.3f} | {g["best_soft_score"]:.6f} | {g["feasible_count"]} | {g["unique_chromosomes"]} | {g["cumulative_fitness_evaluation_count"]} | {g["generation_elapsed_seconds"]:.6f} |' for g in run['generations'])
    penalties='\n'.join(f'| {k} | {v} |' for k,v in run['best_overall']['hard_counts_by_type'].items())
    report=f'''# Dataset C — گزارش تولید، اعتبارسنجی و smoke

Dataset C تحت قیود فعلی **حل‌پذیر است**. گواهی مستقل قبل از خروجی و پس از بارگذاری Excel، HardPenalty=0، صفر نقض و همهٔ شمارنده‌های سخت صفر دارد. الگوریتم در smoke جواب معتبر پیدا نکرد؛ این دو نتیجه متفاوت‌اند.

## اندازه و فایل‌ها

| مشخصه | مقدار |
|---|---:|
| دانشکده | 4 |
| رشته | 12 |
| دانشجو | 1000 |
| استاد | 104 |
| درس یکتا | 160 |
| اتاق نظری / عملی | 60 / 20 |
| درخواست | {p['counts']['StudentRequests']} |
| سابقهٔ گذرانده | {p['counts']['PassedCourses']} |
| گروه فعال مرجع | {p['active_sections']} |
| جایگاه بالقوه | {p['preallocated_section_slots']} |
| مجموع واحد درخواست / تخصیص | {p['total_requested_credits']} / {p['total_assigned_credits']} |
| درخواست بی‌پاسخ مرجع | {p['unassigned_requests']} |
| Seed تولید داده | {p['dataset_seed']} |

همهٔ هدف‌های عددی در محدودهٔ درخواست‌اند. گواهی، پروفایل سابقه و آمار کنار XLSX با پسوندهای `_certificate.json`، `_profiles.json` و `_quality.json` ذخیره‌اند. جزئیات روش و محدودیت‌ها در [DATASET_C_METHODOLOGY.md](DATASET_C_METHODOLOGY.md) است.

SHA256 فایل تحویلی: `{p['dataset_sha256']}`.

SHA256 محتوای canonical: `{p['canonical_content_sha256']}`.

## توزیع جمعیت و تقاضا

| دانشکده | دانشجو | استاد |
|---|---:|---:|
{faculty_rows}

| ترم | دانشجو |
|---|---:|
{histogram(p['students_by_semester'])}

| تعداد درخواست هر دانشجو | دانشجو |
|---|---:|
{histogram(p['requests_per_student'])}

| واحد درخواستی | دانشجو |
|---|---:|
{histogram(p['requested_credits_distribution'])}

تعداد مردودیِ درحال تکرار: {sum(int(k)*v for k,v in p['failed_retake_counts_distribution'].items())}؛ تأخیر ثبت‌شدهٔ بدون درخواست: {sum(int(k)*v for k,v in p['delayed_counts_distribution'].items())}. این‌ها سابقهٔ همین برش کاتالوگ‌اند؛ سابقهٔ کامل دوره یا کارنامهٔ واقعی نیستند. PassedCourses مطابق schema در Excel است؛ مردودی/تأخیر در فایل audit جداست. تقاضای هر درس بین {min(p['course_demand'].values())} و {max(p['course_demand'].values())} است. آمار تک‌درس، تک‌رشته، سابقه و صلاحیت‌ها در quality JSON موجود است.

## منابع و استفادهٔ مرجع

مرجع {sum(v['meetings'] for v in p['room_utilization'].values())} جلسهٔ هفتگی دارد؛ استفادهٔ تجمیعی از اتاق‌ها {sum(v['meetings'] for v in p['room_utilization'].values())/(80*25):.1%} از ۸۰×۲۵ فرصت است. بیشترین رزرو یک اتاق {max(v['meetings'] for v in p['room_utilization'].values())} بازه و اتاق‌های بدون رزرو {p['rooms_with_no_bookings']} موردند. استاد بدون تدریس {p['professors_with_no_bookings']} مورد است. ظرفیت‌ها متنوع، دسترسی اتاق‌ها فراوان و مالکیت دانشکده غیرانحصاری است. این آمار مرجع است، نه خروجی GA.

برای ۱۲ درس یک استاد و برای ۱۴۸ درس سه استاد واجد صلاحیت وجود دارد. معمولاً ۲–۵ درس برای هر استاد و برای ۲۱ نفر استثنائاً ۶ درس تعریف شده است. همهٔ زمان‌های سخت شخصی با مرجع سازگارند؛ برای هر شخص دو بازه با پروفایل زمانی مشترک ممنوع شده است. ممنوعیت اختصاصی اتاق، نمرهٔ مردودی و درس مشترک چندرشته‌ای در مدل فعلی قابل بیان مستقیم نیستند؛ هیچ قید تازه‌ای به موتور اضافه نشد.

## smoke اندازه‌گیری‌شده

موتورِ ثبت‌شدهٔ `{verification['baseline_commit']}`، جمعیت ۶، Tournament=3، Elite=2، Mutation=0.30، Crossover=0.70، Repair روشن، Seed الگوریتم=11، سه نسل، بدون سقف Fitness و بدون توقف رکود/اولین جواب معتبر. تنها ورودی جست‌وجو XLSX بوده است. یک جمعیت مستقل ۶عضوی نیز برای آزمون سازگاری ساخته شد؛ ۶ ارزیابی آن جدا از شمارندهٔ Evolution است.

| نسل | بهترین Hard | میانگین Hard | Soft فرد برتر | معتبر | یکتا | Fitness تجمعی | ثانیهٔ نسل |
|---|---:|---:|---:|---:|---:|---:|---:|
{generations}

سه نسل **کامل** شدند. زمان بارگذاری {smoke['load_seconds']:.6f}، ساخت Context {smoke['context_seconds']:.6f}، Initialization خود Evolution برابر {run['timings']['initialization_seconds']:.6f} و تکامل {run['timings']['evolution_seconds']:.6f} ثانیه بود. مجموع بارگذاری+Context+جمعیت مستقل+Initialization تکامل+تکامل تقریباً {smoke['load_seconds']+smoke['context_seconds']+smoke['separate_initial_population_seconds']+run['timings']['initialization_seconds']+run['timings']['evolution_seconds']:.6f} ثانیه است؛ زمان سریال‌سازی گزارش در این جمع نیست. میانگین سه نسل پس از Initialization برابر {statistics.mean(g['generation_elapsed_seconds'] for g in run['generations'][1:]):.6f} ثانیه است.

تعداد Fitness در Evolution: **{run['evaluation_usage']['total']}**، با جمعیت مستقل: **{run['evaluation_usage']['total']+smoke['separate_initial_population_evaluations']}**. تمام افراد اولیه و نهایی ساختار سالم دارند. پوشش درخواستِ بهترین فرد {run['best_overall']['request_coverage']:.0%} است، اما Hard={run['best_overall']['hard_penalty']}؛ بنابراین برنامهٔ معتبر محسوب نمی‌شود.

تفکیک جریمهٔ بهترین فردِ smoke:

| قید | شدت |
|---|---:|
{penalties}

در این بودجهٔ کوتاه بهترین Hard بهتر نشد. از این اجرا نمی‌توان دربارهٔ همگرایی، مقیاس جمعیت ۱۰۰، زمان ۵۰۰ نسل یا برتری روش نتیجه گرفت. هیچ اجرای ۵۰۰نسلی و هیچ تزریق مرجعی انجام نشد. اعداد زمان به همین دستگاه و بار هم‌زمان آن وابسته‌اند. [دادهٔ خام smoke](DATASET_C_SMOKE.json) تنظیمات کامل، هش موتور، شمارنده‌ها و روند نسل‌ها را نگه می‌دارد.

## کنترل نهایی

{verification['tests_passed']} تست، شامل {verification['dataset_c_tests']} تست Dataset C و تمام تست‌های قبلی، موفق شدند؛ زمان مجموعه {verification['test_seconds']} ثانیه و skip صفر بود. تست‌های Dataset B همچنان موفق‌اند. آزمون‌ها روی snapshot تمیز موتور اجرا شدند؛ تغییرات محلی قبلی در Initialization و فایل‌های PyCharm حفظ و وارد این مرحله نشدند.

دو تولید واقعی XLSX با Seed یکسان، رکوردهای Loader و محتوای canonical یکسان داشتند؛ بایت فایل به‌علت metadata ZIP متفاوت بود. بازتولید با PYTHONHASHSEED=1 و 999 نیز آزموده شد. علاوه بر تست‌های چند Seed، بررسی دستی Seedهای ۰ تا ۴۹ همگی گواهی Hard=0 داد. این بررسی تضمین موفقیت همهٔ اعداد Seed نیست؛ شکست سازنده هرگز با برچسب feasible ذخیره نمی‌شود.

هش Dataset A و B با نسخهٔ قبلی برابر است. هیچ فایل موتور، schema، تنظیم Evolution یا دادهٔ A/B در این مرحله تغییر نکرد. [جزئیات تأیید](DATASET_C_VERIFICATION.json) و [نمایهٔ آماری](DATASET_C_PROFILE.png) در مخزن قرار دارند.
'''
    (ROOT/'docs/DATASET_C_REPORT.md').write_text(report,encoding='utf-8')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes=plt.subplots(2,2,figsize=(12,8),layout='constrained')
    values=p['students_by_program']
    axes[0,0].bar([x.removeprefix('C-') for x in values],values.values(),color='#276680')
    axes[0,0].tick_params(axis='x',rotation=45)
    axes[0,0].set(title='Students by program',ylabel='Students')
    credits=sorted((int(k),v) for k,v in p['requested_credits_distribution'].items())
    axes[0,1].bar([k for k,v in credits],[v for k,v in credits],color='#397a54')
    axes[0,1].set(title='Requested credit loads',xlabel='Credits per student',ylabel='Students')
    axes[1,0].hist(list(p['course_demand'].values()),bins=12,color='#80734f',edgecolor='white')
    axes[1,0].set(title='Demand across 160 courses',xlabel='Requests per course',ylabel='Courses')
    axes[1,1].hist([v['meetings'] for v in p['room_utilization'].values()],bins=range(0,26,2),color='#6a5884',edgecolor='white')
    axes[1,1].set(title='Witness room use (not GA output)',xlabel='Booked periods out of 25',ylabel='Rooms')
    for ax in axes.flat:
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle('Dataset C — synthetic university benchmark, dataset seed 20261104')
    fig.savefig(ROOT/'docs/DATASET_C_PROFILE.png',dpi=150)
    plt.close(fig)


if __name__=='__main__': main()
