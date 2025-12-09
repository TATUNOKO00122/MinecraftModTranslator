import json
import os

class Glossary:
    def __init__(self, filepath="glossary.json"):
        self.filepath = filepath
        self.terms = {} # { "original": "translation" }
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r', encoding='utf-8') as f:
                    self.terms = json.load(f)
            except Exception as e:
                print(f"Failed to load glossary: {e}")
                self.terms = {}

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
