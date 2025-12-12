import re
from collections import defaultdict
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

