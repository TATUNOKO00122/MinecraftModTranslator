import requests
import json
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QThread, Signal

from logic.file_handler import TARGET_LANGUAGES


VARIABLE_PATTERNS = [
    r'\[calc:[a-zA-Z0-9_]+\]',        # [calc:blessed_hammer], [calc:damage]
    r'\[[a-zA-Z0-9_]+\]',             # [entity], [item], [tag], etc.
    r'\{[a-zA-Z_][a-zA-Z0-9_]*\}',   # {player}, {name}, {item}
    r'\{[0-9]+\}',                    # {0}, {1}, {2}
    r'\{[0-9]+\$[sdf]\}',             # {0$s}, {1$d}, {2$f}
    r'%[0-9]*\$?[sdf]',               # %s, %d, %1$s, %2$d, %.2f
    r'%%',                            # Escaped percent
    r'§[0-9a-fk-or]',                 # Minecraft color codes §a, §r, §l, etc.
    r'&[0-9a-fk-or]',                 # Alternate color codes &a, &r, etc.
    r'[☀⚔❤✦✧★☆➤►▸●◆◇▲▼✹❂❃❄✴✵✶]',    # Game UI decorative symbols
    r'\\n',                           # Newline escape
    r'<br\s*/?>',                     # HTML line breaks
]

# Compile patterns for efficiency
COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in VARIABLE_PATTERNS]


def protect_variables(text):
    """
    Replace variables/format codes with placeholders before translation.
    Returns (protected_text, list_of_original_variables)
    """
    if not text or not isinstance(text, str):
        return text, []
    
    variables = []
    result = text
    
    for pattern in COMPILED_PATTERNS:
        matches = list(pattern.finditer(result))
        if not matches:
            continue
        
        replacements = []
        for match in matches:
            placeholder = f"__VAR_{len(variables)}__"
            variables.append(match.group())
            replacements.append((match.start(), match.end(), placeholder))
        
        for start, end, placeholder in reversed(replacements):
            result = result[:start] + placeholder + result[end:]
    
    result, variables = _merge_adjacent_placeholders(result, variables)
    
    return result, variables


def _merge_adjacent_placeholders(text, variables):
    """隣接する __VAR_N__ を単一プレースホルダに統合し、LLMによる順序入れ替えを防止する。"""
    merged = []
    var_ref = re.compile(r'__VAR_(\d+)__')
    
    def replacer(m):
        parts = []
        last_end = 0
        full = m.group()
        for vm in var_ref.finditer(full):
            if vm.start() > last_end:
                parts.append(full[last_end:vm.start()])
            parts.append(variables[int(vm.group(1))])
            last_end = vm.end()
        merged.append(''.join(parts))
        return f'__VAR_{len(merged) - 1}__'
    
    result = re.sub(r'__VAR_\d+__(?:\s*__VAR_\d+__)*', replacer, text)
    return result, merged


def restore_variables(text, variables):
    """
    Restore original variables from placeholders after translation.
    """
    if not text or not isinstance(text, str) or not variables:
        return text
    
    result = text
    for i, var in enumerate(variables):
        placeholder = f"__VAR_{i}__"
        result = result.replace(placeholder, var)
    
    return result


