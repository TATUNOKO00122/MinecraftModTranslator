"""
Translation Memory - Compatibility wrapper for v2 SQLite backend

This module maintains backward compatibility with existing code
while using the new SQLite-based TranslationMemoryV2 internally.
"""

import json
import os
from typing import Dict, Optional

# Import v2 implementation
from logic.translation_memory_v2 import TranslationMemoryV2, TranslationMemoryCompat


class TranslationMemory:
    """
    Translation Memory with SQLite backend.
    
    This is a drop-in replacement for the original JSON-based TranslationMemory.
    It uses SQLite internally for better performance with large datasets,
    while maintaining the same API for backward compatibility.
    """
    
    def __init__(self, db_path: str = "translation_memory.json"):
        # Determine paths
        if db_path.endswith('.json'):
            self.legacy_json_path = db_path
            self.db_path = db_path.replace('.json', '.db')
        else:
            self.db_path = db_path
            self.legacy_json_path = db_path.replace('.db', '.json')
        
        # Use v2 backend
        self._v2 = TranslationMemoryV2(self.db_path)
        
        # Compatibility: expose memory dict-like interface
        # Note: This is for backward compatibility only
        self._memory_cache = None
        
        # Context for updates
        self._current_mod_name: Optional[str] = None
        self._current_model: Optional[str] = None
        self._current_sources: Dict[str, str] = {}
    
    @property
    def memory(self) -> Dict[str, str]:
        """
        Compatibility property: Returns dict-like access to translations.
        Note: This loads all translations into memory - use sparingly for large datasets.
        """
        if self._memory_cache is None:
            # Lazy load for compatibility with code that accesses .memory directly
            self._memory_cache = self._v2.apply_to({})
        return self._memory_cache
    
    def set_context(self, mod_name: str = None, model: str = None, sources: Dict[str, str] = None):
        """
        Set context for subsequent update() calls.
        This allows storing metadata about translations.
        
        Args:
            mod_name: Name of the MOD being translated
            model: LLM model used for translation
            sources: Dict of {key: source_text} (original English text)
        """
        self._current_mod_name = mod_name
        self._current_model = model
        if sources:
            self._current_sources = sources
    
    def update(self, translations: Dict[str, str], mod_name: str = None, 
               model: str = None, sources: Dict[str, str] = None, origin: str = 'ai'):
        """
        Updates memory with new translations {key: text}.
        
        Args:
            translations: Dict of {key: translated_text}
            mod_name: Optional, override context mod_name
            model: Optional, override context model
            sources: Optional, override context sources
            origin: 'ai' | 'user' | 'ai_corrected'
        """
        if not translations:
            return
        
        effective_mod_name = mod_name or self._current_mod_name
        effective_model = model or self._current_model
        effective_sources = sources or self._current_sources
        
        self._v2.update(
            translations,
            mod_name=effective_mod_name,
            model=effective_model,
            sources=effective_sources,
            origin=origin
        )
        
        self._memory_cache = None
    
    def delete(self, keys: list, mod_name: str = None):
        """Delete translations by keys from memory."""
        if not keys:
            return
        self._v2.delete(keys, mod_name=mod_name)
        self._memory_cache = None
    
    def get(self, key: str, mod_name: str = None) -> Optional[str]:
        """Get translation by key."""
        return self._v2.get(key, mod_name=mod_name)
    
    def get_with_context(self, key: str, mod_name: str = None) -> Optional[dict]:
        """
        Get translation with full context metadata.
        
        Returns dict with keys: key, source, translation, mod_name, category, 
                                model, translated_at, reviewed
        """
        return self._v2.get_with_context(key, mod_name=mod_name)
    
    def apply_to(self, target_data: Dict[str, str], mod_name: str = None) -> Dict[str, str]:
        """
        Returns a dict of translations for the given target_data {key: original}
        found in memory. mod_name があればそのMODのエントリを優先。
        """
        return self._v2.apply_to(target_data, mod_name=mod_name)
    
    def save(self):
        """Commit any pending changes."""
        self._v2.save()
    
    def mark_reviewed(self, keys: list, reviewed: bool = True, mod_name: str = None):
        """Mark translations as reviewed."""
        self._v2.mark_reviewed(keys, reviewed, mod_name=mod_name)
    
    def get_unreviewed_count(self) -> int:
        """Get count of unreviewed translations."""
        return self._v2.get_unreviewed_count()
    
    def batch_get_review_status(self, keys, mod_name: str = None) -> Dict[str, dict]:
        """
        Batch get review status for multiple keys.
        Returns dict of {key: {"reviewed": bool, "origin": str}}
        """
        return self._v2.batch_get_review_status(keys, mod_name=mod_name)
    
    def find_changed_sources(self, current_data: Dict[str, str], mod_name: str = None) -> Dict[str, tuple]:
        """
        Find keys where source text has changed since last translation.
        Returns dict of {key: (old_source, new_source)}
        """
        return self._v2.find_changed_sources(current_data, mod_name=mod_name)
    
    def find_similar(self, batch_texts: list, mod_name: str = None,
                     limit: int = 5) -> list:
        return self._v2.find_similar(batch_texts, mod_name=mod_name, limit=limit)

    def find_term_translations(self, batch_texts: list, cross_mod_data: dict = None,
                                exclude_keys: set = None,
                                limit: int = 30,
                                cross_mod_index: dict = None,
                                same_mod_data: dict = None) -> list:
        return self._v2.find_term_translations(
            batch_texts, cross_mod_data=cross_mod_data,
            exclude_keys=exclude_keys,
            limit=limit,
            cross_mod_index=cross_mod_index,
            same_mod_data=same_mod_data
        )

    def get_stats(self) -> dict:
        """Get statistics about the translation memory."""
        return self._v2.get_stats()
    
    def export_to_json(self, path: str = None) -> str:
        """Export all translations to JSON format for backup."""
        return self._v2.export_to_json(path)
    
    def close(self):
        """Close database connection."""
        self._v2.close()
