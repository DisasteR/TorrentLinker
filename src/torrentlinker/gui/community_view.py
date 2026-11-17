"""The approved Community workspace, shared with the Web UI.

Qt renders only bundled assets. The channel exposes the owner Community service;
no browser session, arbitrary URL, filesystem or general API is exposed to JS.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QHideEvent, QShowEvent
from PySide6.QtWidgets import QFileDialog, QVBoxLayout, QWidget

from torrentlinker.community import message_library
from torrentlinker.profile_workspace import ProfileWorkspace

if TYPE_CHECKING:
    from PySide6.QtWebEngineWidgets import QWebEngineView

ASSETS = Path(__file__).parents[1] / "web" / "static" / "community"


class CommunityBridge(QObject):
    environment = Signal(str)

    def __init__(self, workspace: ProfileWorkspace, parent: QObject):
        super().__init__(parent)
        self.workspace = workspace
        self.environment_value = "{}"

    @Slot()
    def ready(self) -> None:
        self.environment.emit(self.environment_value)

    @Slot(str, str, result=str)
    def request(self, path: str, payload: str) -> str:
        try:
            body = json.loads(payload)
            if path == "community":
                result = self.workspace.community_state()
            elif path == "community/messages":
                result = {"entries": message_library()}
            elif path == "community/export" and isinstance(body, dict):
                text = body.get("text")
                if not isinstance(text, str) or len(text) > 10_000_000:
                    raise ValueError("Invalid history export.")
                filename, _ = QFileDialog.getSaveFileName(
                    None,
                    self.tr("Export community history"),
                    "torrentlinker-community-history.json",
                    "JSON (*.json)",
                )
                if filename:
                    Path(filename).write_text(text, encoding="utf-8")
                result = {"saved": bool(filename)}
            elif path in {
                "community/settings",
                "community/refresh",
                "community/save",
                "community/control",
            } and isinstance(body, dict):
                result = self.workspace.community_update(path.split("/")[1], body)
            else:
                raise ValueError("Unknown community operation.")
            return json.dumps({"data": result}, ensure_ascii=False)
        except (ValueError, TypeError, KeyError, OSError) as error:
            return json.dumps({"error": str(error)})


class CommunityView(QWidget):
    def __init__(self, workspace: ProfileWorkspace, language: str = "en"):
        super().__init__()
        self.workspace = workspace
        self.language = language
        self.bridge = CommunityBridge(workspace, self)
        self.web: QWebEngineView | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def _load_view(self) -> None:
        # Load Chromium only when the Community tab is opened.
        from PySide6.QtWebChannel import QWebChannel
        from PySide6.QtWebEngineCore import (
            QWebEnginePage,
            QWebEngineSettings,
            QWebEngineUrlRequestInfo,
            QWebEngineUrlRequestInterceptor,
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView

        class LocalAssets(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
                url = info.requestUrl()
                allowed = (
                    url.toString() == "qrc:///qtwebchannel/qwebchannel.js"
                    or url.isLocalFile()
                    and Path(url.toLocalFile()).resolve()
                    in {
                        ASSETS / name
                        for name in ("view.html", "view.css", "view.js", "transport.js")
                    }
                )
                info.block(not allowed)

        class LocalPage(QWebEnginePage):
            def acceptNavigationRequest(
                self,
                url: QUrl | str,
                navigation_type: QWebEnginePage.NavigationType,
                is_main_frame: bool,
            ) -> bool:
                target = QUrl(url)
                if (
                    navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
                    and target.scheme() == "https"
                    and target.host() == "c411.org"
                    and re.fullmatch(r"/torrents/[a-fA-F0-9]{40}", target.path())
                    and not target.hasQuery()
                    and not target.hasFragment()
                ):
                    QDesktopServices.openUrl(target)
                    return False
                return (
                    target.isLocalFile()
                    and Path(target.toLocalFile()).resolve() == ASSETS / "view.html"
                )

        self.web = QWebEngineView(self)
        page = LocalPage(self.web)
        self.interceptor = LocalAssets(page)
        page.setUrlRequestInterceptor(self.interceptor)
        self.web.setPage(page)
        settings = page.settings()
        for attribute in (
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard,
            QWebEngineSettings.WebAttribute.LocalStorageEnabled,
        ):
            settings.setAttribute(attribute, False)
        self.channel = QWebChannel(page)
        self.channel.registerObject("community", self.bridge)
        page.setWebChannel(self.channel)
        self.web.loadFinished.connect(self.refresh)
        self._layout.addWidget(self.web)
        self.web.setUrl(QUrl.fromLocalFile(str(ASSETS / "view.html")))

    def refresh(self) -> None:
        self.bridge.environment_value = json.dumps(
            {
                "language": self.language,
                "theme": "dark" if self.palette().window().color().lightness() < 128 else "light",
                "active": self.isVisible(),
            }
        )
        self.bridge.ready()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.web is None:
            self._load_view()
        self.refresh()

    def closeEvent(self, event: QCloseEvent) -> None:
        # pytest and shutdown can stop the event loop before deferred widget
        # destruction. Release the page before Qt destroys its shared profile.
        if self.web is not None:
            web = self.web
            self.web = None
            web.deleteLater()
            QCoreApplication.sendPostedEvents(web, QEvent.Type.DeferredDelete)
        super().closeEvent(event)

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self.refresh()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "bridge"):
            self.refresh()
