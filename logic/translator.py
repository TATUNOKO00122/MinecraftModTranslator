import collections
import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PySide6.QtCore import QThread, Signal

from logic.file_handler import TARGET_LANGUAGES

KEY_CATEGORY_HINTS = {
    "item": "アイテム名",
    "block": "ブロック名",
    "itemGroup": "クリエイティブタブ名",
    "tooltip": "ツールチップ説明文",
    "desc": "説明文",
    "description": "説明文",
    "enchantment": "エンチャント名・説明",
    "effect": "ステータス効果",
    "potion": "ポーション効果",
    "entity": "エンティティ名",
    "biome": "バイオーム名",
    "advancement": "進捗タイトル・説明",
    "death": "死亡メッセージ",
    "subtitles": "字幕テキスト",
    "subtitle": "字幕テキスト",
    "container": "コンテナ・UI名",
    "stat": "統計名",
    "gui": "GUIテキスト",
    "sound": "サウンド関連",
    "text": "テキスト",
    "message": "メッセージ",
    "key": "キーバインド名",
    "category": "カテゴリ名",
    "tag": "タグ名",
    "fluid": "液体名",
    "gamerule": "ゲームルール",
}

SECOND_LEVEL_HINTS = {
    "name": "名詞（アイテム/ブロック/エンティティの名称）",
    "title": "タイトル",
    "tooltip": "ツールチップ説明文",
    "desc": "説明文",
    "description": "説明文",
    "lore": "背景設定・フレーバーテキスト",
    "flavor": "フレーバーテキスト",
    "tip": "ヒント・チップ",
}

VARIABLE_PATTERNS = [
    r'\[calc:[a-zA-Z0-9_]+\]',
    r'\[[a-zA-Z0-9_]+\]',
    r'\{[a-zA-Z_][a-zA-Z0-9_]*\}',
    r'\{[0-9]+\}',
    r'\{[0-9]+\$[sdf]\}',
    r'%[0-9]*\$?[sdf]',
    r'%%',
    r'§[0-9a-fk-or]',
    r'&[0-9a-fk-or]',
    r'[☀⚔❤✦✧★☆➤►▸●◆◇▲▼✹❂❃❄✴✵✶]',
    r'\\n',
    r'<br\s*/?>',
    r'</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>',
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in VARIABLE_PATTERNS]

MAX_GLOSSARY_TERMS = 40

NUMBER_PATTERN = re.compile(r'\b\d+(?:[.,]\d+)*\b')

PLACEHOLDER_RE = re.compile(r'⟨([0-9a-f]{12})⟩')
PLACEHOLDER_LEN = 14


CJK_PATTERN = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF00-\uFFEF]')

TRANSLATABLE_SHORT_WORDS = frozenset({
    'no', 'yes', 'on', 'off', 'up', 'down', 'in', 'out',
    'ok', 'new', 'old', 'hot', 'wet', 'dry', 'red', 'raw',
    'air', 'ice', 'end', 'use', 'run', 'hit', 'die', 'fly',
    'ore', 'log', 'dye', 'bow', 'rod', 'map', 'key', 'bed',
    'hub', 'eye', 'arm', 'leg', 'sky', 'sun', 'sea', 'gem',
})

NOTRANSLATE_PATTERNS = [
    re.compile(r'^[a-zA-Z]\w*_\w+$'),
    re.compile(r'^[a-z][a-z0-9_.]*:[a-z0-9_./]+$', re.I),
    re.compile(r'^[\d%\.\\\+\-:,/\s]+$'),
    re.compile(r'^[\W\d\s]*$', re.U),
]


def should_skip_translation(text, target_lang="ja_jp"):
    if not text or not isinstance(text, str):
        return True
    trimmed = text.strip()
    if not trimmed:
        return True
    
    if len(trimmed) <= 3:
        if trimmed.lower() in TRANSLATABLE_SHORT_WORDS:
            return False
        if len(trimmed) <= 2 and not re.search(r'[a-zA-Z]', trimmed):
            return True
    
    for pattern in NOTRANSLATE_PATTERNS:
        if pattern.match(trimmed):
            return True
    
    if target_lang.startswith("ja") and len(trimmed) > 5:
        cjk_count = len(CJK_PATTERN.findall(trimmed))
        if cjk_count / len(trimmed) > 0.5:
            return True
    
    return False


def protect_variables(text):
    """
    Replace variables/format codes with UUID-based placeholders before translation.
    Returns (protected_text, list_of_original_variables, tag_to_index dict)
    """
    if not text or not isinstance(text, str):
        return text, [], {}

    variables = []
    tag_to_index = {}
    result = text

    for pattern in COMPILED_PATTERNS:
        matches = list(pattern.finditer(result))
        if not matches:
            continue

        replacements = []
        for match in matches:
            tag = uuid.uuid4().hex[:12]
            idx = len(variables)
            variables.append(match.group())
            tag_to_index[tag] = idx
            placeholder = f'⟨{tag}⟩'
            replacements.append((match.start(), match.end(), placeholder))

        for start, end, placeholder in reversed(replacements):
            result = result[:start] + placeholder + result[end:]

    return result, variables, tag_to_index


