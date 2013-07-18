import logging
import uuid
import datetime
import mimetypes
from Cookie import BaseCookie
from google.appengine.runtime import DeadlineExceededError
from google.appengine.runtime.apiproxy_errors import CapabilityDisabledError, ApplicationError
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util
from google.appengine.api import urlfetch, memcache
from django.utils import simplejson

PASSIFOX_GITHUB_URL = "https://github.com/pfn/passifox/raw/master"
KEEPASSHTTP_GITHUB_URL = "https://github.com/pfn/keepasshttp/raw/master"

class StatusException(Exception):
    def __init__(self, msg, code):
        super(StatusException, self).__init__()
        self.code = code
        self.msg = msg

class Page(db.Model):
    content   = db.BlobProperty()
    create_ts = db.DateTimeProperty(auto_now_add=True)

class Hit(db.Model):
    hit_ts = db.DateTimeProperty(auto_now=True)
    uri    = db.StringProperty()
    ip     = db.StringProperty()
    count  = db.IntegerProperty()
    vers   = db.StringProperty()

class PostReceiveHandler(webapp.RequestHandler):
    def post(self):
        payload = self.request.get('payload', None)
        if not payload:
            self.error(400)
            self.response.out.write("bad request")
            return
        data = simplejson.loads(payload)
        if data['repository']['url'].find("/pfn/passifox") == -1 and data['repository']['url'].find("/pfn/keepasshttp") == -1:
            self.error(403)
            self.response.out.write("bad repository")
            return
        if data.has_key('commits'):
            for commit in data['commits']:
                if commit.has_key('modified'):
                    modified = commit['modified']
                    if len(modified) > 0:
                        db.delete(Page.all())
        else:
            self.error(304)
            self.response.out.write("nothing to do")
            return

def set_cookie(key, value, response):
    max_age = 365 * 24 * 60 * 60
    path = '/'
    # taken from webob.Response, don't use these fields for now
    domain   = None
    secure   = None
    httponly = None
    comment  = None
    version  = None

    cookies = BaseCookie()
    cookies[key] = value
    for var_name, var_value in [
        ('max_age', max_age),
        ('path', path),
        ('domain', domain),
        ('secure', secure),
        ('HttpOnly', httponly),
        ('version', version),
        ('comment', comment),
        ]:
        if var_value is not None and var_value is not False:
            cookies[key][var_name.replace('_', '-')] = str(var_value)

    header_value = cookies[key].output(header='').lstrip()
    response.headers['Set-Cookie'] = header_value

def get_ua_version(request):
    ua = request.headers['user-agent']
    gecko = None
    ff = None
    if ua:
        if ua.find("Gecko/") != -1:
            end = ua.find(")")
            start = -1
            if end != -1:
                start = ua.find(":") + 1
            if start > 0:
                gecko = ua[start:end]
            start = ua.rfind("/") + 1
            end = ua.rfind(",")
            if end == -1: end = None
            if gecko is not None and start > 0:
                ff = ua[start:end]
    if gecko and ff:
        return "%s - %s" % (ff, gecko)

def update_install_tracker(request, response):
    name = 'passifox-install'
    if request.cookies.has_key(name):
        uid = request.cookies[name]
    else:
        uid = str(uuid.uuid4())
    set_cookie(name, uid, response)
    hit = Hit.get_by_key_name(uid)
    if not hit:
        hit = Hit(key_name=uid)
    hit.uri = request.path
    hit.ip = request.remote_addr
    ua = get_ua_version(request)
    if ua:
        hit.vers = ua
    if not hit.count:
        hit.count = 0
    hit.count += 1
    try:
        hit.put()
    except (CapabilityDisabledError, DeadlineExceededError, ApplicationError), e:
        logging.error("Unable to save hit count update: " + str(e))

def get_page_content(request, response, uri=None, source=PASSIFOX_GITHUB_URL):
    if uri is None:
        # only update for file download, not README
        update_install_tracker(request, response)
        uri = request.path

    content = memcache.get(uri)
    if content:
        return content

    page = Page.get_by_key_name(uri)

    if not page:
        url = "%s%s" % (source, uri)
        res = urlfetch.fetch(url)
        if res.status_code == 200:
            page = Page(key_name=uri)
            page.content = db.Blob(res.content)
            memcache.set(uri, page.content)
            try:
                page.put()
            except (CapabilityDisabledError, DeadlineExceededError, ApplicationError), e:
                logging.error("Unable to save page content: " + str(e))
        else:
            ex = StatusException(res.content, res.status_code)
            raise ex

    return page.content

