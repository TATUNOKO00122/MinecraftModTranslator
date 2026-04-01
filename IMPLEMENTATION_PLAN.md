# 実装計画 - 機能追加ロードマップ

## 前提
- 各フェーズ完了後に `python main.py` で手動テストを実施
- 1フェーズ = 1セッションで実装

---

## Phase 1: 基盤 - Undo/Redo + キーボードショートカット
> 編集操作の安全性確保。最も影響度が高く、独立して実装可能。

### 1-1. Undo/Redoスタック
- **対象ファイル**: `ui/editor_widget.py`
- **方針**: `QUndoStack` + `QUndoCommand` パターン
  - 翻訳セル編集時に `QUndoCommand` サブクラスをpush
  - `undo()` で前のテキストに復元、`redo()` で新テキストに再適用
  - `translationChanged` シグナルはコマンド内でemit
- **追加要素**:
  - `EditorWidget` に `self.undo_stack = QUndoStack(self)`
  - `QTableWidget.cellChanged` ではなく、編集完了時にコマンドをpush
  - ツールバーに Undo/Redo ボタン追加（`main_window.py`）

### 1-2. キーボードショートカット
- **対象ファイル**: `ui/main_window.py`, `ui/editor_widget.py`
- **マッピング**:
  - `Ctrl+Z` → Undo
  - `Ctrl+Y` / `Ctrl+Shift+Z` → Redo
  - `Ctrl+S` → セッション保存
  - `Ctrl+F` → 検索フォーカス
  - `Escape` → フィルタークリア

### テスト項目
- [ ] セル編集 → Ctrl+Z → 元に戻る
- [ ] 複数セル連続編集 → 複数回Undo → 全て戻る
- [ ] Redo → 編集が再適用される
- [ ] 翻訳スレッド完了後のupdate_translationsはUndoスタックをクリア

---

## Phase 2: 翻訳品質レビュー + 差分表示
> 翻訳結果の可視化と品質管理。Phase 1の完了後が望ましい（Undoと連携）。

### 2-1. 翻訳差分表示
- **対象ファイル**: `ui/editor_widget.py`
- **方針**:
  - 翻訳セルのツールチップに原文を表示（既存の軽量対応）
  - 「差分表示」トグルボタン追加 → ON時、翻訳列を2行表示（原文/翻訳）
  - 実装: `QTableWidget.setSpan()` ではなく、カスタム `QStyledItemDelegate` で描画
- **カスタムDelegate**:
  - `TranslationDelegate.paint()` で上半分に原文（薄い色）、下半分に翻訳文

### 2-2. 品質レビューマーカー
- **対象ファイル**: `ui/editor_widget.py`, `logic/translator.py`
- **方針**:
  - `validate_translation` のissuesをセルに紐付け（`Qt.UserRole+1` に格納）
  - 警告ありセルの背景をオレンジに変更
  - エディタツールバーに「要確認フィルター」追加（filter_comboに項目追加）
  - 右クリックメニューに「確認済みにする」追加 → 背景を通常の緑に
- **データ構造**:
  ```python
  self.review_status = {}  # {key: {"issues": [...], "reviewed": bool}}
  ```

### テスト項目
- [ ] 翻訳後、警告あり行がオレンジで表示される
- [ ] 差分表示トグルで原文/翻訳の切り替え
- [ ] 「確認済み」にすると緑に戻る
- [ ] 「要確認」フィルターで警告行のみ表示

---

## Phase 3: 設定の動的化 + 多言語対応
> インフラ改善。Phase 1,2と独立しているが、設定UIを変更するため注意。

### 3-1. pack_format自動判定
- **対象ファイル**: `ui/settings_dialog.py`, `logic/file_handler.py`
- **方針**:
  - 設定に「Minecraftバージョン」コンボボックス追加
  - バージョン→pack_formatのマッピングテーブルを定義
  ```python
  PACK_FORMATS = {
      "1.20.x": 15,
      "1.21.0-1.21.3": 34,
      "1.21.4": 42,
  }
  ```
  - `save_resource_pack` / `save_merged_resource_pack` にpack_format引数追加