def _protect_numbers(text, variables, tag_to_index):
    """変数パターン置換後のテキストから、プレースホルダ以外の数値を保護する。"""
    result = ''
    last_end = 0

    def _replacer(match):
        tag = uuid.uuid4().hex[:12]
        idx = len(variables)
        variables.append(match.group())
        tag_to_index[tag] = idx
        return f'⟨{tag}⟩'

    for m in PLACEHOLDER_RE.finditer(text):
        before = text[last_end:m.start()]
        result += NUMBER_PATTERN.sub(_replacer, before)
        result += m.group()
        last_end = m.end()

    remaining = text[last_end:]
    result += NUMBER_PATTERN.sub(_replacer, remaining)
    return result, variables


def restore_variables(text, variables, tag_to_index):
    """
    Restore original variables from UUID-based placeholders after translation.
    Returns (restored_text, unrestored_tags).
    unrestored_tags is empty on success, or contains tags that were lost/altered.
    """
    if not text or not isinstance(text, str) or not variables:
        return text, []

    unrestored = []
    found_raw = []

    def replace_one(match):
        tag = match.group(1)
        found_raw.append(tag)
        idx = tag_to_index.get(tag)
        if idx is not None and idx < len(variables):
            return variables[idx]
        unrestored.append(match.group(0))
        return match.group(0)

    result = PLACEHOLDER_RE.sub(replace_one, text)

    expected_tags = set(tag_to_index.keys())
    found_tags = set(found_raw)
    missing_tags = expected_tags - found_tags
    for tag in missing_tags:
        unrestored.append(f'⟨{tag}⟩')

    dup_check = collections.Counter(found_raw)
    for tag, count in dup_check.items():
        expected = 1
        if count > expected:
            unrestored.append(f'⟨{tag}⟩(x{count})')

    return result, unrestored



def extract_tags(text):
    """テキストから全ての変数タグを抽出する。"""
    if not text:
        return []
    tags = []
    for pattern in COMPILED_PATTERNS:
        tags.extend(m.group() for m in pattern.finditer(text))
    tags.extend(NUMBER_PATTERN.findall(text))
    return sorted(tags)


def deep_tag_check(original, translated):
    """ソースに存在するタグが翻訳結果に欠落していないか検証する。"""
    source_tags = extract_tags(original)
    if not source_tags:
        return []
    translated_tags = extract_tags(translated)
    missing = set(source_tags) - set(translated_tags)
    return [f"Missing tag in translation: {tag}" for tag in sorted(missing)]


def _recover_partial_json(content):
    """LLM出力が途中で切断された場合、完了しているkey-valueペアのみ抽出する。"""
    recovered = {}
    pattern = r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"'
    for match in re.finditer(pattern, content):
        key = match.group(1)
        value = match.group(2)
        recovered[key] = value
    return recovered


