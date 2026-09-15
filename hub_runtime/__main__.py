"""One model instance shared by the unchanged API contract and copied V1 UI."""

import os
import json
import sys
from fastapi.responses import RedirectResponse, JSONResponse
from ttd_model_runtime.home_probe import build_home_payload
from ttd_model_runtime import Runtime
from ttd_model_runtime.integrations.fastapi import attach
if os.getenv("HUB_MODEL_VARIANT", "v1") == "v2":
    from .adapter_v2 import load_model, cleanup, completion
    from .api_v2 import build_api
    UI_ORIGIN = "http://svc-v2-api"
else:
    from .adapter import load_model, cleanup, completion
    from .api import build_api
    UI_ORIGIN = "http://svc-api"


def create_app(runtime=None):
    runtime = runtime or Runtime(load_model, cleanup=cleanup, completion=completion)
    app = build_api(runtime)

    # Existing root remains the API home. UI is newly exposed at its canonical
    # friendly alias; the shared seed-vc/v1 path still serves its original API.
    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def home():
        status = runtime.status()
        healthy = status["accepting"] and status["healthy"]
        return JSONResponse(
            status_code=200 if healthy else 503,
            content=build_home_payload(
                app_name=app.title,
                healthy=healthy,
                unhealthy_detail=runtime.health.snapshot(),
            ),
        )

    @app.middleware("http")
    async def canonical_ui(request, call_next):
        # Gradio 5.23 get_api_call_path uses X-Forwarded-Host as a URL and loses
        # its queue path. CPU entry already preserves Host, so omit this redundant
        # header for UI routes without patching or upgrading native Gradio.
        if request.url.path == "/ui" or request.url.path.startswith("/ui/"):
            request.scope["headers"] = [
                (k, v)
                for k, v in request.scope["headers"]
                if k.lower() != b"x-forwarded-host"
            ]
        if request.url.hostname == "seed-vc" and (
            request.url.path == "/ui" or request.url.path.startswith("/ui/")
        ):
            return RedirectResponse(
                UI_ORIGIN
                + request.url.path
                + ("?" + request.url.query if request.url.query else "")
            )
        return await call_next(request)

    def ui():
        if os.getenv("HUB_MODEL_VARIANT", "v1") == "v2":
            from .ui_v2 import build_ui
        else:
            from .ui import build_ui

        return build_ui(runtime)

    return attach(app, runtime=runtime, ui_factory=ui, ui_path="/ui")


def main():
    if sys.argv[1:] == ["describe"]:

        class Contract:
            def task(self, function):
                return function

        print(json.dumps(build_api(Contract()).openapi(), ensure_ascii=False))
        return
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=7856)


if __name__ == "__main__":
    main()
