"""Bounded synchronous facade over one asyncio owner for each TLS connection.

OpenSSL connection objects must not be read and written concurrently by different
threads. Only this connection's event-loop thread touches its WebSocket/TLS state.
The relay thread keeps ownership until that loop and its DNS executor finish.
"""

import asyncio
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import Event, Lock, Thread

from websockets.asyncio.client import connect


class RelayClientError(RuntimeError):
    pass


class _NoRedirect(connect):
    def process_redirect(self, exc):
        return RelayClientError("The coordinator refused the secure relay handshake")


def _consume(future):
    try:
        future.exception()
    except (CancelledError, asyncio.CancelledError):
        pass


class _Owner:
    def __init__(self, uri, options):
        self._uri, self._options = uri, options
        self._ready, self._finished, self._aborted, self._closing = Event(), Event(), Event(), Event()
        self._opened = Future()
        self._opened.add_done_callback(_consume)
        self._loop = self._connection = self._main_task = self._close_event = None
        self._thread = Thread(target=self._run, name="poltergeist-relay-io", daemon=True)

    def _run(self):
        loop = executor = None
        try:
            loop = asyncio.new_event_loop()
            executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="poltergeist-relay-dns")
            loop.set_default_executor(executor)
            asyncio.set_event_loop(loop)
            # Never format asyncio contexts: they may contain a credential-bearing
            # handshake exception, task repr or HTTP response.
            loop.set_exception_handler(lambda loop, context: None)
            self._loop = loop
            self._ready.set()
            self._main_task = loop.create_task(self._lifecycle())
            loop.run_until_complete(self._main_task)
        except BaseException:
            pass  # Bootstrap and loop failures have the same fixed outer error.
        finally:
            self._closing.set()
            self._ready.set()
            if not self._opened.done():
                self._opened.set_exception(RelayClientError("The coordinator could not be reached securely"))
            try:
                if loop is not None:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    cleanup = [asyncio.gather(*pending, return_exceptions=True)] if pending else []
                    cleanup.extend((loop.shutdown_asyncgens(), loop.shutdown_default_executor()))
                    for operation in cleanup:
                        try:
                            loop.run_until_complete(operation)
                        except BaseException:
                            pass
            finally:
                # Keep the actual executor handle: even a loop-cleanup failure
                # must not abandon an OS resolver thread. Slow DNS retains the
                # owning relay thread, so Stop keeps its runtime/profile lease.
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=True)
                self._options.clear()
                if loop is not None:
                    try:
                        loop.close()
                    except BaseException:
                        pass
                self._finished.set()

    async def _lifecycle(self):
        self._close_event = asyncio.Event()
        try:
            if self._aborted.is_set():
                return
            async with _NoRedirect(self._uri, **self._options) as connection:
                self._connection = connection
                if self._aborted.is_set():
                    connection.transport.abort()
                    self._close_event.set()
                if not self._opened.done():
                    self._opened.set_result(True)
                await self._close_event.wait()
        except (Exception, asyncio.CancelledError):
            # Preserve neither the original exception nor its HTTP response.
            pass
        finally:
            self._connection = None
            if not self._opened.done():
                self._opened.set_exception(RelayClientError("The coordinator could not be reached securely"))

    def start(self):
        try:
            self._thread.start()
        except Exception:
            self._ready.set()
            self._finished.set()
            raise RelayClientError("The coordinator could not be reached securely") from None
        self._ready.wait()
        try:
            self._opened.result()
        except BaseException:
            self.abort()
            self.wait_closed()
            raise RelayClientError("The coordinator could not be reached securely") from None

    def _post(self, callback):
        loop = self._loop
        if loop is not None and not self._finished.is_set():
            try:
                loop.call_soon_threadsafe(callback)
            except RuntimeError:
                pass  # The captured owner completed while shutdown was posted.

    def abort(self):
        self._aborted.set()
        self._closing.set()

        def interrupt():
            if self._connection is not None:
                self._connection.transport.abort()
                self._close_event.set()
            elif self._main_task is not None:
                self._main_task.cancel()

        self._post(interrupt)

    def close(self):
        self._closing.set()
        if self._close_event is not None:
            self._post(self._close_event.set)
        else:
            self.abort()
        self.wait_closed()

    def wait_closed(self):
        self._thread.join()

    def submit(self, operation):
        if self._closing.is_set() or self._finished.is_set() or self._loop is None:
            operation.close()
            raise RelayClientError("The secure relay connection was closed")
        try:
            future = asyncio.run_coroutine_threadsafe(operation, self._loop)
        except RuntimeError:
            operation.close()
            raise RelayClientError("The secure relay connection was closed") from None
        future.add_done_callback(_consume)
        return future

    async def send(self, value):
        if self._connection is None:
            raise RelayClientError("The secure relay connection was closed")
        await self._connection.send(value)

    async def recv(self):
        if self._connection is None:
            raise RelayClientError("The secure relay connection was closed")
        return await self._connection.recv()


class _AbortHandle:
    def __init__(self, owner):
        self._owner = owner

    def shutdown(self, how):
        self._owner.abort()


class _Connection:
    def __init__(self, owner):
        self._owner = owner
        self.socket = _AbortHandle(owner)
        self._receive = None
        self._receive_lock, self._send_lock = Lock(), Lock()

    def __enter__(self):
        return self

    def __exit__(self, kind, error, trace):
        self.close()

    def send(self, value):
        with self._send_lock:
            future = self._owner.submit(self._owner.send(value))
            try:
                future.result(timeout=30)
            except FutureTimeout:
                self._owner.abort()
                future.cancel()
                raise RelayClientError("The secure relay write deadline expired") from None
            except Exception:
                raise RelayClientError("The secure relay connection was closed") from None

    def recv(self, timeout=None):
        with self._receive_lock:
            if self._receive is None:
                self._receive = self._owner.submit(self._owner.recv())
            try:
                value = self._receive.result(timeout=timeout)
            except FutureTimeout:
                # Keep exactly one pending receive across facade timeouts. A
                # message arriving between polls must be returned by the next
                # poll, not lost to a cancelled receive or a second reader.
                raise TimeoutError("The secure relay receive deadline expired") from None
            except Exception:
                self._receive = None
                raise RelayClientError("The secure relay connection was closed") from None
            self._receive = None
            return value

    def close(self, code=1000):
        self._owner.close()


class RelayConnector:
    def __init__(self, uri, **options):
        self._uri, self._options = uri, options
        self._lock, self._aborted, self._owner = Lock(), False, None

    def abort(self):
        with self._lock:
            self._aborted = True
            owner = self._owner
        if owner is not None:
            owner.abort()

    def connect(self):
        with self._lock:
            if self._aborted or self._owner is not None:
                raise RelayClientError("Disconnect the existing relay before reconnecting")
            owner = self._owner = _Owner(self._uri, self._options.copy())
        owner.start()
        return _Connection(owner)
