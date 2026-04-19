import re
import time
import json
from collections import defaultdict, Counter

import requests
from PySide6.QtCore import QThread, Signal

_COMMON_WORDS = frozenset({
    'the', 'is', 'are', 'was', 'were', 'a', 'an', 'and', 'or',
    'of', 'in', 'to', 'for', 'with', 'on', 'at', 'by', 'from',
    'it', 'this', 'that', 'has', 'have', 'had', 'not', 'but',
    'can', 'will', 'your', 'you', 'be', 'do', 'if', 'so', 'no',
    'all', 'any', 'its', 'my', 'me', 'we', 'he', 'she', 'they',
    'them', 'his', 'her', 'our', 'us', 'been', 'being', 'did',
    'get', 'got', 'may', 'must', 'shall', 'should', 'would',
    'could', 'than', 'then', 'there', 'here', 'where', 'when',
    'how', 'what', 'which', 'who', 'whom', 'each', 'every',
    'own', 'other', 'more', 'some', 'such', 'only', 'same',
    'also', 'just', 'very', 'even', 'still', 'already', 'too',
    'into', 'over', 'after', 'before', 'between', 'under',
    'about', 'up', 'out', 'down', 'off', 'above', 'below',
    'through', 'during', 'without', 'within', 'along', 'upon',
    'while', 'because', 'until', 'since', 'although', 'though',
})

_SKIP_TERMS_LOWER = frozenset({
    'minecraft', 'mojang', 'forge', 'fabric', 'quilt', 'neoforge',
    'java', 'bedrock', 'resource', 'pack', 'mod', 'mods', 'config',
    'version', 'update', 'changelog', 'license', 'copyright',
    'github', 'curseforge', 'modrinth', 'discord', 'patreon',
    'paypal', 'bitcoin', 'ethereum', 'http', 'https', 'www',
    'false', 'true', 'null', 'none', 'key', 'value', 'type',
    'name', 'id', 'tag', 'path', 'file', 'folder', 'directory',
    'string', 'integer', 'float', 'double', 'boolean', 'list',
    'description', 'comment', 'tooltip', 'lore', 'text', 'message',
    'desc', 'title', 'info', 'tip', 'note', 'warn', 'error',
    'block', 'item', 'entity', 'fluid', 'enchantment', 'potion',
    'effect', 'biome', 'dimension', 'structure', 'feature',
    'advancement', 'recipe', 'loot', 'damage', 'heal', 'attack',
    'defense', 'speed', 'health', 'mana', 'energy', 'level',
    'tier', 'rarity', 'category', 'group', 'slot', 'container',
})

_TITLE_CASE_NOISE = frozenset({
    'The Player', 'This Item', 'Each Level', 'Per Level',
    'All Players', 'No Damage', 'After Death', 'Before Use',
    'Right Click', 'Left Click', 'Shift Right', 'When Held',
    'While Held', 'If Used', 'When Broken', 'On Hit',
    'On Kill', 'On Use', 'To Use', 'To Craft', 'Can Be',
    'Has Been', 'Will Be', 'Is Not', 'Do Not', 'Does Not',
    'In Order', 'At Least', 'At Most', 'Up To', 'Out Of',
})

_VERB_STARTERS = frozenset({
    'deals', 'grants', 'gives', 'takes', 'causes',
    'applies', 'removes', 'increases', 'decreases',
    'reduces', 'adds', 'sets', 'spawns', 'summons',
    'teleports', 'heals', 'damages', 'kills',
})

_COLOR_CODE_RE = re.compile(r'[&§]([0-9a-fklmno])([^&§]*?)[&§]r')
_TITLE_CASE_RE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b')
_SINGLE_CAP_WORD_RE = re.compile(r'\b([A-Z][a-z]{2,})\b')
_BRACKET_TERM_RE = re.compile(r'[\[<]([A-Z][A-Za-z\s]{2,})[\]>]')
_QUOTED_TERM_RE = re.compile(r'["\u201c]([A-Z][A-Za-z\s]{2,}?)["\u201d]')

_API_URL = "https://openrouter.ai/api/v1/chat/completions"
_MAX_RETRIES = 3


def _parse_api_json_response(content):
    """AI API応答からJSONを抽出する。コードフェンス除去 + フォールバック。"""
    content = content.strip()
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {}


