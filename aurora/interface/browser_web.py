"""
Qt WebEngine for the AURORA Browser: the profile, the request
interceptor, and web views that open new tabs.

Imported only when the Browser page is first opened, so Chromium (and its
memory) is started only if you use the browser. Tests use a fake factory
instead (Tests/test_browser.py); nothing here is needed to test the rest.
"""

import logging

from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtWebEngineCore import (
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from aurora.config import settings

logger = logging.getLogger(__name__)


class Interceptor(QWebEngineUrlRequestInterceptor):
    """Asks BrowserNetwork about every request (on Chromium's IO thread)."""

    def __init__(self, network, parent=None):
        super().__init__(parent)
        self.network = network

    def interceptRequest(self, info):
        try:
            decision = self.network.decide(info.requestUrl().toString(), info.firstPartyUrl().toString(),
                                           bytes(info.requestMethod()).decode("ascii", "replace"))
            if not decision.allowed:
                info.block(True)
        except Exception:                      # never let a request through because of an error
            logger.exception("Browser request check failed")
            info.block(True)


class Page(QWebEnginePage):
    """Refuses camera, microphone, location, notifications, screen sharing and
    the like (sites ask for them; the AURORA Browser doesn't grant them)."""

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        if hasattr(self, "permissionRequested"):
            self.permissionRequested.connect(self._refuse)
        page_settings = self.settings()
        Attribute = QWebEngineSettings.WebAttribute
        page_settings.setAttribute(Attribute.PluginsEnabled, True)          # the built-in PDF viewer
        page_settings.setAttribute(Attribute.PdfViewerEnabled, True)
        page_settings.setAttribute(Attribute.LocalContentCanAccessRemoteUrls, False)
        page_settings.setAttribute(Attribute.LocalContentCanAccessFileUrls, False)
        page_settings.setAttribute(Attribute.DnsPrefetchEnabled, False)
        page_settings.setAttribute(Attribute.ScreenCaptureEnabled, False)

    def _refuse(self, permission):
        logger.info("Browser: a site asked for a permission; refused")
        permission.deny()
        self.refused = True


class View(QWebEngineView):
    """A tab's web view. Links that open a new window open a new tab instead."""

    def __init__(self, factory, open_tab, parent=None):
        super().__init__(parent)
        self.factory = factory
        self.open_tab = open_tab
        self.setPage(Page(factory.profile, self))

    def createWindow(self, _type):
        view = self.open_tab(None)
        return view


class WebFactory:
    """Creates the browser profile once, and a view per tab."""

    def __init__(self, network):
        self.network = network
        profile_dir = settings.BROWSER_PROFILE_DIR
        profile_dir.mkdir(parents=True, exist_ok=True)
        # The profile outlives every page (its parent is the application), as Qt requires.
        self.profile = QWebEngineProfile("aurora-browser", QCoreApplication.instance())
        self.profile.setPersistentStoragePath(str(profile_dir / "storage"))
        self.profile.setCachePath(str(profile_dir / "cache"))
        self.profile.setHttpCacheMaximumSize(100 * 1024 * 1024)
        self.profile.setSpellCheckEnabled(False)          # no dictionary downloads
        self.interceptor = Interceptor(network, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)

    def view(self, open_tab, parent=None) -> View:
        return View(self, open_tab, parent)

    def clear_site_data(self) -> None:
        """Deletes cookies, cache and site storage of the AURORA Browser only."""
        self.profile.cookieStore().deleteAllCookies()
        self.profile.clearHttpCache()
        self.profile.clearAllVisitedLinks()

    @staticmethod
    def url(text: str) -> QUrl:
        return QUrl(text)
