"""Shared explicit route dispatch for ASGI and the compatibility HTTP adapter."""

def response(status, value):
    return status, value


def dispatch(service, method, parts, body=None, query=None):
    supervisor = service.supervisor
    mutation = method != 'GET'
    if method == 'POST' and parts == ['api', 'workspaces']:
        return response(200, service.create_draft(body))
    if method == 'POST' and parts == ['api', 'workspaces', 'setup']:
        return response(200, service.save_setup(body))
    if method == 'POST' and parts == ['api', 'library', 'compatibility']:
        return response(200, service.compatibility(body))
    if method == 'GET' and parts == ['api', 'library']:
        return response(200, service.library_page(query) if query is not None else service.library())
    if method == 'GET' and len(parts) == 5 and parts[:3] == ['api', 'library', 'sources']:
        if parts[4] in {'preflight', 'inspect'}:
            return response(200, service.inspect_source(parts[3], detailed=parts[4] == 'inspect'))
    if len(parts) >= 4 and parts[:2] == ['api', 'workspaces']:
        ident, tail = parts[2], parts[3:]
        if method == 'POST':
            if tail == ['analysis-reset']:
                return response(200, service.reset_analysis(ident, body))
            if tail == ['prepare']:
                return response(202, service.prepare(ident, body))
            if tail == ['reprepare']:
                return response(202, service.reprepare(ident, body))
            if tail in (['archive'], ['restore']):
                return response(200, service.archive(ident, body, tail == ['archive']))
            if tail == ['review', 'confirm-and-approve']:
                return response(200, service.approve(ident, body, confirm_review=True))
        if method == 'PATCH':
            if tail == ['settings']:
                return response(200, service.configure(ident, body))
            if len(tail) == 2 and tail[0] == 'sections':
                return response(200, service.configure(ident, body, tail[1]))
        if method == 'GET':
            if tail == ['analysis-reset']:
                return response(200, service.analysis_reset_status(ident))
            if tail == ['preparation']:
                return response(200, service.application.web.preparation(service.workspaces.resolve(ident)))
            if tail == ['activity']:
                return response(200, service.activity(ident))
            if len(tail) in {2, 3} and tail[0] == 'sections':
                return response(200, service.application.web.preview(service.workspaces.resolve(ident), tail[1], int(tail[2]) if len(tail) == 3 else 0))
    if mutation:
        if method == 'POST' and parts == ['api', 'imports']:
            return response(202, service.import_book(body))
        if len(parts) >= 4 and parts[:2] == ['api', 'workspaces']:
            ident, tail = parts[2], parts[3:]
            if method == 'POST':
                if tail == ['approve']:
                    return response(200, service.approve(ident, body))
                for route, operation in [('prepare', 'prepare'), ('bulk-review', 'bulk'), ('confirmation', 'confirmation')]:
                    if tail == ['review', route]:
                        return response(200, service.review(ident, operation, body))
                if tail == ['reader', 'context']:
                    return response(200, service.reader(ident, 'context', body))
                if tail == ['reader', 'markers']:
                    return response(200, service.reader(ident, 'create', body))
            if method == 'PATCH' and len(tail) == 3 and tail[:2] == ['review', 'terms']:
                return response(200, service.review(ident, 'patch', body, tail[2]))
            if method == 'DELETE' and len(tail) == 3 and tail[:2] == ['reader', 'markers']:
                return response(200, service.reader(ident, 'delete', body, tail[2]))
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "jobs":
            return response(202, service.start(parts[2], body))
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "stop":
            if body:
                raise ValueError("Stop expects an empty object.")
            return response(202, supervisor.stop(parts[2]).public())
    else:
        if len(parts) == 3 and parts[:2] == ['api', 'requests']:
            return response(200, supervisor.registry.receipt(supervisor.workspace_root, parts[2]).public())
        if parts == ['api', 'capabilities']:
            return response(200, service.capabilities())
        if parts == ['api', 'profiles']:
            return response(200, service.profiles())
        if parts == ['api', 'import-sources']:
            return response(200, {'sources': service.imports.sources()})
        if len(parts) >= 4 and parts[:2] == ['api', 'workspaces']:
            ident, tail = parts[2], parts[3:]
            if tail == ['pipeline']:
                return response(200, service.pipeline(ident))
            if tail in (['profiles'], ['settings']):
                return response(200, service.settings(ident))
            if tail == ['review']:
                return response(200, service.review(ident))
            if len(tail) == 4 and tail[:2] == ['review', 'terms'] and tail[3] == 'evidence':
                return response(200, service.review(ident, 'evidence', term_id=tail[2]))
            if tail == ['reader']:
                return response(200, service.reader(ident))
            if tail in (['reader', 'progress'], ['reader', 'markers']):
                return response(200, service.reader(ident, tail[1]))
            if len(tail) == 3 and tail[:2] == ['reader', 'chapters']:
                return response(200, service.reader(ident, 'chapter', identifier=tail[2]))
        if parts == ["api", "health"]:
            return response(200, {"status": "ok"})
        if parts == ["api", "workspaces"]:
            return response(200, {"workspaces": service.list_workspaces()})
        if len(parts) == 3 and parts[:2] == ["api", "workspaces"]:
            return response(200, service.workspace(parts[2]))
        if len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "usage":
            return response(200, service.usage(parts[2]))
        if parts == ["api", "jobs"]:
            return response(200, supervisor.snapshot())
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            return response(200, supervisor.get(parts[2]).public())
        if parts == ["api", "events"]:
            raise ValueError('Events use the stream adapter.')
    return 404, {'error': {'code': 'not_found', 'message': 'Route not found.', 'details': {}}}
