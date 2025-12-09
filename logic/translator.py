import requests
import json
import time
from PySide6.QtCore import QThread, Signal

class TranslatorThread(QThread):
    progress = Signal(int, int) # current, total
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, items, api_key, model, glossary=None):
        super().__init__()
        self.items = items # dict of key: source
        self.api_key = api_key
        self.model = model
        self.glossary = glossary or {}
        self.is_running = True
        self.batch_size = 20 # Reduced from 50 to avoid timeouts/rate limits

    def run(self):
        results = {}
        keys = list(self.items.keys())
        total = len(keys)
        
        # Split into batches
        for i in range(0, total, self.batch_size):
            if not self.is_running:
                break
                
            batch_keys = keys[i:i+self.batch_size]
            batch_items = {k: self.items[k] for k in batch_keys}
            
            try:
                translated_batch = self.translate_batch_with_retry(batch_items)
                results.update(translated_batch)
            except Exception as e:
                print(f"Batch failed: {e}")
                self.error.emit(f"Batch failed: {e}")
                # Fallback removed as per user request
                # Failed items will simply not be in 'results', remaining untranslated in UI
            
            self.progress.emit(min(i + self.batch_size, total), total)
            
            # Increased delay to be safer against rate limits
            time.sleep(1.0) 

        self.finished.emit(results)

    def translate_batch_with_retry(self, items, max_retries=3):
        retries = 0
        while retries < max_retries:
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
                # Other errors, maybe retry once?
                print(f"Error during translation: {e}")
                retries += 1 # Retry strict network errors too
                time.sleep(1)
                
        raise Exception("Max retries exceeded")

    def translate_batch(self, items):
        if not items:
            return {}
            
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Title": "Minecraft MOD Translator Desktop"
        }
        
        # Prepare content as JSON
        # We ask LLM to translate values in the JSON object
        prompt_content = json.dumps(items, ensure_ascii=False)
        
        system_content = (
            "You are a professional translator for Minecraft mods. "
            "Your task is to translate English text into Japanese.\n"
            "You will receive a JSON object. The keys are identifiers (DO NOT CHANGE KEYS). The values are the English text to translate.\n"
            "Rules:\n"
            "1. Translate the value of each key from English to Japanese.\n"
            "2. CRITICAL: Keep ALL format codes EXACTLY as they are, but TRANSLATE the text between them:\n"
            "   - %s, %d, %1$s, %2$d, %.2f, etc. (format specifiers) - keep as-is\n"
            "   - §a, §r, §l, §e, §9, etc. (Minecraft color codes with §) - keep as-is\n"
            "   - &a, &r, &l, &e, &9, etc. (Minecraft color codes with &) - keep as-is\n"
            "   - <br>, \\n, etc. (line breaks) - keep as-is\n"
            "   - {0}, {1}, {name}, etc. (template variables) - keep as-is\n"
            "   DO NOT convert them to full-width characters (e.g. ％ｓ is WRONG).\n"
            "3. IMPORTANT: Text between color codes MUST be translated. For example:\n"
            "   - '&eEverbright&r' should become '&eエバーブライト&r' (translate 'Everbright' to Japanese)\n"
            "   - '&9Blue Journal&r' should become '&9青い日誌&r' (translate 'Blue Journal' to Japanese)\n"
            "   - Place names, item names, location names inside color codes SHOULD be translated.\n"
            "4. Do NOT translate:\n"
            "   - Mod names (e.g., 'Mine and Slash', 'FTB Teams', 'Lightman's Currency')\n"
            "   - Technical identifiers that look like code\n"
            "5. Output ONLY the valid JSON object. Do not include markdown formatting (```json ... ```)."
        )

        if self.glossary:
            # OPTIMIZATION: Only include glossary terms that appear in the source text
            # This reduces token usage and noise
            # Ensure all values are strings before join (handle numbers etc)
            batch_text = " ".join([str(v) for v in items.values()])
            relevant_terms = {k: v for k, v in self.glossary.items() if k in batch_text}
            
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
            # "response_format": { "type": "json_object" } # Not all openrouter models support this, so safe to prompt engineering
        }
        
        response = requests.post(url, headers=headers, json=data)
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
