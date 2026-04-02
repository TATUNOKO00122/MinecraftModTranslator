import json
import time
import requests
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, 
                                QComboBox, QPushButton, QHBoxLayout, QFileDialog,
                                QSpinBox, QGroupBox, QFormLayout, QMessageBox)
from PySide6.QtCore import QSettings, Qt

try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False

from logic.file_handler import PACK_FORMATS, TARGET_LANGUAGES

KEYRING_SERVICE = "MinecraftModTranslator"
KEYRING_USERNAME = "api_key"


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setMinimumWidth(480)
        
        self.settings = QSettings("MinecraftModTranslator", "App")
        
        layout = QVBoxLayout(self)
        
        # API Key
        layout.addWidget(QLabel("OpenRouter API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        stored_key = self._load_api_key()
        self.api_key_input.setText(stored_key)
        layout.addWidget(self.api_key_input)
        
        if KEYRING_AVAILABLE:
            layout.addWidget(QLabel("キーはOSのキーチェーンに安全に保存されます。"))
        else:
            layout.addWidget(QLabel("⚠ keyring未インストール: キーは平文で保存されます。\npip install keyring で安全な保存が可能です。"))
        
        layout.addSpacing(10)

        # Model
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("使用モデル:"), 0)
        self.refresh_models_btn = QPushButton("更新")
        self.refresh_models_btn.setFixedWidth(60)
        self.refresh_models_btn.setToolTip("OpenRouter APIからモデル一覧を取得します")
        self.refresh_models_btn.clicked.connect(self._refresh_models_from_api)
        model_row.addWidget(self.refresh_models_btn, 0)
        model_row.addStretch()
        layout.addLayout(model_row)
        
        self.model_combo = QComboBox()
        self._fallback_models = [
            ('google/gemini-2.0-flash-exp:free', 'Gemini 2.0 Flash (Experimental/Free)'),
            ('google/gemini-exp-1206:free', 'Gemini Exp 1206 (Experimental/Free)'),
            ('openai/gpt-4o', 'GPT-4o (High Performance)'),
            ('openai/gpt-4o-mini', 'GPT-4o mini (Balanced)'),
            ('anthropic/claude-3.5-sonnet', 'Claude 3.5 Sonnet (Recommended)'),
            ('anthropic/claude-3-haiku', 'Claude 3 Haiku (Fast)'),
            ('deepseek/deepseek-chat', 'DeepSeek Chat'),
            ('openai/gpt-3.5-turbo', 'GPT-3.5 Turbo'),
        ]
        self._populate_models(self._fallback_models)
        layout.addWidget(self.model_combo)
        
        layout.addSpacing(10)

        # Frequent Term Translation Model
        freq_model_group = QGroupBox("頻出語抽出 - 翻訳モデル")
        freq_model_layout = QFormLayout()

        self.freq_model_combo = QComboBox()
        self._freq_fallback_models = [
            ('google/gemini-2.5-flash-lite', 'Gemini 2.5 Flash-Lite'),
            ('google/gemini-2.0-flash-exp:free', 'Gemini 2.0 Flash (Free)'),
            ('deepseek/deepseek-chat', 'DeepSeek Chat'),
            ('openai/gpt-4o-mini', 'GPT-4o mini'),
        ]
        self._populate_freq_models(self._freq_fallback_models)
        freq_model_layout.addRow("モデル:", self.freq_model_combo)

        freq_hint = QLabel("頻出語の仮翻訳に使用するモデル（安価・高速モデルを推奨）")
        freq_hint.setObjectName("HintLabel")
        freq_model_layout.addRow("", freq_hint)

        freq_model_group.setLayout(freq_model_layout)
        layout.addWidget(freq_model_group)
        
        layout.addSpacing(10)

        # Minecraft Version / pack_format
        mc_group = QGroupBox("Minecraft バージョン")
        mc_layout = QFormLayout()
        
        self.mc_version_combo = QComboBox()
        for ver, fmt in PACK_FORMATS.items():
            self.mc_version_combo.addItem(f"{ver} (pack_format: {fmt})", fmt)
        current_pack = int(self.settings.value("pack_format", 15))
        idx = self.mc_version_combo.findData(current_pack)
        if idx >= 0:
            self.mc_version_combo.setCurrentIndex(idx)
        mc_layout.addRow("バージョン:", self.mc_version_combo)
        
        mc_group.setLayout(mc_layout)
        layout.addWidget(mc_group)
        
        layout.addSpacing(10)
        
        # Target Language
        lang_group = QGroupBox("翻訳先言語")
        lang_layout = QFormLayout()
        
        self.target_lang_combo = QComboBox()
        for lang_code, (en_name, native_name) in TARGET_LANGUAGES.items():
            self.target_lang_combo.addItem(f"{native_name} ({en_name})", lang_code)
        current_lang = self.settings.value("target_lang", "ja_jp")
        lang_idx = self.target_lang_combo.findData(current_lang)
        if lang_idx >= 0:
            self.target_lang_combo.setCurrentIndex(lang_idx)
        lang_layout.addRow("言語:", self.target_lang_combo)
        
        lang_group.setLayout(lang_layout)
        layout.addWidget(lang_group)
        
        layout.addSpacing(10)
        
        # Performance Settings Group
        perf_group = QGroupBox("パフォーマンス設定")
        perf_layout = QFormLayout()
        
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 10)
        self.parallel_spin.setValue(int(self.settings.value("parallel_count", 3)))
        self.parallel_spin.setToolTip(
            "同時に送信するAPIリクエスト数。\n"
            "高い値: 翻訳速度が向上しますが、レート制限に注意。\n"
            "低い値: 安定しますが、翻訳速度は遅くなります。\n"
            "無料モデル使用時は1〜2を推奨。"
        )
        perf_layout.addRow("並列リクエスト数:", self.parallel_spin)
        
        help_label = QLabel("※ 無料モデル: 1〜2推奨 / 有料モデル: 3〜5推奨")
        help_label.setObjectName("HintLabel")
        perf_layout.addRow("", help_label)
        
        perf_group.setLayout(perf_layout)
        layout.addWidget(perf_group)
        
        layout.addSpacing(10)

        # Default Export Directory
        layout.addWidget(QLabel("デフォルト出力先フォルダ:"))
        dir_layout = QHBoxLayout()
        self.export_dir_input = QLineEdit()
        self.export_dir_input.setText(self.settings.value("export_dir", ""))
        dir_layout.addWidget(self.export_dir_input)
        
        dir_btn = QPushButton("参照...")
        dir_btn.clicked.connect(self.choose_export_dir)
        dir_layout.addWidget(dir_btn)
        layout.addLayout(dir_layout)
        
        layout.addSpacing(20)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("保存")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(save_btn)
        
        layout.addLayout(btn_layout)
        
        self._load_cached_models()

    def _populate_models(self, models):
        current_data = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model_id, model_name in models:
            self.model_combo.addItem(model_name, model_id)
        if current_data:
            idx = self.model_combo.findData(current_data)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(False)

    def _populate_freq_models(self, models):
        current_data = self.freq_model_combo.currentData()
        self.freq_model_combo.blockSignals(True)
        self.freq_model_combo.clear()
        for model_id, model_name in models:
            self.freq_model_combo.addItem(model_name, model_id)
        if current_data:
            idx = self.freq_model_combo.findData(current_data)
            if idx >= 0:
                self.freq_model_combo.setCurrentIndex(idx)
        self.freq_model_combo.blockSignals(False)

    def _load_cached_models(self):
        cached_json = self.settings.value("cached_models", "")
        cached_ts = self.settings.value("cached_models_ts", 0)
        
        if cached_json:
            try:
                cache_age = time.time() - float(cached_ts)
                if cache_age < 86400:
                    models = json.loads(cached_json)
                    self._populate_models(models)
                    current_model = self.settings.value("model", "")
                    idx = self.model_combo.findData(current_model)
                    if idx >= 0:
                        self.model_combo.setCurrentIndex(idx)

                    freq_model = self.settings.value("freq_model", "")
                    if freq_model:
                        idx = self.freq_model_combo.findData(freq_model)
                        if idx >= 0:
                            self.freq_model_combo.setCurrentIndex(idx)
                    return
            except (json.JSONDecodeError, ValueError):
                pass
        
        self._refresh_models_from_api()

    def _refresh_models_from_api(self):
        self.refresh_models_btn.setEnabled(False)
        self.refresh_models_btn.setText("取得中...")
        
        try:
            api_key = self.api_key_input.text()
            if not api_key:
                self._populate_models(self._fallback_models)
                self._restore_model_selection()
                return
            
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = requests.get("https://openrouter.ai/api/v1/models",
                                headers=headers, timeout=15)
            
            if resp.status_code != 200:
                self._populate_models(self._fallback_models)
                self._restore_model_selection()
                return
            
            data = resp.json()
            raw_models = data.get("data", [])
            
            models = []
            for m in raw_models:
                mid = m.get("id", "")
                mname = m.get("name", mid)
                pricing = m.get("pricing", {})
                prompt_price = float(pricing.get("prompt", "1") or "1")
                
                if prompt_price == 0:
                    label = f"{mname} (Free)"
                else:
                    label = mname
                models.append((mid, label))
            
            if not models:
                models = self._fallback_models
            
            models.sort(key=lambda x: (0 if "(Free)" in x[1] else 1, x[1]))
            
            self.settings.setValue("cached_models", json.dumps(models))
            self.settings.setValue("cached_models_ts", str(time.time()))
            
            self._populate_models(models)
            self._restore_model_selection()

            self._populate_freq_models(models)
            freq_model = self.settings.value("freq_model", "google/gemini-2.5-flash-lite")
            idx = self.freq_model_combo.findData(freq_model)
            if idx >= 0:
                self.freq_model_combo.setCurrentIndex(idx)
            
        except Exception:
            self._populate_models(self._fallback_models)
            self._restore_model_selection()
            self._populate_freq_models(self._freq_fallback_models)
            self._restore_freq_model_selection()
        finally:
            self.refresh_models_btn.setEnabled(True)
            self.refresh_models_btn.setText("更新")

    def _restore_model_selection(self):
        current_model = self.settings.value("model", "openai/gpt-4o-mini")
        idx = self.model_combo.findData(current_model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)

    def _restore_freq_model_selection(self):
        freq_model = self.settings.value("freq_model", "google/gemini-2.5-flash-lite")
        idx = self.freq_model_combo.findData(freq_model)
        if idx >= 0:
            self.freq_model_combo.setCurrentIndex(idx)

    def choose_export_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "出力先フォルダを選択")
        if dir_path:
            self.export_dir_input.setText(dir_path)

    def _load_api_key(self):
        if KEYRING_AVAILABLE:
            try:
                key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
                if key:
                    return key
            except Exception:
                pass
        return self.settings.value("api_key", "")

    def _save_api_key(self, api_key):
        if KEYRING_AVAILABLE:
            try:
                if api_key:
                    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, api_key)
                else:
                    try:
                        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
                    except keyring.errors.PasswordDeleteError:
                        pass
                self.settings.remove("api_key")
                return
            except Exception:
                pass
        self.settings.setValue("api_key", api_key)

    def save_settings(self):
        self._save_api_key(self.api_key_input.text())
        self.settings.setValue("model", self.model_combo.currentData())
        self.settings.setValue("freq_model", self.freq_model_combo.currentData())
        self.settings.setValue("export_dir", self.export_dir_input.text())
        self.settings.setValue("parallel_count", self.parallel_spin.value())
        self.settings.setValue("pack_format", self.mc_version_combo.currentData())
        self.settings.setValue("target_lang", self.target_lang_combo.currentData())
        self.accept()

    def get_settings(self):
        api_key = self.api_key_input.text()
        return {
            "api_key": api_key,
            "model": self.model_combo.currentData(),
            "freq_model": self.freq_model_combo.currentData(),
            "export_dir": self.export_dir_input.text(),
            "parallel_count": self.parallel_spin.value(),
            "pack_format": self.mc_version_combo.currentData(),
            "target_lang": self.target_lang_combo.currentData(),
        }
