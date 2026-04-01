import os
import json
import zipfile
from PySide6.QtCore import QThread, Signal

from logic.file_handler import FileHandler

class ResourcePackImportThread(QThread):
    progress = Signal(int, int) # current, total
    finished = Signal(dict, int, list) # all_translations, applied_count, matched_mods
    error = Signal(str)

    def __init__(self, path, loaded_mods, file_handler, memory, target_lang="ja_jp"):
        super().__init__()
        self.path = path
        self.loaded_mods = loaded_mods
        self.file_handler = file_handler
        self.memory = memory
        self.target_lang = target_lang

    def run(self):
        all_translations = {}
        try:
            if os.path.isdir(self.path):
                # Count files for progress (estimate)
                files_to_scan = []
                for root, _, files in os.walk(self.path):
                    for f in files:
                        if f.endswith(f'{self.target_lang}.json') or f.endswith(f'{self.target_lang}.lang'):
                            files_to_scan.append(os.path.join(root, f))
                
                total_files = len(files_to_scan)
                for i, full_path in enumerate(files_to_scan):
                    rel_path = os.path.relpath(full_path, self.path)
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
                    self.progress.emit(i + 1, total_files)
            else:
                with zipfile.ZipFile(self.path, 'r') as zf:
                    namelist = zf.namelist()
                    files_to_scan = [f for f in namelist if f.endswith(f'{self.target_lang}.json') or f.endswith(f'{self.target_lang}.lang')]
                    total_files = len(files_to_scan)
                    
                    for i, f in enumerate(files_to_scan):
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
                        self.progress.emit(i + 1, total_files)

            if not all_translations:
                self.finished.emit({}, 0, [])
                return

            applied_count = 0
            matched_mods = []
            
            # Match with loaded mods
            for mod_path, mod_data in self.loaded_mods.items():
                target_file = mod_data["target_file"]
                ja_target = target_file.replace('en_us', self.target_lang)
                mod_type = mod_data.get("type", "mod")
                
                matched = False
                found_translations = None
                
                for pack_path, translations in all_translations.items():
                    pack_path_normalized = pack_path.replace('\\', '/')
                    ja_target_normalized = ja_target.replace('\\', '/')
                    
                    if pack_path_normalized.endswith(ja_target_normalized) or ja_target_normalized.endswith(pack_path_normalized):
                        matched = True
                        found_translations = translations
                        break
                    elif mod_type == "ftbquest" and "ftbquests" in pack_path_normalized:
                        matched = True
                        found_translations = translations
                        break
                
                # Secondary check for ftbquest if not matched yet
                if not matched and mod_type == "ftbquest":
                    for pack_path, translations in all_translations.items():
                        matching_keys = set(translations.keys()) & set(mod_data["original"].keys())
                        if matching_keys:
                            matched = True
                            found_translations = translations
                            break

                if matched and found_translations:
                    matching_keys = set(found_translations.keys()) & set(mod_data["original"].keys())
                    if matching_keys:
                        for key in matching_keys:
                            mod_data["translations"][key] = found_translations[key]
                        applied_count += len(matching_keys)
                        matched_mods.append(mod_data["name"])
                        self.memory.update({k: found_translations[k] for k in matching_keys})

            self.finished.emit(all_translations, applied_count, matched_mods)

        except Exception as e:
            self.error.emit(str(e))
