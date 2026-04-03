import os
import json
import time
import zipfile
from PySide6.QtCore import QThread, Signal

from logic.file_handler import FileHandler

class ModBatchLoadThread(QThread):
    progress = Signal(int, int)
    mod_loaded = Signal(dict)
    load_finished = Signal(int, int)
    error = Signal(str, str)

    def __init__(self, mod_files, file_handler, memory):
        super().__init__()
        self.mod_files = mod_files
        self.file_handler = file_handler
        self.memory = memory
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        total = len(self.mod_files)
        loaded = 0
        errors = 0

        for i, mod_file in enumerate(self.mod_files):
            if not self.is_running:
                break

            try:
                if os.path.isdir(mod_file):
                    mod_name, found_files = self.file_handler.load_folder(mod_file)
                else:
                    mod_name, found_files = self.file_handler.load_zip(mod_file)

                if not found_files:
                    self.progress.emit(i + 1, total)
                    continue

                target = next(
                    (f for f in found_files if 'en_us.json' in f),
                    next((f for f in found_files if 'en_us.lang' in f), found_files[0])
                )

                data = self.file_handler.read_translation_file(mod_file, target)
                data = {k: v for k, v in data.items() if v and str(v).strip()}
                char_count = sum(len(str(v)) for v in data.values())

                memory_translations = self.memory.apply_to(data, mod_name=mod_name)

                self.mod_loaded.emit({
                    "path": mod_file,
                    "name": mod_name,
                    "original": data,
                    "translations": dict(memory_translations) if memory_translations else {},
                    "files": found_files,
                    "target_file": target,
                    "_char_count": char_count,
                })
                loaded += 1
            except Exception as e:
                errors += 1
                self.error.emit(mod_file, str(e))

            self.progress.emit(i + 1, total)

        self.load_finished.emit(loaded, errors)


