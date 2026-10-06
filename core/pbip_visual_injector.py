import copy
import json
import os
import secrets


class PBIPVisualInjector:
    def __init__(self, template_path: str):
        self.template_path = template_path
        self.template = self._load_template()

    def _load_template(self):
        try:
            with open(self.template_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            raise Exception(f"Failed to load visual template: {e}")

    def inject_visual(
        self,
        page_path: str,
        table_name: str,
        msg_measure: str,
        color_text_measure: str,
        color_bg_measure: str,
    ):
        """
        Creates a new visual file in the page's 'visuals' directory based on the template,
        replaced with the specific table and measures.
        """
        visuals_dir = os.path.join(page_path, "visuals")
        if not os.path.exists(visuals_dir):
            os.makedirs(visuals_dir)

        # Generate unique ID for the visual
        visual_id = secrets.token_hex(10)  # 20 chars

        # Deep copy template to avoid modifying the original loaded dict
        visual_data = copy.deepcopy(self.template)

        # Update Name
        visual_data["name"] = visual_id

        # Perform replacements in the JSON structure
        # We need to traverse the JSON and replace specific values
        # Target placeholders based on the extracted template:
        # Entity: "MB5B_Mensuales" -> table_name
        # Property: "Color Alerta Texto" -> color_text_measure
        # Property: "Color Alerta Fondo" -> color_bg_measure
        # Property: "Texto_Alerta_Mantenimiento" or "Alerta_Mensaje_..." -> msg_measure

        # Since the template values are specific to the extracted file,
        # we will use a recursive replacement function.

        # Let's assume the template has:
        # 1. Color Text measure
        # 2. Color Background measure (might be missing in text-only cards but we expect it)
        # 3. Message data field

        # To make this robust, we will walk the json and looks for "Property" keys
        # and checking if they look like the known source measures, OR
        # we can just blindly replace IF we know the source names.

        # Better approach: The template is standard.
        # We can modify the "config" string if it exists (in report.json style)
        # OR modify the object directly (in PBIR style).

        # In PBIR, visual_data is the whole JSON object.
        self._recursive_replace(
            visual_data, table_name, msg_measure, color_text_measure, color_bg_measure
        )

        # Save new visual file
        # In the directory listing we saw files without extension or dirs.
        # But `visual.json` inside a dir.
        # Wait, the previous inspection showed IS A DIRECTORY with a visual.json inside?
        # Let's check the previous output...
        # [DIR] 7133bbf6273668c3b6e9
        #    visual.json (implied because I opened visual.json inside it)

        # So I need to create a DIRECTORY named `visual_id`
        # And inside it put `visual.json`

        visual_folder = os.path.join(visuals_dir, visual_id)
        os.makedirs(visual_folder, exist_ok=True)

        visual_file_path = os.path.join(visual_folder, "visual.json")
        with open(visual_file_path, "w", encoding="utf-8") as f:
            json.dump(visual_data, f, indent=2)

        print(f"Injected visual {visual_id} into {page_path}")
        return visual_id

    def _recursive_replace(
        self, obj, table_name, msg_measure, color_text_measure, color_bg_measure
    ):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key == "Entity" and value == "MB5B_Mensuales":
                    obj[key] = table_name
                elif key == "Property":
                    # Heuristics based on what we saw in the template
                    if value == "Color Alerta Texto":
                        obj[key] = color_text_measure
                    elif value == "Color Alerta Fondo":
                        obj[key] = color_bg_measure
                    elif "Alerta" in str(value) or "Mensaje" in str(value) or "Texto" in str(value):
                        # This catch-all might be dangerous, but for the Data Field it's likely the Message
                        # We hope there are no other "Alerta" properties.
                        # The extracted template had: "Property": "Color Alerta Texto"
                        # We need to distinguish Message from Color.
                        if value != "Color Alerta Texto" and value != "Color Alerta Fondo":
                            obj[key] = msg_measure

                self._recursive_replace(
                    value, table_name, msg_measure, color_text_measure, color_bg_measure
                )
        elif isinstance(obj, list):
            for item in obj:
                self._recursive_replace(
                    item, table_name, msg_measure, color_text_measure, color_bg_measure
                )

    def remove_visuals(self, page_path: str):
        """
        Removes visuals from the page that look like they were created by this tool.
        Identified by our custom measure names in the visual.json content.
        """
        visuals_dir = os.path.join(page_path, "visuals")
        if not os.path.exists(visuals_dir):
            return 0

        removed_count = 0
        import shutil

        # Iterate all folders in visuals/
        for folder_name in os.listdir(visuals_dir):
            folder_path = os.path.join(visuals_dir, folder_name)
            if os.path.isdir(folder_path):
                visual_json_path = os.path.join(folder_path, "visual.json")
                if os.path.exists(visual_json_path):
                    try:
                        with open(visual_json_path, encoding="utf-8") as f:
                            content = f.read()

                        # Heuristic: Check for our measure name pattern
                        if "Alerta_" in content and ("Mensaje" in content or "Fondo" in content):
                            print(f"Removing visual folder: {folder_path}")
                            shutil.rmtree(folder_path)
                            removed_count += 1
                    except Exception as e:
                        print(f"Error checking/removing visual {folder_path}: {e}")

        return removed_count
