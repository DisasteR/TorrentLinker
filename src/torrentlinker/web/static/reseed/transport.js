"use strict";
// Only the owner reseeding API is available to this bundled view.
window.reseedTransport = new Promise((resolve) => {
  const pending = new Map();
  let sequence = 0;
  if (location.protocol === "file:") {
    const script = document.createElement("script");
    script.src = "qrc:///qtwebchannel/qwebchannel.js";
    script.onload = () =>
      new QWebChannel(qt.webChannelTransport, (channel) => {
        const bridge = channel.objects.reseed;
        bridge.environment.connect((value) =>
          window.dispatchEvent(
            new CustomEvent("reseed-environment", {
              detail: JSON.parse(value),
            }),
          ),
        );
        window.reseedReady = () => bridge.ready();
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
      if (message?.type === "reseed-environment") {
        window.dispatchEvent(
          new CustomEvent(message.type, { detail: message.value }),
        );
      }
      if (message?.type !== "reseed-response" || !pending.has(message.id))
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
            { type: "reseed-request", id, path, body },
            location.origin,
          );
        }),
    );
    window.reseedReady = () =>
      parent.postMessage({ type: "reseed-ready" }, location.origin);
  }
});
