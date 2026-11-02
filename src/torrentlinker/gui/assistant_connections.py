"""Model connections for the Assistant, shown in Settings → Assistant.

Changes apply at once: each connection is saved through the Assistant service.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from torrentlinker.assistant.providers import PRESETS, SAMPLING_PROVIDERS, Provider
from torrentlinker.assistant.research import SearchSettings
from torrentlinker.gui.controls import set_role
from torrentlinker.gui.tasks import run_in_background


class AssistantConnections(QWidget):
    def __init__(self, get_service, parent=None):
        super().__init__(parent)
        self.get_service = get_service
        self.service: Any = None
        self.provider_data: list[dict] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        info = QLabel(
            self.tr(
                "Hosted providers receive messages and tool results. No automatic "
                "fallback to a hosted model. Changes here apply at once."
            )
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        self.providers = QListWidget()
        self.providers.setAccessibleName(self.tr("Model connections"))
        self.providers.itemDoubleClicked.connect(lambda _item: self.edit_provider())
        layout.addWidget(self.providers, 1)
        row = QHBoxLayout()
        add = set_role(QPushButton(self.tr("Add connection")), "primary")
        add.clicked.connect(lambda: self.provider_editor())
        row.addWidget(add)
        for label, action in [
            (self.tr("Edit"), self.edit_provider),
            (self.tr("Test tools"), self.test_provider),
        ]:
            button = QPushButton(label)
            button.clicked.connect(action)
            row.addWidget(button)
        row.addStretch()
        for label, action in [
            (self.tr("Web research"), self.research_editor),
            (self.tr("Chat defaults"), self.chat_defaults),
        ]:
            button = set_role(QPushButton(label), "quiet")
            button.clicked.connect(action)
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        layout.addWidget(self.status)
        try:
            self.service = get_service()
            self.refresh()
        except Exception as error:
            self._notify(str(error))

    def refresh(self):
        if not self.service:
            return
        self.provider_data = self.service.setup()["providers"]
        selected = self.providers.currentRow()
        self.providers.clear()
        for p in self.provider_data:
            self.providers.addItem(p["name"] + " · " + p["model"])
            assessment = p.get("assessment")
            self.providers.item(self.providers.count() - 1).setToolTip(
                "\n".join(
                    f"{name}: {check['status']}. {check['reason']}"
                    for name, check in (assessment or {}).get("checks", {}).items()
                )
                or self.tr("Capabilities untested")
            )
        self.providers.setCurrentRow(selected if selected >= 0 else 0)

    def _notify(self, text):
        if not isValid(self) or (self.service and self.service.closed):
            return
        self.status.setText(text)

    def error(self, error):
        if not isValid(self) or (self.service and self.service.closed):
            return
        detail = self.service.secrets.redact(str(error)) if self.service else str(error)
        QMessageBox.warning(self, self.tr("Assistant"), detail)

    def chat_defaults(self):
        from torrentlinker.gui.assistant import AssistantEditor

        if not self.service:
            return
        initial = self.service.chat_defaults().model_dump()
        AssistantEditor(self.service, initial=initial, defaults=True, parent=self).exec()

    def edit_provider(self):
        row = self.providers.currentRow()
        if 0 <= row < len(self.provider_data):
            self.provider_editor(self.provider_data[row])

    def provider_editor(self, existing=None):
        if not self.service:
            return
        existing = existing or {}
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Model connection"))
        dialog.resize(600, 600)
        form = QFormLayout(dialog)
        name = QLineEdit(existing.get("name", ""))
        kind = QComboBox()
        kind.addItems(list(PRESETS))
        kind.setCurrentText(existing.get("kind", "ollama"))
        address = QLineEdit(existing.get("url", PRESETS["ollama"]))
        model = QComboBox()
        model.setEditable(True)
        model.setEditText(existing.get("model", ""))
        context_window = QSpinBox()
        context_window.setRange(512, 2000000)
        context_window.setValue(existing.get("context_window", 32768))
        context_auto = QCheckBox(self.tr("Detect context automatically"))
        context_auto.setChecked(existing.get("context_auto", True))
        server_sampling = QCheckBox(self.tr("Use server sampling defaults"))
        server_sampling.setChecked(existing.get("temperature") is None)
        temperature = QDoubleSpinBox()
        temperature.setObjectName("assistantTemperature")
        temperature.setRange(0, 2)
        temperature.setSingleStep(0.1)
        temperature.setValue(existing.get("temperature") or 0)

        def sampling_controls():
            supported = kind.currentText() in SAMPLING_PROVIDERS
            if not supported:
                server_sampling.setChecked(True)
            server_sampling.setEnabled(supported)
            temperature.setEnabled(supported and not server_sampling.isChecked())

        def sampling_value():
            return temperature.value() if temperature.isEnabled() else None

        server_sampling.toggled.connect(sampling_controls)
        kind.currentTextChanged.connect(sampling_controls)
        sampling_controls()
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText(self.tr("Leave blank to keep the saved key"))
        for label, widget in [
            (self.tr("Name"), name),
            (self.tr("Provider"), kind),
            (self.tr("Base URL"), address),
            (self.tr("Model ID"), model),
            ("", context_auto),
            (self.tr("Context window (tokens)"), context_window),
            ("", server_sampling),
            (self.tr("Temperature (0 = least variation)"), temperature),
            (self.tr("API key"), key),
        ]:
            form.addRow(label, widget)
        context_hint = QLabel(
            self.tr(
                "Automatic detection runs before each task. The value above is used "
                "when the server does not report its limit. Disable detection to use it manually."
            )
        )
        context_hint.setWordWrap(True)
        form.addRow(context_hint)
        sampling_hint = QLabel(
            self.tr(
                "Sampling overrides are available for Ollama, LM Studio and compatible endpoints. "
                "Lower variation does not guarantee correct answers or actions."
            )
        )
        sampling_hint.setWordWrap(True)
        form.addRow(sampling_hint)
        context_status = QLabel()
        context_status.setWordWrap(True)
        form.addRow(context_status)
        check_context = QPushButton(self.tr("Check context"))
        form.addRow(check_context)

        def context_selection():
            return (
                kind.currentText(),
                address.text(),
                model.currentText(),
                context_auto.isChecked(),
                context_window.value(),
                key.text(),
            )

        def check():
            try:
                selection = context_selection()
                connection = Provider(
                    name=name.text() or "Discovery",
                    kind=selection[0],
                    url=selection[1],
                    model=selection[2],
                    context_auto=selection[3],
                    context_window=selection[4],
                    api_key=selection[5] or None,
                )
                check_context.setEnabled(False)
                context_status.setText(self.tr("Checking server context…"))

                def done(result):
                    from shiboken6 import isValid

                    if not isValid(dialog) or not dialog.isVisible():
                        return
                    check_context.setEnabled(True)
                    if context_selection() != selection:
                        return
                    label = {
                        "server": self.tr("Server context: %1 tokens"),
                        "provider": self.tr("Provider context: %1 tokens"),
                        "fallback": self.tr("Not reported. Fallback: %1 tokens"),
                        "manual": self.tr("Manual context: %1 tokens"),
                    }[result["source"]]
                    text = label.replace("%1", str(result["context_window"]))
                    if result.get("served_models"):
                        text += " " + self.tr(
                            "The server does not list this model. It serves: %1"
                        ).replace("%1", ", ".join(result["served_models"]))
                    context_status.setText(text)

                def failed(error):
                    from shiboken6 import isValid

                    if isValid(dialog) and dialog.isVisible():
                        check_context.setEnabled(True)
                        self.error(error)

                run_in_background(
                    lambda: self.service.context_preview(connection, existing.get("id", "")),
                    done,
                    failed,
                )
            except Exception as error:
                self.error(error)

        check_context.clicked.connect(check)
        for field in (address, key):
            field.textChanged.connect(context_status.clear)
        model.currentTextChanged.connect(context_status.clear)
        context_auto.toggled.connect(context_status.clear)
        context_window.valueChanged.connect(context_status.clear)
        kind.currentTextChanged.connect(lambda value: address.setText(PRESETS[value]))
        hint = QLabel(
            self.tr(
                "Use an address reachable by TorrentLinker. In Docker, localhost "
                "is the container. Changing the address clears the saved key "
                "unless you enter a new one."
            )
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        fetch = QPushButton(self.tr("List models"))

        def discover():
            try:
                connection = Provider.model_validate(
                    dict(
                        name=name.text() or "Discovery",
                        kind=kind.currentText(),
                        url=address.text(),
                        model=model.currentText() or "discovery",
                    )
                )
                credential = key.text()
                if not credential and existing and connection.url == existing.get("url"):
                    credential = self.service.secrets.get("assistant:" + existing["id"])
                run_in_background(
                    lambda: self.service.models.models(connection, credential),
                    lambda values: model.addItems(values),
                    self.error,
                )
            except Exception as error:
                self.error(error)

        fetch.clicked.connect(discover)
        form.addRow(fetch)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)

        def save():
            try:
                p = Provider.model_validate(
                    dict(
                        name=name.text(),
                        kind=kind.currentText(),
                        url=address.text(),
                        model=model.currentText(),
                        api_key=key.text() or None,
                        context_window=context_window.value(),
                        context_auto=context_auto.isChecked(),
                        temperature=sampling_value(),
                    )
                )
                saved = self.service.save_provider(p, existing.get("id", ""))
                dialog.accept()
                self.refresh()
                if not existing or any(
                    existing.get(k) != getattr(p, k)
                    for k in ("kind", "url", "model", "temperature")
                ):
                    self._notify(self.tr("Assessing model capabilities…"))
                    run_in_background(
                        lambda: self.service.test_provider(saved["id"]),
                        lambda result: self._notify(
                            "\n".join(
                                f"{name}: {check['status']}. {check['reason']}"
                                for name, check in result["checks"].items()
                            )
                        ),
                        self.error,
                    )
            except Exception as error:
                self.error(error)

        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def test_provider(self):
        row = self.providers.currentRow()
        if 0 <= row < len(self.provider_data):
            identifier = self.provider_data[row]["id"]
            self._notify(self.tr("Assessing model capabilities…"))
            run_in_background(
                lambda: self.service.test_provider(identifier),
                lambda result: self._notify(
                    "\n".join(
                        f"{name}: {check['status']}. {check['reason']}"
                        for name, check in result.get("checks", {}).items()
                    )
                ),
                self.error,
            )

    def research_editor(self):
        if not self.service:
            return
        current = self.service.research_settings()
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Web research"))
        form = QFormLayout(dialog)
        hint = QLabel(
            self.tr(
                "Use Brave Search or SearXNG for search. Page reading and book "
                "metadata need no search key. Film metadata uses your TMDb key."
            )
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        url = QLineEdit(current.get("searxng_url", ""))
        key = QLineEdit()
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText(self.tr("Leave blank to keep the saved key"))
        form.addRow("SearXNG URL", url)
        form.addRow("Brave Search API key", key)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            try:
                self.service.save_research(
                    SearchSettings(searxng_url=url.text(), brave_key=key.text() or None)
                )
                dialog.accept()
            except Exception as error:
                self.error(error)

        buttons.accepted.connect(save)
        dialog.exec()
