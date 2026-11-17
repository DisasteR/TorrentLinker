"use strict";
// Only the owner Community API is available to this bundled view.
window.communityTransport = new Promise((resolve) => {
  const pending = new Map();
  let sequence = 0;
  if (location.protocol === "file:") {
    const script = document.createElement("script");
    script.src = "qrc:///qtwebchannel/qwebchannel.js";
    script.onload = () =>
      new QWebChannel(qt.webChannelTransport, (channel) => {
        const bridge = channel.objects.community;
        bridge.environment.connect((value) =>
          window.dispatchEvent(
            new CustomEvent("community-environment", {
              detail: JSON.parse(value),
            }),
          ),
        );
        window.communityReady = () => bridge.ready();
        resolve(
          (path, body) =>
            new Promise((accept, reject) => {
              bridge.request(path, JSON.stringify(body ?? null), (value) => {
                const result = JSON.parse(value);
                result.error
                  ? reject(new Error(result.error))
                  : accept(result.data);
              });
            }),
        );
      });
    document.head.append(script);
  } else {
    window.addEventListener("message", (event) => {
      if (event.source !== parent || event.origin !== location.origin) return;
      const message = event.data;
      if (message?.type === "community-environment") {
        window.dispatchEvent(
          new CustomEvent(message.type, { detail: message.value }),
        );
      }
      if (message?.type !== "community-response" || !pending.has(message.id))
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
            { type: "community-request", id, path, body },
            location.origin,
          );
        }),
    );
    window.communityReady = () => parent.postMessage({ type: "community-ready" }, location.origin);
  }
});
