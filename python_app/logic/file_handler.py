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

    def read_translation_file(self, zip_path, internal_path):
        """Reads a translation file from a specific zip."""
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
        """Generates a resource pack with the translations."""
        target_path = lang_path.replace('en_us', 'ja_jp')
        if target_path == lang_path and 'ja_jp' not in target_path:
             pass 
             # If source is not en_us, we might want to logic here, but keeping simple for now
        
        with zipfile.ZipFile(output_path, 'w') as zf:
            # pack.mcmeta
            pack_meta = {
                "pack": {
                    "pack_format": 15,
                    "description": f"Translations for {mod_name}"
                }
            }
            zf.writestr('pack.mcmeta', json.dumps(pack_meta, indent=2))
            
            # Translation file
            content = json.dumps(translations, indent=2, ensure_ascii=False)
            zf.writestr(target_path, content)
