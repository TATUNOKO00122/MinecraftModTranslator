from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                                QTableWidgetItem, QPushButton, QHeaderView, QLabel,
                                QCheckBox, QWidget, QLineEdit, QRadioButton,
                                QButtonGroup, QProgressBar, QMessageBox, QMenu,
                                QStyleOptionButton, QStyle, QAbstractItemView)
from PySide6.QtCore import Qt, QThread, Signal, QRect
from PySide6.QtGui import QColor, QPainter, QPen, QAction
import json
import requests


_INCONSISTENT_BG = QColor(255, 255, 210)


class CheckMarkCheckBox(QCheckBox):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(
            "QCheckBox::indicator { width: 16px; height: 16px;"
            " background: transparent; border: none; }"
        )

    def paintEvent(self, event):
        opt = QStyleOptionButton()
        self.initStyleOption(opt)

        ind_rect = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxIndicator, opt, self
        )
        contents_rect = self.style().subElementRect(
            QStyle.SubElement.SE_CheckBoxContents, opt, self
        )

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        is_checked = self.isChecked()
        border = QColor("#888888") if self.underMouse() else QColor("#666666")
        if is_checked:
            border = QColor("#007acc")

        p.setPen(QPen(border, 1.5))
        p.setBrush(QColor("#1e1e1e"))
        p.drawRect(ind_rect)

        if is_checked:
            pen = QPen(border, 2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            r = ind_rect
            m = max(3, int(r.width() * 0.2))
            left = r.left() + m
            mid_x = r.left() + int(r.width() * 0.4)
            right = r.right() - m
            top = r.top() + m
            bottom = r.bottom() - m
            mid_y = r.center().y() + 1
            p.drawLine(left, mid_y, mid_x, bottom)
            p.drawLine(mid_x, bottom, right, top)

        text = self.text()
        if text:
            p.setPen(QColor("#d4d4d4"))
            p.setFont(self.font())
            p.drawText(contents_rect, Qt.AlignVCenter | Qt.AlignLeft, text)

        p.end()


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
            checkbox = CheckMarkCheckBox()
            checkbox.setChecked(not is_inconsistent)
            checkbox_widget = QWidget()
            checkbox_widget.setStyleSheet("background: transparent;")
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
        add_btn.clicked.connect(self._add_selected)
        add_btn.setStyleSheet(
            "background-color: #007acc; color: #ffffff; border: 1px solid #007acc;"
            " padding: 6px 14px; font-weight: bold;"
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


class FrequentTermTranslateThread(QThread):
    """頻出語のAI翻訳を行うスレッド。"""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, terms, api_key, model):
        super().__init__()
        self.terms = terms
        self.api_key = api_key
        self.model = model
        self.is_running = True

    def run(self):
        try:
            self.progress.emit("AI翻訳を生成中...")

            terms_list = list(self.terms)
            batch_size = 50
            all_translations = {}

            for i in range(0, len(terms_list), batch_size):
                if not self.is_running:
                    break

                batch = terms_list[i:i + batch_size]
                self.progress.emit(
                    f"AI翻訳を生成中... ({i + len(batch)}/{len(terms_list)})"
                )

                translations = self._translate_batch(batch)
                all_translations.update(translations)

            self.finished.emit(all_translations)

        except Exception as e:
            self.error.emit(str(e))

    def _translate_batch(self, terms):
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Minecraft MOD Translator - Frequent Term Translation"
        }

        terms_json = json.dumps(terms, ensure_ascii=False)

        system_prompt = (
            "あなたはMinecraft MODの専門翻訳者です。\n"
            "与えられた英語の固有名詞リストを日本語に翻訳してください。\n"
            "Minecraftの用語 convention に従ってください。\n"
            "出力はJSONオブジェクトのみ（キー: 英語、値: 日本語）。\n"
            "マークダウンのコードブロックは使用しないでください。"
        )

        user_prompt = (
            f"以下の固有名詞を日本語に翻訳してください:\n{terms_json}\n\n"
            "JSONオブジェクトのみを出力してください。"
        )

        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.3
        }

        response = requests.post(url, headers=headers, json=data, timeout=60)
        if response.status_code != 200:
            print(f"FrequentTermTranslate API error {response.status_code}: {response.text[:500]}")
            response.raise_for_status()

        content = response.json()['choices'][0]['message']['content'].strip()

        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if match:
                return json.loads(match.group())
            return {}

    def stop(self):
        self.is_running = False


