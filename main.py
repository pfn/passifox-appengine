import logging
import uuid
from Cookie import BaseCookie
from google.appengine.ext import webapp, db
from google.appengine.ext.webapp import util
from google.appengine.api import urlfetch
from django.utils import simplejson

GITHUB_URL = "https://github.com/pfn/passifox/raw/master"
CACHED_FILES = ('update.rdf', 'passifox.xpi')

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

class PostReceiveHandler(webapp.RequestHandler):
    def post(self):
        payload = self.request.get('payload', None)
        if not payload:
            self.error(400)
            self.response.out.write("bad request")
            return
        data = simplejson.loads(payload)
        if data['repository']['url'].find("/pfn/passifox") == -1:
            self.error(403)
            self.response.out.write("bad repository")
            return
        if data.has_key('commits'):
            updated = False
            for commit in data['commits']:
                if commit.has_key('modified'):
                    modified = commit['modified']
                    for f in CACHED_FILES:
                        if f in modified:
                            p = Page.get_by_key_name("/" + f)
                            if p:
                                updated = True
                                p.delete()

            if not updated:
                self.error(304)
                self.response.out.write("no files cached")
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
    hit.put()

def get_page_content(request, response):
    uri = request.path
    page = Page.get_by_key_name(uri)

    update_install_tracker(request, response)
    if not page:
        url = "%s%s" % (GITHUB_URL, uri)
        res= urlfetch.fetch(url)
        if res.status_code == 200:
            page = Page(key_name=uri)
            page.content = db.Blob(res.content)
            page.put()
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

class RedirectToRootHandler(webapp.RequestHandler):
    def get(self):
        self.redirect('/')

class RootHandler(webapp.RequestHandler):
    def get(self):
        self.response.out.write('PassIFox File Host')

application = webapp.WSGIApplication([
        ('/',                    RootHandler),
        ('/update.rdf',          UpdateFileHandler),
        ('/passifox.xpi',        InstallFileHandler),
        ('/github-post-receive', PostReceiveHandler),
        (r'/.*',                 RedirectToRootHandler),
], debug=True)

def main():
    util.run_wsgi_app(application)

if __name__ == '__main__':
    main()
