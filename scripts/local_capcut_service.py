"""Start the existing CapCut Mate router on loopback; no project edits or installs."""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-dir',type=Path,required=True)
    parser.add_argument('--port',type=int,default=30000)
    args=parser.parse_args();project=args.project_dir.resolve()
    os.environ['DRAFT_URL']=f'http://127.0.0.1:{args.port}/openapi/capcut-mate/v1/get_draft'
    os.environ['DOWNLOAD_URL']=f'http://127.0.0.1:{args.port}'
    # The subprocess imports the project's config, not the skill's same-named module.
    sys.path.insert(0,str(project))
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles
    from src.router import v1_router
    from src.middlewares import PrepareMiddleware,ResponseMiddleware,TraceContextMiddleware
    import uvicorn
    api=FastAPI();api.include_router(v1_router,prefix='/openapi/capcut-mate')
    api.add_middleware(PrepareMiddleware);api.add_middleware(ResponseMiddleware);api.add_middleware(TraceContextMiddleware)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.get('/openapi.json')
    def schema():return JSONResponse(api.openapi())
    (project/'output').mkdir(exist_ok=True)
    app.mount('/output',StaticFiles(directory=project/'output'))
    app.mount('/',api)
    uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__':main()