def validate_translation(original, translated, glossary=None, target_lang="ja_jp"):
    """
    Validate translation quality.
    Returns (is_valid, list_of_issues, quality_score)
    """
    issues = []
    score_penalties = 0.0
    
    if not translated:
        return True, issues, 1.0
    
    if not original:
        original = ""
    
    missing_tags = deep_tag_check(original, translated)
    issues.extend(missing_tags)
    score_penalties += len(missing_tags) * 0.3
    
    if '{{' in translated or '}}' in translated:
        issues.append(f"Nested braces detected: {translated[:50]}...")
        score_penalties += 0.5
    
    fullwidth_patterns = [
        (r'％[ｓｄｆ]', '%s/%d/%f with fullwidth'),
        (r'｛[^｝]*｝', 'Fullwidth braces'),
    ]
    for pattern, desc in fullwidth_patterns:
        if re.search(pattern, translated):
            issues.append(f"{desc} detected in: {translated[:50]}...")
            score_penalties += 0.3
    
    if PLACEHOLDER_RE.search(translated):
        issues.append(f"Unreplaced placeholder in: {translated[:50]}...")
        score_penalties += 0.4
    
    original_placeholders = len(re.findall(r'\{[^}]+\}|%[0-9]*\$?[sdf]', original))
    translated_placeholders = len(re.findall(r'\{[^}]+\}|%[0-9]*\$?[sdf]', translated))
    if original_placeholders != translated_placeholders:
        issues.append(f"Placeholder count mismatch: original={original_placeholders}, translated={translated_placeholders}")
        score_penalties += 0.3
    
    if len(original) > 10:
        ratio = len(translated) / len(original)
        if ratio > 5:
            issues.append(f"Translation is {ratio:.1f}x longer than original")
            score_penalties += 0.3
        elif ratio < 0.2:
            issues.append(f"Translation is {ratio:.1f}x shorter than original")
            score_penalties += 0.3
    
    ascii_chars_translated = sum(1 for c in translated if ord(c) < 128)
    ascii_ratio_translated = ascii_chars_translated / max(len(translated), 1)
    non_latin_japanese = sum(1 for c in translated if '\u3040' <= c <= '\u309F'
                             or '\u30A0' <= c <= '\u30FF'
                             or '\u4E00' <= c <= '\u9FFF')
    
    if ascii_ratio_translated > 0.85 and len(translated) > 15 and non_latin_japanese < 3:
        long_english_words = re.findall(r'[A-Za-z]{5,}', translated)
        if long_english_words:
            issues.append(f"May be untranslated (high ASCII, no Japanese): {translated[:30]}...")
            score_penalties += 0.3
    
    if glossary:
        original_lower = original.lower()
        for en_term, ja_term in glossary.items():
            en_lower = en_term.lower()
            if re.search(r'\b' + re.escape(en_lower) + r'\b', original_lower):
                if ja_term not in translated and en_lower not in translated.lower():
                    issues.append(f"Glossary term missing: '{en_term}' → expected '{ja_term}'")
                    score_penalties += 0.2
    
    if target_lang.startswith("ja"):
        sentences = re.split(r'[。！？\n]+', translated)
        sentences = [s.strip() for s in sentences if s.strip()]
        if len(sentences) >= 3:
            keigo_pattern = re.compile(
                r'(?:です|ます|ください|しました|います|されます)'
                r'(?:[。！？\n]|$)'
            )
            da_pattern = re.compile(
                r'(?:だ|である|した|いる|される)'
                r'(?:[。！？\n]|$)'
            )
            has_keigo = bool(keigo_pattern.search(translated))
            has_da = bool(da_pattern.search(translated))
            if has_keigo and has_da:
                issues.append("敬体・常体の混在")
                score_penalties += 0.2
        
        katakana_chars = sum(1 for c in translated if '\u30A0' <= c <= '\u30FF')
        total_chars = max(len(translated), 1)
        if katakana_chars / total_chars > 0.6 and total_chars > 10:
            issues.append(f"カタカナ比率過多 ({katakana_chars}/{total_chars}文字)")
            score_penalties += 0.15
        
        bracket_english = re.findall(r'[（(][A-Za-z][A-Za-z ]+[)）]', translated)
        if bracket_english:
            issues.append(f"括弧内英語残存: {bracket_english[:3]}")
            score_penalties += 0.1
        
        if re.search(r'(.)\1{4,}', translated):
            issues.append(f"同じ文字の連続あり: {translated[:30]}...")
            score_penalties += 0.3
    
    quality_score = max(0.0, 1.0 - score_penalties)
    
    if quality_score < 0.5 and not issues:
        issues.append(f"品質スコア低: {quality_score:.2f}")
    
    return len(issues) == 0, issues, quality_score



