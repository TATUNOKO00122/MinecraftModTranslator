"""
Translation Memory v2 - SQLite-based with context storage

Features:
- SQLite database for better performance with large datasets
- Context storage (mod_name, category, model, timestamp, reviewed flag)
- Automatic migration from legacy JSON format
- Fuzzy matching support (similarity search)
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Dict, Optional, List, Tuple


class TranslationMemoryV2:
    """SQLite-based translation memory with context storage."""
    
    def __init__(self, db_path: str = "translation_memory.db"):
        self.db_path = db_path
        self.legacy_json_path = db_path.replace('.db', '.json')
        self._conn = None
        self._init_db()
        self._migrate_from_json_if_needed()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Main translations table with context
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                mod_name TEXT,
                category TEXT,
                model TEXT,
                translated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed INTEGER DEFAULT 0,
                source_hash TEXT
            )
        ''')
        
        # Indexes for fast lookup
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_key ON translations(key)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source ON translations(source)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_hash ON translations(source_hash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_reviewed ON translations(reviewed)')
        
        conn.commit()
    
    def _migrate_from_json_if_needed(self):
        """Migrate from legacy JSON format if exists and DB is empty."""
        if not os.path.exists(self.legacy_json_path):
            return
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM translations')
        count = cursor.fetchone()[0]
        
        if count > 0:
            # Database already has data, skip migration
            return
        
        print(f"Migrating from {self.legacy_json_path} to SQLite...")
        try:
            with open(self.legacy_json_path, 'r', encoding='utf-8') as f:
                legacy_data = json.load(f)
            
            # Batch insert for performance
            batch_size = 1000
            items = list(legacy_data.items())
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                cursor.executemany('''
                    INSERT OR IGNORE INTO translations (key, source, translation, source_hash)
                    VALUES (?, ?, ?, ?)
                ''', [(key, text, text, self._hash_text(text)) for key, text in batch])
                conn.commit()
                print(f"  Migrated {min(i + batch_size, len(items))}/{len(items)} entries...")
            
            print(f"Migration complete: {len(legacy_data)} entries migrated.")
            
            # Rename old JSON file as backup
            backup_path = self.legacy_json_path + '.migrated'
            if not os.path.exists(backup_path):
                os.rename(self.legacy_json_path, backup_path)
                print(f"Legacy JSON renamed to {backup_path}")
                
        except Exception as e:
            print(f"Migration failed: {e}")
    
    def _hash_text(self, text: str) -> str:
        """Create a simple hash for similarity matching."""
        if not text:
            return ""
        # Normalize and create a simple hash for grouping similar texts
        normalized = text.lower().strip()
        return str(hash(normalized) % (10 ** 10))
    
    def _detect_category(self, key: str) -> str:
        """Detect category from translation key."""
        key_lower = key.lower()
        if key_lower.startswith('item.'):
            return 'item'
        elif key_lower.startswith('block.'):
            return 'block'
        elif key_lower.startswith('entity.'):
            return 'entity'
        elif key_lower.startswith('enchantment.'):
            return 'enchantment'
        elif key_lower.startswith('effect.'):
            return 'effect'
        elif key_lower.startswith('advancement.'):
            return 'advancement'
        elif key_lower.startswith('gui.') or key_lower.startswith('screen.'):
            return 'ui'
        elif 'tooltip' in key_lower or 'description' in key_lower:
            return 'tooltip'
        elif 'quest' in key_lower or 'ftb' in key_lower:
            return 'quest'
        else:
            return 'other'
    
    def update(self, translations: Dict[str, str], mod_name: str = None, 
               model: str = None, sources: Dict[str, str] = None):
        """
        Update memory with new translations.
        
        Args:
            translations: Dict of {key: translated_text}
            mod_name: Name of the MOD being translated
            model: LLM model used for translation
            sources: Dict of {key: source_text} (original English text)
        """
        if not translations:
            return
        
        conn = self._get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        
        for key, translation in translations.items():
            if not translation:
                continue
            
            source = sources.get(key, '') if sources else ''
            category = self._detect_category(key)
            source_hash = self._hash_text(source)
            
            cursor.execute('''
                INSERT INTO translations (key, source, translation, mod_name, category, model, translated_at, source_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    translation = excluded.translation,
                    mod_name = COALESCE(excluded.mod_name, translations.mod_name),
                    category = excluded.category,
                    model = COALESCE(excluded.model, translations.model),
                    translated_at = excluded.translated_at,
                    source = CASE WHEN excluded.source != '' THEN excluded.source ELSE translations.source END,
                    source_hash = CASE WHEN excluded.source_hash != '' THEN excluded.source_hash ELSE translations.source_hash END
            ''', (key, source, translation, mod_name, category, model, now, source_hash))
        
        conn.commit()
    
    def get(self, key: str) -> Optional[str]:
        """Get translation by key."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT translation FROM translations WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row['translation'] if row else None
    
    def get_with_context(self, key: str) -> Optional[dict]:
        """Get translation with full context."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT key, source, translation, mod_name, category, model, translated_at, reviewed
            FROM translations WHERE key = ?
        ''', (key,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    def apply_to(self, target_data: Dict[str, str]) -> Dict[str, str]:
        """
        Apply stored translations to target data.
        Returns dict of {key: translation} for keys found in memory.
        """
        if not target_data:
            return {}
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        results = {}
        keys = list(target_data.keys())
        
        # Batch query for performance
        batch_size = 500
        for i in range(0, len(keys), batch_size):
            batch_keys = keys[i:i + batch_size]
            placeholders = ','.join(['?' for _ in batch_keys])
            cursor.execute(f'''
                SELECT key, translation FROM translations 
                WHERE key IN ({placeholders})
            ''', batch_keys)
            
            for row in cursor.fetchall():
                results[row['key']] = row['translation']
        
        return results
    
    def mark_reviewed(self, keys: List[str], reviewed: bool = True):
        """Mark translations as reviewed."""
        if not keys:
            return
        
        conn = self._get_connection()
        cursor = conn.cursor()
        placeholders = ','.join(['?' for _ in keys])
        cursor.execute(f'''
            UPDATE translations SET reviewed = ? WHERE key IN ({placeholders})
        ''', [1 if reviewed else 0] + keys)
        conn.commit()
    
    def get_unreviewed_count(self) -> int:
        """Get count of unreviewed translations."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM translations WHERE reviewed = 0')
        return cursor.fetchone()[0]
    
    def find_changed_sources(self, current_data: Dict[str, str]) -> Dict[str, Tuple[str, str]]:
        """
        Find keys where source text has changed.
        Returns dict of {key: (old_source, new_source)}
        """
        if not current_data:
            return {}
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        changed = {}
        for key, new_source in current_data.items():
            cursor.execute('SELECT source FROM translations WHERE key = ?', (key,))
            row = cursor.fetchone()
            if row and row['source'] and row['source'] != new_source:
                changed[key] = (row['source'], new_source)
        
        return changed
    
    def get_stats(self) -> dict:
        """Get statistics about the translation memory."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM translations')
        total = cursor.fetchone()[0]
        
        cursor.execute('SELECT COUNT(*) FROM translations WHERE reviewed = 1')
        reviewed = cursor.fetchone()[0]
        
        cursor.execute('SELECT category, COUNT(*) as count FROM translations GROUP BY category')
        by_category = {row['category']: row['count'] for row in cursor.fetchall()}
        
        return {
            'total': total,
            'reviewed': reviewed,
            'unreviewed': total - reviewed,
            'by_category': by_category
        }
    
    def export_to_json(self, path: str = None) -> str:
        """Export all translations to JSON format (for backup/compatibility)."""
        if path is None:
            path = self.db_path.replace('.db', '_export.json')
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT key, translation FROM translations')
        
        data = {row['key']: row['translation'] for row in cursor.fetchall()}
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return path
    
    def save(self):
        """Commit any pending changes (for compatibility with v1 API)."""
        if self._conn:
            self._conn.commit()
    
    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None


# Compatibility wrapper for gradual migration
class TranslationMemoryCompat:
    """
    Compatibility layer that wraps TranslationMemoryV2 
    but maintains the v1 API for existing code.
    """
    
    def __init__(self, db_path: str = "translation_memory.json"):
        # Convert .json path to .db path
        if db_path.endswith('.json'):
            db_path = db_path.replace('.json', '.db')
        self.v2 = TranslationMemoryV2(db_path)
        self.memory = self  # For compatibility with code that accesses .memory
        self._current_mod_name = None
        self._current_model = None
        self._current_sources = {}
    
    def set_context(self, mod_name: str = None, model: str = None, sources: Dict[str, str] = None):
        """Set context for subsequent updates."""
        self._current_mod_name = mod_name
        self._current_model = model
        if sources:
            self._current_sources = sources
    
    def update(self, translations: Dict[str, str]):
        """Updates memory with new translations {key: text}."""
        self.v2.update(
            translations, 
            mod_name=self._current_mod_name,
            model=self._current_model,
            sources=self._current_sources
        )
    
    def get(self, key: str) -> Optional[str]:
        return self.v2.get(key)
    
    def apply_to(self, target_data: Dict[str, str]) -> Dict[str, str]:
        """Returns a dict of translations for the given target_data {key: original} found in memory."""
        return self.v2.apply_to(target_data)
    
    def save(self):
        self.v2.save()
    
    def close(self):
        self.v2.close()
