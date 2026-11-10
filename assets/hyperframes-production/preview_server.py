"""Serve the project player on loopback; optional browser opening for double-click use."""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser


class Files(SimpleHTTPRequestHandler):
    def list_directory(self, path): self.send_error(403); return None
    def send_head(self):
        path=Path(self.translate_path(self.path)).resolve()
        try: rel=path.relative_to(Path(self.directory).resolve())
        except ValueError: self.send_error(403); return None
        if not rel.parts or (rel.parts[0] not in ('assets','lib','compositions') and
                (len(rel.parts)!=1 or path.suffix not in ('.html','.js','.css'))):
            self.send_error(403);return None
        return super().send_head()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=0);parser.add_argument('--open',action='store_true')
    args=parser.parse_args()
    with ThreadingHTTPServer(('127.0.0.1',args.port),functools.partial(Files,directory=str(Path(__file__).resolve().parent))) as server:
        url=f'http://127.0.0.1:{server.server_port}/play.html';print(url,flush=True)
        if args.open:webbrowser.open(url)
        server.serve_forever()
