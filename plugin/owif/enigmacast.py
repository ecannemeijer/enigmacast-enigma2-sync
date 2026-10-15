# OpenWebif Extras entry. Loaded from WebInterface/WebChilds/External.
# It does not modify OpenWebif itself. The link uses the hostname the browser already used.
PORT = 8766


def _register():
    try:
        from Plugins.Extensions.WebInterface.WebChilds.Toplevel import addExternalChild
        from twisted.web import resource, server
    except Exception:
        return

    class Page(resource.Resource):
        isLeaf = True

        def render(self, request):
            host = request.getRequestHostname()
            if isinstance(host, bytes):
                host = host.decode("ascii", "ignore")
            host = str(host).split("%", 1)[0]
            request.redirect(("https://%s:%s/" % (host, PORT)).encode("ascii"))
            request.finish()
            return server.NOT_DONE_YET

    addExternalChild(("enigmacast", Page(), "EnigmaCast Sync Server", "0.2.4", True, "_blank"))


_register()
