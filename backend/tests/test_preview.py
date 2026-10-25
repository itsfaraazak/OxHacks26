import http.client
import threading

from detector.preview import PreviewServer


def test_preview_stream_is_local_and_sends_latest_frame():
    preview = PreviewServer(port=0)
    conn = http.client.HTTPConnection("127.0.0.1", preview.port, timeout=2)
    try:
        # A JPEG payload is opaque to the server. Test transport without ML dependencies.
        with preview._condition:
            preview._jpeg = b"test-jpeg"
            preview._version += 1
            preview._condition.notify_all()
        conn.request("GET", "/preview.mjpg")
        response = conn.getresponse()
        assert response.status == 200
        assert response.getheader("Cache-Control") == "no-store"
        assert response.getheader("Content-Type") == "multipart/x-mixed-replace; boundary=frame"
        assert response.readline() == b"--frame\r\n"
        assert response.readline() == b"Content-Type: image/jpeg\r\n"
        assert response.readline() == b"Content-Length: 9\r\n"
        assert response.readline() == b"\r\n"
        assert response.read(9) == b"test-jpeg"

        # Stop wakes an active stream instead of leaving it hanging.
        threading.Thread(target=preview.close).start()
        assert response.read() == b"\r\n"
    finally:
        conn.close()


def test_preview_rejects_non_loopback_host_and_unknown_path():
    preview = PreviewServer(port=0)
    try:
        for path, headers, status in [
            ("/preview.mjpg", {"Host": "attacker.example"}, 403),
            ("/other", {}, 404),
        ]:
            conn = http.client.HTTPConnection("127.0.0.1", preview.port, timeout=2)
            conn.request("GET", path, headers=headers)
            assert conn.getresponse().status == status
            conn.close()
    finally:
        preview.close()
