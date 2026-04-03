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
import re
import threading
from datetime import datetime
from typing import Dict, Optional, List, Tuple


class TranslationMemoryV2:
    """SQLite-based translation memory with context storage."""
    
    def __init__(self, db_path: str = "translation_memory.db"):
        self.db_path = db_path
        self.legacy_json_path = db_path.replace('.db', '.json')
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._init_db()
        self._migrate_from_json_if_needed()
    
    def _get_connection(self) -> sqlite3.Connection:
        """スレッドごとに独立した接続を返す。"""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=True)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA journal_mode=WAL')
            self._local.conn = conn
        return self._local.conn
    
    def _init_db(self):
        """Initialize database schema."""
        conn = self._get_connection()
        cursor = conn.cursor()

        self._migrate_to_composite_key(conn)

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS translations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT NOT NULL,
                source TEXT NOT NULL,
                translation TEXT NOT NULL,
                mod_name TEXT,
                category TEXT,
                model TEXT,
                translated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed INTEGER DEFAULT 0,
                source_hash TEXT,
                origin TEXT DEFAULT 'ai',
                UNIQUE(key, mod_name)
            )
        ''')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_key ON translations(key)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source ON translations(source)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_hash ON translations(source_hash)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_reviewed ON translations(reviewed)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mod_name ON translations(mod_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_origin ON translations(origin)')

        conn.commit()
        self._migrate_add_origin()

    def _migrate_to_composite_key(self, conn):
        """旧スキーマ(key単独UNIQUE)から(key, mod_name)複合UNIQUEへ移行。"""
        cursor = conn.cursor()
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='translations'")
        row = cursor.fetchone()
        if row is None:
            return
        schema_sql = row[0] if isinstance(row[0], str) else row['sql']
        if 'UNIQUE(key' in schema_sql and 'UNIQUE(key, mod_name)' not in schema_sql:
            print("TM: マイグレーション — (key, mod_name) 複合UNIQUEへ移行中...")
            cursor.execute('ALTER TABLE translations RENAME TO translations_old')
            cursor.execute('''
                CREATE TABLE translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    translation TEXT NOT NULL,
                    mod_name TEXT,
                    category TEXT,
                    model TEXT,
                    translated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed INTEGER DEFAULT 0,
                    source_hash TEXT,
                    origin TEXT DEFAULT 'ai',
                    UNIQUE(key, mod_name)
                )
            ''')
            cursor.execute('''
                INSERT OR IGNORE INTO translations
                    (id, key, source, translation, mod_name, category, model,
                     translated_at, reviewed, source_hash, origin)
                SELECT id, key, source, translation, mod_name, category, model,
                       translated_at, reviewed, source_hash, origin
                FROM translations_old
            ''')
            cursor.execute('DROP TABLE translations_old')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_key ON translations(key)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_source ON translations(source)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_source_hash ON translations(source_hash)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_reviewed ON translations(reviewed)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_mod_name ON translations(mod_name)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_origin ON translations(origin)')
            conn.commit()
            print("TM: マイグレーション完了")
    
    def _migrate_add_origin(self):
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT origin FROM translations LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE translations ADD COLUMN origin TEXT DEFAULT 'ai'")
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_origin ON translations(origin)')
            conn.commit()
            print("TM schema migrated: added 'origin' column")
    
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
               model: str = None, sources: Dict[str, str] = None, origin: str = 'ai'):
        """
        Update memory with new translations.
        
        Args:
            translations: Dict of {key: translated_text}
            mod_name: Name of the MOD being translated
            model: LLM model used for translation
            sources: Dict of {key: source_text} (original English text)
            origin: 'ai' | 'user' | 'ai_corrected'
        """
        if not translations:
            return
        
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            batch_rows = []
            for key, translation in translations.items():
                if not translation:
                    continue
                
                source = sources.get(key, '') if sources else ''
                category = self._detect_category(key)
                source_hash = self._hash_text(source)
                batch_rows.append((key, source, translation, mod_name, category, model, now, source_hash, origin))
            
            if batch_rows:
                cursor.executemany('''
                    INSERT INTO translations (key, source, translation, mod_name, category, model, translated_at, source_hash, origin)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key, mod_name) DO UPDATE SET
                        translation = excluded.translation,
                        category = excluded.category,
                        model = COALESCE(excluded.model, translations.model),
                        translated_at = excluded.translated_at,
                        source = CASE WHEN excluded.source != '' THEN excluded.source ELSE translations.source END,
                        source_hash = CASE WHEN excluded.source_hash != '' THEN excluded.source_hash ELSE translations.source_hash END,
                        origin = CASE
                            WHEN excluded.origin = 'user' THEN 'user'
                            WHEN excluded.origin = 'ai_corrected' THEN 'ai_corrected'
                            WHEN translations.origin IN ('user', 'ai_corrected') THEN translations.origin
                            ELSE COALESCE(excluded.origin, translations.origin)
                        END,
                        reviewed = CASE
                            WHEN excluded.origin = 'user' THEN 1
                            ELSE translations.reviewed
                        END
                ''', batch_rows)
            
            conn.commit()
    
    def get(self, key: str, mod_name: str = None) -> Optional[str]:
        """Get translation by key. mod_name があれば優先、なければ任意のmod_nameから取得。"""
        conn = self._get_connection()
        cursor = conn.cursor()
        if mod_name:
            cursor.execute(
                'SELECT translation FROM translations WHERE key = ? AND mod_name = ?',
                (key, mod_name)
            )
            row = cursor.fetchone()
            if row:
                return row['translation']
        cursor.execute('SELECT translation FROM translations WHERE key = ? LIMIT 1', (key,))
        row = cursor.fetchone()
        return row['translation'] if row else None
    
    def get_with_context(self, key: str, mod_name: str = None) -> Optional[dict]:
        """Get translation with full context. mod_name があれば優先。"""
        conn = self._get_connection()
        cursor = conn.cursor()
        if mod_name:
            cursor.execute('''
                SELECT key, source, translation, mod_name, category, model, translated_at, reviewed, origin
                FROM translations WHERE key = ? AND mod_name = ?
            ''', (key, mod_name))
            row = cursor.fetchone()
            if row:
                return dict(row)
        cursor.execute('''
            SELECT key, source, translation, mod_name, category, model, translated_at, reviewed, origin
            FROM translations WHERE key = ? LIMIT 1
        ''', (key,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
    
    @staticmethod
    def _build_in_clause(count):
        return ','.join(['?' for _ in range(count)])

    def apply_to(self, target_data: Dict[str, str], mod_name: str = None) -> Dict[str, str]:
        """
        Apply stored translations to target data.
        mod_name が指定された場合、そのMODのエントリを優先し、
        見つからないキーはmod_name=Noneのエントリからフォールバック検索する。
        Returns dict of {key: translation} for keys found in memory.
        """
        if not target_data:
            return {}

        conn = self._get_connection()
        cursor = conn.cursor()

        results = {}
        keys = list(target_data.keys())

        batch_size = 500

        if mod_name:
            for i in range(0, len(keys), batch_size):
                batch_keys = keys[i:i + batch_size]
                placeholders = self._build_in_clause(len(batch_keys))
                cursor.execute(
                    f'SELECT key, translation FROM translations '
                    f'WHERE key IN ({placeholders}) AND mod_name = ?',
                    batch_keys + [mod_name]
                )
                for row in cursor.fetchall():
                    results[row['key']] = row['translation']

            remaining_keys = [k for k in keys if k not in results]
            if remaining_keys:
                for i in range(0, len(remaining_keys), batch_size):
                    batch_keys = remaining_keys[i:i + batch_size]
                    placeholders = self._build_in_clause(len(batch_keys))
                    cursor.execute(
                        f'SELECT key, translation FROM translations '
                        f'WHERE key IN ({placeholders}) AND mod_name IS NULL',
                        batch_keys
                    )
                    for row in cursor.fetchall():
                        if row['key'] not in results:
                            results[row['key']] = row['translation']

                still_remaining = [k for k in remaining_keys if k not in results]
                if still_remaining:
                    for i in range(0, len(still_remaining), batch_size):
                        batch_keys = still_remaining[i:i + batch_size]
                        placeholders = self._build_in_clause(len(batch_keys))
                        cursor.execute(
                            f'SELECT key, translation, mod_name FROM translations '
                            f'WHERE key IN ({placeholders}) AND mod_name IS NOT NULL AND mod_name != ? '
                            f'ORDER BY CASE origin WHEN \'user\' THEN 0 WHEN \'ai_corrected\' THEN 1 ELSE 2 END, '
                            f'reviewed DESC, translated_at DESC',
                            batch_keys + [mod_name]
                        )
                        for row in cursor.fetchall():
                            if row['key'] not in results:
                                results[row['key']] = row['translation']
        else:
            for i in range(0, len(keys), batch_size):
                batch_keys = keys[i:i + batch_size]
                placeholders = self._build_in_clause(len(batch_keys))
                cursor.execute(
                    f'SELECT key, translation FROM translations WHERE key IN ({placeholders})',
                    batch_keys
                )
                for row in cursor.fetchall():
                    if row['key'] not in results:
                        results[row['key']] = row['translation']

        return results
    
    def batch_get_review_status(self, keys, mod_name: str = None) -> Dict[str, dict]:
        """Get reviewed/origin status for multiple keys in one query."""
        if not keys:
            return {}

        conn = self._get_connection()
        cursor = conn.cursor()
        keys_list = list(keys)

        results = {}
        batch_size = 500

        if mod_name:
            for i in range(0, len(keys_list), batch_size):
                batch = keys_list[i:i + batch_size]
                placeholders = self._build_in_clause(len(batch))
                cursor.execute(
                    f'SELECT key, reviewed, origin FROM translations '
                    f'WHERE key IN ({placeholders}) AND mod_name = ?',
                    batch + [mod_name]
                )
                for row in cursor.fetchall():
                    results[row['key']] = {
                        "reviewed": bool(row['reviewed']),
                        "origin": row['origin']
                    }

            remaining = [k for k in keys_list if k not in results]
            if remaining:
                for i in range(0, len(remaining), batch_size):
                    batch = remaining[i:i + batch_size]
                    placeholders = self._build_in_clause(len(batch))
                    cursor.execute(
                        f'SELECT key, reviewed, origin FROM translations '
                        f'WHERE key IN ({placeholders}) AND mod_name IS NULL',
                        batch
                    )
                    for row in cursor.fetchall():
                        if row['key'] not in results:
                            results[row['key']] = {
                                "reviewed": bool(row['reviewed']),
                                "origin": row['origin']
                            }

            still_remaining = [k for k in keys_list if k not in results]
            if still_remaining:
                for i in range(0, len(still_remaining), batch_size):
                    batch = still_remaining[i:i + batch_size]
                    placeholders = self._build_in_clause(len(batch))
                    cursor.execute(
                        f'SELECT key, reviewed, origin FROM translations '
                        f'WHERE key IN ({placeholders}) AND mod_name IS NOT NULL AND mod_name != ? '
                        f'ORDER BY CASE origin WHEN \'user\' THEN 0 WHEN \'ai_corrected\' THEN 1 ELSE 2 END, '
                        f'reviewed DESC, translated_at DESC',
                        batch + [mod_name]
                    )
                    for row in cursor.fetchall():
                        if row['key'] not in results:
                            results[row['key']] = {
                                "reviewed": bool(row['reviewed']),
                                "origin": row['origin']
                            }
        else:
            for i in range(0, len(keys_list), batch_size):
                batch = keys_list[i:i + batch_size]
                placeholders = self._build_in_clause(len(batch))
                cursor.execute(
                    f'SELECT key, reviewed, origin FROM translations WHERE key IN ({placeholders})',
                    batch
                )
                for row in cursor.fetchall():
                    if row['key'] not in results:
                        results[row['key']] = {
                            "reviewed": bool(row['reviewed']),
                            "origin": row['origin']
                        }

        return results
    
    def mark_reviewed(self, keys: List[str], reviewed: bool = True, mod_name: str = None):
        if not keys:
            return

        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if mod_name:
                placeholders = self._build_in_clause(len(keys))
                cursor.execute(
                    f'UPDATE translations SET reviewed = ? '
                    f'WHERE key IN ({placeholders}) AND mod_name = ?',
                    [1 if reviewed else 0] + keys + [mod_name]
                )
            else:
                placeholders = self._build_in_clause(len(keys))
                cursor.execute(
                    f'UPDATE translations SET reviewed = ? WHERE key IN ({placeholders})',
                    [1 if reviewed else 0] + keys
                )
            conn.commit()
    
    def get_unreviewed_count(self) -> int:
        """Get count of unreviewed translations."""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM translations WHERE reviewed = 0')
        return cursor.fetchone()[0]
    
    def find_changed_sources(self, current_data: Dict[str, str], mod_name: str = None) -> Dict[str, Tuple[str, str]]:
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
            if mod_name:
                cursor.execute(
                    'SELECT source FROM translations WHERE key = ? AND mod_name = ?',
                    (key, mod_name)
                )
                row = cursor.fetchone()
                if row:
                    if row['source'] and row['source'] != new_source:
                        changed[key] = (row['source'], new_source)
                    continue
            cursor.execute('SELECT source FROM translations WHERE key = ? LIMIT 1', (key,))
            row = cursor.fetchone()
            if row and row['source'] and row['source'] != new_source:
                changed[key] = (row['source'], new_source)

        return changed
    
    _STOP_WORDS = frozenset({
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'was',
        'one', 'our', 'had', 'his', 'how', 'its', 'let', 'may', 'new', 'now',
        'old', 'see', 'way', 'who', 'did', 'get', 'him', 'she', 'use', 'out',
        'any', 'from', 'them', 'some', 'than', 'been', 'have', 'will', 'into',
        'this', 'that', 'with', 'they', 'what', 'about', 'which', 'when', 'make',
        'like', 'just', 'over', 'such', 'take', 'each', 'very', 'your', 'also',
    })

    _SUFFIXES_SORTED = (
        'ication', 'ization', 'ational', 'iness',
        'ation', 'ition', 'ment', 'ness', 'ence', 'ance',
        'able', 'ible', 'ings',
        'ing', 'ful', 'ous', 'ive',
        'ion', 'ity', 'ism', 'ist',
        'ed', 'er', 'ly',
    )

    @staticmethod
    def _stem(word: str) -> str:
        w = word
        if w.endswith('ies') and len(w) > 4:
            w = w[:-3] + 'y'
        elif w.endswith(('sses', 'shes', 'ches', 'xes', 'zes')) and len(w) > 4:
            w = w[:-2]
        elif w.endswith('s') and not w.endswith('ss') and len(w) > 3:
            w = w[:-1]
        for suffix in TranslationMemoryV2._SUFFIXES_SORTED:
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[:-len(suffix)]
        return w

    _SIMILARITY_THRESHOLD = 0.3
    _MAX_EXAMPLE_LENGTH = 120
    _ORIGIN_WEIGHT = {'user': 3.0, 'ai_corrected': 2.0, 'ai': 1.0}

    def _compute_jaccard(self, stems_a: set, stems_b: set) -> float:
        if not stems_a or not stems_b:
            return 0.0
        intersection = len(stems_a & stems_b)
        union = len(stems_a | stems_b)
        return intersection / union if union > 0 else 0.0

    def find_similar(self, batch_texts: List[str], mod_name: str = None,
                     limit: int = 5) -> List[Tuple[str, str]]:
        """
        バッチ内テキストとJaccard類似度が高いTM訳例を検索する。

        Args:
            batch_texts: 現在のバッチの原文テキスト群
            mod_name: 同じMOD名でフィルタ（None=全MOD対象）
            limit: 返す訳例の最大数
        Returns:
            [(source, translation), ...] 類似度順
        """
        if not batch_texts or limit <= 0:
            return []

        batch_words = set()
        for text in batch_texts:
            if not text or not isinstance(text, str):
                continue
            batch_words.update(w for w in re.findall(r'[a-zA-Z]{3,}', text.lower()))
        batch_words -= self._STOP_WORDS

        if not batch_words:
            return []

        batch_stems = {self._stem(w) for w in batch_words}

        conn = self._get_connection()
        cursor = conn.cursor()

        if len(batch_stems) <= 3:
            min_stem_count = 1
        elif len(batch_stems) <= 8:
            min_stem_count = 2
        else:
            min_stem_count = max(2, len(batch_stems) // 4)

        search_words = list(batch_words | batch_stems)
        top_words = sorted(search_words, key=len, reverse=True)[:20]
        like_clauses = ' OR '.join(['source LIKE ?' for _ in top_words])
        like_params = [f'%{w}%' for w in top_words]

        if mod_name:
            query = (
                f'SELECT source, translation, origin FROM translations '
                f'WHERE mod_name = ? AND source != "" '
                f'AND (origin IN ("user", "ai_corrected") OR reviewed = 1) '
                f'AND ({like_clauses}) '
                f'LIMIT 500'
            )
            params = [mod_name] + like_params
        else:
            query = (
                f'SELECT source, translation, origin FROM translations '
                f'WHERE source != "" '
                f'AND (origin IN ("user", "ai_corrected") OR reviewed = 1) '
                f'AND ({like_clauses}) '
                f'LIMIT 500'
            )
            params = like_params

        cursor.execute(query, params)
        candidates = cursor.fetchall()

        if not candidates:
            return []

        scored = []
        for row in candidates:
            source = row['source'] or ''
            origin = row['origin'] or 'ai'
            translation = row['translation'] or ''

            if not translation or len(source) > self._MAX_EXAMPLE_LENGTH or len(translation) > self._MAX_EXAMPLE_LENGTH:
                continue

            source_words = set(re.findall(r'[a-zA-Z]{3,}', source.lower())) - self._STOP_WORDS
            source_stems = {self._stem(w) for w in source_words}
            common_count = len(batch_stems & source_stems)

            if common_count < min_stem_count:
                continue

            jaccard = self._compute_jaccard(batch_stems, source_stems)
            if jaccard < self._SIMILARITY_THRESHOLD:
                continue

            origin_w = self._ORIGIN_WEIGHT.get(origin, 1.0)
            score = jaccard * origin_w
            scored.append((score, jaccard, source, translation))

        if not scored:
            return []

        scored.sort(key=lambda x: -x[0])

        seen = set()
        results = []
        for _, _, source, translation in scored:
            if source not in seen:
                seen.add(source)
                results.append((source, translation))
                if len(results) >= limit:
                    break

        return results

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
        conn = getattr(self._local, 'conn', None)
        if conn:
            conn.commit()
    
    def close(self):
        """Close database connection."""
        conn = getattr(self._local, 'conn', None)
        if conn:
            conn.close()
            self._local.conn = None


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
        return self.v2.get(key, mod_name=self._current_mod_name)
    
    def apply_to(self, target_data: Dict[str, str]) -> Dict[str, str]:
        """Returns a dict of translations for the given target_data {key: original} found in memory."""
        return self.v2.apply_to(target_data, mod_name=self._current_mod_name)
    
    def find_similar(self, batch_texts: List[str], mod_name: str = None,
                     limit: int = 5) -> List[Tuple[str, str]]:
        return self.v2.find_similar(batch_texts, mod_name=mod_name, limit=limit)
    
    def save(self):
        self.v2.save()
    
    def close(self):
        self.v2.close()
