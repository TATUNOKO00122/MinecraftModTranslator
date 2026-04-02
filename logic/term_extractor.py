import re
import time
from collections import defaultdict, Counter
import requests
import json
from PySide6.QtCore import QThread, Signal

def extract_color_code_terms(original_text, translated_text):
    """
    Extract terms from color codes in original and translated text.
    Returns a dict of {original_term: translated_term} for terms found in both.
    
    Supports:
    - &e...&r, &9...&r, etc. (ampersand format)
    - §e...§r, §9...§r, etc. (section sign format)
    
    Matches terms by color code type (same color code = same term).
    """
    # Pattern to match color-coded text with the color code captured
    # Group 1: color code (e, 9, a, etc.), Group 2: content
    pattern = r'[&§]([0-9a-fklmno])([^&§]*?)[&§]r'
    
    # Extract with color codes
    original_matches = re.findall(pattern, original_text)
    translated_matches = re.findall(pattern, translated_text)
    
    # Group by color code
    original_by_color = defaultdict(list)
    translated_by_color = defaultdict(list)
    
    for color, content in original_matches:
        content = content.strip()
        if content:
            original_by_color[color].append(content)
    
    for color, content in translated_matches:
        content = content.strip()
        if content:
            translated_by_color[color].append(content)
    
    # Pair terms by color code and position within that color
    pairs = {}
    for color, orig_terms in original_by_color.items():
        trans_terms = translated_by_color.get(color, [])
        for i, orig_term in enumerate(orig_terms):
            if i < len(trans_terms):
                trans_term = trans_terms[i]
                # Only add if they're different (actual translation happened)
                # and if the original looks like English (contains Latin letters)
                if orig_term != trans_term and re.search(r'[a-zA-Z]', orig_term):
                    pairs[orig_term] = trans_term
    
    return pairs


def extract_terms_from_batch(original_items, translated_items):
    """
    Extract terms from a batch of translations.
    
    Args:
        original_items: dict of {key: original_text}
        translated_items: dict of {key: translated_text}
    
    Returns:
        dict of {original_term: translated_term}
    """
    all_pairs = {}
    
    for key, original_text in original_items.items():
        if key in translated_items:
            translated_text = translated_items[key]
            pairs = extract_color_code_terms(str(original_text), str(translated_text))
            
            # Merge pairs, preferring existing entries (first occurrence)
            for orig, trans in pairs.items():
                if orig not in all_pairs:
                    all_pairs[orig] = trans
    
    return all_pairs


def filter_new_terms(extracted_terms, existing_glossary):
    """
    Filter out terms that are already in the glossary.
    Also filters out terms that look like MOD names or technical identifiers.
    
    Args:
        extracted_terms: dict of {original_term: translated_term}
        existing_glossary: dict of existing glossary terms
    
    Returns:
        dict of new terms not in glossary
    """
    # MOD names and technical terms to skip
    skip_terms = {
        "Mine and Slash", "FTB Teams", "Lightman's Currency", "Lightman's Teams",
        "Minecraft", "Craft to Exile", "CTE2", "VR", "GUI", "API",
    }
    
    new_terms = {}
    
    for orig, trans in extracted_terms.items():
        # Skip if already in glossary
        if orig in existing_glossary:
            continue
        
        # Skip known MOD names
        if orig in skip_terms:
            continue
        
        # Skip very short terms (likely false positives)
        if len(orig) < 2:
            continue
        
        # Skip if it's just numbers or symbols
        if not re.search(r'[a-zA-Z]', orig):
            continue
        
        new_terms[orig] = trans
    
    return new_terms


