import inspect
import typing

from starlette.routing import Lifespan

from .app_types import ASGIApp, ErrorHandler, EventHandler, Receive, Scope, Send
from .compat import WSGIApp
from .config import settings
from .error_handlers import error_to_json, error_to_text
from .errors import HTTPError
from .injection import STORE
from .meta import DocsMeta
from .middleware import (
    ExceptionMiddleware,
    RequestResponseMiddleware,
    ServerErrorMiddleware,
)
from .routing import Router

if typing.TYPE_CHECKING:  # pragma: no cover
    from .recipes import Recipe


class App(metaclass=DocsMeta):
    """The all-mighty application class.

    This class implements the [ASGI](https://asgi.readthedocs.io) protocol.

    # Example

    ```python
    >>> from bocadillo import App
    >>> app = App()
    ```

    # Parameters
    name (str):
        An optional name for the app.
    """

    __slots__ = (
        "name",
        "router",
        "_exception_middleware",
        "_asgi",
        "_lifespan",
    )

    def __init__(self, name: str = None):
        self.name = name

        self.router = Router()

        self._exception_middleware = ExceptionMiddleware(
            self.router, handlers={HTTPError: error_to_json}
        )
        self._asgi = RequestResponseMiddleware(
            ServerErrorMiddleware(
                self._exception_middleware, handler=error_to_text
            )
        )

        self._lifespan = Lifespan()

        # Startup checks.
        @self.on("startup")
        async def check_app():
            if not settings.configured:
                raise RuntimeError(
                    "You must call `configure(app)` before serving `app`. "
                )

        # NOTE: discover providers from `providerconf` at instanciation time,
        # so that further declared views correctly resolve providers.
        STORE.discover_default()

    def mount(self, prefix: str, app: typing.Union["App", ASGIApp, WSGIApp]):
        """Mount another WSGI or ASGI app at the given prefix.

        [WSGI]: https://wsgi.readthedocs.io
        [ASGI]: https://asgi.readthedocs.io

        # Parameters
        prefix (str):
            A path prefix where the app should be mounted, e.g. `"/myapp"`.
        app:
            an object implementing the [WSGI] or [ASGI] protocol.
        """
        return self.router.mount(prefix, app)

    def route(self, pattern: str):
        """Register an HTTP route by decorating a view.

        # Parameters
        pattern (str): an URL pattern.
        """
        return self.router.route(pattern)

    def websocket_route(
        self,
        pattern: str,
        *,
        auto_accept: bool = True,
        value_type: str = None,
        receive_type: str = None,
        send_type: str = None,
        caught_close_codes: typing.Tuple[int] = None,
    ):
        """Register a WebSocket route by decorating a view.

        See #::bocadillo.websockets#WebSocket for a description of keyword
        parameters.

        # Parameters
        pattern (str): an URL pattern.
        """

        return self.router.websocket_route(
            pattern,
            auto_accept=auto_accept,
            value_type=value_type,
            receive_type=receive_type,
            send_type=send_type,
            caught_close_codes=caught_close_codes,
        )

    def recipe(self, recipe: "Recipe"):
        """Apply a recipe.

        # Parameters
        recipe:
            a #::bocadillo.recipes#Recipe or #::bocadillo.recipes#RecipeBook
            to be applied to the application.

        # See Also
        - [Recipes](../guides/architecture/recipes.md)
        """
        recipe.apply(self)

    def add_error_handler(
        self, exception_cls: typing.Type[BaseException], handler: ErrorHandler
    ):
        """Register a new error handler.

        # Parameters
        exception_cls (exception class):
            The type of exception that should be handled.
        handler (callable):
            The actual error handler, which is called when an instance of
            `exception_cls` is caught.
            Should accept a request, response and exception parameters.
        """
        self._exception_middleware.add_exception_handler(exception_cls, handler)

    def error_handler(self, exception_cls: typing.Type[Exception]):
        """Register a new error handler (decorator syntax).

        # See Also
        - [add_error_handler](#add-error-handler)
        """

        def wrapper(handler):
            self.add_error_handler(exception_cls, handler)
            return handler

        return wrapper

    def add_middleware(self, middleware_cls, **kwargs):
        """Register a middleware class.

        # Parameters
        middleware_cls: a subclass of #::bocadillo.middleware#Middleware.

        # See Also
        - [Middleware](../guides/http/middleware.md)
        """
        self._exception_middleware.app = middleware_cls(
            self._exception_middleware.app, **kwargs
        )

    def add_asgi_middleware(self, middleware_cls, **kwargs):
        """Register an ASGI middleware class.

        # Parameters
        middleware_cls: a class that complies with the ASGI3 specification.

        # See Also
        - [ASGI](https://asgi.readthedocs.io)
        """
        if hasattr(middleware_cls, "__call__"):
            # Verify the class implements ASGI3, not ASGI2.
            sig = inspect.signature(middleware_cls.__call__)
            if "receive" not in sig.parameters or "send" not in sig.parameters:
                raise ValueError(
                    f"ASGI middleware class {middleware_cls.__name__} "
                    "seems to be using the legacy ASGI2 interface. "
                    "Please upgrade to ASGI3: (scope, receive, send) -> None"
                )

        self._asgi = middleware_cls(self._asgi, **kwargs)

    def on(self, event: str, handler: typing.Optional[EventHandler] = None):
        """Register an event handler.

        # Parameters
        event (str):
            Either `"startup"` (when the server boots) or `"shutdown"`
            (when the server stops).
        handler (callback, optional):
            The event handler. If not given, this should be used as a
            decorator.

        # Example

        ```python
        @app.on("startup")
        async def startup():
            pass

        async def shutdown():
            pass

        app.on("shutdown", shutdown)
        ```
        """
        if handler is None:

            def register(func):
                self._lifespan.add_event_handler(event, func)
                return func

            return register

        self._lifespan.add_event_handler(event, handler)
        return handler

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
        else:
            await self._asgi(scope, receive, send)
