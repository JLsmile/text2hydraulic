"""Asynchronous, bounded native-icon rendering cache. No model-service calls."""
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parent
_LOCK=threading.RLock()
_QUEUE=queue.Queue(maxsize=8)
_JOBS={}
_THREAD=None
_PROCESS=None
_STOP=threading.Event()
_PROBES={}


def read_json(path, fallback=None):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return fallback


def resolve_option(value):
    p=Path(value).expanduser()
    return ROOT/p if not p.is_absolute() else p


def environment(cfg, force=False):
    from setup_renderer import probe, installation_commands
    pythons=[resolve_option(cfg['renderer_python'])] if cfg.get('renderer_python') else []
    pythons += [ROOT/'.venv-renderer/bin/python',Path(sys.executable)]
    python=None
    errors=[]
    for p in pythons:
        if not p.is_file():continue
        key=(str(p),p.stat().st_mtime_ns)
        with _LOCK:cached=_PROBES.get(key)
        if force or cached is None or time.monotonic()-cached[0]>60:
            result=probe(p)
            with _LOCK:_PROBES[key]=(time.monotonic(),result)
        else:result=cached[1]
        if result['available']:python=str(p);break
        errors.append(f"{p}: {result['error']}")
    exporters=[resolve_option(cfg['icon_exporter'])] if cfg.get('icon_exporter') else []
    omc=shutil.which(str(resolve_option(cfg['omc_command'])) if '/' in cfg.get('omc_command','') else cfg.get('omc_command','omc'))
    if omc:
        prefix=Path(omc).resolve().parent.parent
        exporters += [prefix/'share/doc/omc/testmodels/generate_icons.py',prefix/'share/omc/scripts/generate_icons.py']
    exporters += [Path('/usr/share/doc/omc/testmodels/generate_icons.py'),ROOT/'bundle/tools/generate_icons.py']
    exporter=next((str(p) for p in exporters if p.is_file()),None)
    library=resolve_option(cfg.get('openhydraulics_package','bundle/OpenHydraulics/package.mo')).resolve()
    available=bool(python and exporter and omc and library.is_file())
    missing=[]
    if not python:
        missing.append('Python renderer imports failed. '+ ' | '.join(errors))
        missing.append('Install dependencies manually using the commands below, then select Auto-detect. No packages are installed automatically.')
    if not omc:missing.append('OpenModelica compiler (omc) was not found. Install it or correct omc_command in config.json.')
    if not exporter:missing.append('generate_icons.py was not found. Set icon_exporter to the script supplied by your OpenModelica installation.')
    if not library.is_file():missing.append('OpenHydraulics package.mo was not found. Install OpenHydraulics and configure openhydraulics_package or T2H_OPENHYDRAULICS.')
    message='Model diagram renderer is ready.' if available else ' '.join(missing)

    return {'available':available,'python':python,'exporter':exporter,'omc':omc,'library':str(library),'message':message,'commands':installation_commands(ROOT) if not python else ''}


def cache_directory(run_path, key):
    if not re.fullmatch(r'[a-f0-9]{64}',key):raise ValueError('Invalid model preview key')
    folder=run_path/'model-previews'/key
    if (run_path/'model-previews').is_symlink() or folder.is_symlink():raise ValueError('Invalid preview directory')
    folder.resolve().relative_to(run_path.resolve())
    return folder


def _kill(process):
    if process is not None:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass


def shutdown():
    _STOP.set()
    with _LOCK:_kill(_PROCESS)


