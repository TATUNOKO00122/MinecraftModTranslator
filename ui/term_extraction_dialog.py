from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                                QTableWidgetItem, QPushButton, QHeaderView, QLabel,
                                QCheckBox, QWidget, QLineEdit)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor


_INCONSISTENT_BG = QColor(255, 255, 210)


class TermExtractionDialog(QDialog):

    def __init__(self, extracted_terms, glossary, parent=None, inconsistent_terms=None):
        super().__init__(parent)
        self.extracted_terms = extracted_terms or {}
        self.inconsistent_terms = inconsistent_terms or {}
        self.glossary = glossary
        self.selected_terms = {}

        self._row_data = []

        self.setWindowTitle("辞書に追加する項目の選択")
        self.resize(800, 550)
        self.setModal(True)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        total = len(self.extracted_terms) + len(self.inconsistent_terms)
        inconsistent_count = len(self.inconsistent_terms)

        header_text = f"{total} 件の項目が見つかりました。"
        if inconsistent_count > 0:
            header_text += (
                f"\n（うち {inconsistent_count} 件は翻訳にブレがあります → 黄色の行）"
            )
        header_text += "\n辞書に追加する項目にチェックを入れてください。原文・訳文ともに直接編集できます。"

        header_label = QLabel(header_text)
        header_label.setStyleSheet("font-size: 12px; margin-bottom: 5px;")
        layout.addWidget(header_label)

        search_layout = QHBoxLayout()
        search_label = QLabel("検索:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("原文または訳文で絞り込み...")
        self.search_input.textChanged.connect(self._filter_table)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["追加", "原文 (英語)", "訳文 (日本語)", "情報"])

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 50)

        self.table.verticalHeader().setDefaultSectionSize(35)

        self._row_data = []
        for orig, trans in self.extracted_terms.items():
            self._row_data.append((orig, trans, False, ""))

        for orig, info in self.inconsistent_terms.items():
            info_text = f"ブレ ({info['count']}件, {len(info['all'])}通り)"
            self._row_data.append((orig, info["most_common"], True, info_text))

        self.table.setRowCount(len(self._row_data))

        for row, (orig, trans, is_inconsistent, info_text) in enumerate(self._row_data):
            checkbox = QCheckBox()
            checkbox.setChecked(not is_inconsistent)
            checkbox_widget = QWidget()
            cb_layout = QHBoxLayout(checkbox_widget)
            cb_layout.addWidget(checkbox)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, checkbox_widget)

            orig_item = QTableWidgetItem(orig)
            self.table.setItem(row, 1, orig_item)

            trans_item = QTableWidgetItem(trans)
            self.table.setItem(row, 2, trans_item)

            info_item = QTableWidgetItem(info_text)
            info_item.setFlags(info_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 3, info_item)

            if is_inconsistent:
                variants = self.inconsistent_terms[orig]["all"]
                tooltip = "翻訳バリエーション:\n" + "\n".join(f"  - {t}" for t in variants)
                for col in range(4):
                    item = self.table.item(row, col)
                    if item:
                        item.setBackground(_INCONSISTENT_BG)
                        item.setToolTip(tooltip)
                checkbox_widget.setToolTip(tooltip)

        layout.addWidget(self.table)

        select_layout = QHBoxLayout()

        select_all_btn = QPushButton("すべて選択")
        select_all_btn.clicked.connect(self._select_all)
        select_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("すべて解除")
        deselect_all_btn.clicked.connect(self._deselect_all)
        select_layout.addWidget(deselect_all_btn)

        if inconsistent_count > 0:
            inconsistent_only_btn = QPushButton("不一致のみ選択")
            inconsistent_only_btn.clicked.connect(self._select_inconsistent_only)
            select_layout.addWidget(inconsistent_only_btn)

        select_layout.addStretch()
        layout.addLayout(select_layout)

        btn_layout = QHBoxLayout()

        add_btn = QPushButton("選択した項目を辞書に追加")
        add_btn.clicked.connect(self._add_selected)
        add_btn.setStyleSheet(
            "background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px;"
        )
        btn_layout.addWidget(add_btn)

        skip_btn = QPushButton("スキップ")
        skip_btn.clicked.connect(self.reject)
        btn_layout.addWidget(skip_btn)

        layout.addLayout(btn_layout)

    def _filter_table(self, text):
        query = text.lower().strip()
        for row in range(self.table.rowCount()):
            if not query:
                self.table.setRowHidden(row, False)
                continue
            orig_item = self.table.item(row, 1)
            trans_item = self.table.item(row, 2)
            orig = orig_item.text().lower() if orig_item else ""
            trans = trans_item.text().lower() if trans_item else ""
            self.table.setRowHidden(row, query not in orig and query not in trans)

    def _set_visible_checkboxes(self, checked):
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(checked)

    def _select_all(self):
        self._set_visible_checkboxes(True)

    def _deselect_all(self):
        self._set_visible_checkboxes(False)

    def _select_inconsistent_only(self):
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    _, _, is_inconsistent, _ = self._row_data[row]
                    checkbox.setChecked(is_inconsistent)

    def _add_selected(self):
        selected = {}

        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    orig_item = self.table.item(row, 1)
                    trans_item = self.table.item(row, 2)
                    if orig_item and trans_item:
                        orig = orig_item.text().strip()
                        trans = trans_item.text().strip()
                        if orig and trans:
                            selected[orig] = trans

        if selected:
            current_terms = self.glossary.get_terms()
            current_terms.update(selected)
            self.glossary.set_terms(current_terms)
            self.selected_terms = selected

        self.accept()

    def get_added_count(self):
        return len(self.selected_terms)
