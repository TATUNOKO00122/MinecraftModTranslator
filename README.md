# MinecraftModTranslator

Minecraft MOD/リソースパックの日本語化を支援するデスクトップアプリケーションです。

## 機能

- **MOD翻訳**: JARファイル内の言語ファイル（`en_us.json`）を日本語に翻訳
- **FTB Quests対応**: FTB QuestsのSNBTファイルを翻訳
- **AI翻訳**: OpenAI API（GPT-4o mini等）を使用した自動翻訳
- **翻訳メモリ**: 過去の翻訳を記憶し、一貫性のある翻訳を提供
- **用語集**: カスタム用語集による統一された翻訳
- **リソースパック出力**: 翻訳結果をリソースパックとして出力

## 必要要件

- Python 3.10以上
- OpenAI APIキー（AI翻訳機能を使用する場合）

## インストール

```bash
# リポジトリをクローン
git clone https://github.com/TATUNOKO00122/MinecraftModTranslator.git
cd MinecraftModTranslator

# 仮想環境を作成（推奨）
python -m venv .venv
.venv\Scripts\activate

# 依存関係をインストール
pip install -r requirements.txt
```

## 使い方

```bash
python main.py
```

1. MODファイル（.jar）またはMinecraftディレクトリをドラッグ＆ドロップ
2. 設定からOpenAI APIキーを入力
3. 翻訳したいテキストを選択して翻訳ボタンをクリック
4. 「リソースパック作成」で翻訳結果を出力

## ライセンス

MIT License
