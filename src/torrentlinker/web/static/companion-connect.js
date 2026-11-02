// Both desktop and WebUI use this same one-click handoff. The one-time code is
// removed from history immediately and never appears in an HTTP URL or log.
const extensionId = "jfnellfifmdbccmgmcfllkgglikdcaia";
const status = document.getElementById("status");
const install = document.getElementById("install");
let payload;
try {
  payload = JSON.parse(decodeURIComponent(location.hash.slice(1)));
} catch {}
history.replaceState(null, "", location.pathname);
let attempts = 0;
async function connect() {
  // Browsers hide the companion from remote HTTP pages, so waiting cannot succeed.
  if (
    location.protocol === "http:" &&
    !["127.0.0.1", "localhost"].includes(location.hostname)
  ) {
    status.textContent =
      "The companion connects to a remote TorrentLinker only over HTTPS. Open TorrentLinker through an HTTPS address (for example with tailscale serve or a reverse proxy), download the companion again from Settings at that address, install it, then click Connect companion.";
    return;
  }
  if (!payload?.code || !payload?.accountId) {
    status.textContent =
      "Return to TorrentLinker → Zen → Profile and click Connect companion.";
    return;
  }
  if (++attempts > 120) {
    status.textContent =
      "Connection link expired. Click Connect companion in TorrentLinker again.";
    return;
  }
  try {
    const reply = await chrome.runtime.sendMessage(extensionId, {
      ...payload,
      action: "connect",
    });
    if (reply?.error) {
      status.textContent = reply.error;
      return;
    }
    status.textContent =
      "Connected. Your profile updates automatically. You can close this tab and return to TorrentLinker.";
    payload = null;
    install.hidden = true;
  } catch {
    status.textContent = "Waiting for the companion in this browser.";
    install.hidden = false;
    setTimeout(connect, 5000);
  }
}
connect();
