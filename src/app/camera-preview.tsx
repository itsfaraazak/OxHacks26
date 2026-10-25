"use client";

import { useState } from "react";
import { Camera } from "lucide-react";

/** This is the Python detector's feed, not a second browser camera capture. */
export default function CameraPreview() {
  const [visible, setVisible] = useState(false);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const url = process.env.NEXT_PUBLIC_FIDO_PREVIEW ?? "http://localhost:5002/preview.mjpg";

  function show() {
    setFailed(false);
    setAttempt(n => n + 1);
    setVisible(true);
  }

  return <section className="surface detector-preview" aria-label="Detector camera preview">
    <div className="section-heading">
      <div><span className="section-label">YOUR FOCUS CAMERA</span><h2>What Fido sees.</h2></div>
      <button className="secondary-button" onClick={() => visible ? setVisible(false) : show()}>
        <Camera size={16} aria-hidden="true"/>{visible ? "Hide camera" : "Show camera"}
      </button>
    </div>
    <p className="muted">Live from the Python detector on this computer, with its focus overlay. No video is sent to the cloud.</p>
    {visible && (failed
      ? <div className="privacy-note" role="status"><span>Camera preview unavailable. Start the detector on this computer with <code>python -m detector.agent --server http://localhost:5001</code>. Then <button className="link-button" onClick={show}>retry preview</button>. An HTTPS website may block this local HTTP feed; use the localhost website.</span></div>
      // An MJPEG feed must use a native img, not Next's image optimizer.
      // eslint-disable-next-line @next/next/no-img-element
      : <img key={attempt} className="detector-preview-feed" src={`${url}${url.includes("?") ? "&" : "?"}attempt=${attempt}`} alt="Live detector camera with focus status overlay" onError={() => setFailed(true)}/>)}
    <p className="muted">Hiding the preview does not stop detection. Stop the detector with Ctrl+C to turn the camera off. On another phone, localhost refers to that phone—not the laptop.</p>
  </section>;
}
