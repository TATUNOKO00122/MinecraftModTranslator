import json
import os


class Glossary:
    def __init__(self, filepath="glossary.json", default_glossary_path=None):
        self.filepath = filepath
        self.default_glossary_path = default_glossary_path
        self.terms = {}
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict) and data.get('version') == 2:
                    raw_terms = data.get('terms', {})
                else:
                    raw_terms = data if isinstance(data, dict) else {}
                self.terms = {}
                for key, value in raw_terms.items():
                    if isinstance(value, list):
                        self.terms[key] = value
                    else:
                        self.terms[key] = value
                return
            except Exception as e:
                print(f"Failed to load glossary: {e}")

        if self.default_glossary_path and os.path.exists(self.default_glossary_path):
            self._load_default_glossary()

    def _load_default_glossary(self):
        try:
            with open(self.default_glossary_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if ' → ' in line:
                        parts = line.split(' → ', 1)
                        key = parts[0].strip()
                        value = parts[1].strip()
                        if key and value:
                            self.terms[key] = value
            self.save()
        except Exception as e:
            print(f"Failed to load default glossary: {e}")

    def save(self):
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(self.terms, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Failed to save glossary: {e}")

    def update(self, new_terms):
        self.terms.update(new_terms)
        self.save()
        
    def set_terms(self, terms):
        self.terms = terms
        self.save()

    def get_terms(self):
        return self.terms
