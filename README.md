# MinecraftModTranslator

<div align="center">

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)
![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![PySide6](https://img.shields.io/badge/PySide6-GUI-41CD52?logo=qt&logoColor=white)

**Minecraft MOD/Modpackの日本語化を強力に支援するデスクトップアプリケーション**

[ダウンロード](#-ダウンロード) • [使い方](#-使い方) • [機能一覧](#-主な機能) • [開発者向け](#-開発者向け)

</div>

---

## 📖 概要

MinecraftModTranslatorは、Minecraft MODやModpackの翻訳作業を効率化するためのGUIツールです。

OpenRouter API経由で様々なAIモデル（GPT-4o、Claude、Geminiなど）を活用し、大量のテキストを素早く高品質に翻訳できます。翻訳結果はMinecraftで使用できるリソースパック形式で出力されます。

## ✨ 主な機能

### 🎮 MOD翻訳
- **JAR/ZIPファイル対応**: MODのJARファイルから言語ファイル（`en_us.json`）を自動読み込み
- **Minecraftディレクトリ対応**: `mods`フォルダ内のすべてのMODを一括読み込み
- **リソースパック出力**: 翻訳結果を`ja_jp.json`形式でリソースパックとして出力
- **既存翻訳のインポート**: リソースパックから既存の翻訳をインポート可能

### 📜 FTB Quests対応
- **SNBTファイル解析**: FTB QuestsのクエストデータをSNBT形式から直接読み込み
- **クエスト翻訳**: タイトル、説明文、サブタイトルなど全テキストを翻訳
- **SNBT変換**: 翻訳キーをSNBTファイルに直接適用（バックアップ自動作成）
- **KubeJS対応**: `kubejs/data/ftbquests`配下のクエストファイルも検出

### 🤖 AI翻訳
- **[OpenRouter](https://openrouter.ai/) API対応**: 様々なAIモデルを統一的に利用可能
  - Google Gemini 2.0 Flash (無料)
  - OpenAI GPT-4o / GPT-4o mini
  - Anthropic Claude 3.5 Sonnet / Claude 3 Haiku
  - その他多数
- **並列翻訳**: 複数のリクエストを同時に送信して高速化
- **レート制限対応**: 自動リトライ機能付き
- **翻訳中断機能**: 翻訳途中で停止可能（それまでの翻訳は保持）

### 📚 翻訳支援機能
- **翻訳メモリ**: 過去の翻訳を自動保存し、同じ原文に対して一貫した翻訳を提供
- **辞書（用語集）**: カスタム辞書で固有名詞や専門用語の翻訳を統一
- **AI用語抽出**: AIが原文から重要な用語を自動抽出して辞書に追加
- **手動編集**: エディタで翻訳を直接編集可能

### 🔍 検索・フィルタリング
- **全MOD横断検索**: キーワードで全MODを横断検索
- **フィルタリング機能**: 
  - 未翻訳のみ表示
  - 原文と同じ翻訳を表示
  - ローマ字を含む翻訳を表示（色コード等は除外）
- **一括翻訳**: フィルタリングされたMODをまとめて翻訳

### 💾 セッション管理
- **セッション保存**: 読み込んだMOD情報を自動保存
- **セッション復元**: 前回のセッションを復元可能

---

## 📥 ダウンロード

[Releases](https://github.com/TATUNOKO00122/MinecraftModTranslatorTest/releases)から最新版のexeファイルをダウンロードしてください。

> **Note**: 実行ファイルはPyInstallerでビルドされているため、初回起動時にウイルス対策ソフトの警告が表示される場合があります。

---

## 🚀 使い方

### 基本的な流れ

```
1. アプリケーションを起動
2. MOD/Modpackを読み込み
3. 設定でAPIキーを入力
4. 翻訳を実行
5. リソースパックとして出力
```

### 詳細な手順

#### 1️⃣ MODを読み込み

以下のいずれかの方法でMODを読み込みます：

- **ドラッグ＆ドロップ**: MODファイル（.jar）をウィンドウにドラッグ
- **Minecraftディレクトリ**: `.minecraft`フォルダをドラッグ（modsフォルダ内を一括読み込み）
- **メニュー**: 「開く」ボタンから選択

#### 2️⃣ 設定を開く（⚙️アイコン）

| 項目 | 説明 |
|------|------|
| OpenRouter APIキー | AI翻訳に必要なAPIキー（[OpenRouter](https://openrouter.ai/)で取得） |
| 使用モデル | 翻訳に使用するAIモデル |
| 並列リクエスト数 | 同時APIリクエスト数（無料モデル: 1-2推奨） |
| デフォルト出力先 | リソースパックの出力先フォルダ |

#### 3️⃣ 翻訳を実行

- **選択翻訳**: 行を選択して「翻訳」ボタン
- **一括翻訳**: 「すべて翻訳」ボタンで表示中の全行を翻訳
- **全MOD一括翻訳**: MOD一覧の「一括翻訳」ボタンでフィルタリングされた全MODを翻訳

#### 4️⃣ リソースパック出力

「リソースパック作成」ボタンで翻訳結果を出力。Minecraftの`resourcepacks`フォルダに配置して使用できます。

### FTB Questsの翻訳

1. Modpackのフォルダをドラッグ＆ドロップ
   - `config/ftbquests/quests` を自動検出
   - `kubejs/data/ftbquests/quests` も検出
2. クエストファイルが読み込まれ、翻訳対象として表示
3. 翻訳を実行
4. **「SNBT適用」ボタン**でSNBTファイルを変換
   - 原本は自動でバックアップされます（`.backup_YYYYMMDD_HHMMSS`）

### 翻訳のインポート

既存のリソースパックから翻訳をインポートできます：

1. リソースパック（.zip または フォルダ）をドラッグ＆ドロップ
2. 「リソースパックとしてインポート」を選択
3. 既存の翻訳がエディタに反映されます

---

## 📋 必要要件

### 実行ファイル版（推奨）
- Windows 10/11
- インターネット接続（AI翻訳機能使用時）
- OpenRouter APIキー

### ソースから実行する場合
- Python 3.10以上
- 依存パッケージ

```bash
# リポジトリをクローン
git clone https://github.com/TATUNOKO00122/MinecraftModTranslatorTest.git
cd MinecraftModTranslatorTest

# 仮想環境を作成（推奨）
python -m venv .venv
.venv\Scripts\activate

# 依存関係をインストール
pip install -r requirements.txt

# 実行
python main.py
```

---

## 🛠️ 開発者向け

### プロジェクト構造

```
MinecraftModTranslator/
├── main.py                 # エントリーポイント
├── ui/                     # UIコンポーネント
│   ├── main_window.py      # メインウィンドウ
│   ├── editor_widget.py    # 翻訳エディタ
│   ├── settings_dialog.py  # 設定ダイアログ
│   ├── glossary_dialog.py  # 用語集ダイアログ
│   ├── term_extraction_dialog.py  # AI用語抽出ダイアログ
│   └── styles.qss          # スタイルシート
├── logic/                  # ビジネスロジック
│   ├── file_handler.py     # ファイル読み込み/書き出し
│   ├── ftbquest_handler.py # FTB Quests処理
│   ├── translator.py       # AI翻訳処理
│   ├── glossary.py         # 用語集管理
│   ├── translation_memory.py  # 翻訳メモリ管理
│   └── term_extractor.py   # AI用語抽出
├── build_exe.bat           # 実行ファイルビルドスクリプト
├── MinecraftModTranslator.spec  # PyInstaller設定
└── README.md
```

### 実行ファイルのビルド

```bash
# PyInstallerでビルド
python -m PyInstaller MinecraftModTranslator.spec

# または
build_exe.bat
```

ビルドされた実行ファイルは `dist/` フォルダに出力されます。

### 主要な技術スタック

| 技術 | 用途 |
|------|------|
| PySide6 | GUIフレームワーク（Qt for Python） |
| OpenRouter API | AI翻訳APIアクセス |
| ftb-snbt-lib | SNBT形式の解析・変換 |
| PyInstaller | 実行ファイル生成 |

---

## 💡 Tips

### API料金について
- OpenRouterでは複数の無料モデル（Gemini 2.0 Flash等）が利用可能
- 有料モデル使用時は[OpenRouter](https://openrouter.ai/)で料金を確認してください

### 翻訳品質を上げるには
1. **用語集を活用**: 固有名詞（アイテム名、MOD名等）を事前に登録
2. **AI用語抽出**: 翻訳前に用語を抽出して統一
3. **翻訳メモリ**: 同じ原文は自動で同じ翻訳が適用されます

### 大量のMODを翻訳するには
1. 「未翻訳のみ」フィルタを適用
2. 「一括翻訳」ボタンをクリック
3. 翻訳完了まで待機（途中で停止も可能）

---

## 📜 ライセンス

MIT License - 詳細は[LICENSE](LICENSE)を参照してください。

---

## 🙏 謝辞

- [OpenRouter](https://openrouter.ai/) - 複数のAIモデルへの統一的なAPIアクセス
- [ftb-snbt-lib](https://github.com/ftbteam/ftb-snbt-lib) - SNBT形式の解析
- [PySide6](https://www.qt.io/qt-for-python) - クロスプラットフォームGUIフレームワーク
- [PyInstaller](https://pyinstaller.org/) - Python実行ファイル生成

---

## 📝 更新履歴

### v1.0.0
- 初回リリース
- MOD翻訳機能
- FTB Quests対応
- AI翻訳（OpenRouter API）
- 翻訳メモリ・用語集機能
- リソースパック出力
