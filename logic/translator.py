import requests
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from PySide6.QtCore import QThread, Signal

class TranslatorThread(QThread):
    progress = Signal(int, int) # current, total
    finished = Signal(dict)
    stopped = Signal(dict)  # Emitted when stopped with partial results
    error = Signal(str)

    def __init__(self, items, api_key, model, glossary=None, parallel_count=3):
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
                except Exception as e:
                    print(f"Batch {batch_idx + 1} failed: {e}")
                    self.error.emit(f"Batch {batch_idx + 1} failed: {e}")
                
                if not self.is_running:
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
            
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Minecraft MOD Translator Desktop"
        }
        
        # Prepare content as JSON
        prompt_content = json.dumps(items, ensure_ascii=False)
        
        system_content = (
            "You are a professional translator for Minecraft mods. "
            "Your task is to translate English text into natural Japanese.\n"
            "You will receive a JSON object. The keys are identifiers (DO NOT CHANGE KEYS). The values are the English text to translate.\n"
            "Rules:\n"
            "1. Translate the value of each key from English to Japanese.\n"
            "2. CRITICAL: Keep ALL format codes EXACTLY as they are, but ALWAYS TRANSLATE the text between them:\n"
            "   - %s, %d, %1$s, %2$d, %.2f, etc. (format specifiers) - keep as-is\n"
            "   - §a, §r, §l, §e, §9, etc. (Minecraft color codes with §) - keep as-is\n"
            "   - &a, &r, &l, &e, &9, etc. (Minecraft color codes with &) - keep as-is\n"
            "   - <br>, \\n, etc. (line breaks) - keep as-is\n"
            "   - {0}, {1}, {name}, etc. (template variables) - keep as-is\n"
            "   DO NOT convert them to full-width characters (e.g. ％ｓ is WRONG).\n"
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
            
        return json.loads(content)

    def stop(self):
        self.is_running = False
