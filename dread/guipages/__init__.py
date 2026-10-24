"""The pages of dread gui beyond browsing an image: each one a module of this package with its API
routes, and a script (and a stylesheet) of its own in dread/web.

dread gui imports every module of this package when it starts (load(), in the order of their names);
a module registers its routes and its page as it is imported. Adding a page touches no line of another
page: a new module here and a new script in dread/web. The pages of browsing (the overview, a
function, the relations, the whole program, the system run) came before this and stay in dread/gui.py
and dread/web/app.js; they answer through the same server.

1. dread/guipages/NAME.py:

    from dread.guipages import Failure, page, route

    page("NAME", script="NAME.js", style="NAME.css", status=lambda server: {...})

    @route("GET", "/api/NAME/things")
    def things(request):
        return {"things": [...]}                    # JSON with status 200, or (status, data)

    @route("POST", "/api/NAME/do")                  # a change: it needs the page's token, and
    def do(request):                                # dread gui --read-only refuses it
        what = request.data.get("what")             # the request's JSON
        ...

   page(): the files in dread/web the page brings (served at /NAME.js and /NAME.css), and a function
   whose answer joins /api/status as "pages": {NAME: ...} (the page polls /api/status every few
   seconds: a page that follows something running gets its news there without a poll of its own).
   A route gets a Request: .query (the query string's values), .data (a POST's JSON object), .body (a
   raw route's bytes: route("POST", PATH, raw=True), an upload, refused beyond the server's limit before
   it is read), .server, .target (None while the server has no target: the Setup page makes or opens
   one, server.open_target), .image (the name of the image the page shows), .state() (that image's
   dread.gui State, loaded and refreshed: costly the first time on a large image, so only where it is
   needed), .jobs (the server's dread.jobs Jobs: the target's, or without a target the user's, run in
   server.home) and .read_only. A page runs DREAD's commands as jobs with
   dread.guipages.run start_command(request, COMMAND, values=... or args=[...], grace=, after=, cwd=):
   the record's "id" is the job, #/jobs/N its page, /api/job-log its log; after= chains jobs. It returns data or (status, data); Failure(status, message) answers {"error":
   message}, a KeyError 404, a ValueError or TypeError 400, anything else 500 (the traceback on the
   server's stderr).

2. dread/web/NAME.js, an ES module the page imports when it starts, after app.js:

    import { register, enter, api, esc, isCurrent, nextRender } from "./app.js";

    register({
      name: "NAME",
      match: /^\\/NAME(?:\\/(.*))?$/,           // the address it shows: #/NAME, #/NAME/...
      show: async (match, keep) => {            // keep: shown again (a reload), keep the scroll
        const mine = nextRender();
        const d = await api("/api/NAME/things");
        if (!isCurrent(mine)) return;           // another view was asked for meanwhile
        const main = enter("NAME", "Things");   // stops the other views' animations, sets the title
        main.innerHTML = ...;
      },
      leave: () => {},                          // another view is shown: stop timers
      status: (data) => {},                     // this page's share of /api/status, at every poll
      keys: { x: () => { location.hash = "#/NAME"; } },
      help: [["x", "what x does"]],             // rows of the keys' help
      nav: { title: "Things (x)", href: "#/NAME", icon: "<svg ...>" },     // a button in the bar
    });

   app.js exports its helpers (api: GET, or POST with the token when given a body; esc, $, toast,
   size, ago, when, hex, prefs, S: the view's state, watch, showError, GUI: {pages, readOnly}); a
   page's script imports from app.js alone, not from another page's. Its stylesheet uses app.css's
   colour variables, so that both themes hold. tests/test_gui_browser.py drives the Run page in a
   browser; a new page can be looked at the same way.
"""
import importlib
import os
import pkgutil
import re

ROUTES = {}         # (method, path) -> Route
PAGES = {}          # name -> Page
_loaded = []
WEB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
ASSET = re.compile(r"[A-Za-z0-9_-]+\.(js|css)")
KINDS = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8"}


class Failure(Exception):
    """An answer other than 200, with a message for the page."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Route:
    def __init__(self, fn, method, path, change, raw=False):
        self.fn, self.method, self.path, self.change, self.raw = fn, method, path, change, raw


class Page:
    def __init__(self, name, script, style, status):
        self.name, self.script, self.style, self.status = name, script, style, status

    def brief(self):
        return {"name": self.name, "script": self.script, "style": self.style}


def route(method, path, change=None, raw=False):
    """Register a function as the answer to METHOD PATH. change (default: every POST): it needs the
    page's token and is refused by a read-only server (a POST always needs the token). raw (a POST): the
    request's body is a file's bytes, not JSON (request.body), refused beyond the server's upload limit
    (dread gui --upload-limit) before it is read."""
    method = method.upper()

    def register(fn):
        key = (method, path)
        if key in ROUTES and ROUTES[key].fn.__module__ != fn.__module__:
            raise ValueError("%s %s is %s's already" % (method, path, ROUTES[key].fn.__module__))
        ROUTES[key] = Route(fn, method, path, method == "POST" if change is None else bool(change),
                            raw and method == "POST")
        return fn
    return register


def page(name, script=None, style=None, status=None):
    """Register a page: its script and stylesheet in dread/web, and what it adds to /api/status."""
    for f in (script, style):
        if f is not None and not ASSET.fullmatch(f):
            raise ValueError("a page's file is a plain name in dread/web ending in .js or .css, not %r" % f)
    PAGES[name] = Page(name, script, style, status)
    return PAGES[name]


def load():
    """Import every module of this package once (in the order of their names): their routes and pages
    register. Returns PAGES."""
    if not _loaded:
        for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
            importlib.import_module(__name__ + "." + info.name)
        _loaded.append(True)
    return PAGES


def assets():
    """{"/FILE": (path, content type)} of the pages' scripts and stylesheets that exist."""
    out = {}
    for p in PAGES.values():
        for f in (p.script, p.style):
            if f and os.path.isfile(os.path.join(WEB, f)):
                out["/" + f] = (os.path.join(WEB, f), KINDS[f.rsplit(".", 1)[1]])
    return out


def statuses(server):
    """{page: its status} of the pages that add to /api/status."""
    out = {}
    for p in PAGES.values():
        if p.status is not None:
            try:
                out[p.name] = p.status(server)
            except Exception as e:          # (a page's status never takes the others' with it)
                out[p.name] = {"error": str(e)}
    return out


class Request:
    """What a route gets (see the module's docstring)."""

    def __init__(self, handler, query, data=None, body=None):
        self.handler, self.query, self.data = handler, query, data if data is not None else {}
        self.body = body                    # (a raw route's bytes)
        self.server = handler.server

    @property
    def target(self):
        return self.handler.target

    @property
    def image(self):
        return self.query.get("image") or self.handler.default

    @property
    def jobs(self):
        return getattr(self.server, "jobs", None)

    @property
    def read_only(self):
        return bool(getattr(self.server, "read_only", False))

    def state(self):
        return self.handler.state(self.query).refresh()
