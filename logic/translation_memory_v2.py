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
            cleaned = re.sub(r'&[0-9a-fk-or]', '', text)
            batch_words.update(w for w in re.findall(r'[a-zA-Z]{3,}', cleaned.lower()))
        batch_words -= self._STOP_WORDS

        if not batch_words:
            return []

        batch_stems = {self._stem(w) for w in batch_words}

        print(f"[TM-INT] batch_words: {batch_words}")
        print(f"[TM-INT] batch_stems: {batch_stems}")

        conn = self._get_connection()
        cursor = conn.cursor()

        if len(batch_stems) <= 3:
            min_stem_count = 1
        elif len(batch_stems) <= 8:
            min_stem_count = 2
        else:
            min_stem_count = max(2, len(batch_stems) // 4)

        print(f"[TM-INT] min_stem_count: {min_stem_count}")

        search_words = list(batch_words | batch_stems)
        top_words = sorted(search_words, key=len, reverse=True)[:20]
        print(f"[TM-INT] top_words (LIKE targets): {top_words[:10]}")

        # First: try without origin filter to see if entries exist at all
        if mod_name:
            cursor.execute(
                'SELECT COUNT(*) FROM translations WHERE mod_name = ? AND source != ""',
                (mod_name,)
            )
            mod_total = cursor.fetchone()[0]
            print(f"[TM-INT] TM entries for mod '{mod_name}': {mod_total}")

        cursor.execute('SELECT COUNT(*) FROM translations WHERE source != ""')
        total = cursor.fetchone()[0]
        print(f"[TM-INT] Total TM entries: {total}")

        # Check if 'gem' or 'extract' words exist in TM at all
        cursor.execute(
            "SELECT source, translation, origin, reviewed FROM translations WHERE source LIKE '%gem%' AND source LIKE '%extract%' LIMIT 5"
        )
        gem_rows = cursor.fetchall()
        print(f"[TM-INT] TM entries matching 'gem'+'extract': {len(gem_rows)}")
        for r in gem_rows:
            print(f"[TM-INT]   -> src='{r['source']}', trans='{r['translation']}', origin='{r['origin']}', reviewed={r['reviewed']}")

        cursor.execute(
            "SELECT source, translation, origin FROM translations WHERE translation LIKE '%ジェムエクストラクター%' LIMIT 5"
        )
        trans_rows = cursor.fetchall()
        print(f"[TM-INT] TM entries with 'ジェムエクストラクター' in translation: {len(trans_rows)}")
        for r in trans_rows:
            print(f"[TM-INT]   -> src='{r['source'][:80]}', trans='{r['translation'][:80]}', origin='{r['origin']}'")

        cursor.execute(
            "SELECT source, translation, origin FROM translations WHERE source LIKE '%Gem Extractor%' LIMIT 5"
        )
        ge_rows = cursor.fetchall()
        print(f"[TM-INT] TM entries with 'Gem Extractor' in source: {len(ge_rows)}")
        for r in ge_rows:
            print(f"[TM-INT]   -> src='{r['source'][:80]}', trans='{r['translation'][:80]}', origin='{r['origin']}'")

        like_clauses = ' OR '.join(['source LIKE ?' for _ in top_words])
        like_params = [f'%{w}%' for w in top_words]

        if mod_name:
            query = (
                f'SELECT source, translation, origin FROM translations '
                f'WHERE mod_name = ? AND source != "" '
                f'AND ({like_clauses}) '
                f'LIMIT 500'
            )
            params = [mod_name] + like_params
        else:
            query = (
                f'SELECT source, translation, origin FROM translations '
                f'WHERE source != "" '
                f'AND ({like_clauses}) '
                f'LIMIT 500'
            )
            params = like_params

        cursor.execute(query, params)
        candidates = cursor.fetchall()

        print(f"[TM-INT] SQL candidates: {len(candidates)}")
        print(f"[TM-INT] min_stem_count: {min_stem_count}")

        if not candidates:
            return []

        batch_texts_set = set(t for t in batch_texts if isinstance(t, str))

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

            if 'extract' in source.lower() and 'gem' in source.lower():
                common = batch_stems & source_stems
                jaccard_debug = self._compute_jaccard(batch_stems, source_stems)
                print(f"[TM-INT] gem+extract candidate: common={len(common)}/{min_stem_count}, jaccard={jaccard_debug:.3f}, src_len={len(source)}")
                print(f"[TM-INT]   source_words: {source_words}")
                print(f"[TM-INT]   common_stems: {common}")

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

            if jaccard >= self._SIMILARITY_THRESHOLD:
                if len(source) <= self._MAX_EXAMPLE_LENGTH and len(translation) <= self._MAX_EXAMPLE_LENGTH:
                    score = jaccard * origin_w
                    scored.append((score, jaccard, source, translation))
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

        empty_source_results = self._search_empty_source_pairs(cursor, batch_texts)
        scored.extend(empty_source_results)

        print(f"[TM-INT] scored: {len(scored)}, long_matched: {len(long_matched)}, empty_source: {len(empty_source_results)}")

        if not scored:
            return []

        for s in scored[:5]:
            print(f"[TM-INT]   scored: {s[2][:60]} → {s[3][:60]}")

        scored.sort(key=lambda x: -x[0])

        seen_phrases = set()
        results = []
        for _, _, source, translation in scored:
            if source not in seen_phrases:
                seen_phrases.add(source)
                results.append((source, translation))
                if len(results) >= limit:
                    break
        
        return results

    def _search_empty_source_pairs(self, cursor, batch_texts: List[str]) -> List[Tuple[float, float, str, str]]:
        batch_joined = ' '.join(t for t in batch_texts if isinstance(t, str))
        cleaned_batch = re.sub(r'&[0-9a-fk-or]', '', batch_joined)
        en_phrases = re.findall(r'[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*', cleaned_batch)
        en_phrases = [p for p in en_phrases if len(p.split()) >= 2]
        if not en_phrases:
            return []

        scored = []
        seen = set()
        for phrase in en_phrases:
            words = phrase.lower().split()
            conditions = []
            params = []
            for w in words:
                stems = set([w])
                s = w
                for _ in range(3):
                    ns = self._stem(s)
                    stems.add(ns)
                    if ns == s:
                        break
                    s = ns
                if any(s.endswith(x) for x in ('or', 'er', 'ion')) and len(s) > 4:
                    stems.add(s[:-2])
                sub = ' OR '.join(['key LIKE ?' for _ in stems])
                conditions.append(f'({sub})')
                params.extend([f'%{st}%' for st in stems])
            where = ' AND '.join(conditions)
            cursor.execute(
                f"SELECT key, translation, origin FROM translations "
                f"WHERE (source = '' OR source IS NULL) AND ({where}) AND translation != '' "
                f"AND LENGTH(translation) <= 30 "
                f"ORDER BY LENGTH(translation) ASC LIMIT 3",
                params
            )
            rows = cursor.fetchall()
            for row in rows:
                trans = row['translation'] or ''
                if not trans:
                    continue
                if re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', trans):
                    pair_key = (phrase, trans)
                    if pair_key not in seen:
                        seen.add(pair_key)
                        origin = row['origin'] or 'ai'
                        origin_w = self._ORIGIN_WEIGHT.get(origin, 1.0)
                        scored.append((1.0 * origin_w, 1.0, phrase, trans))
                        print(f"[TM-EMPTY] MATCH: '{phrase}' → '{trans}' (key={row['key']})")
                        break
        return scored

    def _extract_proper_noun_pairs(self, source: str, translation: str) -> List[Tuple[str, str]]:
        src_segments = re.findall(r'&[0-9a-fk-or]([^&]+?)(?:&r|$)', source)
        trans_segments = re.findall(r'&[0-9a-fk-or]([^&]+?)(?:&r|$)', translation)

        if not src_segments or not trans_segments:
            cleaned_src = re.sub(r'&[0-9a-fk-or]', '', source)
            cleaned_trans = re.sub(r'&[0-9a-fk-or]', '', translation)
            src_segments = [cleaned_src]
            trans_segments = [cleaned_trans]

        pairs = []
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

        return pairs

    def _extract_noun_pair_from_text(self, source: str, translation: str,
                                      term: str, stems: set) -> Optional[str]:
        """
        色コードがない長文から、termに対応する訳語を推定して抽出する。
        英語原文でtermが出現する位置と、日本語訳の対応するCJKチャンクから推定。
        """
        pairs = self._extract_proper_noun_pairs(source, translation)
        for en_phrase, ja_phrase in pairs:
            en_words = set(re.findall(r'[a-zA-Z]{2,}', en_phrase.lower()))
            en_stems = {self._stem(w) for w in en_words}
            overlap = len(stems & en_stems)
            ratio = overlap / len(stems) if stems else 0
            if ratio >= 0.6 and len(ja_phrase) <= 40:
                return ja_phrase

        src_clean = re.sub(r'&[0-9a-fk-or]', '', source)
        term_lower = term.lower()

        term_words = term_lower.split()
        src_lower = src_clean.lower()

        all_positions = []
        for tw in term_words:
            idx = src_lower.find(tw)
            if idx >= 0:
                all_positions.append(idx)

        if not all_positions:
            return None

        trans_clean = re.sub(r'&[0-9a-fk-or]', '', translation)

        cjk_chunks = re.findall(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\u30FC\uFF70]+', trans_clean)
        if not cjk_chunks:
            return None

        term_char_count = len(term.replace(' ', ''))
        for chunk in cjk_chunks:
            ratio = len(chunk) / term_char_count if term_char_count > 0 else 0
            if 0.3 <= ratio <= 3.0 and len(chunk) <= 30:
                return chunk

        return None

    def find_term_translations(self, batch_texts: List[str],
                               cross_mod_data: dict = None,
                               exclude_keys: set = None,
                               limit: int = 30) -> List[Tuple[str, str]]:
        """
        バッチテキストから固有名詞（アイテム名・ブロック名等）を抽出し、
        TM全体と他MODデータから訳語を検索する。

        "Gem Extractors" → stem "gem extract" → TM内の "Gem Extractor" (単数形) もヒット

        Args:
            batch_texts: 翻訳対象テキスト群
            cross_mod_data: 他MODの翻訳データ (オプション)
            exclude_keys: 翻訳対象のキー集合（同一キーのTMエントリを除外）
            limit: 返す最大結果数
        Returns:
            [(english_term, japanese_term), ...] 固有名詞訳語リスト
        """
        if not batch_texts or limit <= 0:
            return []

        exclude_keys = exclude_keys or set()

        all_text = " ".join(t for t in batch_texts if isinstance(t, str))
        cleaned = re.sub(r'&[0-9a-fk-or]', '', all_text)

        terms = self._extract_terms_from_text(cleaned)
        if not terms:
            return []

        print(f"[TM-TERM] extracted terms: {terms}")

        results = []
        seen = set()

        for term, stems in terms:
            if term.lower() in seen:
                continue

            ja = self._search_term_in_tm(term, stems, exclude_keys)
            if not ja and cross_mod_data:
                ja = self._search_term_in_cross_mod(term, stems, cross_mod_data, exclude_keys)

            if ja:
                seen.add(term.lower())
                results.append((term, ja))
                print(f"[TM-TERM] MATCH: '{term}' → '{ja}'")

            if len(results) >= limit:
                break

        return results

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
        全単語の語幹がANDで含まれるsourceのみ候補とし、長文descriptionは除外する。
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
        params = []
        for group in word_stem_groups:
            or_parts = [f"(source LIKE ?)" for _ in group]
            and_conditions.append(f"({' OR '.join(or_parts)})")
            params.extend([f'%{v}%' for v in group])

        where_clause = ' AND '.join(and_conditions)

        max_src = self._MAX_TERM_SOURCE_LEN
        query = (
            f"SELECT source, translation, origin FROM translations "
            f"WHERE source != '' AND translation != '' "
            f"AND LENGTH(source) <= {max_src} "
            f"AND ({where_clause}) "
            f"ORDER BY CASE origin WHEN 'user' THEN 0 WHEN 'ai_corrected' THEN 1 ELSE 2 END, "
            f"LENGTH(source) ASC "
            f"LIMIT 50"
        )
        cursor.execute(query, params)
        rows = cursor.fetchall()

        print(f"[TM-TERM-SEARCH] term='{term}', stems={stems}, word_groups={[list(g) for g in word_stem_groups]}, candidates={len(rows)}")
        for r in rows[:5]:
            print(f"[TM-TERM-SEARCH]   src='{(r['source'] or '')[:60]}' → '{(r['translation'] or '')[:40]}'")

        best = None
        best_score = 0.0

        for row in rows:
            source = row['source'] or ''
            translation = row['translation'] or ''
            origin = row['origin'] or 'ai'

            has_cjk = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', translation))
            if not has_cjk:
                continue

            src_clean = re.sub(r'&[0-9a-fk-or]', '', source).strip()
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
            if origin == 'user':
                score += 0.5
            elif origin == 'ai_corrected':
                score += 0.3

            cleaned_trans = re.sub(r'&[0-9a-fk-or]', '', translation).strip()
            trans_len = len(cleaned_trans)
            if trans_len <= 15:
                score += 0.3
            elif trans_len <= 30:
                score += 0.1

            if score > best_score:
                best_score = score
                best = cleaned_trans

        if best and best_score >= 1.0:
            print(f"[TM-TERM-SEARCH] source match: '{term}' → '{best}' (score={best_score:.2f})")
            return best

        all_variants = set()
        for group in word_stem_groups:
            all_variants.update(group)
        key_variants = [v for v in all_variants if len(v) >= 3]
        if not key_variants:
            return None

        key_and_conditions = []
        key_params = []
        for group in word_stem_groups:
            or_parts = [f"(key LIKE ?)" for _ in group]
            key_and_conditions.append(f"({' OR '.join(or_parts)})")
            key_params.extend([f'%{v}%' for v in group])

        key_where = ' AND '.join(key_and_conditions)

        exclude_key_clause = ""
        exclude_key_params = []
        if exclude_keys:
            key_placeholders = ','.join(['?' for _ in exclude_keys])
            exclude_key_clause = f"AND key NOT IN ({key_placeholders})"
            exclude_key_params = list(exclude_keys)

        cursor.execute(
            f"SELECT key, translation, origin FROM translations "
            f"WHERE (source = '' OR source IS NULL) AND translation != '' "
            f"AND LENGTH(translation) <= 40 "
            f"{exclude_key_clause} "
            f"AND ({key_where}) "
            f"LIMIT 50",
            exclude_key_params + key_params
        )
        key_rows = cursor.fetchall()

        print(f"[TM-TERM-SEARCH] key search candidates: {len(key_rows)}")
        for r in key_rows[:5]:
            print(f"[TM-TERM-SEARCH]   key='{r['key']}' → '{(r['translation'] or '')[:40]}'")

        for row in key_rows:
            trans = row['translation'] or ''
            if not trans:
                continue
            key_text = row['key'] or ''
            key_clean = key_text.replace('_', ' ').replace('.', ' ').lower()
            key_words = set(re.findall(r'[a-zA-Z]{2,}', key_clean))
            key_stems = {self._stem(w) for w in key_words}

            overlap = len(stems & key_stems)
            ratio = overlap / len(stems) if stems else 0

            if ratio >= 0.6 and re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]', trans):
                cleaned_trans = re.sub(r'&[0-9a-fk-or]', '', trans).strip()
                print(f"[TM-TERM-SEARCH] key match: '{term}' → '{cleaned_trans}' (ratio={ratio:.2f})")
                return cleaned_trans

        print(f"[TM-TERM-SEARCH] Phase 3: searching long descriptions for '{term}'...")
        long_query = (
            f"SELECT source, translation, origin, key FROM translations "
            f"WHERE source != '' AND translation != '' "
            f"AND LENGTH(source) > {max_src} AND LENGTH(source) <= 500 "
            f"AND ({where_clause}) "
            f"LIMIT 20"
        )
        cursor.execute(long_query, params)
        long_rows = cursor.fetchall()

        print(f"[TM-TERM-SEARCH] Phase 3 long description candidates: {len(long_rows)}")

        for row in long_rows:
            source = row['source'] or ''
            translation = row['translation'] or ''
            row_key = row['key'] or ''

            if row_key in exclude_keys:
                continue

            pairs = self._extract_proper_noun_pairs(source, translation)
            found_in_pairs = False
            for en_phrase, ja_phrase in pairs:
                en_words = set(re.findall(r'[a-zA-Z]{2,}', en_phrase.lower()))
                en_stems = {self._stem(w) for w in en_words}
                overlap = len(stems & en_stems)
                ratio = overlap / len(stems) if stems else 0

                if ratio >= 0.6:
                    print(f"[TM-TERM-SEARCH] Phase 3 noun pair: '{en_phrase}' → '{ja_phrase}' (ratio={ratio:.2f})")
                    return ja_phrase
                found_in_pairs = True

            if not found_in_pairs:
                result = self._extract_noun_pair_from_text(source, translation, term, stems)
                if result:
                    print(f"[TM-TERM-SEARCH] Phase 3 text extraction: '{term}' → '{result}'")
                    return result

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