def _call_openrouter_api(api_key, model, system_prompt, user_prompt,
                         temperature=0.1, timeout=60,
                         title="Minecraft MOD Translator"):
    """OpenRouter APIを呼び出し、レスポンステキストを返す。HTTP 429時にリトライ。"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Title": title,
    }
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }

    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.post(_API_URL, headers=headers, json=data, timeout=timeout)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except requests.exceptions.HTTPError:
            if response.status_code == 429 and attempt < _MAX_RETRIES - 1:
                wait = 2 ** (attempt + 1)
                print(f"[API] Rate limited, retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise

    return ""


def _is_valid_term_candidate(text, existing_glossary, strict=False):
    """用語候補の妥当性チェック。

    Args:
        strict: True の場合、SKIP_TERMS、TITLE_CASE_NOISE、動詞始まり等の
                追加フィルタを適用（原文からの頻出語抽出向け）
    """
    stripped = text.strip()
    if len(stripped) < 3 or len(stripped) > 60:
        return False
    if not re.search(r'[a-zA-Z]', stripped):
        return False

    lower = stripped.lower()
    if lower in existing_glossary or stripped in existing_glossary:
        return False

    if strict:
        if lower in _SKIP_TERMS_LOWER:
            return False
        if stripped in _TITLE_CASE_NOISE:
            return False
        if re.match(r'^[A-Z][a-z]+$', stripped) and lower in _COMMON_WORDS:
            return False
        word_list = lower.split()
        if len(word_list) >= 2 and word_list[0] in _VERB_STARTERS:
            return False

    words = set(lower.split())
    if words <= _COMMON_WORDS:
        return False

    return True


def extract_all_term_candidates(original_items, translated_items, existing_glossary=None):
    """
    一貫した用語と翻訳ブレのある用語を両方抽出する（ローカル、API不要）。

    Returns:
        tuple: (consistent_terms, inconsistent_terms)
            consistent_terms: dict[str, str] - {original: translation}
            inconsistent_terms: dict[str, dict] -
                {original: {"most_common": str, "all": [str], "count": int}}
    """
    if existing_glossary is None:
        existing_glossary = {}

    orig_to_translations = defaultdict(list)

    for key, original in original_items.items():
        if key in translated_items and translated_items[key]:
            trans = translated_items[key]
            if str(original) != str(trans):
                orig_to_translations[str(original)].append(str(trans))

    consistent = {}
    inconsistent = {}

    for original, translations in orig_to_translations.items():
        if len(translations) < 2:
            continue
        if not _is_valid_term_candidate(original, existing_glossary, strict=False):
            continue

        unique_trans = set(translations)

        if len(unique_trans) == 1:
            consistent[original] = translations[0]
        else:
            counter = Counter(translations)
            most_common = counter.most_common(1)[0][0]
            inconsistent[original] = {
                "most_common": most_common,
                "all": sorted(unique_trans),
                "count": len(translations),
            }

    return consistent, inconsistent


def extract_frequent_terms_from_original(original_items, min_count=2,
                                         existing_glossary=None):
    """
    翻訳前の原文のみから頻出する固有名詞候補を抽出する（API不要・ローカル処理）。

    抽出戦略:
      1. カラーコード付きテキスト（&eEverbright&r 等）— MOD固有名詞の信頼度が高い
      2. ブラケット/引用符内の語
      3. Title Case 複合語（Blue Journal, Night Lich 等）
      4. lang key の末尾セグメント（item.minecraft.diamond_sword → Diamond Sword）
      5. 大文字始まりの単語（出現数が多いもの、フォールバック）

    Returns:
        list[tuple[str, int, list[str]]] — [(term, count, sample_keys), ...]
            出現回数降順でソート済み
    """
    if existing_glossary is None:
        existing_glossary = {}

    term_sources = defaultdict(lambda: {"count": 0, "keys": [], "priority": 0})

    for key, text in original_items.items():
        text = str(text)
        if not text:
            continue

        found_in_entry = set()

        for color, content in _COLOR_CODE_RE.findall(text):
            content = content.strip()
            if _is_valid_term_candidate(content, existing_glossary, strict=True):
                found_in_entry.add(content)
                if term_sources[content]["priority"] < 5:
                    term_sources[content]["priority"] = 5

        for match in _BRACKET_TERM_RE.finditer(text):
            term = match.group(1).strip()
            if _is_valid_term_candidate(term, existing_glossary, strict=True):
                found_in_entry.add(term)
                if term_sources[term]["priority"] < 4:
                    term_sources[term]["priority"] = 4

        for match in _QUOTED_TERM_RE.finditer(text):
            term = match.group(1).strip()
            if _is_valid_term_candidate(term, existing_glossary, strict=True):
                found_in_entry.add(term)
                if term_sources[term]["priority"] < 4:
                    term_sources[term]["priority"] = 4

        for match in _TITLE_CASE_RE.finditer(text):
            term = match.group(1).strip()
            if _is_valid_term_candidate(term, existing_glossary, strict=True):
                found_in_entry.add(term)
                if term_sources[term]["priority"] < 3:
                    term_sources[term]["priority"] = 3

        key_last = _extract_term_from_key(key)
        if key_last and _is_valid_term_candidate(key_last, existing_glossary, strict=True):
            found_in_entry.add(key_last)
            if term_sources[key_last]["priority"] < 2:
                term_sources[key_last]["priority"] = 2

        if not found_in_entry:
            for match in _SINGLE_CAP_WORD_RE.finditer(text):
                term = match.group(1)
                if _is_valid_term_candidate(term, existing_glossary, strict=True):
                    found_in_entry.add(term)
                    if term_sources[term]["priority"] < 1:
                        term_sources[term]["priority"] = 1

        for term in found_in_entry:
            src = term_sources[term]
            src["count"] += 1
            if len(src["keys"]) < 5:
                src["keys"].append(key)

    scored = [
        (term, src["count"], src["keys"], src["priority"])
        for term, src in term_sources.items()
        if src["count"] >= min_count
    ]
    scored.sort(key=lambda x: (x[1] * x[3], x[1], x[0]), reverse=True)

    return [(term, count, keys) for term, count, keys, _ in scored]


def _extract_term_from_key(key):
    """lang key の末尾セグメントから人が読める形式の名前を抽出する。"""
    segments = key.split('.')
    last = segments[-1] if segments else ''
    if not last or len(last) < 3:
        return None
    if last.startswith('_'):
        last = last[1:]
    if not any(c.isalpha() for c in last):
        return None
    readable = last.replace('_', ' ').strip()
    readable = ' '.join(w.capitalize() for w in readable.split())
    words = set(readable.lower().split())
    if words <= _COMMON_WORDS or words <= _SKIP_TERMS_LOWER:
        return None
    return readable if len(readable) >= 3 else None


class AITermExtractorThread(QThread):
    """AIで翻訳対から固有名詞を抽出するスレッド。"""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    DEFAULT_MODEL = "deepseek/deepseek-chat"

    def __init__(self, original_items, translated_items, api_key, model=None, existing_glossary=None):
        super().__init__()
        self.original_items = original_items
        self.translated_items = translated_items
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.existing_glossary = existing_glossary or {}
        self.is_running = True

    def run(self):
        try:
            self.progress.emit("用語抽出の準備中...")

            pairs_to_analyze = []
            for key, original in self.original_items.items():
                if key in self.translated_items:
                    translated = self.translated_items[key]
                    if original and translated and str(original) != str(translated):
                        pairs_to_analyze.append({
                            "original": str(original),
                            "translated": str(translated)
                        })

            if not pairs_to_analyze:
                self.finished.emit({})
                return

            batch_size = 20
            total_batches = (len(pairs_to_analyze) + batch_size - 1) // batch_size
            all_extracted = {}

            for i in range(0, len(pairs_to_analyze), batch_size):
                if not self.is_running:
                    break

                batch = pairs_to_analyze[i:i + batch_size]
                current_batch_num = (i // batch_size) + 1

                self.progress.emit(f"AIで用語を抽出中... (バッチ {current_batch_num}/{total_batches})")

                try:
                    extracted = self._call_ai_for_extraction(batch)
                    for k, v in extracted.items():
                        if k not in self.existing_glossary:
                            all_extracted[k] = v
                except Exception as e:
                    print(f"Batch {current_batch_num} extraction failed: {e}")

                time.sleep(1.0)

            self.finished.emit(all_extracted)

        except Exception as e:
            self.error.emit(str(e))

    def _call_ai_for_extraction(self, pairs):
        pairs_text = json.dumps(pairs, ensure_ascii=False, indent=2)

        system_prompt = (
            "You are a term extraction assistant for Minecraft mod translations.\n"
            "Your task is to extract proper nouns (地名, アイテム名, モンスター名, スキル名, etc.) "
            "from English-Japanese translation pairs.\n\n"
            "Rules:\n"
            "1. Extract ONLY proper nouns that should be consistently translated:\n"
            "   - Location names (Everbright, The Otherside, Underdark, etc.)\n"
            "   - Item names (Blue Journal, Soul Star, etc.)\n"
            "   - Monster/NPC names (Night Lich, Invoker, etc.)\n"
            "   - Skill/Ability names\n"
            "   - Dimension names\n\n"
            "2. Do NOT extract:\n"
            "   - MOD names (Mine and Slash, FTB Teams, Lightman's Currency, etc.)\n"
            "   - Common words that are not proper nouns\n"
            "   - Technical terms or UI labels\n\n"
            "3. Output format: JSON object with English term as key, Japanese translation as value.\n"
            "   Example: {\"Everbright\": \"エバーブライト\", \"Blue Journal\": \"青い日誌\"}\n\n"
            "4. Output ONLY the JSON object, no markdown formatting or explanation."
        )

        user_prompt = (
            f"Extract proper nouns from these English-Japanese translation pairs:\n\n"
            f"{pairs_text}\n\n"
            "Return a JSON object mapping English proper nouns to their Japanese translations."
        )

        content = _call_openrouter_api(
            self.api_key, self.model, system_prompt, user_prompt,
            temperature=0.1, timeout=60,
            title="Minecraft MOD Translator - Term Extraction"
        )
        return _parse_api_json_response(content)

    def stop(self):
        self.is_running = False


class AITermClassifierThread(QThread):
    """AIで候補リストを仕分けし、Minecraft固有名詞として翻訳すべきものを抽出する。"""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, candidates, api_key, model=None, batch_size=80):
        super().__init__()
        self.candidates = candidates
        self.api_key = api_key
        self.model = model or "deepseek/deepseek-chat"
        self.batch_size = batch_size
        self.is_running = True

    def run(self):
        try:
            self.progress.emit("AI仕分けを準備中...")

            all_classified = {}
            total = len(self.candidates)
            total_batches = (total + self.batch_size - 1) // self.batch_size

            for i in range(0, total, self.batch_size):
                if not self.is_running:
                    break

                batch = self.candidates[i:i + self.batch_size]
                batch_num = i // self.batch_size + 1
                self.progress.emit(f"AI仕分け中... ({batch_num}/{total_batches})")

                try:
                    result = self._classify_batch(batch)
                    all_classified.update(result)
                except Exception as e:
                    print(f"Classification batch {batch_num} failed: {e}")

                time.sleep(0.5)

            self.finished.emit(all_classified)

        except Exception as e:
            self.error.emit(str(e))

    def _classify_batch(self, batch):
        candidates_text = "\n".join(
            f"- {term} (出現{count}回)" for term, count, _ in batch
        )

        system_prompt = (
            "あなたはMinecraft MODの翻訳アシスタントです。\n"
            "与えられた英語の語句リストのうち、Minecraft MODの文脈で**固有名詞**"
            "（アイテム名、Mob名、ボス名、バイオーム名、ディメンション名、"
            "スキル名、クラス名、ストーリー固有名詞など）として翻訳すべきものだけを抽出してください。\n\n"
            "**除外するもの:**\n"
            "- 一般的な英単語やフレーズ (\"Blue\", \"Level\", \"Damage\" 等)\n"
            "- UI ラベルや汎用メッセージ (\"Click to\", \"Requires\" 等)\n"
            "- MOD名そのもの\n"
            "- 技術的な識別子\n"
            "- 文脈なしでは固有名詞と判断できない短い単語\n\n"
            "出力形式: JSONオブジェクトのみ（キー: 英語原文、値: 日本語訳）\n"
            "マークダウンコードブロックは使わない。\n"
            "固有名詞でないものは出力に含めない。"
        )

        user_prompt = (
            f"以下の語句リストから、Minecraft MODの固有名詞として翻訳すべきものだけを抽出し、"
            f"日本語訳を付けてください:\n\n{candidates_text}"
        )

        content = _call_openrouter_api(
            self.api_key, self.model, system_prompt, user_prompt,
            temperature=0.1, timeout=60,
            title="Minecraft MOD Translator - Term Classification"
        )
        return _parse_api_json_response(content)

    def stop(self):
        self.is_running = False


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

        content = _call_openrouter_api(
            self.api_key, self.model, system_prompt, user_prompt,
            temperature=0.3, timeout=60,
            title="Minecraft MOD Translator - Frequent Term Translation"
        )
        return _parse_api_json_response(content)

    def stop(self):
        self.is_running = False
