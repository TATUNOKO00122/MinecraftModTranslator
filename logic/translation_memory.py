import json
import os

class TranslationMemory:
    def __init__(self, db_path="translation_memory.json"):
        self.db_path = db_path
        self.memory = self._load()

    def _load(self):
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save(self):
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(self.memory, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Failed to save translation memory: {e}")

    def update(self, translations):
        """Updates memory with new translations {key: text}."""
        updated = False
        for key, text in translations.items():
            if text and self.memory.get(key) != text:
                self.memory[key] = text
                updated = True
        
        if updated:
            self.save()

    def get(self, key):
        return self.memory.get(key)

    def apply_to(self, target_data):
        """
        Returns a dict of translations for the given target_data {key: original}
        found in memory.
        """
        results = {}
        for key in target_data.keys():
            if key in self.memory:
                results[key] = self.memory[key]
        return results
