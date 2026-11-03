"""Cross-platform local launcher; the .bat bootstraps its own virtualenv."""
from pathlib import Path
import argparse
import socket
import sys
from threading import Timer
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))

if __name__ == '__main__':
    if sys.version_info<(3,11):
        raise SystemExit('Python 3.11 or newer is required.')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    if not 1<=args.port<=65535:
        parser.error('port must be in 1..65535')
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',args.port))
    except OSError:
        raise SystemExit(f'Port {args.port} is busy. Try: python scripts/start_ui.py --port 8001')
    import uvicorn
    url = f'http://127.0.0.1:{args.port}'
    print(f'Local dashboard: {url}\nCtrl+C: request safe shutdown (wait for current generation).',flush=True)
    if not args.no_browser:
        Timer(2,lambda:webbrowser.open(url)).start()
    uvicorn.run('scheduler_web.app:app',host='127.0.0.1',port=args.port,workers=1)
