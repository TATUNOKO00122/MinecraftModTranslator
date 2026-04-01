import requests
import json
import time
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QThread, Signal


# Variable patterns to protect during translation
VARIABLE_PATTERNS = [
    r'\{[a-zA-Z_][a-zA-Z0-9_]*\}',   # {player}, {name}, {item}
    r'\{[0-9]+\}',                    # {0}, {1}, {2}
    r'\{[0-9]+\$[sdf]\}',             # {0$s}, {1$d}, {2$f}
    r'%[0-9]*\$?[sdf]',               # %s, %d, %1$s, %2$d, %.2f
    r'%%',                            # Escaped percent
    r'§[0-9a-fk-or]',                 # Minecraft color codes §a, §r, §l, etc.
    r'&[0-9a-fk-or]',                 # Alternate color codes &a, &r, etc.
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
    
    # Find and replace all variables with unique placeholders
    for pattern in COMPILED_PATTERNS:
        matches = list(pattern.finditer(result))
        # Process matches in reverse order to preserve indices
        for match in reversed(matches):
            var = match.group()
            # Check if this exact position hasn't been replaced yet
            placeholder = f"__VAR_{len(variables)}__"
            result = result[:match.start()] + placeholder + result[match.end():]
            variables.insert(0, var)  # Insert at beginning since we're going in reverse
    
    return result, variables


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

    def __init__(self, items, api_key, model, glossary=None, parallel_count=3, 
                 memory=None, mod_name=None):
        super().__init__()
        self.items = items # dict of key: source
        self.api_key = api_key
        self.model = model
        self.glossary = glossary or {}
        self.is_running = True
        self._partial_results = {}  # Store partial results for stop
        # Dynamic batch sizing: target ~4000 characters per batch
        self.target_batch_chars = 4000
        self.min_batch_size = 5    # Minimum items per batch
        self.max_batch_size = 150  # Maximum items per batch
        # Parallel processing settings
        self.parallel_count = max(1, min(parallel_count, 10))  # Clamp between 1-10
        self._rate_limit_hit = False  # Flag for adaptive throttling
        # Progressive save settings
        self.memory = memory  # TranslationMemory instance for progressive saving
        self.mod_name = mod_name  # MOD name for context
        self._batches_since_save = 0
        self.save_interval = 5  # Save every N batches

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
        
        # Create dynamic batches based on character count
        batches = self._create_batches(self.items)
        total_items = len(self.items)
        processed = 0
        
        if self.parallel_count == 1:
            # Sequential processing (original behavior)
            for batch_idx, batch_items in enumerate(batches):
                if not self.is_running:
                    self.stopped.emit(results)
                    return
                
                try:
                    translated_batch = self.translate_batch_with_retry(batch_items)
                    results.update(translated_batch)
                    self._partial_results = results.copy()
                    
                    # Progressive save
                    self._batches_since_save += 1
                    if self._batches_since_save >= self.save_interval:
                        self._progressive_save(results, batch_items)
                        self._batches_since_save = 0
                except Exception as e:
                    print(f"Batch {batch_idx + 1} failed: {e}")
                    self.error.emit(f"Batch {batch_idx + 1} failed: {e}")
                
                if not self.is_running:
                    self._progressive_save(results, {})  # Save before stopping
                    self.stopped.emit(results)
                    return
                
                processed += len(batch_items)
                self.progress.emit(processed, total_items)
                time.sleep(0.5)
        else:
            # Parallel processing with ThreadPoolExecutor
            self._run_parallel(batches, results, total_items)
            if not self.is_running:
                self.stopped.emit(results)
                return

        self.finished.emit(results)

    def _run_parallel(self, batches, results, total_items):
        """Execute batches in parallel using ThreadPoolExecutor."""
        processed = 0
        current_parallel = self.parallel_count
        
        # Process batches in chunks of parallel_count
        batch_index = 0
        while batch_index < len(batches):
            if not self.is_running:
                return
            
            # Get next chunk of batches to process in parallel
            chunk_end = min(batch_index + current_parallel, len(batches))
            batch_chunk = [(i, batches[i]) for i in range(batch_index, chunk_end)]
            
            with ThreadPoolExecutor(max_workers=current_parallel) as executor:
                # Submit all batches in the chunk
                future_to_batch = {
                    executor.submit(self._translate_batch_safe, idx, batch_items): (idx, batch_items)
                    for idx, batch_items in batch_chunk
                }
                
                # Collect results as they complete
                for future in as_completed(future_to_batch):
                    if not self.is_running:
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    
                    idx, batch_items = future_to_batch[future]
                    try:
                        translated_batch, rate_limited = future.result()
                        if translated_batch:
                            results.update(translated_batch)
                            self._partial_results = results.copy()
                        
                        # Adaptive throttling: reduce parallel count on rate limit
                        if rate_limited and current_parallel > 1:
                            current_parallel = max(1, current_parallel - 1)
                            print(f"Rate limit hit, reducing parallel count to {current_parallel}")
                            
                    except Exception as e:
                        print(f"Batch {idx + 1} failed: {e}")
                        self.error.emit(f"Batch {idx + 1} failed: {e}")
                    
                    processed += len(batch_items)
                    self.progress.emit(processed, total_items)
            
            batch_index = chunk_end
            
            # Short delay between parallel chunks to avoid rate limiting
            if batch_index < len(batches):
                time.sleep(0.3)
    
    def _translate_batch_safe(self, batch_idx, items):
        """Thread-safe wrapper for translate_batch_with_retry.
        Returns tuple of (results, rate_limited_flag)."""
        rate_limited = False
        try:
            result = self.translate_batch_with_retry(items)
            return result, rate_limited
        except Exception as e:
            if "429" in str(e):
                rate_limited = True
            raise

    def translate_batch_with_retry(self, items, max_retries=3):
        retries = 0
        while retries < max_retries:
            if not self.is_running:
                return {}
            try:
                return self.translate_batch(items)
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    retries += 1
                    wait_time = 2 ** retries # Exponential backoff
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
            return {}
        
        if not self.is_running:
            return {}
        
        # Step 1: Protect variables before sending to LLM
        protected_items = {}
        variable_map = {}  # key -> list of variables
        
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
        
        # Prepare content as JSON (with protected variables)
        prompt_content = json.dumps(protected_items, ensure_ascii=False)
        
        system_content = (
            "You are a professional translator for Minecraft mods. "
            "Your task is to translate English text into natural Japanese.\n"
            "You will receive a JSON object. The keys are identifiers (DO NOT CHANGE KEYS). The values are the English text to translate.\n"
            "Rules:\n"
            "1. Translate the value of each key from English to Japanese.\n"
            "2. CRITICAL: Keep ALL placeholders like __VAR_0__, __VAR_1__ EXACTLY as they are. DO NOT modify, translate, or remove them.\n"
            "3. TRANSLATION STYLE:\n"
            "   A) Use ESTABLISHED Minecraft/gaming terms in Japanese (these are standard):\n"
            "     * 'Enchant/Enchanted' → 'エンチャント/エンチャントされた' (NOT '魔法' or '魔法の')\n"
            "     * 'Nether' → 'ネザー'\n"
            "     * 'Ender' → 'エンダー'\n"
            "     * 'Golem' → 'ゴーレム'\n"
            "     * 'Potion' → 'ポーション'\n"
            "     * 'Level' → 'レベル'\n"
            "     * 'Skill' → 'スキル'\n"
            "     * 'Quest' → 'クエスト'\n"
            "   B) Use NATURAL Japanese for common descriptive words:\n"
            "     * 'Magic Sword' → '魔法の剣' (NOT 'マジックソード')\n"
            "     * 'Fire Elemental' → '炎の精霊'\n"
            "     * 'Healing Potion' → '回復ポーション'\n"
            "     * 'Dragon Scale' → '竜の鱗'\n"
            "     * 'Ice Golem' → '氷のゴーレム'\n"
            "   C) Use katakana for original proper nouns (unique made-up names):\n"
            "     * 'Everbright' → 'エバーブライト'\n"
            "   Examples:\n"
            "     * 'Enchanted Bow' → 'エンチャントされた弓' (NOT '魔法の弓')\n"
            "     * 'Enchanted Diamond Sword' → 'エンチャントされたダイヤの剣'\n"
            "4. Do NOT translate ONLY if the term is in the glossary below (keep glossary terms as-is).\n"
            "5. Output ONLY the valid JSON object. Do not include markdown formatting (```json ... ```)."
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
                    "content": f"Translate the following values to Japanese:\n\n{prompt_content}"
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
        
        translated = json.loads(content)
        
        # Step 2: Restore variables and validate
        final_results = {}
        for key, translated_text in translated.items():
            if key in variable_map and variable_map[key]:
                # Restore original variables
                restored_text = restore_variables(translated_text, variable_map[key])
                
                # Validate the result
                is_valid, issues = validate_translation(items.get(key, ''), restored_text)
                if not is_valid:
                    print(f"Translation warning for '{key}': {issues}")
                    # Still use the restored text, but log the warning
                
                final_results[key] = restored_text
            else:
                # No variables to restore, validate directly
                is_valid, issues = validate_translation(items.get(key, ''), translated_text)
                if not is_valid:
                    print(f"Translation warning for '{key}': {issues}")
                
                final_results[key] = translated_text
        
        return final_results

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