def _loop():
    global _PROCESS
    while not _STOP.is_set():
        try:job=_QUEUE.get(timeout=.5)
        except queue.Empty:continue
        identity,folder,content,filename,env,timeout=job
        with _LOCK:_JOBS[identity]={'status':'rendering','message':'Rendering the model diagram…'}
        process=None
        try:
            folder.mkdir(parents=True,exist_ok=True)
            work=folder/'.work';work.mkdir(exist_ok=True)
            if work.is_symlink():raise ValueError('Invalid render workspace')
            model=work/filename
            if model.is_symlink():raise ValueError('Invalid model snapshot')
            model.write_bytes(content)
            runtime=dict(os.environ)
            runtime['PATH']=str(Path(env['omc']).parent)+os.pathsep+runtime.get('PATH','')
            runtime['OPENMODELICAHOME']=str(Path(env['omc']).resolve().parent.parent)
            runtime['PYTHONUNBUFFERED']='1'
            command=[env['python'],str(ROOT/'render_model.py'),'--model',str(model),'--library',env['library'],'--exporter',env['exporter'],'--output',str(folder)]
            with (folder/'render.log').open('w') as log:
                with _LOCK:
                    if _STOP.is_set():raise RuntimeError('Renderer stopped')
                    process=subprocess.Popen(command,cwd=work,env=runtime,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    _PROCESS=process
                try:code=process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    _kill(process);process.wait()
                    raise RuntimeError('Model diagram rendering timed out. See the exported render log.')
            if code or not (folder/'diagram.svg').is_file() or not (folder/'diagram.png').is_file():
                with (folder/'render.log').open('rb') as log:
                    log.seek(max(0,log.seek(0,2)-16000))
                    detail=log.read().decode('utf-8',errors='replace')
                if 'No placed components found' in detail:
                    raise RuntimeError(f'{filename} has no component Placement annotations. Select the final circuit model from the Model list, or add Placement annotations to this model.')
                raise RuntimeError('Model diagram could not be rendered. Check Placement / Line annotations and the exported render log.')
            metadata=read_json(folder/'metadata.json',{})
            result={'status':'ready','message':'Library icons with model Placement and Line annotations.',**metadata}
        except Exception as exc:
            result={'status':'failed','message':str(exc)}
        finally:
            with _LOCK:_PROCESS=None
            _kill(process)
        # Publish completion only after both image files have been written.
        try:(folder/'result.json').write_text(json.dumps(result))
        except OSError:pass
        with _LOCK:_JOBS.pop(identity,None)
        _QUEUE.task_done()


def preview(run_path, requested, cfg, resolve, list_artifacts, retry=False):
    global _THREAD
    from visualization import candidates
    files,paths=candidates(run_path,'.mo',requested,resolve,list_artifacts,'mo_file')
    base={'files':files,'status':'empty','message':'The model diagram will appear after a .mo file is generated.'}
    if not paths:return base
    model=paths[0]
    base.update(file=model.relative_to(run_path).as_posix())
    if model.stat().st_size>4_000_000:
        return {**base,'status':'failed','message':'Model exceeds the 4 MB preview limit.'}
    env=environment(cfg)
    if not env['available']:return {**base,'status':'unavailable','message':env['message']}
    content=model.read_bytes()
    fingerprint=hashlib.sha256(content)
    for file in [Path(env['exporter']),Path(env['library']),ROOT/'render_model.py',ROOT/'visualization.py']:
        stat=file.stat();fingerprint.update(f'{file}:{stat.st_mtime_ns}:{stat.st_size}'.encode())
    fingerprint.update(json.dumps({k:env[k] for k in ('python','exporter','omc','library')},sort_keys=True).encode())
    key=fingerprint.hexdigest();folder=cache_directory(run_path,key)
    base['key']=key
    previous=read_json(folder/'result.json')
    if previous and not retry:
        if previous.get('status')!='ready' or all((folder/f'diagram.{fmt}').is_file() for fmt in ['svg','png']):
            return {**base,**previous}
    identity=str(folder)
    with _LOCK:
        if identity in _JOBS:return {**base,**_JOBS[identity]}
        if _STOP.is_set():return {**base,'status':'failed','message':'Renderer is shutting down.'}
        if _QUEUE.full():return {**base,'status':'waiting','message':'Waiting for the diagram renderer…'}
        if time.time()-model.stat().st_mtime<.8:
            return {**base,'status':'waiting','message':'Waiting for the model file to finish updating…'}
        folder.mkdir(parents=True,exist_ok=True)
        if retry:
            (folder/'result.json').unlink(missing_ok=True)
        _JOBS[identity]={'status':'queued','message':'Preparing the model diagram…'}
        try:timeout=max(5,min(300,int(cfg.get('render_timeout_seconds',120))))
        except (ValueError,TypeError):timeout=120
        _QUEUE.put_nowait((identity,folder,content,model.name,env,timeout))
        if _THREAD is None or not _THREAD.is_alive():
            _THREAD=threading.Thread(target=_loop,daemon=True,name='model-diagram-renderer');_THREAD.start()
        return {**base,**_JOBS[identity]}