class UpdateFileHandler(webapp.RequestHandler):
    def get(self):
        try:
            c = get_page_content(self.request, self.response)
            self.response.headers['Content-type'] = "application/xml"
            self.response.out.write(c)
        except StatusException, e:
            self.error(e.code)
            self.response.out.write(e.msg)

class InstallFileHandler(webapp.RequestHandler):
    def get(self):
        try:
            c = get_page_content(self.request, self.response)
            self.response.headers['Content-type'] = "application/x-xpinstall"
            self.response.out.write(c)
        except StatusException, e:
            self.error(e.code)
            self.response.out.write(e.msg)

class KeePassHttpPLGXHandler(webapp.RequestHandler):
    def get(self):
        try:
            c = get_page_content(self.request, self.response, '/KeePassHttp.plgx', KEEPASSHTTP_GITHUB_URL)
            self.response.headers['Content-type'] = "application/octet-stream"
            self.response.headers['Content-disposition'] = "attachment; filename=KeePassHttp.plgx"
            self.response.out.write(c)
        except StatusException, e:
            self.error(e.code)
            self.response.out.write(e.msg)

class KeePassHttpUpdateHandler(webapp.RequestHandler):
    def get(self):
        try:
            c = get_page_content(self.request, self.response, '/update-version.txt', KEEPASSHTTP_GITHUB_URL)
            self.response.headers['Content-type'] = "text/plain"
            self.response.out.write(c)
        except StatusException, e:
            self.error(e.code)
            self.response.out.write(e.msg)

class KPHContentHandler(webapp.RequestHandler):
    def get(self):
        try:
            p = self.request.path[len("/kph"):]
            fn = p[p.rindex("/") + 1:]
            mtype = mimetypes.guess_type(fn, False)
            if mtype[1]:
              mime = "%s; charset=%s" % (mtype[0], mtype[1])
            else:
              mime = mtype[0]
            c = get_page_content(self.request, self.response, p, KEEPASSHTTP_GITHUB_URL)
            self.response.headers['Content-type'] = mime
            if mime.startswith("application/"):
              disp = "attachment; filename=%s" % fn
            else:
              disp = "inline"
            self.response.headers['Content-disposition'] = disp
            self.response.out.write(c)
        except StatusException, e:
            self.error(e.code)
            self.response.out.write(e.msg)

class PIFContentHandler(webapp.RequestHandler):
    def get(self):
        try:
            p = self.request.path[len("/ext"):]
            fn = p[p.rindex("/") + 1:]
            mtype = mimetypes.guess_type(fn, False)
            if mtype[1]:
              mime = "%s; charset=%s" % (mtype[0], mtype[1])
            else:
              mime = mtype[0]
            c = get_page_content(self.request, self.response, p)
            self.response.headers['Content-type'] = mime
            if mime.startswith("application/"):
              disp = "attachment; filename=%s" % fn
            else:
              disp = "inline"
            self.response.headers['Content-disposition'] = disp
            self.response.out.write(c)
        except StatusException, e:
            self.error(e.code)
            self.response.out.write(e.msg)

class RedirectToRootHandler(webapp.RequestHandler):
    def get(self):
        self.redirect('/')

class ClearHitsHandler(webapp.RequestHandler):
    def get(self):
        now = datetime.datetime.now()
        lastweek = now - datetime.timedelta(days=7)
        hits = db.GqlQuery("SELECT __key__ FROM Hit WHERE hit_ts < :1", 
                lastweek)
        db.delete(hits)

class RootHandler(webapp.RequestHandler):
    def get(self):
        try:
            c = get_page_content(self.request, self.response, '/README.md')
            self.response.headers['Content-type'] = "text/plain"
            self.response.out.write(c)
        except StatusException, e:
            self.error(e.code)
            self.response.out.write(e.msg)

application = webapp.WSGIApplication([
        ('/',                    RootHandler),
        ('/cron/clearhits',      ClearHitsHandler),
        ('/update.rdf',          UpdateFileHandler),
        ('/update-version.txt',  KeePassHttpUpdateHandler),
        ('/passifox.xpi',        InstallFileHandler),
        ('/KeePassHttp.plgx',    KeePassHttpPLGXHandler),
        ('/github-post-receive', PostReceiveHandler),
        (r'/kph/.*',             KPHContentHandler),
        (r'/ext/.*',             PIFContentHandler),
        (r'/.*',                 RedirectToRootHandler),
], debug=True)

def main():
    util.run_wsgi_app(application)

if __name__ == '__main__':
    main()
