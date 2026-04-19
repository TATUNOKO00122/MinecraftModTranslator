from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                                QTableWidgetItem, QPushButton, QHeaderView, QLabel,
                                QCheckBox, QWidget, QLineEdit, QProgressBar, QMessageBox,
                                QMenu, QAbstractItemView)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QAction

from logic.term_extractor import AITermClassifierThread, FrequentTermTranslateThread

_INCONSISTENT_BG = QColor(255, 255, 210)


def _stop_thread(dialog, attr_name):
    """ダイアログのスレッド属性を安全に停止・解放する。"""
    thread = getattr(dialog, attr_name, None)
    if thread is not None:
        thread.stop()
        thread.wait(3000)
        setattr(dialog, attr_name, None)


def _set_visible_checkboxes(table, checked):
    """テーブル内の可視行のチェックボックスを一括設定する。"""
    for row in range(table.rowCount()):
        if table.isRowHidden(row):
            continue
        widget = table.cellWidget(row, 0)
        if widget:
            checkbox = widget.findChild(QCheckBox)
            if checkbox:
                checkbox.setChecked(checked)


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
        header_label.setObjectName("HeaderLabel")
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

        select_layout.addStretch()
        layout.addLayout(select_layout)

        btn_layout = QHBoxLayout()

        add_btn = QPushButton("選択した項目を辞書に追加")
        add_btn.setObjectName("PrimaryButton")
        add_btn.clicked.connect(self._add_selected)
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

    def _select_all(self):
        _set_visible_checkboxes(self.table, True)

    def _deselect_all(self):
        _set_visible_checkboxes(self.table, False)

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