class ResourcePackImportThread(QThread):
    progress = Signal(int, int, str) # current, total, phase
    import_finished = Signal(dict, int, list, dict) # all_translations, applied_count, matched_mods, mod_updates
    error = Signal(str)

    def __init__(self, path, loaded_mods, file_handler, memory, target_lang="ja_jp"):
        super().__init__()
        self.path = path
        # メインスレッドのdictを直接操作しないよう、読み取り専用のスナップショットを作成
        self.mod_snapshots = {
            mod_path: {
                "name": mod_data["name"],
                "original_keys": set(mod_data["original"].keys()),
                "target_file": mod_data["target_file"],
                "type": mod_data.get("type", "mod"),
            }
            for mod_path, mod_data in loaded_mods.items()
        }
        self.file_handler = file_handler
        self.memory = memory
        self.target_lang = target_lang
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        all_translations = {}
        try:
            t0 = time.time()
            self._read_pack_files(all_translations)
            print(f"[RP] _read_pack_files: {time.time() - t0:.2f}s, files={len(all_translations)}")

            if not self.is_running or not all_translations:
                self.import_finished.emit({}, 0, [], {})
                return

            t1 = time.time()
            self._match_and_apply(all_translations)
            print(f"[RP] _match_and_apply: {time.time() - t1:.2f}s")

        except Exception as e:
            print(f"[RP] ERROR: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))

    def _read_pack_files(self, all_translations):
        if os.path.isdir(self.path):
            files_to_scan = []
            for root, _, files in os.walk(self.path):
                for f in files:
                    if f.endswith(f'{self.target_lang}.json') or f.endswith(f'{self.target_lang}.lang'):
                        files_to_scan.append(os.path.join(root, f))

            total_files = len(files_to_scan)
            progress_interval = max(1, total_files // 100)
            for i, full_path in enumerate(files_to_scan):
                if not self.is_running:
                    return
                rel_path = os.path.relpath(full_path, self.path).replace('\\', '/')
                try:
                    with open(full_path, 'r', encoding='utf-8') as lang_file:
                        content = lang_file.read()
                        if full_path.endswith('.json'):
                            translations = json.loads(content)
                        else:
                            translations = self.file_handler._parse_lang(content)
                        if translations:
                            all_translations[rel_path] = translations
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    continue
                if (i + 1) % progress_interval == 0 or i + 1 == total_files:
                    self.progress.emit(i + 1, total_files, "read")
        else:
            with zipfile.ZipFile(self.path, 'r') as zf:
                namelist = zf.namelist()
                files_to_scan = [f for f in namelist if f.endswith(f'{self.target_lang}.json') or f.endswith(f'{self.target_lang}.lang')]
                total_files = len(files_to_scan)
                progress_interval = max(1, total_files // 100)
                for i, f in enumerate(files_to_scan):
                    if not self.is_running:
                        return
                    if not FileHandler._is_safe_zip_path(f):
                        continue
                    try:
                        with zf.open(f) as zfile:
                            content = zfile.read().decode('utf-8')
                            if f.endswith('.json'):
                                translations = json.loads(content)
                            else:
                                translations = self.file_handler._parse_lang(content)
                            if translations:
                                all_translations[f] = translations
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError, KeyError):
                        continue
                    if (i + 1) % progress_interval == 0 or i + 1 == total_files:
                        self.progress.emit(i + 1, total_files, "read")

    def _match_and_apply(self, all_translations):
        t0 = time.time()
        target_suffix_map = {}
        for pack_path in all_translations:
            parts = pack_path.replace('\\', '/').split('/')
            for p in parts:
                if p.endswith('.json') or p.endswith('.lang'):
                    target_suffix_map.setdefault(p, []).append(pack_path)

        namespace_map = {}
        for pack_path in all_translations:
            parts = pack_path.replace('\\', '/').split('/')
            if len(parts) >= 3 and parts[0] == 'assets':
                ns = parts[1]
                namespace_map.setdefault(ns, []).append(pack_path)

        translation_key_sets = {
            pack_path: set(trans.keys())
            for pack_path, trans in all_translations.items()
        }
        total_pack_keys = sum(len(k) for k in translation_key_sets.values())
        print(f"[RP] index built: {time.time() - t0:.2f}s, suffixes={len(target_suffix_map)}, namespaces={len(namespace_map)}, pack_files={len(all_translations)}, total_keys={total_pack_keys}")

        ftbquest_paths = [
            pack_path for pack_path in all_translations
            if "ftbquests" in pack_path.replace('\\', '/')
        ]

        mods_list = list(self.mod_snapshots.items())
        total_mods = len(mods_list)
        applied_count = 0
        matched_mods = []
        memory_updates = {}
        mod_updates = {}
        progress_interval = max(1, total_mods // 100)

        t_match = time.time()
        for i, (mod_path, snap) in enumerate(mods_list):
            if not self.is_running:
                break

            target_file = snap["target_file"].replace('\\', '/')
            ja_target = target_file.replace('en_us', self.target_lang)
            mod_type = snap["type"]
            mod_keys = snap["original_keys"]

            found_translations, pack_path = self._find_matching_translations(
                snap, ja_target, mod_type, all_translations,
                target_suffix_map, namespace_map,
                translation_key_sets, ftbquest_paths
            )

            if found_translations is not None:
                pack_keys = translation_key_sets.get(pack_path, set(found_translations.keys()))
                matching_keys = pack_keys & mod_keys
                if matching_keys:
                    updates = {}
                    for key in matching_keys:
                        updates[key] = found_translations[key]
                    mod_updates[mod_path] = updates
                    applied_count += len(matching_keys)
                    matched_mods.append(snap["name"])
                    memory_updates.update(updates)

            if (i + 1) % progress_interval == 0 or i + 1 == total_mods:
                self.progress.emit(i + 1, total_mods, "match")

        print(f"[RP] matching loop: {time.time() - t_match:.2f}s, matched={len(matched_mods)}/{total_mods}, keys={applied_count}")

        t_mem = time.time()
        if memory_updates:
            self.memory.update(memory_updates)
        print(f"[RP] memory update: {time.time() - t_mem:.2f}s, keys={len(memory_updates)}")

        self.import_finished.emit(all_translations, applied_count, matched_mods, mod_updates)

    def _find_matching_translations(self, snap, ja_target, mod_type,
                                     all_translations, target_suffix_map, namespace_map,
                                     translation_key_sets=None, ftbquest_paths=None):
        ja_target_norm = ja_target.replace('\\', '/')

        filename = ja_target_norm.rsplit('/', 1)[-1] if '/' in ja_target_norm else ja_target_norm
        if filename in target_suffix_map:
            for pack_path in target_suffix_map[filename]:
                pack_norm = pack_path.replace('\\', '/')
                if pack_norm.endswith(ja_target_norm) or ja_target_norm.endswith(pack_norm):
                    return all_translations[pack_path], pack_path

        if mod_type == "ftbquest" and ftbquest_paths:
            for pack_path in ftbquest_paths:
                return all_translations[pack_path], pack_path

        if mod_type != "ftbquest":
            mod_ns = self._extract_namespace(ja_target_norm)
            if mod_ns and mod_ns in namespace_map:
                for pack_path in namespace_map[mod_ns]:
                    pack_norm = pack_path.replace('\\', '/')
                    if pack_norm.endswith(ja_target_norm) or ja_target_norm.endswith(pack_norm):
                        return all_translations[pack_path], pack_path

        if mod_type == "ftbquest" and translation_key_sets is not None:
            mod_keys = snap["original_keys"]
            for pack_path, keys in translation_key_sets.items():
                if mod_keys & keys:
                    return all_translations[pack_path], pack_path

        return None, None

    @staticmethod
    def _extract_namespace(path):
        parts = path.replace('\\', '/').split('/')
        if len(parts) >= 3 and parts[0] == 'assets':
            return parts[1]
        return None
