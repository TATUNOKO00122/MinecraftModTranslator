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

MAX_GLOSSARY_TERMS = 60

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
            alpha_words = re.findall(r'[a-zA-Z]{3,}', trimmed)
            if not alpha_words:
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


def _recover_partial_json(content, valid_keys=None, expected_count=0):
    """LLM出力が途中で切断された場合、完了しているkey-valueペアのみ抽出する。
    
    Args:
        content: LLM出力テキスト
        valid_keys: 翻訳リクエストに含まれていたキーの集合。指定時はこれに含まれるキーのみ採用
        expected_count: 期待される翻訳件数。乖離時に警告出力
    """
    recovered = {}
    pattern = r'"((?:[^"\\]|\\.)*)"\s*:\s*"((?:[^"\\]|\\.)*)"'
    for match in re.finditer(pattern, content):
        key = match.group(1)
        value = match.group(2)
        if key in recovered:
            continue
        if valid_keys is not None and key not in valid_keys:
            continue
        recovered[key] = value

    if expected_count > 0 and len(recovered) > 0:
        ratio = len(recovered) / expected_count
        if ratio < 0.5:
            print(f"WARNING: Partial JSON recovery got {len(recovered)}/{expected_count} items ({ratio:.0%}). "
                  f"Response may be severely truncated or corrupted.")
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
        
        print(f"[GLOSSARY-DBG] batch_words({len(batch_words)}): {sorted(batch_words)[:20]}")
        
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
            f"Expert EN→{lang_english} translator for Minecraft mods/RPG games.\n"
            f"Input: JSON object (keys = identifiers, NEVER change). Values = English text.\n"
            f"Output: JSON with same keys, naturally translated values. No markdown fences.\n\n"
            f"=== CORE PRINCIPLE ===\n"
            f"Translate MEANING, not words. EN→JP requires complete restructuring.\n"
            f"1) Read full context → 2) Grasp intent → 3) Express in natural Japanese.\n"
            f"Japanese: REASON before RESULT. 'X because Y' → 'YなのでX'.\n"
            f"Multi-line values: treat as ONE coherent text, keep consistent tone.\n\n"
            f"=== RESTRUCTURING EXAMPLES ===\n"
            f"BAD: 'Make sure to check Quests as they contain useful info'\n"
            f"  →「クエストをチェックすることを忘れないでください。役立つ情報が含まれているためです。」\n"
            f"GOOD:→「クエストには役立つ情報がたくさん含まれているので、ぜひチェックしてみてください。」\n\n"
            f"BAD: 'It has been a long time' → 'それは長い時間だった'\n"
            f"GOOD:→ '久しぶりだな'\n\n"
            f"BAD: 'You might want to try...' → 'あなたは試してみたいかもしれない...'\n"
            f"GOOD:→ '…してみるといいだろう'\n\n"
            f"BAD: 'This is that thing' → 'これはそのものだ' (wrong reading of 'thing')\n"
            f"GOOD:→ 'それがその何かだ' / 'それがあれだ'\n\n"
            f"=== FORMATTING TOKENS ===\n"
            f"⟨...⟩ tokens are Minecraft formatting codes (color, bold, reset). NOT text content.\n"
            f"Text split by ⟨...⟩ is still ONE sentence semantically.\n"
            f"Translate naturally first, then position ⟨...⟩ tokens where they belong.\n\n"
            f"=== RULES ===\n"
            f"1. Keep ALL ⟨⟩ tokens and numeric values EXACTLY as-is. '150%' → '150%', '3-block radius' → '半径3ブロック'.\n"
            f"2. Glossary terms: use EXACTLY as specified. A validator will auto-override mismatches.\n"
            f"3. Use 常体 (だ・である調), NOT 敬体 (です・ます調).\n"
            f"   'Increases attack power' → '攻撃力が上がる' NOT '攻撃力が上がります'\n"
            f"4. Natural Japanese over katakana transliteration:\n"
            f"   'melee weapon' → '近接武器' (NOT ミーリー武器), 'ranged attack' → '遠距離攻撃'\n"
            f"   Proper nouns → katakana: Invoker → インヴォーカー, Warden → ウォーデン\n"
            f"   'mod/mods' → ALWAYS 'MOD' (大文字半角), NEVER 'モッド'\n"
            f"   'gear/equipment' → '装備', 'stats' → 'ステータス'\n"
            f"5. Game verbs — use Japanese verbs, NEVER katakana+する: 'slot'→'装着する', 'loot'→'入手する'\n"
            f"   'equip'→'装備する', 'craft'→'作成する', 'spawn'→'スポーンする'\n"
            f"   'buff'→'強化効果', 'debuff'→'弱体効果', 'upgrade'→'強化する', 'smelt'→'精錬する'\n"
            f"6. RPG structure terms: 'Act I/II'→'第1幕/第2幕' (Roman→Arabic), 'Chapter 1'→'第1章', "
            f"'Stage 1'→'第1ステージ', 'Wave 1'→'第1波'\n"
            f"7. Keep ALL parts of compound nouns: 'Mini-Boss'→'ミニボス' (NOT ボス), 'Elite Mob'→'エリートモブ'\n"
            f"   Compound creatures: naturalize if base is common noun ('lava squid'→'溶岩イカ'), katakana if proper noun\n"
            f"8. Structure words — translate naturally, NOT word-by-word:\n"
            f"   'Has:' → '以下の特徴がある:' (NOT '持っている:'), 'Properties:' → '特性:'\n"
            f"   'integration(s) between X and Y' → 'XとYの連携(機能)' (NOT '統合')\n"
            f"   'enhance X with Y' → 'YでXを強化する' (NOT 'Yを強化する' — subject-object swap)\n"
        )

        if self.source_type == "ftbquest":
            base += (
                "\n=== FTB QUESTS CONTEXT ===\n"
                "You are translating text from FTB Quests (a Minecraft quest modpack system).\n"
                "Keys follow the pattern: chapter.XXXX.title, quest.XXXX.title, quest.XXXX.subtitle, "
                "quest.XXXX.descriptionN, task.XXXX.title, reward.XXXX.title\n\n"
                "- chapter.title: 2-6字の簡潔なタイトル。例: 「始まり」「鋼の試練」「深淵」\n"
                "- quest.title: 短い名詞句または命令形。例: 「石炭を見つけよう」「鉄の装備」\n"
                "- quest.subtitle: クエストの雰囲気を示す1文。\n"
                "- quest.description: 世界観説明は叙述体(〜だ。〜である。)、指示は命令形(〜せよ。)または柔らかい表現(〜しよう。)。\n"
                "- task.title / reward.title: 名詞句のみ(動詞を含めない)。例: 「鉄のインゴット」「ゾンビ」「ダイヤモンドのツルハシ」\n"
                "  決して「アイテムを集める」「モブを倒す」のような文にしないこと。\n"
            )
        elif self.source_type == "datapack":
            base += (
                "\n=== DATAPACK CONTEXT ===\n"
                "You are translating text from a Minecraft datapack (RPG skill/class/stat system).\n"
                "Keys include: spell names, spell descriptions, perk/talent names, stat names, "
                "unique gear names, support gem descriptions, class names, runeword names, etc.\n\n"
                "- Spell/skill names: 2-6字。固有名詞はカタカナ、汎用は漢字。例: 「火球」「フリーズ」「鉄壁」\n"
                "- Spell/skill descriptions: 数値・効果を正確に伝える。全ての数値・パーセント・キーワードを保持。\n"
                "  'Deals 150% fire damage to enemies in a 3-block radius' → "
                "'半径3ブロック以内の敵に火属性ダメージ150%を与える'\n"
                "- Stat names: 2-4字。例: 「攻撃力」「魔力」「防御」\n"
                "- Class/school names: 簡潔に。例: 「火術師」「バーサーカー」\n"
            )
        else:
            base += (
                "\n=== MOD ITEM/BLOCK CONTEXT ===\n"
                "- Item/block names: 簡潔に。例: 「ダイヤモンドの剣」「不思議なリンゴ」\n"
                "- Tooltip/description: 実用的で読みやすく。フォーマットコードを保持。\n"
                "- Advancement titles: 短く力強く。\n"
                "- Advancement descriptions: 実績のヒントを1文で。\n"
            )

        return base

    def __init__(self, items, api_key, model, glossary=None, parallel_count=3, 
                 memory=None, mod_name=None, target_lang="ja_jp", source_type=None,
                 cross_mod_data=None):
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
        self.cross_mod_data = cross_mod_data or {}
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

        current_texts = [self.items.get(k, '') for k in current_keys]
        current_text_joined = ' '.join(t for t in current_texts if t)
        significant_words = self._extract_significant_words(current_text_joined)

        scored = []
        for key, translation in completed.items():
            prefix = key.split('.')[0] if '.' in key else ''
            original = self.items.get(key, '')
            if not original or not translation:
                continue

            word_overlap = sum(1 for w in significant_words if w in original)

            if word_overlap > 0 and len(original) <= 120:
                scored.append((10 + word_overlap, len(original), original, translation))
            elif prefix in current_prefixes and len(original) <= 120:
                scored.append((2, len(original), original, translation))
            elif key in self.items and len(original) <= 80:
                scored.append((1, len(original), original, translation))

        scored.sort(key=lambda x: (-x[0], x[1]))

        seen = set()
        results = []
        for _, _, s, t in scored:
            if s not in seen:
                seen.add(s)
                results.append((s, t))
                if len(results) >= limit:
                    break
        return results

    @staticmethod
    def _extract_significant_words(text):
        words = re.findall(r'[A-Z][a-zA-Z]{2,}', text)
        counted = collections.Counter(words)
        return [w for w, _ in counted.most_common(20)]

    @staticmethod
    def _extract_proper_noun_rules(examples):
        rules = []
        for source, translation in examples:
            src_segs = re.findall(r'&[0-9a-fk-or]([^&]+?)(?:&r|$)', source)
            trans_segs = re.findall(r'&[0-9a-fk-or]([^&]+?)(?:&r|$)', translation)
            if src_segs and trans_segs:
                seg_count = min(len(src_segs), len(trans_segs))
                for i in range(seg_count):
                    s = src_segs[i].strip()
                    t = trans_segs[i].strip()
                    if len(s.split()) >= 2 and re.search(r'[A-Z][a-zA-Z]+', s) and re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', t):
                        rules.append((s, t))
                continue
            en_words = re.findall(r'[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*', source)
            for phrase in en_words:
                if len(phrase.split()) >= 2 and phrase in source:
                    cjk_chunks = re.findall(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u30FC]+', translation)
                    for k in cjk_chunks:
                        if len(k) >= 3:
                            rules.append((phrase, k))
                            break
        seen = set()
        unique = []
        for en, ja in rules:
            if en not in seen:
                seen.add(en)
                unique.append((en, ja))
        return unique[:10]

    @staticmethod
    def _group_by_context(items):
        groups = {}
        for key, text in items.items():
            prefix = key.split('.')[0] if '.' in key else ''
            groups.setdefault((text, prefix), []).append(key)

        unique_items = {}
        group_to_keys = {}
        duplicate_groups = []

        for (text, prefix), keys in groups.items():
            if len(keys) == 1:
                unique_items[keys[0]] = text
            else:
                for key in keys:
                    unique_items[key] = text
                duplicate_groups.append(keys)
            group_to_keys[(text, prefix)] = keys

        return unique_items, group_to_keys, duplicate_groups

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
        
        unique_items, group_to_keys, duplicate_groups = self._group_by_context(translatable)
        
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

        if duplicate_groups:
            dup_examples = []
            for grp in duplicate_groups[:5]:
                dup_examples.append("  " + ", ".join(grp))
            system_content += (
                "\n\n=== DUPLICATE TEXT NOTE ===\n"
                "Some source texts appear under multiple keys.\n"
                "When keys suggest different contexts (name vs description, title vs tooltip), "
                "provide context-appropriate translations for each key.\n"
                "When keys clearly share the same purpose, identical translations are acceptable.\n"
                "Duplicate groups:\n" + "\n".join(dup_examples)
            )

        if self.glossary:
            batch_text = " ".join([str(v) for v in unique_items.values()])
            relevant_terms = self._extract_relevant_glossary(batch_text)
            print(f"[GLOSSARY] {len(relevant_terms)} relevant terms from {len(self.glossary)} total")
            
            if relevant_terms:
                glossary_text = "\n".join([f"- {k}: {v}" for k, v in relevant_terms.items()])
                system_content += (
                    f"\n\n=== GLOSSARY (MANDATORY) ===\n"
                    f"Use these EXACT translations for the following terms.\n"
                    f"A post-translation validator will force-replace any non-matching terms.\n"
                    f"{glossary_text}"
                )

        if self.memory:
            try:
                batch_texts = list(unique_items.values())
                batch_keys = set(items.keys())
                term_translations = self.memory.find_term_translations(
                    batch_texts, cross_mod_data=self.cross_mod_data,
                    exclude_keys=batch_keys,
                    limit=30
                )
                print(f"[TM-TERM] find_term_translations returned: {len(term_translations)} results")
                if term_translations:
                    rules_text = "\n".join([f"  {en} → {ja}" for en, ja in term_translations])
                    system_content += (
                        f"\n\n=== MANDATORY TERM TRANSLATIONS ===\n"
                        f"The following terms have established translations from other MODs/translations.\n"
                        f"You MUST use these EXACT Japanese terms when they appear:\n"
                        f"{rules_text}\n"
                        f"CRITICAL RULES:\n"
                        f"- These terms are ALREADY TRANSLATED. Use the EXACT Japanese text shown above.\n"
                        f"- Do NOT re-translate, paraphrase, or invent new translations for these terms.\n"
                        f"- Singular/plural variants must also use the same translation.\n"
                    )
            except Exception as e:
                print(f"TM term lookup skipped: {e}")

        if self.memory:
            try:
                similar_examples = self.memory.find_similar(
                    batch_texts, mod_name=self.mod_name, limit=5
                )
                if similar_examples:
                    examples_text = "\n".join(
                        [f'  "{s}" → "{t}"' for s, t in similar_examples]
                    )
                    system_content += (
                        f"\n\n=== SIMILAR TRANSLATIONS FROM MEMORY ===\n"
                        f"These are similar texts that were previously translated. Use as style/terminology reference:\n"
                        f"{examples_text}\n"
                    )
                    print(f"[TM-SIMILAR] Added {len(similar_examples)} similar examples to prompt")
            except Exception as e:
                print(f"TM similar lookup skipped: {e}")

        if completed_context:
            context_examples = self._select_context_examples(
                completed_context, list(items.keys()), limit=8
            )
            if context_examples:
                examples_text = "\n".join(
                    [f'  "{s}" → "{t}"' for s, t in context_examples]
                )
                proper_nouns = self._extract_proper_noun_rules(context_examples)
                noun_rules = ""
                if proper_nouns:
                    noun_rules = "\n\nMANDATORY REPLACEMENT RULES (apply BEFORE translating):\n" + "\n".join(
                        f"  {en} → {ja}" for en, ja in proper_nouns
                    )
                system_content += (
                    f"\n\n=== PREVIOUSLY TRANSLATED IN THIS SESSION ===\n"
                    f"These items were already translated. You MUST use the EXACT same terminology:\n"
                    f"{examples_text}\n"
                    f"CRITICAL: Do NOT re-translate or paraphrase terms that appear above. Use them EXACTLY.\n"
                    f"{noun_rules}"
                )

        print(f"\n===== TRANSLATION PROMPT DEBUG =====")
        print(f"[MODEL] {self.model}")
        print(f"[MOD] {self.mod_name}")
        print(f"[BATCH ITEMS] {len(unique_items)} items")
        print(f"[MEMORY] {'YES' if self.memory else 'NO'}")
        print(f"[GLOSSARY] {len(self.glossary) if self.glossary else 0} terms")
        print(f"[COMPLETED_CONTEXT] {len(completed_context) if completed_context else 0} items")
        print(f"[SYSTEM PROMPT LENGTH] {len(system_content)} chars")
        print(f"--- SYSTEM PROMPT (last 2000 chars) ---")
        print(system_content[-2000:])
        print(f"--- USER PROMPT (first 500 chars) ---")
        print(prompt_content[:500])
        print(f"===== END PROMPT DEBUG =====\n")

        data = {
            "model": self.model,
            "temperature": 0.3,
            "messages": [
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": (
                        f"Translate to natural {lang_english}. "
                        f"Restructure into natural Japanese flow. Output ONLY the JSON object.\n\n"
                        f"{prompt_content}"
                    )
                }
            ],
        }
        
        translated, token_info = self._call_llm(url, headers, data, len(protected_items), valid_keys=set(protected_items.keys()))
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
            if len(keys) == 1:
                if keys[0] in unique_results:
                    final_results[keys[0]] = unique_results[keys[0]]
            else:
                representative_key = keys[0]
                for key in keys:
                    if key in unique_results:
                        final_results[key] = unique_results[key]
                    elif representative_key in unique_results:
                        final_results[key] = unique_results[representative_key]
                if representative_key in validation_results:
                    for key in keys[1:]:
                        if key not in validation_results:
                            validation_results[key] = validation_results[representative_key].copy()

        self._apply_glossary_post_process(final_results, unique_items)

        return final_results, validation_results

    def _call_llm(self, url, headers, data, expected_count, valid_keys=None):
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
            recovered = _recover_partial_json(content, valid_keys=valid_keys, expected_count=expected_count)
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
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user",
                     "content": f"Translate to natural {lang_english}. Preserve keys and ⟨⟩ tokens exactly:\n\n{prompt}"}
                ],
            }

            try:
                translated, token_info = self._call_llm(url, headers, data, len(retry_protected), valid_keys=set(retry_protected.keys()))
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

            en_words = re.findall(r'[A-Za-z]+', en_norm)
            ja_compound_re = None
            if len(en_words) >= 2:
                katakana_parts = []
                for w in en_words:
                    w_lower = w.lower()
                    if w_lower in ('the', 'a', 'an', 'of', 'in', 'on', 'at', 'to', 'for', 'and', 'or'):
                        continue
                    katakana_parts.append(re.escape(w))
                if katakana_parts:
                    ja_compound_re = re.compile(r'[\u30A0-\u30FF\u3040-\u309F\u4E00-\u9FFF]*'
                                                + r'[\s\u30FB\u30F7\u30FC]?'.join(katakana_parts)
                                                + r'[\u30A0-\u30FF\u3040-\u309F\u4E00-\u9FFF]*',
                                                re.IGNORECASE)

            for key, translated in final_results.items():
                source = unique_items.get(key, self.items.get(key, ''))
                if not en_pattern.search(source):
                    continue

                if ja_term in translated:
                    continue

                if en_pattern.search(translated):
                    final_results[key] = en_pattern.sub(ja_term, translated)
                    continue

                if ja_compound_re and ja_compound_re.search(translated):
                    final_results[key] = ja_compound_re.sub(ja_term, translated)

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