def validate_translation(original, translated):
    """
    Validate that translation doesn't have issues.
    Returns (is_valid, list_of_issues)
    
    Checks:
    - Nested braces
    - Full-width format specifiers
    - Unreplaced placeholders
    - Placeholder count mismatch
    - Extreme length changes
    - Untranslated text (English remaining)
    """
    issues = []
    
    if not translated:
        return True, issues
    
    if not original:
        original = ""
    
    # Check for nested braces {{...}}
    if '{{' in translated or '}}' in translated:
        issues.append(f"Nested braces detected: {translated[:50]}...")
    
    # Check for full-width format specifiers (common LLM mistake)
    fullwidth_patterns = [
        (r'％[ｓｄｆ]', '%s/%d/%f with fullwidth'),
        (r'｛[^｝]*｝', 'Fullwidth braces'),
    ]
    for pattern, desc in fullwidth_patterns:
        if re.search(pattern, translated):
            issues.append(f"{desc} detected in: {translated[:50]}...")
    
    # Check if placeholder wasn't properly restored
    if '__VAR_' in translated:
        issues.append(f"Unreplaced placeholder in: {translated[:50]}...")
    
    # Check placeholder count mismatch
    original_placeholders = len(re.findall(r'\{[^}]+\}|%[0-9]*\$?[sdf]', original))
    translated_placeholders = len(re.findall(r'\{[^}]+\}|%[0-9]*\$?[sdf]', translated))
    if original_placeholders != translated_placeholders:
        issues.append(f"Placeholder count mismatch: original={original_placeholders}, translated={translated_placeholders}")
    
    # Check for extreme length changes (more than 4x longer or less than 1/4)
    if len(original) > 10:  # Only check for non-trivial strings
        ratio = len(translated) / len(original)
        if ratio > 4:
            issues.append(f"Translation is {ratio:.1f}x longer than original")
        elif ratio < 0.25:
            issues.append(f"Translation is {ratio:.1f}x shorter than original")
    
    # Check if translation looks untranslated (mostly ASCII for Japanese target)
    # Skip if original has mostly non-ASCII (already in target language)
    ascii_ratio_original = sum(1 for c in original if ord(c) < 128) / max(len(original), 1)
    ascii_ratio_translated = sum(1 for c in translated if ord(c) < 128) / max(len(translated), 1)
    
    # If original is mostly English (high ASCII) but translation is also mostly ASCII,
    # it might not have been translated
    if ascii_ratio_original > 0.8 and ascii_ratio_translated > 0.9 and len(translated) > 20:
        # Check if it's not just numbers/symbols
        if re.search(r'[A-Za-z]{4,}', translated):
            issues.append(f"May be untranslated (high ASCII ratio): {translated[:30]}...")
    
    return len(issues) == 0, issues