class FrequentTermDialog(QDialog):
    """翻訳前に原文から抽出した頻出語を確認し、辞書に登録するダイアログ。"""

    def __init__(self, frequent_terms, glossary, parent=None, api_key=None, freq_model=None,
                 initial_translations=None):
        super().__init__(parent)
        self.glossary = glossary
        self.selected_terms = {}
        self._frequent_terms = frequent_terms
        self._api_key = api_key
        self._freq_model = freq_model
        self._translate_thread = None
        self._classifier_thread = None
        self._initial_translations = initial_translations or {}

        self.setWindowTitle("頻出固有名詞の抽出結果（翻訳前）")
        self.resize(850, 600)
        self.setModal(True)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        total = len(self._frequent_terms)
        header_label = QLabel(
            f"原文から {total} 件の頻出固有名詞候補が見つかりました。\n"
            "辞書に登録する項目にチェックを入れ、訳文（日本語）を入力してください。\n"
            "登録された用語は翻訳時の一貫性確保に使用されます。"
        )
        header_label.setObjectName("HeaderLabel")
        layout.addWidget(header_label)

        search_layout = QHBoxLayout()
        search_label = QLabel("検索:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("原文で絞り込み...")
        self.search_input.textChanged.connect(self._filter_table)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        layout.addLayout(search_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(
            ["追加", "原文 (英語)", "訳文 (日本語)", "出現数"]
        )

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 50)
        self.table.verticalHeader().setDefaultSectionSize(35)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)

        self.table.setRowCount(len(self._frequent_terms))

        for row, (term, count, sample_keys) in enumerate(self._frequent_terms):
            checkbox = QCheckBox()
            checkbox.setChecked(False)
            checkbox_widget = QWidget()
            cb_layout = QHBoxLayout(checkbox_widget)
            cb_layout.addWidget(checkbox)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, checkbox_widget)

            orig_item = QTableWidgetItem(term)
            self.table.setItem(row, 1, orig_item)

            initial_trans = self._initial_translations.get(term, "")
            trans_item = QTableWidgetItem(initial_trans)
            trans_item.setBackground(QColor(60, 60, 60))
            self.table.setItem(row, 2, trans_item)

            count_item = QTableWidgetItem(f"{count} 回")
            count_item.setFlags(count_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 3, count_item)

            tooltip_lines = [f"出現数: {count} 回"]
            if sample_keys:
                tooltip_lines.append("使用箇所:")
                for k in sample_keys[:5]:
                    tooltip_lines.append(f"  - {k}")
            tooltip = "\n".join(tooltip_lines)
            for col in range(4):
                item = self.table.item(row, col)
                if item:
                    item.setToolTip(tooltip)

        layout.addWidget(self.table)

        select_layout = QHBoxLayout()

        select_all_btn = QPushButton("すべて選択")
        select_all_btn.clicked.connect(self._select_all)
        select_layout.addWidget(select_all_btn)

        deselect_all_btn = QPushButton("すべて解除")
        deselect_all_btn.clicked.connect(self._deselect_all)
        select_layout.addWidget(deselect_all_btn)

        self.show_all_btn = QPushButton("すべて表示")
        self.show_all_btn.setToolTip("仕分けで非表示になった項目も含め、全項目を表示します")
        self.show_all_btn.clicked.connect(self._show_all_rows)
        select_layout.addWidget(self.show_all_btn)

        self.ai_classify_btn = QPushButton("AI仕分け")
        self.ai_classify_btn.setToolTip("AIで固有名詞を自動仕分けし、チェック・訳文を設定します")
        self.ai_classify_btn.clicked.connect(self._start_ai_classify)
        if not self._api_key or not self._freq_model:
            self.ai_classify_btn.setEnabled(False)
            self.ai_classify_btn.setToolTip("API設定が必要です")
        select_layout.addWidget(self.ai_classify_btn)

        self.stop_classify_btn = QPushButton("仕分け停止")
        self.stop_classify_btn.setToolTip("AI仕分けを中止します")
        self.stop_classify_btn.clicked.connect(self._stop_ai_classify)
        self.stop_classify_btn.setEnabled(False)
        select_layout.addWidget(self.stop_classify_btn)

        select_layout.addStretch()

        self.ai_translate_btn = QPushButton("AI翻訳")
        self.ai_translate_btn.setToolTip("AIで選択項目の訳文を生成します")
        self.ai_translate_btn.clicked.connect(self._start_ai_translate)
        if not self._api_key or not self._freq_model:
            self.ai_translate_btn.setEnabled(False)
            self.ai_translate_btn.setToolTip("API設定が必要です")
        select_layout.addWidget(self.ai_translate_btn)

        self.stop_translate_btn = QPushButton("停止")
        self.stop_translate_btn.setToolTip("AI翻訳を中止します")
        self.stop_translate_btn.clicked.connect(self._stop_ai_translate)
        self.stop_translate_btn.setEnabled(False)
        select_layout.addWidget(self.stop_translate_btn)

        layout.addLayout(select_layout)

        btn_layout = QHBoxLayout()

        add_btn = QPushButton("選択した項目を辞書に追加")
        add_btn.setObjectName("PrimaryButton")
        add_btn.clicked.connect(self._add_selected)
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
            orig = orig_item.text().lower() if orig_item else ""
            self.table.setRowHidden(row, query not in orig)

    def _select_all(self):
        _set_visible_checkboxes(self.table, True)

    def _deselect_all(self):
        _set_visible_checkboxes(self.table, False)

    def _show_all_rows(self):
        for row in range(self.table.rowCount()):
            self.table.setRowHidden(row, False)

    # --- AI仕分け ---

    def _start_ai_classify(self):
        if not self._api_key or not self._freq_model:
            QMessageBox.warning(self, "エラー", "API設定が必要です。")
            return

        self.ai_classify_btn.setEnabled(False)
        self.stop_classify_btn.setEnabled(True)
        self.ai_translate_btn.setEnabled(False)

        self._classifier_thread = AITermClassifierThread(
            self._frequent_terms, self._api_key, self._freq_model
        )
        self._classifier_thread.finished.connect(self._on_classify_finished)
        self._classifier_thread.error.connect(self._on_classify_error)
        self._classifier_thread.progress.connect(
            lambda msg: self.ai_classify_btn.setText(msg)
        )
        self._classifier_thread.start()

    def _stop_ai_classify(self):
        _stop_thread(self, '_classifier_thread')
        self.ai_classify_btn.setEnabled(True)
        self.ai_classify_btn.setText("AI仕分け")
        self.stop_classify_btn.setEnabled(False)
        self.ai_translate_btn.setEnabled(bool(self._api_key and self._freq_model))

    def _reset_classify_buttons(self):
        self.ai_classify_btn.setEnabled(True)
        self.ai_classify_btn.setText("AI仕分け")
        self.stop_classify_btn.setEnabled(False)
        self.ai_translate_btn.setEnabled(bool(self._api_key and self._freq_model))
        _stop_thread(self, '_classifier_thread')

    def _on_classify_finished(self, classified):
        self._reset_classify_buttons()

        classified_lower = {k.lower(): (k, v) for k, v in classified.items()}
        visible_count = 0
        for row in range(self.table.rowCount()):
            orig_item = self.table.item(row, 1)
            if not orig_item:
                self.table.setRowHidden(row, True)
                continue
            orig = orig_item.text().strip()
            match = classified_lower.get(orig.lower())
            if match:
                widget = self.table.cellWidget(row, 0)
                if widget:
                    checkbox = widget.findChild(QCheckBox)
                    if checkbox:
                        checkbox.setChecked(True)
                trans_item = self.table.item(row, 2)
                if trans_item and not trans_item.text().strip():
                    trans_item.setText(match[1])
                self.table.setRowHidden(row, False)
                visible_count += 1
            else:
                self.table.setRowHidden(row, True)

        if visible_count > 0:
            QMessageBox.information(
                self, "仕分け完了",
                f"AI仕分け完了: {visible_count} 件を固有名詞として抽出しました。\n"
                "内容を確認してください。\n"
                "「すべて表示」で元のリストに戻せます。"
            )
        else:
            QMessageBox.information(self, "情報", "固有名詞として判定された項目がありませんでした。")

    def _on_classify_error(self, error_msg):
        self._reset_classify_buttons()
        QMessageBox.warning(self, "エラー", f"AI仕分けに失敗しました:\n{error_msg}")

    # --- AI翻訳（共通ロジック） ---

    def _collect_empty_translations(self, target_rows=None):
        """訳文が空の項目を収集する。target_rows=Noneの場合はチェック済み全行対象。"""
        terms = []
        rows = target_rows if target_rows is not None else range(self.table.rowCount())
        for row in rows:
            if target_rows is None:
                if self.table.isRowHidden(row):
                    continue
                widget = self.table.cellWidget(row, 0)
                if not widget:
                    continue
                checkbox = widget.findChild(QCheckBox)
                if not checkbox or not checkbox.isChecked():
                    continue
            trans_item = self.table.item(row, 2)
            if trans_item and not trans_item.text().strip():
                orig_item = self.table.item(row, 1)
                if orig_item:
                    terms.append(orig_item.text().strip())
        return terms

    def _apply_translations(self, translations, target_rows=None):
        """翻訳結果をテーブルに適用し、適用件数を返す。"""
        applied = 0
        rows = target_rows if target_rows is not None else range(self.table.rowCount())
        for row in rows:
            if target_rows is None and self.table.isRowHidden(row):
                continue
            orig_item = self.table.item(row, 1)
            trans_item = self.table.item(row, 2)
            if not orig_item or not trans_item:
                continue
            orig = orig_item.text().strip()
            if orig in translations and not trans_item.text().strip():
                trans_item.setText(translations[orig])
                applied += 1
        return applied

    def _start_ai_translate_for_rows(self, target_rows=None):
        """AI翻訳を開始する。target_rows=Noneの場合はチェック済み全行対象。"""
        terms_to_translate = self._collect_empty_translations(target_rows)

        if not terms_to_translate:
            QMessageBox.information(self, "情報", "訳文が空の項目がありません。")
            return

        self.ai_translate_btn.setEnabled(False)
        self.stop_translate_btn.setEnabled(True)

        self._translate_thread = FrequentTermTranslateThread(
            terms_to_translate, self._api_key, self._freq_model
        )
        self._translate_thread.finished.connect(
            lambda t: self._on_translate_finished(t, target_rows)
        )
        self._translate_thread.error.connect(self._on_translate_error)
        self._translate_thread.progress.connect(
            lambda msg: self.ai_translate_btn.setText(msg)
        )
        self._translate_thread.start()

    def _start_ai_translate(self):
        self._start_ai_translate_for_rows(None)

    def _stop_ai_translate(self):
        _stop_thread(self, '_translate_thread')
        self.ai_translate_btn.setEnabled(True)
        self.ai_translate_btn.setText("AI翻訳")
        self.stop_translate_btn.setEnabled(False)

    def _on_translate_finished(self, translations, target_rows=None):
        self.ai_translate_btn.setEnabled(True)
        self.ai_translate_btn.setText("AI翻訳")
        self.stop_translate_btn.setEnabled(False)
        _stop_thread(self, '_translate_thread')

        applied = self._apply_translations(translations, target_rows)

        if applied > 0:
            QMessageBox.information(
                self, "完了",
                f"{applied} 件のAI翻訳を適用しました。\n"
                "内容を確認してから辞書に追加してください。"
            )
        else:
            QMessageBox.information(self, "情報", "適用可能な翻訳結果がありませんでした。")

    def _on_translate_error(self, error_msg):
        self.ai_translate_btn.setEnabled(True)
        self.ai_translate_btn.setText("AI翻訳")
        self.stop_translate_btn.setEnabled(False)
        _stop_thread(self, '_translate_thread')
        QMessageBox.warning(self, "エラー", f"AI翻訳に失敗しました:\n{error_msg}")

    # --- コンテキストメニュー ---

    def _show_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return

        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())
        if not selected_rows:
            selected_rows.add(row)

        menu = QMenu(self)

        if len(selected_rows) == 1:
            translate_action = QAction("この項目をAI翻訳", self)
        else:
            translate_action = QAction(f"選択範囲をAI翻訳 ({len(selected_rows)}件)", self)
        translate_action.triggered.connect(lambda: self._start_ai_translate_for_rows(list(selected_rows)))
        menu.addAction(translate_action)

        menu.exec(self.table.mapToGlobal(pos))

    # --- 辞書追加 ---

    def _add_selected(self):
        selected = {}
        no_translation = []

        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if not widget:
                continue
            checkbox = widget.findChild(QCheckBox)
            if not checkbox or not checkbox.isChecked():
                continue

            orig_item = self.table.item(row, 1)
            trans_item = self.table.item(row, 2)
            if not orig_item or not trans_item:
                continue

            orig = orig_item.text().strip()
            trans = trans_item.text().strip()
            if orig and trans:
                selected[orig] = trans
            elif orig:
                no_translation.append(orig)

        if no_translation:
            reply = QMessageBox.warning(
                self, "訳文未入力",
                f"{len(no_translation)} 件の項目に訳文が入力されていません。\n"
                "訳文のない項目を除外して続行しますか？",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                return

        if selected:
            current_terms = self.glossary.get_terms()
            current_terms.update(selected)
            self.glossary.set_terms(current_terms)
            self.selected_terms = selected

        self._cleanup_all_threads()
        self.accept()

    def reject(self):
        self._cleanup_all_threads()
        super().reject()

    def _cleanup_all_threads(self):
        _stop_thread(self, '_translate_thread')
        _stop_thread(self, '_classifier_thread')

    def get_added_count(self):
        return len(self.selected_terms)
