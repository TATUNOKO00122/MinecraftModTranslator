# MinecraftModTranslator

<div align="center">

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

**Minecraft MOD/Modpackの日本語化を強力に支援するデスクトップアプリケーション**

</div>

---

## 📖 概要

MinecraftModTranslatorは、Minecraft MODやModpackの翻訳作業を効率化するためのツールです。AI翻訳機能により、大量のテキストを素早く翻訳し、リソースパックとして出力できます。

## ✨ 主な機能

### 🎮 MOD翻訳
- **JAR/ZIPファイル対応**: MODのJARファイルから言語ファイル（`en_us.json`）を自動読み込み
- **Minecraftディレクトリ対応**: modsフォルダ内のすべてのMODを一括読み込み
- **リソースパック出力**: 翻訳結果を`ja_jp.json`形式でリソースパックとして出力

### 📜 FTB Quests対応
- **SNBTファイル解析**: FTB QuestsのクエストデータをSNBT形式から直接読み込み
- **クエスト翻訳**: タイトル、説明文、サブタイトルなど全テキストを翻訳
- **SNBT変換**: 翻訳キーをSNBTファイルに直接適用（バックアップ自動作成）

### 🤖 AI翻訳
- **OpenRouter API対応**: [OpenRouter](https://openrouter.ai/)経由で様々なAIモデルを使用可能

### 📚 翻訳支援機能
- **翻訳メモリ**: 過去の翻訳を自動保存し、同じ原文に対して一貫した翻訳を提供
- **辞書（用語集）**: カスタム辞書で固有名詞や専門用語の翻訳を統一
- **AI用語抽出**: AIが原文から重要な用語を自動抽出して辞書に追加

## 📥 ダウンロード

[Releases](https://github.com/TATUNOKO00122/MinecraftModTranslator/releases)から最新版のexeファイルをダウンロードしてください。

## 🚀 使い方

### 基本的な流れ

1. **アプリケーションを起動**
2. **MODを読み込み**
   - MODファイル（.jar）をドラッグ＆ドロップ
   - または、Minecraftディレクトリをドラッグ＆ドロップ（modsフォルダ内を一括読み込み）
3. **設定を開く**（歯車アイコン）
   - OpenRouter APIキーを入力
   - 使用するAIモデルを選択
4. **翻訳を実行**
   - 個別翻訳：行を選択して「翻訳」ボタン
   - 一括翻訳：「すべて翻訳」ボタン
5. **リソースパック出力**
   - 「リソースパック作成」ボタンで翻訳結果を出力

### FTB Questsの翻訳

1. Modpackのフォルダをドラッグ＆ドロップ
2. クエストファイルが自動検出されます
3. 翻訳後、「SNBT適用」ボタンでゲームに反映

## ⚙️ 設定項目

| 項目 | 説明 |
|------|------|
| OpenRouter APIキー | AI翻訳に必要なAPIキー |
| 使用モデル | 翻訳に使用するAIモデル |
| 並列リクエスト数 | 同時APIリクエスト数（無料モデル: 1-2推奨） |
| デフォルト出力先 | リソースパックの出力先フォルダ |

## 📋 必要要件

### 実行ファイル版（推奨）
- Windows 10/11
- インターネット接続（AI翻訳機能使用時）
- OpenRouter APIキー

### ソースから実行する場合
- Python 3.10以上
- 依存パッケージ（`requirements.txt`参照）

```bash
# リポジトリをクローン
git clone https://github.com/TATUNOKO00122/MinecraftModTranslator.git
cd MinecraftModTranslator

# 仮想環境を作成（推奨）
python -m venv .venv
.venv\Scripts\activate

# 依存関係をインストール
pip install -r requirements.txt

# 実行
python main.py
```

## 📜 ライセンス

MIT License - 詳細は[LICENSE](LICENSE)を参照してください。

## 🙏 謝辞

- [OpenRouter](https://openrouter.ai/) - 複数のAIモデルへの統一的なAPIアクセス
- [ftb-snbt-lib](https://github.com/ftbteam/ftb-snbt-lib) - SNBT形式の解析
- [PySide6](https://www.qt.io/qt-for-python) - クロスプラットフォームGUIフレームワーク
