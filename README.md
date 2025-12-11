# MinecraftModTranslator

<div align="center">

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![Platform](https://img.shields.io/badge/platform-Windows-lightgrey)

**Minecraft MOD/Modpackの日本語化を支援するデスクトップアプリケーション**

</div>

---

## 概要

MinecraftModTranslatorは、Minecraft MODやModpackの翻訳作業を効率化するためのGUIツールです。

OpenRouter API経由でAI翻訳を行い、翻訳結果はMinecraftで使用できるリソースパック形式で出力されます。

## 主な機能

### MOD翻訳
- **JAR/ZIPファイル対応**: MODのJARファイルから言語ファイル（`en_us.json`）を自動読み込み
- **Minecraftディレクトリ対応**: `mods`フォルダ内のすべてのMODを一括読み込み
- **リソースパック出力**: 翻訳結果を`ja_jp.json`形式でリソースパックとして出力
- **既存翻訳のインポート**: リソースパックから既存の翻訳をインポート可能

### FTB Quests対応
- **SNBTファイル解析**: FTB QuestsのクエストデータをSNBT形式から直接読み込み
- **クエスト翻訳**: タイトル、説明文、サブタイトルなど全テキストを翻訳
- **SNBT変換**: 翻訳キーをSNBTファイルに直接適用（バックアップ自動作成）

### 翻訳
- [OpenRouter](https://openrouter.ai/) APIのみ対応

### 翻訳支援機能
- **辞書（用語集）**: カスタム辞書で固有名詞や専門用語の翻訳を統一

---

## ダウンロード

[Releases](https://github.com/TATUNOKO00122/MinecraftModTranslator/releases)から最新版のexeファイルをダウンロードしてください。

---

## 使い方

### 基本的な流れ

1. アプリケーションを起動
2. MOD/Modpackを読み込み
3. 設定でAPIキーを入力
4. 翻訳を実行
5. リソースパックとして出力

### MODを読み込み

以下のいずれかの方法でMODを読み込みます：

- **ドラッグ&ドロップ**: MODファイル（.jar）をウィンドウにドラッグ
- **Minecraftディレクトリ**: `.minecraft`フォルダをドラッグ（modsフォルダ内を一括読み込み）
- **メニュー**: 「開く」ボタンから選択

### 設定

| 項目 | 説明 |
|------|------|
| OpenRouter APIキー | AI翻訳に必要なAPIキー（[OpenRouter](https://openrouter.ai/)で取得） |
| 使用モデル | 翻訳に使用するAIモデル |
| 並列リクエスト数 | 同時APIリクエスト数（無料モデル: 1-2推奨） |
| デフォルト出力先 | リソースパックの出力先フォルダ |

### 翻訳を実行

- **選択翻訳**: 行を選択して「翻訳」ボタン
- **一括翻訳**: 「すべて翻訳」ボタンで表示中の全行を翻訳

### リソースパック出力

「リソースパック作成」ボタンで翻訳結果を出力。Minecraftの`resourcepacks`フォルダに配置して使用できます。

### FTB Questsの翻訳

1. Modpackのフォルダをドラッグ&ドロップ
   - `config/ftbquests/quests` を自動検出
2. クエストファイルが読み込まれ、翻訳対象として表示
3. 翻訳を実行
4. 「SNBT適用」ボタンでSNBTファイルを変換（原本は自動でバックアップ）
