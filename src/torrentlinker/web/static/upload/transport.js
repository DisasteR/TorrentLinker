"use strict";
// Only the owner uploading API is available to this bundled view.
window.uploadTransport = new Promise((resolve) => {
  const pending = new Map();
  let sequence = 0;
  if (location.protocol === "file:") {
    const script = document.createElement("script");
    script.src = "qrc:///qtwebchannel/qwebchannel.js";
    script.onload = () =>
      new QWebChannel(qt.webChannelTransport, (channel) => {
        const bridge = channel.objects.upload;
        bridge.environment.connect((value) =>
          window.dispatchEvent(
            new CustomEvent("upload-environment", {
              detail: JSON.parse(value),
            }),
          ),
        );
        window.uploadReady = () => bridge.ready();
        bridge.response.connect((id, value) => {
          if (!pending.has(id)) return;
          const [accept, reject] = pending.get(id);
          pending.delete(id);
          const result = JSON.parse(value);
          result.error ? reject(new Error(result.error)) : accept(result.data);
        });
        resolve(
          (path, body) =>
            new Promise((accept, reject) => {
              const id = ++sequence;
              pending.set(id, [accept, reject]);
              bridge.requestAsync(id, path, JSON.stringify(body ?? {}));
            }),
        );
      });
    document.head.append(script);
  } else {
    window.addEventListener("message", (event) => {
      if (event.source !== parent || event.origin !== location.origin) return;
      const message = event.data;
      if (message?.type === "upload-environment") {
        window.dispatchEvent(
          new CustomEvent(message.type, { detail: message.value }),
        );
      }
      if (message?.type !== "upload-response" || !pending.has(message.id))
        return;
      const [accept, reject] = pending.get(message.id);
      pending.delete(message.id);
      message.error ? reject(new Error(message.error)) : accept(message.value);
    });
    resolve(
      (path, body) =>
        new Promise((accept, reject) => {
          const id = ++sequence;
          pending.set(id, [accept, reject]);
          parent.postMessage(
            { type: "upload-request", id, path, body },
            location.origin,
          );
        }),
    );
    window.uploadReady = () =>
      parent.postMessage({ type: "upload-ready" }, location.origin);
  }
});