class FrequentTermDialog(QDialog):
    """翻訳前に原文から抽出した頻出語を確認し、辞書に登録するダイアログ。"""

    def __init__(self, frequent_terms, glossary, parent=None, api_key=None, freq_model=None,
                 initial_translations=None):
        """
        Args:
            frequent_terms: list[tuple[str, int, list[str]]]
            glossary: Glossary
            parent: QWidget
            api_key: str
            freq_model: str
            initial_translations: dict[str, str] — 事前AI翻訳結果（トグルON時）
        """
        super().__init__(parent)
        self.glossary = glossary
        self.selected_terms = {}
        self._frequent_terms = frequent_terms
        self._api_key = api_key
        self._freq_model = freq_model
        self._translate_thread = None
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
        header_label.setStyleSheet("font-size: 12px; margin-bottom: 5px;")
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
            checkbox = CheckMarkCheckBox()
            checkbox.setChecked(False)
            checkbox_widget = QWidget()
            checkbox_widget.setStyleSheet("background: transparent;")
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
        add_btn.clicked.connect(self._add_selected)
        add_btn.setStyleSheet(
            "background-color: #007acc; color: #ffffff; border: 1px solid #007acc;"
            " padding: 6px 14px; font-weight: bold;"
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
            orig = orig_item.text().lower() if orig_item else ""
            self.table.setRowHidden(row, query not in orig)

    def _set_visible_checkboxes(self, checked):
        for row in range(self.table.rowCount()):
            if self.table.isRowHidden(row):
                continue
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(checked)

    def _cleanup_thread(self):
        if self._translate_thread is not None:
            self._translate_thread.stop()
            self._translate_thread.wait(3000)
            self._translate_thread = None

    def _start_ai_translate(self):
        terms_to_translate = []
        for row in range(self.table.rowCount()):
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
                    terms_to_translate.append(orig_item.text().strip())

        if not terms_to_translate:
            QMessageBox.information(self, "情報", "訳文が空のチェック済み項目がありません。")
            return

        self.ai_translate_btn.setEnabled(False)
        self.stop_translate_btn.setEnabled(True)

        self._translate_thread = FrequentTermTranslateThread(
            terms_to_translate, self._api_key, self._freq_model
        )
        self._translate_thread.finished.connect(self._on_translate_finished)
        self._translate_thread.error.connect(self._on_translate_error)
        self._translate_thread.progress.connect(
            lambda msg: self.ai_translate_btn.setText(msg)
        )
        self._translate_thread.start()

    def _stop_ai_translate(self):
        self._cleanup_thread()
        self.ai_translate_btn.setEnabled(True)
        self.ai_translate_btn.setText("AI翻訳")
        self.stop_translate_btn.setEnabled(False)

    def _on_translate_finished(self, translations):
        self.ai_translate_btn.setEnabled(True)
        self.ai_translate_btn.setText("AI翻訳")
        self.stop_translate_btn.setEnabled(False)
        self._cleanup_thread()

        applied = 0
        for row in range(self.table.rowCount()):
            orig_item = self.table.item(row, 1)
            trans_item = self.table.item(row, 2)
            if not orig_item or not trans_item:
                continue
            orig = orig_item.text().strip()
            if orig in translations and not trans_item.text().strip():
                trans_item.setText(translations[orig])
                applied += 1

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
        self._cleanup_thread()
        QMessageBox.warning(self, "エラー", f"AI翻訳に失敗しました:\n{error_msg}")

    def _select_all(self):
        self._set_visible_checkboxes(True)

    def _deselect_all(self):
        self._set_visible_checkboxes(False)

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
            translate_action.triggered.connect(lambda: self._translate_rows(list(selected_rows)))
            menu.addAction(translate_action)
        else:
            translate_action = QAction(f"選択範囲をAI翻訳 ({len(selected_rows)}件)", self)
            translate_action.triggered.connect(lambda: self._translate_rows(list(selected_rows)))
            menu.addAction(translate_action)

        menu.exec(self.table.mapToGlobal(pos))

    def _translate_rows(self, rows):
        if not self._api_key or not self._freq_model:
            QMessageBox.warning(self, "エラー", "API設定が必要です。")
            return

        terms_to_translate = []
        for row in rows:
            trans_item = self.table.item(row, 2)
            if trans_item and not trans_item.text().strip():
                orig_item = self.table.item(row, 1)
                if orig_item:
                    terms_to_translate.append(orig_item.text().strip())

        if not terms_to_translate:
            QMessageBox.information(self, "情報", "訳文が空の項目がありません。")
            return

        self.ai_translate_btn.setEnabled(False)
        self.stop_translate_btn.setEnabled(True)

        self._translate_thread = FrequentTermTranslateThread(
            terms_to_translate, self._api_key, self._freq_model
        )
        self._translate_thread.finished.connect(
            lambda t: self._apply_translations_to_rows(t, rows)
        )
        self._translate_thread.error.connect(self._on_translate_error)
        self._translate_thread.progress.connect(
            lambda msg: self.ai_translate_btn.setText(msg)
        )
        self._translate_thread.start()

    def _apply_translations_to_rows(self, translations, target_rows):
        self.ai_translate_btn.setEnabled(True)
        self.ai_translate_btn.setText("AI翻訳")
        self.stop_translate_btn.setEnabled(False)
        self._cleanup_thread()

        applied = 0
        for row in target_rows:
            orig_item = self.table.item(row, 1)
            trans_item = self.table.item(row, 2)
            if not orig_item or not trans_item:
                continue
            orig = orig_item.text().strip()
            if orig in translations and not trans_item.text().strip():
                trans_item.setText(translations[orig])
                applied += 1

        if applied > 0:
            QMessageBox.information(
                self, "完了",
                f"{applied} 件のAI翻訳を適用しました。\n"
                "内容を確認してください。"
            )
        else:
            QMessageBox.information(self, "情報", "適用可能な翻訳結果がありませんでした。")

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

        self._cleanup_thread()
        self.accept()

    def reject(self):
        self._cleanup_thread()
        super().reject()

    def get_added_count(self):
        return len(self.selected_terms)
