import os
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolBar, 
                               QFileDialog, QMessageBox, QLabel, QProgressBar, QMenu, QSplitter, QListWidget)
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
from PySide6.QtCore import Qt

from logic.file_handler import FileHandler
from logic.translator import TranslatorThread
from ui.editor_widget import EditorWidget
from ui.settings_dialog import SettingsDialog

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft MOD 翻訳ツール")
        self.resize(1200, 800)
        self.setAcceptDrops(True)

        self.file_handler = FileHandler()
        self.settings_dialog = SettingsDialog(self)
        self.translator_thread = None
        
        # State: { "path/to/mod.jar": { "name": "ModName", "original": {}, "translations": {}, "files": [], "target_file": "..." } }
        self.loaded_mods = {}
        self.current_mod_path = None

        self._setup_ui()

    def _setup_ui(self):
        # Central Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0,0,0,0)
        
        # Toolbar
        self.toolbar = QToolBar("Main Toolbar")
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)
        
        # Toolbar Actions
        open_action = QAction("開く", self)
        open_action.triggered.connect(self.open_file_dialog)
        self.toolbar.addAction(open_action)
        
        self.toolbar.addSeparator()
        
        settings_action = QAction("設定", self)
        settings_action.triggered.connect(self.settings_dialog.show)
        self.toolbar.addAction(settings_action)
        
        translate_all_action = QAction("全体翻訳", self)
        translate_all_action.triggered.connect(self.start_auto_translate_all)
        self.toolbar.addAction(translate_all_action)
        
        export_action = QAction("パック作成", self)
        export_action.triggered.connect(self.export_resource_pack)
        self.toolbar.addAction(export_action)

        # Splitter Layout
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Left: MOD List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("読み込み済みMOD:"))
        
        self.mod_list = QListWidget()
        self.mod_list.currentItemChanged.connect(self.on_mod_selected)
        left_layout.addWidget(self.mod_list)
        
        splitter.addWidget(left_widget)

        # Right: Editor Area
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0,0,0,0)

        # Status Label
        self.mod_label = QLabel("MODファイルまたはMinecraftディレクトリをドラッグ＆ドロップしてください")
        self.mod_label.setAlignment(Qt.AlignCenter)
        self.mod_label.setStyleSheet("font-size: 16px; color: #94a3b8; padding: 20px;")
        right_layout.addWidget(self.mod_label)
        
        # Editor (Hidden initially)
        self.editor = EditorWidget()
        self.editor.hide()
        self.editor.table.customContextMenuRequested.connect(self.show_context_menu)
        right_layout.addWidget(self.editor)
        
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 4) # Make editor wider

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

    # --- Logic ---
    def on_mod_selected(self, current, previous):
        # Save previous
        if previous:
            prev_path = previous.data(Qt.UserRole)
            if prev_path in self.loaded_mods:
                self.loaded_mods[prev_path]["translations"] = self.editor.get_translations()

        # Load current
        if current:
            path = current.data(Qt.UserRole)
            self.current_mod_path = path
            mod_data = self.loaded_mods[path]
            
            self.mod_label.hide()
            self.editor.show()
            
            # Load data into editor
            self.editor.load_data(mod_data["original"])
            
            # Restore translations
            self.editor.update_translations(mod_data["translations"])
            
            self.setWindowTitle(f"Minecraft MOD 翻訳ツール - {mod_data['name']}")
        else:
            self.current_mod_path = None
            self.editor.hide()
            self.mod_label.show()

    # --- Context Menu ---
    def show_context_menu(self, pos):
        menu = QMenu(self)
        translate_selected_action = QAction("選択範囲を翻訳", self)
        translate_selected_action.triggered.connect(self.start_translate_selected)
        menu.addAction(translate_selected_action)
        menu.exec(self.editor.table.mapToGlobal(pos))

    # --- File Handling ---
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        for f in files:
            if f.endswith('.jar') or f.endswith('.zip'):
                self.load_file(f)

    def open_file_dialog(self):
        file_paths, _ = QFileDialog.getOpenFileNames(self, "MODファイルを開く", "", "Zip/Jar Files (*.zip *.jar)")
        for path in file_paths:
            self.load_file(path)

    def load_file(self, path):
        if path in self.loaded_mods:
            return # Already loaded

        try:
            mod_name, found_files = self.file_handler.load_zip(path)
            if not found_files:
                # Silently fail for bulk load? Or log?
                print(f"Skipping {mod_name}: No translation files found.")
                return
            
            # Select target
            target = next((f for f in found_files if 'en_us.json' in f), 
                          next((f for f in found_files if 'en_us.lang' in f), found_files[0]))
            
            data = self.file_handler.read_translation_file(path, target)
            
            # Store data
            self.loaded_mods[path] = {
                "name": mod_name,
                "original": data,
                "translations": {},
                "files": found_files,
                "target_file": target
            }

            # Add to list
            from PySide6.QtWidgets import QListWidgetItem
            item = QListWidgetItem(mod_name)
            item.setData(Qt.UserRole, path)
            self.mod_list.addItem(item)
            
            # Select if it's the first one
            if self.mod_list.count() == 1:
                self.mod_list.setCurrentItem(item)
            
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"ファイルの読み込みに失敗しました ({os.path.basename(path)}):\n{e}")

    # --- Translation Helpers ---
    def _run_translation(self, items, confirm_message):
        if not self.current_mod_path: return

        settings = self.settings_dialog.get_settings()
        api_key = settings["api_key"]
        model = settings["model"]
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。\n設定ボタンからキーを入力してください。")
            self.settings_dialog.show()
            return
            
        confirm = QMessageBox.question(self, "確認", confirm_message)
        if confirm != QMessageBox.Yes:
            return

        # Start Thread
        self.progress_bar.setRange(0, len(items))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.toolbar.setEnabled(False)
        self.editor.setEnabled(False)
        self.mod_list.setEnabled(False)

        self.translator_thread = TranslatorThread(items, api_key, model)
        self.translator_thread.progress.connect(self.progress_bar.setValue)
        self.translator_thread.finished.connect(self.on_translate_finished)
        self.translator_thread.error.connect(print)
        self.translator_thread.start()

    def start_auto_translate_all(self):
        if not self.current_mod_path: return
        missing_items = self.editor.get_missing_items()
        if not missing_items:
            QMessageBox.information(self, "情報", "未翻訳の項目はありません。")
            return
        self._run_translation(missing_items, f"{len(missing_items)} 項目（未翻訳のみ）を自動翻訳しますか？\n(API使用量にご注意ください)")

    def start_translate_selected(self):
        if not self.current_mod_path: return
        selected_items = self.editor.get_selected_items()
        if not selected_items:
            QMessageBox.information(self, "情報", "項目が選択されていません。")
            return
        self._run_translation(selected_items, f"選択された {len(selected_items)} 項目を翻訳しますか？")

    def on_translate_finished(self, results):
        self.editor.update_translations(results)
        
        # Update memory immediately
        if self.current_mod_path:
             self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()

        self.progress_bar.hide()
        self.toolbar.setEnabled(True)
        self.editor.setEnabled(True)
        self.mod_list.setEnabled(True)
        QMessageBox.information(self, "完了", "自動翻訳が完了しました！")
        self.translator_thread = None

    # --- Export ---
    def export_resource_pack(self):
        if not self.current_mod_path:
            return

        mod_data = self.loaded_mods[self.current_mod_path]
        default_name = f"{mod_data['name']}-resources.zip"
        save_path, _ = QFileDialog.getSaveFileName(self, "リソースパックを保存", default_name, "Zip Files (*.zip)")
        
        if save_path:
            try:
                # Sync current editor state first
                current_translations = self.editor.get_translations()
                self.file_handler.save_resource_pack(
                    save_path, 
                    mod_data['name'], 
                    current_translations, 
                    mod_data['target_file']
                )
                QMessageBox.information(self, "成功", "リソースパックを保存しました。")
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{e}")