class TranslatorThread(QThread):
    progress = Signal(int, int) # current, total
    finished = Signal(dict)
    stopped = Signal(dict)  # Emitted when stopped with partial results
    error = Signal(str)
    partial_save = Signal(dict)  # Signal for progressive saving
    validation_finished = Signal(dict)  # {key: {"issues": [...], "reviewed": False}}

    def __init__(self, items, api_key, model, glossary=None, parallel_count=3, 
                 memory=None, mod_name=None, target_lang="ja_jp"):
        super().__init__()
        self.items = items
        self.api_key = api_key
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

    def _create_batches(self, items):
        """Create batches based on character count, not fixed item count."""
        batches = []
        current_batch = {}
        current_chars = 0
        
        for key, text in items.items():
            text_len = len(str(text)) if text else 0
            
            # If adding this item would exceed target and we have items, start new batch
            if current_chars + text_len > self.target_batch_chars and len(current_batch) >= self.min_batch_size:
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
            
            current_batch[key] = text
            current_chars += text_len
            
            # Force new batch if max size reached
            if len(current_batch) >= self.max_batch_size:
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
        
        # Add remaining items
        if current_batch:
            batches.append(current_batch)
        
        return batches

    def run(self):
        results = {}
        validation_results = {}
        
        batches = self._create_batches(self.items)
        total_items = len(self.items)
        processed = 0
        
        if self.parallel_count == 1:
            for batch_idx, batch_items in enumerate(batches):
                if not self.is_running:
                    self.stopped.emit(results)
                    return
                
                try:
                    translated_batch, batch_validation = self.translate_batch_with_retry(batch_items)
                    results.update(translated_batch)
                    validation_results.update(batch_validation)
                    self._partial_results = results.copy()
                    
                    self._batches_since_save += 1
                    if self._batches_since_save >= self.save_interval:
                        self._progressive_save(results, batch_items)
                        self._batches_since_save = 0
                except Exception as e:
                    print(f"Batch {batch_idx + 1} failed: {e}")
                    self.error.emit(f"Batch {batch_idx + 1} failed: {e}")
                
                if not self.is_running:
                    self._progressive_save(results, {})
                    self.stopped.emit(results)
                    return
                
                processed += len(batch_items)
                self.progress.emit(processed, total_items)
                time.sleep(0.5)
        else:
            self._run_parallel(batches, results, validation_results, total_items)
            if not self.is_running:
                self.stopped.emit(results)
                return

        if validation_results:
            self.validation_finished.emit(validation_results)
        self.finished.emit(results)

    def _run_parallel(self, batches, results, validation_results, total_items):
        """Execute batches in parallel using ThreadPoolExecutor."""
        processed = 0
        current_parallel = self.parallel_count
        
        batch_index = 0
        while batch_index < len(batches):
            if not self.is_running:
                return
            
            chunk_end = min(batch_index + current_parallel, len(batches))
            batch_chunk = [(i, batches[i]) for i in range(batch_index, chunk_end)]
            
            with ThreadPoolExecutor(max_workers=current_parallel) as executor:
                future_to_batch = {
                    executor.submit(self._translate_batch_safe, idx, batch_items): (idx, batch_items)
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
                            self._partial_results = results.copy()
                        
                        if rate_limited and current_parallel > 1:
                            current_parallel = max(1, current_parallel - 1)
                            print(f"Rate limit hit, reducing parallel count to {current_parallel}")
                            
                    except Exception as e:
                        print(f"Batch {idx + 1} failed: {e}")
                        self.error.emit(f"Batch {idx + 1} failed: {e}")
                    
                    processed += len(batch_items)
                    self.progress.emit(processed, total_items)
            
            batch_index = chunk_end
            
            if batch_index < len(batches):
                time.sleep(0.3)
    
    def _translate_batch_safe(self, batch_idx, items):
        """Thread-safe wrapper for translate_batch_with_retry.
        Returns tuple of (results, validation_results, rate_limited_flag)."""
        rate_limited = False
        try:
            result, validation = self.translate_batch_with_retry(items)
            return result, validation, rate_limited
        except Exception as e:
            if "429" in str(e):
                rate_limited = True
            raise

    def translate_batch_with_retry(self, items, max_retries=3):
        retries = 0
        while retries < max_retries:
            if not self.is_running:
                return {}, {}
            try:
                return self.translate_batch(items)
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

    def translate_batch(self, items):
        if not items:
            return {}, {}
        
        if not self.is_running:
            return {}, {}
        
        protected_items = {}
        variable_map = {}
        
        for key, text in items.items():
            if text and isinstance(text, str):
                protected_text, variables = protect_variables(text)
                protected_items[key] = protected_text
                variable_map[key] = variables
            else:
                protected_items[key] = text
                variable_map[key] = []
            
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Minecraft MOD Translator Desktop"
        }
        
        prompt_content = json.dumps(protected_items, ensure_ascii=False)
        
        lang_info = TARGET_LANGUAGES.get(self.target_lang, ("Japanese", "日本語"))
        lang_english = lang_info[0]
        
        system_content = (
            f"You are a professional translator for Minecraft mods and RPG games.\n"
            f"Your task is to translate English text into natural {lang_english}.\n"
            f"You will receive a JSON object. The keys are identifiers (DO NOT CHANGE KEYS). The values are the English text to translate.\n\n"
            f"=== FORMAT RULES ===\n"
            f"1. Translate the value of each key from English to {lang_english}.\n"
            f"2. CRITICAL: Keep ALL placeholders like __VAR_0__, __VAR_1__ EXACTLY as they are. DO NOT modify, translate, or remove them.\n"
            f"3. CRITICAL: When multiple __VAR_N__ placeholders appear near each other, "
            f"you MUST keep their original left-to-right order. Do not swap or reorder adjacent placeholders.\n"
            f"4. Output ONLY the valid JSON object. No markdown formatting.\n\n"
            f"=== SYNTAX RULES ===\n"
            f"5. English post-modifiers (relative clauses with 'which', 'that', 'who') MUST be restructured into {lang_english} pre-modifiers. "
            f"Example: 'A sword which deals fire damage' → '{lang_english} equivalent with modifier before noun'.\n"
            f"6. NEVER translate word-by-word. Read the full sentence first, understand its meaning in context, then produce a natural {lang_english} sentence with correct grammar and word order.\n\n"
            f"=== CONTEXT & VOCABULARY RULES ===\n"
            f"7. All text is from Minecraft mods, RPG games, or fantasy settings. Translate with this context in mind.\n"
            f"8. Choose words that fit the game/fantasy context, not literal dictionary meanings:\n"
            f"   - 'Spiritual' in combat/magic context → mystic/divine/holy, NOT 'mental/psychological'\n"
            f"   - 'Throw' in attack/skill context → hurl/launch/cast, NOT 'toss away'\n"
            f"   - 'Spirit' in fantasy context → soul/phantom/aura, NOT 'enthusiasm'\n"
            f"   - 'Strike' in combat context → slash/smite/burst, NOT 'labor dispute'\n"
            f"   - Adapt all polysemous words to their in-game meaning, not the most common general meaning.\n"
            f"9. Use established Minecraft/gaming terms consistently. Keep proper nouns as transliterations.\n"
            f"10. Do NOT translate ONLY if the term is in the glossary below (keep glossary terms as-is).\n"
        )

        if self.glossary:
            # OPTIMIZATION: Only include glossary terms that appear in the source text
            batch_text_lower = " ".join([str(v) for v in items.values()]).lower()
            relevant_terms = {k: v for k, v in self.glossary.items() if k.lower() in batch_text_lower}
            
            if relevant_terms:
                glossary_text = "\n".join([f"- {k}: {v}" for k, v in relevant_terms.items()])
                system_content += f"\n\nUse the following glossary for consistency:\n{glossary_text}"

        data = {
            "model": self.model,
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
        
        response = requests.post(url, headers=headers, json=data, timeout=120)
        response.raise_for_status()
        
        result_json = response.json()
        content = result_json['choices'][0]['message']['content'].strip()
        
        # Clean up code blocks if present
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        # Extract JSON object from surrounding natural language
        first_brace = content.find('{')
        last_brace = content.rfind('}')
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            content = content[first_brace:last_brace + 1]
        
        translated = json.loads(content)
        
        final_results = {}
        validation_results = {}
        for key, translated_text in translated.items():
            if key in variable_map and variable_map[key]:
                restored_text = restore_variables(translated_text, variable_map[key])
                
                is_valid, issues = validate_translation(items.get(key, ''), restored_text)
                if not is_valid:
                    print(f"Translation warning for '{key}': {issues}")
                    validation_results[key] = {"issues": issues, "reviewed": False}
                
                final_results[key] = restored_text
            else:
                is_valid, issues = validate_translation(items.get(key, ''), translated_text)
                if not is_valid:
                    print(f"Translation warning for '{key}': {issues}")
                    validation_results[key] = {"issues": issues, "reviewed": False}
                
                final_results[key] = translated_text
        
        return final_results, validation_results

    def _progressive_save(self, results: dict, batch_sources: dict):
        """
        Save partial results to translation memory during translation.
        This prevents data loss if the translation is interrupted.
        """
        if not self.memory or not results:
            return
        
        try:
            # Set context for the memory update
            self.memory.set_context(
                mod_name=self.mod_name,
                model=self.model,
                sources=self.items  # Original source texts
            )
            
            # Update memory with partial results
            self.memory.update(results)
            
            # Emit signal for UI update
            self.partial_save.emit(results)
            
            print(f"Progressive save: {len(results)} translations saved")
        except Exception as e:
            print(f"Progressive save failed: {e}")

    def stop(self):
        self.is_running = False
