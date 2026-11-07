"""Standard-library bootstrap: usable even when the invoking Python lacks requests/PIL."""
import argparse
import json
import subprocess
from pathlib import Path
import config
from artifacts import atomic_json, read_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True);g.add_argument('--manifest',type=Path);g.add_argument('--job-dir',type=Path)
    a=p.parse_args()
    try:
        if a.job_dir:jobs=[str(a.job_dir.resolve())];output=a.job_dir.resolve()/'preflight.json'
        else:
            source=a.manifest.resolve();values=read_json(source)['jobs'];jobs=[]
            for value in values:
                path=Path(value).expanduser();jobs.append(str(path.resolve() if path.is_absolute() else (source.parent/path).resolve()))
            output=source.with_suffix('.preflight.json')
        script=('import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
                'import production_io;print(json.dumps(production_io.preflight([Path(p) for p in json.loads(sys.argv[2])]),ensure_ascii=False))')
        r=subprocess.run([config.PY,'-c',script,str(config.SCRIPT_DIR),json.dumps(jobs)],capture_output=True,text=True,timeout=180)
        if r.returncode:result={'ok':False,'interpreter':config.PY,'error':r.stderr[-1500:]}
        else:result=json.loads(r.stdout)
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        result={'ok':False,'interpreter':config.PY,'error':str(exc)}
        print(json.dumps(result,ensure_ascii=False));return 1
    atomic_json(output,result);print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
