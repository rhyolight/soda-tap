import json

import web
import redis

urls = (
  "/", "index",
  "/catalog", "catalog",
  "/catalog/(.+)", "catalog"
)
app = web.application(urls, globals())
render = web.template.render('templates')
r = redis.StrictRedis(host="localhost", port=6379, db=0)


def chunks(l, n):
  """Yield successive n-sized chunks from l."""
  for i in xrange(0, len(l), n):
    yield l[i:i + n]

#####
# HTTP handlers
#####

class index:
  def GET(self):
    return "Nothing to see here (maybe /catalog)."


class catalog:
  def GET(self, page=0):
    stored = r.keys("*")
    chunked = list(chunks(stored, 10))
    pageIds = chunked[int(page)]
    page = [json.loads(r.get(id)) for id in pageIds]
    print page[0]
    print len(page)
    return render.catalog(page, render.dict, render.list)


if __name__ == "__main__":
  app.run()