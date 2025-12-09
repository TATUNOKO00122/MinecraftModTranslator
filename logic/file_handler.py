import zipfile
import json
import os

class FileHandler:
    def load_zip(self, file_path):
        """
        Loads a zip/jar file and finds translation files.
        Returns: (mod_name, found_files)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError("File not found")

        mod_name = os.path.splitext(os.path.basename(file_path))[0]
        
        found_files = []
        try:
            with zipfile.ZipFile(file_path, 'r') as zf:
                file_list = zf.namelist()
                for f in file_list:
                    if f.startswith('assets/') and '/lang/' in f:
                        if f.endswith('.json') or f.endswith('.lang'):
                            found_files.append(f)
        except zipfile.BadZipFile:
            raise Exception("Invalid ZIP/JAR file")
            
        return mod_name, found_files

    def load_folder(self, folder_path):
        """
        Loads a folder and finds translation files.
        Returns: (mod_name, found_files)
        """
        if not os.path.isdir(folder_path):
            raise FileNotFoundError("Folder not found")

        mod_name = os.path.basename(folder_path)
        
        found_files = []
        for root, dirs, files in os.walk(folder_path):
            for f in files:
                if f.endswith('.json') or f.endswith('.lang'):
                    full_path = os.path.join(root, f)
                    rel_path = os.path.relpath(full_path, folder_path)
                    if 'assets' in rel_path and 'lang' in rel_path:
                        found_files.append(rel_path)
            
        return mod_name, found_files

    def read_translation_file(self, source_path, internal_path):
        """Reads a translation file from a zip or folder."""
        # Check if source is a folder
        if os.path.isdir(source_path):
            return self.read_translation_file_from_folder(source_path, internal_path)
        else:
            return self.read_translation_file_from_zip(source_path, internal_path)
    
    def read_translation_file_from_zip(self, zip_path, internal_path):
        """Reads a translation file from a zip."""
        with zipfile.ZipFile(zip_path, 'r') as zf:
            with zf.open(internal_path) as f:
                content = f.read().decode('utf-8', errors='replace')
                
                if internal_path.endswith('.json'):
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return {}
                else:
                    return self._parse_lang(content)

    def read_translation_file_from_folder(self, folder_path, internal_path):
        """Reads a translation file from a folder."""
        full_path = os.path.join(folder_path, internal_path)
        if not os.path.exists(full_path):
            return {}
            
        with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
            
        if internal_path.endswith('.json'):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                return {}
        else:
            return self._parse_lang(content)

    def _parse_lang(self, content):
        result = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            result[key.strip()] = val.strip()
        return result

    def save_resource_pack(self, output_path, mod_name, translations, lang_path="en_us.json"):
        """Generates a resource pack directory with the translations."""
        target_path = lang_path.replace('en_us', 'ja_jp')
        if target_path == lang_path and 'ja_jp' not in target_path:
             pass 
        
        # Ensure output directory exists
        if not os.path.exists(output_path):
            os.makedirs(output_path)
            
        # pack.mcmeta
        pack_meta = {
            "pack": {
                "pack_format": 15,
                "description": f"Translations for {mod_name}"
            }
        }
        with open(os.path.join(output_path, 'pack.mcmeta'), 'w', encoding='utf-8') as f:
            json.dump(pack_meta, f, indent=2)
            
        # Translation file
        # Create dir for assets/.../lang/
        full_target_path = os.path.join(output_path, target_path)
        os.makedirs(os.path.dirname(full_target_path), exist_ok=True)
        
        with open(full_target_path, 'w', encoding='utf-8') as f:
            json.dump(translations, f, indent=2, ensure_ascii=False)

    def save_merged_resource_pack(self, output_path, mod_data_list):
        """
        Generates a single resource pack directory containing translations for multiple MODs.
        """
        if not os.path.exists(output_path):
            os.makedirs(output_path)

        # pack.mcmeta
        pack_meta = {
            "pack": {
                "pack_format": 15,
                "description": "Merged Translations Pack"
            }
        }
        with open(os.path.join(output_path, 'pack.mcmeta'), 'w', encoding='utf-8') as f:
            json.dump(pack_meta, f, indent=2)
            
        for mod in mod_data_list:
            translations = mod["translations"]
            if not translations:
                continue
                
            # Determine target path (en_us -> ja_jp)
            target_path = mod["target_file"].replace('en_us', 'ja_jp')
            
            full_target_path = os.path.join(output_path, target_path)
            os.makedirs(os.path.dirname(full_target_path), exist_ok=True)
            
            with open(full_target_path, 'w', encoding='utf-8') as f:
                json.dump(translations, f, indent=2, ensure_ascii=False)