class TranslatorThread(QThread):
    progress = Signal(int, int)
    finished = Signal(dict)
    stopped = Signal(dict)
    error = Signal(str)
    partial_save = Signal(dict)
    validation_finished = Signal(dict)
    consistency_warnings = Signal(list)
    token_stats = Signal(dict)

    @staticmethod
    def _extract_key_context(keys):
        prefix_counts = collections.Counter()
        suffix_counts = collections.Counter()

        for key in keys:
            parts = key.split(".")
            if len(parts) >= 2:
                prefix_counts[parts[0]] += 1
            if len(parts) >= 3:
                suffix_counts[parts[-1]] += 1

        total = len(keys)
        if total == 0:
            return ""

        hints = []
        for prefix, count in prefix_counts.most_common(2):
            if count / total >= 0.2:
                label = KEY_CATEGORY_HINTS.get(prefix)
                if label:
                    ratio = f"({count}/{total})"
                    hints.append(f"{label} {ratio}")

        for suffix, count in suffix_counts.most_common(2):
            if count / total >= 0.2:
                label = SECOND_LEVEL_HINTS.get(suffix)
                if label:
                    ratio = f"({count}/{total})"
                    hints.append(f"{label} {ratio}")

        if not hints:
            return ""

        return "このバッチの翻訳対象の主な種類: " + "、".join(hints)

    def _build_headers(self):
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": "Minecraft MOD Translator Desktop"
        }

    def _extract_relevant_glossary(self, batch_text):
        """バッチテキストから関連glossary語を抽出する（ステミング対応）。"""
        if not self.glossary:
            return {}
        
        batch_text_clean = batch_text.lower()
        for pat in COMPILED_PATTERNS:
            batch_text_clean = pat.sub(' ', batch_text_clean)
        
        batch_words = set(re.findall(r'[a-z]{3,}', batch_text_clean))
        batch_stems = {self._stem_word(w): w for w in batch_words}
        
        scored = []
        for en_term, ja_term in self.glossary.items():
            en_lower = en_term.lower()
            
            if en_lower in batch_text_clean:
                scored.append((100, en_term, ja_term))
                continue
            
            term_words = en_lower.split()
            stem_match_count = 0
            for tw in term_words:
                tw_stem = self._stem_word(tw)
                if tw_stem in batch_stems or tw in batch_words:
                    stem_match_count += 1
            
            if term_words and stem_match_count == len(term_words):
                scored.append((50, en_term, ja_term))
        
        scored.sort(key=lambda x: -x[0])
        
        return {k: v for _, k, v in scored[:MAX_GLOSSARY_TERMS]}

    _SUFFIXES_SORTED = (
        'ication', 'ization', 'ational', 'iness',
        'ation', 'ition', 'ment', 'ness', 'ence', 'ance',
        'able', 'ible', 'ings',
        'ing', 'ful', 'ous', 'ive',
        'ion', 'ity', 'ism', 'ist',
        'ed', 'er', 'ly',
    )

    @staticmethod
    def _stem_word(word):
        w = word.lower()
        if w.endswith('ies') and len(w) > 4:
            w = w[:-3] + 'y'
        elif w.endswith(('sses', 'shes', 'ches', 'xes', 'zes')) and len(w) > 4:
            w = w[:-2]
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            w = w[:-1]
        for suffix in TranslatorThread._SUFFIXES_SORTED:
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[:-len(suffix)]
        return w

    def _build_system_prompt(self, lang_english):
        base = (
            f"Professional translator for Minecraft mods/RPG games. EN→{lang_english}.\n"
            f"Input: JSON object. Keys are identifiers (NEVER change keys). Values are English text.\n"
            f"Output: JSON object with the same keys and translated values. No markdown fences.\n\n"
            f"=== RULES (priority order) ===\n"
            f"1. Keep ALL ⟨⟩ tokens EXACTLY as-is. Never modify, translate, remove, or reorder them.\n"
            f"   Multiple ⟨...⟩ tokens must stay in original left-to-right order.\n"
            f"2. Glossary terms MUST be used exactly as specified (see glossary section).\n"
            f"   A post-translation validator will OVERRIDE any non-matching terms automatically.\n"
            f"3. Keep ALL numeric values EXACTLY as-is. '150%' → '150%', '3-block radius' → '半径3ブロック'.\n"
            f"4. Use 常体 (だ・である調), NOT 敬体 (です・ます調).\n"
            f"   'Increases attack power' → '攻撃力が上がる' NOT '攻撃力が上がります'\n"
            f"5. Restructure English relative clauses into Japanese pre-modifiers (連体修飾):\n"
            f"   'A sword which deals fire damage' → '火ダメージを与える剣'\n"
            f"6. Prefer natural Japanese compounds over katakana transliteration:\n"
            f"   'melee weapon' → '近接武器' (NOT ミーリー武器), 'ranged attack' → '遠距離攻撃' (NOT レンジド攻撃)\n"
            f"   ONLY proper nouns (entity/boss/biome names) use katakana: Invoker → インヴォーカー, Warden → ウォーデン\n"
        )

        if self.source_type == "ftbquest":
            base += (
                "\n=== FTB QUESTS CONTEXT ===\n"
                "You are translating text from FTB Quests (a Minecraft quest modpack system).\n"
                "Keys follow the pattern: chapter.XXXX.title, quest.XXXX.title, quest.XXXX.subtitle, "
                "quest.XXXX.descriptionN, task.XXXX.title, reward.XXXX.title\n\n"
                "- chapter.title: Chapter title. Keep it 2-6 characters, concise and evocative. "
                "Examples: 「始まり」「鋼の試練」「深淵」\n"
                "- quest.title: Quest name. Brief noun phrase or short imperative. "
                "Examples: 「石炭を見つけよう」「鉄の装備」「モブの討伐」\n"
                "- quest.subtitle: Quest subtitle. One short sentence setting the quest tone.\n"
                "- quest.description: Quest description addressed to the player. "
                "Use narrative style (〜だ。〜である。) for world-building explanations. "
                "For instructional text, use imperative (〜せよ。〜を探せ。) or soft request (〜しよう。〜してみよう。).\n"
                "- task.title / reward.title: Short noun phrase — just the item or mob name itself. "
                "DO NOT write full sentences like 「アイテムを集める」 or 「モブを倒す」. "
                "Examples: 「鉄のインゴット」「ゾンビ」「ダイヤモンドのツルハシ」\n"
            )
        elif self.source_type == "datapack":
            base += (
                "\n=== DATAPACK CONTEXT ===\n"
                "You are translating text from a Minecraft datapack (RPG skill/class/stat system).\n"
                "Keys include: spell names, spell descriptions, perk/talent names, stat names, "
                "unique gear names, support gem descriptions, class names, runeword names, etc.\n\n"
                "- Spell/skill names: Concise, 2-6 characters. Use katakana for proper nouns, "
                "kanji for generic abilities. Examples: 「火球」「フリーズ」「鉄壁」\n"
                "- Spell/skill descriptions: Accurately convey numerical values and effects. "
                "Preserve all numbers, percentages, and mechanical keywords exactly. "
                "Example: 'Deals 150% fire damage to enemies in a 3-block radius' → "
                "'半径3ブロック以内の敵に火属性ダメージ150%を与える'\n"
                "- Stat names: Very concise, 2-4 characters. Examples: 「攻撃力」「魔力」「防御」\n"
                "- Class/school names: Concise katakana or kanji. Examples: 「火術師」「バーサーカー」\n"
            )
        else:
            base += (
                "\n=== MOD ITEM/BLOCK CONTEXT ===\n"
                "- Item/block names: Concise. Avoid overly long names. "
                "Examples: 「ダイヤモンドの剣」「不思議なリンゴ」\n"
                "- Tooltip/description: Practical and readable. Preserve formatting codes.\n"
                "- Advancement titles: Short and punchy.\n"
                "- Advancement descriptions: One sentence hint about the achievement.\n"
            )

        return base

    def __init__(self, items, api_key, model, glossary=None, parallel_count=3, 
                 memory=None, mod_name=None, target_lang="ja_jp", source_type=None):
        super().__init__()
        self.items = items
        self._api_key = api_key
        self.model = model
        self.glossary = glossary or {}
        self.is_running = True
        self._partial_results = {}
        self.target_batch_chars = 4000
        self.min_batch_size = 5
        self.max_batch_size = 150
        self.parallel_count = max(1, min(parallel_count, 10))
        self._rate_limit_hit = False
        self.memory = memory
        self.mod_name = mod_name
        self._batches_since_save = 0
        self.save_interval = 5
        self.target_lang = target_lang
        self.source_type = source_type
        self._completed_translations = {}
        self._token_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'api_calls': 0,
        }

    def _accumulate_tokens(self, token_info):
        if not token_info:
            return
        self._token_usage['prompt_tokens'] += token_info.get('prompt_tokens', 0)
        self._token_usage['completion_tokens'] += token_info.get('completion_tokens', 0)
        self._token_usage['total_tokens'] += token_info.get('total_tokens', 0)
        self._token_usage['api_calls'] += 1
        self.token_stats.emit(dict(self._token_usage))

    def _select_context_examples(self, completed, current_keys, limit=8):
        current_prefixes = set(k.split('.')[0] for k in current_keys if '.' in k)

        scored = []
        for key, translation in completed.items():
            prefix = key.split('.')[0] if '.' in key else ''
            original = self.items.get(key, '')
            if not original or not translation:
                continue
            if prefix in current_prefixes:
                if len(original) <= 120:
                    scored.append((2, original, translation))
            elif key in self.items:
                if len(original) <= 80:
                    scored.append((1, original, translation))

        scored.sort(key=lambda x: -x[0])
        seen = set()
        results = []
        for _, s, t in scored:
            if s not in seen:
                seen.add(s)
                results.append((s, t))
                if len(results) >= limit:
                    break
        return results

    @staticmethod
    def _group_by_context(items):
        groups = {}
        for key, text in items.items():
            prefix = key.split('.')[0] if '.' in key else ''
            groups.setdefault((text, prefix), []).append(key)

        unique_items = {}
        group_to_keys = {}
        for (text, prefix), keys in groups.items():
            unique_items[keys[0]] = text
            group_to_keys[(text, prefix)] = keys

        return unique_items, group_to_keys

    def _create_batches(self, items):
        batches = []
        current_batch = {}
        current_chars = 0
        
        for key, text in items.items():
            text_len = len(str(text)) if text else 0
            
            if current_chars + text_len > self.target_batch_chars and len(current_batch) >= self.min_batch_size:
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
            
            current_batch[key] = text
            current_chars += text_len
            
            if len(current_batch) >= self.max_batch_size:
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
        
        if current_batch:
            batches.append(current_batch)
        
        return batches

    def run(self):
        results = {}
        validation_results = {}
        
        batches = self._create_batches(self.items)
        total_items = len(self.items)
        processed = 0
        
        self._completed_translations = {}
        
        if self.parallel_count == 1:
            for batch_idx, batch_items in enumerate(batches):
                if not self.is_running:
                    self.stopped.emit(results)
                    return
                
                try:
                    translated_batch, batch_validation = self.translate_batch_with_retry(
                        batch_items,
                        completed_context=self._completed_translations
                    )
                    results.update(translated_batch)
                    validation_results.update(batch_validation)
                    self._partial_results = results
                    self._completed_translations.update(translated_batch)
                    
                    self._batches_since_save += 1
                    if self._batches_since_save >= self.save_interval:
                        self._progressive_save(results, batch_items, validation_results)
                        self._batches_since_save = 0
                except Exception as e:
                    print(f"Batch {batch_idx + 1} failed: {e}")
                    self.error.emit(f"Batch {batch_idx + 1} failed: {e}")
                
                if not self.is_running:
                    self._progressive_save(results, {}, validation_results)
                    self.stopped.emit(results)
                    return
                
                processed += len(batch_items)
                self.progress.emit(processed, total_items)
                time.sleep(0.5)
        else:
            self._run_parallel(batches, results, validation_results, total_items)
            if not self.is_running:
                self._progressive_save(results, {}, validation_results)
                self.stopped.emit(results)
                return

        if validation_results:
            self.validation_finished.emit(validation_results)
        
        if self.glossary and results:
            warnings, corrections = self._check_batch_consistency(results)
            if corrections:
                results.update(corrections)
                print(f"Glossary consistency auto-fix: {len(corrections)} keys corrected")
            if warnings:
                self.consistency_warnings.emit(warnings)
        
        self._api_key = None
        self.token_stats.emit(dict(self._token_usage))
        self.finished.emit(results)

    def _run_parallel(self, batches, results, validation_results, total_items):
        processed = 0
        current_parallel = self.parallel_count
        batches_since_save = 0
        
        chunk_results = {}
        
        batch_index = 0
        while batch_index < len(batches):
            if not self.is_running:
                return
            
            chunk_end = min(batch_index + current_parallel, len(batches))
            batch_chunk = [(i, batches[i]) for i in range(batch_index, chunk_end)]
            
            with ThreadPoolExecutor(max_workers=current_parallel) as executor:
                context_snapshot = dict(chunk_results)
                future_to_batch = {
                    executor.submit(
                        self._translate_batch_safe, idx, batch_items, context_snapshot
                    ): (idx, batch_items)
                    for idx, batch_items in batch_chunk
                }
                
                for future in as_completed(future_to_batch):
                    if not self.is_running:
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    
                    idx, batch_items = future_to_batch[future]
                    try:
                        translated_batch, batch_validation, rate_limited = future.result()
                        if translated_batch:
                            results.update(translated_batch)
                            validation_results.update(batch_validation)
                            self._partial_results = results
                            chunk_results.update(translated_batch)
                        
                        if rate_limited and current_parallel > 1:
                            current_parallel = max(1, current_parallel - 1)
                            print(f"Rate limit hit, reducing parallel count to {current_parallel}")
                            
                    except Exception as e:
                        print(f"Batch {idx + 1} failed: {e}")
                        self.error.emit(f"Batch {idx + 1} failed: {e}")
                    
                    processed += len(batch_items)
                    self.progress.emit(processed, total_items)
                    batches_since_save += 1
            
            if batches_since_save >= self.save_interval:
                self._progressive_save(results, {}, validation_results)
                batches_since_save = 0
            
            self._completed_translations.update(chunk_results)
            batch_index = chunk_end
            
            if batch_index < len(batches):
                time.sleep(0.3)
    
    def _translate_batch_safe(self, batch_idx, items, chunk_results=None):
        rate_limited = False
        try:
            result, validation = self.translate_batch_with_retry(
                items, completed_context=chunk_results
            )
            return result, validation, rate_limited
        except Exception as e:
            if "429" in str(e):
                rate_limited = True
            raise

    def translate_batch_with_retry(self, items, max_retries=3, completed_context=None):
        retries = 0
        while retries < max_retries:
            if not self.is_running:
                return {}, {}
            try:
                return self.translate_batch(items, completed_context=completed_context)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    retries += 1
                    wait_time = 2 ** retries
                    print(f"Rate limit hit (429). Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise Exception(f"HTTP {e.response.status_code}: {e}")
            except Exception as e:
                print(f"Error during translation: {e}")
                retries += 1
                time.sleep(1)
                
        raise Exception("Max retries exceeded")

    def translate_batch(self, items, completed_context=None):
        if not items:
            return {}, {}
        
        if not self.is_running:
            return {}, {}
        
        final_results = {}
        validation_results = {}
        
        translatable = {}
        for key, text in items.items():
            if should_skip_translation(text, self.target_lang):
                final_results[key] = text
            else:
                translatable[key] = text
        
        if not translatable:
            return final_results, validation_results
        
        unique_items, group_to_keys = self._group_by_context(translatable)
        
        protected_items = {}
        variable_map = {}
        tag_map = {}

        for key, text in unique_items.items():
            if text and isinstance(text, str):
                protected_text, variables, t2i = protect_variables(text)
                protected_text, variables = _protect_numbers(protected_text, variables, t2i)
                protected_items[key] = protected_text
                variable_map[key] = variables
                tag_map[key] = t2i
            else:
                protected_items[key] = text
                variable_map[key] = []
                tag_map[key] = {}
        
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = self._build_headers()
        
        prompt_content = json.dumps(protected_items, ensure_ascii=False)
        
        lang_info = TARGET_LANGUAGES.get(self.target_lang, ("Japanese", "日本語"))
        lang_english = lang_info[0]
        
        system_content = self._build_system_prompt(lang_english)

        key_context = self._extract_key_context(list(items.keys()))
        if key_context:
            system_content += f"\n\n=== KEY CONTEXT ===\n{key_context}"

        if self.glossary:
            batch_text = " ".join([str(v) for v in unique_items.values()])
            relevant_terms = self._extract_relevant_glossary(batch_text)
            
            if relevant_terms:
                glossary_text = "\n".join([f"- {k}: {v}" for k, v in relevant_terms.items()])
                system_content += f"\n\nUse the following glossary for consistency:\n{glossary_text}"

        if self.memory:
            try:
                batch_texts = list(unique_items.values())
                similar = self.memory.find_similar(batch_texts, mod_name=self.mod_name, limit=5)
                if similar:
                    filtered = [(s, t) for s, t in similar if len(s) <= 120 and len(t) <= 120]
                    if filtered:
                        examples = "\n".join([f'  "{s}" → "{t}"' for s, t in filtered])
                        system_content += (
                            f"\n\n=== REFERENCE TRANSLATIONS (already translated in "
                            f"{'this MOD' if self.mod_name else 'your translation memory'}) ===\n"
                            f"Use these for consistent terminology:\n{examples}\n"
                            f"Rules:\n"
                            f"- If a proper noun appears above, you MUST use the same translation.\n"
                            f"- If a term has no established translation, keep the original English as-is.\n"
                        )
            except Exception as e:
                print(f"TM few-shot lookup skipped: {e}")

        if completed_context:
            context_examples = self._select_context_examples(
                completed_context, list(items.keys()), limit=8
            )
            if context_examples:
                examples_text = "\n".join(
                    [f'  "{s}" → "{t}"' for s, t in context_examples]
                )
                system_content += (
                    f"\n\n=== PREVIOUSLY TRANSLATED IN THIS SESSION ===\n"
                    f"These items were already translated. Use the SAME terminology:\n"
                    f"{examples_text}\n"
                )

        data = {
            "model": self.model,
            "temperature": 0.0,
            "messages": [
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": f"Translate the following values to {lang_english}:\n\n{prompt_content}"
                }
            ],
        }
        
        translated, token_info = self._call_llm(url, headers, data, len(protected_items))
        if translated is None:
            return final_results, validation_results
        self._accumulate_tokens(token_info)

        unique_results = {}
        corrupted_keys = {}

        for key, translated_text in translated.items():
            if key in variable_map and variable_map[key]:
                restored_text, unrestored = restore_variables(
                    translated_text, variable_map[key], tag_map[key]
                )

                if unrestored:
                    print(f"Placeholder corruption for '{key}': {len(unrestored)} tag(s) lost/altered")
                    corrupted_keys[key] = unique_items[key]
                    continue

                is_valid, issues, quality = validate_translation(
                    unique_items.get(key, ''), restored_text, self.glossary, self.target_lang
                )
                if not is_valid:
                    print(f"Translation warning for '{key}': {issues}")
                    validation_results[key] = {"issues": issues, "reviewed": False, "quality_score": quality}

                unique_results[key] = restored_text
            else:
                is_valid, issues, quality = validate_translation(
                    unique_items.get(key, ''), translated_text, self.glossary, self.target_lang
                )
                if not is_valid:
                    print(f"Translation warning for '{key}': {issues}")
                    validation_results[key] = {"issues": issues, "reviewed": False, "quality_score": quality}

                unique_results[key] = translated_text

        if corrupted_keys:
            retried = self._retry_corrupted(
                corrupted_keys, url, headers, lang_english, variable_map, tag_map
            )
            for key, text in retried.items():
                unique_results[key] = text
            corrupted_keys = {k: v for k, v in corrupted_keys.items() if k not in retried}

        for key in corrupted_keys:
            print(f"Using original text for corrupted key: {key}")
            unique_results[key] = unique_items[key]
            validation_results[key] = {"issues": ["Placeholder corruption — kept original"], "reviewed": False}

        for (text, prefix), keys in group_to_keys.items():
            representative_key = keys[0]
            if representative_key in unique_results:
                result_text = unique_results[representative_key]
                for key in keys:
                    final_results[key] = result_text
                if representative_key in validation_results:
                    for key in keys[1:]:
                        validation_results[key] = validation_results[representative_key].copy()

        self._apply_glossary_post_process(final_results, unique_items)

        return final_results, validation_results

    def _call_llm(self, url, headers, data, expected_count):
        try:
            response = requests.post(url, headers=headers, json=data, timeout=120)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            print(f"LLM request failed: {e}")
            return None, {}

        result_json = response.json()
        content = result_json['choices'][0]['message']['content'].strip()

        usage = result_json.get('usage', {})
        token_info = {
            'prompt_tokens': usage.get('prompt_tokens', 0),
            'completion_tokens': usage.get('completion_tokens', 0),
            'total_tokens': usage.get('total_tokens', 0),
        }

        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        first_brace = content.find('{')
        last_brace = content.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            content = content[first_brace:last_brace + 1]

        try:
            translated = json.loads(content)
        except json.JSONDecodeError as e:
            recovered = _recover_partial_json(content)
            if recovered:
                print(f"JSON parse failed ({e}), recovered {len(recovered)} of {expected_count} items from partial response")
                translated = recovered
            else:
                raise

        return translated, token_info

    def _retry_corrupted(self, corrupted_keys, url, headers, lang_english, variable_map, tag_map):
        max_retries = 3
        resolved = {}
        remaining = dict(corrupted_keys)

        for attempt in range(max_retries):
            if not remaining:
                break

            print(f"Retry attempt {attempt + 1}/{max_retries} for {len(remaining)} corrupted item(s)")

            retry_protected = {}
            retry_tag_map = {}
            for key, text in remaining.items():
                p, v, t = protect_variables(text)
                protected_text, v = _protect_numbers(p, v, t)
                retry_protected[key] = protected_text
                variable_map[key] = v
                retry_tag_map[key] = t
                tag_map[key] = t

            system_content = self._build_system_prompt(lang_english)
            system_content += "\n=== RETRY CONTEXT ===\n"
            system_content += "The previous translation corrupted some placeholder tokens.\n"
            system_content += "Be EXTRA careful to preserve ALL ⟨⟩ tokens exactly.\n"

            if self.glossary:
                batch_text = " ".join(str(v) for v in remaining.values())
                relevant = self._extract_relevant_glossary(batch_text)
                if relevant:
                    glossary_text = "\n".join(f"- {k}: {v}" for k, v in relevant.items())
                    system_content += f"\n\nGlossary:\n{glossary_text}"

            prompt = json.dumps(retry_protected, ensure_ascii=False)
            data = {
                "model": self.model,
                "temperature": 0.0,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user",
                     "content": f"Translate the following values to {lang_english}:\n\n{prompt}"}
                ],
            }

            try:
                translated, token_info = self._call_llm(url, headers, data, len(retry_protected))
                self._accumulate_tokens(token_info)
            except Exception as e:
                print(f"Retry LLM call failed: {e}")
                continue
            if translated is None:
                continue

            still_corrupted = {}
            for key, translated_text in translated.items():
                if key not in remaining:
                    continue
                restored_text, unrestored = restore_variables(
                    translated_text, variable_map[key], tag_map[key]
                )
                if unrestored:
                    still_corrupted[key] = remaining[key]
                    continue

                is_valid, issues, quality = validate_translation(remaining[key], restored_text, self.glossary, self.target_lang)
                if not is_valid:
                    print(f"Retry warning for '{key}': {issues}")
                resolved[key] = restored_text

            remaining = still_corrupted

        return resolved

    def _progressive_save(self, results: dict, batch_sources: dict, validation_results: dict = None):
        if not self.memory or not results:
            return
        
        try:
            FATAL_ISSUES = {
                "Placeholder corruption",
                "Placeholder count mismatch",
                "Nested braces",
                "Unreplaced placeholder",
            }
            QUALITY_ISSUES = {
                "untranslated",
                "longer than",
                "shorter than",
                "Glossary term missing",
            }
            
            saveable = results
            if validation_results:
                saveable = {
                    k: v for k, v in results.items()
                    if k not in validation_results or not any(
                        any(bad in issue for bad in FATAL_ISSUES | QUALITY_ISSUES)
                        for issue in validation_results[k].get("issues", [])
                    )
                }
            
            if saveable:
                self.memory.set_context(
                    mod_name=self.mod_name,
                    model=self.model,
                    sources=self.items
                )
                self.memory.update(saveable, origin='ai')
                print(f"Progressive save: {len(saveable)}/{len(results)} translations saved "
                      f"({len(results) - len(saveable)} quality-filtered)")
            
            self.partial_save.emit(results)
        except Exception as e:
            print(f"Progressive save failed: {e}")

    def _apply_glossary_post_process(self, final_results: dict, unique_items: dict):
        if not self.glossary:
            return

        for en_term, ja_term in self.glossary.items():
            en_norm = en_term.strip()
            if not en_norm or len(en_norm) < 2:
                continue

            en_pattern = re.compile(re.escape(en_norm), re.IGNORECASE)

            for key, translated in final_results.items():
                source = unique_items.get(key, self.items.get(key, ''))
                if not en_pattern.search(source):
                    continue

                if en_pattern.search(translated):
                    final_results[key] = en_pattern.sub(ja_term, translated)

    def _check_batch_consistency(self, all_translated: dict):
        issues = []
        corrections = {}
        glossary = self.glossary if isinstance(self.glossary, dict) else {}
        if not glossary:
            return issues, corrections

        glossary_reverse = {}
        for en, ja in glossary.items():
            normalized = en.strip().lower()
            if len(normalized) >= 3:
                glossary_reverse[normalized] = ja

        term_translations = {}
        term_keys = {}
        for key, source_text in self.items.items():
            if key not in all_translated:
                continue
            translation = all_translated[key]
            words = re.findall(r'[a-zA-Z]{3,}', source_text)
            for word in words:
                word_lower = word.lower()
                if word_lower in glossary_reverse:
                    ja_expected = glossary_reverse[word_lower]
                    term_translations.setdefault(word_lower, {})
                    term_keys.setdefault(word_lower, [])
                    term_keys[word_lower].append(key)
                    if ja_expected in translation:
                        term_translations[word_lower][key] = ja_expected
                    else:
                        extracted = re.findall(r'[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\u3000-\u303f\uff00-\uffef]{2,}', translation)
                        found_term = extracted[0] if extracted else translation[:min(len(translation), 20)]
                        term_translations[word_lower][key] = found_term

        for word, key_translations in term_translations.items():
            unique_vals = set(key_translations.values())
            if len(unique_vals) <= 1:
                continue

            canonical = glossary_reverse.get(word)
            if canonical and canonical in unique_vals:
                target_term = canonical
            else:
                counter = collections.Counter(key_translations.values())
                target_term = counter.most_common(1)[0][0]

            samples = list(unique_vals)[:5]
            issues.append(f"用語不統一: '{word}' → {samples} (統一 → '{target_term}')")

            for key, current_term in key_translations.items():
                if current_term != target_term:
                    old_text = all_translated[key]
                    new_text = old_text.replace(current_term, target_term)
                    if new_text != old_text:
                        corrections[key] = new_text

        return issues, corrections

    def stop(self):
        self.is_running = False