### 3-2. モデル一覧の動的取得
- **対象ファイル**: `ui/settings_dialog.py`
- **方針**:
  - 設定ダイアログ表示時にOpenRouter API `/api/v1/models` を叩いて一覧取得
  - 取得失敗時はハードコードリストにフォールバック
  - 取得したモデルはキャッシュ（QSettingsにJSON保存、24時間TTL）

### 3-3. 多言語対応（ターゲット言語選択）
- **対象ファイル**: `ui/settings_dialog.py`, `logic/translator.py`, `logic/file_handler.py`
- **方針**:
  - 設定に「翻訳先言語」コンボボックス追加
  - プロンプト内の "Japanese" を動的に置換
  - `ja_jp` → 選択言語のコードに置換（`en_us` → `zh_cn` 等）
- **言語マッピング**:
  ```python
  TARGET_LANGUAGES = {
      "ja_jp": ("Japanese", "日本語"),
      "zh_cn": ("Simplified Chinese", "簡体字中国語"),
      "ko_kr": ("Korean", "韓国語"),
      "fr_fr": ("French", "フランス語"),
  }
  ```

### テスト項目
- [ ] MC 1.21.4選択時にpack_format=42で出力される
- [ ] モデル一覧がAPIから取得・表示される
- [ ] ターゲット言語を中国語に変更→プロンプトが "Simplified Chinese" になる
- [ ] ターゲット言語変更後の出力ファイル名が正しい（`zh_cn.json`）

---

## Phase 4: ポリッシュ - 進捗レジューム + 用語集自動化 + 部分翻訳UX
> 残りの改善項目。Phase 3の多言語設定を前提とする部分あり。

### 4-1. 進捗レジュームのUI反映
- **対象ファイル**: `ui/main_window.py`
- **方針**:
  - `partial_save` シグナルのハンドラで `editor.update_translations()` を呼ぶ
  - 翻訳完了後に「N件の翻訳を自動保存しました」ステータスバー表示
  - 中断→再開時に「前回のN件は翻訳済み、残りM件を翻訳します」確認ダイアログ

### 4-2. 用語集の自動提案
- **対象ファイル**: `ui/main_window.py`, `ui/editor_widget.py`
- **方針**:
  - 翻訳完了後、原文→訳文のペアから高頻度の固有名詞を自動抽出
  - 「以下の用語を辞書に追加しますか？」ダイアログ表示
  - 既存の `AITermExtractorThread` をベースに、ローカルでの簡易抽出も追加
  - 抽出ロジック: 原文で2回以上出現し、訳文が一貫している用語を候補とする

### 4-3. 部分翻訳のUX改善
- **対象ファイル**: `ui/editor_widget.py`
- **方針**:
  - 右クリックメニューの「選択範囲を翻訳」の導線を改善
  - 選択時のツールチップに「右クリックで翻訳可能」を表示
  - ステータスバーに「N行選択中」を表示

### テスト項目
- [ ] 翻訳中断→再開で「残りM件」ダイアログが表示される
- [ ] 翻訳完了後に用語提案ダイアログが表示される
- [ ] 選択行の右クリックで「選択範囲を翻訳」が分かりやすい

---

## ファイル変更影響マトリクス

| ファイル | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|----------|---------|---------|---------|---------|
| `ui/editor_widget.py` | **大** | **大** | - | 中 |
| `ui/main_window.py` | 中 | 小 | - | **大** |
| `ui/settings_dialog.py` | - | - | **大** | - |
| `logic/translator.py` | - | 中 | 中 | - |
| `logic/file_handler.py` | - | - | 中 | - |
| `logic/glossary.py` | - | - | - | 小 |

## 実装順序の依存関係

```
Phase 1 (Undo/Redo + Shortcut)
    ↓
Phase 2 (Review + Diff)  ← Undoスタック前提
    ↓
Phase 3 (Settings)       ← 独立可能だがPhase 2と同時変更ファイルなし
    ↓
Phase 4 (Polish)         ← Phase 1-3完了後
```

Phase 2と3は独立しているため、並行実装も可能。