class AITermExtractorThread(QThread):
    """Thread for extracting terms using AI (DeepSeek or other cheap models)."""
    
    finished = Signal(dict)  # {original: translated}
    error = Signal(str)
    progress = Signal(str)  # Status message
    
    # Default model for term extraction (cheap and fast)
    DEFAULT_MODEL = "deepseek/deepseek-chat"
    
    def __init__(self, original_items, translated_items, api_key, model=None, existing_glossary=None):
        """
        Args:
            original_items: dict of {key: original_text}
            translated_items: dict of {key: translated_text}
            api_key: OpenRouter API key
            model: Model to use (default: DeepSeek)
            existing_glossary: Existing glossary terms to exclude
        """
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
            
            # Prepare sample pairs for AI to analyze
            # We send pairs of original and translated text
            pairs_to_analyze = []
            for key, original in self.original_items.items():
                if key in self.translated_items:
                    translated = self.translated_items[key]
                    # Only calculate/add if translation differs and has content
                    if original and translated and str(original) != str(translated):
                        pairs_to_analyze.append({
                            "original": str(original),
                            "translated": str(translated)
                        })
            
            if not pairs_to_analyze:
                self.finished.emit({})
                return
            
            # Batch processing
            batch_size = 20 # User requested specific batch size
            total_batches = (len(pairs_to_analyze) + batch_size - 1) // batch_size
            all_extracted = {}
            
            import time
            
            for i in range(0, len(pairs_to_analyze), batch_size):
                if not self.is_running:
                    break
                    
                batch = pairs_to_analyze[i:i+batch_size]
                current_batch_num = (i // batch_size) + 1
                
                self.progress.emit(f"AIで用語を抽出中... (バッチ {current_batch_num}/{total_batches})")
                
                try:
                    # Call AI to extract terms
                    extracted = self._call_ai_for_extraction(batch)
                    
                    # Merge results
                    for k, v in extracted.items():
                        if k not in self.existing_glossary:
                            all_extracted[k] = v
                            
                except Exception as e:
                    print(f"Batch {current_batch_num} extraction failed: {e}")
                    # Continue to next batch even if one fails
                
                # Small delay to respect rate limits
                time.sleep(1.0)
            
            self.finished.emit(all_extracted)
            
        except Exception as e:
            self.error.emit(str(e))
    
    def _call_ai_for_extraction(self, pairs):
        """Call AI API to extract terms from translation pairs."""
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Minecraft MOD Translator - Term Extraction"
        }
        
        # Prepare the prompt
        pairs_text = json.dumps(pairs, ensure_ascii=False, indent=2)
        
        system_prompt = """You are a term extraction assistant for Minecraft mod translations.
Your task is to extract proper nouns (地名, アイテム名, モンスター名, スキル名, etc.) from English-Japanese translation pairs.

Rules:
1. Extract ONLY proper nouns that should be consistently translated:
   - Location names (Everbright, The Otherside, Underdark, etc.)
   - Item names (Blue Journal, Soul Star, etc.)
   - Monster/NPC names (Night Lich, Invoker, etc.)
   - Skill/Ability names
   - Dimension names

2. Do NOT extract:
   - MOD names (Mine and Slash, FTB Teams, Lightman's Currency, etc.)
   - Common words that are not proper nouns
   - Technical terms or UI labels

3. Output format: JSON object with English term as key, Japanese translation as value.
   Example: {"Everbright": "エバーブライト", "Blue Journal": "青い日誌"}

4. Output ONLY the JSON object, no markdown formatting or explanation."""

        user_prompt = f"""Extract proper nouns from these English-Japanese translation pairs:

{pairs_text}

Return a JSON object mapping English proper nouns to their Japanese translations."""

        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1  # Low temperature for consistent extraction
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=60)
        response.raise_for_status()
        
        result = response.json()
        content = result['choices'][0]['message']['content'].strip()
        
        # Clean up code blocks if present
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
            # Try to extract JSON from the response
            import re
            json_match = re.search(r'\{[^{}]*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {}
    
    def stop(self):
        self.is_running = False


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


def _is_valid_term(text, existing_glossary):
    stripped = text.strip()
    if len(stripped) < 3 or len(stripped) > 60:
        return False
    if not re.search(r'[a-zA-Z]', stripped):
        return False
    if text in existing_glossary:
        return False
    words = set(stripped.lower().split())
    if words <= _COMMON_WORDS:
        return False
    return True


def extract_all_term_candidates(original_items, translated_items, existing_glossary=None):
    """
    一貫した用語と翻訳ブレのある用語を両方抽出する（ローカル、API不要）。

    Args:
        original_items: dict of {key: original_text}
        translated_items: dict of {key: translated_text}
        existing_glossary: dict of existing glossary terms to exclude

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
        if not _is_valid_term(original, existing_glossary):
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


_COLOR_CODE_RE = re.compile(r'[&§]([0-9a-fklmno])([^&§]*?)[&§]r')
_TITLE_CASE_RE = re.compile(
    r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+(?:\s+[A-Z][a-z]+)*)\b'
)
_SINGLE_CAP_WORD_RE = re.compile(r'\b([A-Z][a-z]{2,})\b')
_BRACKET_TERM_RE = re.compile(r'[\[<]([A-Z][A-Za-z\s]{2,})[\]>]')
_QUOTED_TERM_RE = re.compile(r'["\u201c]([A-Z][A-Za-z\s]{2,}?)["\u201d]')


def extract_frequent_terms_from_original(original_items, min_count=2,
                                         existing_glossary=None):
    """
    翻訳前の原文のみから頻出する固有名詞候補を抽出する（API不要・ローカル処理）。

    抽出戦略:
      1. カラーコード付きテキスト（&eEverbright&r 等）— MOD固有名詞の信頼度が高い
      2. Title Case 複合語（Blue Journal, Night Lich 等）
      3. ブラケット/引用符内の語
      4. 大文字始まりの単語（出現数が多いもの）
      5. lang key の末尾セグメント（item.minecraft.diamond_sword → Diamond Sword）

    Args:
        original_items: dict[str, str] — {key: original_text}
        min_count: int — 最低出現回数
        existing_glossary: dict[str, str] — 既存辞書（既登録語は除外）

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
            if _is_valid_frequent_term(content, existing_glossary):
                found_in_entry.add(content)
                if term_sources[content]["priority"] < 5:
                    term_sources[content]["priority"] = 5

        for match in _BRACKET_TERM_RE.finditer(text):
            term = match.group(1).strip()
            if _is_valid_frequent_term(term, existing_glossary):
                found_in_entry.add(term)
                if term_sources[term]["priority"] < 4:
                    term_sources[term]["priority"] = 4

        for match in _QUOTED_TERM_RE.finditer(text):
            term = match.group(1).strip()
            if _is_valid_frequent_term(term, existing_glossary):
                found_in_entry.add(term)
                if term_sources[term]["priority"] < 4:
                    term_sources[term]["priority"] = 4

        for match in _TITLE_CASE_RE.finditer(text):
            term = match.group(1).strip()
            if _is_valid_frequent_term(term, existing_glossary):
                found_in_entry.add(term)
                if term_sources[term]["priority"] < 3:
                    term_sources[term]["priority"] = 3

        key_last = _extract_term_from_key(key)
        if key_last and _is_valid_frequent_term(key_last, existing_glossary):
            found_in_entry.add(key_last)
            if term_sources[key_last]["priority"] < 2:
                term_sources[key_last]["priority"] = 2

        if len(found_in_entry) == 0:
            for match in _SINGLE_CAP_WORD_RE.finditer(text):
                term = match.group(1)
                if _is_valid_frequent_term(term, existing_glossary):
                    found_in_entry.add(term)
                    if term_sources[term]["priority"] < 1:
                        term_sources[term]["priority"] = 1

        for term in found_in_entry:
            src = term_sources[term]
            src["count"] += 1
            if len(src["keys"]) < 5:
                src["keys"].append(key)

    results = []
    for term, src in term_sources.items():
        if src["count"] >= min_count:
            results.append((term, src["count"], src["keys"]))

    results.sort(key=lambda x: (x[1] * 10 + term_sources[x[0]]["priority"], x[0]),
                 reverse=True)

    return results


def _is_valid_frequent_term(text, existing_glossary):
    stripped = text.strip()
    if len(stripped) < 3 or len(stripped) > 60:
        return False
    if not re.search(r'[a-zA-Z]', stripped):
        return False
    lower = stripped.lower()
    if lower in existing_glossary or stripped in existing_glossary:
        return False
    if lower in _SKIP_TERMS_LOWER:
        return False
    words = set(lower.split())
    if words <= _COMMON_WORDS:
        return False
    if re.match(r'^[A-Z][a-z]+$', stripped) and lower in _COMMON_WORDS:
        return False
    return True


def _extract_term_from_key(key):
    """
    lang key の末尾セグメントから人が読める形式の名前を抽出する。
    例: "item.mineandslash.blue_journal" → "Blue Journal"
    """
    segments = key.split('.')
    last = segments[-1] if segments else ''
    if not last or len(last) < 3:
        return None
    if last.startswith('_'):
        last = last[1:]
    has_letter = any(c.isalpha() for c in last)
    if not has_letter:
        return None
    readable = last.replace('_', ' ').strip()
    readable = ' '.join(w.capitalize() for w in readable.split())
    words = set(readable.lower().split())
    if words <= _COMMON_WORDS or words <= _SKIP_TERMS_LOWER:
        return None
    return readable if len(readable) >= 3 else None


class AITermClassifierThread(QThread):
    """AIで候補リストを仕分けし、Minecraft固有名詞として翻訳すべきものを抽出する。"""

    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, candidates, api_key, model=None, batch_size=80):
        """
        Args:
            candidates: list[tuple[str, int, list[str]]] — (term, count, sample_keys)
            api_key: OpenRouter API key
            model: 使用モデル (default: DeepSeek)
            batch_size: 1バッチあたりの候補数
        """
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
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Minecraft MOD Translator - Term Classification"
        }

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

        data = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
        }

        response = requests.post(url, headers=headers, json=data, timeout=60)
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
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {}

    def stop(self):
        self.is_running = False


def extract_consistent_terms(original_items, translated_items, existing_glossary=None):
    """
    原文で2回以上出現し、訳文が一貫している用語を候補として抽出する（ローカル抽出、API不要）。
    後方互換ラッパー。新規コードでは extract_all_term_candidates を使用してください。
    """
    consistent, _ = extract_all_term_candidates(
        original_items, translated_items, existing_glossary
    )
    return consistent

