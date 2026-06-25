from __future__ import annotations

from pathlib import PurePosixPath

from Qt.QtCore import QObject, QSize, Qt, Signal
from Qt.QtGui import QFontMetrics
from Qt.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableView,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..actions import (
    ACTION_FIND_IN_TREE,
    ACTION_OPEN_HALF_MAPS,
    ACTION_OPEN_LAST_COMPLETED,
    ACTION_OPEN_LATEST_REFINE,
    ACTION_OPEN_POSTPROCESS_MODEL,
    ACTION_OPEN_SELECTED,
    ACTION_REFRESH,
    ACTION_REFRESH_PIPELINE,
    ACTION_REVEAL_RELATED,
    compute_action_availability,
)
from ..models import PreviewResult, RelionProjectIndex
from ..opening import is_openable_path
from ..ssh_config import resolve_host
from .assets import load_icon, load_pixmap
from .pipeline_table_model import RelionPipelineTableModel
from .pipeline_view import RelionPipelineFlowchart


class InteractivePromptDialog(QDialog):
    def __init__(self, parent, title: str, instructions: str, prompts: list[tuple[str, bool]]):
        super().__init__(parent)
        self.setWindowTitle(title or "SSH Prompt")
        self._inputs: list[QLineEdit] = []

        layout = QVBoxLayout(self)
        if instructions:
            layout.addWidget(QLabel(instructions))

        form = QFormLayout()
        for prompt, echoed in prompts:
            edit = QLineEdit()
            if not echoed:
                edit.setEchoMode(QLineEdit.Password)
            form.addRow(QLabel(prompt), edit)
            self._inputs.append(edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def prompt(cls, parent, title: str, instructions: str, prompts: list[tuple[str, bool]]) -> list[str]:
        dialog = cls(parent, title, instructions, prompts)
        if dialog.exec() != QDialog.Accepted:
            raise RuntimeError("Authentication prompt cancelled.")
        return [widget.text() for widget in dialog._inputs]


class MainWidget(QWidget):
    connect_requested = Signal(dict)
    disconnect_requested = Signal()
    refresh_requested = Signal()
    refresh_pipeline_requested = Signal()
    clear_cache_requested = Signal()
    preview_requested = Signal(object)
    pipeline_job_selected = Signal(object)
    open_selected_requested = Signal()
    open_latest_refine_requested = Signal()
    open_last_completed_requested = Signal()
    open_half_maps_requested = Signal()
    open_postprocess_requested = Signal()
    reveal_related_requested = Signal()
    find_in_tree_requested = Signal()
    browse_path_requested = Signal(str)
    browse_up_requested = Signal()
    set_directory_requested = Signal()
    connection_page_requested = Signal()

    def __init__(
        self,
        aliases: list[str],
        preferred_alias: str = "",
        preferred_host: str = "",
        preferred_user: str = "",
        preferred_port: int = 22,
        preferred_root: str = "/",
        remembered_roots_by_alias: dict[str, str] | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._model = None
        self._project: RelionProjectIndex | None = None
        self._syncing_pipeline_selection = False
        self._preferred_root = preferred_root
        self._remembered_roots_by_alias = dict(remembered_roots_by_alias or {})
        self._root_source = "preferred"
        self._root_user_edited = False
        self._build(
            aliases,
            preferred_alias,
            preferred_host,
            preferred_user,
            preferred_port,
            preferred_root,
        )
        self._connect()
        if preferred_alias:
            self._apply_alias_defaults(preferred_alias)

    def _build(
        self,
        aliases: list[str],
        preferred_alias: str,
        preferred_host: str,
        preferred_user: str,
        preferred_port: int,
        preferred_root: str,
    ):
        self.setLayout(QVBoxLayout())
        self.page_stack = QStackedWidget()

        self.connection_page = QWidget()
        connection_page_layout = QVBoxLayout(self.connection_page)
        self.brand_mark_label = QLabel()
        self.brand_wordmark_label = QLabel("CryoRemote")
        self.brand_wordmark_label.setStyleSheet("font-size: 20px; font-weight: 600; color: #1f3e5d;")
        self._build_connection_header(connection_page_layout)

        connect_box = QGroupBox("Connection")
        connect_layout = QGridLayout(connect_box)

        self.alias_combo = QComboBox()
        self.alias_combo.addItem("")
        self.alias_combo.addItems(aliases)
        if preferred_alias:
            self.alias_combo.setCurrentText(preferred_alias)
        self.host_edit = QLineEdit(preferred_host)
        self.user_edit = QLineEdit(preferred_user)
        self.port_edit = QLineEdit(str(preferred_port))
        self.root_edit = QLineEdit(preferred_root)
        self.root_edit.setMinimumWidth(520)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)

        self.connect_button = QPushButton("Connect")
        self.disconnect_button = QPushButton("Disconnect")
        self.refresh_button = QPushButton("Refresh All")
        self.clear_cache_button = QPushButton("Clear Cache")
        self.browse_disconnect_button = QPushButton("Disconnect")

        connect_layout.addWidget(QLabel("Alias"), 0, 0)
        connect_layout.addWidget(self.alias_combo, 0, 1)
        connect_layout.addWidget(QLabel("Host"), 0, 2)
        connect_layout.addWidget(self.host_edit, 0, 3)
        connect_layout.addWidget(QLabel("User"), 1, 0)
        connect_layout.addWidget(self.user_edit, 1, 1)
        connect_layout.addWidget(QLabel("Port"), 1, 2)
        connect_layout.addWidget(self.port_edit, 1, 3)
        connect_layout.addWidget(QLabel("Root"), 2, 0)
        connect_layout.addWidget(self.root_edit, 2, 1, 1, 3)
        connect_layout.addWidget(QLabel("Password"), 3, 0)
        connect_layout.addWidget(self.password_edit, 3, 1)
        connect_layout.addWidget(self.connect_button, 3, 2)
        connect_layout.addWidget(self.disconnect_button, 3, 3)
        connect_layout.addWidget(self.refresh_button, 4, 2)
        connect_layout.addWidget(self.clear_cache_button, 4, 3)
        connect_layout.setColumnStretch(1, 2)
        connect_layout.setColumnStretch(3, 3)

        connection_page_layout.addWidget(connect_box)
        connection_page_layout.addStretch(1)

        self.browse_page = QWidget()
        browse_page_layout = QVBoxLayout(self.browse_page)
        browse_page_layout.setContentsMargins(0, 0, 0, 0)
        browse_page_layout.setSpacing(8)
        browse_page_layout.addWidget(self._build_session_bar())

        tree_and_details_splitter = QSplitter(Qt.Vertical)
        self.tree = QTreeView()
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionBehavior(QTreeView.SelectRows)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.setMinimumHeight(220)
        self._configure_tree_header()
        tree_and_details_splitter.addWidget(self.tree)

        bottom_splitter = QSplitter(Qt.Horizontal)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        self.project_label = QLabel("No RELION project detected.")
        self.project_label.setWordWrap(True)
        self.project_label.setAlignment(Qt.AlignCenter)
        center_layout.addWidget(self.project_label)

        self.empty_state_label = QLabel()
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        self.empty_state_label.setMinimumHeight(240)
        center_layout.addWidget(self.empty_state_label)

        self.pipeline_tabs = QTabWidget()
        self.flowchart_view = RelionPipelineFlowchart()
        self.flowchart_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pipeline_tabs.addTab(self.flowchart_view, "Flowchart")

        self.pipeline_table = QTableView()
        self.pipeline_table_model = RelionPipelineTableModel(self.pipeline_table)
        self.pipeline_table.setModel(self.pipeline_table_model)
        self.pipeline_table.setSelectionBehavior(QTableView.SelectRows)
        self.pipeline_table.setSelectionMode(QTableView.SingleSelection)
        self.pipeline_table.horizontalHeader().setStretchLastSection(True)
        self.pipeline_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pipeline_tabs.addTab(self.pipeline_table, "Jobs")
        center_layout.addWidget(self.pipeline_tabs)

        self.project_buttons_widget = QWidget()
        project_buttons = QHBoxLayout(self.project_buttons_widget)
        project_buttons.setContentsMargins(0, 0, 0, 0)
        self.refresh_pipeline_button = QPushButton("Refresh Pipeline")
        self.last_job_button = QPushButton("Open Last Completed Job")
        self.find_in_tree_button = QPushButton("Find In Tree")
        project_buttons.addWidget(self.refresh_pipeline_button)
        project_buttons.addWidget(self.last_job_button)
        project_buttons.addWidget(self.find_in_tree_button)
        center_layout.addWidget(self.project_buttons_widget)
        bottom_splitter.addWidget(center)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        self.path_label = QLabel("No selection")
        self.path_label.setWordWrap(True)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.related_list = QListWidget()

        buttons_row_1 = QHBoxLayout()
        self.open_button = QPushButton("Open Selected")
        self.latest_button = QPushButton("Open Latest Refine Map")
        buttons_row_1.addWidget(self.open_button)
        buttons_row_1.addWidget(self.latest_button)

        buttons_row_2 = QHBoxLayout()
        self.half_button = QPushButton("Open Half Maps")
        self.postprocess_button = QPushButton("Open PostProcess + Model")
        buttons_row_2.addWidget(self.half_button)
        buttons_row_2.addWidget(self.postprocess_button)

        buttons_row_3 = QHBoxLayout()
        self.related_button = QPushButton("Reveal Related Files")
        buttons_row_3.addWidget(self.related_button)

        side_layout.addWidget(self.path_label)
        side_layout.addWidget(self.preview)
        side_layout.addWidget(QLabel("Related Files"))
        side_layout.addWidget(self.related_list)
        side_layout.addLayout(buttons_row_1)
        side_layout.addLayout(buttons_row_2)
        side_layout.addLayout(buttons_row_3)
        bottom_splitter.addWidget(side)

        bottom_splitter.setStretchFactor(0, 4)
        bottom_splitter.setStretchFactor(1, 3)
        tree_and_details_splitter.addWidget(bottom_splitter)
        tree_and_details_splitter.setStretchFactor(0, 5)
        tree_and_details_splitter.setStretchFactor(1, 4)
        browse_page_layout.addWidget(tree_and_details_splitter)

        self.page_stack.addWidget(self.connection_page)
        self.page_stack.addWidget(self.browse_page)
        self.layout().addWidget(self.page_stack)
        self.status_label = QLabel("Disconnected.")
        self.layout().addWidget(self.status_label)

        self._apply_static_images()
        self._apply_icons()
        self.set_connected(False)
        self.clear_project()
        self.show_connection_page()

    def _connect(self):
        self.connect_button.clicked.connect(self._emit_connect_request)
        self.disconnect_button.clicked.connect(lambda *_args: self.disconnect_requested.emit())
        self.browse_disconnect_button.clicked.connect(lambda *_args: self.disconnect_requested.emit())
        self.refresh_button.clicked.connect(lambda *_args: self.refresh_requested.emit())
        self.refresh_pipeline_button.clicked.connect(lambda *_args: self.refresh_pipeline_requested.emit())
        self.clear_cache_button.clicked.connect(lambda *_args: self.clear_cache_requested.emit())
        self.open_button.clicked.connect(lambda *_args: self.open_selected_requested.emit())
        self.latest_button.clicked.connect(lambda *_args: self.open_latest_refine_requested.emit())
        self.last_job_button.clicked.connect(lambda *_args: self.open_last_completed_requested.emit())
        self.half_button.clicked.connect(lambda *_args: self.open_half_maps_requested.emit())
        self.postprocess_button.clicked.connect(lambda *_args: self.open_postprocess_requested.emit())
        self.related_button.clicked.connect(lambda *_args: self.reveal_related_requested.emit())
        self.find_in_tree_button.clicked.connect(lambda *_args: self.find_in_tree_requested.emit())
        self.alias_combo.currentTextChanged.connect(self._alias_selected)
        self.root_edit.textEdited.connect(self._root_text_edited)
        self.path_go_button.clicked.connect(lambda *_args: self.browse_path_requested.emit(self.path_edit.text()))
        self.path_up_button.clicked.connect(lambda *_args: self.browse_up_requested.emit())
        self.set_directory_button.clicked.connect(lambda *_args: self.set_directory_requested.emit())
        self.connection_page_button.clicked.connect(lambda *_args: self.connection_page_requested.emit())
        self.path_edit.returnPressed.connect(lambda: self.browse_path_requested.emit(self.path_edit.text()))
        self.flowchart_view.job_selected.connect(self._flowchart_job_selected)
        self.pipeline_table.selectionModel().currentRowChanged.connect(self._table_row_changed)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.pipeline_table.customContextMenuRequested.connect(self._show_pipeline_context_menu)
        self.flowchart_view.customContextMenuRequested.connect(self._show_flowchart_context_menu)

    def _alias_selected(self, alias: str):
        self._apply_alias_defaults(alias)

    def _apply_alias_defaults(self, alias: str):
        alias = (alias or "").strip()
        if not alias:
            return
        try:
            config = resolve_host(alias)
        except Exception:
            return
        self.host_edit.setText(config.hostname)
        self.user_edit.setText(config.user or "")
        self.port_edit.setText(str(config.port))
        if not self._root_user_edited:
            remembered_root = self._remembered_roots_by_alias.get(alias)
            if remembered_root:
                self.set_root_value(remembered_root, source="remembered")
            else:
                self.set_root_value(self._preferred_root or "/", source="preferred")

    def _emit_connect_request(self):
        self.connect_requested.emit(
            {
                "alias": self.alias_combo.currentText(),
                "host": self.host_edit.text(),
                "user": self.user_edit.text(),
                "port": self.port_edit.text(),
                "root": self.root_edit.text(),
                "root_source": self._root_source,
                "password": self.password_edit.text(),
            }
        )

    def current_root_source(self) -> str:
        return self._root_source

    def show_connection_page(self):
        self.page_stack.setCurrentWidget(self.connection_page)
        self.alias_combo.setFocus(Qt.OtherFocusReason)

    def show_browse_page(self):
        self.page_stack.setCurrentWidget(self.browse_page)
        self.path_edit.setFocus(Qt.OtherFocusReason)

    def set_session_target(self, text: str):
        self.session_target_label.setToolTip(text)
        metrics = QFontMetrics(self.session_target_label.font())
        available_width = max(self.session_target_label.maximumWidth() - 12, 120)
        self.session_target_label.setText(metrics.elidedText(text, Qt.ElideMiddle, available_width))

    def set_current_path(self, path: str):
        self.path_edit.setText(path)

    def set_root_value(self, root: str, *, source: str):
        self.root_edit.setText(root)
        self._root_source = source
        self._root_user_edited = False

    def set_remembered_root(self, alias: str, root: str):
        alias = (alias or "").strip()
        if not alias:
            return
        self._remembered_roots_by_alias[alias] = root

    def _root_text_edited(self, _text: str):
        self._root_source = "manual"
        self._root_user_edited = True

    def install_model(self, model):
        self._model = model
        self.tree.setModel(model)
        self._configure_tree_header()
        self.tree.selectionModel().selectionChanged.connect(self._selection_changed)
        self.tree.selectionModel().currentChanged.connect(self._tree_current_changed)
        self.tree.expandToDepth(0)

    def clear_model(self):
        self.tree.setModel(None)
        self._model = None
        self.related_list.clear()
        self.preview.setPlainText("")
        self.path_label.setText("No selection")
        self._update_action_state()

    def current_entry(self):
        if self._model is None:
            return None
        index = self._tree_active_index()
        return self._model.entry_from_index(index) if index.isValid() else None

    def current_job_id(self) -> str | None:
        return self.flowchart_view.current_job_id()

    def update_preview(self, preview: PreviewResult):
        self.path_label.setText(preview.title)
        self.preview.setPlainText(preview.body)
        self.related_list.clear()
        for note in preview.notes:
            QListWidgetItem(f"[note] {note}", self.related_list)
        for path in preview.related_files:
            QListWidgetItem(str(path), self.related_list)

    def set_connected(self, connected: bool):
        self.disconnect_button.setEnabled(connected)
        self.browse_disconnect_button.setEnabled(connected)
        self.refresh_button.setEnabled(connected)
        self.clear_cache_button.setEnabled(connected)
        self.path_edit.setEnabled(connected)
        self.path_go_button.setEnabled(connected)
        self.path_up_button.setEnabled(connected)
        self.set_directory_button.setEnabled(False)
        self.connection_page_button.setEnabled(True)
        self._update_action_state(connected=connected)

    def set_project(self, project: RelionProjectIndex | None, *, preferred_job_id: str | None = None):
        self._project = project
        self.pipeline_table_model.set_project(project)
        self.flowchart_view.set_project(project)
        if project is None:
            self.project_label.setText("No RELION project detected.")
            self.empty_state_label.show()
            self.pipeline_tabs.hide()
            self.project_buttons_widget.hide()
            self._update_action_state()
            return

        summary = f"Project root: {project.root} | Jobs: {len(project.jobs)} | Source: {project.source}"
        self.project_label.setText(summary)
        self.empty_state_label.hide()
        self.pipeline_tabs.show()
        self.project_buttons_widget.show()
        default_job_id = preferred_job_id if project.job_by_id(preferred_job_id) else (project.jobs[0].job_id if project.jobs else None)
        self.select_job(default_job_id, emit=False)
        self._update_action_state()

    def clear_project(self):
        self._project = None
        self.project_label.setText("No RELION project detected.")
        self.pipeline_table_model.set_project(None)
        self.flowchart_view.set_project(None)
        self.empty_state_label.show()
        self.pipeline_tabs.hide()
        self.project_buttons_widget.hide()
        self._update_action_state()

    def select_job(self, job_id: str | None, *, emit: bool):
        if job_id is None:
            self._syncing_pipeline_selection = True
            self.pipeline_table.clearSelection()
            self.flowchart_view.set_current_job(None, emit=False)
            self._syncing_pipeline_selection = False
            if emit:
                self.pipeline_job_selected.emit(None)
            self._update_action_state()
            return

        self._syncing_pipeline_selection = True
        row = self.pipeline_table_model.row_for_job(job_id)
        if row >= 0:
            index = self.pipeline_table_model.index(row, 0)
            self.pipeline_table.setCurrentIndex(index)
            self.pipeline_table.selectRow(row)
        self.flowchart_view.set_current_job(job_id, emit=False)
        self._syncing_pipeline_selection = False
        if emit:
            self.pipeline_job_selected.emit(job_id)
        self._update_action_state()

    def select_tree_path(self, path: PurePosixPath):
        if self._model is None:
            return
        index = self._model.index_for_path(path)
        if not index.isValid():
            return
        parent = index.parent()
        while parent.isValid():
            self.tree.expand(parent)
            parent = parent.parent()
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index, QTreeView.PositionAtCenter)

    def show_status(self, message: str, *, warning: bool = False, error: bool = False):
        if error:
            color = "#b00020"
        elif warning:
            color = "#8a6d1d"
        else:
            color = "#2f4f4f"
        self.status_label.setText(message)
        self.status_label.setStyleSheet(f"color: {color};")

    def confirm_command_file_open(self, path: PurePosixPath) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Run ChimeraX Command File")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Open and execute {path.name}?")
        box.setInformativeText(
            "CryoRemote will cache the remote command file, rewrite supported open paths, and run the rewritten local script."
        )
        box.setDetailedText(str(path))
        box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        return box.exec() == QMessageBox.Ok

    def refresh_action_state(self):
        self._update_action_state()

    def _build_connection_header(self, parent_layout: QVBoxLayout):
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(10)
        header_layout.addWidget(self.brand_mark_label, 0, Qt.AlignVCenter)
        header_layout.addWidget(self.brand_wordmark_label, 0, Qt.AlignVCenter)
        header_layout.addStretch(1)
        parent_layout.addWidget(header)

    def _build_session_bar(self):
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.session_target_label = QLabel("Not connected")
        self.session_target_label.setMinimumWidth(180)
        self.session_target_label.setMaximumWidth(320)
        self.session_target_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.path_edit = QLineEdit("/")
        self.path_edit.setMinimumWidth(420)
        self.path_edit.setPlaceholderText("/share/scratch/... or relative/path")
        self.path_go_button = QPushButton("Go")
        self.path_up_button = QPushButton("Up")
        self.set_directory_button = QPushButton("Set Dir")
        self.connection_page_button = QPushButton("Connection")

        layout.addWidget(self.session_target_label, 0)
        layout.addWidget(self.path_edit, 1)
        layout.addWidget(self.path_go_button, 0)
        layout.addWidget(self.path_up_button, 0)
        layout.addWidget(self.set_directory_button, 0)
        layout.addWidget(self.browse_disconnect_button, 0)
        layout.addWidget(self.connection_page_button, 0)
        return bar

    def _configure_tree_header(self):
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)

    def _apply_static_images(self):
        mark = load_pixmap("brand/cryoremote-mark-1024.png")
        if mark is not None:
            self.brand_mark_label.setPixmap(mark.scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        wordmark = load_pixmap("brand/cryoremote-wordmark-1536x512.png")
        if wordmark is not None:
            self.brand_wordmark_label.setPixmap(wordmark.scaledToHeight(48, Qt.SmoothTransformation))
            self.brand_wordmark_label.setText("")
        empty_state = load_pixmap("illustrations/empty-state-connect-1600x1200.png")
        if empty_state is not None:
            self.empty_state_label.setPixmap(empty_state.scaledToWidth(320, Qt.SmoothTransformation))
        else:
            self.empty_state_label.setText("Connect to a direct SSH alias and open a RELION project.")

    def _apply_icons(self):
        button_icon_size = QSize(18, 18)
        button_icon_map = {
            self.connect_button: "icons/connect-512.png",
            self.disconnect_button: "icons/disconnect-512.png",
            self.refresh_button: "icons/refresh-512.png",
            self.clear_cache_button: "icons/clear-cache-512.png",
            self.browse_disconnect_button: "icons/disconnect-512.png",
            self.refresh_pipeline_button: "icons/refresh-512.png",
            self.open_button: "icons/connect-512.png",
            self.latest_button: "icons/latest-refine-512.png",
            self.last_job_button: "icons/last-completed-512.png",
            self.half_button: "icons/half-maps-512.png",
            self.postprocess_button: "icons/postprocess-model-512.png",
            self.find_in_tree_button: "icons/find-tree-512.png",
            self.path_go_button: "icons/refresh-512.png",
            self.set_directory_button: "icons/find-tree-512.png",
            self.connection_page_button: "icons/connect-512.png",
        }
        for button, relative_path in button_icon_map.items():
            icon = load_icon(relative_path)
            if icon.isNull():
                continue
            button.setIcon(icon)
            button.setIconSize(button_icon_size)

        flowchart_icon = load_icon("icons/flowchart-512.png")
        jobs_icon = load_icon("icons/jobs-512.png")
        if not flowchart_icon.isNull():
            self.pipeline_tabs.setTabIcon(0, flowchart_icon)
        if not jobs_icon.isNull():
            self.pipeline_tabs.setTabIcon(1, jobs_icon)

    def _selection_changed(self, *_args):
        self._update_action_state()
        self.preview_requested.emit(self.current_entry())

    def _tree_current_changed(self, current, _previous):
        if current.isValid():
            self._update_action_state()
            self.preview_requested.emit(self.current_entry())

    def _table_row_changed(self, current, _previous):
        if self._syncing_pipeline_selection:
            return
        job = self.pipeline_table_model.job_at(current.row()) if current.isValid() else None
        self.select_job(job.job_id if job else None, emit=True)

    def _flowchart_job_selected(self, job_id: str | None):
        if self._syncing_pipeline_selection:
            return
        self.select_job(job_id, emit=True)

    def _show_tree_context_menu(self, pos):
        index = self.tree.indexAt(pos)
        if index.isValid():
            self.tree.setCurrentIndex(index)
        availability = self._action_availability()
        menu = QMenu(self)
        self._add_menu_action(menu, "Open Selected", self.open_selected_requested.emit, availability[ACTION_OPEN_SELECTED])
        menu.addSeparator()
        self._add_menu_action(menu, "Refresh All", self.refresh_requested.emit, availability[ACTION_REFRESH])
        self._add_menu_action(menu, "Open Latest Refine", self.open_latest_refine_requested.emit, availability[ACTION_OPEN_LATEST_REFINE])
        self._add_menu_action(menu, "Open Half Maps", self.open_half_maps_requested.emit, availability[ACTION_OPEN_HALF_MAPS])
        self._add_menu_action(
            menu,
            "Open PostProcess + Model",
            self.open_postprocess_requested.emit,
            availability[ACTION_OPEN_POSTPROCESS_MODEL],
        )
        self._add_menu_action(menu, "Reveal Related Files", self.reveal_related_requested.emit, availability[ACTION_REVEAL_RELATED])
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _show_pipeline_context_menu(self, pos):
        index = self.pipeline_table.indexAt(pos)
        if index.isValid():
            self.pipeline_table.setCurrentIndex(index)
            self.pipeline_table.selectRow(index.row())
        availability = self._action_availability()
        menu = QMenu(self)
        self._add_menu_action(menu, "Refresh Pipeline", self.refresh_pipeline_requested.emit, availability[ACTION_REFRESH_PIPELINE])
        menu.addSeparator()
        self._add_menu_action(menu, "Open Half Maps", self.open_half_maps_requested.emit, availability[ACTION_OPEN_HALF_MAPS])
        self._add_menu_action(
            menu,
            "Open PostProcess + Model",
            self.open_postprocess_requested.emit,
            availability[ACTION_OPEN_POSTPROCESS_MODEL],
        )
        self._add_menu_action(menu, "Reveal Related Files", self.reveal_related_requested.emit, availability[ACTION_REVEAL_RELATED])
        self._add_menu_action(menu, "Find In Tree", self.find_in_tree_requested.emit, availability[ACTION_FIND_IN_TREE])
        menu.exec(self.pipeline_table.viewport().mapToGlobal(pos))

    def _show_flowchart_context_menu(self, pos):
        job_id = self.flowchart_view.job_id_at(pos)
        if job_id is not None:
            self.select_job(job_id, emit=True)
        availability = self._action_availability()
        menu = QMenu(self)
        self._add_menu_action(menu, "Refresh Pipeline", self.refresh_pipeline_requested.emit, availability[ACTION_REFRESH_PIPELINE])
        menu.addSeparator()
        self._add_menu_action(menu, "Open Half Maps", self.open_half_maps_requested.emit, availability[ACTION_OPEN_HALF_MAPS])
        self._add_menu_action(
            menu,
            "Open PostProcess + Model",
            self.open_postprocess_requested.emit,
            availability[ACTION_OPEN_POSTPROCESS_MODEL],
        )
        self._add_menu_action(menu, "Reveal Related Files", self.reveal_related_requested.emit, availability[ACTION_REVEAL_RELATED])
        self._add_menu_action(menu, "Find In Tree", self.find_in_tree_requested.emit, availability[ACTION_FIND_IN_TREE])
        menu.exec(self.flowchart_view.viewport().mapToGlobal(pos))

    def _add_menu_action(self, menu: QMenu, text: str, callback, enabled: bool):
        action = menu.addAction(text)
        action.setEnabled(enabled)
        action.triggered.connect(lambda *_args, _callback=callback: _callback())
        return action

    def _action_availability(self, *, connected: bool | None = None) -> dict[str, bool]:
        is_connected = self.disconnect_button.isEnabled() if connected is None else connected
        entry = self.current_entry()
        has_tree_entry = entry is not None
        has_job = self.current_job_id() is not None
        has_project = self._project is not None and bool(self._project.jobs)
        is_openable_file = bool(has_tree_entry and entry.is_file and is_openable_path(entry.path))
        return compute_action_availability(
            connected=is_connected,
            has_project=has_project,
            has_job=has_job,
            has_tree_entry=has_tree_entry,
            is_openable_file=is_openable_file,
        )

    def _update_action_state(self, *, connected: bool | None = None):
        availability = self._action_availability(connected=connected)
        self.open_button.setEnabled(availability[ACTION_OPEN_SELECTED])
        self.latest_button.setEnabled(availability[ACTION_OPEN_LATEST_REFINE])
        self.last_job_button.setEnabled(availability[ACTION_OPEN_LAST_COMPLETED])
        self.half_button.setEnabled(availability[ACTION_OPEN_HALF_MAPS])
        self.postprocess_button.setEnabled(availability[ACTION_OPEN_POSTPROCESS_MODEL])
        self.related_button.setEnabled(availability[ACTION_REVEAL_RELATED])
        self.refresh_pipeline_button.setEnabled(availability[ACTION_REFRESH_PIPELINE])
        self.find_in_tree_button.setEnabled(availability[ACTION_FIND_IN_TREE])
        self.set_directory_button.setEnabled(bool((connected if connected is not None else self.disconnect_button.isEnabled()) and self.current_entry() is not None))

    def _tree_active_index(self):
        if self._model is None:
            return self.tree.currentIndex()
        selection_model = self.tree.selectionModel()
        if selection_model is None:
            return self.tree.currentIndex()
        current = selection_model.currentIndex()
        if current.isValid():
            return current
        rows = selection_model.selectedRows(0)
        if rows:
            return rows[0]
        selected = selection_model.selectedIndexes()
        if selected:
            return selected[0]
        return self.tree.currentIndex()
