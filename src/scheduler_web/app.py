"""Local-only FastAPI administrator application. Run with one Uvicorn worker."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .projection import export_excel, project
from .services import MAX_UPLOAD, ROOT, RunManager

HERE = Path(__file__).resolve().parent


def create_app(storage=None):
    manager = RunManager(storage or os.environ.get('SCHEDULER_UI_STORAGE',ROOT/'outputs/web_ui'))
    @asynccontextmanager
    async def lifespan(app):
        yield
        await run_in_threadpool(manager.shutdown)
    app = FastAPI(title='سامانه انتخاب واحد تکاملی',lifespan=lifespan)
    app.state.manager = manager; app.state.token = secrets.token_urlsafe(32)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver'])
    app.mount('/static',StaticFiles(directory=HERE/'static'),name='static')
    templates = Jinja2Templates(directory=HERE/'templates')

    @app.middleware('http')
    async def local_request(request,call_next):
        if request.method in ('POST','PUT','DELETE','PATCH'):
            if not secrets.compare_digest(request.headers.get('x-local-token',''),app.state.token):
                return JSONResponse({'detail':'درخواست محلی نامعتبر است؛ صفحه را بازخوانی کنید.'},status_code=403)
            try:
                length = int(request.headers.get('content-length','0'))
            except ValueError:
                return JSONResponse({'detail':'Invalid Content-Length'},status_code=400)
            if length > MAX_UPLOAD+1024*1024:
                return JSONResponse({'detail':'حجم درخواست بیش از حد مجاز است.'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(KeyError)
    @app.exception_handler(FileNotFoundError)
    async def not_found(request,exc):
        return JSONResponse({'detail':'رکورد یا snapshot در دسترس نیست.'},status_code=404)

    @app.exception_handler(ValueError)
    @app.exception_handler(TypeError)
    async def invalid(request,exc):
        return JSONResponse({'detail':str(exc)},status_code=422)

    @app.get('/')
    def index(request: Request):
        return templates.TemplateResponse(request=request,name='index.html',context={'token':app.state.token})

    @app.get('/api/state')
    def state():
        with manager.lock:
            return {'datasets':list(manager.datasets.values()),'runs':[manager.summary(r) for r in sorted(manager.runs.values(),key=lambda r:r['created'],reverse=True)],
                    'active_run_id':manager.active_id,'status':manager.runs[manager.active_id]['status'] if manager.active_id else 'Idle'}

    @app.post('/api/datasets/upload')
    async def upload(file: UploadFile):
        content = bytearray()
        while block := await file.read(1024*1024):
            content.extend(block)
            if len(content)>MAX_UPLOAD:
                raise HTTPException(413,'فایل بیش از ۱۰ مگابایت است.')
        await file.close()
        return await run_in_threadpool(manager.upload,file.filename or '',bytes(content))

    @app.post('/api/datasets/examples/{label}')
    def example(label: str):
        return manager.example(label)

    @app.post('/api/runs')
    def start(body: dict):
        try:
            return manager.start(body['dataset_id'],body.get('settings',{}),seed_mode=body.get('seed_mode'))
        except RuntimeError as exc:
            raise HTTPException(409,str(exc)) from exc

    @app.get('/api/runs/{run_id}/events')
    def events(run_id: str,after: int=-1,limit: int=100):
        if after < -1 or not 1<=limit<=100:
            raise ValueError('invalid polling cursor or limit')
        return manager.events(run_id,after,limit)

    @app.post('/api/runs/{run_id}/stop')
    def stop(run_id: str):
        return manager.stop(run_id)

    @app.get('/api/runs/{run_id}/generations/{generation}')
    def generation(run_id: str,generation: int):
        return manager.population(run_id,generation)

    @app.post('/api/runs/{run_id}/generations/{generation}/persist')
    def persist(run_id: str,generation: int):
        manager.persist_generation(run_id,generation)
        return {'saved':True}

    @app.get('/api/runs/{run_id}/entities')
    def entities(run_id: str):
        context = manager.dataset_context(manager.get_run(run_id)['dataset_id'])
        return {'student':[{'id':sid,'name':sid} for sid in context.students],
                'professor':[{'id':pid,'name':p.professor_name} for pid,p in context.professors.items()],
                'room':[{'id':rid,'name':rid+' · '+r.room_type} for rid,r in context.rooms.items()]}

    @app.get('/api/runs/{run_id}/snapshot')
    def snapshot(run_id: str,generation: int=0,selection: str='best'):
        payload = manager.snapshot(run_id,generation,selection)
        return {'summary':manager.snapshot_summary(payload),'snapshot':payload}

    @app.get('/api/runs/{run_id}/timetable')
    def timetable(run_id: str,role: str,entity_id: str,generation: int=0,selection: str='best'):
        context = manager.dataset_context(manager.get_run(run_id)['dataset_id'])
        return project(manager.snapshot(run_id,generation,selection),context,role,entity_id)

    @app.get('/api/runs/{run_id}/download/{kind}')
    def download(run_id: str,kind: str,generation: int=0,selection: str='best'):
        run = manager.get_run(run_id)
        if kind == 'report':
            path = manager.path('runs',run_id)/'report.json'
            return Response(path.read_bytes(),media_type='application/json',headers={'Content-Disposition':'attachment; filename="report.json"'})
        payload = manager.snapshot(run_id,generation,selection)
        if kind == 'json':
            content = json.dumps(payload,ensure_ascii=False).encode('utf-8'); media = 'application/json'; ext = 'json'
        elif kind == 'xlsx':
            context = manager.dataset_context(run['dataset_id'])
            content = export_excel(payload,context,run_id,payload.get('observed_generation',generation))
            media = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'; ext = 'xlsx'
        else:
            raise KeyError('unknown export type')
        return Response(content,media_type=media,headers={'Content-Disposition':f'attachment; filename="schedule_{payload["fingerprint"][:12]}.{ext}"'})

    return app


app = create_app()
