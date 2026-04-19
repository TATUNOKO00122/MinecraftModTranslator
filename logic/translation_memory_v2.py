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
import time
from difflib import SequenceMatcher
from datetime import datetime
from typing import Dict, Optional, List, Tuple


class TranslationMemoryV2:
    """SQLite-based translation memory with context storage."""
    
    def __init__(self, db_path: str = "translation_memory.db"):
        self.db_path = db_path
        self.legacy_json_path = db_path.replace('.db', '.json')
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._fts5_available = False
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
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_reviewed ON translations(reviewed)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mod_name ON translations(mod_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_origin ON translations(origin)')

        cursor.execute('CREATE INDEX IF NOT EXISTS idx_key_mod ON translations(key, mod_name)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_key_mod_origin ON translations(key, mod_name, origin)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_origin_reviewed ON translations(origin, reviewed)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_mod_source ON translations(mod_name, source)')

        cursor.execute('''CREATE TABLE IF NOT EXISTS translation_stems (
            translation_id INTEGER PRIMARY KEY REFERENCES translations(id),
            source_stems TEXT NOT NULL,
            source_words TEXT NOT NULL,
            word_count INTEGER DEFAULT 0
        )''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_stems_words ON translation_stems(word_count)')

        conn.commit()
        self._migrate_add_origin()
        self._migrate_null_mod_name()
        self._ensure_fts5()
        self._ensure_fts5_index()

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

    def _migrate_null_mod_name(self):
        """Convert NULL mod_name to empty string and remove duplicates caused by NULL UNIQUE issue."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM translations WHERE mod_name IS NULL")
            null_count = cursor.fetchone()[0]
            if null_count == 0:
                return

            print(f"TM: Migration - NULL mod_name -> empty string ({null_count} rows), removing duplicates...")

            # Keep only the latest row per (key, '') by deleting older duplicates
            cursor.execute('''
                DELETE FROM translations WHERE mod_name IS NULL AND id NOT IN (
                    SELECT MAX(id) FROM translations WHERE mod_name IS NULL GROUP BY key
                )
            ''')

            cursor.execute('''
                UPDATE translations SET mod_name = '' WHERE mod_name IS NULL
            ''')
            conn.commit()
            print(f"TM: Migration complete (NULL mod_name converted to empty string)")
        except Exception as e:
            print(f"TM: NULL mod_name migration failed: {e}")
            conn.rollback()
    
    def _migrate_from_json_if_needed(self):
        """Migrate from legacy JSON format if exists and DB is empty."""
        if not os.path.exists(self.legacy_json_path):
            return
        
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM translations')
        count = cursor.fetchone()[0]
        
        if count > 0:
            return
        
        print(f"Migrating from {self.legacy_json_path} to SQLite...")
        try:
            with open(self.legacy_json_path, 'r', encoding='utf-8') as f:
                legacy_data = json.load(f)
            
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
            
            backup_path = self.legacy_json_path + '.migrated'
            if not os.path.exists(backup_path):
                os.rename(self.legacy_json_path, backup_path)
                print(f"Legacy JSON renamed to {backup_path}")
                
        except Exception as e:
            print(f"Migration failed: {e}")
    
    def _hash_text(self, text: str) -> str:
        return ""

    def _ensure_fts5(self):
        try:
            conn = self._get_connection()
            conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_check USING fts5(x)")
            conn.execute("DROP TABLE IF EXISTS _fts5_check")
            self._fts5_available = True
            print("[TM] FTS5 is available")
        except sqlite3.OperationalError:
            self._fts5_available = False
            print("[TM] FTS5 unavailable, falling back to LIKE queries")

    def _ensure_fts5_index(self):
        if not self._fts5_available:
            return

        conn = self._get_connection()
        cursor = conn.cursor()

        exists = cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='translations_fts'"
        ).fetchone()
        if exists:
            return

        count = cursor.execute("SELECT COUNT(*) FROM translations").fetchone()[0]

        cursor.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS translations_fts USING fts5(
                source,
                content='translations',
                content_rowid='id'
            )
        ''')

        if count > 0:
            t0 = time.time() if hasattr(time, 'time') else 0
            cursor.execute('''
                INSERT INTO translations_fts(rowid, source)
                SELECT id, source FROM translations WHERE source != ''
            ''')
            conn.commit()
            elapsed = (time.time() - t0) if t0 else 0
            print(f"[TM] FTS5 index built ({count} rows, {elapsed:.1f}s)")

        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS translations_fts_ai AFTER INSERT ON translations BEGIN
                INSERT INTO translations_fts(rowid, source)
                VALUES (new.id, new.source);
            END
        ''')
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS translations_fts_ad AFTER DELETE ON translations BEGIN
                INSERT INTO translations_fts(translations_fts, rowid, source)
                VALUES ('delete', old.id, old.source);
            END
        ''')
        cursor.execute('''
            CREATE TRIGGER IF NOT EXISTS translations_fts_au AFTER UPDATE ON translations BEGIN
                INSERT INTO translations_fts(translations_fts, rowid, source)
                VALUES ('delete', old.id, old.source);
                INSERT INTO translations_fts(rowid, source)
                VALUES (new.id, new.source);
            END
        ''')
        conn.commit()
        print("[TM] FTS5 triggers created")

        self._ensure_stems_cache()

    def _ensure_stems_cache(self):
        conn = self._get_connection()
        cursor = conn.cursor()

        missing = cursor.execute('''
            SELECT t.id, t.source FROM translations t
            LEFT JOIN translation_stems ts ON t.id = ts.translation_id
            WHERE ts.translation_id IS NULL AND t.source != ''
        ''').fetchall()

        if not missing:
            return

        batch_rows = []
        for row in missing:
            tid = row['id']
            source = row['source']
            words_list = re.findall(r'[a-zA-Z]{3,}', source.lower())
            if not words_list:
                continue
            words_set = set(words_list) - self._STOP_WORDS
            stems_list = [self._stem(w) for w in words_set]
            batch_rows.append((tid, ' '.join(stems_list), ' '.join(words_set), len(words_set)))

        if batch_rows:
            cursor.executemany(
                'INSERT OR IGNORE INTO translation_stems (translation_id, source_stems, source_words, word_count) '
                'VALUES (?, ?, ?, ?)',
                batch_rows
            )
            conn.commit()
            print(f"[TM] Stems cache built: {len(batch_rows)} entries")

    def _fts_match(self, match_expr, mod_name=None, limit=200):
        """FTS5 MATCH式で検索。失敗時は空リストを返す。"""
        if not self._fts5_available:
            return []
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            if mod_name:
                cursor.execute(
                    'SELECT t.source, t.translation, t.origin '
                    'FROM translations_fts f '
                    'JOIN translations t ON t.id = f.rowid '
                    'WHERE f.source MATCH ? AND t.source != "" AND t.mod_name = ? '
                    'LIMIT ?',
                    (match_expr, mod_name, limit)
                )
            else:
                cursor.execute(
                    'SELECT t.source, t.translation, t.origin '
                    'FROM translations_fts f '
                    'JOIN translations t ON t.id = f.rowid '
                    'WHERE f.source MATCH ? AND t.source != "" '
                    'LIMIT ?',
                    (match_expr, limit)
                )
            return cursor.fetchall()
        except sqlite3.OperationalError:
            return []

    def _fts_search(self, words, mod_name=None, limit=500):
        conn = self._get_connection()
        cursor = conn.cursor()

        if self._fts5_available:
            safe_words = [w for w in words if w and not w.upper() in ('AND', 'OR', 'NOT', 'NEAR')]
            if not safe_words:
                return []
            match_expr = ' OR '.join(f'"{w}"' for w in safe_words)
            try:
                if mod_name:
                    cursor.execute(
                        'SELECT t.source, t.translation, t.origin '
                        'FROM translations_fts f '
                        'JOIN translations t ON t.id = f.rowid '
                        'WHERE f.source MATCH ? AND t.source != "" AND t.mod_name = ? '
                        'LIMIT ?',
                        (match_expr, mod_name, limit)
                    )
                else:
                    cursor.execute(
                        'SELECT t.source, t.translation, t.origin '
                        'FROM translations_fts f '
                        'JOIN translations t ON t.id = f.rowid '
                        'WHERE f.source MATCH ? AND t.source != "" '
                        'LIMIT ?',
                        (match_expr, limit)
                    )
                return cursor.fetchall()
            except sqlite3.OperationalError:
                print("[TM] FTS5 query failed, falling back to LIKE")

        like_clauses = ' OR '.join(['source LIKE ?' for _ in words])
        like_params = [f'%{w}%' for w in words]
        if mod_name:
            cursor.execute(
                f'SELECT source, translation, origin FROM translations '
                f'WHERE mod_name = ? AND source != "" '
                f'AND ({like_clauses}) LIMIT ?',
                [mod_name] + like_params + [limit]
            )
        else:
            cursor.execute(
                f'SELECT source, translation, origin FROM translations '
                f'WHERE source != "" '
                f'AND ({like_clauses}) LIMIT ?',
                like_params + [limit]
            )
        return cursor.fetchall()
    
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
                effective_mod = mod_name if mod_name else ''
                batch_rows.append((key, source, translation, effective_mod, category, model, now, source_hash, origin))
            
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
    
    def delete(self, keys: List[str], mod_name: str = None):
        """Delete translations by keys. mod_name があればそのMODのみ、なければ全mod_nameから削除。"""
        if not keys:
            return
        
        with self._write_lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            batch_size = 500
            for i in range(0, len(keys), batch_size):
                batch = keys[i:i + batch_size]
                placeholders = ','.join(['?'] * len(batch))
                if mod_name:
                    cursor.execute(
                        f'DELETE FROM translations WHERE key IN ({placeholders}) AND mod_name = ?',
                        batch + [mod_name]
                    )
                else:
                    cursor.execute(
                        f'DELETE FROM translations WHERE key IN ({placeholders})',
                        batch
                    )
            
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
                        f'WHERE key IN ({placeholders}) AND mod_name = ? ',
                        batch_keys + ['']
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
                            f'WHERE key IN ({placeholders}) AND mod_name IS NOT NULL AND mod_name != ? AND mod_name != ? '
                            f'ORDER BY CASE origin WHEN \'user\' THEN 0 WHEN \'ai_corrected\' THEN 1 ELSE 2 END, '
                            f'reviewed DESC, translated_at DESC',
                            batch_keys + [mod_name, '']
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
                        f'WHERE key IN ({placeholders}) AND mod_name = ?',
                        batch + ['']
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
                        f'WHERE key IN ({placeholders}) AND mod_name IS NOT NULL AND mod_name != ? AND mod_name != ? '
                        f'ORDER BY CASE origin WHEN \'user\' THEN 0 WHEN \'ai_corrected\' THEN 1 ELSE 2 END, '
                        f'reviewed DESC, translated_at DESC',
                        batch + [mod_name, '']
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
        'ion', 'ity', 'ism', 'ist', 'ite', 'ium',
        'ed', 'er', 'ly', 'en',
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
        if not batch_texts or limit <= 0:
            return []

        import time as _time
        t0 = _time.perf_counter()

        batch_words = set()
        cleaned_batch_map = {}
        for text in batch_texts:
            if not text or not isinstance(text, str):
                continue
            cleaned = re.sub(r'&[0-9a-fk-or]', '', text)
            batch_words.update(w for w in re.findall(r'[a-zA-Z]{3,}', cleaned.lower()))
            cleaned_batch_map[text] = cleaned.strip()
        batch_words -= self._STOP_WORDS

        if not batch_words:
            return []

        batch_stems = {self._stem(w) for w in batch_words}

        if len(batch_stems) <= 3:
            min_stem_count = 1
        elif len(batch_stems) <= 8:
            min_stem_count = 2
        else:
            min_stem_count = max(2, len(batch_stems) // 4)

        candidates = []
        seen_sources = set()

        if self._fts5_available:
            phrases = set()
            for text in batch_texts:
                if not text or not isinstance(text, str):
                    continue
                words = [w for w in re.findall(r'[a-zA-Z]{3,}', text.lower()) if w not in self._STOP_WORDS]
                for i in range(len(words) - 1):
                    phrases.add((words[i], words[i + 1]))

            unique_phrases = list(phrases)[:30]
            for pw in unique_phrases:
                match_expr = ' AND '.join(f'"{w}"' for w in pw)
                phrase_rows = self._fts_match(match_expr, mod_name=mod_name, limit=10)
                for row in phrase_rows:
                    s = row['source'] or ''
                    if s and s not in seen_sources:
                        seen_sources.add(s)
                        candidates.append(row)

            if len(candidates) < 30:
                top_words = sorted(batch_words, key=len, reverse=True)[:8]
                broad_expr = ' OR '.join(f'"{w}"' for w in top_words)
                broad_rows = self._fts_match(broad_expr, mod_name=mod_name, limit=50)
                for row in broad_rows:
                    s = row['source'] or ''
                    if s and s not in seen_sources:
                        seen_sources.add(s)
                        candidates.append(row)

        if not candidates:
            search_words = list(batch_words | batch_stems)
            top_words = sorted(search_words, key=len, reverse=True)[:20]
            candidates = self._fts_search(top_words, mod_name=mod_name, limit=200)

        print(f"[TM-SIMILAR] stems={len(batch_stems)}, min_stem={min_stem_count}, candidates={len(candidates)}, fts={_time.perf_counter()-t0:.3f}s")

        if not candidates:
            return []

        batch_texts_set = set(t for t in batch_texts if isinstance(t, str))

        batch_text_stems = []
        for text, cleaned in cleaned_batch_map.items():
            if not cleaned:
                continue
            words = set(re.findall(r'[a-zA-Z]{3,}', cleaned.lower())) - self._STOP_WORDS
            stems = {self._stem(w) for w in words}
            batch_text_stems.append((text, cleaned, stems))

        scored = []
        long_matched = []
        for row in candidates:
            source = row['source'] or ''
            origin = row['origin'] or 'ai'
            translation = row['translation'] or ''

            if not translation:
                continue

            if source in batch_texts_set:
                continue

            source_clean = re.sub(r'&[0-9a-fk-or]', '', source)
            source_words = set(re.findall(r'[a-zA-Z]{3,}', source_clean.lower())) - self._STOP_WORDS
            source_stems = {self._stem(w) for w in source_words}
            origin_w = self._ORIGIN_WEIGHT.get(origin, 1.0)

            if len(source_words) <= 5 and source_stems and source_stems.issubset(batch_stems):
                if len(source) <= self._MAX_EXAMPLE_LENGTH and len(translation) <= self._MAX_EXAMPLE_LENGTH:
                    scored.append((1.5 * origin_w, 1.0, source, translation))
                else:
                    long_matched.append((origin_w, source, translation))
                continue

            common_count = len(batch_stems & source_stems)

            if common_count < min_stem_count:
                continue

            jaccard = self._compute_jaccard(batch_stems, source_stems)

            if jaccard >= 0.4:
                source_clean_text = source_clean.strip()
                source_len = len(source_clean_text)

                top_bt = []
                for bt, bt_clean, bt_stems in batch_text_stems:
                    bt_len = len(bt_clean)
                    if bt_len == 0:
                        continue
                    len_ratio = source_len / bt_len if bt_len > source_len else bt_len / source_len
                    if len_ratio < 0.4:
                        continue
                    overlap = len(bt_stems & source_stems)
                    if overlap > 0:
                        top_bt.append((bt_clean, bt_len, overlap))

                top_bt.sort(key=lambda x: -x[2])
                top_bt = top_bt[:5]

                best_str_sim = 0.0
                for bt_clean, bt_len, _ in top_bt:
                    sim = SequenceMatcher(None, bt_clean, source_clean_text, autojunk=False).ratio()
                    if sim > best_str_sim:
                        best_str_sim = sim

                hybrid = 0.4 * jaccard + 0.6 * best_str_sim
                score = hybrid * origin_w

                if len(source) <= self._MAX_EXAMPLE_LENGTH and len(translation) <= self._MAX_EXAMPLE_LENGTH:
                    scored.append((score, hybrid, source, translation))
                else:
                    long_matched.append((origin_w, source, translation))
            else:
                pairs = self._extract_proper_noun_pairs(source, translation)
                for en_phrase, ja_phrase in pairs:
                    scored.append((0.5 * origin_w, 0.5, en_phrase, ja_phrase))

        for origin_w, source, translation in long_matched:
            pairs = self._extract_proper_noun_pairs(source, translation)
            for en_phrase, ja_phrase in pairs:
                scored.append((1.5 * origin_w, 1.0, en_phrase, ja_phrase))

        empty_source_results = self._search_empty_source_pairs(self._get_connection().cursor(), batch_texts)
        scored.extend(empty_source_results)

        print(f"[TM-SIMILAR] scored={len(scored)}, long_matched={len(long_matched)}, empty_source={len(empty_source_results)}, total={_time.perf_counter()-t0:.3f}s")

        if not scored:
            return []

        scored.sort(key=lambda x: -x[0])

        seen_phrases = set()
        results = []
        for _, _, source, translation in scored:
            if source not in seen_phrases:
                seen_phrases.add(source)
                results.append((source, translation))
                if len(results) >= limit:
                    break

        print(f"[TM-SIMILAR] returning {len(results)} examples")
        return results

    _TM_EMPTY_BLACKLIST = frozenset(['未使用', '使用不可', '未実装', '削除済み', 'deprecated', 'unused', 'n/a'])

    def _search_empty_source_pairs(self, cursor, batch_texts: List[str]) -> List[Tuple[float, float, str, str]]:
        batch_joined = ' '.join(t for t in batch_texts if isinstance(t, str))
        cleaned_batch = re.sub(r'&[0-9a-fk-or]', '', batch_joined)
        en_phrases = re.findall(r'[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*', cleaned_batch)
        en_phrases = [p for p in en_phrases if len(p.split()) >= 2]
        en_phrases = [p for p in en_phrases if not any(w.lower() in self._TERM_STOP_WORDS for w in p.split())]
        if not en_phrases:
            return []

        all_stems = set()
        phrase_stems_map = {}
        for phrase in en_phrases:
            words = phrase.lower().split()
            p_stems = set()
            for w in words:
                stems = {w}
                s = w
                for _ in range(3):
                    ns = self._stem(s)
                    stems.add(ns)
                    if ns == s:
                        break
                    s = ns
                if any(s.endswith(x) for x in ('or', 'er', 'ion')) and len(s) > 4:
                    stems.add(s[:-2])
                p_stems.update(stems)
                all_stems.update(stems)
            phrase_stems_map[phrase] = p_stems

        like_clauses = ' OR '.join([f'key LIKE ?' for _ in all_stems])
        like_params = [f'%{st}%' for st in all_stems]

        cursor.execute(
            f"SELECT key, translation, origin FROM translations "
            f"WHERE (source = '' OR source IS NULL) AND ({like_clauses}) AND translation != '' "
            f"AND LENGTH(translation) <= 30 "
            f"LIMIT 200",
            like_params
        )
        all_rows = cursor.fetchall()

        scored = []
        seen = set()
        for phrase in en_phrases:
            p_stems = phrase_stems_map[phrase]
            phrase_words = set(w.lower() for w in phrase.split())
            best_trans = None
            best_len = 999
            for row in all_rows:
                key_text = (row['key'] or '').replace('_', ' ').replace('.', ' ').lower()
                if not p_stems.intersection(key_text.split()):
                    continue

                key_words = set(key_text.split())
                if not phrase_words & key_words:
                    continue

                trans = row['translation'] or ''
                if not trans:
                    continue
                trans_stripped = trans.strip().lower()
                if trans_stripped in self._TM_EMPTY_BLACKLIST:
                    continue
                if not re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', trans):
                    continue

                if len(trans) < best_len:
                    best_len = len(trans)
                    best_trans = (row, trans)

            if best_trans:
                row, trans = best_trans
                pair_key = (phrase, trans)
                if pair_key not in seen:
                    seen.add(pair_key)
                    origin = row['origin'] or 'ai'
                    origin_w = self._ORIGIN_WEIGHT.get(origin, 1.0)
                    scored.append((1.0 * origin_w, 1.0, phrase, trans))
                    print(f"[TM-EMPTY] MATCH: '{phrase}' → '{trans}' (key={row['key']})")
        return scored

    def _extract_proper_noun_pairs(self, source: str, translation: str) -> List[Tuple[str, str]]:
        pairs = []

        src_segments = re.findall(r'&[0-9a-fk-or]([^&]+?)(?:&r|$)', source)
        trans_segments = re.findall(r'&[0-9a-fk-or]([^&]+?)(?:&r|$)', translation)

        if src_segments and trans_segments:
            pair_count = min(len(src_segments), len(trans_segments))
            for i in range(pair_count):
                s = src_segments[i].strip()
                t = trans_segments[i].strip()
                s = re.sub(r'^[\d\s]+', '', s).strip()
                s = re.sub(r'[\d\s]+$', '', s).strip()
                t = re.sub(r'^[\d\s]+', '', t).strip()
                t = re.sub(r'[\d\s]+$', '', t).strip()
                if not s or not t:
                    continue
                has_proper = bool(re.search(r'[A-Z][a-zA-Z]+', s))
                has_cjk = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', t))
                if has_proper and has_cjk:
                    pairs.append((s, t))
            if pairs:
                return pairs

        cleaned_src = re.sub(r'&[0-9a-fk-or]', '', source)
        cleaned_trans = re.sub(r'&[0-9a-fk-or]', '', translation)

        bracket_src = re.findall(r'[(\uff08]([^)(\uff08\uff09]+)[)\uff09]', cleaned_src)
        bracket_trans = re.findall(r'[(\uff08]([^)(\uff08\uff09]+)[)\uff09]', cleaned_trans)
        if bracket_src and bracket_trans:
            bc = min(len(bracket_src), len(bracket_trans))
            for i in range(bc):
                s = bracket_src[i].strip()
                t = bracket_trans[i].strip()
                has_proper = bool(re.search(r'[A-Z][a-zA-Z]+', s))
                has_cjk = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', t))
                if has_proper and has_cjk:
                    pairs.append((s, t))

        cap_phrases = re.findall(r'[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+', cleaned_src)
        cjk_blocks = re.findall(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u30FC]{2,}', cleaned_trans)
        if cap_phrases and cjk_blocks:
            cjk_used = set()
            for phrase in cap_phrases[:5]:
                if not any(phrase == p[0] for p in pairs):
                    for idx, block in enumerate(cjk_blocks[:10]):
                        if idx not in cjk_used and 2 <= len(block) <= 20:
                            pairs.append((phrase, block))
                            cjk_used.add(idx)
                            break

        if not pairs:
            src_words = re.findall(r'[A-Z][a-zA-Z]+', cleaned_src)
            if src_words:
                has_cjk = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', cleaned_trans))
                if has_cjk:
                    pairs.append((cleaned_src.strip(), cleaned_trans.strip()))

        return list(set(pairs))

    def build_cross_mod_index(self, cross_mod_data):
        if not cross_mod_data:
            return {}
        index = {}
        for key, entry in cross_mod_data.items():
            if isinstance(entry, str):
                source = ""
            elif isinstance(entry, dict):
                source = entry.get("original", "")
            else:
                continue
            if not source or not isinstance(source, str):
                continue
            words = set(re.findall(r'[a-zA-Z]{3,}', source.lower()))
            for word in words:
                stem = self._stem(word)
                for term in (word, stem):
                    if term not in index:
                        index[term] = []
                    index[term].append((key, entry))
        return index

    def _extract_noun_pair_from_text(self, source: str, translation: str,
                                      term: str, stems: set) -> Optional[str]:
        """
        色コードがない長文から、termに対応する訳語を推定して抽出する。
        _extract_proper_noun_pairsで構造的にマッチする対のみ返す。
        CJKチャンクの位置推定は精度が低すぎるため使用しない。
        """
        pairs = self._extract_proper_noun_pairs(source, translation)
        best = None
        best_score = 0.0
        for en_phrase, ja_phrase in pairs:
            en_words = set(re.findall(r'[a-zA-Z]{2,}', en_phrase.lower()))
            en_stems = {self._stem(w) for w in en_words}
            overlap = len(stems & en_stems)
            ratio = overlap / len(stems) if stems else 0
            if ratio >= 0.6 and len(ja_phrase) <= 40:
                score = ratio
                if len(en_phrase.split()) <= 6:
                    score += 0.3
                if score > best_score:
                    best_score = score
                    best = ja_phrase
        return best

    def find_term_translations(self, batch_texts: List[str],
                               cross_mod_data: dict = None,
                               exclude_keys: set = None,
                               limit: int = 30,
                               cross_mod_index: dict = None) -> List[Tuple[str, str]]:
        """
        バッチテキストから固有名詞（アイテム名・ブロック名等）を抽出し、
        TM全体と他MODデータから訳語を検索する。

        "Gem Extractors" → stem "gem extract" → TM内の "Gem Extractor" (単数形) もヒット
        """
        if not batch_texts or limit <= 0:
            return []

        import time as _time
        t0 = _time.perf_counter()

        exclude_keys = exclude_keys or set()

        seen_terms = {}
        for t in batch_texts:
            if not isinstance(t, str):
                continue
            cleaned = re.sub(r'&[0-9a-fk-or]', '', t)
            for term, stems in self._extract_terms_from_text(cleaned):
                key = term.lower()
                if key not in seen_terms:
                    seen_terms[key] = (term, stems)
        terms = list(seen_terms.values())
        if not terms:
            return []

        print(f"[TM-TERM] extracted {len(terms)} terms: {[t[0] for t in terms[:10]]}")

        batch_rows = self._batch_search_terms_in_tm(terms, exclude_keys)
        print(f"[TM-TERM] batch SQL returned {len(batch_rows)} rows in {_time.perf_counter()-t0:.3f}s")

        term_results = self._score_batch_term_rows(terms, batch_rows)

        results = []
        seen = set()
        for term, stems in terms:
            if term.lower() in seen:
                continue
            ja = term_results.get(term.lower())
            if not ja and cross_mod_data:
                if cross_mod_index:
                    ja = self._search_term_in_cross_mod_indexed(term, stems, cross_mod_index, cross_mod_data, exclude_keys)
                else:
                    ja = self._search_term_in_cross_mod(term, stems, cross_mod_data, exclude_keys)

            if ja:
                seen.add(term.lower())
                results.append((term, ja))
                print(f"[TM-TERM] MATCH: '{term}' → '{ja}'")

            if len(results) >= limit:
                break

        print(f"[TM-TERM] {len(results)} matches in {_time.perf_counter()-t0:.3f}s")
        return results

    def _build_fts5_term_expr(self, term: str) -> str:
        """termからFTS5 AND/ORプレフィックスMATCH式を構築する。"""
        word_exprs = []
        for w in term.split():
            wl = w.lower()
            if len(wl) < 2:
                continue
            group = {wl, self._stem(wl)}
            s = wl
            for _ in range(3):
                ns = self._stem(s)
                group.add(ns)
                if ns == s:
                    break
                s = ns
            or_parts = [f'{v}*' for v in group if len(v) >= 2]
            if or_parts:
                word_exprs.append('(' + ' OR '.join(or_parts) + ')')
        return ' AND '.join(word_exprs) if word_exprs else ''

    def _build_word_stem_groups_for(self, term: str) -> list:
        words = term.split()
        groups = []
        for w in words:
            wl = w.lower()
            if len(wl) < 2:
                continue
            group = {wl, self._stem(wl)}
            s = wl
            for _ in range(3):
                ns = self._stem(s)
                group.add(ns)
                if ns == s:
                    break
                s = ns
            groups.append(group)
        return groups

    def _batch_search_terms_in_tm(self, terms: list, exclude_keys: set) -> list:
        if not terms:
            return []

        if self._fts5_available:
            try:
                return self._batch_search_terms_fts5(terms, exclude_keys)
            except Exception:
                pass

        return self._batch_search_terms_like(terms, exclude_keys)

    def _batch_search_terms_fts5(self, terms: list, exclude_keys: set) -> list:
        conn = self._get_connection()
        cursor = conn.cursor()

        all_rows = []
        seen_rk = set()

        exclude_clause = ""
        exclude_params = []
        if exclude_keys:
            placeholders = ','.join(['?' for _ in exclude_keys])
            exclude_clause = f"AND t.key NOT IN ({placeholders})"
            exclude_params = list(exclude_keys)

        for term, stems in terms:
            match_expr = self._build_fts5_term_expr(term)
            if not match_expr:
                continue

            try:
                cursor.execute(
                    f'SELECT DISTINCT source, translation, origin, key '
                    f'FROM translations_fts f '
                    f'JOIN translations t ON t.id = f.rowid '
                    f'WHERE f.source MATCH ? AND t.source != "" AND t.translation != "" '
                    f'{exclude_clause} '
                    f'LIMIT 30',
                    [match_expr] + exclude_params
                )
                for row in cursor.fetchall():
                    rk = (row['source'] or '', row['key'] or '')
                    if rk not in seen_rk:
                        seen_rk.add(rk)
                        all_rows.append(row)
            except sqlite3.OperationalError:
                raise

            if len(all_rows) >= 500:
                break

        key_conditions = []
        key_params = []
        for term, stems in terms:
            wsg = self._build_word_stem_groups_for(term)
            if not wsg:
                continue
            key_and = []
            for group in wsg:
                or_parts = [f"(key LIKE ?)" for _ in group]
                key_and.append(f"({' OR '.join(or_parts)})")
                key_params.extend([f'%{v}%' for v in group])
            key_conditions.append(f"({' AND '.join(key_and)})")

        if key_conditions:
            key_where = ' OR '.join(key_conditions)
            exclude_clause2 = ""
            exclude_params2 = []
            if exclude_keys:
                placeholders = ','.join(['?' for _ in exclude_keys])
                exclude_clause2 = f"AND key NOT IN ({placeholders})"
                exclude_params2 = list(exclude_keys)
            cursor.execute(
                f"SELECT DISTINCT source, translation, origin, key FROM translations "
                f"WHERE (source = '' OR source IS NULL) AND ({key_where}) "
                f"AND translation != '' AND LENGTH(translation) <= 40 "
                f"{exclude_clause2} LIMIT 100",
                key_params + exclude_params2
            )
            for row in cursor.fetchall():
                rk = ('', row['key'] or '')
                if rk not in seen_rk:
                    seen_rk.add(rk)
                    all_rows.append(row)

        return all_rows

    def _batch_search_terms_like(self, terms: list, exclude_keys: set) -> list:
        all_source_conditions = []
        all_source_params = []
        all_key_conditions = []
        all_key_params = []

        for term, stems in terms:
            words = term.split()
            word_stem_groups = self._build_word_stem_groups_for(term)
            if not word_stem_groups:
                continue

            and_parts = []
            for group in word_stem_groups:
                or_parts = [f"(source LIKE ?)" for _ in group]
                and_parts.append(f"({' OR '.join(or_parts)})")
                all_source_params.extend([f'%{v}%' for v in group])
            all_source_conditions.append(f"({' AND '.join(and_parts)})")

            all_variants = set()
            for group in word_stem_groups:
                all_variants.update(group)
            key_variants = [v for v in all_variants if len(v) >= 3]
            if key_variants:
                key_and = []
                for group in word_stem_groups:
                    or_parts = [f"(key LIKE ?)" for _ in group]
                    key_and.append(f"({' OR '.join(or_parts)})")
                    all_key_params.extend([f'%{v}%' for v in group])
                all_key_conditions.append(f"({' AND '.join(key_and)})")

        if not all_source_conditions:
            return []

        conn = self._get_connection()
        cursor = conn.cursor()

        source_where = ' OR '.join(all_source_conditions)
        key_where = ' OR '.join(all_key_conditions) if all_key_conditions else "1=0"

        exclude_clause = ""
        exclude_params = []
        if exclude_keys:
            placeholders = ','.join(['?' for _ in exclude_keys])
            exclude_clause = f"AND key NOT IN ({placeholders})"
            exclude_params = list(exclude_keys)

        query = (
            f"SELECT DISTINCT source, translation, origin, key FROM translations "
            f"WHERE translation != '' "
            f"{exclude_clause} "
            f"AND ("
            f"  (source != '' AND ({source_where}))"
            f"  OR ((source = '' OR source IS NULL) AND ({key_where}) AND LENGTH(translation) <= 40)"
            f") "
            f"LIMIT 300"
        )
        cursor.execute(query, exclude_params + all_source_params + all_key_params)
        return cursor.fetchall()

    def _score_batch_term_rows(self, terms: list, rows: list) -> dict:
        if not rows:
            return {}

        cjk_re = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]')
        color_re = re.compile(r'&[0-9a-fk-or]')
        word_re = re.compile(r'[a-zA-Z]{2,}')

        pre_rows = []
        stem_to_ri = {}

        for i, row in enumerate(rows):
            translation = row['translation'] or ''
            if not cjk_re.search(translation):
                pre_rows.append(None)
                continue

            source = row['source'] or ''
            row_key = row['key'] or ''
            origin = row['origin'] or 'ai'
            cleaned_trans = color_re.sub('', translation).strip()

            row_stems = set()
            if source:
                src_clean = color_re.sub('', source).strip()
                src_words = set(word_re.findall(src_clean.lower()))
                row_stems = {self._stem(w) for w in src_words}
                pre_rows.append({
                    'source': source, 'origin': origin, 'key': row_key,
                    'cleaned_trans': cleaned_trans, 'src_clean': src_clean,
                    'src_stems': row_stems, 'src_words': src_words,
                    'row_stems': row_stems, 'is_empty': False,
                })
            else:
                key_text = row_key.replace('_', ' ').replace('.', ' ').lower()
                key_words = set(word_re.findall(key_text))
                row_stems = {self._stem(w) for w in key_words}
                pre_rows.append({
                    'source': source, 'origin': origin, 'key': row_key,
                    'cleaned_trans': cleaned_trans, 'key_text': key_text,
                    'key_stems': row_stems, 'row_stems': row_stems,
                    'is_empty': True,
                })

            for stem in row_stems:
                stem_to_ri.setdefault(stem, set()).add(i)

        term_results = {}
        for term, stems in terms:
            word_stem_groups = self._build_word_stem_groups_for(term)
            words = term.split()
            best = None
            best_score = 0.0
            term_lower = term.lower()

            all_variants = set()
            for group in word_stem_groups:
                all_variants.update(group)

            candidate_ri = set()
            for v in all_variants:
                if v in stem_to_ri:
                    candidate_ri.update(stem_to_ri[v])

            for i in candidate_ri:
                rd = pre_rows[i]
                if rd is None:
                    continue

                origin = rd['origin']
                cleaned_trans = rd['cleaned_trans']

                if not rd['is_empty']:
                    source = rd['source']
                    match = True
                    for group in word_stem_groups:
                        if not any(v in source.lower() for v in group):
                            match = False
                            break
                    if not match:
                        continue

                    src_clean = rd['src_clean']
                    src_stems = rd['src_stems']
                    src_words = rd['src_words']

                    if len(source) <= self._MAX_TERM_SOURCE_LEN:
                        overlap = len(stems & src_stems)
                        ratio = overlap / len(stems) if stems else 0

                        if ratio < 0.6:
                            continue

                        score = ratio
                        if src_clean.lower() == term_lower:
                            score += 2.0
                        if abs(len(src_words) - len(words)) <= 1:
                            score += 0.5
                        if len(src_stems) > len(stems):
                            extra_ratio = (len(src_stems) - len(stems)) / len(src_stems)
                            score -= extra_ratio * 0.5
                        if origin == 'user':
                            score += 0.5
                        elif origin == 'ai_corrected':
                            score += 0.3

                        trans_len = len(cleaned_trans)
                        if trans_len <= 15:
                            score += 0.3
                        elif trans_len <= 30:
                            score += 0.1

                        if score > best_score:
                            best_score = score
                            best = cleaned_trans
                    else:
                        pairs = self._extract_proper_noun_pairs(source, rows[i]['translation'] or '')
                        for en_phrase, ja_phrase in pairs:
                            en_words = set(word_re.findall(en_phrase.lower()))
                            en_stems = {self._stem(w) for w in en_words}
                            overlap = len(stems & en_stems)
                            ratio = overlap / len(stems) if stems else 0

                            if ratio >= 0.6:
                                score = 0.75 + (0.3 if origin == 'user' else 0.1 if origin == 'ai_corrected' else 0)
                                if score > best_score:
                                    best_score = score
                                    best = ja_phrase
                                break
                else:
                    key_text = rd['key_text']
                    key_stems = rd['key_stems']

                    match = True
                    for group in word_stem_groups:
                        if not any(v in key_text for v in group):
                            match = False
                            break
                    if not match:
                        continue

                    overlap = len(stems & key_stems)
                    ratio = overlap / len(stems) if stems else 0

                    if ratio >= 0.6:
                        score = 0.7 + (0.2 if origin == 'user' else 0.1 if origin == 'ai_corrected' else 0)
                        if len(cleaned_trans) <= 15:
                            score += 0.2
                        if score > best_score:
                            best_score = score
                            best = cleaned_trans

            if best and best_score >= 0.8:
                if best.strip().lower() in self._TM_EMPTY_BLACKLIST:
                    continue
                term_results[term_lower] = best
                print(f"[TM-TERM-BATCH] '{term}' → '{best}' (score={best_score:.2f})")

        return term_results

    def _build_word_stem_groups(self, term: str) -> list:
        words = term.split()
        groups = []
        for w in words:
            wl = w.lower()
            if len(wl) < 2:
                continue
            group = {wl, self._stem(wl)}
            s = wl
            for _ in range(3):
                ns = self._stem(s)
                group.add(ns)
                if ns == s:
                    break
                s = ns
            groups.append(group)
        return groups

    _TERM_STOP_WORDS = frozenset({
        'there', 'here', 'this', 'that', 'these', 'those',
        'which', 'where', 'when', 'what', 'who', 'whom', 'whose',
        'will', 'would', 'shall', 'should', 'could', 'might', 'must',
        'have', 'has', 'had', 'having', 'been', 'being',
        'does', 'does', 'doing', 'done',
        'from', 'with', 'into', 'upon', 'about', 'above', 'after',
        'before', 'below', 'between', 'during', 'except', 'through',
        'under', 'without', 'within', 'along', 'among',
        'also', 'very', 'just', 'only', 'even', 'still', 'already',
        'some', 'every', 'each', 'both', 'either', 'neither',
        'other', 'another', 'more', 'most', 'much', 'many',
        'than', 'then', 'else',
        'your', 'their', 'our', 'my', 'his', 'her', 'its',
    })

    _MAX_TERM_SOURCE_LEN = 60

    def _extract_terms_from_text(self, text: str) -> List[Tuple[str, set]]:
        """
        テキストから固有名詞候補を抽出する。
        カラーコード区切りセグメントと、連続大文字語列を対象。
        単一の大文字単語はノイズが多いため除外。

        Returns:
            [(term_text, stem_set), ...] 重複排除済み
        """
        candidates = []

        color_segments = re.findall(r'&[0-9a-fk-or]([^&]+?)(?= &[0-9a-fk-or]|&r|$)', text)
        for seg in color_segments:
            seg = seg.strip()
            seg = re.sub(r'\d+', '', seg).strip()
            if not seg:
                continue
            words = seg.split()
            words = [w for w in words if w.lower() not in self._TERM_STOP_WORDS and len(w) >= 2]
            if not words:
                continue
            if 1 <= len(words) <= 5 and any(re.search(r'[A-Z][a-zA-Z]+', w) for w in words):
                stems = {self._stem(w.lower()) for w in words}
                stems -= {self._stem(w) for w in self._TERM_STOP_WORDS}
                if stems:
                    candidates.append((seg, stems))

        cap_phrases = re.findall(r'[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)+', text)
        for phrase in cap_phrases:
            words = phrase.split()
            words = [w for w in words if w.lower() not in self._TERM_STOP_WORDS]
            if len(words) < 2:
                continue
            stems = {self._stem(w.lower()) for w in words}
            stems -= {self._stem(w) for w in self._TERM_STOP_WORDS}
            if stems:
                candidates.append((phrase, stems))

        unique = []
        seen = set()
        for term, stems in candidates:
            key = term.lower()
            if key not in seen:
                seen.add(key)
                unique.append((term, stems))

        return unique

    def _search_term_in_tm(self, term: str, stems: set,
                           exclude_keys: set = None) -> Optional[str]:
        """
        TM内から固有名詞の訳語を検索する。単複変形・活用形も考慮。
        source検索・key検索・長文source検索を1クエリに統合し、Python側でスコアリング。
        exclude_keys に一致するキーのエントリはスキップする。
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        words = term.split()
        word_stem_groups = []
        for w in words:
            wl = w.lower()
            if len(wl) < 2:
                continue
            group = {wl, self._stem(wl)}
            s = wl
            for _ in range(3):
                ns = self._stem(s)
                group.add(ns)
                if ns == s:
                    break
                s = ns
            word_stem_groups.append(group)

        if not word_stem_groups:
            return None

        exclude_keys = exclude_keys or set()

        and_conditions = []
        source_like_params = []
        for group in word_stem_groups:
            or_parts = [f"(source LIKE ?)" for _ in group]
            and_conditions.append(f"({' OR '.join(or_parts)})")
            source_like_params.extend([f'%{v}%' for v in group])

        source_where = ' AND '.join(and_conditions)

        all_variants = set()
        for group in word_stem_groups:
            all_variants.update(group)
        key_variants = [v for v in all_variants if len(v) >= 3]

        key_and_conditions = []
        key_like_params = []
        if key_variants:
            for group in word_stem_groups:
                or_parts = [f"(key LIKE ?)" for _ in group]
                key_and_conditions.append(f"({' OR '.join(or_parts)})")
                key_like_params.extend([f'%{v}%' for v in group])
        key_where = ' AND '.join(key_and_conditions) if key_and_conditions else "1=0"

        exclude_clause = ""
        exclude_params = []
        if exclude_keys:
            placeholders = ','.join(['?' for _ in exclude_keys])
            exclude_clause = f"AND key NOT IN ({placeholders})"
            exclude_params = list(exclude_keys)

        query = (
            f"SELECT source, translation, origin, key FROM translations "
            f"WHERE translation != '' "
            f"{exclude_clause} "
            f"AND ("
            f"  (source != '' AND ({source_where}))"
            f"  OR ((source = '' OR source IS NULL) AND ({key_where}) AND LENGTH(translation) <= 40)"
            f") "
            f"LIMIT 100"
        )
        cursor.execute(query, exclude_params + source_like_params + key_like_params)
        rows = cursor.fetchall()

        print(f"[TM-TERM-SEARCH] term='{term}', candidates={len(rows)}")

        best = None
        best_score = 0.0

        for row in rows:
            source = row['source'] or ''
            translation = row['translation'] or ''
            origin = row['origin'] or 'ai'
            row_key = row['key'] or ''

            has_cjk = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', translation))
            if not has_cjk:
                continue

            cleaned_trans = re.sub(r'&[0-9a-fk-or]', '', translation).strip()

            if source:
                src_clean = re.sub(r'&[0-9a-fk-or]', '', source).strip()

                if len(source) <= self._MAX_TERM_SOURCE_LEN:
                    src_words = set(re.findall(r'[a-zA-Z]{2,}', src_clean.lower()))
                    src_stems = {self._stem(w) for w in src_words}

                    overlap = len(stems & src_stems)
                    ratio = overlap / len(stems) if stems else 0

                    if ratio < 0.6:
                        continue

                    score = ratio
                    if src_clean.lower() == term.lower():
                        score += 2.0
                    if abs(len(src_words) - len(words)) <= 1:
                        score += 0.5
                    if len(src_stems) > len(stems) and ratio < 1.0:
                        extra_ratio = (len(src_stems) - len(stems)) / len(src_stems)
                        score -= extra_ratio * 0.5
                    if origin == 'user':
                        score += 0.5
                    elif origin == 'ai_corrected':
                        score += 0.3

                    trans_len = len(cleaned_trans)
                    if trans_len <= 15:
                        score += 0.3
                    elif trans_len <= 30:
                        score += 0.1

                    if score > best_score:
                        best_score = score
                        best = cleaned_trans
                        print(f"[TM-TERM-SEARCH]   source match: '{src_clean[:60]}' → '{cleaned_trans[:40]}' (score={score:.2f})")
                else:
                    pairs = self._extract_proper_noun_pairs(source, translation)
                    for en_phrase, ja_phrase in pairs:
                        en_words = set(re.findall(r'[a-zA-Z]{2,}', en_phrase.lower()))
                        en_stems = {self._stem(w) for w in en_words}
                        overlap = len(stems & en_stems)
                        ratio = overlap / len(stems) if stems else 0

                        if ratio >= 0.6:
                            score = 0.75 + (0.3 if origin == 'user' else 0.1 if origin == 'ai_corrected' else 0)
                            if score > best_score:
                                best_score = score
                                best = ja_phrase
                                print(f"[TM-TERM-SEARCH]   long source pair: '{en_phrase}' → '{ja_phrase}' (ratio={ratio:.2f})")
                            break
                    else:
                        result = self._extract_noun_pair_from_text(source, translation, term, stems)
                        if result and best_score < 0.5:
                            best_score = 0.5
                            best = result
                            print(f"[TM-TERM-SEARCH]   long source text extraction: '{term}' → '{result}'")
            else:
                key_text = row_key.replace('_', ' ').replace('.', ' ').lower()
                key_words = set(re.findall(r'[a-zA-Z]{2,}', key_text))
                key_stems = {self._stem(w) for w in key_words}

                overlap = len(stems & key_stems)
                ratio = overlap / len(stems) if stems else 0

                if ratio >= 0.6:
                    score = 0.7 + (0.2 if origin == 'user' else 0.1 if origin == 'ai_corrected' else 0)
                    if len(cleaned_trans) <= 15:
                        score += 0.2
                    if score > best_score:
                        best_score = score
                        best = cleaned_trans
                        print(f"[TM-TERM-SEARCH]   key match: '{row_key}' → '{cleaned_trans[:40]}' (ratio={ratio:.2f})")

        if best and best_score >= 0.8:
            print(f"[TM-TERM-SEARCH] result: '{term}' → '{best}' (best_score={best_score:.2f})")
            return best

        print(f"[TM-TERM-SEARCH] no match for '{term}' (best_score={best_score:.2f})")
        return None

    def _search_term_in_cross_mod(self, term: str, stems: set,
                                   cross_mod_data: dict,
                                   exclude_keys: set = None) -> Optional[str]:
        """
        現在のセッションでロードされている他MODデータから訳語を検索する。
        cross_mod_data: {key: {"original": "English text", "translation": "日本語"}}
        原文(English)にtermが含まれるエントリから訳語を返す。
        """
        if not cross_mod_data:
            return None

        exclude_keys = exclude_keys or set()

        term_lower = term.lower()
        words = term.split()
        best = None
        best_score = 0.0

        for key, entry in cross_mod_data.items():
            if key in exclude_keys:
                continue
            if isinstance(entry, str):
                original = ""
                translation = entry
            elif isinstance(entry, dict):
                original = entry.get("original", "")
                translation = entry.get("translation", "")
            else:
                continue

            if not translation or not isinstance(translation, str):
                continue

            original = original if isinstance(original, str) else ""

            has_cjk = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', translation))
            if not has_cjk:
                continue

            orig_clean = re.sub(r'&[0-9a-fk-or]', '', original).strip()
            orig_lower = orig_clean.lower()
            orig_words = set(re.findall(r'[a-zA-Z]{2,}', orig_lower))
            orig_stems = {self._stem(w) for w in orig_words}

            overlap = len(stems & orig_stems)
            ratio = overlap / len(stems) if stems else 0

            score = 0.0

            if ratio >= 0.8:
                score = ratio
                if orig_clean.strip().lower() == term_lower:
                    score += 2.0
                if abs(len(orig_words) - len(words)) <= 1:
                    score += 0.5
                if len(translation) <= 30:
                    score += 0.3
                elif len(translation) <= 50:
                    score += 0.1
                else:
                    score -= 0.5

            if score <= 0 and term_lower in orig_lower:
                score = 0.5

            if score <= 0:
                key_clean = key.replace('_', ' ').replace('.', ' ').lower()
                key_words = set(re.findall(r'[a-zA-Z]{2,}', key_clean))
                key_stems = {self._stem(w) for w in key_words}
                key_overlap = len(stems & key_stems)
                key_ratio = key_overlap / len(stems) if stems else 0
                if key_ratio >= 0.8:
                    score = key_ratio * 0.5
                elif term_lower in key.lower().replace('_', ' '):
                    score = 0.3

            if score > best_score:
                best_score = score
                best = re.sub(r'&[0-9a-fk-or]', '', translation).strip()

        if best and best_score >= 0.8:
            return best

        return None

    def _search_term_in_cross_mod_indexed(self, term: str, stems: set,
                                           cross_mod_index: dict,
                                           cross_mod_data: dict,
                                           exclude_keys: set = None) -> Optional[str]:
        exclude_keys = exclude_keys or set()
        candidates = {}

        for stem in stems:
            for entry_tuple in cross_mod_index.get(stem, []):
                key = entry_tuple[0]
                if key in exclude_keys:
                    continue
                if entry_tuple not in candidates:
                    candidates[entry_tuple] = 0
                candidates[entry_tuple] += 1

        scored = sorted(candidates.items(), key=lambda x: -x[1])

        term_lower = term.lower()
        words = term.split()
        best = None
        best_score = 0.0

        for (key, entry), _ in scored[:50]:
            if isinstance(entry, str):
                original = ""
                translation = entry
            elif isinstance(entry, dict):
                original = entry.get("original", "")
                translation = entry.get("translation", "")
            else:
                continue

            if not translation or not isinstance(translation, str):
                continue

            has_cjk = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', translation))
            if not has_cjk:
                continue

            orig_clean = re.sub(r'&[0-9a-fk-or]', '', original if isinstance(original, str) else "").strip()
            orig_lower = orig_clean.lower()
            orig_words = set(re.findall(r'[a-zA-Z]{2,}', orig_lower))
            orig_stems = {self._stem(w) for w in orig_words}

            overlap = len(stems & orig_stems)
            ratio = overlap / len(stems) if stems else 0

            score = 0.0
            if ratio >= 0.8:
                score = ratio
                if orig_clean.strip().lower() == term_lower:
                    score += 2.0
                if abs(len(orig_words) - len(words)) <= 1:
                    score += 0.5
                if len(translation) <= 30:
                    score += 0.3
                elif len(translation) <= 50:
                    score += 0.1
                else:
                    score -= 0.5

            if score <= 0 and term_lower in orig_lower:
                score = 0.5

            if score <= 0:
                key_clean = key.replace('_', ' ').replace('.', ' ').lower()
                key_words = set(re.findall(r'[a-zA-Z]{2,}', key_clean))
                key_stems = {self._stem(w) for w in key_words}
                key_overlap = len(stems & key_stems)
                key_ratio = key_overlap / len(stems) if stems else 0
                if key_ratio >= 0.8:
                    score = key_ratio * 0.5
                elif term_lower in key.lower().replace('_', ' '):
                    score = 0.3

            if score > best_score:
                best_score = score
                best = re.sub(r'&[0-9a-fk-or]', '', translation).strip()

        if best and best_score >= 0.8:
            return best

        return None

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
