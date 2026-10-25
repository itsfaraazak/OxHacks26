"""Local-only camera preview. No frames are uploaded to the Flask backend."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class PreviewServer:
    """Keep only the latest JPEG; slow browsers never build a frame backlog."""

    def __init__(self, port: int = 5002):
        self._condition = threading.Condition()
        self._jpeg: bytes | None = None
        self._version = 0
        self._closed = False
        preview = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                # Loopback binding plus Host validation prevents DNS rebinding.
                if self.headers.get("Host") not in {
                    f"localhost:{preview.port}", f"127.0.0.1:{preview.port}",
                }:
                    self.send_error(403)
                    return
                if self.path.split("?")[0] != "/preview.mjpg":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                version = -1
                try:
                    while True:
                        with preview._condition:
                            changed = preview._condition.wait_for(
                                lambda: preview._closed or preview._version != version, timeout=5,
                            )
                            if preview._closed or not changed:
                                return
                            version, jpeg = preview._version, preview._jpeg
                        if jpeg is None:
                            continue
                        self.wfile.write(
                            b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                            + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n"
                        )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_port
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._last_frame = 0.0

    def publish(self, frame) -> None:
        # Preview is capped at 10 fps; detection still runs at full speed.
        if time.monotonic() - self._last_frame < 0.1:
            return
        import cv2

        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with self._condition:
                self._jpeg = jpeg.tobytes()
                self._version += 1
                self._last_frame = time.monotonic()
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._server.shutdown()
        self._server.server_close()
