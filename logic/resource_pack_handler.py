import os
import json
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

                memory_translations = self.memory.apply_to(data)

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
    finished = Signal(dict, int, list) # all_translations, applied_count, matched_mods
    error = Signal(str)

    def __init__(self, path, loaded_mods, file_handler, memory, target_lang="ja_jp"):
        super().__init__()
        self.path = path
        self.loaded_mods = loaded_mods
        self.file_handler = file_handler
        self.memory = memory
        self.target_lang = target_lang
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        all_translations = {}
        try:
            self._read_pack_files(all_translations)

            if not self.is_running or not all_translations:
                self.finished.emit({}, 0, [])
                return

            self._match_and_apply(all_translations)

        except Exception as e:
            self.error.emit(str(e))

    def _read_pack_files(self, all_translations):
        if os.path.isdir(self.path):
            files_to_scan = []
            for root, _, files in os.walk(self.path):
                for f in files:
                    if f.endswith(f'{self.target_lang}.json') or f.endswith(f'{self.target_lang}.lang'):
                        files_to_scan.append(os.path.join(root, f))

            total_files = len(files_to_scan)
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
                self.progress.emit(i + 1, total_files, "read")
        else:
            with zipfile.ZipFile(self.path, 'r') as zf:
                namelist = zf.namelist()
                files_to_scan = [f for f in namelist if f.endswith(f'{self.target_lang}.json') or f.endswith(f'{self.target_lang}.lang')]
                total_files = len(files_to_scan)

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
                    self.progress.emit(i + 1, total_files, "read")

    def _match_and_apply(self, all_translations):
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

        mods_list = list(self.loaded_mods.items())
        total_mods = len(mods_list)
        applied_count = 0
        matched_mods = []
        memory_updates = {}

        for i, (mod_path, mod_data) in enumerate(mods_list):
            if not self.is_running:
                break

            target_file = mod_data["target_file"].replace('\\', '/')
            ja_target = target_file.replace('en_us', self.target_lang)
            mod_type = mod_data.get("type", "mod")

            found_translations = self._find_matching_translations(
                mod_data, ja_target, mod_type, all_translations,
                target_suffix_map, namespace_map
            )

            if found_translations is not None:
                matching_keys = set(found_translations.keys()) & set(mod_data["original"].keys())
                if matching_keys:
                    updates = {}
                    for key in matching_keys:
                        mod_data["translations"][key] = found_translations[key]
                        updates[key] = found_translations[key]
                    applied_count += len(matching_keys)
                    matched_mods.append(mod_data["name"])
                    memory_updates.update(updates)

            if (i + 1) % 10 == 0 or i + 1 == total_mods:
                self.progress.emit(i + 1, total_mods, "match")

        if memory_updates:
            self.memory.update(memory_updates)

        self.finished.emit(all_translations, applied_count, matched_mods)

    def _find_matching_translations(self, mod_data, ja_target, mod_type,
                                     all_translations, target_suffix_map, namespace_map):
        ja_target_norm = ja_target.replace('\\', '/')

        filename = ja_target_norm.rsplit('/', 1)[-1] if '/' in ja_target_norm else ja_target_norm
        if filename in target_suffix_map:
            for pack_path in target_suffix_map[filename]:
                pack_norm = pack_path.replace('\\', '/')
                if pack_norm.endswith(ja_target_norm) or ja_target_norm.endswith(pack_norm):
                    return all_translations[pack_path]

        if mod_type == "ftbquest":
            for pack_path, translations in all_translations.items():
                if "ftbquests" in pack_path.replace('\\', '/'):
                    return translations

        if mod_type != "ftbquest":
            mod_ns = self._extract_namespace(ja_target_norm)
            if mod_ns and mod_ns in namespace_map:
                for pack_path in namespace_map[mod_ns]:
                    pack_norm = pack_path.replace('\\', '/')
                    if pack_norm.endswith(ja_target_norm) or ja_target_norm.endswith(pack_norm):
                        return all_translations[pack_path]

        if mod_type == "ftbquest":
            mod_keys = set(mod_data["original"].keys())
            for pack_path, translations in all_translations.items():
                if mod_keys & set(translations.keys()):
                    return translations

        return None

    @staticmethod
    def _extract_namespace(path):
        parts = path.replace('\\', '/').split('/')
        if len(parts) >= 3 and parts[0] == 'assets':
            return parts[1]
        return None
